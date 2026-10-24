"""Consent API (Phase 12): participant-facing consent signing/withdrawal,
plus a researcher-facing org consent registration endpoint. Thin HTTP
layer over app.services.consent -- see that module for the actual gating
logic (app/api/gate.py, app/api/dev_game.py, app/api/lead_gate.py,
app/services/github_app.py all call it directly, not through this
blueprint).
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services import consent

consent_bp = Blueprint("consent", __name__, url_prefix="/consent")


@consent_bp.post("/participant")
@jwt_required()
def sign_participant():
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    for field in ("version", "retain_code_snippets", "retain_transcripts", "allow_quotes_in_publication"):
        if field not in body:
            return jsonify({"error": f"{field} is required"}), 400

    db = current_app.config["DB"]
    try:
        doc = consent.sign_participant_consent(
            db,
            user_id,
            version=body["version"],
            retain_code_snippets=bool(body["retain_code_snippets"]),
            retain_transcripts=bool(body["retain_transcripts"]),
            allow_quotes_in_publication=bool(body["allow_quotes_in_publication"]),
            events_collection=db["events"],
        )
    except consent.ConsentVersionMismatchError as exc:
        return jsonify({"error": "consent_version_mismatch", "detail": str(exc)}), 400
    return (
        jsonify(
            {
                "version": doc.version,
                "signed_at": doc.signed_at.isoformat(),
                "retain_code_snippets": doc.retain_code_snippets,
                "retain_transcripts": doc.retain_transcripts,
                "allow_quotes_in_publication": doc.allow_quotes_in_publication,
            }
        ),
        201,
    )


@consent_bp.get("/participant/me")
@jwt_required()
def get_participant_consent():
    user_id = get_jwt_identity()
    db = current_app.config["DB"]
    doc = db["participant_consents"].find_one({"user_id": user_id})
    if doc is None:
        return jsonify({"signed": False}), 200
    return (
        jsonify(
            {
                "signed": True,
                "version": doc["version"],
                "signed_at": doc["signed_at"].isoformat(),
                "retain_code_snippets": doc["retain_code_snippets"],
                "retain_transcripts": doc["retain_transcripts"],
                "allow_quotes_in_publication": doc["allow_quotes_in_publication"],
                "withdrawn": doc.get("withdrawn", False),
            }
        ),
        200,
    )


@consent_bp.post("/withdraw")
@jwt_required()
def withdraw():
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    categories = body.get("categories")
    if not categories or not isinstance(categories, list):
        return jsonify({"error": "categories (non-empty list) is required"}), 400

    db = current_app.config["DB"]
    try:
        audit = consent.withdraw(
            db, user_id, categories, events_collection=db["events"],
            researcher_note=body.get("researcher_note"),
        )
    except consent.InvalidWithdrawalCategoryError as exc:
        return jsonify({"error": "invalid_withdrawal_category", "detail": str(exc)}), 400
    return jsonify({"withdrawn_at": audit.withdrawn_at.isoformat(), "categories": audit.categories}), 200


@consent_bp.post("/org")
@jwt_required()
def register_org():
    # NOTE: no real researcher/admin RBAC exists yet -- any authenticated
    # user can call this endpoint. Same caveat as dev_game.py's/
    # lead_gate.py's override() endpoints; see reports/phase-5-review.md
    # open questions.
    body = request.get_json(silent=True) or {}
    for field in ("org", "signed_agreement_ref", "permitted_data_categories", "retention_period_days", "enforcement_mode"):
        if field not in body:
            return jsonify({"error": f"{field} is required"}), 400

    db = current_app.config["DB"]
    doc = consent.register_org_consent(
        db,
        org=body["org"],
        repos=body.get("repos", []),
        signed_agreement_ref=body["signed_agreement_ref"],
        permitted_data_categories=body["permitted_data_categories"],
        retention_period_days=body["retention_period_days"],
        enforcement_mode=body["enforcement_mode"],
    )
    return jsonify({"id": doc.id, "org": doc.org, "repos": doc.repos}), 201
