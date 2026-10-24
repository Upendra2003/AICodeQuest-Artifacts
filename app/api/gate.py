"""Gate API: diff-scoping + trigger decision (Phase 2 scope only — no
comprehension quiz/gate-state logic yet; that's a later phase).

ai_tagged_ranges is mandatory in the request (may be an empty list) so the
study always captures an explicit developer self-tag rather than silently
defaulting to "nothing is AI-generated".
"""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.core.config import get_config
from app.services import consent, dev_gate
from app.services.diff_scope import AiTaggedRange, build_change_set, evaluate_trigger
from app.services.extraction import extract_functions
from app.services.records import record_evaluation
from app.services.similarity import compute_diff_fingerprint, should_skip_regating
from app.telemetry.events import log_event

logger = logging.getLogger(__name__)

gate_bp = Blueprint("gate", __name__, url_prefix="/gate")


@gate_bp.post("/evaluate")
@jwt_required(optional=True)
def evaluate():
    # Optional, not required: an unauthenticated caller (or one that
    # can't supply the extension's own pr_id -- see below) still gets a
    # normal trigger decision, exactly as before this parameter existed.
    # It's only needed for the Phase 13 similarity-skip wiring below.
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}

    for field in ("repo", "pr_number", "diff"):
        if field not in body:
            return jsonify({"error": f"{field} is required"}), 400
    if "ai_tagged_ranges" not in body:
        return jsonify({"error": "ai_tagged_ranges is required (may be an empty list)"}), 400
    if not isinstance(body["ai_tagged_ranges"], list):
        return jsonify({"error": "ai_tagged_ranges must be a list"}), 400

    repo = body["repo"]
    pr_number = body["pr_number"]
    diff_text = body["diff"]

    # Phase 12: no PR from a repo without an org consent record may be
    # processed at all -- checked before any diff parsing work, since even
    # looking at the diff to decide the trigger is itself processing.
    db = current_app.config["DB"]
    owner, _, repo_name = repo.partition("/")
    try:
        org_consent = consent.check_org_consent(db, owner, repo_name, events_collection=db["events"])
    except consent.OrgConsentMissingError as exc:
        return jsonify({"error": "org_consent_missing", "detail": str(exc)}), 403
    # Phase 36: under `consent.org_consent_mode: advisory` the call above
    # returns None instead of raising, so the gate works on any owner. The
    # absence is carried onto the evaluation record rather than forgotten --
    # "we do not know whether this organisation agreed" must stay
    # answerable from the data.
    org_consent_present = org_consent is not None

    try:
        ai_tagged_ranges = [AiTaggedRange(**r) for r in body["ai_tagged_ranges"]]
    except (TypeError, ValidationError):
        return jsonify(
            {"error": "each ai_tagged_ranges entry needs file_path, start_line, end_line"}
        ), 400

    try:
        change_set = build_change_set(diff_text, ai_tagged_ranges)
    except Exception as exc:  # unidiff raises its own exception types on some malformed diffs
        return jsonify({"error": f"could not parse diff: {exc}"}), 400

    # unidiff silently parses unrecognizable text to zero files rather than
    # raising (verified directly, not assumed) — a non-empty diff that
    # yields nothing is almost certainly malformed input, not a real "no
    # changes" diff, which would normally be an empty string.
    if not change_set.files and diff_text.strip():
        return jsonify({"error": "diff produced no parseable files — is this a valid unified diff?"}), 400

    trigger_decision = evaluate_trigger(change_set)
    logger.info(
        "gate/evaluate repo=%s pr=%s ai_tagged_ranges=%d fires=%s reasons=%s metrics=%s",
        repo,
        pr_number,
        len(ai_tagged_ranges),
        trigger_decision.fires,
        trigger_decision.reasons,
        trigger_decision.metrics,
    )

    functions = []
    for f in change_set.files:
        if f.ignored or f.language is None or not f.new_content:
            continue
        changed_ranges = [
            (h.new_start, h.new_start + h.new_lines - 1) for h in f.hunks if h.new_lines > 0
        ]
        if not changed_ranges:
            continue
        functions.extend(extract_functions(f.file_path, f.language, f.new_content, changed_ranges))

    fingerprint = compute_diff_fingerprint(functions)

    # Re-push similarity skip: this phase has no PR persistence to look up
    # a prior evaluation's functions itself (see reports/phase-2-review.md
    # open questions), so the caller may optionally supply what it stored
    # from the previous /gate/evaluate response as `previous_functions`:
    # [{file, qualified_name, source, language}, ...].
    db = current_app.config["DB"]
    threshold = get_config()["trigger"]["similarity_skip_threshold"]
    previous_functions = body.get("previous_functions") or []
    prev_by_key = {(pf["file"], pf["qualified_name"]): pf for pf in previous_functions}

    skipped_functions = []
    for func in functions:
        prev = prev_by_key.get((func.file, func.qualified_name))
        if prev is None:
            continue
        skip, score = should_skip_regating(prev["source"], func.source, func.language, threshold)
        if not skip:
            continue
        skipped_functions.append(
            {
                "file": func.file,
                "qualified_name": func.qualified_name,
                "similarity": score,
                # Phase 16: the two versions actually compared, so the skip
                # screen can show a real diff instead of only asserting a
                # percentage. Kept out of the telemetry event (which still
                # logs just the score) -- this is for the participant's own
                # screen, and app/services/minimization.py already governs
                # how long it survives on a terminal session.
                "previous_source": prev["source"],
                "current_source": func.source,
            }
        )
        log_event(
            event_type="gate_skipped_similarity",
            actor_id="system",
            pr_ref=f"{repo}#{pr_number}",
            payload={"file": func.file, "qualified_name": func.qualified_name, "similarity": score},
            collection=db["events"],
        )

    # Phase 13: a genuinely cosmetic re-push (every changed function that
    # HAD a previous counterpart is similarity-skippable, and at least
    # one such function exists) transitions an existing, non-terminal
    # Developer gate session straight to SKIPPED_SIMILARITY -- Scenario F.
    # dev_gate.similarity_skip() (Phase 2/5) has existed since Phase 5 but
    # was never actually wired to any caller until now (see
    # reports/phase-13-review.md): before this, /gate/evaluate computed
    # skipped_functions purely as informational data and never acted on
    # it. A NEW function that has no previous counterpart means this is
    # NOT a pure cosmetic re-push, so it's excluded from the "all
    # cosmetic" check. Requires both an authenticated caller (to know
    # WHOSE session) and a caller-supplied pr_id (the extension's own
    # opaque key, not reconstructable from repo/pr_number alone for the
    # pre-PR "new_pr" format) -- silently skipped, not an error, if
    # either is missing, exactly like the trigger decision was before
    # this existed.
    functions_with_prev = [f for f in functions if (f.file, f.qualified_name) in prev_by_key]

    # NOTHING CHANGED AT ALL is not a re-push, cosmetic or otherwise.
    #
    # The extension re-caches `previous_functions` on EVERY /gate/evaluate
    # call, keyed only by pr_id (see the service worker's EVALUATE_GATE
    # handler). So any second evaluation of the same head commit -- a page
    # reload, an SPA re-navigation, re-submitting the tagging panel --
    # replays the previous call's own functions back as
    # `previous_functions`, and every function gets compared against a
    # byte-identical copy of itself for a similarity of exactly 1.0.
    #
    # Without this guard that reads as "every changed function is
    # cosmetically similar" and terminates a live session into
    # SKIPPED_SIMILARITY, which is a TERMINAL state -- so a participant who
    # merely reloaded the PR page had their gate permanently dismissed,
    # and a genuine code change afterwards could never reopen it. Reported
    # from real use; reproduced against the real backend before fixing
    # (reports/phase-15-review.md).
    #
    # Byte-identical is the right test rather than a similarity score: a
    # genuinely cosmetic re-push (reformatting, a comment edit) DOES change
    # the source and must still skip, which is Phase 13's Scenario F.
    nothing_changed = (
        bool(functions)
        and len(functions_with_prev) == len(functions)
        # Also require the previous set to hold no EXTRA functions: one
        # that disappeared between evaluations is a real change.
        and len(prev_by_key) == len(functions)
        and all(
            prev_by_key[(f.file, f.qualified_name)]["source"] == f.source for f in functions_with_prev
        )
    )
    # Every current function must have a previous counterpart (a brand-new
    # function present in `functions` but absent from `functions_with_prev`
    # means genuinely new code was added -- NOT a pure cosmetic re-push,
    # even if everything else is skippable) AND every one of those matched
    # functions must itself be skippable.
    all_cosmetic = (
        bool(functions)
        and not nothing_changed
        and len(functions_with_prev) == len(functions)
        and len(skipped_functions) == len(functions_with_prev)
    )
    pr_id = body.get("pr_id")
    similarity_skipped = False
    if all_cosmetic and user_id and pr_id:
        session_doc = db[dev_gate.SESSIONS].find_one({"pr_id": pr_id, "user_id": user_id})
        if session_doc is not None and session_doc["state"] not in {
            s.value for s in dev_gate.TERMINAL_STATES
        }:
            avg_similarity = sum(s["similarity"] for s in skipped_functions) / len(skipped_functions)
            dev_gate.similarity_skip(
                db,
                pr_id,
                user_id,
                avg_similarity,
                events_collection=db["events"],
                detail={
                    "threshold": threshold,
                    "average_similarity": avg_similarity,
                    "functions": skipped_functions,
                },
            )
            similarity_skipped = True

    # Phase 24: persist the evaluation itself. Until now this endpoint
    # decided whether to block a pull request, on which functions, from
    # which self-tagged authorship ranges -- and then stored none of it
    # (only a telemetry event). The self-tagged ranges in particular are a
    # STUDY MEASURE that was being thrown away on every call, and
    # `pull_requests`/`repos` both sat at zero documents because only the
    # unused GitHub App webhook path ever wrote them.
    #
    # Requires an authenticated caller and a pr_id, for the same reason the
    # similarity skip above does: without both there is no (pr_id, user_id)
    # to key a record on. Never fatal -- a failed projection write must not
    # cost the participant their evaluation.
    if user_id and pr_id:
        try:
            record_evaluation(
                db,
                pr_id=pr_id,
                user_id=user_id,
                # A pre-PR "new_pr" evaluation is the Developer gate; an
                # existing PR conversation is the Lead's. Same split the
                # extension makes in content/pr_page.js's modeFor().
                persona="lead" if pr_number else "developer",
                repo=repo,
                pr_number=pr_number,
                head_sha=body.get("head_sha"),
                trigger_decision=trigger_decision.model_dump(),
                ai_tagged_ranges=[r.model_dump() for r in ai_tagged_ranges],
                # Phase 36: how the participant gave that answer. Accepted
                # from the client because only the client knows -- it is the
                # panel that offered the buttons. Unvalidated beyond being a
                # string: a stale extension build sends nothing and gets
                # "unknown", which is the honest value.
                tagging_method=str(body.get("tagging_method") or "") or None,
                org_consent_present=org_consent_present,
                functions=[f.model_dump() for f in functions],
                diff_stats={
                    "files_changed": len(change_set.files),
                    "functions_extracted": len(functions),
                    "skipped_functions": len(skipped_functions),
                    "similarity_skipped": similarity_skipped,
                    "nothing_changed": nothing_changed,
                    "fingerprint": fingerprint,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("gate/evaluate: could not record evaluation for pr_id=%s", pr_id)

    logger.info(
        "gate/evaluate repo=%s pr=%s -> functions_extracted=%d skipped_functions=%d "
        "similarity_skipped=%s nothing_changed=%s",
        repo,
        pr_number,
        len(functions),
        len(skipped_functions),
        similarity_skipped,
        nothing_changed,
    )

    return (
        jsonify(
            {
                "trigger_decision": trigger_decision.model_dump(),
                "functions": [f.model_dump() for f in functions],
                "fingerprint": fingerprint,
                "skipped_functions": skipped_functions,
                "similarity_skipped": similarity_skipped,
                # Phase 15: both exposed so a caller (and a human reading
                # the response) can tell the three cases apart -- nothing
                # changed at all, changed-but-cosmetic, and meaningfully
                # changed -- which `similarity_skipped: false` alone
                # conflates into one.
                "nothing_changed": nothing_changed,
                "similarity_threshold": threshold,
            }
        ),
        200,
    )
