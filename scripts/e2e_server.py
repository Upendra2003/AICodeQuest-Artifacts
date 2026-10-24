"""Starts a REAL, listening Flask HTTP server for Phase 9's Playwright
end-to-end tests.

Why a real server and not `flask test_client()`: the extension's service
worker makes genuine `fetch()` calls to `config/params.yaml`'s
`extension.backend_base_url`, from inside a real Chromium process --
`page.route()` (Playwright's usual "fake this response" mechanism) only
intercepts requests made by the PAGE it's attached to, not the SEPARATE
service-worker execution context, so there is no way to fake the backend
at the Playwright layer for gate-evaluation/dev-gate/lead-gate calls. A
real listening server is the only option.

Uses the exact same test doubles the backend's own pytest suite uses --
mongomock (in-memory, lives only as long as THIS process), ScriptedLLM
(prompt-shape-aware, tests/support/scripted_llm.py, extended in Phase 9
to also cover nfr.py's prompts), and local_executor.run_locally (since
Judge0 cannot execute anything in this sandbox -- see
reports/phase-3-review.md) -- just wrapped in a real running process
instead of Flask's test_client().

Run directly, or via Playwright's `webServer` config
(extension/playwright.config.js), which starts/stops it automatically:

    cd backend && ../.venv/bin/python scripts/e2e_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))  # backend/tests/ (support/)

import mongomock  # noqa: E402

import app.services.llm as llm_mod  # noqa: E402
from app import create_app  # noqa: E402
from app.core.config import get_config  # noqa: E402
from app.services import consent  # noqa: E402
from support.local_executor import run_locally  # noqa: E402
from support.scripted_llm import ScriptedLLM  # noqa: E402

E2E_USER_EMAIL = "e2e@example.com"
E2E_USER_PASSWORD = "correct-horse-battery"

# Phase 14: a SECOND user who is registered but has deliberately never
# signed consent, so consent_gate.spec.js can drive the real consent
# dialog. Every other spec keeps using E2E_USER_EMAIL (pre-consented), so
# adding this changes nothing for them -- the alternative, un-consenting
# the shared user, would have made all of them start with this dialog.
E2E_UNCONSENTED_EMAIL = "e2e-unconsented@example.com"
E2E_UNCONSENTED_PASSWORD = "correct-horse-battery"


def build_app():
    scripted = ScriptedLLM(opponent_fixes_correctly=True)
    llm_mod._build_provider = lambda name: scripted

    db = mongomock.MongoClient()["extension_e2e"]
    application = create_app(mongo_db=db, runner=run_locally)
    application.config["SCRIPTED_LLM"] = scripted
    return application, db


def seed_user(application) -> None:
    client = application.test_client()
    r = client.post(
        "/auth/register",
        json={
            "email": E2E_USER_EMAIL,
            "password": E2E_USER_PASSWORD,
            "display_name": "E2E Tester",
            "role_hint": "both",
        },
    )
    db = application.config["DB"]
    # Phase 12: /gate/evaluate, /dev-gate/*/start, and /lead-gate/*/nfrs
    # all now hard-require consent -- the fixture PR pages this server
    # backs (backend/tests/fixtures/pr_pages/) are all octocat/Hello-World.
    consent.register_org_consent(
        db, org="octocat", repos=[], signed_agreement_ref="e2e-ref",
        permitted_data_categories=["code_snippets", "metrics"],
        retention_period_days=365, enforcement_mode="shadow",
    )
    consent.sign_participant_consent(
        db, r.get_json()["id"], version=get_config()["consent"]["current_version"],
        retain_code_snippets=True, retain_transcripts=True, allow_quotes_in_publication=True,
    )

    # Registered but NOT consented -- no sign_participant_consent() call
    # here, on purpose. See E2E_UNCONSENTED_EMAIL's comment above.
    client.post(
        "/auth/register",
        json={
            "email": E2E_UNCONSENTED_EMAIL,
            "password": E2E_UNCONSENTED_PASSWORD,
            "display_name": "E2E Unconsented",
            "role_hint": "both",
        },
    )


def main() -> None:
    application, _db = build_app()
    seed_user(application)

    backend_base_url = get_config()["extension"]["backend_base_url"]
    parsed = urlparse(backend_base_url)
    host, port = parsed.hostname or "localhost", parsed.port or 5055

    print(f"e2e backend listening on {backend_base_url} (user: {E2E_USER_EMAIL})")
    # threaded=True: dev_gate's prefetch/fallback ladder does an internal
    # bounded wait (see app/services/dev_gate.py's
    # _serve_defense_turn_via_ladder) -- a single-threaded dev server
    # would block itself on any concurrent request during that wait.
    application.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
