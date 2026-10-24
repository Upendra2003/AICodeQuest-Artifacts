"""Phase 11 demo: simulates 5 users with varied outcomes (a gold-tier
power user, a silver-tier steady clearer, a bronze user whose gate was
escalated, a bronze user whose gate was researcher-overridden, and a
brand-new participant with nothing yet), runs the REAL leaderboard
aggregate pipeline against real Mongo-shaped documents, then prints BOTH
the PUBLIC leaderboard exactly as another participant would see it
(GET /leaderboard/public) and ONE user's PRIVATE view
(GET /leaderboard/me) into the report -- side by side, so the exact same
underlying data's public vs. private shape is directly visible and it's
easy to visually confirm nothing diagnostic (per-level accuracy,
injection success rate, TTD) ever appears in the public payload.

Run via `make demo-phase-11` or directly (needs real Mongo + Redis,
`make up`):

    cd backend && ../.venv/bin/python scripts/demo_phase_11.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

import mongomock  # noqa: E402

from app import create_app  # noqa: E402
from app.core.config import CONFIG_VERSION  # noqa: E402
from app.services.leaderboard import update_leaderboard_for_user  # noqa: E402
from scripts.report import generate_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)

BUILT = [
    "backend/app/services/leaderboard.py -- raw-metrics computation from "
    "dev_gate/lead_gate sessions + attempts, the rate limit + streak "
    "sliding-window pass, tier resolution, config-driven badge "
    "evaluation, and the PUBLIC/PRIVATE split (compute_public_metrics() "
    "is the ONLY function allowed to decide what's public, and asserts "
    "against FORBIDDEN_PUBLIC_KEYS on every call)",
    "backend/app/workers/tasks.py's update_leaderboard_job -- enqueued "
    "on the RQ 'maintenance' queue (reserved for exactly this since "
    "Phase 6) from dev_game.py's/lead_gate.py's advance()/override() "
    "handlers whenever a session reaches a terminal state; deliberately "
    "NOT deduplicated via Phase 6's enqueue_unique() (see that job's "
    "docstring for why a static per-user job_id would silently drop a "
    "second completion's refresh)",
    "backend/scripts/reconcile_leaderboard.py -- nightly (cron-able) "
    "full recomputation, logging + auto-fixing any drift beyond "
    "leaderboard.reconciliation_drift_tolerance",
    "backend/app/api/leaderboard.py -- GET /leaderboard/public (every "
    "user's coarse public standing), GET /leaderboard/me (the caller's "
    "own full private view + consent status + history), GET "
    "/leaderboard/export (a GDPR-ish full data export of the caller's "
    "own raw documents, pw_hash excluded)",
    "config/params.yaml -- new `leaderboard` (tiers, anti-gaming rate "
    "limit, reconciliation tolerance) and `badges` (config-driven, no "
    "code change needed to add one) sections",
    "DevGateSessionDocument/LeadGateSessionDocument gained "
    "cleared_via_override, set by dev_gate.py's/lead_gate.py's "
    "override() -- lets the leaderboard exclude researcher-overridden "
    "clears from public standings while still surfacing them privately",
    "extension/src/popup/ -- a Profile section (tier/streak/badges/"
    "private stats/consent/data export) added to the existing popup",
    "backend/app/api/profile_page.py -- a simple, self-contained "
    "GET /profile web page (login form + the SAME JSON API, no "
    "extension needed) mirroring the popup's profile view",
]

OPEN_QUESTIONS = [
    "The rate limit (leaderboard.max_completions_per_window per "
    "rate_limit_window_hours) applies to ALL of a user's completions "
    "combined (developer + lead), not per-persona -- a simplification; "
    "confirm whether the study wants separate windows per persona "
    "instead.",
    "'No-scaffold clear' is defined here as round2_score_pct is None on "
    "the FINAL resolved session document (round 2/scaffold was never "
    "reached) -- a session that failed round 1, replayed, and passed "
    "round 1 again on the replay also satisfies this (round2_score_pct "
    "is still None for that final pass), which arguably undersells how "
    "much scaffolding the user actually needed across the whole replay "
    "history. Flagged, not fixed -- redefining it needs a decision on "
    "whether 'no scaffold, ever, across all replays' or 'no scaffold in "
    "the FINAL successful attempt' is the intended badge criterion.",
    "The extension popup's new Profile section and the standalone "
    "GET /profile web page are both implemented and manually verified "
    "against the real backend via test_client(), but NOT verified via a "
    "real browser/Playwright screenshot this phase -- the phase brief's "
    "own test list (T1+T2) is entirely backend-focused (serializer leak, "
    "aggregate correctness, reconciliation, badges, override/escalated "
    "exclusion), unlike Phase 9's explicit Playwright requirement. Worth "
    "a follow-up visual check before relying on either UI for the real "
    "study.",
    "GET /profile's JWT is held in an in-memory JS variable only (gone "
    "on refresh/tab-close) -- a deliberate simplicity tradeoff for a "
    "quick-profile-check page, not a persistent session; confirm this is "
    "acceptable rather than mildly annoying for real participants who'd "
    "want to bookmark it.",
    "No researcher-facing UI exists yet for the 'researcher sees all' "
    "half of the PRIVATE metrics requirement -- GET /leaderboard/me only "
    "returns the CALLER's own data (by design, for participant privacy), "
    "and there is no admin/researcher role or endpoint to view an "
    "arbitrary user's private metrics. A researcher today would query "
    "Mongo directly. Same no-real-RBAC gap flagged since Phase 5.",
]

MANUAL_CHECKLIST = [
    "[ ] Load the rebuilt extension, log in, open the popup's Profile "
    "section against a real backend with real gate history, and "
    "visually confirm it matches what GET /leaderboard/me returns.",
    "[ ] Open GET /profile in a real browser (not just test_client()), "
    "log in, and click 'Export my data' to confirm the browser actually "
    "downloads a JSON file (this demo only calls the endpoint directly, "
    "it doesn't drive a real browser's download UI).",
    "[ ] Confirm the nightly reconciliation cron entry (see "
    "backend/scripts/reconcile_leaderboard.py's docstring) is actually "
    "installed once this is deployed anywhere real.",
]


def _dev_session(db, pr_id, user_id, state, ts, cleared_via_override=False, round2_score_pct=None):
    db.dev_gate_sessions.insert_one(
        {
            "pr_id": pr_id, "user_id": user_id, "function_file": "m.py", "function_language": "python",
            "function_source": "def f(): pass", "state": state, "round": 1, "replay_index": 0,
            "turn_pointer": 0, "failed_levels": [],
            "round1_score_pct": 100.0 if state == "CLEARED" else 40.0,
            "round2_score_pct": round2_score_pct, "used_perturbation_signatures": {},
            "cleared_via_override": cleared_via_override, "config_version": "demo",
            "created_at": ts, "updated_at": ts,
        }
    )


def _lead_session(db, pr_id, user_id, state, ts, cleared_via_override=False):
    db.lead_gate_sessions.insert_one(
        {
            "pr_id": pr_id, "user_id": user_id, "state": state, "selected_nfrs": ["security"],
            "question_pointer": 0, "retry_index": 0, "weighted_score": 90.0 if state == "CLEARED" else 40.0,
            "passed": state == "CLEARED", "diff_text": "diff",
            "cleared_via_override": cleared_via_override, "config_version": "demo",
            "created_at": ts, "updated_at": ts,
        }
    )


def _dev_attempt_won(db, pr_id, user_id, level):
    db.dev_attempts.insert_one({"pr_id": pr_id, "user_id": user_id, "phase": "defense", "level": level, "outcome": "won"})


def _register(client, email, display_name) -> str:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "correct-horse-battery", "display_name": display_name, "role_hint": "both"},
    )
    return r.get_json()["id"]


def _login(client, email) -> str:
    return client.post("/auth/login", json={"email": email, "password": "correct-horse-battery"}).get_json()["access_token"]


def build_simulation():
    db = mongomock.MongoClient()["demo_phase_11"]
    app = create_app(mongo_db=db)
    client = app.test_client()

    dave = _register(client, "dave@example.com", "Dave (gold, power user)")
    alice = _register(client, "alice@example.com", "Alice (silver, steady clearer)")
    bob = _register(client, "bob@example.com", "Bob (bronze, one escalation)")
    carol = _register(client, "carol@example.com", "Carol (bronze, one override)")
    eve = _register(client, "eve@example.com", "Eve (brand new)")

    t = BASE_TIME

    def next_ts():
        nonlocal t
        t = t + timedelta(hours=1)
        return t

    # Dave: 15 clean dev clears (no scaffold), spanning all 4 levels ->
    # gold tier (>=15), first_clear/cleared_a1-a4/no_scaffold_clear/
    # consistency_streak_3 all earned.
    for i in range(15):
        _dev_session(db, f"dave-pr-{i}", dave, "CLEARED", next_ts())
        _dev_attempt_won(db, f"dave-pr-{i}", dave, ["A1", "A2", "A3", "A4"][i % 4])

    # Alice: 6 clean dev clears + 2 lead clears -> silver tier (8 in
    # [5,15)), same per-level/no-scaffold/lead/streak badges as Dave.
    for i in range(6):
        _dev_session(db, f"alice-pr-{i}", alice, "CLEARED", next_ts())
        _dev_attempt_won(db, f"alice-pr-{i}", alice, ["A1", "A2", "A3", "A4"][i % 4])
    for i in range(2):
        _lead_session(db, f"alice-lead-{i}", alice, "CLEARED", next_ts())

    # Bob: 2 clears then an ESCALATION (last) -- bronze tier, streak
    # reset to 0 (so no consistency_streak_3), and no per-level badges
    # (no dev_attempts recorded for him) -- but he DOES still earn
    # no_scaffold_clear, since his 2 real clears genuinely never reached
    # round 2.
    for i in range(2):
        _dev_session(db, f"bob-pr-{i}", bob, "CLEARED", next_ts())
    _dev_session(db, "bob-pr-escalated", bob, "ESCALATED", next_ts())

    # Carol: 1 dev clear via researcher OVERRIDE (excluded from public
    # entirely) + 3 real lead clears -- bronze tier, streak=3.
    _dev_session(db, "carol-pr-override", carol, "CLEARED", next_ts(), cleared_via_override=True)
    for i in range(3):
        _lead_session(db, f"carol-lead-{i}", carol, "CLEARED", next_ts())

    # Eve: nothing at all -- no gate ever completed, so no RQ job was
    # ever triggered for her; she has NO leaderboard_agg document, same
    # as a real brand-new participant.

    for user_id in (dave, alice, bob, carol):
        update_leaderboard_for_user(db, user_id)

    return client, {"dave": dave, "alice": alice, "bob": bob, "carol": carol, "eve": eve}


def _run_tests() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"], cwd=REPO_ROOT / "backend", capture_output=True, text=True
    )
    status = "PASSED" if result.returncode == 0 else "FAILED"
    output = (result.stdout + result.stderr).strip()
    return f"**pytest: {status}**\n\n```\n{output}\n```"


def main() -> None:
    tests_markdown = _run_tests()

    client, users = build_simulation()

    # "Exactly as another participant would see it" -- logged in as Eve,
    # who has nothing of her own to show yet, viewing everyone else's
    # PUBLIC standings.
    eve_token = _login(client, "eve@example.com")
    public = client.get("/leaderboard/public", headers={"Authorization": f"Bearer {eve_token}"}).get_json()

    # Eve herself: brand new, no leaderboard_agg document exists yet --
    # confirms /leaderboard/me's zeroed-default path, not an error.
    eve_me = client.get("/leaderboard/me", headers={"Authorization": f"Bearer {eve_token}"}).get_json()

    # Dave's own PRIVATE view -- the richest dataset, for maximum
    # contrast against what the public listing above showed for him.
    dave_token = _login(client, "dave@example.com")
    dave_me = client.get("/leaderboard/me", headers={"Authorization": f"Bearer {dave_token}"}).get_json()

    demo_parts = [
        "### PUBLIC leaderboard -- exactly as another participant (Eve, logged in, viewing everyone) sees it",
        "",
        "```json",
        json.dumps(public, indent=2, sort_keys=True),
        "```",
        "",
        "Note what's absent for every entry: no per-level accuracy, no injection success rate, no TTD, "
        "no replay/scaffold/escalation/override counts, no raw score -- only total_gates_cleared, tier, badges, streak.",
        "",
        "### Eve's own PRIVATE view (`GET /leaderboard/me`) -- brand new, zero gates completed",
        "",
        "```json",
        json.dumps(eve_me, indent=2, sort_keys=True),
        "```",
        "",
        "No leaderboard_agg document exists for Eve at all (no gate completion ever triggered the RQ job) -- "
        "this is a hardcoded zeroed default, not a from-scratch recompute, and not a 404.",
        "",
        "### Dave's own PRIVATE view (`GET /leaderboard/me`) -- the same user shown publicly above, now with everything",
        "",
        "```json",
        json.dumps(dave_me, indent=2, sort_keys=True),
        "```",
        "",
        "Compare against Dave's entry in the PUBLIC listing above: same total_gates_cleared/tier/badges/streak, "
        "PLUS per_level_defense_win_rate, per_level_attack_win_rate, ttd_ms_avg_by_level, replay_count, "
        "scaffold_exposures, escalated_count, override_count -- exactly the fields FORBIDDEN_PUBLIC_KEYS blocks "
        "from ever appearing in the public payload.",
        "",
        "### Manual checklist (things automation in this phase cannot cover)",
        "",
        "\n".join(MANUAL_CHECKLIST),
    ]
    demo_markdown = "\n".join(demo_parts)
    print(demo_markdown)

    report_path = generate_report(
        phase=11,
        title="Leaderboard and profile",
        config_version=CONFIG_VERSION,
        summary=(
            "The leaderboard is maintained as an aggregate (one "
            "LeaderboardAggDocument per user), refreshed via an RQ "
            "'maintenance'-queue job whenever a dev_gate/lead_gate "
            "session reaches a terminal state, plus a nightly (cron-able) "
            "reconciliation job that recomputes from raw attempts and "
            "logs+fixes any drift. PUBLIC metrics are deliberately coarse "
            "-- total gates cleared, a banded tier, badges, a streak -- "
            "specifically so a participant can never reverse-engineer "
            "which perturbation types the AI opponent fails to fix; "
            "compute_public_metrics() is the single function allowed to "
            "decide what's public and asserts this on every call. "
            "Badges are entirely config-driven (id/name/tier/metric/op/"
            "value in config/params.yaml, no code change to add one). "
            "ESCALATED sessions and researcher-overridden clears "
            "(a new cleared_via_override field) are excluded from public "
            "standings but remain visible in the private/researcher view "
            "and in a new GDPR-ish full data export endpoint. A property-"
            "based test (hypothesis) extends Phase 1's single hardcoded "
            "serializer-leak example across randomly generated aggregate "
            "documents AND randomly generated raw metrics, asserting no "
            "private field name or forbidden metric ever reaches a "
            "public payload. A hand-verified 5-user/20-gate simulation "
            "confirms the incrementally-maintained aggregate and a full "
            "from-scratch reconciliation never disagree."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
