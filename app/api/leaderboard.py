"""Leaderboard + profile API (Phase 11).

GET /leaderboard/public is the ONLY endpoint that returns other users'
data, and it returns ONLY LeaderboardAggDocument.to_public_dict()-shaped
output plus a display_name -- never private_metrics, never raw session/
attempt documents. GET /leaderboard/me and GET /leaderboard/export return
the CALLER's own data only (their JWT identity, never a caller-supplied
user_id) -- there is no "view another user's private profile" endpoint
at all, intentionally.
"""

from __future__ import annotations

from datetime import datetime, timezone

import logging

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.core.config import get_config
from app.services.leaderboard import (
    PERSONA_SORT_KEYS,
    compute_private_metrics,
    compute_public_metrics,
)
from app.services.report_docx import build_progress_report

logger = logging.getLogger(__name__)

leaderboard_bp = Blueprint("leaderboard", __name__, url_prefix="/leaderboard")

EMPTY_RAW_METRICS = {
    "total_gates_cleared": 0,
    "dev_gates_cleared": 0,
    "lead_gates_cleared": 0,
    "current_streak": 0,
    "no_scaffold_clears": 0,
    "won_defense_turns_a1": 0,
    "won_defense_turns_a2": 0,
    "won_defense_turns_a3": 0,
    "won_defense_turns_a4": 0,
    "longest_streak": 0,
    "escalated_count": 0,
    "override_count": 0,
    "replay_count": 0,
    "scaffold_exposures": 0,
    "per_level_defense_win_rate": {"A1": None, "A2": None, "A3": None, "A4": None},
    "per_level_attack_win_rate": {"A1": None, "A2": None, "A3": None, "A4": None},
    "ttd_ms_avg_by_level": {"A1": None, "A2": None, "A3": None, "A4": None},
}


