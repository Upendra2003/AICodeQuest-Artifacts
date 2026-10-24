"""Phase 3 demo: run the full adversarial battery against the real Judge0
instance and print a verdict table (+ observed time/memory per case) into
the report. This is the safety review surface for this phase.

IMPORTANT: as documented at length in reports/phase-3-review.md, this
sandbox's host runs cgroup v2 only, which Judge0 v1.13.1's bundled
`isolate` sandbox cannot use (needs legacy cgroup v1). Every row below
that needs real code EXECUTION will show verdict=infra_error here — that
is Judge0 failing to sandbox anything at all, not a passing safety test.
Only the language-resolution rows and the harness-build-error row (which
never reaches Judge0) are real, verified results in this environment.

Run via `make demo-phase-3` or directly:

    cd backend && ../.venv/bin/python scripts/demo_phase_3.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.core.config import CONFIG_VERSION, get_config  # noqa: E402
from app.services.execution import Judge0Client, LanguageMissingError  # noqa: E402
from app.services.extraction import extract_functions  # noqa: E402
from app.services.harness import (  # noqa: E402
    HarnessBuildError,
    TestCase,
    build_harness,
    parse_harness_output,
)
from scripts.report import generate_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

BUILT = [
    "infra/judge0/ — Judge0 v1.13.1 self-hosted via docker-compose (server, "
    "workers, its own postgres+redis, both server/workers privileged per "
    "upstream's own requirement); judge0.conf.template + render_conf.py "
    "generate judge0.conf from config/params.yaml so limits never drift",
    "config/params.yaml — new execution.* knobs: max_processes_and_threads, "
    "poll_interval_sec, poll_timeout_buffer_sec, max_infra_error_retries, "
    "infra_error_retry_backoff_sec, language_name_patterns",
    "backend/app/services/execution.py — Judge0Client: dynamic language_id "
    "resolution (cached, verified live), submit/poll (wait=false only), "
    "ExecutionResult normalization (verified status.id mapping against a "
    "live GET /statuses), infra_error-only retry (verified both live and "
    "via deterministic mocks)",
    "backend/app/services/harness.py — per-language (C/Python/JS/Java) "
    "self-contained harness generation from an extracted function + scalar "
    "test cases; HarnessBuildError for unstubbable dependencies and "
    "non-testable methods, verified by actually compiling/running every "
    "generated harness locally",
]

OPEN_QUESTIONS = [
    "CRITICAL: Judge0 cannot execute any code in this sandbox. A trivial "
    "`print(1+2)` submission returns Internal Error: 'Failed to create "
    "control group /sys/fs/cgroup/memory/box-1/: No such file or "
    "directory'. Confirmed root cause: this host runs cgroup v2 only "
    "(`mount` shows cgroup2 unified hierarchy, no v1 at all), but Judge0 "
    "1.13.1's bundled isolate needs legacy cgroup v1. This is a known, "
    "currently-unresolved upstream issue (judge0/judge0#554) with no "
    "official fix. NONE of this phase's adversarial containment claims "
    "(fork bomb, high-memory, network/filesystem escape, timeout) are "
    "verified in this environment — they're implemented and will run for "
    "real the moment this is deployed on a cgroup v1-capable host (most "
    "cloud VMs and dedicated Docker hosts qualify; this specific sandbox "
    "does not). Please don't treat this phase as a safety sign-off until "
    "it's been run somewhere Judge0 can actually execute code.",
    "What DID get verified live here: GET /languages and GET /statuses "
    "(language_id resolution + status mapping are real, not assumed), "
    "submission creation/polling mechanics (real HTTP round-trips), the "
    "infra_error retry ceiling (real — every submission here genuinely "
    "errors, so retry-then-give-up was exercised for real, not mocked), "
    "and the full harness builder for all 4 languages (compiled/ran "
    "locally with python3/node/javac/gcc, no Judge0 needed).",
    "Harness builder scope limits (see harness.py module docstring): "
    "test case inputs/expected_output are scalars only (int/float/str/"
    "bool) — no lists/dicts. The unstubbable-dependency check only "
    "catches bare calls (`foo()`), not attribute/method calls "
    "(`obj.foo()`). Python/JavaScript class methods are always "
    "untestable (HarnessBuildError) regardless of @staticmethod/static, "
    "since Phase 2's extraction output doesn't carry decorator/modifier "
    "info reliably; Java is the exception (methods are testable when "
    "their signature text contains the word 'static', a substring check "
    "not real modifier parsing) since Java has no true top-level "
    "functions at all.",
    "C harness only supports int-typed functions/args/expected_output — "
    "C's static typing means float/string returns would need real "
    "return-type parsing, not attempted this phase.",
    "language_name_patterns picks the highest-version match by regex on "
    "the language name when multiple variants exist (e.g. 3 GCC C "
    "versions) — resolved python=71, javascript=63, java=62, c=50 "
    "against this Judge0 instance.",
    "ALLOW_ENABLE_NETWORK=false is hard-locked in judge0.conf (not just "
    "ENABLE_NETWORK=false) so a submission can't request network access "
    "even if it tried — confirm this is the right call vs. leaving it "
    "overridable per-submission for some future use case.",
]


def _language_resolution_section(client: Judge0Client) -> tuple[str, dict]:
    try:
        ids = client.resolve_language_ids()
        rows = "\n".join(f"| {k} | {v} |" for k, v in sorted(ids.items()))
        return (
            "### Language resolution (live GET /languages)\n\n"
            "| language | resolved language_id |\n|---|---|\n" + rows + "\n",
            ids,
        )
    except LanguageMissingError as exc:
        return f"### Language resolution FAILED\n\n```\n{exc}\n```\n", {}


def _adversarial_battery(client: Judge0Client, language_ids: dict) -> str:
    header = "| test | verdict | time_ms | memory_kb | notes |"
    sep = "|---|---|---|---|---|"
    rows = [header, sep]

    def add_row(name, result=None, notes=""):
        if result is None:
            rows.append(f"| {name} | - | - | - | {notes} |")
        else:
            rows.append(
                f"| {name} | {result.verdict} | {result.time_ms} | {result.memory_kb} | {notes} |"
            )

    # 1. Unstubbable dependency -- never reaches Judge0, real regardless of environment.
    src = "def compute(x):\n    return validate_input(x) + 1\n"
    func = extract_functions("m.py", "python", src, [(1, 2)])[0]
    try:
        build_harness(func, [TestCase(inputs=[1], expected_output=2)])
        add_row("unstubbable dependency -> HarnessBuildError", notes="FAILED: did not raise")
    except HarnessBuildError as e:
        add_row("unstubbable dependency -> HarnessBuildError", notes=f"raised correctly: {e.reason[:60]}...")

    if "c" not in language_ids or "python" not in language_ids:
        add_row("(remaining adversarial tests)", notes="skipped: language resolution failed")
        return "\n".join(rows)

    # 2. Correct C function
    src = (
        "int max_of_three(int a, int b, int c) {\n"
        "    int m = a;\n    if (b > m) m = b;\n    if (c > m) m = c;\n    return m;\n}\n"
    )
    func = extract_functions("m.c", "c", src, [(1, 5)])[0]
    harness = build_harness(func, [TestCase(inputs=[1, 2, 3], expected_output=3)])
    result = client.run(harness, language_ids["c"])
    add_row("correct C function passes all cases", result)

    # 3. Off-by-one boundary
    src = "int is_in_range(int x, int lo, int hi) {\n    return x >= lo && x < hi;\n}\n"
    func = extract_functions("m.c", "c", src, [(1, 3)])[0]
    harness = build_harness(func, [TestCase(inputs=[5, 0, 5], expected_output=1)])
    result = client.run(harness, language_ids["c"])
    boundary_note = ""
    if result.verdict == "passed":
        parsed = parse_harness_output(result.stdout)
        boundary_note = f"case passed={parsed[0].passed if parsed else 'n/a'} (expected False)"
    add_row("off-by-one C function fails boundary case", result, boundary_note)

    # 4. Infinite loop -> timeout
    result = client.run("while True:\n    pass\n", language_ids["python"])
    add_row("infinite loop -> timeout", result)

    # 5. Fork bomb
    src = "#include <unistd.h>\nint main(void) {\n    while (1) { fork(); }\n    return 0;\n}\n"
    result = client.run(src, language_ids["c"])
    add_row("fork bomb contained", result)

    # 6. High memory allocation
    cfg = get_config()["execution"]
    over_mb = (cfg["memory_limit_kb"] * 10) // 1024
    src = (
        "#include <stdlib.h>\n#include <string.h>\n"
        "int main(void) {\n"
        f"    size_t n = {over_mb}UL * 1024 * 1024;\n"
        "    char *p = malloc(n);\n    if (p) memset(p, 1, n);\n    return 0;\n}\n"
    )
    result = client.run(src, language_ids["c"])
    add_row("high-memory allocation contained", result)

    # 7. Network escape
    src = (
        "import socket\ntry:\n"
        "    s = socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
        "    print('CONNECTED')\nexcept Exception:\n    print('BLOCKED')\n"
    )
    result = client.run(src, language_ids["python"])
    add_row("network access blocked", result, (result.stdout or "").strip())

    # 8. Filesystem escape
    src = (
        "read_ok = False\nwrite_ok = False\n"
        "try:\n    open('/etc/passwd').read()\n    read_ok = True\nexcept Exception:\n    pass\n"
        "try:\n    open('/escaped.txt', 'w').write('x')\n    write_ok = True\nexcept Exception:\n    pass\n"
        "print(f'read_ok={read_ok} write_ok={write_ok}')\n"
    )
    result = client.run(src, language_ids["python"])
    add_row("filesystem escape contained", result, (result.stdout or "").strip())

    # Host health check after the dangerous ones.
    import requests

    healthy = False
    try:
        resp = requests.get(f"{client.base_url}/about", timeout=10)
        healthy = resp.status_code == 200
    except requests.RequestException:
        healthy = False
    add_row("Judge0 API still responsive after battery", notes=f"healthy={healthy}")

    return "\n".join(rows)


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
    client = Judge0Client()
    lang_section, language_ids = _language_resolution_section(client)
    print(lang_section)

    battery_table = _adversarial_battery(client, language_ids)
    print(battery_table)

    tests_markdown = _run_tests()
    demo_markdown = (
        lang_section
        + "\n### Adversarial battery\n\n"
        + battery_table
        + "\n\n**Read `verdict=infra_error` above as \"Judge0 could not sandbox this "
        "at all\" in this environment, not as a passing containment result — see "
        "open question 1.**\n"
    )

    report_path = generate_report(
        phase=3,
        title="Sandboxed execution via Judge0 with function-level harnesses",
        config_version=CONFIG_VERSION,
        summary=(
            "Judge0 v1.13.1 is self-hosted via docker-compose (server, workers, "
            "its own postgres+redis). execution.py provides a typed submit/poll "
            "client with dynamic language_id resolution and infra_error-only "
            "retries; harness.py generates and runs function-level test "
            "harnesses for C/Python/JavaScript/Java. Language resolution, "
            "status-code mapping, submission mechanics, retry logic, and the "
            "full harness builder are verified for real. Live adversarial "
            "execution (correctness, timeout, fork bomb, high-memory, network/"
            "filesystem escape) could NOT be verified in this sandbox — see "
            "open question 1, this is the most important thing in this report."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
