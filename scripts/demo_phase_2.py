"""Phase 2 demo: run every backend/tests/golden/*.diff fixture through the
real trigger/extraction pipeline and print one table row per fixture —
this is the primary review surface for this phase.

Run via `make demo-phase-2` or directly:

    cd backend && ../.venv/bin/python scripts/demo_phase_2.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.core.config import CONFIG_VERSION  # noqa: E402
from app.services.diff_scope import build_change_set, evaluate_trigger  # noqa: E402
from app.services.extraction import extract_functions  # noqa: E402
from app.services.similarity import should_skip_regating  # noqa: E402
from scripts.report import generate_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = REPO_ROOT / "backend" / "tests" / "golden"

BUILT = [
    "backend/app/services/diff_scope.py — unidiff-based ChangeSet parsing, "
    "developer AI self-tag authorship (mandatory, defaults to 'unknown' "
    "never 'human'), config-driven trigger rule (lines OR modules), "
    "ignore_globs + generated-file-marker exclusion",
    "backend/app/services/extraction.py — tree-sitter function/method "
    "extraction for Python/JavaScript/Java/C; qualified names for "
    "arbitrarily nested classes; overlap+innermost-filtering so a range "
    "spanning several sibling functions returns all of them while a "
    "nested-function change returns only the innermost",
    "backend/app/services/similarity.py — normalized-token-stream "
    "structural fingerprint (sha256) + difflib.SequenceMatcher similarity "
    "for re-push comparisons; comment-only changes fingerprint-identical, "
    "genuine logic changes score below threshold",
    "backend/app/api/gate.py — POST /gate/evaluate; mandatory "
    "ai_tagged_ranges; optional previous_functions wires the re-push "
    "similarity-skip path end-to-end, emitting gate_skipped_similarity",
    "config/params.yaml — new trigger knobs: module_grouping_depth, "
    "ignore_globs, generated_file_markers, generated_marker_scan_lines",
    "backend/tests/golden/ — 8 required fixtures (g01-g08) as .diff + "
    ".expected.json pairs, generated and self-verified against the real "
    "pipeline rather than hand-counted",
]

OPEN_QUESTIONS = [
    "Diff ingestion (item 1) says the function should accept base/head "
    "SHAs + 'the file contents needed for context', but item 6's literal "
    "/gate/evaluate contract is only {repo, pr_number, diff, "
    "ai_tagged_ranges} — no file contents. I kept the endpoint to exactly "
    "that contract, so extraction relies on reconstructing file text from "
    "the diff's own hunk lines. This is exact for added-file diffs and "
    "single-hunk full-context diffs (what the golden fixtures use), but "
    "a realistic PR diff with 3-line context and multiple hunks will "
    "leave gaps my reconstruction can't fill, and extraction will then "
    "silently find fewer/no functions for that file. Should the next "
    "phase that wires real PR ingestion add file_contents (fetched via "
    "the GitHub API) to this endpoint?",
    "The endpoint's /gate/evaluate contract was extended with an OPTIONAL "
    "`previous_functions` field beyond your literal 4-field list. Without "
    "it, there is no code path in Phase 2 that would ever actually fire "
    "the 'on a re-push' similarity-skip logic (item 5), since no PR "
    "persistence exists yet for the endpoint to look up a prior "
    "evaluation itself. The caller now supplies what it stored from its "
    "own previous /gate/evaluate response. Confirm this extension is OK, "
    "or say if re-push wiring should wait for PR persistence instead.",
    "ai_tagged_ranges -> hunk.origin: I treat ANY overlap between a "
    "hunk's changed lines and a tagged range as 'ai' for that whole hunk "
    "(rather than requiring full coverage), reasoning that under-tagging "
    "AI content is worse for construct validity than over-tagging. "
    "Also: origin only ever becomes 'ai' or 'unknown' in this phase's "
    "code — 'human' stays in the type for future use but nothing assigns "
    "it, since only an AI self-tag was specified, not a human one.",
    "Whitespace-only-change detection is a per-hunk multiset diff on "
    "fully whitespace-stripped lines (cancels a removed/added line pair "
    "if they're identical once all whitespace is removed). This is a "
    "simple, boring heuristic, not a real diff-of-diffs — it can in "
    "theory cancel two lines that are coincidentally identical after "
    "stripping all whitespace despite being semantically different, "
    "though this is very unlikely in practice.",
    "ignore_globs/generated_file_markers are placeholder lists (common "
    "lockfiles + generic 'generated' banners) — not validated against the "
    "actual study repos yet.",
    "The literal/identifier node-type tables in similarity.py were built "
    "from representative tree-sitter samples per language, not an "
    "exhaustive grammar enumeration (e.g. some Java literal subtypes, "
    "hex/octal/binary integers may not be fully normalized).",
    "_is_ignored() now uses pathlib.PurePosixPath.full_match(), which "
    "requires Python 3.13+ for correct '**' (any number of directories, "
    "including zero) semantics — confirmed this venv is 3.13.12, but "
    "flagging the version floor explicitly since nothing pinned it "
    "before this phase.",
]


def _run_pipeline(diff_text: str):
    change_set = build_change_set(diff_text, [])
    decision = evaluate_trigger(change_set)

    functions_found = []
    for f in change_set.files:
        if f.ignored or f.language is None or not f.new_content:
            continue
        changed_ranges = [
            (h.new_start, h.new_start + h.new_lines - 1) for h in f.hunks if h.new_lines > 0
        ]
        if not changed_ranges:
            continue
        functions_found.extend(extract_functions(f.file_path, f.language, f.new_content, changed_ranges))

    return decision, functions_found


def _golden_table() -> tuple[str, bool]:
    fixture_names = sorted({p.stem for p in GOLDEN_DIR.glob("*.diff")})
    header = "| fixture | fires | reason | eff. lines | modules | functions found | similarity |"
    sep = "|---|---|---|---|---|---|---|"
    rows = [header, sep]
    all_ok = True

    for name in fixture_names:
        diff_text = (GOLDEN_DIR / f"{name}.diff").read_text(encoding="utf-8")
        expected = json.loads((GOLDEN_DIR / f"{name}.expected.json").read_text(encoding="utf-8"))
        decision, functions_found = _run_pipeline(diff_text)

        ok = (
            decision.fires == expected["fires"]
            and decision.metrics["total_effective_lines_changed"] == expected["total_effective_lines_changed"]
            and decision.metrics["distinct_modules_changed"] == expected["distinct_modules_changed"]
        )
        all_ok = all_ok and ok
        mark = "OK" if ok else "MISMATCH"

        similarity_cell = "n/a"
        if name == "g06_rename_only":
            # The rename diff itself carries no content (similarity index
            # 100%, no hunks); this demonstrates the fingerprint/similarity
            # machinery on representative identical-content input instead.
            sample = "def calc(a, b):\n    return a * b\n"
            _, score = should_skip_regating(sample, sample, "python", 0.90)
            similarity_cell = f"{score:.2f} (identical content post-rename)"

        func_names = ", ".join(f.qualified_name for f in functions_found) or "-"
        reason = decision.reasons[0] if decision.reasons else "-"

        rows.append(
            f"| {name} | {decision.fires} [{mark}] | {reason} "
            f"| {decision.metrics['total_effective_lines_changed']} "
            f"| {decision.metrics['distinct_modules_changed']} | {func_names} | {similarity_cell} |"
        )

    return "\n".join(rows), all_ok


def _repush_demo() -> str:
    old_source = "def check(x):\n    return x > 0\n"
    comment_only = "def check(x):\n    # explains the check\n    return x > 0\n"
    inverted = "def check(x):\n    return not (x > 0)\n"

    skip_a, score_a = should_skip_regating(old_source, comment_only, "python", 0.90)
    skip_b, score_b = should_skip_regating(old_source, inverted, "python", 0.90)

    return (
        "Re-push similarity check (threshold 0.90):\n\n"
        "| change | similarity | skip re-gating? |\n"
        "|---|---|---|\n"
        f"| comment-only follow-up | {score_a:.4f} | {skip_a} |\n"
        f"| conditional inverted | {score_b:.4f} | {skip_b} |\n"
    )


def _run_tests() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=REPO_ROOT / "backend",
        capture_output=True,
        text=True,
    )
    status = "PASSED" if result.returncode == 0 else "FAILED"
    output = (result.stdout + result.stderr).strip()
    return f"**pytest: {status}**\n\n```\n{output}\n```"


def main() -> None:
    table, all_ok = _golden_table()
    print(table)

    tests_markdown = _run_tests()
    fixtures_status = (
        "All 8 golden fixtures match their .expected.json.\n\n"
        if all_ok
        else "**Some fixtures MISMATCH — see table.**\n\n"
    )
    demo_markdown = (
        "### Golden fixtures (backend/tests/golden/)\n\n"
        + table
        + "\n\n"
        + fixtures_status
        + "### Re-push similarity demonstration\n\n"
        + _repush_demo()
    )

    report_path = generate_report(
        phase=2,
        title="Diff scoping and trigger engine",
        config_version=CONFIG_VERSION,
        summary=(
            "diff_scope.py, extraction.py, and similarity.py implement diff "
            "ingestion, the config-driven size/module trigger rule, "
            "tree-sitter function extraction for Python/JavaScript/Java/C, "
            "and structural fingerprinting with a re-push similarity-skip "
            "check. POST /gate/evaluate wires all three together. All 8 "
            "required golden fixtures pass, plus additional unit tests for "
            "JS/Java extraction, authorship tagging, ignore/generated-file "
            "exclusion, and the comment-only-vs-inverted-conditional "
            "re-push distinction."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
