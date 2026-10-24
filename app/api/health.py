"""GET /healthz (Phase 13). No auth -- a health endpoint an external
monitor/load-balancer can poll must not require a login. Deliberately
returns dependency-level detail (not just "ok"/"down") since a study
operator watching this during a live session needs to know WHICH piece
broke, not just that something did.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from app.services.health import check_dependencies

health_bp = Blueprint("health", __name__)


@health_bp.get("/healthz")
def healthz():
    db = current_app.config["DB"]
    result = check_dependencies(db)
    # 200 for ok/degraded (the process itself is up and can still serve
    # SOME traffic in shadow/advisory-adjacent ways), 503 only when a
    # CRITICAL dependency (Mongo/Redis) is down -- see check_dependencies()
    # docstring for the ok/degraded/down distinction.
    status_code = 503 if result["status"] == "down" else 200
    return jsonify(result), status_code
