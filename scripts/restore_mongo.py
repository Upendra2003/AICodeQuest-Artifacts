"""mongorestore-based restore (Phase 13) -- the other half of
backup_mongo.py. Runs mongorestore INSIDE the `mongo` container, reading
an archive from the SAME /backups bind mount backup_mongo.py writes to.

--drop (mongorestore's own flag, passed through here unconditionally):
this REPLACES the target database's existing collections with the
archive's contents. That is the whole point of a restore, but it is
destructive -- this script requires an explicit --yes flag to actually
run, printing what it's about to do first, so a mistyped cron invocation
or a copy-pasted command can't silently wipe live study data.

Run via:
    cd backend && ../.venv/bin/python scripts/restore_mongo.py <archive_name_or_path> --yes
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUPS_DIR = REPO_ROOT / "backups"
DB_NAME = "aicodequest"
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"


def _compose_exec(mongo_service: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", mongo_service, *args],
        capture_output=True,
        text=True,
    )


def restore(archive: str, mongo_service: str = "mongo", db_name: str = DB_NAME) -> None:
    archive_path = Path(archive)
    archive_name = archive_path.name  # accept either a bare filename or a full host path
    host_path = BACKUPS_DIR / archive_name
    if not host_path.exists():
        raise FileNotFoundError(f"{host_path} does not exist -- run backup_mongo.py first, or check the archive name")

    container_path = f"/backups/{archive_name}"
    result = _compose_exec(
        mongo_service, "mongorestore", f"--db={db_name}", f"--archive={container_path}", "--gzip", "--drop"
    )
    if result.returncode != 0:
        raise RuntimeError(f"mongorestore failed (exit {result.returncode}): {result.stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Archive filename (as printed by backup_mongo.py) or full path")
    parser.add_argument(
        "--yes", action="store_true", help="Required: confirms you understand this DROPS existing collections first"
    )
    args = parser.parse_args()

    print(f"About to restore '{args.archive}' into database '{DB_NAME}', DROPPING its existing collections first.")
    if not args.yes:
        print("Refusing to proceed without --yes. Re-run with --yes to confirm.", file=sys.stderr)
        sys.exit(1)

    try:
        restore(args.archive)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"restored '{DB_NAME}' from {args.archive}")


if __name__ == "__main__":
    main()
