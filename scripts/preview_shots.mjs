// Screenshots every panel/popup screen from the REAL built bundle
// (extension/dist), into reports/assets/.
//
// WHY THIS EXISTS: the Playwright suite is the only thing that renders
// this UI, and it cannot run in this sandbox -- headless Chrome refuses
// to load unpacked MV3 extensions at all (verified in Phase 8), and there
// is no Xvfb. That left the panel's appearance verifiable only by
// reading the source, which is how a missing import once reached a real
// browser as a ReferenceError (see tests/unit/game_root.test.js).
//
// What this does instead: serve dist/ over plain HTTP, open game.html /
// popup.html as ORDINARY PAGES in headless Chromium (which works fine --
// only the extension-loading path is blocked), and stub `chrome.runtime`
// /`chrome.storage` with scripted responses. Real bundle, real React
// tree, real Monaco, real CSS -- everything except the extension host.
// It is NOT a replacement for the e2e suite: nothing here exercises the
// service worker, the content script, or a real backend.
//
//   npm run build && npm run shots
//
// Any console error or page error fails the run, so a broken import can
// never screenshot "successfully".

import { chromium } from "@playwright/test";
import { createServer } from "node:http";
import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(ROOT, "dist");
const OUT = path.join(ROOT, "..", "reports", "assets");
const PREFIX = "phase23-";

const CONTENT_TYPES = {
  ".js": "text/javascript",
  ".css": "text/css",
  ".html": "text/html",
  ".ttf": "font/ttf",
  ".json": "application/json",
  ".svg": "image/svg+xml",
};

const CODE = `def movies_by_genre(genre):
    result = []
    for movie in movies:
        if movie['genre'].lower() == genre.lower():
            result.append(movie)
    return len(result)`;

const DIFF = [
  "diff --git a/app.py b/app.py",
  "--- /dev/null",
  "+++ b/app.py",
  "@@ -0,0 +1,6 @@",
  ...CODE.split("\n").map((line) => `+${line}`),
  "",
].join("\n");

const PATCH = [
  "--- top_rated (perturbed)",
  "+++ top_rated (original)",
  "@@ -1,11 +1,14 @@",
  "-    if movie['rating'] > highest:",
  "+    if movie['rating'] == highest:",
  "         result.append(movie)",
  "     return jsonify(result)",
].join("\n");

const DEFENSE_TASK = {
  attempt_id: "att-1",
  type: "defense",
  round: 1,
  level: "A2",
  language: "python",
  code: CODE,
  test_cases: [
    { inputs: ["drama"], expected_output: 2 },
    { inputs: ["comedy"], expected_output: 1 },
  ],
  function_ref: "movies_by_genre",
  from_pr: true,
  turn_number: 3,
  // Four, matching `developer_gate.round1.questions_per_level: 1` since
  // 2026-09-09. The app reads this from the server and never from a
  // constant, so this is the SCREENSHOT that would otherwise keep claiming
  // eight turns long after the gate stopped having them.
  turns_in_round: 4,
};

const ATTACK_TASK = { ...DEFENSE_TASK, attempt_id: "att-2", type: "attack", level: "A3", turn_number: 3 };

