"""Rebuilds `developer_records` / `lead_records` from the source
collections (Phase 24).

Two uses:
  * BACKFILL -- every session that existed before the projections did.
  * RECONCILE -- cron-able, the same discipline as
    scripts/reconcile_leaderboard.py. The projections are maintained
    incrementally on every transition and every graded turn, and this is
    what makes that safe: they can always be recomputed from the FSM's own
    collections, which remain the source of truth.

    python backend/scripts/rebuild_records.py [--mongo-uri URI] [--db NAME]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient  # noqa: E402

from app.services import records  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    parser.add_argument("--db", default=os.environ.get("MONGO_DB", "aicodequest"))
    args = parser.parse_args()

    db = MongoClient(args.mongo_uri)[args.db]
    before = {
        "developer": db[records.DEVELOPER_RECORDS].count_documents({}),
        "lead": db[records.LEAD_RECORDS].count_documents({}),
    }
    counts = records.rebuild_all(db)
    after = {
        "developer": db[records.DEVELOPER_RECORDS].count_documents({}),
        "lead": db[records.LEAD_RECORDS].count_documents({}),
    }

    print(f"database: {args.db}")
    print(f"  developer_records: {before['developer']} -> {after['developer']} ({counts['developer']} sessions rebuilt)")
    print(f"  lead_records:      {before['lead']} -> {after['lead']} ({counts['lead']} sessions rebuilt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
