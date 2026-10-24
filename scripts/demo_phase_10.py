"""Phase 10 demo: runs the backend pytest suite (including this phase's
new GitHub App tests, all against a MOCKED GitHub API -- see
docs/GITHUB-APP-SETUP.md for why no live GitHub App exists to test
against here), then drives a narrated walkthrough of a single PR's
lifecycle through the REAL webhook HTTP endpoint (POST /github/webhook):
opened (fires, action_required) -> developer clears their gate ->
cosmetic re-push (stays green) -> logic re-push (reopens) -> repeated
under all three enforcement modes, so the report shows exactly what each
mode reports for the identical underlying sequence of events.

Run via `make demo-phase-10` or directly:

    cd backend && ../.venv/bin/python scripts/demo_phase_10.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

import mongomock  # noqa: E402
import responses  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from app import create_app  # noqa: E402
from app.core.config import CONFIG_VERSION  # noqa: E402
from scripts.report import generate_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

OWNER, REPO, PR_NUMBER = "octocat", "Hello-World", 101
INSTALLATION_ID = 999
TOKEN_URL = f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens"
DIFF_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}"
CHECK_RUNS_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/check-runs"
WEBHOOK_SECRET = "demo-webhook-secret"


def _padding(n: int) -> str:
    return "\n".join(f"+# padding line {i}" for i in range(n))


DIFF_BUGGY = (
    "diff --git a/app.py b/app.py\nindex 1111111..2222222 100644\n--- a/app.py\n+++ b/app.py\n"
    f"@@ -1,1 +1,{4 + 110} @@\n existing_line = 1\n+def process_payment(a, b):\n+    return a - b\n+\n"
    f"{_padding(110)}\n"
)
DIFF_COSMETIC = (
    "diff --git a/app.py b/app.py\nindex 2222222..3333333 100644\n--- a/app.py\n+++ b/app.py\n"
    f"@@ -1,1 +1,{4 + 110} @@\n existing_line = 1\n+def process_payment(a,b):\n+    return a-b\n+\n"
    f"{_padding(110)}\n"
)
DIFF_LOGIC_CHANGE = (
    "diff --git a/app.py b/app.py\nindex 3333333..4444444 100644\n--- a/app.py\n+++ b/app.py\n"
    f"@@ -1,1 +1,{5 + 110} @@\n existing_line = 1\n"
    "+def process_payment(a, b):\n+    if a < 0 or b < 0:\n+        raise ValueError('negative')\n+    return a - b\n+\n"
    f"{_padding(110)}\n"
)

BUILT = [
    "backend/app/services/github_auth.py -- App-level RS256 JWT "
    "signing + installation access token exchange, cached in Mongo "
    "(InstallationTokenDocument) with an expiry buffer",
    "backend/app/services/github_api.py -- thin REST client: PR diff "
    "fetch (diff media type), Checks API create/update",
    "backend/app/services/github_app.py -- the orchestration: resolves "
    "each repo's enforcement mode, runs Phase 2's trigger/extraction "
    "against the fetched diff, classifies a re-push as cosmetic "
    "(similarity-skippable) or meaningful, looks up each persona's live "
    "dev_gate/lead_gate session state (by the SAME pr_id format the "
    "Chrome extension uses), and creates/updates exactly one check run "
    "per (pr, head_sha, persona)",
    "backend/app/api/github_webhook.py -- POST /github/webhook: "
    "X-Hub-Signature-256 verification (HMAC-SHA256, constant-time "
    "compare) before any payload parsing, dispatch on X-GitHub-Event "
    "(pull_request/push/check_run/installation)",
    "backend/app/db/models.py -- RepoDocument gained installation_id + "
    "enforcement_mode; PullRequestDocument reshaped (Phase 1 defined it "
    "but nothing had ever written to it -- same situation Phase 5/7 "
    "found DevAttemptDocument/LeadAttemptDocument in); new "
    "CheckRunDocument (repo, pr_number, head_sha, persona) and "
    "InstallationTokenDocument",
    "config/params.yaml -- new `github_app` section (all non-secret "
    "tunables; the App ID/private key/webhook secret are environment "
    "variables, never committed) and `enforcement.mode` (the study's "
    "between-subjects condition, shadow/advisory/enforced)",
    "docs/GITHUB-APP-SETUP.md -- App registration, permissions, event "
    "subscriptions, private key handling, test-org install, "
    "participant-org install flow",
    "docs/BRANCH-PROTECTION-SETUP.md -- exact branch-protection settings "
    "for `enforced` mode, what each of the three modes can and cannot "
    "guarantee stated plainly, and the bypass paths that exist even when "
    "correctly configured",
]

OPEN_QUESTIONS = [
    "NOTHING in this phase has been exercised against a real GitHub App "
    "or a real repo -- this sandbox has no registered App and no "
    "confirmed outbound internet access to api.github.com. Every test "
    "(unit AND integration) mocks the GitHub API at the HTTP layer via "
    "`responses`, following GitHub's documented request/response shapes "
    "but never verified live. The phase brief's T5 (manual, real-repo, "
    "screen-recorded: enforced mode genuinely blocking a merge, then "
    "unblocking after the lead gate clears) could NOT be performed here "
    "and is left as a manual checklist item below -- this is the single "
    "most important verification still outstanding before treating "
    "`enforced` mode as proven, not just implemented.",
    "`advisory` and `enforced` modes are BYTE-FOR-BYTE IDENTICAL from "
    "this app's own code (see docs/BRANCH-PROTECTION-SETUP.md section "
    "1) -- the app was deliberately not given the Administration "
    "permission needed to configure OR even read a repo's branch-"
    "protection settings, per the phase brief's requested permission "
    "list. This means the app has NO WAY to verify that a repo recorded "
    "as `enforced` in our database is actually configured that way on "
    "GitHub's side. A repo could be silently mis-recorded and no test "
    "or telemetry event here would catch it -- this must be verified "
    "manually per participating repo (see BRANCH-PROTECTION-SETUP.md "
    "section 3), which is itself a real, ongoing operational burden for "
    "the study, not a one-time setup cost.",
    "dev_gate/lead_gate sessions (Phase 5/7) have no transition back out "
    "of CLEARED once reached. A meaningfully-changed re-push after a "
    "prior clearance is handled ENTIRELY at this phase's check-run-"
    "reporting layer (github_app.py compares the new push's functions "
    "against the last-evaluated snapshot and overrides the reported "
    "conclusion to action_required even though the session document "
    "still says CLEARED) -- but the underlying session itself never "
    "resets. A participant who opens the extension after such a re-push "
    "would confusingly see their old Cleared screen instead of a fresh "
    "gate. Fixing this properly means adding a real 'reopen' transition "
    "to Phase 5/7's FSMs, which is out of this phase's scope -- flagging "
    "rather than quietly working around it further.",
    "Phase 9's pre-PR-creation Developer gate (played on the compare "
    "page, keyed by \"{owner}__{repo}@{base}...{head}\") and this phase's "
    "webhook-driven gate (keyed by \"{owner}__{repo}#{pr_number}\", "
    "which only exists once the PR is actually opened) use DIFFERENT "
    "pr_id keys for what is conceptually the same code change. A "
    "developer who plays the gate before creating the PR gets a wasted, "
    "orphaned session and must play it again against the real PR once "
    "it exists and this webhook fires. Not silently patched (that would "
    "mean guessing which pre-creation session corresponds to which real "
    "PR, which nothing in the payload actually tells us).",
    "The webhook handler processes everything SYNCHRONOUSLY in the "
    "request (diff fetch + Checks API calls, both real outbound HTTP in "
    "production) rather than via Phase 6's already-built RQ background "
    "queue. GitHub expects a webhook delivery acknowledged within a few "
    "seconds and retries on timeout -- under real load or a slow GitHub "
    "API response this risks duplicate/racing deliveries. Not addressed "
    "this phase since the brief didn't ask for it; a production "
    "deployment should likely enqueue this instead.",
    "Only one dev_gate/lead_gate session is considered per pr_id (no "
    "GitHub-identity-to-AICodeQuest-user mapping exists anywhere in this "
    "project) -- if more than one AICodeQuest user somehow has a session "
    "against the same PR, an arbitrary one is used for the check run's "
    "reported state. Fine for this study's expected one-participant-per-"
    "PR-per-persona usage; not a general solution.",
    "The webhook subscribes to `push` per the phase brief but "
    "deliberately does not separately process it (pull_request's "
    "`synchronize` action already covers 'new commits on an open PR', "
    "and processing both risked double-evaluating the identical commit) "
    "-- acknowledged with 200 and logged as a no-op. Confirm this "
    "simplification is acceptable, or whether `push` events to a PR's "
    "branch BEFORE it has an open PR need separate handling.",
]

MANUAL_CHECKLIST = [
    "[ ] T5 (the phase brief's required manual test): on a REAL test "
    "repo you own, with the App installed and branch protection "
    "configured per docs/BRANCH-PROTECTION-SETUP.md (`enforced` mode) -- "
    "open a PR large enough to fire the trigger, confirm the merge "
    "button is genuinely blocked (try the UI button AND `gh pr merge`), "
    "clear the lead gate for real, confirm the check goes green and the "
    "merge button/CLI both unblock. Record this as a screen capture -- "
    "it is the paper's actual evidence that the gate is real, not "
    "cosmetic. Nothing in this automated demo can substitute for it.",
    "[ ] Confirm `docs/GITHUB-APP-SETUP.md`'s registration steps against "
    "the REAL GitHub App-creation UI once one exists -- written from "
    "GitHub's documentation, never walked through live.",
    "[ ] Confirm a real webhook delivery's signature actually verifies "
    "correctly end-to-end (App-configured secret -> env var -> this "
    "backend) -- the unit/integration tests use a synthetic secret and "
    "synthetic payloads throughout.",
    "[ ] Manually verify GitHub's 'Recent Deliveries' page (App's "
    "Advanced tab) shows 200 responses for real deliveries, not just "
    "mocked ones.",
]


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


class Narrator:
    def __init__(self, title: str):
        self.lines: list[str] = [f"### {title}", ""]

    def event(self, description: str, response_json: dict) -> None:
        result = response_json.get("result", {})
        personas = result.get("personas", {})
        conclusions = ", ".join(f"{p}: **{v['conclusion']}**" for p, v in personas.items()) or "(no personas -- trigger did not fire)"
        self.lines.append(f"- {description} -> {conclusions}")

    def note(self, text: str) -> None:
        self.lines.append(f"  - {text}")

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"


def _make_app(enforcement_mode: str):
    db = mongomock.MongoClient()[f"demo_phase_10_{enforcement_mode}"]
    db.repos.insert_one(
        {
            "provider": "github",
            "owner": OWNER,
            "name": REPO,
            "default_branch": "main",
            "installation_id": INSTALLATION_ID,
            "enforcement_mode": enforcement_mode,
        }
    )
    # Phase 12: evaluate_pull_request() now hard-requires an org consent
    # record -- see app/services/consent.py.
    from app.services import consent as consent_mod

    consent_mod.register_org_consent(
        db, org=OWNER, repos=[], signed_agreement_ref="demo-ref",
        permitted_data_categories=["code_snippets", "metrics"],
        retention_period_days=365, enforcement_mode=enforcement_mode,
    )
    return create_app(mongo_db=db), db


def _pr_payload(action: str, head_sha: str) -> bytes:
    return json.dumps(
        {
            "action": action,
            "pull_request": {"number": PR_NUMBER, "head": {"sha": head_sha}, "base": {"sha": "base"}},
            "repository": {"name": REPO, "owner": {"login": OWNER}},
            "installation": {"id": INSTALLATION_ID},
        }
    ).encode()


def _register_token_and_checkruns(check_run_id_start: int):
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    counter = {"n": check_run_id_start}

    def token_cb(_request):
        return 201, {}, json.dumps({"token": "tok", "expires_at": expires})

    def create_cb(_request):
        counter["n"] += 1
        return 201, {}, json.dumps({"id": counter["n"]})

    def update_cb(_request):
        return 200, {}, json.dumps({"id": counter["n"]})

    responses.add_callback(responses.POST, TOKEN_URL, callback=token_cb, content_type="application/json")
    responses.add_callback(responses.POST, CHECK_RUNS_URL, callback=create_cb, content_type="application/json")
    import re

    responses.add_callback(
        responses.PATCH,
        re.compile(rf"{CHECK_RUNS_URL}/\d+"),
        callback=update_cb,
        content_type="application/json",
    )


@responses.activate
def run_lifecycle_under_mode(enforcement_mode: str) -> str:
    app, db = _make_app(enforcement_mode)
    client = app.test_client()
    narrator = Narrator(f"Lifecycle under enforcement.mode = \"{enforcement_mode}\"")
    _register_token_and_checkruns(check_run_id_start=1000)

    responses.add(responses.GET, DIFF_URL, body=DIFF_BUGGY, status=200)
    body = _pr_payload("opened", "sha-open")
    r = client.post(
        "/github/webhook",
        data=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    narrator.event("PR opened (head_sha=sha-open)", r.get_json())

    # Developer clears their gate for real.
    pr_id = f"{OWNER}__{REPO}#{PR_NUMBER}"
    db.dev_gate_sessions.insert_one(
        {
            "pr_id": pr_id, "user_id": "demo-dev", "function_file": "app.py", "function_language": "python",
            "function_source": "def process_payment(a, b):\n    return a - b\n", "state": "CLEARED",
            "round": 1, "replay_index": 0, "turn_pointer": 0, "failed_levels": [],
            "used_perturbation_signatures": {}, "config_version": "demo",
            "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
        }
    )
    responses.add(responses.GET, DIFF_URL, body=DIFF_BUGGY, status=200)
    r = client.post(
        "/github/webhook",
        data=_pr_payload("synchronize", "sha-open"),
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(_pr_payload("synchronize", "sha-open")), "Content-Type": "application/json"},
    )
    narrator.event("developer clears their gate (same head_sha re-evaluated)", r.get_json())

    # Cosmetic re-push -- should stay success for the developer persona.
    responses.add(responses.GET, DIFF_URL, body=DIFF_COSMETIC, status=200)
    body = _pr_payload("synchronize", "sha-cosmetic")
    r = client.post(
        "/github/webhook",
        data=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    narrator.event("cosmetic re-push (reformat only, sha-cosmetic)", r.get_json())

    # Logic re-push -- should reopen for the developer persona.
    responses.add(responses.GET, DIFF_URL, body=DIFF_LOGIC_CHANGE, status=200)
    body = _pr_payload("synchronize", "sha-logic")
    r = client.post(
        "/github/webhook",
        data=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    narrator.event("logic re-push (new validation added, sha-logic)", r.get_json())
    narrator.note(
        "developer's underlying session document still says CLEARED (dev_gate.py has no "
        "'reopen' transition) -- only the reported check-run conclusion changed. See open questions."
    )

    return narrator.render()


def _run_tests() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"], cwd=REPO_ROOT / "backend", capture_output=True, text=True
    )
    status = "PASSED" if result.returncode == 0 else "FAILED"
    output = (result.stdout + result.stderr).strip()
    return f"**pytest: {status}**\n\n```\n{output}\n```"


def main() -> None:
    import os

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    os.environ.setdefault("GITHUB_APP_ID", "1")
    os.environ["GITHUB_APP_PRIVATE_KEY"] = pem
    os.environ["GITHUB_WEBHOOK_SECRET"] = WEBHOOK_SECRET

    tests_markdown = _run_tests()

    transcripts = [run_lifecycle_under_mode(mode) for mode in ("shadow", "advisory", "enforced")]
    for t in transcripts:
        print(t)
    demo_markdown = "\n".join(transcripts)

    report_path = generate_report(
        phase=10,
        title="GitHub App and Checks API enforcement",
        config_version=CONFIG_VERSION,
        summary=(
            "A GitHub App (backend/app/services/github_auth.py, "
            "github_api.py, github_app.py, backend/app/api/"
            "github_webhook.py) turns pull_request/check_run/installation "
            "webhooks into two Checks API status checks per PR -- one for "
            "the Developer's own comprehension gate, one for the Lead's "
            "NFR review -- reusing Phase 2's trigger/extraction and the "
            "SAME opaque pr_id format the Chrome extension uses, so a "
            "webhook-driven evaluation and a participant's in-browser "
            "session refer to the same gate session for an already-open "
            "PR. Re-pushes are classified cosmetic vs. meaningful via the "
            "same difflib-based similarity check Phase 2's own re-push "
            "skip uses, persisted per-PR since a webhook has no caller to "
            "supply `previous_functions` the way the extension's HTTP "
            "caller does. Three enforcement modes (shadow/advisory/"
            "enforced) are config-driven and recorded per PR in "
            "telemetry as the study's between-subjects condition; the "
            "walkthrough below runs the IDENTICAL event sequence under "
            "all three to show exactly what differs (only whether "
            "action_required is ever actually reported, per shadow "
            "mode's always-success override) and, just as importantly, "
            "what does NOT differ (advisory and enforced are identical "
            "from this app's own code -- see docs/BRANCH-PROTECTION-"
            "SETUP.md for why, and why that's the single biggest "
            "internal-validity risk for the study)."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown + "\n### Manual checklist (things automation in this phase cannot cover)\n\n" + "\n".join(MANUAL_CHECKLIST),
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
