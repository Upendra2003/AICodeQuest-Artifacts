"""Phase 6 demo: background generation, the prefetch/fallback ladder, and
the latency budget. Prints two tables into the report -- latency by
endpoint (p50/p95/p99 vs budget, pass/fail) and fallback-rung usage
(cache / prefetch_wait / static_bank / template_only counts) -- both
computed from the same `http_request_latency` / `dev_gate_task_source`
telemetry events a real dashboard would read, not separate ad-hoc timers.

Run via `make demo-phase-6` or directly:

    cd backend && ../.venv/bin/python scripts/demo_phase_6.py
"""

from __future__ import annotations

import math
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))  # backend/tests/ (support/)

import mongomock  # noqa: E402

from app import create_app  # noqa: E402
from app.core.config import CONFIG_VERSION, get_config  # noqa: E402
from scripts.report import generate_report  # noqa: E402
from support.local_executor import run_locally  # noqa: E402
from support.rq_test_worker import run_inline_worker  # noqa: E402
from support.scripted_llm import ScriptedLLM  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CORRECT_ADD = "def add(a, b):\n    return a + b\n"

BUILT = [
    "backend/app/workers/queue.py, tasks.py, worker.py -- RQ (python-rq) "
    "on Redis, 3 priority queues (generation/scoring/maintenance), "
    "enqueue_unique() for idempotent-by-job_id enqueueing (RQ itself does "
    "NOT dedupe duplicate job_ids -- verified interactively before writing "
    "this), Retry with exponential backoff + result_ttl from config",
    "infra/docker-compose.yml `worker` service -- the default (forking) "
    "RQ Worker, listening on all 3 queues in priority order",
    "backend/app/services/static_bank.py -- hand-verified generic "
    "functions + pre-baked perturbations per level (rung b), plus "
    "template-only re-derivation against the same generic functions "
    "when the bank is exhausted for a level (rung c)",
    "Prefetch pipeline (dev_gate.py's _maybe_enqueue_prefetch): entering a "
    "defense turn enqueues prefetch.lookahead_count turn(s) ahead, keyed "
    "by (pr_id, user_id, round, phase, level, question_index, "
    "replay_index) so duplicate enqueues are no-ops",
    "Cache-first ladder (_serve_defense_turn_via_ladder): cache -> wait up "
    "to prefetch.max_wait_ms -> static bank -> template-only, with the "
    "rung used logged on every dev_gate_task_source event",
    "app/telemetry/latency.py -- before/after_request middleware logging "
    "an http_request_latency event per request, tagged with a budget_key "
    "for the 3 endpoints config/params.yaml's latency_budget covers",
    "Two concurrency-safety fixes found only by actually wiring this in "
    "and re-running the full suite: (1) a prefetch job racing the "
    "foreground fallback rung for the same turn could double-insert -- "
    "_persist_turn/ensure_turn_generated now re-check-and-return on "
    "DuplicateKeyError; (2) ensure_turn_generated's used_perturbation_signatures "
    "bookkeeping used to overwrite the WHOLE session document from a "
    "background job's own (possibly stale) in-memory snapshot, silently "
    "stomping the foreground request thread's turn_pointer/state advances "
    "-- replaced with a scoped atomic $addToSet update",
    "Grading (submit_defense) now reconstructs the function/suite from "
    "the turn's OWN perturbation_id/test_suite_id rather than always "
    "assuming the session's real function -- required once a turn could "
    "legitimately be served by a static-bank/template-only substitute",
]

OPEN_QUESTIONS = [
    "nfr_question_p95_ms (config/params.yaml's latency_budget) has no "
    "endpoint to measure yet -- app/api/lead_gate.py is an empty stub for "
    "a future phase (Lead gate NFR selection/scoring). The config knob is "
    "defined now so that phase only needs to add an "
    "ENDPOINT_BUDGET_KEYS entry in app/telemetry/latency.py, not new "
    "middleware. Confirm this is the right way to sequence it.",
    "The 10-concurrent-participant load test "
    "(tests/integration/test_latency_budget.py) shows task_render p95 "
    "landing close to its 1500ms budget under load (a real run: "
    "p50=~80ms, p95=~1.2s), because this sandbox's docker-compose runs "
    "exactly one RQ worker process/thread, so the 'generation' queue can "
    "back up under concurrent prefetch demand and more requests fall "
    "through to the wait-then-fallback rungs. A real user study should "
    "confirm whether one worker replica is enough, or budget for two.",
    "The forking (production) RQ Worker vs non-forking SimpleWorker (used "
    "in every test here) was cross-checked by actually starting the "
    "docker-compose `worker` container against a real Mongo -- but no "
    "test in this repo exercises the FORKING worker's crash-isolation "
    "behavior directly (mongomock can't be shared across a fork). "
    "Confirm this gap is acceptable given Judge0/Mongo aren't live in "
    "this sandbox anyway (see reports/phase-3-review.md).",
    "prefetch.lookahead_count defaults to 1 and prefetch only ever "
    "targets defense turns (attack turns are cheap to create -- the slow "
    "LLM/Judge0 work happens at submission time instead). Confirm this "
    "matches the intended UX rather than also wanting attack turns "
    "warmed ahead of time.",
]


