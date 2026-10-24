"""GitHub App webhook receiver.

Every delivery must carry a valid `X-Hub-Signature-256` (HMAC-SHA256 of
the RAW request body, keyed with GITHUB_WEBHOOK_SECRET) -- missing or
mismatched signatures are rejected with 401 before the payload is even
parsed as JSON, so a forged webhook can never reach evaluation logic.

Processing happens SYNCHRONOUSLY in the request handler (fetch the diff,
call the Checks API) rather than via Phase 6's RQ background queue --
"boring over clever" for a phase whose brief didn't ask for background
webhook processing. Flagged in reports/phase-10-review.md open questions:
GitHub expects a webhook delivery acknowledged within a few seconds and
will retry on timeout, so a slow GitHub API response here risks duplicate
deliveries under real load -- fine for this phase's scope, a real
production deployment would likely want this enqueued instead.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

from flask import Blueprint, current_app, jsonify, request

from app.services import github_app
from app.services.consent import OrgConsentMissingError

logger = logging.getLogger(__name__)

github_webhook_bp = Blueprint("github_webhook", __name__, url_prefix="/github")

# Actions that should trigger a fresh evaluation -- "reopened" is included
# since a closed-then-reopened PR's check runs should reflect current
# state again, same as a fresh open.
PR_EVAL_ACTIONS = {"opened", "synchronize", "reopened"}


def _verify_signature(secret: str, payload: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@github_webhook_bp.post("/webhook")
def webhook():
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        logger.error("GITHUB_WEBHOOK_SECRET is not set; rejecting all webhook deliveries")
        return jsonify({"error": "webhook receiver not configured"}), 500

    raw_body = request.get_data()
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_signature(secret, raw_body, signature):
        return jsonify({"error": "invalid or missing signature"}), 401

    event_type = request.headers.get("X-GitHub-Event")
    body = request.get_json(silent=True) or {}
    db = current_app.config["DB"]

    if event_type == "pull_request":
        return _handle_pull_request(db, body)
    if event_type == "push":
        # pull_request.synchronize already covers "new commits on an open
        # PR's branch" -- see this module's docstring for why push itself
        # is acknowledged but not separately processed.
        return jsonify({"ok": True, "handled": False, "reason": "push acknowledged, no-op"}), 200
    if event_type == "check_run":
        return _handle_check_run(db, body)
    if event_type == "installation":
        return _handle_installation(db, body)

    return jsonify({"ok": True, "handled": False, "reason": f"unrecognized event type {event_type!r}"}), 200


def _handle_pull_request(db, body: dict):
    action = body.get("action")
    if action not in PR_EVAL_ACTIONS:
        return jsonify({"ok": True, "handled": False, "reason": f"action {action!r} ignored"}), 200

    try:
        pr = body["pull_request"]
        repo = body["repository"]
        installation_id = body["installation"]["id"]
    except KeyError as exc:
        return jsonify({"error": f"malformed pull_request payload, missing {exc}"}), 400

    try:
        result = github_app.evaluate_pull_request(
            db,
            owner=repo["owner"]["login"],
            repo=repo["name"],
            pr_number=pr["number"],
            head_sha=pr["head"]["sha"],
            base_sha=pr["base"]["sha"],
            installation_id=installation_id,
            events_collection=db["events"],
            base_ref=pr.get("base", {}).get("ref"),
            head_ref=pr.get("head", {}).get("ref"),
        )
    except OrgConsentMissingError as exc:
        # Acknowledged (200), not an error response -- this is GitHub's
        # webhook delivery, which will retry on anything other than 2xx;
        # a missing org consent record isn't transient and retrying won't
        # help. `handled: False` + `error` distinguishes this from a
        # normal ignored-action response for anyone reading delivery logs.
        return jsonify({"ok": True, "handled": False, "error": "org_consent_missing", "reason": str(exc)}), 200
    return jsonify({"ok": True, "handled": True, "result": result}), 200


def _handle_check_run(db, body: dict):
    action = body.get("action")
    if action != "rerequested":
        return jsonify({"ok": True, "handled": False, "reason": f"action {action!r} ignored"}), 200

    try:
        github_check_run_id = body["check_run"]["id"]
    except KeyError as exc:
        return jsonify({"error": f"malformed check_run payload, missing {exc}"}), 400

    result = github_app.handle_check_run_rerequested(db, github_check_run_id, events_collection=db["events"])
    if result is None:
        return jsonify({"ok": True, "handled": False, "reason": "check run not found in our records"}), 200
    return jsonify({"ok": True, "handled": True, "result": result}), 200


def _handle_installation(db, body: dict):
    action = body.get("action")
    try:
        installation_id = body["installation"]["id"]
        owner = body["installation"]["account"]["login"]
        repo_names = [r["name"] for r in body.get("repositories", [])]
    except KeyError as exc:
        return jsonify({"error": f"malformed installation payload, missing {exc}"}), 400

    github_app.handle_installation_event(db, action, installation_id, owner, repo_names)
    return jsonify({"ok": True, "handled": True}), 200
