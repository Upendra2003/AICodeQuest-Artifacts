"""Phase 0 demo: boot the app, log three synthetic telemetry events against a
real Mongo instance, and write reports/phase-0-review.md.

Run via `make demo-phase-0` (which brings up mongo/redis first) or directly
with mongo/redis already running:

    cd backend && ../.venv/bin/python scripts/demo_phase_0.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.core.config import CONFIG_VERSION, get_config  # noqa: E402
from app.telemetry.events import log_event  # noqa: E402
from scripts.report import generate_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

BUILT = [
    "config/params.yaml — all Phase 0 knobs (trigger, developer_gate, "
    "lead_gate, execution, llm, prefetch), grouped and commented",
    "config/params.schema.json — JSON Schema validating types/ranges/enums "
    "for every knob above",
    "backend/app/core/config.py — load_and_validate(), get_config(), "
    "CONFIG_VERSION (short sha256 of canonicalised config), logs on boot",
    "config/CHANGELOG-config.md — initial entry with date + computed "
    "CONFIG_VERSION",
    "backend/app/telemetry/events.py — log_event(event_type, actor_id, "
    "pr_ref, payload) writing to Mongo `events`; ts_utc/config_version/"
    "code_version are set inside the function, not accepted from callers",
    "infra/docker-compose.yml — mongo:7.0, redis:7.4-alpine, placeholder "
    "backend service, commented Judge0 stub for Phase 3",
    "Makefile — venv, up, down, test, lint, demo-phase-0, report",
    "backend/scripts/report.py — generate_report()/render_report(), "
    "reusable by future phases",
]

OPEN_QUESTIONS = [
    "report.py's section skeleton (Summary / What was built / Tests / Demo "
    "output / Open questions) is my interpretation of the standing rules' "
    "prose description — no literal template was pasted. Confirm this "
    "shape is what you want future phases to keep using, or give me the "
    "exact one you had in mind.",
    "backend/app/db/client.py was left as an empty stub — events.py opens "
    "its own Mongo connection directly (MONGO_URI env var, default "
    "mongodb://localhost:27017, db 'aicodequest'). A shared DB client "
    "module wasn't in this phase's explicit scope; should Phase 1 build "
    "one, or is per-module connection handling fine long-term?",
    "This repo is not yet a git repository, so code_version in every "
    "telemetry event currently reads 'unknown' (git rev-parse fails). Say "
    "when you want me to `git init` — I won't do it unprompted.",
    "DB connection settings (MONGO_URI, REDIS_URL) are read from "
    "environment variables, not config/params.yaml, on the reasoning that "
    "params.yaml/CONFIG_VERSION should reflect research-tunable knobs, not "
    "infra endpoints. Confirm that split is right.",
    "infra/docker-compose.yml's `backend` service has no Dockerfile yet — "
    "it's a python:3.11-slim container mounting backend/ and doing "
    "nothing (`sleep infinity`), purely so the compose shape exists. "
    "Actual backend containerization wasn't requested this phase.",
    "llm.provider/model default to 'groq' / 'llama-3.3-70b-versatile' as "
    "placeholders since no LLM behavior has been verified yet (per rule 7). "
    "These aren't validated against a real Groq call in this phase.",
    "judge0_url, cpu/memory/wall-clock limits, and the commented Judge0 "
    "compose block are placeholder values — Judge0's actual container "
    "requirements (it normally needs its own postgres + workers) haven't "
    "been verified and are deferred to Phase 3.",
    "lint target uses flake8 (max-line-length=110) as the 'boring' choice "
    "over e.g. ruff — no linter was specified, confirm or override.",
]


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


def _events_table(events: list[dict]) -> str:
    header = "| event_type | actor_id | pr_ref | ts_utc | config_version | code_version |"
    sep = "|---|---|---|---|---|---|"
    rows = [
        f"| {e['event_type']} | {e['actor_id']} | {e['pr_ref']} | {e['ts_utc']} "
        f"| {e['config_version']} | {e['code_version']} |"
        for e in events
    ]
    return "\n".join([header, sep, *rows])


def main() -> None:
    get_config()  # importing app.core.config already booted + validated
    print(f"booted: CONFIG_VERSION={CONFIG_VERSION}")

    events = [
        log_event(
            event_type="pr_gate_triggered",
            actor_id="demo-developer",
            pr_ref="demo-org/demo-repo#101",
            payload={"lines_changed": 142, "modules_changed": 4},
        ),
        log_event(
            event_type="developer_quiz_started",
            actor_id="demo-developer",
            pr_ref="demo-org/demo-repo#101",
            payload={"round": 1, "levels": ["A1", "A2", "A3", "A4"]},
        ),
        log_event(
            event_type="developer_quiz_passed",
            actor_id="demo-developer",
            pr_ref="demo-org/demo-repo#101",
            payload={"score_pct": 87.5},
        ),
    ]
    for e in events:
        print(f"logged event: {e['event_type']} @ {e['ts_utc']}")

    tests_markdown = _run_tests()
    demo_markdown = (
        "Three synthetic events logged via `log_event()` against the live "
        "Mongo instance (`infra` docker compose), fetched back with their "
        "server-assigned `_id`s:\n\n" + _events_table(events)
    )

    report_path = generate_report(
        phase=0,
        title="Foundations",
        config_version=CONFIG_VERSION,
        summary=(
            "Repository skeleton created per the supplied folder structure. "
            "Config system (params.yaml + schema + CONFIG_VERSION hashing) "
            "and telemetry event logging are implemented and tested; both "
            "run against real infra (Mongo/Redis via docker compose), not "
            "mocks, for this demo. No Flask app, HTTP routes, or gate logic "
            "were implemented — out of scope for this phase."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