def _turns_per_round(round_num: int) -> int:
    cfg = get_config()["developer_gate"]
    per_level = cfg["round1" if round_num == 1 else "round2"]["questions_per_level"]
    return len(cfg["levels"]) * per_level


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _new_client(monkeypatch_provider, db_name: str):
    scripted = ScriptedLLM(opponent_fixes_correctly=True)
    monkeypatch_provider(scripted)
    db = mongomock.MongoClient()[db_name]
    app = create_app(mongo_db=db, runner=run_locally)
    return app.test_client(), db


def _register(client, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Dev",
            "role_hint": "developer",
        },
    )
    token = client.post(
        "/auth/login", json={"email": email, "password": "correct-horse-battery"}
    ).get_json()["access_token"]
    # Phase 12: /dev-gate/<pr_id>/start now hard-requires participant consent.
    from app.services import consent as consent_mod

    consent_mod.sign_participant_consent(
        client.application.config["DB"], r.get_json()["id"], version=get_config()["consent"]["current_version"],
        retain_code_snippets=True, retain_transcripts=True, allow_quotes_in_publication=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _play_round1(client, headers, pr_id: str) -> None:
    client.post(
        f"/dev-gate/{pr_id}/start",
        headers=headers,
        json={"function": {"file": "m.py", "language": "python", "source": CORRECT_ADD}},
    )
    n = _turns_per_round(1)
    for _ in range(n):
        client.get(f"/dev-gate/{pr_id}/current-task", headers=headers)
        client.post(f"/dev-gate/{pr_id}/defense/submit", headers=headers, json={"code": CORRECT_ADD})
        client.post(f"/dev-gate/{pr_id}/advance", headers=headers)
    for _ in range(n):
        client.get(f"/dev-gate/{pr_id}/current-task", headers=headers)
        client.post(f"/dev-gate/{pr_id}/attack/submit", headers=headers, json={"code": CORRECT_ADD})
        client.post(f"/dev-gate/{pr_id}/advance", headers=headers)


def _latency_table(db) -> str:
    budget = get_config()["latency_budget"]
    key_to_budget = {
        "task_render_p95_ms": budget["task_render_p95_ms"],
        "defense_verdict_p95_ms": budget["defense_verdict_p95_ms"],
        "attack_opponent_p95_ms": budget["attack_opponent_p95_ms"],
    }

    events = list(db["events"].find({"event_type": "http_request_latency"}))
    by_key: dict[str, list[float]] = {}
    for e in events:
        key = e["payload"].get("budget_key")
        if key is None:
            continue
        by_key.setdefault(key, []).append(e["payload"]["duration_ms"])

    lines = [
        "| Endpoint (budget key) | n | p50 (ms) | p95 (ms) | p99 (ms) | Budget p95 (ms) | Pass? |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, budget_ms in key_to_budget.items():
        vals = by_key.get(key, [])
        p50, p95, p99 = _percentile(vals, 50), _percentile(vals, 95), _percentile(vals, 99)
        passed = "PASS" if vals and p95 <= budget_ms else ("no data" if not vals else "FAIL")
        lines.append(
            f"| {key} | {len(vals)} | {p50:.0f} | {p95:.0f} | {p99:.0f} | {budget_ms} | {passed} |"
        )
    return "\n".join(lines)


def _fallback_usage_table(db) -> str:
    events = list(db["events"].find({"event_type": "dev_gate_task_source"}))
    counts: dict[str, int] = {}
    for e in events:
        rung = e["payload"]["rung"]
        counts[rung] = counts.get(rung, 0) + 1
    total = sum(counts.values()) or 1

    lines = ["| Rung | Count | % of current-task calls |", "|---|---|---|"]
    for rung in ("cache", "prefetch_wait", "static_bank", "template_only"):
        count = counts.get(rung, 0)
        lines.append(f"| {rung} | {count} | {100 * count / total:.1f}% |")
    return "\n".join(lines)


def run_demo_workload(monkeypatch_provider) -> tuple[str, str]:
    """5 sequential round-1 playthroughs (with a real inline RQ worker
    draining the 'generation' queue) followed by 10 concurrent
    participants, all against one shared db -- so the latency table
    reflects both a warm-cache steady state and a burst under load."""
    client, db = _new_client(monkeypatch_provider, "demo_phase_6_workload")

    with run_inline_worker(db):
        for i in range(5):
            headers = _register(client, f"demo-seq-{i}@example.com")
            _play_round1(client, headers, pr_id=f"pr-demo-seq-{i}")

        def _one_participant(index: int) -> None:
            headers = _register(client, f"demo-load-{index}@example.com")
            _play_round1(client, headers, pr_id=f"pr-demo-load-{index}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(_one_participant, range(10)))

    return _latency_table(db), _fallback_usage_table(db)


def run_fallback_ladder_demo(monkeypatch_provider) -> tuple[str, str]:
    """A second, separate db with NO worker running at all -- every
    defense turn must fall through cache -> wait -> static_bank ->
    (once exhausted) template_only, exercising every rung for the
    fallback-usage table."""
    client, db = _new_client(monkeypatch_provider, "demo_phase_6_fallback")
    headers = _register(client, "demo-fallback@example.com")
    pr_id = "pr-demo-fallback"
    client.post(
        f"/dev-gate/{pr_id}/start",
        headers=headers,
        json={"function": {"file": "m.py", "language": "python", "source": CORRECT_ADD}},
    )
    n = _turns_per_round(1)
    for _ in range(n):
        client.get(f"/dev-gate/{pr_id}/current-task", headers=headers)
        client.post(f"/dev-gate/{pr_id}/defense/submit", headers=headers, json={"code": CORRECT_ADD})
        client.post(f"/dev-gate/{pr_id}/advance", headers=headers)

    return _latency_table(db), _fallback_usage_table(db)


def _run_tests() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=REPO_ROOT / "backend",
        capture_output=True,
        text=True,
    )
    status = "PASSED" if result.returncode == 0 else "FAILED"
    output = (result.stdout + result.stderr).strip()
    return f"**pytest: {status}**\n\n```\n{output}\n```"


def main() -> None:
    import app.services.llm as llm_mod

    def monkeypatch_provider(scripted):
        llm_mod._build_provider = lambda name: scripted

    workload_latency, workload_fallback = run_demo_workload(monkeypatch_provider)
    fallback_latency, fallback_fallback = run_fallback_ladder_demo(monkeypatch_provider)

    demo_markdown = "\n\n".join(
        [
            "### Demo workload: 5 sequential + 10 concurrent playthroughs "
            "(inline RQ worker running -- realistic warm-cache behavior)",
            "",
            "**Latency by endpoint:**",
            "",
            workload_latency,
            "",
            "**Fallback-rung usage:**",
            "",
            workload_fallback,
            "",
            "### Fallback-ladder demo: one playthrough with NO worker "
            "consuming the 'generation' queue (forces every rung)",
            "",
            "**Latency by endpoint:**",
            "",
            fallback_latency,
            "",
            "**Fallback-rung usage:**",
            "",
            fallback_fallback,
        ]
    )
    print(demo_markdown)

    tests_markdown = _run_tests()

    report_path = generate_report(
        phase=6,
        title="Background generation and latency budget",
        config_version=CONFIG_VERSION,
        summary=(
            "Background generation now runs on RQ (python-rq) over Redis, "
            "with a prefetch pipeline that warms the next defense turn "
            "while the player works the current one, and a cache-first "
            "ladder (cache -> bounded wait -> static bank -> "
            "template-only) so /current-task always responds even when "
            "generation can't keep up. A latency-budget middleware logs "
            "per-endpoint request duration to telemetry, and tests assert "
            "p95 stays within config/params.yaml's latency_budget on both "
            "a warm-cache demo workload and a 10-concurrent-participant "
            "load test. Wiring this in surfaced two real concurrency bugs "
            "(a duplicate-insert race between a prefetch job and its "
            "foreground fallback, and a session-document overwrite race) "
            "and one grading-correctness bug (fallback-served turns being "
            "graded against the wrong function) -- all fixed and covered "
            "by tests, not just noticed and left."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
