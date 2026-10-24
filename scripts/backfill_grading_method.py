"""Recovers DevAttemptDocument.grading_method for attempts graded before
that field existed (Phase 33).

WHY THIS IS INFERENTIAL, AND WHY THAT IS RECORDED. `grading_method` is a
study measure: a turn graded by EXECUTING an executable suite and a turn
graded by COMPARING the fix against the known original are different
measurements, and pooling them makes "accuracy" mean different things for
different participants. Attempts written before the field exists carry
None, which `compute_raw_metrics` deliberately counts in NEITHER split
rate (it reports them as `graded_defense_turns_unknown_regime` instead),
so nothing is silently misattributed.

This script narrows that gap where the telemetry can prove the answer.
`submit_defense` logged `dev_gate_graded_by_execution` or
`dev_gate_graded_without_execution` on every graded turn, with the round,
the level and a timestamp. Those events ARE the record of which branch
ran — but they carry no attempt id, so an attempt is matched on
(pr_ref, actor_id, round, level) plus a timestamp within
--window-seconds of `submitted_at`.

THE MATCH MUST BE UNAMBIGUOUS. If two candidate events fit one attempt,
or none does, the attempt is LEFT UNKNOWN. A wrong regime is worse than an
absent one: an absent one is visible in
`graded_defense_turns_unknown_regime` and can be excluded, while a wrong
one silently corrupts the very comparison the field exists to support.

EVERY RECOVERED ROW IS STAMPED. `grading_detail` is written as
`backfilled_from_telemetry:<detail>`, never as a bare detail value, so an
analysis can always separate recorded data from reconstructed data — and
so re-running this script is idempotent (a stamped row is skipped).

Attack attempts are NOT touched. Their regime was decided by a `graded_by`
local that was never persisted and never logged with enough context to
reconstruct.

    python backend/scripts/backfill_grading_method.py [--dry-run] [--window-seconds N]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient  # noqa: E402

STAMP = "backfilled_from_telemetry:"

EVENT_TO_METHOD = {
    "dev_gate_graded_by_execution": "executable_test",
    "dev_gate_graded_without_execution": "comparison",
}


def _aware(value):
    """Mongo hands back naive UTC datetimes; comparing those to an aware
    one raises. Same normalisation dev_gate._as_aware_utc does."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_ts(raw):
    """Events store ts_utc as an ISO-8601 string ending in Z."""
    from datetime import datetime

    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument(
        "--window-seconds",
        type=float,
        default=10.0,
        help="how far a grading event may sit from the attempt's submitted_at (default 10)",
    )
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    parser.add_argument("--database", default=os.environ.get("MONGO_DB", "aicodequest"))
    args = parser.parse_args()

    db = MongoClient(args.mongo_uri)[args.database]

    events = [
        {
            "method": EVENT_TO_METHOD[e["event_type"]],
            "event_type": e["event_type"],
            "pr_ref": e.get("pr_ref"),
            "actor_id": e.get("actor_id"),
            "round": (e.get("payload") or {}).get("round"),
            "level": (e.get("payload") or {}).get("level"),
            "ts": _parse_ts(e.get("ts_utc")),
        }
        for e in db["events"].find({"event_type": {"$in": list(EVENT_TO_METHOD)}})
    ]
    events = [e for e in events if e["ts"] is not None]

    candidates = list(
        db["dev_attempts"].find(
            {"phase": "defense", "outcome": {"$ne": None}, "grading_method": None}
        )
    )

    window = timedelta(seconds=args.window_seconds)
    matched = ambiguous = unmatched = 0

    for attempt in candidates:
        submitted = _aware(attempt.get("submitted_at"))
        if submitted is None:
            unmatched += 1
            continue

        fits = [
            e
            for e in events
            if e["pr_ref"] == attempt.get("pr_id")
            and e["actor_id"] == attempt.get("user_id")
            and e["round"] == attempt.get("round")
            and e["level"] == attempt.get("level")
            and abs(e["ts"] - submitted) <= window
        ]

        if len(fits) != 1:
            if len(fits) > 1:
                ambiguous += 1
                print(
                    f"  AMBIGUOUS {attempt['_id']} ({attempt.get('level')} r{attempt.get('round')}): "
                    f"{len(fits)} events fit -- left unknown"
                )
            else:
                unmatched += 1
            continue

        event = fits[0]
        matched += 1
        print(
            f"  {attempt['_id']} {attempt.get('level')} r{attempt.get('round')} "
            f"-> {event['method']}"
        )
        if not args.dry_run:
            db["dev_attempts"].update_one(
                {"_id": attempt["_id"]},
                {
                    "$set": {
                        "grading_method": event["method"],
                        "grading_detail": f"{STAMP}{event['event_type']}",
                    }
                },
            )

    print(
        f"\n{len(candidates)} defense attempt(s) without a regime: "
        f"{matched} recovered, {ambiguous} ambiguous, {unmatched} with no matching event."
    )
    if args.dry_run:
        print("--dry-run: nothing was written.")
    else:
        print(
            "Recovered rows are stamped grading_detail="
            f"'{STAMP}...' so reconstructed data stays distinguishable from recorded data."
        )
    print(
        "Rows left unknown are correct as they are: compute_raw_metrics counts them in "
        "graded_defense_turns_unknown_regime and in NEITHER split rate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
