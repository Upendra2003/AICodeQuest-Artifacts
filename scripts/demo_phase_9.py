"""Phase 9 demo: builds the extension, runs its unit tests (Vitest) and
its full Playwright e2e suite (real Chromium, real unpacked extension,
saved fixture PR pages, a real Flask+ScriptedLLM backend --
backend/scripts/e2e_server.py), then assembles reports/phase-9-review.md
as a SCREENSHOT WALKTHROUGH in narrative order: arrive at a PR -> tag
hunks -> gate fires -> play -> verdict -> cleared -> button re-enabled,
for both the Developer gate (new-PR/compare page) and the Lead gate
(existing PR conversation page).

Run via `make demo-phase-9` or directly:

    cd backend && ../.venv/bin/python scripts/demo_phase_9.py

Requires Node/npm and `extension/`'s devDependencies installed
(`cd extension && npm install`), plus a display for Playwright -- see
Phase 8's Xvfb note, unchanged here (Chrome does not load unpacked MV3
extensions in headless mode at all).
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
ASSETS_DIR = REPO_ROOT / "reports" / "assets"

# (filename, caption) in the exact narrative order the demo walks through.
WALKTHROUGH = [
    ("phase9-01-new-pr-gate-fires.png",
     "Developer arrives at a new-PR (compare) page with a large AI-tagged "
     "diff. After tagging every hunk in the AI-authorship panel, "
     "/gate/evaluate fires: the Create pull request button is disabled "
     "and the red comprehension-gate icon appears beside it."),
    ("phase9-02-pr-gate-fires.png",
     "Same gate, from the Lead's side: an existing PR's Conversation tab, "
     "Merge pull request disabled, the same red icon."),
    ("phase9-03-panel-monaco-open.png",
     "Clicking the icon opens the injected iframe panel (chrome-"
     "extension://.../game.html, not inline React in github.com's "
     "isolated world) -- Monaco renders the first Defense task with its "
     "level badge and timer."),
    ("phase9-04-dev-play-01-gate-fires.png",
     "Developer playthrough, start: gate fired, panel about to open."),
    ("phase9-04-dev-play-02-verdict.png",
     "First Defense turn submitted correctly -- verdict banner shows "
     "'Correct!'."),
    ("phase9-04-dev-play-03-cleared.png",
     "After all Round 1 Defense turns (2 questions x 4 levels) and Round "
     "1 Attack turns (skipped -- attack_counts_toward_score was false; that "
     "key was replaced by developer_gate.score_weights on 2026-09-24) are "
     "done, the score is 100% >= pass_threshold_pct (80): CLEARED, no "
     "Round 2 scaffold needed for this clean-sweep run."),
    ("phase9-04-dev-play-04-button-reenabled.png",
     "Panel closed: the Create pull request button is genuinely enabled "
     "again -- content/pr_page.js's handleGateClearedMessage() reacting "
     "to the iframe's GATE_CLEARED postMessage."),
    ("phase9-05-lead-play-01-gate-fires.png",
     "Lead playthrough, start: same gate-fired state on the PR "
     "conversation page."),
    ("phase9-05-lead-play-02-nfr-select.png",
     "Lead panel: NFR multi-select screen (a custom NFR can also be "
     "typed in) -- one NFR selected here to keep the walkthrough short "
     "(questions_per_nfr=1)."),
    ("phase9-05-lead-play-03-result.png",
     "After answering the diff-grounded trade-off question, the score "
     "breakdown table shows per-criterion points and feedback."),
    ("phase9-05-lead-play-04-cleared.png",
     "Weighted score >= lead_gate.pass_threshold_pct (80): CLEARED."),
    ("phase9-05-lead-play-05-button-reenabled.png",
     "Panel closed: the Merge pull request button is genuinely enabled "
     "again."),
    ("phase9-06-dark-mode-gate-fires.png",
     "Dark-mode PR page: selectors.js's candidates still match GitHub's "
     "dark theme (data-color-mode=\"dark\"), gate UI renders correctly."),
]

BUILT = [
    "extension/src/content/selectors.js -- one selector registry "
    "(prTitle, createPrButton, mergeButton, filesChangedContainer), each "
    "an ordered list of candidate CSS selectors, with a findElement() "
    "that never throws and reports a selector miss via an injectable "
    "reporter instead",
    "extension/src/content/page_state.js -- SPA navigation detection via "
    "a debounced MutationObserver + popstate (NOT monkey-patched "
    "history.pushState/replaceState -- verified that doesn't reliably "
    "cross the isolated-world boundary)",
    "extension/src/content/diff_utils.js -- unified-diff parsing in JS "
    "(function extraction for dev_gate's /start, per-hunk extraction for "
    "the tagging panel), mirroring backend/app/services/diff_scope.py's "
    "_reconstruct_new_content approach",
    "extension/src/content/tagging_panel.js -- the AI-authorship "
    "self-tagging panel required by the Phase 2 trigger contract: "
    "nothing proceeds until every hunk is tagged",
    "extension/src/content/gate_ui.js -- neutral badge (fires:false) or "
    "disabled-button + red icon + a PLAIN-TEXT (not just tooltip) "
    "cosmetic-enforcement disclaimer (fires:true); DOM-disabling a button "
    "is explicitly documented as non-enforcing -- real enforcement is "
    "GitHub's Checks API (Phase 10)",
    "extension/src/content/panel.js -- the injected iframe overlay "
    "(game.html), idempotent open, explicit or Escape-key close, tied to "
    "one pr_id so navigating to a different PR closes it",
    "extension/src/content/pr_page.js -- the orchestrator: SPA-aware "
    "context detection -> tag -> /gate/evaluate -> badge or fired UI -> "
    "panel open/close -> GATE_CLEARED message handling to re-enable the "
    "button once the game reports CLEARED",
    "extension/src/game/ -- React + Monaco game shell: CodeEditor.jsx "
    "(direct monaco.editor.create(), mount-once), useDevGate.js/"
    "useLeadGate.js (Phase 5/7 API lifecycles), DevGameApp.jsx (task/"
    "verdict/attack/scaffold/cleared/escalated screens), LeadGameApp.jsx "
    "(NFR select/question/result/cleared/escalated screens)",
    "backend/app/api/telemetry.py -- POST /telemetry/selector-miss, the "
    "backend side of selectors.js's miss-reporting contract",
    "backend/app/services/lead_gate.py -- submit_response() now returns "
    "criterion_scores (previously computed and stored but never returned "
    "to the caller) -- needed for the Lead panel's score-breakdown table",
    "backend/scripts/e2e_server.py -- a real listening Flask server "
    "(mongomock + ScriptedLLM + run_locally) for Playwright's genuine "
    "HTTP calls, which flask test_client() cannot service",
    "backend/tests/fixtures/pr_pages/ -- 5 hand-written fixtures: normal, "
    "draft (no merge button), conflicts (merge button present but "
    "disabled), compare/new-PR, dark mode",
    "extension/tests/unit/ -- selector tests against all 5 fixtures, "
    "page_state/pr_page/gate_ui/panel/api unit tests (17+11+8+8+ tests, "
    "chrome-independent by design plus one sinon-chrome wiring path)",
    "extension/tests/e2e/ -- gate_ui, dev_playthrough, lead_playthrough, "
    "spa_nav, dark_mode, a11y specs (10 tests total) against the real "
    "built extension and a real running backend",
]

MANUAL_CHECKLIST = [
    "[ ] Load the built extension unpacked against a REAL github.com PR "
    "(not a saved fixture) and confirm selectors.js's candidates still "
    "match today's live DOM -- fixtures are hand-written snapshots and "
    "can drift from what GitHub actually ships.",
    "[ ] Confirm the selector-miss telemetry (POST /telemetry/"
    "selector-miss) actually fires and is visible in the events "
    "collection when a real selector genuinely fails to match, not just "
    "in the unit tests' synthetic misses.",
    "[ ] Manually verify the panel's 960x680 fixed size is usable on a "
    "genuinely small laptop screen (max-width/max-height are percentage-"
    "based fallbacks, but never tested against a real small viewport).",
    "[ ] Manually confirm a keyboard-only user (no mouse at all) can "
    "complete an entire Defense/Attack turn inside Monaco, not just "
    "reach and open the panel (the a11y smoke test only covers the "
    "gate icon itself).",
]

OPEN_QUESTIONS = [
    "The phase brief's example narrative includes a 'fail -> scaffold' "
    "path; this demo's automated playthrough deliberately submits "
    "CORRECT fixes throughout (a 'clean sweep', per Phase 5's own "
    "playthrough naming), so it never reaches ROUND2_SCAFFOLD and there "
    "is no scaffold screenshot here. The scaffold UI itself is built and "
    "unit-covered (ScaffoldView, data-testid=\"dev-scaffold-view\"), and "
    "the backend transition was verified directly against the real "
    "server during debugging (a session that skips every Attack turn "
    "scores 50% and DOES fall through to ROUND2_SCAFFOLD) -- but "
    "capturing that specific path as a screenshot walkthrough would need "
    "a second, deliberately-wrong-answer scripted playthrough, which "
    "wasn't built this phase. Worth adding if the study protocol wants "
    "that path demoed visually.",
    "Found and fixed two real UI bugs only by running full playthroughs "
    "for real, not by inspection: (1) useDevGate.js's and useLeadGate.js's "
    "advance() could commit a transient render with the NEW state but the "
    "OLD task/question/result, because refreshState()'s setState() can "
    "flush a render before the subsequent refreshTask()/refreshQuestion() "
    "call clears the stale data -- fixed by setting the loading flag "
    "BEFORE calling refreshState(), not after. (2) The Create/Merge "
    "button was never being told when the gate reached CLEARED, so it "
    "never re-enabled -- fixed with a new notify_parent.js postMessage "
    "the iframe sends and pr_page.js's handleGateClearedMessage() "
    "listens for. Both are the kind of bug that unit tests around each "
    "hook in isolation would not have caught.",
    "Playwright typing into Monaco is NOT reliable via keystroke "
    "simulation (pressSequentially or even page.keyboard.insertText()): "
    "Monaco's auto-indent triggers per embedded newline and COMPOUNDS "
    "line over line, silently corrupting otherwise-correct multi-line "
    "code. Confirmed directly by logging actual rendered content next to "
    "the intended fix. Worked around by exposing the live editor instance "
    "on its container (CodeEditor.jsx's `__aicodequestEditor`) so tests "
    "call setValue() directly -- fine for tests, but flags that a REAL "
    "study participant typing multi-line fixes by hand won't hit this "
    "(they're not simulating keystrokes programmatically), so this is a "
    "test-tooling finding, not a product bug.",
    "backend/scripts/e2e_server.py is long-lived across test runs "
    "(playwright.config.js's reuseExistingServer) and its mongomock state "
    "persists for the whole process lifetime; dev_gate/lead_gate "
    "sessions are keyed purely by the caller-supplied prId, and "
    "start()/START are idempotent (resume-if-exists). A hardcoded fixture "
    "PR URL across multiple test runs therefore silently resumes a PRIOR "
    "run's leftover session state instead of starting clean. Worked "
    "around with helpers.js's uniqueComparePrUrl()/"
    "uniquePrConversationUrl() (a fresh branch name / PR number per run) "
    "-- but this is a testing-environment workaround, not a fix to the "
    "underlying assumption; a REAL deployment's Mongo would of course "
    "persist real PRs' sessions on purpose. Flagging in case a future "
    "phase's test infra wants a cleaner per-suite database reset instead.",
    "Selector-miss telemetry (POST /telemetry/selector-miss) requires "
    "the user to be logged in (@jwt_required()) -- an unauthenticated "
    "visitor whose page selectors fail to match gets no server-side "
    "record at all, only the console warning. Not addressed this phase; "
    "flagging since selector drift is exactly the kind of failure you'd "
    "want visibility into even for logged-out browsing.",
    "The panel's Escape-key close only works when the TOP page's "
    "document has keyboard focus -- panel.js's keydown listener is on "
    "the outer doc, not the iframe's, and keyboard events don't bubble "
    "across the frame boundary. A real user who has been interacting "
    "inside the game (typing in Monaco, clicking a button) and then "
    "presses Escape expecting to close the panel will find it doesn't "
    "work unless they click outside the iframe first. The explicit close "
    "button (×) always works regardless of focus. Worth deciding whether "
    "this is acceptable or whether the iframe should forward Escape to "
    "the parent via postMessage.",
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
        # Xvfb) is available in this environment -- use it directly. See
        # reports/phase-8-review.md open questions for why this sandbox
        # needed a conda-installed Xvfb instead of the standard apt
        # package.
        cmd = ["npx", "playwright", "test"]
    else:
        return (
            "**Playwright e2e: SKIPPED**\n\n"
            "No `xvfb-run` on PATH and no `$DISPLAY` set -- Chrome cannot "
            "load an unpacked extension in headless mode (verified "
            "directly, see reports/phase-8-review.md), so this suite "
            "needs a real or virtual display. Install `xvfb` "
            "(`apt install xvfb`) or export `DISPLAY` pointing at a "
            "running X server, then re-run."
        )
    return run(cmd, cwd=EXTENSION_DIR, env=env)


def main() -> None:
    unit_markdown = run_unit_tests()
    build_markdown = build_extension()
    e2e_markdown = run_e2e_tests()

    demo_parts = [
        "### Unit tests (Vitest -- selectors/page_state/pr_page/gate_ui/"
        "panel/api, chrome-independent by design)",
        "",
        unit_markdown,
        "",
        "### Build (`npm run build` -> extension/dist/, 4 targets: "
        "service worker + content script as IIFE, popup + game.html as "
        "ES-module pages)",
        "",
        build_markdown,
        "",
        "### Playwright e2e (real Chromium, real unpacked extension, "
        "real Flask+ScriptedLLM backend -- backend/scripts/e2e_server.py, "
        "saved fixture PR pages -- backend/tests/fixtures/pr_pages/)",
        "",
        e2e_markdown,
        "",
        "### Screenshot walkthrough (narrative order: arrive -> tag -> "
        "gate fires -> play -> verdict -> cleared -> button re-enabled, "
        "for both the Developer and Lead gates)",
        "",
    ]

    for filename, caption in WALKTHROUGH:
        path = ASSETS_DIR / filename
        if path.exists():
            demo_parts += [f"**{caption}**", "", f"![{caption}](assets/{filename})", ""]
        else:
            demo_parts += [f"**{caption}**", "", f"_Screenshot NOT FOUND: expected {path}_", ""]

    demo_parts += [
        "### Manual checklist (things automation in this phase cannot cover)",
        "",
        "\n".join(MANUAL_CHECKLIST),
    ]

    demo_markdown = "\n".join(demo_parts)
    print(demo_markdown)

    tests_markdown = f"{unit_markdown}\n\n{e2e_markdown}"

    report_path = generate_report(
        phase=9,
        title="GitHub PR page integration and gate UI",
        config_version=CONFIG_VERSION,
        summary=(
            "The Chrome extension now does the real job: detect a "
            "GitHub PR page (new-PR/compare, existing-PR conversation, or "
            "neither) across GitHub's client-side SPA navigation, fetch "
            "the diff, require AI-authorship self-tagging before "
            "anything else proceeds, call /gate/evaluate, and either "
            "show a neutral badge or disable the Create/Merge button and "
            "inject a red icon whose click opens a full React+Monaco "
            "game panel in an isolated iframe -- Phase 5's Developer "
            "gate (task/verdict/attack/scaffold/cleared/escalated) and "
            "Phase 7's Lead gate (NFR select/question/result/cleared/"
            "escalated), both wired to the real backend through the "
            "service worker. Every non-negotiable from the phase brief "
            "holds: DOM-disabling a button is documented in plain, "
            "visible UI copy as cosmetic (real enforcement is GitHub's "
            "Checks API, Phase 10), the panel survives same-PR SPA "
            "navigation and closes cleanly on a different PR, and a "
            "selector miss reports to the backend instead of silently "
            "breaking. Two genuine UI bugs (not caught by any unit test) "
            "surfaced only by scripting full playthroughs against a real "
            "running backend: a stale-render race in both gate hooks' "
            "advance() functions, and a missing GATE_CLEARED signal that "
            "left the Create/Merge button disabled forever after "
            "actually clearing the gate. Both are fixed and covered by "
            "the playthrough tests that found them."
        ),
        built=BUILT,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=OPEN_QUESTIONS,
    )
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
