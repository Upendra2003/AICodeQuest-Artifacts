"""Auth API: register / login / logout / profile.

Never logs passwords or tokens: request bodies containing them aren't
passed to any logger, and telemetry payloads below only ever include
non-secret fields (role_hint, which fields changed).
"""

from __future__ import annotations

from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required
from pymongo.errors import DuplicateKeyError

from app.core.config import get_config
from app.db.models import RevokedTokenDocument, UserDocument

from app.telemetry.events import log_event

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

ROLE_HINTS = ("developer", "lead", "both")

_hasher: PasswordHasher | None = None


def _get_hasher() -> PasswordHasher:
    global _hasher
    if _hasher is None:
        cfg = get_config()["auth"]
        _hasher = PasswordHasher(
            time_cost=cfg["argon2_time_cost"],
            memory_cost=cfg["argon2_memory_cost_kib"],
            parallelism=cfg["argon2_parallelism"],
        )
    return _hasher


def _find_user_by_id(db, user_id: str) -> dict | None:
    try:
        oid = ObjectId(user_id)
    except (InvalidId, TypeError):
        return None
    return db.users.find_one({"_id": oid})


def _public_user(doc: dict) -> dict:
    """User representation safe to return from the API — never pw_hash."""
    return {
        "id": str(doc["_id"]),
        "email": doc["email"],
        "display_name": doc["display_name"],
        "role_hint": doc["role_hint"],
        "opt_in_flags": doc.get("opt_in_flags", {}),
        "created_at": doc["created_at"].isoformat(),
    }


@auth_bp.post("/register")
def register():
    body = request.get_json(silent=True) or {}
    email = body.get("email")
    password = body.get("password")
    display_name = body.get("display_name")
    role_hint = body.get("role_hint")

    errors = []
    if not email:
        errors.append("email is required")
    if not password:
        errors.append("password is required")
    if not display_name:
        errors.append("display_name is required")
    if role_hint not in ROLE_HINTS:
        errors.append(f"role_hint must be one of {ROLE_HINTS}")
    if errors:
        return jsonify({"errors": errors}), 400

    db = current_app.config["DB"]

    user = UserDocument(
        email=email,
        pw_hash=_get_hasher().hash(password),
        display_name=display_name,
        role_hint=role_hint,
        created_at=datetime.now(timezone.utc),
    )
    doc = user.model_dump(by_alias=True, exclude={"id"})

    try:
        result = db.users.insert_one(doc)
    except DuplicateKeyError:
        return jsonify({"error": "email already registered"}), 409

    user_id = str(result.inserted_id)

    log_event(
        event_type="register",
        actor_id=user_id,
        pr_ref=None,
        payload={"role_hint": role_hint},
        collection=db["events"],
    )

    doc["_id"] = result.inserted_id
    return jsonify(_public_user(doc)), 201


@auth_bp.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    email = body.get("email")
    password = body.get("password")
    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400

    db = current_app.config["DB"]
    doc = db.users.find_one({"email": email})
    if doc is None:
        return jsonify({"error": "invalid credentials"}), 401

    try:
        _get_hasher().verify(doc["pw_hash"], password)
    except VerifyMismatchError:
        return jsonify({"error": "invalid credentials"}), 401

    user_id = str(doc["_id"])
    token = create_access_token(identity=user_id)

    log_event(
        event_type="login",
        actor_id=user_id,
        pr_ref=None,
        payload={},
        collection=db["events"],
    )

    return jsonify({"access_token": token}), 200


@auth_bp.post("/logout")
@jwt_required()
def logout():
    """Revoke the caller's token (2026-10-18).

    Tokens never expire, so this is the ONLY way a session ends short of
    rotating JWT_SECRET_KEY (which signs everyone out). Revokes this one
    token, not every token for the account: signing out on a shared lab
    machine should not also sign the participant out of their own laptop.
    Idempotent -- the blocklist loader refuses a revoked token before this
    handler runs, so a second call answers 401 and inserts nothing.
    """
    db = current_app.config["DB"]
    user_id = get_jwt_identity()
    doc = RevokedTokenDocument(
        jti=get_jwt()["jti"],
        user_id=user_id,
        revoked_at=datetime.now(timezone.utc),
    ).model_dump(by_alias=True, exclude={"id"})
    try:
        db.revoked_tokens.insert_one(doc)
    except DuplicateKeyError:
        pass  # a concurrent double-click; the token is revoked either way

    log_event(
        event_type="logout",
        actor_id=user_id,
        pr_ref=None,
        payload={},
        collection=db["events"],
    )
    return jsonify({"signed_out": True}), 200


@auth_bp.get("/me")
@jwt_required()
def get_me():
    db = current_app.config["DB"]
    doc = _find_user_by_id(db, get_jwt_identity())
    if doc is None:
        return jsonify({"error": "user not found"}), 404
    return jsonify(_public_user(doc)), 200


@auth_bp.patch("/me")
@jwt_required()
def patch_me():
    db = current_app.config["DB"]
    user_id = get_jwt_identity()
    doc = _find_user_by_id(db, user_id)
    if doc is None:
        return jsonify({"error": "user not found"}), 404

    body = request.get_json(silent=True) or {}
    update: dict = {}

    if "display_name" in body:
        if not body["display_name"]:
            return jsonify({"error": "display_name cannot be empty"}), 400
        update["display_name"] = body["display_name"]

    if "opt_in_flags" in body:
        if not isinstance(body["opt_in_flags"], dict):
            return jsonify({"error": "opt_in_flags must be an object"}), 400
        update["opt_in_flags"] = {**doc.get("opt_in_flags", {}), **body["opt_in_flags"]}

    if not update:
        return jsonify({"error": "nothing to update"}), 400

    db.users.update_one({"_id": doc["_id"]}, {"$set": update})

    log_event(
        event_type="profile_update",
        actor_id=user_id,
        pr_ref=None,
        payload={"fields": sorted(update.keys())},
        collection=db["events"],
    )

    updated = _find_user_by_id(db, user_id)
    return jsonify(_public_user(updated)), 200
