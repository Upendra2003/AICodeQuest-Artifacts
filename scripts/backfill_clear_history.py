"""Backfills DevGateSessionDocument.clear_history (Phase 27).

Sessions that reached CLEARED before that field existed have no history,
so `compute_raw_metrics` would count them only while they are STILL in
the CLEARED state -- and a session reopened or restarted since then reads
as never cleared, losing the participant's badges and streak.

Two sources, in order of reliability:
  1. The session's current state (CLEARED now -> it cleared, at updated_at).
  2. The `dev_gate_transition` telemetry events, which record every
     to_state="CLEARED" with a timestamp -- this is what recovers a clear
     from a session that has since been reopened.

Idempotent: a session that already has a clear_history is left alone.

    python backend/scripts/backfill_clear_history.py [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient  # noqa: E402


def _parse(ts) -> datetime | None:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    parser.add_argument("--db", default=os.environ.get("MONGO_DB", "aicodequest"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = MongoClient(args.mongo_uri)[args.db]

    # Every recorded transition INTO CLEARED, grouped by (pr_id, user_id).
    from_events: dict[tuple[str, str], list[datetime]] = {}
    for event in db["events"].find(
        {"event_type": "dev_gate_transition", "payload.to_state": "CLEARED"}
    ):
        pr_id, user_id = event.get("pr_ref"), event.get("actor_id")
        when = _parse(event.get("ts_utc"))
        if pr_id and user_id and when:
            from_events.setdefault((pr_id, user_id), []).append(when)

    updated = recovered = 0
    for session in db["dev_gate_sessions"].find({}):
        if session.get("clear_history"):
            continue
        key = (session["pr_id"], session["user_id"])
        history = sorted(from_events.get(key, []))
        if not history and session.get("state") == "CLEARED":
            when = _parse(session.get("updated_at"))
            history = [when] if when else []
        if not history:
            continue
        if session.get("state") != "CLEARED":
            recovered += 1
        updated += 1
        print(
            f"  {session['pr_id'][:52]:<52} state={session.get('state'):<18} "
            f"+{len(history)} clear(s)"
        )
        if not args.dry_run:
            db["dev_gate_sessions"].update_one(
                {"_id": session["_id"]}, {"$set": {"clear_history": history}}
            )

    print(f"\nsessions given a clear_history : {updated}")
    print(f"  of which had ALREADY lost the clear (state is no longer CLEARED): {recovered}")
    if args.dry_run:
        print("\n(dry run -- nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
