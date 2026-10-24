"""Phase 1 demo: register 2 users, log in, and print the resulting profile
documents (password fields redacted) into reports/phase-1-review.md.

Runs against real Mongo (the same `infra` docker compose used in Phase 0),
using Flask's test client to exercise the full app — routing, JWT, argon2,
telemetry — without needing a separately-running server process.

Run via `make demo-phase-1` or directly with mongo/redis already running:

    cd backend && ../.venv/bin/python scripts/demo_phase_1.py
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app import create_app  # noqa: E402
from app.core.config import CONFIG_VERSION  # noqa: E402
from scripts.report import generate_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

BUILT = [
    "backend/app/api/auth.py — POST /auth/register, POST /auth/login, "
    "GET /auth/me, PATCH /auth/me; argon2 password hashing, "
    "flask-jwt-extended access tokens",
    "backend/app/db/models.py — pydantic v2 models for users, repos, "
    "pull_requests, perturbations, test_suites, dev_attempts, "
    "lead_attempts, leaderboard_agg (extra='forbid', required-field "
    "validation); LeaderboardAggDocument.to_public_dict() is the only "
    "path that may leave private_metrics behind",
    "backend/app/db/indexes.py — ensure_indexes(), idempotent, called on "
    "every app boot from create_app()",
    "backend/app/db/client.py — shared Mongo client (resolves Phase 0 "
    "open question #2: events.py now uses this instead of its own "
    "connection)",
    "backend/app/__init__.py — create_app() Flask factory: JWTManager, "
    "DB wiring (with test-injection support), index creation on boot, "
    "auth blueprint registration",
    "config/params.yaml — new `auth` section (jwt_access_token_expires_min, "
    "argon2_time_cost, argon2_memory_cost_kib, argon2_parallelism)",
    "Telemetry: register/login/profile_update events wired to their "
    "endpoints via log_event()",
]

OPEN_QUESTIONS = [
    "No consent-signing endpoint exists. The users.consent field and the "
    "consent_signed telemetry event you asked for have no trigger in this "
    "phase's endpoint list (register only takes email/password/"
    "display_name/role_hint). I modeled `consent` as Optional (None until "
    "signed) rather than invent an endpoint for something this sensitive. "
    "Where should consent capture happen — folded into register, or a "
    "separate endpoint/flow?",
    "\"opt-in flags\" in PATCH /auth/me was unspecified beyond the name. "
    "I implemented it as a free-form dict[str, bool] merged on PATCH (e.g. "
    "{'leaderboard_public': true}) since no concrete flag names were "
    "given. Confirm the actual flag set you need, or say if this generic "
    "shape is fine long-term.",
    "repos.org_consent_ref is modeled Optional for the same reason as "
    "user consent — no org sign-off flow exists yet.",
    "pull_requests/perturbations/test_suites/dev_attempts/lead_attempts "
    "models exist but have no endpoints writing to them yet (expected — "
    "no PR ingestion, quiz, or gate logic was in this phase's scope). "
    "They're validated by unit tests only, not exercised end-to-end.",
    "JWT_SECRET_KEY falls back to a hardcoded dev-only string with a "
    "logged warning when the env var isn't set (used for this demo). "
    "Needs a real secret before anything beyond local dev/the study pilot.",
    "No password strength/length policy was specified, so register only "
    "checks the password is non-empty. Flag if the study needs a minimum.",
    "code_version in every event still reads 'unknown' — repo still isn't "
    "a git repository (same open question as Phase 0).",
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


def _redact(doc: dict) -> dict:
    doc = dict(doc)
    if "pw_hash" in doc:
        doc["pw_hash"] = "***redacted***"
    if "password" in doc:
        doc["password"] = "***redacted***"
    return doc


def _profile_table(profiles: list[dict]) -> str:
    header = "| id | email | display_name | role_hint | opt_in_flags | created_at |"
    sep = "|---|---|---|---|---|---|"
    rows = [
        f"| {p['id']} | {p['email']} | {p['display_name']} | {p['role_hint']} "
        f"| {p['opt_in_flags']} | {p['created_at']} |"
        for p in profiles
    ]
    return "\n".join([header, sep, *rows])


def main() -> None:
    app = create_app()  # real Mongo via MONGO_URI, indexes created on boot
    client = app.test_client()

    suffix = uuid.uuid4().hex[:8]
    users = [
        {
            "email": f"demo-dev-{suffix}@example.com",
            "password": "correct-horse-battery-staple",
            "display_name": "Demo Developer",
            "role_hint": "developer",
        },
        {
            "email": f"demo-lead-{suffix}@example.com",
            "password": "another-strong-passphrase",
            "display_name": "Demo Lead",
            "role_hint": "lead",
        },
    ]

    profiles = []
    for u in users:
        r = client.post("/auth/register", json=u)
        assert r.status_code == 201, r.get_json()
        print(f"registered: {u['email']} -> {r.get_json()['id']}")

        login = client.post("/auth/login", json={"email": u["email"], "password": u["password"]})
        assert login.status_code == 200, login.get_json()
        token = login.get_json()["access_token"]
        print(f"logged in: {u['email']}")

        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        profiles.append(_redact(me.get_json()))

    tests_markdown = _run_tests()
    demo_markdown = (
        "Two users registered and logged in via the real HTTP routes "
        "(Flask test client) against the live Mongo instance. Profiles "
        "below are exactly what GET /auth/me returned — the API never "
        "includes pw_hash, so redaction here is a defensive no-op, not "
        "the actual privacy boundary:\n\n" + _profile_table(profiles)
    )

    report_path = generate_report(
        phase=1,
        title="Backend core: auth + data model",
        config_version=CONFIG_VERSION,
        summary=(
            "Auth endpoints (register/login/me/patch-me), pydantic v2 "
            "document models for every Phase 1 collection, idempotent "
            "index creation on boot, and register/login/profile_update "
            "telemetry are implemented and tested against real infra. "
            "The leaderboard public/private metric split is enforced by "
            "LeaderboardAggDocument.to_public_dict() and covered by a "
            "dedicated privacy test. No gate/quiz/PR-ingestion logic was "
            "implemented — out of scope for this phase."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
