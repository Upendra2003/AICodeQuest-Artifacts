"""Nightly leaderboard reconciliation (Phase 11).

Recomputes every user's leaderboard aggregate from raw dev_gate/
lead_gate sessions + attempts and compares against what's currently
stored, logging (event_type="leaderboard_reconciliation_drift") and
fixing any discrepancy beyond leaderboard.reconciliation_drift_tolerance.
An aggregate silently going stale mid-study (a missed/failed RQ job, a
manual Mongo edit, a bug in the incremental path) would corrupt a
reported metric -- this is the check that catches that, not a formality.

Meant to run via cron, e.g. (adjust paths for your deployment):

    0 3 * * * cd /path/to/AICodeQuest/backend && \\
      /path/to/AICodeQuest/.venv/bin/python scripts/reconcile_leaderboard.py \\
      >> /var/log/aicodequest/reconcile_leaderboard.log 2>&1

Run manually via `make reconcile-leaderboard` or directly:

    cd backend && ../.venv/bin/python scripts/reconcile_leaderboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.client import get_db  # noqa: E402
from app.services.leaderboard import reconcile_all  # noqa: E402


def main() -> None:
    db = get_db()
    result = reconcile_all(db, events_collection=db["events"])
    print(f"users checked: {result['users_checked']}")
    print(f"users with drift: {result['users_drifted']}")
    for entry in result["drift"]:
        print(f"  user {entry['user_id']}:")
        for detail in entry["details"]:
            print(f"    - {detail}")
    if result["users_drifted"]:
        sys.exit(1)  # non-zero exit so a cron wrapper can alert on drift


if __name__ == "__main__":
    main()
