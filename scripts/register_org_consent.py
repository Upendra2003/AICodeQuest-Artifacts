"""Register organisation consent for a repository owner (Phase 36).

WHY THIS SCRIPT EXISTS. `/gate/evaluate` refuses with 403
`org_consent_missing` for any repository whose OWNER has no
OrgConsentDocument -- and `POST /consent/org` is reachable from no UI, so
until now the only way to grant it was to hand-write a Mongo document.
That turned a one-line setup step into a mystery, and it has now cost real
testing time twice: once when the database was wiped on 2026-08-19, and
again on 2026-08-27 when a second repository (`studyhost/CrossLink`) was
opened and the extension silently did nothing.

THE TWO CONSENTS ARE DIFFERENT THINGS, and confusing them is the actual
trap:

  PARTICIPANT consent -- signed by a person, in the extension popup.
                         Covers what the study may keep about THEM.
  ORG consent         -- asserted by a RESEARCHER, per repository owner.
                         Covers whether the study may run on that
                         organisation's code at all.

A participant who signs the first can never satisfy the second, and no
amount of re-signing will help them. Only this.

WHAT RUNNING THIS ASSERTS. That the named organisation has agreed to take
part, on the terms recorded in `signed_agreement_ref`. It is a governance
record, not a switch -- `--agreement-ref` is required with no default for
exactly that reason. Do not run it for an organisation that has not
actually agreed.

    cd backend && ../.venv/bin/python scripts/register_org_consent.py \\
        --org studyhost --agreement-ref study-lead-self-hosted-testing

`--repos` scopes it to named repositories; omitting it covers every
repository under that owner, which is what the existing record for
`example-owner` does.

A WILDCARD owner covers every organisation:

    ... --org '*' --agreement-ref pilot-any-repo

Use that when the study must run on repositories the researcher does not
own -- it says "any repository" as an explicit governance record carrying
its own agreement reference, which is a different and more honest thing
than turning the check down via `consent.org_consent_mode: advisory`.
Quote the asterisk, or the shell will expand it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.core.config import get_config  # noqa: E402
from app.db.client import get_db  # noqa: E402
from app.services.consent import (  # noqa: E402
    ORG_CONSENTS,
    OrgConsentMissingError,
    check_org_consent,
    register_org_consent,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org", help="repository OWNER, e.g. studyhost; '*' covers every owner")
    parser.add_argument(
        "--repos",
        nargs="*",
        default=[],
        help="specific repositories; omit to cover every repo under --org",
    )
    parser.add_argument(
        "--agreement-ref",
        help="what was agreed, and where it is recorded. No default: this is a governance record.",
    )
    parser.add_argument("--retention-days", type=int, default=365)
    parser.add_argument(
        "--enforcement-mode",
        default=None,
        choices=["shadow", "advisory", "enforced"],
        help="defaults to config's enforcement.mode",
    )
    parser.add_argument("--list", action="store_true", help="show what is already registered and exit")
    args = parser.parse_args()

    db = get_db()

    if args.list:
        # --list is read-only, so it must not demand the arguments a WRITE
        # needs. Validated here rather than by argparse for that reason.
        rows = list(db[ORG_CONSENTS].find())
        if not rows:
            print("No organisation consent is registered. Every repository will be refused.")
            return 0
        print(f"{len(rows)} organisation consent record(s):")
        for row in rows:
            scope = ", ".join(row.get("repos") or []) or "ALL repositories"
            print(f"  {row.get('org')}: {scope}   ({row.get('signed_agreement_ref')})")
        return 0

    if not args.org or not args.agreement_ref:
        parser.error("--org and --agreement-ref are required unless --list is given")

    # Refuse a duplicate rather than silently adding a second record.
    # register_org_consent() is explicitly not idempotent, and a pile of
    # near-identical records is how a governance table stops being
    # readable.
    probe_repo = (args.repos or ["*"])[0]
    try:
        existing = check_org_consent(db, args.org, probe_repo, events_collection=db["events"])
        print(
            f"Already covered: org={existing.org} repos={existing.repos or 'ALL'} "
            f"({existing.signed_agreement_ref}). Nothing to do."
        )
        return 0
    except OrgConsentMissingError:
        pass

    cfg = get_config()
    doc = register_org_consent(
        db,
        org=args.org,
        repos=args.repos,
        signed_agreement_ref=args.agreement_ref,
        permitted_data_categories=["code_snippets", "metrics", "transcripts"],
        retention_period_days=args.retention_days,
        enforcement_mode=args.enforcement_mode or cfg["enforcement"]["mode"],
    )
    scope = ", ".join(doc.repos) if doc.repos else "ALL repositories"
    print(f"Registered organisation consent for {doc.org} ({scope}).")
    print(f"  agreement ref:  {doc.signed_agreement_ref}")
    print(f"  retention:      {doc.retention_period_days} days")
    print(f"  enforcement:    {doc.enforcement_mode}")
    print()
    print(
        "NOTE: retention_period_days is RECORDED, not enforced -- no scheduled "
        "job deletes on it. See docs/DATA-FLOW.md."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
