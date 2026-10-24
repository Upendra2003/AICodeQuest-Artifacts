"""Per-language suite-generation acceptance bench (Phase 32).

WHY THIS EXISTS. The live database says the executable-suite pipeline has
produced 29 suites in production -- every one of them Python -- while 38
non-Python turns (cpp/java/c) were played and graded by comparison instead.
Whether suite generation "works for C++/C/Java/Rust" has never been a
number; it has been an anecdote from whichever pull request was last
played. This script makes it a number, the same way Phase 31 made suite
REJECTIONS a number.

WHAT IT DOES. For every function in the committed corpus
(backend/tests/fixtures/suitegen_bench/ -- one self-contained file per
language, deliberately mixing free functions and class methods, because
the method shape is precisely where the first real C++ pull request
failed), it runs the REAL prefetch-time sequence:

    inject_perturbation(verify=False)   -- templates first, LLM fallback
    generate_suite(...)                 -- the three-gate validator

against the REAL local Ollama and the REAL Piston sandbox, then reports
acceptance rate, latency, and rejection reasons per (language, shape).

WHAT IT DELIBERATELY DOES NOT TOUCH. The production `events` collection:
Phase 31 found 172 self-inflicted `suite_generation_rejected` rows written
by its own benchmark (log_event(collection=None) falls back to the real
Mongo), which made the production distribution unreadable until filtered.
Every event this script generates goes to an in-memory recorder, and the
LLM cache goes to a separate `aicodequest_bench` database so a cached
answer from real play can neither mask latency nor leak back.

Usage:
    python backend/scripts/bench_suitegen.py                       # all languages
    python backend/scripts/bench_suitegen.py --languages cpp,rust
    python backend/scripts/bench_suitegen.py --out reports/assets/bench-suitegen.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import get_config, get_config_version  # noqa: E402
from app.db.client import get_client  # noqa: E402
from app.services.extraction import can_host_a_bug, extract_functions  # noqa: E402
from app.services.file_context import FileContext  # noqa: E402
from app.services.llm import LLMClient  # noqa: E402
from app.services.perturbation import inject_perturbation  # noqa: E402
from app.services.suitegen import generate_suite  # noqa: E402
from app.workers.runners import resolve_runner  # noqa: E402

CORPUS_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "suitegen_bench"

# filename -> language, kept explicit rather than re-deriving from config's
# extension table: the corpus is a fixed research fixture, and a config
# edit must not silently change what the bench measures.
CORPUS_FILES = {
    "inventory.py": "python",
    "inventory.js": "javascript",
    "inventory.c": "c",
    "inventory.cpp": "cpp",
    "Inventory.java": "java",
    "inventory.rs": "rust",
}

LEVELS = ("A1", "A2", "A3", "A4")


class EventRecorder:
    """Duck-typed stand-in for a Mongo collection: keeps every event in
    memory so the bench can report rejection reasons WITHOUT writing a
    single row to the production `events` collection."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def insert_one(self, doc: dict[str, Any]) -> Any:
        self.events.append(doc)

        class _Result:
            inserted_id = None

        return _Result()


def load_corpus(languages: list[str]) -> list[dict[str, Any]]:
    """One entry per usable function: {file, language, func, shape}."""
    minimum = get_config()["developer_gate"]["min_function_logic_nodes"]
    entries: list[dict[str, Any]] = []
    for filename, language in CORPUS_FILES.items():
        if language not in languages:
            continue
        path = CORPUS_DIR / filename
        source = path.read_text()
        funcs = extract_functions(
            filename, language, source, [(1, source.count("\n") + 1)]
        )
        for func in funcs:
            if not can_host_a_bug(func.source, language, minimum):
                continue
            # Constructors are extraction artifacts here, not exercises.
            # Only a DOTTED name can be one: `Inventory.Inventory` (java),
            # `Inventory.__init__` (python), `Inventory.new` (rust).
            if "." in func.qualified_name:
                owner, bare = func.qualified_name.rsplit(".", 1)
                if bare in ("__init__", "new") or bare == owner.rsplit(".", 1)[-1]:
                    continue
            entries.append(
                {
                    "file": filename,
                    "language": language,
                    "func": func,
                    "file_source": source,
                    "shape": "method" if "." in func.qualified_name else "free",
                }
            )
    return entries