const PROFILE = {
  public_metrics: {
    total_gates_cleared: 5,
    turns_played: 130,
    turns_won: 58,
    current_turn_streak: 2,
    longest_turn_streak: 7,
    tier: "bronze",
    streak: 4,
    bug_detection_accuracy: 0.72,
    graded_defense_turns: 40,
    bug_injection_success_rate: 0.4,
    graded_attack_turns: 30,
    // 0.5*0.72 + 0.5*0.40. Spelled out rather than computed, because this
    // fixture stands in for the SERVER's answer and the panel must render
    // what it is sent -- a fixture that derived it would hide a panel that
    // had started deriving it too.
    combined_score: 0.56,
    typical_completion_ms: 288_000,
    typical_turn_ms: 36_000,
    badges: [
      { id: "first_clear", name: "First Clear", tier: "bronze" },
      { id: "cleared_a2", name: "Cleared at A2", tier: "silver" },
      { id: "no_scaffold", name: "No-Scaffold Clear", tier: "gold" },
      { id: "first_bug", name: "First Bug Caught", tier: "bronze" },
      { id: "bugs_10", name: "Bug Hunter", tier: "bronze" },
    ],
    mcq_reviews_completed: 7,
    // Phase 42: what the bug-catching ladder counts. 13 puts the stub
    // participant two rungs in with the next two visibly in progress,
    // which is the state the Badges screen exists to render.
    bugs_found: 13,
  },
  // Phase 37: every badge that exists, so the Badges screen can show the
  // locked ones. A subset of the real config table -- enough to exercise
  // earned, locked-with-progress and locked-without-progress.
  badge_catalogue: [
    { id: "first_clear", name: "First Clear", tier: "bronze", metric: "total_gates_cleared", op: ">=", value: 1 },
    { id: "cleared_a2", name: "Cleared at A2", tier: "silver", metric: "won_defense_turns_a2", op: ">=", value: 1 },
    { id: "no_scaffold", name: "No-Scaffold Clear", tier: "gold", metric: "no_scaffold_clears", op: ">=", value: 1 },
    { id: "reviewer_5", name: "First Reviews", tier: "bronze", metric: "mcq_reviews_completed", op: ">=", value: 5 },
    { id: "reviewer_10", name: "Steady Reviewer", tier: "silver", metric: "mcq_reviews_completed", op: ">=", value: 10 },
    { id: "reviewer_50", name: "Review Veteran", tier: "gold", metric: "mcq_reviews_completed", op: ">=", value: 50 },
    { id: "mastermind", name: "Mastermind", tier: "gold", metric: "bug_injection_success_rate", op: ">=", value: 0.7 },
    // Phase 42's bug-catching ladder, including the two tiers ABOVE gold
    // -- the shot is the only place their colours are actually checked.
    { id: "first_bug", name: "First Bug Caught", tier: "bronze", metric: "bugs_found", op: ">=", value: 1 },
    { id: "bugs_10", name: "Bug Hunter", tier: "bronze", metric: "bugs_found", op: ">=", value: 10 },
    { id: "bugs_20", name: "Bug Tracker", tier: "silver", metric: "bugs_found", op: ">=", value: 20 },
    { id: "bugs_30", name: "Bug Slayer", tier: "gold", metric: "bugs_found", op: ">=", value: 30 },
    { id: "bugs_40", name: "Skilled Contributor", tier: "platinum", metric: "bugs_found", op: ">=", value: 40 },
    { id: "bugs_50", name: "Code Champion", tier: "legend", metric: "bugs_found", op: ">=", value: 50 },
  ],
  private_metrics: {
    total_gates_cleared: 5,
    rounds_played: 11,
    dev_gates_cleared: 4,
    lead_gates_cleared: 1,
    no_scaffold_clears: 3,
    replay_count: 1,
    scaffold_exposures: 2,
    escalated_count: 0,
    longest_streak: 4,
    won_defense_turns_a1: 6,
    won_defense_turns_a2: 5,
    won_defense_turns_a3: 4,
    won_defense_turns_a4: 3,
  },
  consent: { study_id: "aicodequest-2026", version: "v1" },
};

const player = (id, name, cleared, streak, tier, detection, defenseTurns, injection, attackTurns, avgMs) => ({
  user_id: id,
  display_name: name,
  public_metrics: {
    total_gates_cleared: cleared,
    streak,
    tier,
    bug_detection_accuracy: detection,
    graded_defense_turns: defenseTurns,
    bug_injection_success_rate: injection,
    graded_attack_turns: attackTurns,
    // The board's second ranking key. Computed HERE (unlike the profile
    // fixture above) only because these rows are generated from arguments;
    // it is still a value the fixture supplies, not one the panel derives.
    combined_score:
      detection == null && injection == null
        ? null
        : 0.5 * (detection ?? 0) + 0.5 * (injection ?? 0),
    typical_completion_ms: avgMs,
    typical_turn_ms: avgMs ? avgMs / 8 : null,
  },
});

