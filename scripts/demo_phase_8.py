"""Phase 8 demo: builds the extension, runs its unit tests (Vitest +
sinon-chrome) and its Playwright e2e suite (real Chromium, real unpacked
extension, a saved fixture PR page), embeds the resulting screenshot, and
appends the manual checklist for what automation can't cover.

Run via `make demo-phase-8` or directly:

    cd backend && ../.venv/bin/python scripts/demo_phase_8.py

Requires Node/npm and `extension/`'s devDependencies installed
(`cd extension && npm install`). The Playwright suite needs a real
(possibly virtual) display -- see the Xvfb note in this file's
MANUAL_CHECKLIST / reports/phase-8-review.md open questions: Chrome does
not load unpacked MV3 extensions in headless mode at all, so
`headless: true` was never an option here, only `headless: false` against
a real or virtual display.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.core.config import CONFIG_VERSION  # noqa: E402
from scripts.report import generate_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = REPO_ROOT / "extension"
SCREENSHOT_PATH = REPO_ROOT / "reports" / "assets" / "phase8-badge-injected.png"

BUILT = [
    "extension/manifest.json + extension/scripts/build.js -- MV3 manifest "
    "with the minimum viable permission set (`storage` + `host_permissions` "
    "scoped to exactly the backend's origin, computed from "
    "config/params.yaml's new `extension.backend_base_url` at build time, "
    "never hand-duplicated); three separate Vite builds (service worker "
    "+ content script as IIFE, popup as a normal ES-module page) because "
    "content scripts cannot be ES modules in MV3",
    "extension/src/background/service_worker.js -- exposes PING/LOGIN/"
    "GET_ME/EVALUATE_GATE over chrome.runtime messaging; every handler "
    "re-reads chrome.storage.local instead of caching in memory, since "
    "MV3 service workers are ephemeral and Chrome can kill/restart them "
    "at any time (see docs/CHROME-EXTENSION-PRIMER.md section 1.3)",
    "extension/src/popup/ -- login form, logged-in-user display, a "
    "backend-reachable indicator (via PING), and a live debug panel "
    "(chrome.storage.session, last 10 messages, updated via "
    "chrome.storage.onChanged)",
    "extension/src/content/pr_page.js -- matches "
    "https://github.com/*/*/pull/* only (not all of github.com), logs a "
    "detection line, injects one non-functional badge; deliberately not "
    "coupled to GitHub's actual DOM structure",
    "extension/src/shared/api.js + messages.js -- the backend client "
    "(used ONLY by the service worker -- never the content script, for "
    "CORS + token-leak reasons explained in the primer) and the "
    "message-type router, both independent of chrome.* entirely and "
    "directly unit-testable without any mocking",
    "docs/CHROME-EXTENSION-PRIMER.md -- full MV3 architecture, permission "
    "rationale, message-passing patterns, storage-area tradeoffs + JWT "
    "risk/mitigation, the local dev loop and its three separate consoles, "
    "five causes of 'my content script didn't run', packaging, and a "
    "troubleshooting table",
    "config/params.yaml's new `extension` section (`backend_base_url`), "
    "read at BUILD time by extension/scripts/build.js, not fetched at "
    "runtime",
]

MANUAL_CHECKLIST = [
    "[ ] Popup login works against a REAL running backend (not just the "
    "mocked unit tests / fixture-page e2e test above) -- start the "
    "backend for real (`flask run`, port matching "
    "config/params.yaml's extension.backend_base_url), load the built "
    "extension unpacked, open the popup, and log in with a real "
    "registered user.",
    "[ ] The three consoles described in the primer's section 5.1 show "
    "what it says they will: service worker logs ONLY under "
    "chrome://extensions -> \"service worker\" link; popup logs ONLY via "
    "right-click-inside-the-popup -> Inspect; content script logs "
    "interleaved with the PAGE's own console on an actual github.com PR "
    "tab.",
    "[ ] The backend-reachable indicator in the popup correctly flips "
    "red when the backend is stopped, and green again once it's "
    "restarted, without reloading the extension.",
    "[ ] Loading the SAME unpacked extension twice from different paths "
    "(simulating a stale copy) surfaces via chrome://extensions rather "
    "than silently shadowing -- sanity check for troubleshooting-table "
    "row 6.",
]

OPEN_QUESTIONS = [
    "Chrome does NOT load unpacked MV3 extensions in headless mode at "
    "all -- verified directly for this project (zero service workers, "
    "zero content-script injection under `headless: true`, confirmed "
    "working immediately under `headless: false` against a display). "
    "This sandbox has no sudo/apt access, so a real X server (Xvfb) had "
    "to be installed via conda into a separate env "
    "(`conda create -n pw-xvfb -c conda-forge xorg-xvfb-server`) as a "
    "one-time workaround to actually run and verify the Playwright "
    "suite here. `extension/package.json`'s `test:e2e` script instead "
    "assumes the standard, portable `xvfb-run` (from the `xvfb` apt "
    "package, present on GitHub Actions' ubuntu-latest and any normal "
    "Debian/Ubuntu dev machine) -- confirm this is the right thing to "
    "commit as the canonical/CI approach rather than something else.",
    "POST-login token storage uses a short-lived JWT in "
    "chrome.storage.local with no refresh-token flow (the participant "
    "re-logs-in via the popup when it expires) -- an explicitly accepted "
    "trade-off for this phase, not a solved problem for a production "
    "deployment with real industry participants' credentials. See the "
    "primer's section 4.1.",
    "extension.backend_base_url defaults to http://localhost:5055 "
    "(moved off Flask's default 5000, which another project on the study "
    "host already holds) -- the backend had never actually been served "
    "outside `flask test_client()` until this phase needed a real base "
    "URL to build against. Confirm this before any real deployment "
    "target is decided.",
    "Distribution is unpacked-only for the study (no Chrome Web Store "
    "listing) -- see the primer's section 7 for exactly what that means "
    "for participant onboarding. Whether a private/unlisted Web Store "
    "listing is worth pursuing for a smoother install experience is a "
    "decision for a later phase.",
    "The content script's injected badge is fixed-position and "
    "deliberately NOT coupled to GitHub's real DOM structure (see "
    "pr_page.js's module docstring) -- correct for THIS phase's proof-"
    "of-injection scope, but Phase 9's real game UI will need to decide "
    "an actual, more durable injection point into GitHub's PR page "
    "layout.",
]


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    output = (result.stdout + result.stderr).strip()
    status = "PASSED" if result.returncode == 0 else "FAILED"
    return f"**{' '.join(cmd)}: {status}**\n\n```\n{output}\n```"


def run_unit_tests() -> str:
    return run(["npm", "run", "test:unit"], cwd=EXTENSION_DIR)


def build_extension() -> str:
    return run(["npm", "run", "build"], cwd=EXTENSION_DIR)


def run_e2e_tests() -> str:
    import os

    env = os.environ.copy()
    if shutil.which("xvfb-run"):
        cmd = ["npx", "xvfb-run", "--auto-servernum", "npx", "playwright", "test"]
    elif env.get("DISPLAY"):
        # No xvfb-run on PATH, but a display (real or already-running
        # Xvfb) is available in this environment -- use it directly.
        # See this script's OPEN_QUESTIONS for why this sandbox needed a
        # conda-installed Xvfb instead of the standard apt package.
        cmd = ["npx", "playwright", "test"]
    else:
        return (
            "**Playwright e2e: SKIPPED**\n\n"
            "No `xvfb-run` on PATH and no `$DISPLAY` set -- Chrome cannot "
            "load an unpacked extension in headless mode (verified "
            "directly, see OPEN_QUESTIONS), so this suite needs a real or "
            "virtual display. Install `xvfb` (`apt install xvfb`) or "
            "export `DISPLAY` pointing at a running X server, then re-run."
        )
    return run(cmd, cwd=EXTENSION_DIR, env=env)


def main() -> None:
    unit_markdown = run_unit_tests()
    build_markdown = build_extension()
    e2e_markdown = run_e2e_tests()

    demo_parts = [
        "### Unit tests (Vitest -- message router, api client, "
        "service-worker handler logic + restart survival, and one "
        "sinon-chrome-mocked wiring test)",
        "",
        unit_markdown,
        "",
        "### Build (`npm run build` -> extension/dist/)",
        "",
        build_markdown,
        "",
        "### Playwright e2e (real Chromium, real unpacked extension, "
        "saved fixture PR page -- backend/tests/fixtures/pr_pages/sample_pr.html)",
        "",
        e2e_markdown,
        "",
    ]

    if SCREENSHOT_PATH.exists():
        demo_parts += [
            "### Screenshot: badge injected into the fixture PR page",
            "",
            f"![AICodeQuest badge injected into a GitHub PR page](assets/{SCREENSHOT_PATH.name})",
            "",
        ]
    else:
        demo_parts += [
            "### Screenshot: NOT FOUND",
            "",
            f"Expected {SCREENSHOT_PATH} -- the Playwright suite above should have produced it.",
            "",
        ]

    demo_parts += [
        "### Manual checklist (things automation in this phase cannot cover)",
        "",
        "\n".join(MANUAL_CHECKLIST),
    ]

    demo_markdown = "\n".join(demo_parts)
    print(demo_markdown)

    tests_markdown = f"{unit_markdown}\n\n{e2e_markdown}"

    report_path = generate_report(
        phase=8,
        title="Chrome extension foundations + primer documentation",
        config_version=CONFIG_VERSION,
        summary=(
            "docs/CHROME-EXTENSION-PRIMER.md covers the full MV3 "
            "architecture (manifest/service worker/content script/popup, "
            "the isolated-world and ephemeral-service-worker caveats "
            "explicitly), the exact minimum-viable permission set and why "
            "each one is there, message-passing patterns with runnable "
            "snippets, the three chrome.storage areas and why the JWT "
            "lives in .local (not .session or .sync) with its risk/"
            "mitigation spelled out, the local dev loop including the "
            "three genuinely separate consoles, five causes of 'my "
            "content script didn't run', packaging (unpacked vs. Web "
            "Store) and what that means for the study, and a "
            "troubleshooting table built from errors actually hit while "
            "building this phase. The minimal extension itself "
            "(manifest.json, service worker, popup, content script, "
            "shared api client) is built with Vite (extension/scripts/"
            "build.js) and verified two ways: Vitest unit tests (message "
            "router + api client, chrome-independent by design, plus one "
            "sinon-chrome-mocked wiring test and a restart-survival test) "
            "and a Playwright e2e test that loads the REAL built "
            "extension into a REAL Chromium against a saved fixture PR "
            "page, asserting the badge actually gets injected and "
            "capturing a screenshot -- not just testing the build output "
            "in isolation."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
