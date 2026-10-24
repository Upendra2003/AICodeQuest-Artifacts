"""Anonymised study data export (Phase 12).

Produces, in the output directory (default: backend/study_export/):
  dataset.csv                    -- one row per attempt (see app/services/
                                     study_export.py's COLUMNS/CODEBOOK)
  codebook.md                    -- documents every column
  _private_pseudonym_mapping.csv -- real user_id <-> pseudonym_id. Keep
                                     this file access-restricted and
                                     SEPARATE from dataset.csv -- see
                                     docs/DATA-FLOW.md.

Requires STUDY_PSEUDONYM_SALT in the environment -- deliberately NO
insecure default (unlike JWT_SECRET_KEY's dev fallback in app/__init__.py):
a hardcoded/checked-in salt would defeat the entire point of
pseudonymising a real research dataset.

Run via:
    cd backend && ../.venv/bin/python scripts/export_study_data.py [output_dir]
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.client import get_db  # noqa: E402
from app.services.study_export import (  # noqa: E402
    COLUMNS,
    build_codebook_markdown,
    build_dataset,
    build_pseudonym_mapping,
)


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    salt = os.environ.get("STUDY_PSEUDONYM_SALT")
    if not salt:
        print(
            "ERROR: STUDY_PSEUDONYM_SALT is not set. Refusing to export with no "
            "salt -- a real research dataset must not use a hardcoded/checked-in "
            "pseudonymisation salt. Set it to a long random value kept only by "
            "the research team, e.g.:\n"
            "  export STUDY_PSEUDONYM_SALT=$(python3 -c 'import secrets; print(secrets.token_hex(32))')",
            file=sys.stderr,
        )
        sys.exit(1)

    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "study_export"
    out_dir.mkdir(parents=True, exist_ok=True)

    db = get_db()
    rows = build_dataset(db, salt)
    mapping = build_pseudonym_mapping(db, salt)

    _write_csv(out_dir / "dataset.csv", rows, COLUMNS)
    _write_csv(out_dir / "_private_pseudonym_mapping.csv", mapping, ["user_id", "pseudonym_id"])
    (out_dir / "codebook.md").write_text(build_codebook_markdown(), encoding="utf-8")

    print(f"wrote {len(rows)} row(s) -> {out_dir / 'dataset.csv'}")
    print(f"wrote codebook -> {out_dir / 'codebook.md'}")
    print(
        f"wrote PRIVATE pseudonym mapping ({len(mapping)} participant(s)) -> "
        f"{out_dir / '_private_pseudonym_mapping.csv'} -- keep this file "
        "access-restricted, separate from dataset.csv"
    )


if __name__ == "__main__":
    main()