const leadPlayer = (id, name, reviews, accuracy, answered, nfrs, reviewsDone, answerMs) => ({
  user_id: id,
  display_name: name,
  public_metrics: {
    lead_gates_cleared: reviews,
    lead_answer_accuracy: accuracy,
    lead_questions_answered: answered,
    lead_nfrs_reviewed: nfrs,
    // Replaced lead_verifications_given / lead_items_flagged_invalid on
    // 2026-08-31, when the NFR verification form was removed.
    mcq_reviews_completed: reviewsDone,
    typical_lead_answer_ms: answerMs,
  },
});

// Phase 30: the LEAD board's own numbers -- different people, different
// columns, different ordering from the developer board above.
const LEAD_LEADERBOARD = {
  persona: "lead",
  leaderboard: [
    leadPlayer("l1", "nfr_navigator", 9, 0.88, 41, 5, 33, 38_000),
    leadPlayer("l2", "review_ranger", 7, 0.81, 36, 5, 21, 45_000),
    leadPlayer("l3", "tradeoff_tamer", 6, 0.74, 27, 4, 12, 52_000),
    leadPlayer("me", "Alex", 3, 0.67, 15, 3, 9, 61_000),
  ],
};

const LEADERBOARD = {
  persona: "developer",
  leaderboard: [
    player("u1", "bug_hunter_42", 12, 7, "gold", 0.92, 96, 0.61, 88, 214_000),
    player("u2", "code_warrior", 11, 5, "gold", 0.87, 88, 0.55, 80, 246_000),
    player("u3", "debug_master", 10, 4, "silver", 0.81, 80, 0.48, 72, 268_000),
    player("u4", "logic_finder", 9, 3, "silver", 0.76, 72, 0.42, 64, 301_000),
    player("u5", "syntax_sleuth", 8, 2, "silver", 0.7, 64, 0.35, 56, 355_000),
    player("me", "Alex", 5, 4, "bronze", 0.72, 40, 0.4, 30, 288_000),
  ],
};

const ME = {
  loggedIn: true,
  user: { id: "me", email: "developer@example.com", display_name: "Alex", role_hint: "developer" },
};

const DEV_STUB = {
  GET_DIFF: { ok: true, diff: DIFF },
  DEV_GATE_START: { state: "ROUND1_DEFENSE" },
  DEV_GATE_STATE: {
    state: "ROUND1_DEFENSE",
    scoreboard: { developer: 1, ai: 1, skipped: 0, turns_played: 2 },
    restart_available: true,
  },
  DEV_GATE_CURRENT_TASK: { task: DEFENSE_TASK },
  DEV_GATE_SUBMIT_DEFENSE: {
    attempt_id: "att-1",
    outcome: "lost",
    case_results: [
      { inputs: ["drama"], expected_output: 2, actual: 2, passed: true },
      { inputs: ["comedy"], expected_output: 1, actual: 0, passed: false },
    ],
    bug_reveal: {
      description: "The comparison was changed so only strictly-greater ratings were kept.",
      patch: PATCH,
      fix_explanation:
        "The original code returns every movie with the highest rating. The bug used '> highest', which returns only movies rated above the highest — always empty. '== highest' includes every movie matching it.",
    },
  },
  GET_ME: ME,
  LEADERBOARD_ME: PROFILE,
  LEADERBOARD_PUBLIC: LEADERBOARD,
  "LEADERBOARD_PUBLIC:developer": LEADERBOARD,
  "LEADERBOARD_PUBLIC:lead": LEAD_LEADERBOARD,
};