def run_bench(
    entries: list[dict[str, Any]],
    llm_client: LLMClient,
    runner,
    per_language_cap: Optional[int],
    isolated: bool = False,
) -> list[dict[str, Any]]:
    """`isolated=True` withholds the file context, measuring the
    pre-Phase-32 assembly through this same loop. That is the only honest
    way to attribute a gain: the loop itself changed too (it now mirrors
    production's perturbation retry), so comparing against the first
    baseline run would credit file context for both changes."""
    results: list[dict[str, Any]] = []
    taken: Counter = Counter()
    for i, entry in enumerate(entries):
        language = entry["language"]
        if per_language_cap and taken[language] >= per_language_cap:
            continue
        taken[language] += 1
        func = entry["func"]
        level = LEVELS[i % len(LEVELS)]
        recorder = EventRecorder()
        row: dict[str, Any] = {
            "language": language,
            "file": entry["file"],
            "function": func.qualified_name,
            "shape": entry["shape"],
            "level": level,
        }
        print(f"[{language:>6}] {func.qualified_name:<28} {level} ... ", end="", flush=True)
        file_context = FileContext(
            file=entry["file"], language=language, source=entry["file_source"]
        )
        # Mirror dev_gate._generate_turn_content: a suite that cannot tell
        # the perturbed code from the original means the BUG is a no-op, so
        # production regenerates the perturbation and tries again rather
        # than giving up. Measuring one perturbation per function would
        # report a turn as unservable that production would in fact serve.
        max_bug_attempts = get_config()["perturbation"]["max_bug_verification_attempts"]
        t0 = time.monotonic()
        suite = None
        all_rejections: list[str] = []
        rejected_as_no_op: set[str] = set()
        bug_attempts = 0
        failure: Optional[str] = None

        for bug_attempt in range(max_bug_attempts):
            bug_attempts = bug_attempt + 1
            try:
                perturbation = inject_perturbation(
                    func,
                    level,
                    [],
                    runner=runner,
                    seed=i * 97 + bug_attempt * 97,
                    llm_client=llm_client,
                    events_collection=recorder,
                    avoid_perturbed_code=rejected_as_no_op,
                    verify=False,
                )
            except Exception as exc:  # noqa: BLE001
                failure = f"{type(exc).__name__}: {exc}"[:200]
                break

            suite, rejections = generate_suite(
                func,
                perturbation.perturbed_code,
                llm_client,
                runner,
                pr_ref="bench",
                session_ref="bench",
                events_collection=recorder,
                # Phase 32: the same file-context the production path now
                # passes; suitegen's own policy decides whether to use it.
                file_context=None if isolated else file_context,
            )
            all_rejections.extend(r.reason for r in rejections)
            if suite is not None:
                break
            if not any(r.reason == "no_discrimination" for r in rejections):
                break  # a format/compile failure: a new bug would not help
            rejected_as_no_op.add(perturbation.perturbed_code)

        latency_ms = round((time.monotonic() - t0) * 1000)
        row.update(
            accepted=suite is not None,
            stage="perturbation" if (failure and suite is None) else "suite",
            attempts=(suite.attempts if suite else None),
            bug_attempts=bug_attempts,
            model=(suite.model if suite else None),
            cases=(suite.case_count if suite else 0),
            mode=("context" if (suite and suite.context_template) else "isolated"),
            rejections=all_rejections + ([failure] if failure else []),
            latency_ms=latency_ms,
        )
        results.append(row)
        if failure and suite is None:
            print(f"PERTURBATION FAILED ({failure.split(':')[0]}) in {latency_ms}ms")
        else:
            print(
                f"{'OK' if suite else 'NO SUITE'} in {latency_ms}ms "
                f"(bug attempts: {bug_attempts}, rejections: {all_rejections or 'none'})"
            )
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for language in sorted({r["language"] for r in results}):
        rows = [r for r in results if r["language"] == language]
        accepted = [r for r in rows if r["accepted"]]
        latencies = [r["latency_ms"] for r in rows if r.get("stage") == "suite"]
        reasons = Counter(reason for r in rows for reason in r.get("rejections", []))
        by_shape = {}
        for shape in ("free", "method"):
            shape_rows = [r for r in rows if r["shape"] == shape]
            if shape_rows:
                by_shape[shape] = {
                    "tried": len(shape_rows),
                    "accepted": sum(1 for r in shape_rows if r["accepted"]),
                }
        summary[language] = {
            "tried": len(rows),
            "accepted": len(accepted),
            "acceptance_pct": round(100 * len(accepted) / len(rows), 1) if rows else 0.0,
            "median_latency_ms": round(statistics.median(latencies)) if latencies else None,
            "mean_latency_ms": round(statistics.mean(latencies)) if latencies else None,
            "rejection_reasons": dict(reasons.most_common()),
            "by_shape": by_shape,
        }
    return summary


def print_table(summary: dict[str, Any]) -> None:
    print("\n" + "=" * 76)
    print(f"{'language':<10} {'tried':>5} {'accept':>6} {'rate':>7} {'median':>8}  by shape")
    print("-" * 76)
    for language, s in summary.items():
        shapes = "  ".join(
            f"{shape}:{v['accepted']}/{v['tried']}" for shape, v in s["by_shape"].items()
        )
        med = f"{s['median_latency_ms']}ms" if s["median_latency_ms"] is not None else "-"
        print(
            f"{language:<10} {s['tried']:>5} {s['accepted']:>6} "
            f"{s['acceptance_pct']:>6}% {med:>8}  {shapes}"
        )
        if s["rejection_reasons"]:
            print(f"{'':10} rejections: {s['rejection_reasons']}")
    print("=" * 76)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--languages",
        default=",".join(sorted(set(CORPUS_FILES.values()))),
        help="comma-separated subset of: " + ", ".join(sorted(set(CORPUS_FILES.values()))),
    )
    parser.add_argument(
        "--per-language-cap", type=int, default=None,
        help="bench at most N functions per language",
    )
    parser.add_argument(
        "--isolated", action="store_true",
        help="withhold file context (measures the pre-Phase-32 assembly)",
    )
    parser.add_argument(
        "--out", default=None,
        help="write full per-function results as JSON to this path",
    )
    args = parser.parse_args()
    languages = [x.strip() for x in args.languages.split(",") if x.strip()]

    entries = load_corpus(languages)
    if not entries:
        print("no corpus entries matched", file=sys.stderr)
        return 2

    # A dedicated database: the bench must not read real play's LLM cache
    # (a hit would fake the latency) and must not write into it either.
    bench_db = get_client()["aicodequest_bench"]
    llm_client = LLMClient(db=bench_db)
    runner = resolve_runner(None)

    print(
        f"bench: {len(entries)} function(s) across {languages}; "
        f"config {get_config_version()}; llm cache db aicodequest_bench; "
        f"mode {'ISOLATED (pre-Phase-32)' if args.isolated else 'file-context'}"
    )
    results = run_bench(
        entries, llm_client, runner, args.per_language_cap, isolated=args.isolated
    )
    summary = summarize(results)
    print_table(summary)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "config_version": get_config_version(),
                    "summary": summary,
                    "results": results,
                },
                indent=2,
            )
        )
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
