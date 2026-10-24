"""Queue-depth / p95-latency alert check (Phase 13).

Meant to run on a schedule during the study, e.g.:

    */5 * * * * cd /path/to/AICodeQuest/backend && \\
      /path/to/AICodeQuest/.venv/bin/python scripts/check_alerts.py \\
      >> /var/log/aicodequest/check_alerts.log 2>&1

Posts to AICODEQUEST_ALERT_WEBHOOK_URL (an env var, never committed) when
current RQ queue depth or recent p95 request latency breaches
config.observability's thresholds. With no webhook configured, still
prints breaches to stdout and exits non-zero, so a bare cron job without
a webhook set up yet still surfaces problems via cron's own mail-on-error
behavior.

Run manually via `make check-alerts` or directly:

    cd backend && ../.venv/bin/python scripts/check_alerts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.client import get_db  # noqa: E402
from app.services.alerts import ALERT_WEBHOOK_ENV_VAR, check_alerts, send_webhook_alert  # noqa: E402


def main() -> None:
    db = get_db()
    result = check_alerts(db)

    print(f"queue depths: {result['queue_depths']}")
    print(f"p95 by budget key: {result['p95_by_budget_key']}")

    if not result["breaches"]:
        print("no breaches")
        return

    print("BREACHES:")
    for b in result["breaches"]:
        print(f"  - {b}")

    sent = send_webhook_alert(result["breaches"])
    if sent:
        print(f"webhook alert sent ({ALERT_WEBHOOK_ENV_VAR})")
    else:
        print(f"NOTE: {ALERT_WEBHOOK_ENV_VAR} not set (or empty) -- no webhook sent")

    sys.exit(1)  # non-zero so a cron wrapper / CI check can alert on this too


if __name__ == "__main__":
    main()