const ATTACK_STUB = {
  ...DEV_STUB,
  DEV_GATE_START: { state: "ROUND1_ATTACK" },
  DEV_GATE_STATE: {
    state: "ROUND1_ATTACK",
    scoreboard: { developer: 2, ai: 1, skipped: 1, turns_played: 4 },
    restart_available: true,
  },
  DEV_GATE_CURRENT_TASK: { task: ATTACK_TASK },
};

// Four options of similar length -- the shape nfr.py's structural QC
// check enforces, so the screenshot shows what a lead really sees.
const LEAD_OPTIONS = [
  "Nothing: callers already ignored the list and only read the count.",
  "The endpoint gets faster, but the response schema is unchanged.",
  "Callers lose the movies and must make a second request to get them.",
  "The count is wrong for empty genres, but the list still comes back.",
];

const LEAD_STUB = {
  GET_DIFF: { ok: true, diff: DIFF },
  CONSENT_GET: { signed: true },
  LEAD_GATE_NFRS: { default_nfrs: ["reliability", "security", "performance", "maintainability"] },
  LEAD_GATE_STATE: { state: "IN_PROGRESS" },
  LEAD_GATE_CURRENT_QUESTION: {
    state: "IN_PROGRESS",
    question: {
      question_id: "q-1",
      nfr: "reliability",
      text: "movies_by_genre() now returns len(result) rather than the matched list. What does that change for a caller that needs the movies themselves?",
      options: LEAD_OPTIONS,
      progress: { current: 1, total: 3 },
    },
    answered: null,
  },
  // Phase 30: the reveal. The stub answers option A while the key is C,
  // so the shot shows both the wrong pick and the recorded answer rather
  // than the easier all-green case.
  LEAD_GATE_ANSWER: {
    question_id: "q-1",
    nfr: "reliability",
    selected_index: 0,
    correct_index: 2,
    is_correct: false,
    score: 0,
    explanation:
      "Callers lose the movie objects entirely: len(result) discards them, so any caller that rendered titles now has to make a second request.",
    options: LEAD_OPTIONS,
    verification: null,
    flagged_for_review: false,
  },
  GET_ME: ME,
  LEADERBOARD_ME: PROFILE,
  LEADERBOARD_PUBLIC: LEADERBOARD,
  "LEADERBOARD_PUBLIC:developer": LEADERBOARD,
  "LEADERBOARD_PUBLIC:lead": LEAD_LEADERBOARD,
};

// The AI-authorship tagging panel is a CONTENT-SCRIPT surface: it renders
// into github.com's own page, not into an extension page, so it has no
// built HTML entry to open. It is also a self-contained ES module with no
// imports of its own, which makes it directly importable in a scratch
// page -- so this harness serves src/ alongside dist/ and renders it on a
// stand-in for a pull request page.
const TAGGING_PAGE = `<!doctype html>
<meta charset="utf-8">
<title>tagging panel preview</title>
<style>
  body { margin: 0; font: 14px system-ui, sans-serif; background: #0d1117; color: #c9d1d9; }
  .fake-pr { padding: 28px 32px; }
  .fake-pr h1 { font-size: 20px; font-weight: 400; margin: 0 0 6px; }
  .fake-pr p { color: #8b949e; margin: 0 0 20px; }
  .fake-diff { border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
  .fake-diff header { padding: 10px 14px; background: #161b22; border-bottom: 1px solid #30363d; font-size: 13px; }
  .fake-diff pre { margin: 0; padding: 12px 14px; font: 12px ui-monospace, monospace; color: #7ee787; }
</style>
<div class="fake-pr">
  <h1>Add movie search endpoints</h1>
  <p>example-owner wants to merge 1 commit into <code>main</code> from <code>coder</code></p>
  <div class="fake-diff">
    <header>movie_collection_api.py</header>
    <pre>+ def search_movie():
+     keyword = request.args.get("title", "").lower()
+     return jsonify([m for m in movies if keyword in m["title"].lower()])</pre>
  </div>
</div>
<script type="module">
  import { renderTaggingPanel } from "/src/content/tagging_panel.js";
  renderTaggingPanel(document.body, [
    { path: "movie_collection_api.py", hunks: [
      { startLine: 12, endLine: 18, preview: "def search_movie():" },
      { startLine: 31, endLine: 44, preview: "def movies_by_genre(genre):" },
      { startLine: 57, endLine: 63, preview: "def top_rated():" },
    ]},
    { path: "tests/test_api.py", hunks: [
      { startLine: 4, endLine: 9, preview: "def test_search_returns_matches():" },
    ]},
  ]);
</script>`;

