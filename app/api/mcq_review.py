"""Lead review queue API (Phase 35).

Thin HTTP layer over app.services.mcq_review -- all ordering rules,
eligibility and governance live there; this module handles request shape,
auth and error mapping. Mirrors app/api/lead_gate.py's structure.

Four endpoints:

    GET  /lead-review/queue              what is waiting for me
    GET  /lead-review/items/<item_id>    one item; the key ONLY if answered
    POST /lead-review/items/<id>/answer  my cold answer -- reveals the key
    POST /lead-review/items/<id>/rate    quality rating, or an explicit skip

The ORDER of the last two is enforced in the service, not here, so it holds
for every caller rather than for this one.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from bson import ObjectId
from bson.errors import InvalidId

from app.services import mcq_review
from app.services.mcq_review import ReviewError, ReviewNotFoundError

mcq_review_bp = Blueprint("mcq_review", __name__, url_prefix="/lead-review")


def _is_lead(db, user_id: str) -> bool:
    """Whether this caller reviews.

    ON `role_hint`, WHICH IS SELF-DECLARED AT REGISTRATION. This is a
    PRODUCT restriction, not an authorisation, and it is the only role
    signal this system has -- there is still no researcher RBAC anywhere
    (a standing gap recorded in CLAUDE.md, and the reason `/dev-gate/*/
    override` is reachable by anyone). A participant who registered as a
    lead reviews; one who registered as a developer does not.

    WHY IT IS ENFORCED HERE AND NOT ONLY HIDDEN IN THE PANEL: hiding the
    sidebar item leaves every endpoint reachable by anyone who kept a URL,
    and a developer DID record a real review before this existed
    (dev@gmail.com, 2026-08-29), so the gap was not hypothetical. The panel
    hides the destination as well, because a visible screen that always
    refuses is worse than no screen.

    Phase 35 deliberately showed the queue to everyone, reasoning that an
    empty queue says "nothing for you" without a wrong answer being
    possible. That was wrong in practice: the queue is NOT empty for a
    developer -- other participants' items qualify -- so a developer was
    shown, and completed, work that is supposed to be an independent
    second rater's.
    """
    try:
        user = db["users"].find_one({"_id": ObjectId(user_id)}, {"role_hint": 1})
    except (InvalidId, TypeError):
        return False
    return bool(user) and user.get("role_hint") == "lead"


def _refuse_non_lead(db, user_id):
    """403 body for a caller who does not review, or None to continue."""
    if _is_lead(db, user_id):
        return None
    return (
        jsonify(
            {
                "error": "not_a_reviewer",
                "detail": (
                    "Reviewing comprehension items is the reviewing lead's role. "
                    "Your account is registered as a developer."
                ),
            }
        ),
        403,
    )


@mcq_review_bp.get("/queue")
@jwt_required()
def queue():
    db = current_app.config["DB"]
    refusal = _refuse_non_lead(db, get_jwt_identity())
    if refusal:
        return refusal
    limit = request.args.get("limit", type=int)
    return jsonify(mcq_review.list_queue(db, get_jwt_identity(), limit=limit)), 200


@mcq_review_bp.get("/items/<item_id>")
@jwt_required()
def item(item_id):
    db = current_app.config["DB"]
    refusal = _refuse_non_lead(db, get_jwt_identity())
    if refusal:
        return refusal
    try:
        return jsonify(mcq_review.get_item(db, get_jwt_identity(), item_id)), 200
    except ReviewNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ReviewError as exc:
        return jsonify({"error": str(exc)}), 400


@mcq_review_bp.post("/items/<item_id>/answer")
@jwt_required()
def answer(item_id):
    db = current_app.config["DB"]
    refusal = _refuse_non_lead(db, get_jwt_identity())
    if refusal:
        return refusal
    body = request.get_json(silent=True) or {}
    if "selected_index" not in body:
        return jsonify({"error": "selected_index is required"}), 400
    try:
        result = mcq_review.answer(
            db,
            get_jwt_identity(),
            item_id,
            body["selected_index"],
            events_collection=db["events"],
        )
    except ReviewNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ReviewError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 200


# `POST /items/<item_id>/rate` was REMOVED on 2026-08-31 along with the
# rating step it served. Gone rather than aliased, for the reason Phase 30
# gave when it deleted the lead gate's `/respond`: a live route lets a stale
# extension build keep collecting data nothing analyses.
