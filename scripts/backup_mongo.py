"""mongodump-based backup (Phase 13). Runs mongodump INSIDE the `mongo`
container (via `docker compose exec`) rather than requiring the
mongodb-database-tools package on the host -- the official `mongo:7.0`
image already bundles mongodump/mongorestore. The container's
/backups mount (infra/docker-compose.yml's `mongo` service) is a bind
mount to <repo_root>/backups, so the resulting archive lands directly on
the host with no extra `docker cp` step.

Meant to run on a schedule during the study, e.g.:

    0 2 * * * cd /path/to/AICodeQuest && \\
      .venv/bin/python backend/scripts/backup_mongo.py \\
      >> /var/log/aicodequest/backup.log 2>&1

Run manually via `make backup` or directly:

    cd backend && ../.venv/bin/python scripts/backup_mongo.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUPS_DIR = REPO_ROOT / "backups"
DB_NAME = "aicodequest"  # app/db/client.py's DEFAULT_DB_NAME
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"


def _compose_exec(mongo_service: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", mongo_service, *args],
        capture_output=True,
        text=True,
    )


def backup(mongo_service: str = "mongo", db_name: str = DB_NAME) -> Path:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"{db_name}-{timestamp}.archive.gz"
    container_path = f"/backups/{archive_name}"

    result = _compose_exec(
        mongo_service, "mongodump", f"--db={db_name}", f"--archive={container_path}", "--gzip"
    )
    if result.returncode != 0:
        raise RuntimeError(f"mongodump failed (exit {result.returncode}): {result.stderr}")

    host_path = BACKUPS_DIR / archive_name
    if not host_path.exists():
        raise RuntimeError(
            f"mongodump reported success but {host_path} doesn't exist on the host -- "
            "is infra/docker-compose.yml's mongo service's /backups bind mount configured?"
        )
    return host_path


def main() -> None:
    try:
        path = backup()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"backed up '{DB_NAME}' -> {path}")


if __name__ == "__main__":
    main()