async function serveDist() {
  const server = createServer(async (req, res) => {
    const pathname = new URL(req.url, "http://localhost").pathname;
    if (pathname === "/tagging.html") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(TAGGING_PAGE);
      return;
    }
    if (pathname.startsWith("/src/")) {
      try {
        const body = await readFile(path.join(ROOT, pathname));
        res.writeHead(200, { "Content-Type": "text/javascript" });
        res.end(body);
      } catch {
        res.writeHead(404).end("not found");
      }
      return;
    }
    const file = path.join(DIST, pathname === "/" ? "/game.html" : pathname);
    try {
      const body = await readFile(file);
      res.writeHead(200, { "Content-Type": CONTENT_TYPES[path.extname(file)] || "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(404).end("not found");
    }
  });
  await new Promise((resolve) => server.listen(0, resolve));
  return { server, port: server.address().port };
}

const problems = [];

async function newPage(browser, stub, viewport) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 2 });
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(`console.error: ${msg.text()}`);
  });
  await page.addInitScript((stubbed) => {
    window.chrome = {
      runtime: {
        sendMessage: async (message) => {
          // A persona-qualified key wins when present, so the two
          // leaderboards can be stubbed with genuinely different data --
          // otherwise both tabs photograph the same table and the shot
          // proves nothing about the split.
          const result =
            message.persona !== undefined && stubbed[`${message.type}:${message.persona}`] !== undefined
              ? stubbed[`${message.type}:${message.persona}`]
              : stubbed[message.type];
          if (result === undefined) return { ok: false, error: `unstubbed ${message.type}` };
          // "__never__" holds the panel on a loading state so it can be
          // photographed -- the chain-of-thought wait has no other way to
          // be captured, since a stubbed reply arrives instantly.
          if (result === "__never__") return new Promise(() => {});
          return { ok: true, result };
        },
        getURL: (p) => `/${p}`,
      },
      storage: {
        local: { get: async () => ({}), set: async () => {} },
        session: { get: async () => ({}), set: async () => {} },
        onChanged: { addListener() {} },
      },
    };
  }, stub);
  return page;
}

async function shot(page, name) {
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, `${PREFIX}${name}.png`) });
  console.log(`  ${PREFIX}${name}.png`);
}

await mkdir(OUT, { recursive: true });
const { server, port } = await serveDist();
const browser = await chromium.launch();
const panelViewport = { width: 1160, height: 760 };
const gameUrl = (mode) =>
  `http://localhost:${port}/game.html?mode=${mode}&prId=owner__repo%231&diffUrl=http://localhost:${port}/diff`;

