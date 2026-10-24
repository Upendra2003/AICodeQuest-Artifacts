"""Lead gate API: NFR selection and multiple-choice questions.

Thin HTTP layer over app.services.lead_gate -- all FSM/generation/grading
logic lives there; this module just handles request/response shape,
auth, and error mapping. Mirrors app/api/dev_game.py's structure.

Phase 30 replaced `POST /<pr_id>/respond` (a free-text paragraph, scored
by an LLM against a generated rubric) with:

  POST /<pr_id>/answer  -- {"selected_index": n}; graded against the
                           answer key committed at generation time, and
                           the response reveals that key.

`POST /<pr_id>/verify` was REMOVED on 2026-08-31 at the study lead's
direction. It asked the lead, having just answered a question, whether
that question and its marked-correct option were valid -- i.e. it asked
one person to answer an item and to judge the item in the same sitting.
Judging generated items is researcher work, done over the stored items,
and mixing it into a participant's turn made the gate longer and the
answer harder to interpret. See reports/phase-40-review.md.

`/respond` and `/verify` are GONE rather than aliased: nothing outside
this repo calls them, and leaving a route live would let a stale
extension build silently keep collecting data the study no longer
analyses. That is the same reasoning Phase 30 applied to `/respond`.

POST /nfrs accepts a `diff` field (raw unified diff text) directly in the
body, beyond what the phase brief's literal {selected, custom} example
shows -- there is still no PR-ingestion pipeline (see
reports/phase-5-review.md open questions; dev_game.py's /start hit the
identical gap and solved it the identical way, requiring the caller to
supply directly what a real pipeline would eventually provide). Flagged
again in reports/phase-7-review.md.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import consent, lead_gate
from app.services.lead_gate import (
    TERMINAL_STATES,
    GateNotFoundError,
    InvalidAnswerError,
    InvalidNfrSelectionError,
    InvalidTransitionError,
)
from app.services.llm import LLMClient
from app.workers.queue import enqueue
from app.workers.tasks import update_leaderboard_job

lead_gate_bp = Blueprint("lead_gate", __name__, url_prefix="/lead-gate")


def _llm_client() -> LLMClient:
    return LLMClient(db=current_app.config["DB"])


def _maybe_refresh_leaderboard(user_id: str, state: str) -> None:
    """Phase 11 -- mirrors app/api/dev_game.py's helper of the same name."""
    if state in TERMINAL_STATES:
        enqueue("maintenance", update_leaderboard_job, user_id)


@lead_gate_bp.get("/<pr_id>/nfrs")
@jwt_required()
def list_nfrs(pr_id):
    return jsonify({"default_nfrs": lead_gate.get_default_nfrs()}), 200


@lead_gate_bp.post("/<pr_id>/nfrs")
@jwt_required()
def select_nfrs(pr_id):
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    selected = body.get("selected") or []
    custom = body.get("custom") or []
    diff_text = body.get("diff")
    if not diff_text:
        return jsonify({"error": "diff is required"}), 400

    db = current_app.config["DB"]
    try:
        consent.check_participant_consent(db, user_id, events_collection=db["events"])
    except consent.ParticipantConsentMissingError as exc:
        return jsonify({"error": "participant_consent_missing", "detail": str(exc)}), 403
    except consent.ParticipantWithdrawnError as exc:
        return jsonify({"error": "participant_consent_withdrawn", "detail": str(exc)}), 403

    try:
        session = lead_gate.start_gate(
            db, pr_id, user_id, selected, custom, diff_text, events_collection=db["events"],
            is_practice=bool(body.get("practice", False)),
        )
    except InvalidNfrSelectionError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"state": session.state, "selected_nfrs": session.selected_nfrs}), 201


@lead_gate_bp.get("/<pr_id>/state")
@jwt_required()
def state(pr_id):
    user_id = get_jwt_identity()
    db = current_app.config["DB"]
    try:
        session = lead_gate.get_state(db, pr_id, user_id)
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    return (
        jsonify(
            {
                "state": session.state,
                "retry_index": session.retry_index,
                "selected_nfrs": session.selected_nfrs,
                "weighted_score": session.weighted_score,
                "passed": session.passed,
                "persona_order": session.persona_order,
            }
        ),
        200,
    )


@lead_gate_bp.get("/<pr_id>/current-question")
@jwt_required()
def current_question(pr_id):
    user_id = get_jwt_identity()
    db = current_app.config["DB"]
    try:
        result = lead_gate.get_current_question(
            db, pr_id, user_id, _llm_client(), events_collection=db["events"]
        )
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(result), 200


@lead_gate_bp.get("/<pr_id>/progress")
@jwt_required()
def progress(pr_id):
    """What question generation is doing right now, for the panel's
    chain-of-thought waiting screen -- the Lead gate's counterpart to
    GET /dev-gate/<pr_id>/progress, serving the same shape so both gates
    render through the same component.

    A missing session answers 200 with a null step rather than 404: this
    decorates a wait, and a poll that errors would turn the waiting
    screen into its own failure. The panel treats a null step as "all
    rows pending", which is honest -- we do not know, so we do not claim.
    """
    db = current_app.config["DB"]
    try:
        return jsonify(lead_gate.get_question_progress(db, pr_id, get_jwt_identity())), 200
    except GateNotFoundError:
        return jsonify({"step": None, "steps": []}), 200


@lead_gate_bp.post("/<pr_id>/answer")
@jwt_required()
def answer(pr_id):
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    if "selected_index" not in body:
        return jsonify({"error": "selected_index is required"}), 400

    db = current_app.config["DB"]
    try:
        result = lead_gate.submit_answer(
            db, pr_id, user_id, body["selected_index"], events_collection=db["events"]
        )
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except InvalidAnswerError as exc:
        return jsonify({"error": str(exc)}), 400
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(result), 200


@lead_gate_bp.post("/<pr_id>/advance")
@jwt_required()
def advance(pr_id):
    user_id = get_jwt_identity()
    db = current_app.config["DB"]
    try:
        session = lead_gate.advance(db, pr_id, user_id, events_collection=db["events"])
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except InvalidTransitionError as exc:
        # Phase 30: advance() can now legitimately refuse, when
        # lead_gate.verification.required is set and the current question
        # hasn't been verified. Without this arm that refusal surfaced as
        # a 500 -- caught by test_required_verification_blocks_advance.
        return jsonify({"error": str(exc)}), 409
    _maybe_refresh_leaderboard(user_id, session.state)
    return jsonify({"state": session.state}), 200


@lead_gate_bp.post("/<pr_id>/override")
@jwt_required()
def override(pr_id):
    # NOTE: no real researcher RBAC exists yet -- any authenticated user
    # can call this endpoint. Same caveat as dev_game.py's override(); see
    # reports/phase-5-review.md open questions.
    researcher_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    target_user_id = body.get("user_id")
    reason = body.get("reason")
    if not target_user_id or not reason:
        return jsonify({"error": "user_id and reason are required"}), 400

    db = current_app.config["DB"]
    try:
        session = lead_gate.override(
            db, pr_id, target_user_id, researcher_id, reason, events_collection=db["events"]
        )
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    _maybe_refresh_leaderboard(target_user_id, session.state)
    return jsonify({"state": session.state}), 200
