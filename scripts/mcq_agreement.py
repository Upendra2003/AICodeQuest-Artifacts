"""Three-rater comprehension analysis, end to end (Phase 35).

Produces, in the output directory (default: backend/study_export/):

  mcq_ratings.csv      one row per comprehension item, with every rater's
                       choice, whether each matched the EXECUTED key, the
                       leads' quality ratings and their two judgements
  mcq_agreement.json   the computed statistics, exactly as
                       GET /research/mcq-agreement returns them
  mcq_codebook.md      what every column means, including the instructions
                       an analyst must follow rather than infer

and prints a readable summary to stdout, because the number a study lead
actually wants ("did the leads agree with the developers?") should not
require opening a file.

WHAT IS AND IS NOT AUTOMATED. Every statistic here is computed from stored
data with no manual step: accuracy against the executed key for all three
raters, three-way Fleiss' kappa, all three pairwise Cohen's kappas, and the
item-quality ratings. What is NOT automated, and cannot be, is the decision
about what they mean -- a kappa of 0.4 is a fact; "moderate agreement,
therefore the instrument is sound" is an argument.

PSEUDONYMISATION. Off by default, because the primary use of this script is
the study lead reading their own pilot data, where real ids are what make a
row traceable. Pass --pseudonymise for anything that leaves the study
machine; it then requires STUDY_PSEUDONYM_SALT with no insecure default,
the same rule scripts/export_study_data.py enforces.

Run via:
    cd backend && ../.venv/bin/python scripts/mcq_agreement.py [output_dir]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.core.config import CONFIG_VERSION, get_config  # noqa: E402
from app.db.client import get_db  # noqa: E402
from app.services.agreement import (  # noqa: E402
    RATERS,
    build_rating_table,
    compute_agreement,
    interpret_kappa,
)

COLUMNS = [
    "item_id",
    "pr_id",
    "developer_id",
    "language",
    "function_ref",
    "n_options",
    "answer_index",
    "developer_choice",
    "lead_choice",
    "llm_choice",
    "developer_correct",
    "lead_correct",
    "llm_correct",
    "llm_self_agreement",
    "n_raters_answered",
    "all_three_agree",
    "developer_answer_ms",
    "lead_answer_ms",
    "n_reviews",
    "mean_quality_rating",
    "quality_ratings",
    "question_is_well_formed_yes",
    "question_is_well_formed_no",
    "key_is_correct_yes",
    "key_is_correct_no",
]

CODEBOOK = {
    "item_id": "Mongo id of the comprehension item. Join key to mcq_reviews.",
    "pr_id": "Opaque pull-request identifier the item was generated for.",
    "developer_id": "Who answered it as the developer. Pseudonymised with --pseudonymise.",
    "language": "Programming language of the function the question is about.",
    "function_ref": "Qualified name of that function.",
    "n_options": "Options shown. Chance accuracy is 1/n_options.",
    "answer_index": (
        "THE GOLD STANDARD. Index of the correct option, obtained by EXECUTING "
        "the original function on the question's input -- never authored by a "
        "model. This is a fact about the program, so 'correct' here needs no "
        "expert consensus and is not a majority vote."
    ),
    "developer_choice": "Index the developer chose. EMPTY means unanswered, NOT wrong.",
    "lead_choice": (
        "Index the reviewing lead chose, answering COLD -- the key was withheld "
        "until they committed. EMPTY means no lead has reviewed this item yet. "
        "With lead_review.reviewers_per_item > 1 this is the FIRST lead's "
        "answer; every lead's answer is in mcq_reviews."
    ),
    "llm_choice": (
        "Index the generating model chose, answering its own item cold across "
        "mcq.llm_self_answer.passes independent passes (modal choice). EMPTY "
        "means not asked or unparseable, NOT wrong."
    ),
    "developer_correct/lead_correct/llm_correct": (
        "Whether that rater matched answer_index. EMPTY where that rater did "
        "not answer. ANALYSE EMPTIES AS MISSING, never as 0 -- an unanswered "
        "item is not a failed one, and treating it as one biases every rate "
        "downward by exactly the response-rate gap."
    ),
    "llm_self_agreement": (
        "Fraction of the model's independent passes that chose llm_choice. "
        "1.0 = every pass agreed. A low value marks an unstable model answer "
        "and is worth excluding in a sensitivity analysis."
    ),
    "n_raters_answered": "How many of the three raters answered (0-3).",
    "all_three_agree": (
        "TRUE when all three chose the same option, whether or not it was "
        "right. Agreement and accuracy are different questions: three raters "
        "can agree perfectly on a wrong answer, which marks a MISLEADING item "
        "rather than a hard one."
    ),
    "developer_answer_ms/lead_answer_ms": (
        "Milliseconds to the FIRST committed answer. The developer's may be "
        "revised afterwards (revision_count on the item); this figure keeps "
        "the first commitment. Response time is a comprehension proxy in its "
        "own right (Arvan et al., ASE 2026) and is more reliable than "
        "accuracy on small item counts."
    ),
    # The next four columns are HISTORICAL. Collection stopped on
    # 2026-08-31, when the lead's rating step was removed: a review is now
    # the lead's cold ANSWER and nothing else. An EMPTY value therefore
    # means NOT COLLECTED -- never a low rating and never a lead who
    # declined. Any rate computed over them must be reported with the date
    # cutoff, or it silently mixes items nobody was asked about with items
    # somebody judged.
    "n_reviews": (
        "HISTORICAL (collection stopped 2026-08-31). Leads who rated this "
        "item's QUALITY. 0 does NOT mean the item went unreviewed -- see "
        "`lead` for whether a lead answered it, which is what a review is "
        "now."
    ),
    "mean_quality_rating": (
        "HISTORICAL (collection stopped 2026-08-31). Mean of the leads' "
        "Likert quality ratings, 1-5. See mcq_codebook's scale anchors for "
        "the exact words each point carried."
    ),
    "quality_ratings": (
        "HISTORICAL (collection stopped 2026-08-31). Every individual "
        "rating, semicolon-separated."
    ),
    "question_is_well_formed_yes/no": (
        "HISTORICAL (collection stopped 2026-08-31). Leads' judgement of "
        "whether the question was clear and unambiguous. This has NO ground "
        "truth -- it is a human judgement, and the right statistic for it "
        "is agreement, not accuracy."
    ),
    "key_is_correct_yes/no": (
        "HISTORICAL (collection stopped 2026-08-31). Leads' judgement of "
        "whether the option marked correct really was. This one DOES have a "
        "ground truth (the key was executed), so a 'no' here is a lead "
        "disagreeing with the machine -- worth reading individually rather "
        "than aggregating, since each is either a misunderstanding by the "
        "lead or a real bug in the pipeline. With the rating step gone, "
        "nothing collects this signal any more: `nfr.py`'s automated QC "
        "gate is the only remaining check on item quality."
    ),
}


def _pseudonym(user_id: str, salt: str) -> str:
    return "P" + hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).hexdigest()[:10]


def _fmt(value: Optional[float], places: int = 3) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def _rows(table, pseudonymise: bool, salt: str) -> list[dict[str, Any]]:
    rows = []
    for item in table:
        choices = [c for c in (item.developer, item.lead, item.llm) if c is not None]
        judgement_counts = {"question_is_well_formed": [0, 0], "key_is_correct": [0, 0]}
        for judged in item.judgements:
            for key, value in judged.items():
                if key in judgement_counts:
                    judgement_counts[key][0 if value else 1] += 1

        def mark(rater: str) -> str:
            verdict = item.correct(rater)
            return "" if verdict is None else ("1" if verdict else "0")

        rows.append(
            {
                "item_id": item.item_id,
                "pr_id": item.pr_id,
                "developer_id": (
                    _pseudonym(item.developer_id, salt) if pseudonymise else item.developer_id
                ),
                "language": item.language,
                "function_ref": item.function_ref,
                "n_options": item.options,
                "answer_index": item.key,
                "developer_choice": "" if item.developer is None else item.developer,
                "lead_choice": "" if item.lead is None else item.lead,
                "llm_choice": "" if item.llm is None else item.llm,
                "developer_correct": mark("developer"),
                "lead_correct": mark("lead"),
                "llm_correct": mark("llm"),
                "llm_self_agreement": "" if item.llm_agreement is None else item.llm_agreement,
                "n_raters_answered": len(choices),
                # Only meaningful when all three answered; empty otherwise,
                # because "they agreed" over two raters is a different claim.
                "all_three_agree": (
                    "" if len(choices) < 3 else ("1" if len(set(choices)) == 1 else "0")
                ),
                "developer_answer_ms": "" if item.developer_ms is None else round(item.developer_ms),
                "lead_answer_ms": "" if item.lead_ms is None else round(item.lead_ms),
                "n_reviews": len(item.quality_ratings),
                "mean_quality_rating": (
                    round(sum(item.quality_ratings) / len(item.quality_ratings), 3)
                    if item.quality_ratings
                    else ""
                ),
                "quality_ratings": ";".join(str(r) for r in item.quality_ratings),
                "question_is_well_formed_yes": judgement_counts["question_is_well_formed"][0],
                "question_is_well_formed_no": judgement_counts["question_is_well_formed"][1],
                "key_is_correct_yes": judgement_counts["key_is_correct"][0],
                "key_is_correct_no": judgement_counts["key_is_correct"][1],
            }
        )
    return rows


def _codebook(report: dict[str, Any]) -> str:
    cfg = get_config()["lead_review"]
    anchors = "\n".join(
        f"  - **{point}** — {text}" for point, text in sorted(cfg["quality_scale"]["anchors"].items())
    )
    lines = [
        "# Codebook — mcq_ratings.csv",
        "",
        f"Generated from CONFIG_VERSION `{CONFIG_VERSION}`.",
        "One row per comprehension item. Three raters answer each item: the",
        "DEVELOPER who wrote the pull request, a reviewing LEAD, and the MODEL",
        "that generated the item. All three are scored against the same key.",
        "",
        "## Read this before analysing",
        "",
        "1. **The key is a fact, not a consensus.** `answer_index` was obtained",
        "   by executing the original function. A rater who disagrees with it is",
        "   wrong about the program, not merely in a minority.",
        "2. **Empty is MISSING, never zero.** An unanswered item is not a failed",
        "   one. Every `*_correct` column is blank where that rater did not",
        "   answer, and treating blanks as 0 biases every rate downward by",
        "   exactly the response-rate gap.",
        "3. **Accuracy and agreement answer different questions.** Accuracy is",
        "   per rater against the key. Agreement (Fleiss'/Cohen's kappa, in",
        "   mcq_agreement.json) is whether raters chose the SAME option,",
        "   whatever it was. An item all three fail identically is a misleading",
        "   item; one they fail differently is a hard one. You need both.",
        "4. **`key_is_correct` disagreements are individually informative.** A",
        "   lead saying the executed key is wrong is either a misunderstanding",
        "   by the lead or a real defect in the generation pipeline. Read them",
        "   one by one; do not aggregate them into a quality score.",
        "5. **Missingness is biased by construction.** An item exists only where",
        "   an executable test suite could be built, so the measure is absent on",
        "   exactly the code that is hardest to comprehend (framework-shaped",
        "   code, class members without a legal constructed state). Report this",
        "   as a limitation rather than assuming the items are representative.",
        "",
        "## Quality-rating scale, as the leads saw it",
        "",
        anchors,
        "",
        "## Columns",
        "",
    ]
    for name, description in CODEBOOK.items():
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append(description)
        lines.append("")
    return "\n".join(lines)


def _print_summary(report: dict[str, Any]) -> None:
    acc = report["accuracy"]
    agr = report["agreement"]
    quality = report["item_quality"]
    chance = report["chance_accuracy"]

    print()
    print("=" * 68)
    print("  COMPREHENSION ITEMS — THREE-RATER ANALYSIS")
    print("=" * 68)
    print(f"  items: {report['n_items']}   options each: {report['n_options']}   "
          f"chance: {_fmt(chance, 2)}")
    print()
    print("  ACCURACY against the EXECUTED key")
    print("  " + "-" * 64)
    print(f"  {'rater':<12} {'n':>5} {'correct':>8} {'accuracy':>10}  95% CI")
    for rater in RATERS:
        block = acc[rater]
        ci = block["ci95"]
        ci_text = "—" if ci is None else f"[{ci[0]:.3f}, {ci[1]:.3f}]"
        print(
            f"  {rater:<12} {block['n_answered']:>5} {block['n_correct']:>8} "
            f"{_fmt(block['accuracy']):>10}  {ci_text}"
        )
    print()
    print("  AGREEMENT (chance-corrected; a different question from accuracy)")
    print("  " + "-" * 64)
    print(
        f"  three-way Fleiss' kappa: {_fmt(agr['three_way_fleiss_kappa'])}"
        f"  ({agr['three_way_interpretation']})"
    )
    print(
        f"    computed on {agr['n_items_with_2plus_raters']} item(s) with 2+ raters; "
        f"{agr['n_items_with_all_3_raters']} had all three"
    )
    for pair, block in agr["pairwise"].items():
        print(
            f"  {pair:<24} kappa {_fmt(block['kappa']):>7}  "
            f"raw {_fmt(block['raw_agreement']):>7}  n={block['n_items']:<4} "
            f"({block['interpretation']})"
        )
    print()
    print("  ITEM QUALITY, as judged by the reviewing leads")
    print("  " + "-" * 64)
    print(
        f"  rated: {quality['n_reviewed']} item(s) / {quality['n_reviews']} review(s); "
        f"{quality['n_skipped']} skipped"
    )
    print(f"  mean quality rating: {_fmt(quality['mean_rating'], 2)} / 5")
    if quality["rating_distribution"]:
        print(f"  distribution: {quality['rating_distribution']}")
    for key, block in quality["judgements"].items():
        print(f"  {key:<26} yes {block['yes']:<4} no {block['no']:<4} "
              f"({_fmt(block['yes_rate'], 2)})")
    print()
    cov = report["coverage"]
    print("  COVERAGE  (a rate cannot be read without its denominator gap)")
    print("  " + "-" * 64)
    print(f"  developer answered: {cov['developer_answered']}")
    print(f"  lead answered:      {cov['lead_answered']}   "
          f"awaiting review: {cov['items_awaiting_lead_review']}")
    print(f"  model answered:     {cov['llm_answered']}")
    print("=" * 68)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", nargs="?", default="study_export")
    parser.add_argument("--pr-id", help="restrict to one pull request")
    parser.add_argument("--include-practice", action="store_true")
    parser.add_argument(
        "--pseudonymise",
        action="store_true",
        help="replace developer_id with a salted hash; requires STUDY_PSEUDONYM_SALT",
    )
    args = parser.parse_args()

    salt = ""
    if args.pseudonymise:
        salt = os.environ.get("STUDY_PSEUDONYM_SALT", "")
        if not salt:
            print(
                "STUDY_PSEUDONYM_SALT is not set. Refusing to write a "
                "'pseudonymised' file with a guessable salt.",
                file=sys.stderr,
            )
            return 2

    db = get_db()
    table = build_rating_table(
        db, pr_id=args.pr_id, include_practice=args.include_practice
    )
    report = compute_agreement(
        db, pr_id=args.pr_id, include_practice=args.include_practice
    )

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = _rows(table, args.pseudonymise, salt)
    with (out / "mcq_ratings.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    (out / "mcq_agreement.json").write_text(
        json.dumps({"config_version": CONFIG_VERSION, **report}, indent=2, default=str),
        encoding="utf-8",
    )
    (out / "mcq_codebook.md").write_text(_codebook(report), encoding="utf-8")

    _print_summary(report)
    print(f"  wrote {len(rows)} row(s) to {out / 'mcq_ratings.csv'}")
    print(f"        {out / 'mcq_agreement.json'}")
    print(f"        {out / 'mcq_codebook.md'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