console.log("developer gate:");
{
  const page = await newPage(browser, DEV_STUB, panelViewport);
  await page.goto(gameUrl("dev"));
  await page.waitForSelector('[data-testid="dev-level-intro"]', { timeout: 20_000 });
  await shot(page, "01-level-intro");

  await page.getByRole("button", { name: /Start this level/ }).click();
  await page.waitForSelector('[data-testid="dev-defense-view"]');
  await shot(page, "02-defense");

  await page.getByTestId("theme-toggle").click();
  await shot(page, "03-defense-dark");
  await page.getByTestId("theme-toggle").click();

  // FULL SCREEN: the editor has to actually get bigger. Reported plainly --
  // full screen made the window bigger and left the code in the same 280px
  // box at the same 13px type, so the extra space went to whitespace. The
  // panel here is a standalone page with no parent to answer
  // REQUEST_PANEL_SIZE, so the PANEL_SIZE reply is posted directly; that is
  // exactly the message content/panel.js sends.
  await page.evaluate(() =>
    window.postMessage({ source: "aicodequest", type: "PANEL_SIZE", size: "full" }, "*")
  );
  await page.waitForFunction(
    () => document.querySelector('[data-testid="monaco-editor"]')?.dataset.fontSize === "17",
    { timeout: 5_000 }
  );
  await shot(page, "27-defense-full-screen");

  await page.getByTestId("dev-submit-fix").click();
  await page.waitForSelector('[data-testid="dev-bug-reveal"]');
  await page.evaluate(() => document.querySelector(".aq-main").scrollTo(0, 400));
  await shot(page, "04-verdict-and-bug-reveal");
  await page.close();
}

// Phase 42: the in-turn celebration. Its own page because the stub above
// deliberately LOSES the turn (to exercise the bug reveal), and a
// milestone only rides on a WON one.
console.log("milestone celebration:");
{
  const page = await newPage(
    browser,
    {
      ...DEV_STUB,
      DEV_GATE_SUBMIT_DEFENSE: {
        attempt_id: "att-win",
        outcome: "won",
        case_results: [
          { inputs: ["drama"], expected_output: 2, actual: 2, passed: true },
          { inputs: ["comedy"], expected_output: 1, actual: 1, passed: true },
        ],
        milestone: {
          kind: "badge",
          count: 10,
          badge_id: "bugs_10",
          name: "Bug Hunter",
          tier: "bronze",
          confetti: true,
          toast_ms: 2000,
        },
      },
    },
    panelViewport
  );
  await page.goto(gameUrl("dev"));
  await page.waitForSelector('[data-testid="dev-level-intro"]', { timeout: 20_000 });
  await page.getByRole("button", { name: /Start this level/ }).click();
  await page.waitForSelector('[data-testid="dev-defense-view"]');
  await page.getByTestId("dev-submit-fix").click();
  // Caught mid-hold: the toast is on screen for `toast_ms` and then leaves
  // on its own, so the shot has to happen inside that window.
  await page.waitForSelector('[data-testid="badge-toast"]', { timeout: 5_000 });
  await shot(page, "25-milestone-toast");

  // The CONFETTI itself, requested explicitly. It is a canvas the module
  // appends and removes on its own, so the shot has to land while
  // particles are still in flight -- hence the wait on the canvas rather
  // than a fixed sleep, and a short settle so they have spread from the
  // single point they all start at (a shot taken at t=0 is one dot).
  await page.waitForSelector("canvas", { timeout: 5_000 });
  await page.waitForTimeout(280);
  await shot(page, "26-milestone-confetti");

  await page.getByTestId("dev-restart-button").click();
  await page.waitForSelector('[data-testid="dev-restart-confirm"]');
  await page.evaluate(() => document.querySelector(".aq-main").scrollTo(0, 0));
  await shot(page, "05-restart-confirm");
  await page.close();
}

console.log("attack turn:");
{
  const page = await newPage(browser, ATTACK_STUB, panelViewport);
  await page.goto(gameUrl("dev"));
  await page.waitForSelector('[data-testid="dev-level-intro"]', { timeout: 20_000 });
  await page.getByRole("button", { name: /Start this level/ }).click();
  await page.waitForSelector('[data-testid="dev-attack-view"]');
  await shot(page, "06-attack");
  await page.close();
}

