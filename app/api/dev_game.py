"""Developer gate API: Defense/Attack rounds.

Thin HTTP layer over app.services.dev_gate -- all FSM/grading logic lives
there; this module just handles request/response shape, auth, and error
mapping.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import consent, dev_gate, gamification
from app.services.diff_scope import detect_language
from app.services.dev_gate import TERMINAL_STATES, GateNotFoundError, InvalidTransitionError, SubmissionConflictError
from app.services.llm import LLMClient
from app.workers.queue import enqueue
from app.workers.tasks import update_leaderboard_job

dev_game_bp = Blueprint("dev_game", __name__, url_prefix="/dev-gate")


def _llm_client() -> LLMClient:
    return LLMClient(db=current_app.config["DB"])


def _runner():
    return current_app.config["RUNNER"]


def _maybe_refresh_leaderboard(user_id: str, state: str) -> None:
    """Phase 11: any transition INTO a terminal state (CLEARED/ESCALATED/
    SKIPPED_SIMILARITY) is a "gate completion" for leaderboard purposes --
    refresh that user's aggregate via the RQ "maintenance" queue rather
    than blocking this request on a synchronous recompute. Enqueued
    unconditionally on every terminal-state response (not just the exact
    request that CAUSED the transition) -- cheap, idempotent, and simpler
    than tracking "did this specific call just transition", since
    update_leaderboard_for_user always recomputes from current data
    anyway (see app/workers/tasks.py's update_leaderboard_job docstring)."""
    if state in TERMINAL_STATES:
        enqueue("maintenance", update_leaderboard_job, user_id)


def _refresh_leaderboard_after_turn(user_id: str) -> None:
    """Phase 27: a GRADED TURN changes public metrics too.

    Phase 11 refreshed the aggregate only on a terminal transition, which
    was right when every public metric was about COMPLETIONS. Phase 24
    added per-turn ones -- bug_detection_accuracy, bug_injection_success_
    rate, the typical timings and their sample sizes -- and those change
    on every submission. Left as it was, a participant could play twenty
    turns and watch none of their numbers move, which is exactly what was
    reported ("played 2 games and it didn't update correctly").

    Same queue, same job, same idempotence as the terminal refresh: the
    job always recomputes from current data, so an extra enqueue is
    cheap and never wrong. At most ~16 per session."""
    enqueue("maintenance", update_leaderboard_job, user_id)


@dev_game_bp.post("/<pr_id>/start")
@jwt_required()
def start(pr_id):
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    function = body.get("function") or {}
    if not all(k in function for k in ("file", "language", "source")):
        return jsonify({"error": "function {file, language, source} is required"}), 400

    # The caller's `language` is a HINT, not the authority. A client whose
    # own extension->language table has gone stale (which is exactly what
    # happened to the extension's hand-copied map after config grew to 8
    # languages) sends language=null, and an empty grammar name reaches
    # tree-sitter as a 500 several layers down. Resolve it from the file
    # path against config's `languages` table instead, and refuse clearly
    # when the file genuinely isn't a language this project supports.
    language = function["language"] or detect_language(function["file"] or "")
    if not language:
        return (
            jsonify(
                {
                    "error": "unsupported_language",
                    "detail": (
                        f"no supported language for {function['file']!r} "
                        "(see config/params.yaml `languages`)"
                    ),
                }
            ),
            400,
        )

    db = current_app.config["DB"]
    try:
        consent.check_participant_consent(db, user_id, events_collection=db["events"])
    except consent.ParticipantConsentMissingError as exc:
        return jsonify({"error": "participant_consent_missing", "detail": str(exc)}), 403
    except consent.ParticipantWithdrawnError as exc:
        return jsonify({"error": "participant_consent_withdrawn", "detail": str(exc)}), 403

    session = dev_gate.start_gate(
        db,
        pr_id,
        user_id,
        function["file"],
        language,
        function["source"],
        events_collection=db["events"],
        is_practice=bool(body.get("practice", False)),
        # Phase 20: every supported-language file in the PR diff. The backend
        # runs tree-sitter over them to build the pool of functions turns can
        # ask about. Optional -- a caller that sends only `function` gets the
        # previous single-function behaviour.
        files=body.get("files"),
    )
    return jsonify({"state": session.state}), 201


@dev_game_bp.post("/<pr_id>/restart")
@jwt_required()
def restart(pr_id):
    """Phase 22: start this gate over from turn 1, on the same code.

    Distinct from /start, which RESUMES an existing session by design and
    reopens only when the pushed code genuinely changed. This is the
    participant explicitly abandoning the current playthrough.
    """
    user_id = get_jwt_identity()
    db = current_app.config["DB"]
    try:
        session = dev_gate.restart_gate(db, pr_id, user_id, events_collection=db["events"])
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except dev_gate.RestartNotAllowedError as exc:
        return jsonify({"error": "restart_not_allowed", "detail": str(exc)}), 409
    return jsonify({"state": session.state, "manual_restart_count": session.manual_restart_count}), 200


@dev_game_bp.get("/<pr_id>/state")
@jwt_required()
def state(pr_id):
    user_id = get_jwt_identity()
    db = current_app.config["DB"]
    try:
        session = dev_gate.get_state(db, pr_id, user_id)
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    return (
        jsonify(
            {
                "state": session.state,
                "round": session.round,
                "replay_index": session.replay_index,
                "round1_score_pct": session.round1_score_pct,
                "round2_score_pct": session.round2_score_pct,
                "failed_levels": session.failed_levels,
                "persona_order": session.persona_order,
                # Phase 15: null unless this session was skipped as a
                # cosmetic re-push. Lets the panel show WHAT was compared
                # instead of only asserting that nothing changed.
                "similarity_skip_detail": session.similarity_skip_detail,
                # Phase 20: running Developer-vs-AI tally for the panel header.
                "scoreboard": dev_gate.compute_scoreboard(db, pr_id, user_id),
                # Phase 22: whether to offer the panel's "Start over"
                # button at all. Computed here rather than left to the
                # client to infer from config it cannot read -- and so a
                # disabled/ESCALATED/scrubbed session never shows a button
                # whose only possible outcome is a 409.
                "restart_available": dev_gate.can_restart(session),
                "manual_restart_count": session.manual_restart_count,
            }
        ),
        200,
    )


@dev_game_bp.get("/<pr_id>/progress")
@jwt_required()
def turn_progress(pr_id: str):
    """What turn generation is doing right now (Phase 29).

    Feeds the panel's waiting screen, which shows the real pipeline rather
    than a spinner. Cheap and read-only: one indexed document lookup, polled
    only while a participant is actually waiting.
    """
    db = current_app.config["DB"]
    try:
        return jsonify(dev_gate.get_turn_progress(db, pr_id, get_jwt_identity())), 200
    except GateNotFoundError:
        return jsonify({"error": "gate_not_found"}), 404


@dev_game_bp.get("/<pr_id>/current-task")
@jwt_required()
def current_task(pr_id):
    user_id = get_jwt_identity()
    db = current_app.config["DB"]
    try:
        result = dev_gate.get_current_task(
            db, pr_id, user_id, _llm_client(), _runner(), events_collection=db["events"]
        )
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(result), 200


@dev_game_bp.post("/<pr_id>/mcq/answer")
@jwt_required()
def mcq_answer(pr_id):
    """Record the developer's choice on this turn's comprehension question.

    Not scored into the gate (see dev_gate.answer_mcq). The response never
    says whether the answer was right: telling the participant would let
    them infer the key across turns, and there is no gameplay reason to --
    the gate's verdict comes from the repair task.
    """
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    item_id = body.get("item_id")
    selected_index = body.get("selected_index")
    if not item_id or selected_index is None:
        return jsonify({"error": "item_id and selected_index are required"}), 400

    db = current_app.config["DB"]
    try:
        result = dev_gate.answer_mcq(
            db, pr_id, user_id, item_id, selected_index, events_collection=db["events"]
        )
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 200


@dev_game_bp.post("/<pr_id>/defense/submit")
@jwt_required()
def defense_submit(pr_id):
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    if "code" not in body:
        return jsonify({"error": "code is required"}), 400

    db = current_app.config["DB"]
    try:
        result = dev_gate.submit_defense(
            db,
            pr_id,
            user_id,
            body["code"],
            _runner(),
            # Phase 19: needed by the execution-free grading path's LLM
            # equivalence judge (grading.py), used when this turn's function
            # had no runnable harness and therefore no test suite.
            llm_client=_llm_client(),
            client_rendered_at_ms=body.get("client_rendered_at_ms"),
            idempotency_key=body.get("idempotency_key"),
            events_collection=db["events"],
        )
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (InvalidTransitionError, SubmissionConflictError) as exc:
        return jsonify({"error": str(exc)}), 409
    _refresh_leaderboard_after_turn(user_id)
    # Phase 42: the celebration rides on the RESPONSE, computed
    # synchronously from one count, rather than being read back off the
    # leaderboard aggregate the line above just enqueued. That aggregate
    # refreshes on the RQ `maintenance` queue and lags the turn that earned
    # it -- and does not refresh at all when the worker is down, which has
    # happened repeatedly here. See services/gamification.py.
    milestone = gamification.milestone_after_defense_win(
        db, user_id, pr_id, result.get("outcome"), events_collection=db["events"]
    )
    if milestone is not None:
        result = {**result, "milestone": milestone}
    return jsonify(result), 200


@dev_game_bp.post("/<pr_id>/attack/submit")
@jwt_required()
def attack_submit(pr_id):
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    if "code" not in body:
        return jsonify({"error": "code is required"}), 400

    db = current_app.config["DB"]
    try:
        result = dev_gate.submit_attack(
            db,
            pr_id,
            user_id,
            body["code"],
            _runner(),
            _llm_client(),
            client_rendered_at_ms=body.get("client_rendered_at_ms"),
            idempotency_key=body.get("idempotency_key"),
            events_collection=db["events"],
        )
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (InvalidTransitionError, SubmissionConflictError) as exc:
        return jsonify({"error": str(exc)}), 409
    _refresh_leaderboard_after_turn(user_id)
    return jsonify(result), 200


@dev_game_bp.post("/<pr_id>/attack/skip")
@jwt_required()
def attack_skip(pr_id):
    user_id = get_jwt_identity()
    db = current_app.config["DB"]
    try:
        result = dev_gate.skip_attack(db, pr_id, user_id, events_collection=db["events"])
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(result), 200


@dev_game_bp.post("/<pr_id>/advance")
@jwt_required()
def advance(pr_id):
    user_id = get_jwt_identity()
    db = current_app.config["DB"]
    try:
        session = dev_gate.advance(db, pr_id, user_id, events_collection=db["events"])
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    _maybe_refresh_leaderboard(user_id, session.state)
    return jsonify({"state": session.state}), 200


@dev_game_bp.post("/<pr_id>/override")
@jwt_required()
def override(pr_id):
    # NOTE: no real researcher RBAC exists yet -- any authenticated user
    # can call this endpoint. Flagged explicitly in
    # reports/phase-5-review.md; a real deployment MUST add a role check
    # here before this goes anywhere near real participants.
    researcher_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    target_user_id = body.get("user_id")
    reason = body.get("reason")
    if not target_user_id or not reason:
        return jsonify({"error": "user_id and reason are required"}), 400

    db = current_app.config["DB"]
    try:
        session = dev_gate.override(
            db, pr_id, target_user_id, researcher_id, reason, events_collection=db["events"]
        )
    except GateNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    _maybe_refresh_leaderboard(target_user_id, session.state)
    return jsonify({"state": session.state}), 200
