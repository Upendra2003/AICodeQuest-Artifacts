"""Phase 4 demo: for one function per level (A1-A4), run the full
perturbation -> test-generation -> quiz-generation pipeline against the
REAL local Ollama model (qwen2.5:7b-instruct) and print a readable
(not-raw-JSON) writeup into the report -- this is the taxonomy-fidelity
sanity-check artifact.

Perturbation verification uses tests/support/local_executor.py (real
local compilation/execution of our own authored code) instead of Judge0,
since Judge0 cannot execute anything in this sandbox (see
reports/phase-3-review.md).

Run via `make demo-phase-4` or directly:

    cd backend && ../.venv/bin/python scripts/demo_phase_4.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))  # backend/tests/ (support/)

from app.core.config import CONFIG_VERSION  # noqa: E402
from app.db.client import get_db  # noqa: E402
from app.services.extraction import extract_functions  # noqa: E402
from app.services.llm import LLMClient  # noqa: E402
from app.services.perturbation import UnperturbableError, inject_perturbation  # noqa: E402
from app.services.quizgen import generate_mcq  # noqa: E402
from app.services.testgen import generate_and_validate_suite  # noqa: E402
from scripts.report import generate_report  # noqa: E402
from support.local_executor import run_locally  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

FIXTURES = {
    "A1": (
        "check.py",
        "def check(x, n):\n    if x < n:\n        return True\n    return False\n",
    ),
    "A2": (
        "add.py",
        "def add(a, b):\n    return a + b\n",
    ),
    "A3": (
        "safe_div.py",
        "def safe_div(a, b):\n    if b == 0:\n        return -1\n    return a // b\n",
    ),
    "A4": (
        "char_code.py",
        "def char_code_at_index(text, target_idx):\n"
        "    result = 0\n"
        "    for i, v in enumerate(text):\n"
        "        if i == target_idx:\n"
        "            result = ord(v)\n"
        "    return result\n",
    ),
}

BUILT = [
    "backend/app/services/llm.py — provider-agnostic client, real verified "
    "Ollama provider (local qwen2.5:7b-instruct), MockLLM for all tests, "
    "Mongo response cache, strict JSON mode with 1 retry, telemetry event "
    "per call that never logs raw prompt/code text",
    "backend/app/services/perturbation.py — A1-A4 template-driven injection "
    "(one deterministic template per level, verified byte-exact "
    "reproducible), LLM fallback, verify-breaks-a-test + regenerate + "
    "un-perturbable path (all real, via tests/support/local_executor.py)",
    "backend/app/services/testgen.py — LLM-generated test cases validated "
    "against original code (drop-rate reported), discriminating-power "
    "check against a perturbation, mutation-kill-rate against standard "
    "mutants built from perturbation.py's own templates",
    "backend/app/services/quizgen.py — 4-option MCQ generation with a QC "
    "gate: structural checks + self-consistency (k re-asks at temperature "
    "0), static per-level fallback bank, accept/reject events logged",
    "config/params.yaml — new llm.opponent_model/ollama_base_url/"
    "json_validation_max_retries, perturbation.max_regen_attempts, "
    "testgen.*, quizgen.self_consistency_k/temperature",
]

OPEN_QUESTIONS = [
    "Provider pivot: llm.provider changed from the Phase 0 placeholder "
    "'groq' (never implemented, no API key) to 'ollama', since a local "
    "Ollama server with qwen2.5:7b-instruct (generator) and mistral:latest "
    "(opponent) was found running in this dev environment. Confirm this "
    "is fine for the study, or if Groq/another hosted provider should "
    "still be added before real participants use this.",
    "Templates are implemented for Python only, one per level (matching "
    "the phase brief's own first example each: off-by-one bound, operator "
    "flip, dropped edge case, index-vs-value confusion) -- not the full "
    "set of examples listed, and not for C/JS/Java. Non-Python functions, "
    "or Python functions none of these 4 templates find a foothold in, "
    "fall through to the LLM path (the fallback this hybrid design "
    "anticipates).",
    "Regeneration semantics: LLM fallback only triggers when NO template "
    "matches at all, per a literal reading of your brief -- not when a "
    "matched template's output fails to verify as breaking a test. A "
    "function with exactly one template candidate that never breaks any "
    "given test case will retry the same (byte-identical) output up to "
    "max_regen_attempts and correctly reach UnperturbableError, rather "
    "than escalating to the LLM. Flag if you intended LLM as a secondary "
    "fallback after template verification failures too.",
    "Perturbation verification (item 2's hard requirement) runs against "
    "tests/support/local_executor.py (real local compile/execute, no "
    "sandboxing) instead of Judge0, since Judge0 cannot execute anything "
    "in this sandbox (Phase 3 finding). This is safe here because only "
    "our own authored templates/harnesses run this way, never "
    "participant-supplied code -- production must go through Judge0 once "
    "deployed somewhere it actually works.",
    "The unstubbable-dependency check's curated builtin allowlist "
    "(harness.py) was missing `ord`/`chr` until this phase's testing "
    "caught it via the A4 fixture -- now fixed, but the list remains "
    "small and curated, not exhaustive; other legitimate builtins may "
    "still be missing.",
    "MCQ fallback bank content is generic placeholder text, not tied to "
    "any specific function or curated for the real study -- it only "
    "guarantees a QC rejection never stalls the user.",
    "quizgen's self-consistency re-asks and testgen's regeneration "
    "attempts both append a bare per-attempt marker to the prompt so "
    "llm.py's response cache doesn't silently collapse k independent "
    "re-asks into one cached answer -- this was a real bug caught while "
    "testing (self-consistency trivially always passed before the fix). "
    "Worth knowing if you add more LLM call sites that repeat an "
    "identical prompt on purpose.",
]


def _diff(original: str, perturbed: str) -> str:
    import difflib

    # extract_functions' source has no trailing newline; without one,
    # unified_diff runs the last removed/added lines together visually.
    if not original.endswith("\n"):
        original += "\n"
    if not perturbed.endswith("\n"):
        perturbed += "\n"

    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            perturbed.splitlines(keepends=True),
            fromfile="original",
            tofile="perturbed",
        )
    )


def _format_case(case, catches: bool) -> str:
    marker = " <-- catches the bug" if catches else ""
    return f"  inputs={case.inputs!r} -> expected={case.expected_output!r}{marker}"


def _run_level(level: str, llm_client: LLMClient, events_collection) -> str:
    file_name, src = FIXTURES[level]
    func = extract_functions(file_name, "python", src, [(1, src.count(chr(10)))])[0]

    lines = [
        f"## Level {level}: `{func.qualified_name}`",
        "",
        "**Original:**",
        "```python",
        func.source,
        "```",
        "",
    ]

    try:
        seed_suite = generate_and_validate_suite(
            func, llm_client, run_locally, events_collection=events_collection
        )
    except Exception as exc:
        lines.append(f"Test generation failed: {exc}\n")
        return "\n".join(lines)

    try:
        perturbation = inject_perturbation(
            func,
            level,
            seed_suite.cases,
            runner=run_locally,
            llm_client=llm_client,
            events_collection=events_collection,
        )
    except UnperturbableError as exc:
        lines.append(f"**Un-perturbable at {level}:** {exc.reason}\n")
        return "\n".join(lines)

    lines += [
        f"**Strategy:** {perturbation.strategy}",
        f"**What changed:** {perturbation.description}",
        "",
        "**Perturbed (diff):**",
        "```diff",
        _diff(func.source, perturbation.perturbed_code).strip(),
        "```",
        "",
    ]

    try:
        full_suite = generate_and_validate_suite(
            func, llm_client, run_locally, perturbation=perturbation, events_collection=events_collection
        )
    except Exception as exc:
        lines.append(f"Full suite validation failed: {exc}\n")
        full_suite = None

    if full_suite is not None:
        perturbed_func = func.model_copy(update={"source": perturbation.perturbed_code})
        from app.services.harness import build_harness, parse_harness_output

        harness = build_harness(perturbed_func, full_suite.cases)
        result = run_locally(harness, "python")
        catching = set()
        if result.verdict == "passed":
            catching = {r.case for r in parse_harness_output(result.stdout) if not r.passed}

        lines.append("**Generated test cases:**")
        for i, case in enumerate(full_suite.cases):
            lines.append(_format_case(case, i in catching))
        lines += [
            "",
            "**Suite validation:**",
            f"- generated: {full_suite.validation.generated_count}, surviving: "
            f"{full_suite.validation.surviving_count}, drop rate: {full_suite.validation.drop_rate:.2f}",
            f"- discriminates perturbation: {full_suite.validation.discriminates_perturbation}",
            f"- mutation kill rate: {full_suite.validation.mutation_kill_rate} "
            f"({full_suite.validation.mutants_tested} mutants tested)",
            "",
        ]

    try:
        mcq = generate_mcq(func, level, llm_client, events_collection=events_collection)
        lines += [
            "**Generated MCQ:**",
            f"- Q: {mcq.question}",
            *[f"  {i}. {opt}" for i, opt in enumerate(mcq.options)],
            f"- Answer: option {mcq.answer_index} | QC: {mcq.source}",
            "",
        ]
    except Exception as exc:
        lines.append(f"MCQ generation failed: {exc}\n")

    return "\n".join(lines)


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
    db = get_db()
    llm_client = LLMClient(db=db)

    sections = []
    for level in ["A1", "A2", "A3", "A4"]:
        print(f"running level {level}...")
        section = _run_level(level, llm_client, db["events"])
        print(section)
        sections.append(section)

    tests_markdown = _run_tests()
    demo_markdown = "\n\n".join(sections)

    report_path = generate_report(
        phase=4,
        title="LLM layer, A1-A4 perturbation injection, test generation, quiz generation",
        config_version=CONFIG_VERSION,
        summary=(
            "llm.py provides a provider-agnostic client with a real, live-verified "
            "Ollama provider, Mongo response caching, strict JSON-mode validation "
            "with one retry, and telemetry that never logs raw prompt/code text. "
            "perturbation.py implements one deterministic, byte-exact-reproducible "
            "template per A1-A4 level plus an LLM fallback, with real "
            "breaks-a-test verification and an un-perturbable path. testgen.py "
            "validates LLM-generated test cases against original code (drop rate "
            "reported), checks discriminating power against a perturbation, and "
            "reports a mutation-kill-rate. quizgen.py generates 4-option MCQs "
            "behind a QC gate (structural checks + self-consistency) with a "
            "static fallback bank. All demonstrated below against the real local "
            "model, one function per level."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