console.log("lead gate:");
{
  const page = await newPage(browser, LEAD_STUB, panelViewport);
  await page.goto(gameUrl("lead"));
  await page.waitForSelector('[data-testid="lead-question-view"]', { timeout: 20_000 });
  await shot(page, "07-lead-question");

  // Answering reveals the key. The verification form that followed it was
  // removed on 2026-08-31, so the reveal is now the last screen of a
  // question and this is the shot that would catch it regressing.
  await page.getByTestId("lead-option-0").click();
  await page.getByTestId("lead-submit-answer").click();
  await page.waitForSelector('[data-testid="lead-result-view"]');
  await shot(page, "20-lead-reveal");
  await page.close();
}

console.log("lead chain-of-thought wait:");
{
  // Phase 30: the Lead gate's wait now renders the SAME chain-of-thought
  // component the Developer gate does. LEAD_GATE_CURRENT_QUESTION is
  // deliberately never answered so the panel stays on it, and
  // LEAD_GATE_PROGRESS reports a step the way generation really would.
  const page = await newPage(
    browser,
    {
      ...LEAD_STUB,
      LEAD_GATE_CURRENT_QUESTION: "__never__",
      LEAD_GATE_PROGRESS: {
        step: "check_answer",
        detail: "reliability",
        index: 2,
        total: 3,
        steps: [
          { id: "read_diff", label: "Reading the pull request" },
          { id: "write_question", label: "Writing a question about this change" },
          { id: "check_grounded", label: "Checking the question is about your diff" },
          { id: "check_answer", label: "Checking the answer holds up" },
          { id: "ready", label: "Your question" },
        ],
      },
    },
    panelViewport
  );
  await page.goto(gameUrl("lead"));
  await page.waitForSelector('[data-testid="lead-loading-cot"]', { timeout: 20_000 });
  await shot(page, "23-lead-cot-wait");
  await page.close();
}

console.log("navigation screens:");
{
  const page = await newPage(browser, DEV_STUB, panelViewport);
  await page.goto(gameUrl("dev"));
  await page.waitForSelector(".aq-app", { timeout: 20_000 });
  await page.getByTestId("brand-home").click();
  await page.waitForSelector('[data-testid="leaderboard-table-developer"]');
  await shot(page, "08-home");
  // Phase 30: the second board. Same screen, different columns -- worth
  // its own shot because "two leaderboards" is the change, and a stub
  // that quietly served the developer list to both tabs would look
  // identical without it.
  await page.getByTestId("board-tab-lead").click();
  await page.waitForSelector('[data-testid="leaderboard-table-lead"]');
  await shot(page, "22-home-lead-board");
  await page.getByTestId("nav-progress").click();
  await shot(page, "09-your-progress");
  // Phase 37: the Badges screen. Worth its own shot because the whole
  // point of it is the LOCKED cards and their progress -- a grid of only
  // what you already have would look fine here and be the wrong screen.
  await page.getByTestId("nav-badges").click();
  await page.waitForSelector('[data-testid="badges-view"]');
  await shot(page, "24-badges");
  await page.getByTestId("nav-how").click();
  await shot(page, "10-how-it-works");
  await page.getByTestId("nav-faq").click();
  await shot(page, "11-faq");
  await page.getByTestId("nav-settings").click();
  await shot(page, "12-settings");
  await page.close();
}

console.log("chain-of-thought wait:");
{
  // Phase 29: the waiting screen mid-pipeline. DEV_GATE_CURRENT_TASK is
  // deliberately never answered so the panel stays on the wait, and
  // DEV_GATE_PROGRESS reports a step the way generation really would.
  const page = await newPage(
    browser,
    {
      ...DEV_STUB,
      DEV_GATE_CURRENT_TASK: "__never__",
      DEV_GATE_PROGRESS: {
        step: "generate_tests",
        detail: "movies_by_genre",
        level: "A2",
        steps: [
          { id: "select_function", label: "Reading your pull request" },
          { id: "inject_bug", label: "Hiding a bug in your code" },
          { id: "generate_tests", label: "Working out what the correct answer is" },
          { id: "verify_bug", label: "Making sure the bug is actually catchable" },
          { id: "ready", label: "Your turn" },
        ],
      },
    },
    { width: 1100, height: 720 },
  );
  await page.goto(`http://localhost:${port}/game.html?mode=dev&prId=demo%231&diffUrl=x`);
  await page.waitForSelector('[data-testid="dev-loading-cot"]', { timeout: 20_000 });
  await page.waitForTimeout(900);
  await shot(page, "19-cot-wait");
  await page.close();
}

