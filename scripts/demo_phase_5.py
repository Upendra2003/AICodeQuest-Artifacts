"""Phase 5 demo: drive three scripted playthroughs of the Developer gate
and print a turn-by-turn narrative into the report -- the primary review
surface for confirming the locked state-machine flow.

Uses tests/support/scripted_llm.ScriptedLLM (a prompt-shape-aware mock,
since a full playthrough makes many differently-shaped LLM calls a strict
FIFO queue can't practically pre-script -- see its module docstring) and
tests/support/local_executor.run_locally instead of Judge0 (which cannot
execute anything in this dev sandbox, see reports/phase-3-review.md).

Run via `make demo-phase-5` or directly:

    cd backend && ../.venv/bin/python scripts/demo_phase_5.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))  # backend/tests/ (support/)

import mongomock  # noqa: E402

from app import create_app  # noqa: E402
from app.core.config import CONFIG_VERSION, get_config  # noqa: E402
from scripts.report import generate_report  # noqa: E402
from support.local_executor import run_locally  # noqa: E402
from support.scripted_llm import ScriptedLLM  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CORRECT_ADD = "def add(a, b):\n    return a + b\n"
WRONG_ADD = "def add(a, b):\n    return a * b\n"

BUILT = [
    "backend/app/services/dev_gate.py — hand-rolled enum-based Developer "
    "gate state machine (not the `transitions` library); all state in "
    "Mongo (DevGateSessionDocument + one DevAttemptDocument per turn), so "
    "a backend restart mid-round is transparent",
    "backend/app/api/dev_game.py — all 7 endpoints (start/state/"
    "current-task/defense-submit/attack-submit/attack-skip/advance/"
    "override)",
    "Defense grading against the Phase 4 validated suite; Attack grading "
    "via the AI opponent (llm.opponent_model) attempting a fix",
    "Scoring: developer_gate.attack_counts_toward_score (default false) "
    "makes Attack purely diagnostic, so a skipped Attack never penalises "
    "(SUPERSEDED 2026-09-24: the key is gone and the score is a weighted "
    "blend, developer_gate.score_weights -- a skipped Attack still "
    "penalises nothing, via score_absent_phase: renormalize)",
    "Replay generates genuinely NEW perturbations per level (not just a "
    "new Mongo _id) via perturbation.py's avoid_perturbed_code, extended "
    "this phase specifically for this guarantee",
    "Idempotency key + atomic submission_count CAS so exactly one "
    "concurrent submission is accepted per turn",
    "config/params.yaml — new developer_gate.max_replays, "
    "attack_counts_toward_score",
]

OPEN_QUESTIONS = [
    "Earlier phases (0/1) assumed the Developer gate would be quiz-based "
    "(developer_gate.max_quiz_tiers, DevAttemptDocument.quiz_tier) before "
    "this phase locked in Defense/Attack turns instead. Both are kept, "
    "superseded rather than deleted -- confirm whether to clean them up "
    "in a later phase.",
    "No real researcher RBAC exists yet: POST /override is JWT-"
    "authenticated but does NOT verify the caller is actually a "
    "researcher. This must be closed before any real deployment; flagged "
    "loudly in api/dev_game.py's override() docstring/comment too.",
    "The Developer gate needs a target function per session; there's no "
    "PR-ingestion pipeline yet to supply one automatically (Phase 2's "
    "/gate/evaluate is stateless), so POST /start requires the caller to "
    "supply {file, language, source} directly. A future phase wiring "
    "real PR ingestion should feed this from Phase 2's extraction output.",
    "Perturbation/suite generation happens lazily on first GET "
    "current-task per turn (not all upfront at /start), to avoid a very "
    "long /start call given how many real LLM calls a full round needs. "
    "No prefetching (config/params.yaml's prefetch.* knobs from Phase 0) "
    "is wired in yet -- a real UI would feel this as think-time on each "
    "new turn.",
    "ROUND2_SCAFFOLD content is generic static per-level text "
    "(LEVEL_SCAFFOLD_CONTENT in dev_gate.py), not curated for the real "
    "study, mirroring quizgen's fallback-bank placeholder pattern from "
    "Phase 4.",
    "Grading (Defense/Attack) runs against tests/support/local_executor.py "
    "(real local compile/execute, no sandboxing) instead of Judge0 in "
    "this demo/test suite, since Judge0 cannot execute anything in this "
    "sandbox. Production must go through Judge0Client once deployed "
    "somewhere it actually works -- app.__init__.create_app()'s `runner` "
    "parameter makes this a one-line swap.",
    "Concurrency is proven via the atomic CAS primitive directly (two "
    "'racing' reads of the same attempt, only one claim succeeds), not a "
    "true multi-threaded HTTP test -- mongomock isn't a real concurrent "
    "datastore, so that wouldn't prove anything more.",
]


def _turns_per_round(round_num: int) -> int:
    cfg = get_config()["developer_gate"]
    levels = cfg["levels"]
    per_level = cfg["round1" if round_num == 1 else "round2"]["questions_per_level"]
    return len(levels) * per_level


class Narrator:
    def __init__(self, title: str):
        self.title = title
        self.lines: list[str] = [f"### {title}", ""]
        self.turn_count = 0
        self.wins = 0

    def turn(self, client, headers, pr_id: str, kind: str, code: str | None) -> str:
        task = client.get(f"/dev-gate/{pr_id}/current-task", headers=headers).get_json()
        level = task["task"]["level"]
        shown = task["task"]["code"].strip().replace("\n", " / ")

        if kind == "defense":
            r = client.post(f"/dev-gate/{pr_id}/defense/submit", headers=headers, json={"code": code})
        elif kind == "attack_skip":
            r = client.post(f"/dev-gate/{pr_id}/attack/skip", headers=headers)
        else:
            r = client.post(f"/dev-gate/{pr_id}/attack/submit", headers=headers, json={"code": code})
        body = r.get_json()

        client.post(f"/dev-gate/{pr_id}/advance", headers=headers)
        state = client.get(f"/dev-gate/{pr_id}/state", headers=headers).get_json()

        if body.get("outcome") in ("won", "lost"):
            self.turn_count += 1
            if body["outcome"] == "won":
                self.wins += 1
        running = f"{self.wins}/{self.turn_count}" if self.turn_count else "-"

        submitted = (code or "(skipped)").strip().replace("\n", " / ")
        self.lines.append(
            f"- **{kind}** level={level} | shown: `{shown}` | submitted: `{submitted}` "
            f"| verdict: **{body.get('outcome')}** | running score: {running} "
            f"| state -> `{state['state']}`"
        )
        return state["state"]

    def note(self, text: str) -> None:
        self.lines.append(f"  - {text}")

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"


def _new_client(monkeypatch_provider, db_name: str):
    scripted = ScriptedLLM(opponent_fixes_correctly=True)
    monkeypatch_provider(scripted)
    db = mongomock.MongoClient()[db_name]
    app = create_app(mongo_db=db, runner=run_locally)
    return app.test_client(), db


def _register(client) -> dict:
    r = client.post(
        "/auth/register",
        json={
            "email": "dev@example.com",
            "password": "correct-horse-battery",
            "display_name": "Dev",
            "role_hint": "developer",
        },
    )
    token = client.post(
        "/auth/login", json={"email": "dev@example.com", "password": "correct-horse-battery"}
    ).get_json()["access_token"]
    # Phase 12: /dev-gate/<pr_id>/start now hard-requires participant consent.
    from app.services import consent as consent_mod

    consent_mod.sign_participant_consent(
        client.application.config["DB"], r.get_json()["id"], version=get_config()["consent"]["current_version"],
        retain_code_snippets=True, retain_transcripts=True, allow_quotes_in_publication=True,
    )
    return {"Authorization": f"Bearer {token}"}


def run_clean_sweep(monkeypatch_provider) -> str:
    client, _ = _new_client(monkeypatch_provider, "demo_clean_sweep")
    headers = _register(client)
    pr_id = "pr-clean-sweep"
    client.post(
        f"/dev-gate/{pr_id}/start",
        headers=headers,
        json={"function": {"file": "m.py", "language": "python", "source": CORRECT_ADD}},
    )

    n1 = _turns_per_round(1)
    narrator = Narrator("Transcript 1: clean sweep (wins every round-1 turn)")
    for _ in range(n1):
        narrator.turn(client, headers, pr_id, "defense", CORRECT_ADD)
    for _ in range(n1):
        narrator.turn(client, headers, pr_id, "attack_skip", None)

    final = client.get(f"/dev-gate/{pr_id}/state", headers=headers).get_json()
    narrator.note(f"FINAL: state={final['state']} round1_score_pct={final['round1_score_pct']}")
    return narrator.render()


def run_fail_scaffold_pass(monkeypatch_provider) -> str:
    client, _ = _new_client(monkeypatch_provider, "demo_fail_scaffold_pass")
    headers = _register(client)
    pr_id = "pr-fail-scaffold-pass"
    client.post(
        f"/dev-gate/{pr_id}/start",
        headers=headers,
        json={"function": {"file": "m.py", "language": "python", "source": CORRECT_ADD}},
    )

    n1 = _turns_per_round(1)
    narrator = Narrator("Transcript 2: fails round 1 -> scaffold -> passes round 2 -> CLEARED")
    for _ in range(n1):
        narrator.turn(client, headers, pr_id, "defense", WRONG_ADD)
    for _ in range(n1):
        narrator.turn(client, headers, pr_id, "attack_skip", None)

    state = client.get(f"/dev-gate/{pr_id}/state", headers=headers).get_json()
    narrator.note(f"round1_score_pct={state['round1_score_pct']} -> {state['state']}")
    task = client.get(f"/dev-gate/{pr_id}/current-task", headers=headers).get_json()
    narrator.note(f"scaffold shown for failed levels {task['task']['failed_levels']}")
    client.post(f"/dev-gate/{pr_id}/advance", headers=headers)

    n2 = _turns_per_round(2)
    narrator.turn_count, narrator.wins = 0, 0  # round 2 gets its own running score
    for _ in range(n2):
        narrator.turn(client, headers, pr_id, "defense", CORRECT_ADD)
    for _ in range(n2):
        narrator.turn(client, headers, pr_id, "attack_skip", None)

    final = client.get(f"/dev-gate/{pr_id}/state", headers=headers).get_json()
    narrator.note(f"FINAL: state={final['state']} round2_score_pct={final['round2_score_pct']}")
    return narrator.render()


def run_replay(monkeypatch_provider) -> str:
    client, db = _new_client(monkeypatch_provider, "demo_replay")
    headers = _register(client)
    pr_id = "pr-replay-demo"
    client.post(
        f"/dev-gate/{pr_id}/start",
        headers=headers,
        json={"function": {"file": "m.py", "language": "python", "source": CORRECT_ADD}},
    )

    n1 = _turns_per_round(1)
    n2 = _turns_per_round(2)
    narrator = Narrator("Transcript 3: fails both rounds -> REPLAY with new perturbations")
    for _ in range(n1):
        narrator.turn(client, headers, pr_id, "defense", WRONG_ADD)
    for _ in range(n1):
        narrator.turn(client, headers, pr_id, "attack_skip", None)
    client.post(f"/dev-gate/{pr_id}/advance", headers=headers)

    round1_perts = {
        d["level"]: d["perturbation_id"]
        for d in db["dev_attempts"].find({"pr_id": pr_id, "phase": "defense", "round": 1, "replay_index": 0})
    }

    narrator.turn_count, narrator.wins = 0, 0
    for _ in range(n2):
        narrator.turn(client, headers, pr_id, "defense", WRONG_ADD)
    for _ in range(n2):
        narrator.turn(client, headers, pr_id, "attack_skip", None)

    state = client.get(f"/dev-gate/{pr_id}/state", headers=headers).get_json()
    narrator.note(f"round2 fails too -> state={state['state']} (replay_index will become 1)")
    client.post(f"/dev-gate/{pr_id}/advance", headers=headers)
    state = client.get(f"/dev-gate/{pr_id}/state", headers=headers).get_json()
    narrator.note(f"after advance: state={state['state']} replay_index={state['replay_index']}")

    narrator.turn_count, narrator.wins = 0, 0
    for _ in range(n1):
        narrator.turn(client, headers, pr_id, "defense", CORRECT_ADD)
    for _ in range(n1):
        narrator.turn(client, headers, pr_id, "attack_skip", None)

    replay_perts = {
        d["level"]: d["perturbation_id"]
        for d in db["dev_attempts"].find({"pr_id": pr_id, "phase": "defense", "round": 1, "replay_index": 1})
    }
    for level in round1_perts:
        same_id = round1_perts[level] == replay_perts.get(level)
        narrator.note(f"level {level}: round-1 perturbation_id != replay perturbation_id: {not same_id}")

    final = client.get(f"/dev-gate/{pr_id}/state", headers=headers).get_json()
    narrator.note(f"FINAL: state={final['state']} round1_score_pct={final['round1_score_pct']}")
    return narrator.render()


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

    transcripts = [
        run_clean_sweep(monkeypatch_provider),
        run_fail_scaffold_pass(monkeypatch_provider),
        run_replay(monkeypatch_provider),
    ]
    for t in transcripts:
        print(t)

    tests_markdown = _run_tests()
    demo_markdown = "\n".join(transcripts)

    report_path = generate_report(
        phase=5,
        title="Developer gate state machine",
        config_version=CONFIG_VERSION,
        summary=(
            "The Developer gate is a hand-rolled enum-based state machine "
            "(app/services/dev_gate.py), persisted entirely in Mongo so a "
            "backend restart mid-round is transparent. All 7 API endpoints "
            "are implemented (api/dev_game.py). Three scripted playthroughs "
            "below confirm the locked flow: a clean sweep clearing on "
            "round 1, a round-1 failure that scaffolds into a round-2 pass, "
            "and a double failure that replays with genuinely new "
            "perturbations per level. Scoring, replay-cap escalation, "
            "idempotency, and similarity-skip are covered by the test suite."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