def _jsonify_value(value):
    """Recursively converts ObjectId/datetime into JSON-safe values for
    the data-export endpoint -- raw Mongo documents contain both."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonify_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify_value(v) for v in value]
    return value


@leaderboard_bp.get("/public")
@jwt_required()
def public_leaderboard():
    """Every user's PUBLIC standing -- deliberately coarse, never
    private_metrics.

    Phase 30 split this into TWO boards, selected by `?persona=`:
    `developer` (the default, so every existing caller is unaffected) and
    `lead`. They rank on different metrics because they measure different
    things -- averaging "found a planted bug" with "picked the right
    non-functional consequence" produces a number that means nothing.

    Each board EXCLUDES participants who have never played that persona.
    Without that, the lead board is mostly developers sitting at zero,
    which is both noise and a small unkindness: it publicly lists people
    as bottom-ranked at something they never attempted.
    """
    persona = request.args.get("persona", "developer")
    if persona not in PERSONA_SORT_KEYS:
        return jsonify({"error": f"unknown persona {persona!r}"}), 400

    db = current_app.config["DB"]
    entries = []
    for agg in db["leaderboard_agg"].find({}):
        metrics = agg.get("public_metrics", {})
        if not _has_played(persona, metrics):
            continue
        user = db["users"].find_one({"_id": ObjectId(agg["_id"])}, {"display_name": 1})
        entries.append(
            {
                "user_id": agg["_id"],
                "display_name": user["display_name"] if user else "(unknown)",
                "public_metrics": metrics,
                "updated_at": agg["updated_at"].isoformat(),
            }
        )

    entries.sort(key=lambda e: _sort_key(persona, e["public_metrics"]), reverse=True)
    return jsonify({"persona": persona, "leaderboard": entries}), 200


# How "has this person played this persona at all" is decided. Deliberately
# an ACTIVITY check, not a clears check: someone mid-way through their
# first gate belongs on the board, at the bottom, rather than being
# invisible until they finish.
_PLAYED_SIGNALS = {
    "developer": ("turns_played", "dev_gates_cleared"),
    "lead": ("lead_questions_answered", "lead_gates_cleared"),
}


def _has_played(persona: str, metrics: dict) -> bool:
    return any((metrics.get(key) or 0) > 0 for key in _PLAYED_SIGNALS[persona])


def _sort_key(persona: str, metrics: dict) -> tuple:
    """Descending sort key for a board. A null rate sorts BELOW 0.0 (-1
    here) rather than being coerced to zero: "never answered one" and
    "got them all wrong" are different, and only the second has earned a
    place above the other."""
    return tuple(
        (metrics.get(key) if metrics.get(key) is not None else -1)
        for key in PERSONA_SORT_KEYS[persona]
    )


def _consent_status(db, user_id: str) -> dict | None:
    """The caller's participant-consent record, or None if unsigned.

    Withdrawn counts as NOT consented for display purposes -- a withdrawn
    participant must never see a screen implying they are still enrolled --
    but the withdrawal itself is reported so the state is legible.
    """
    doc = db["participant_consents"].find_one({"user_id": user_id})
    if doc is None:
        return None
    if doc.get("withdrawn"):
        return {"withdrawn": True, "withdrawn_at": _jsonify_value(doc.get("withdrawn_at"))}
    return {
        "version": doc["version"],
        "signed_at": _jsonify_value(doc["signed_at"]),
        "retain_code_snippets": doc.get("retain_code_snippets", False),
        "retain_transcripts": doc.get("retain_transcripts", False),
        "allow_quotes_in_publication": doc.get("allow_quotes_in_publication", False),
        "withdrawn": False,
    }


@leaderboard_bp.get("/me")
@jwt_required()
def my_profile():
    """The caller's own PRIVATE view: full metrics breakdown, badges,
    consent status, and completion history -- never derived from a
    caller-supplied user_id, always the JWT identity."""
    user_id = get_jwt_identity()
    db = current_app.config["DB"]

    user = db["users"].find_one({"_id": ObjectId(user_id)})
    if user is None:
        return jsonify({"error": "user not found"}), 404

    agg = db["leaderboard_agg"].find_one({"_id": user_id})
    if agg is not None:
        public_metrics = agg["public_metrics"]
        private_metrics = agg["private_metrics"]
        updated_at = agg["updated_at"].isoformat()
    else:
        # No gate completed yet -- a fresh participant sees an empty
        # profile, not a 404. This is a fixed, hardcoded default, not a
        # from-scratch recompute -- the "maintained as an aggregate"
        # locked decision is about not silently recomputing a STALE
        # stored aggregate on every read, not about refusing to show
        # zeros before one has ever been written.
        public_metrics = compute_public_metrics(EMPTY_RAW_METRICS)
        private_metrics = compute_private_metrics(EMPTY_RAW_METRICS)
        updated_at = None

    dev_history = list(
        db["dev_gate_sessions"].find({"user_id": user_id}, {"pr_id": 1, "state": 1, "updated_at": 1, "_id": 0})
    )
    lead_history = list(
        db["lead_gate_sessions"].find({"user_id": user_id}, {"pr_id": 1, "state": 1, "updated_at": 1, "_id": 0})
    )
    history = sorted(
        [{"persona": "developer", **h} for h in dev_history] + [{"persona": "lead", **h} for h in lead_history],
        key=lambda h: h["updated_at"],
        reverse=True,
    )

    return (
        jsonify(
            {
                "user_id": user_id,
                "display_name": user["display_name"],
                # Phase 25 FIX: read the real consent record.
                #
                # This read `user.get("consent")` -- Phase 0/1's
                # UserDocument.consent field, whose signing flow was never
                # built and which Phase 12 superseded with
                # `participant_consents` (see CLAUDE.md's own note on it).
                # Nothing has ever written that field, so this returned
                # null for EVERY participant, and both the popup and the
                # profile page displayed "not yet consented" to people who
                # had signed. Reported from real use (2026-08-05).
                "consent": _consent_status(db, user_id),
                "public_metrics": public_metrics,
                "private_metrics": private_metrics,
                # Phase 37: every badge that EXISTS, not just the ones
                # earned. `public_metrics.badges` carries the earned ones;
                # the Badges screen needs the locked ones too, because a
                # grid showing only what you already have says nothing
                # about what to do next -- which is the whole reason the
                # ladders are tiered.
                #
                # Straight from the config table, so adding a badge stays
                # a config change with no code behind it (the property
                # Phase 11 built the table for). No thresholds are secret:
                # `value` is what the card shows progress against.
                "badge_catalogue": get_config()["badges"],
                "updated_at": updated_at,
                "history": _jsonify_value(history),
            }
        ),
        200,
    )


@leaderboard_bp.get("/report.docx")
@jwt_required()
def progress_report():
    """The caller's OWN progress report as a .docx with charts.

    Phase 28. `/export` returns a GDPR-ish JSON dump -- the right artefact
    for a data request and the wrong one for a person. This is the same
    underlying data, built into something a developer can read: standing,
    accuracy per bug class, turn outcomes, time per turn.

    Same scope as `/me`: the caller's own data, including private_metrics
    (which a participant is entitled to about themselves), and no other
    participant's data at all -- the only comparison is the caller's own
    rank, which the panel already shows them.
    """
    user_id = get_jwt_identity()
    db = current_app.config["DB"]

    user = db["users"].find_one({"_id": ObjectId(user_id)})
    if user is None:
        return jsonify({"error": "user not found"}), 404

    agg = db["leaderboard_agg"].find_one({"_id": user_id})
    if agg is not None:
        public_metrics, private_metrics = agg["public_metrics"], agg["private_metrics"]
    else:
        # A participant who has not finished anything still gets a report,
        # with every chart replaced by its "nothing here yet" sentence.
        public_metrics = compute_public_metrics(EMPTY_RAW_METRICS)
        private_metrics = compute_private_metrics(EMPTY_RAW_METRICS)

    turns = list(
        db["dev_attempts"]
        .find({"user_id": user_id, "outcome": {"$ne": None}})
        .sort("submitted_at", 1)
    )

    # Rank among everyone with a stored aggregate, computed the same way
    # GET /leaderboard/public sorts.
    standings = sorted(
        db["leaderboard_agg"].find({}, {"public_metrics": 1}),
        key=lambda a: (
            a.get("public_metrics", {}).get("total_gates_cleared", 0),
            a.get("public_metrics", {}).get("streak", 0),
        ),
        reverse=True,
    )
    ids = [str(a["_id"]) for a in standings]
    rank = ids.index(user_id) + 1 if user_id in ids else None

    try:
        stream = build_progress_report(
            user=user,
            public_metrics=public_metrics,
            private_metrics=private_metrics,
            turns=turns,
            rank=rank,
            total_participants=len(ids) or None,
            consent=_consent_status(db, user_id),
            # Phase 37: which report to build. `role_hint` is self-declared
            # at registration and is the only role signal this system has;
            # build_progress_report falls back to the developer report for
            # anything it does not recognise, which is the flow every
            # participant plays at least once.
            persona=user.get("role_hint"),
        )
    except Exception:  # noqa: BLE001
        # A report is a nicety; failing it must not look like a broken
        # account. The JSON export remains available regardless.
        logger.exception("could not build the progress report for user_id=%s", user_id)
        return jsonify({"error": "could not build the report"}), 500

    filename = f"aicodequest-report-{datetime.now(timezone.utc):%Y%m%d}.docx"
    return send_file(
        stream,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=filename,
    )


@leaderboard_bp.get("/export")
@jwt_required()
def export_data():
    """A GDPR-ish full data export of the caller's OWN raw data -- every
    document referencing their user_id, across every collection this
    project writes to, minus pw_hash (never exported, even to the user
    it belongs to -- it's a hash, not a secret they'd recognize, and
    exporting it is needless exposure surface for zero benefit)."""
    user_id = get_jwt_identity()
    db = current_app.config["DB"]

    user = db["users"].find_one({"_id": ObjectId(user_id)})
    if user is None:
        return jsonify({"error": "user not found"}), 404
    user_export = _jsonify_value({k: v for k, v in user.items() if k != "pw_hash"})

    export = {
        "user": user_export,
        "dev_gate_sessions": _jsonify_value(list(db["dev_gate_sessions"].find({"user_id": user_id}))),
        "dev_attempts": _jsonify_value(list(db["dev_attempts"].find({"user_id": user_id}))),
        "lead_gate_sessions": _jsonify_value(list(db["lead_gate_sessions"].find({"user_id": user_id}))),
        "lead_attempts": _jsonify_value(list(db["lead_attempts"].find({"user_id": user_id}))),
        "leaderboard_agg": _jsonify_value(db["leaderboard_agg"].find_one({"_id": user_id})),
    }
    return jsonify(export), 200