console.log("replay -> home:");
{
  const stub = {
    ...DEV_STUB,
    DEV_GATE_START: { state: "REPLAY" },
    DEV_GATE_STATE: { state: "REPLAY", scoreboard: { developer: 2, ai: 6, skipped: 0, turns_played: 8 }, restart_available: true },
  };
  const page = await newPage(browser, stub, panelViewport);
  await page.goto(gameUrl("dev"));
  await page.waitForSelector('[data-testid="home-notice"]', { timeout: 20_000 });
  await shot(page, "16-replay-on-home");
  await page.close();
}

console.log("tagging panel:");
{
  const page = await newPage(browser, DEV_STUB, { width: 1100, height: 720 });
  await page.goto(`http://localhost:${port}/tagging.html`);
  await page.waitForSelector(".aicodequest-tagging-panel");
  await shot(page, "14-tagging-panel");

  // The completed state: the counter and the enabled Continue are the two
  // things a participant reads here. Phase 29 removed the "All mine" /
  // "All AI" bulk buttons this used to click, so it now answers each hunk
  // the way a participant has to.
  const radios = await page.$$('.aicodequest-tagging-panel input[type="radio"][value="human"]');
  for (const radio of radios) await radio.click();
  await shot(page, "15-tagging-panel-complete");
  await page.close();
}

console.log("popup:");
{
  const page = await newPage(browser, { ...DEV_STUB, PING: { reachable: true, status: 401 } }, { width: 400, height: 900 });
  await page.goto(`http://localhost:${port}/popup.html`);
  // Phase 37: the popup no longer carries a profile card -- the panel's
  // Home and Badges screens show those numbers with room to show them
  // properly, and two surfaces rendering one set of metrics is two places
  // to keep correct. What is left is what the popup is uniquely for:
  // identity, consent, and the two exports.
  await page.waitForSelector("#consent-card", { timeout: 20_000 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${PREFIX}13-popup-profile.png`), fullPage: true });
  console.log(`  ${PREFIX}13-popup-profile.png`);
  await page.close();
}

// Logged OUT: the sign-in / create-account card. Until Phase 25 there was
// no way to create an account from the extension at all.
{
  const page = await newPage(
    browser,
    { PING: { reachable: true, status: 401 }, GET_ME: { loggedIn: false } },
    { width: 400, height: 640 }
  );
  await page.goto(`http://localhost:${port}/popup.html`);
  await page.waitForSelector("#auth-card", { timeout: 20_000 });
  await page.click("#tab-register");
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, `${PREFIX}17-popup-register.png`), fullPage: true });
  console.log(`  ${PREFIX}17-popup-register.png`);
  await page.close();
}

// Signed in but WITHOUT a consent record: the one-click consent button.
{
  const unconsented = {
    ...DEV_STUB,
    PING: { reachable: true, status: 401 },
    LEADERBOARD_ME: { ...PROFILE, consent: null },
  };
  const page = await newPage(browser, unconsented, { width: 400, height: 900 });
  await page.goto(`http://localhost:${port}/popup.html`);
  await page.waitForSelector("#consent-button", { timeout: 20_000 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${PREFIX}18-popup-consent.png`), fullPage: true });
  console.log(`  ${PREFIX}18-popup-consent.png`);
  await page.close();
}

await browser.close();
server.close();

if (problems.length) {
  console.error("\nBrowser reported errors while rendering:");
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
console.log(`\nAll screens rendered with no console/page errors -> ${OUT}`);
