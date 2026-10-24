// The ONE place GitHub DOM knowledge lives. Every other content-script
// module (page_state.js, tagging_panel.js, gate_ui.js) asks THIS module
// for elements/URL context instead of writing its own querySelector --
// a GitHub redesign means editing candidates here, nowhere else.
//
// Each selector key maps to an ORDERED LIST of candidate CSS selectors
// (most-specific/most-likely first) tried in turn, so a single markup
// change doesn't necessarily break matching -- this is a resilience
// heuristic, not a guarantee.
//
// Deliberately free of chrome.* -- fully unit-testable against a jsdom
// document with zero mocking (see tests/unit/selectors.test.js, run
// against real saved fixture pages in
// backend/tests/fixtures/pr_pages/). The one side effect this module
// has (console.warn on a miss) is safe in any environment; REPORTING a
// miss to the backend is a separate, injectable hook (see
// setSelectorMissReporter) so this file has no direct chrome.runtime
// dependency either.

// TEXT FALLBACK (Phase 27). Every CSS candidate below is a bet on
// GitHub's markup, and this project has now lost that bet three times --
// most recently on a live compare page where `createPrButton` matched
// nothing at all, so the Create button was never disabled and the gate
// icon fell back to the corner of the screen.
//
// A button's LABEL is far more stable than its classes: GitHub has
// rewritten this UI into Primer React (hashed CSS-module class names, new
// data-testids, different DOM shape) while the words on the button stayed
// exactly the same. So when every CSS candidate misses, we look for a
// button whose accessible name matches.
//
// Deliberately anchored and narrow -- "Create pull request", not
// /create/i -- because a false positive here disables the WRONG control
// on a participant's page, which is worse than not finding one.
export const BUTTON_TEXT_PATTERNS = {
  createPrButton: /^(create|draft a?)\s*pull request$/i,
  existingPrLink: /^view pull request$/i,
  mergeButton: /^(merge pull request|squash and merge|rebase and merge|confirm merge|confirm squash and merge|confirm rebase and merge)$/i,
};

const CLICKABLE = 'button, [role="button"], input[type="submit"], summary, a[href*="/pull/"]';

/** The text a user would read on a control: its label, or its aria-label,
 *  or (for an <input>) its value. */
export function accessibleName(el) {
  const aria = el.getAttribute?.("aria-label");
  if (aria && aria.trim()) return aria.trim();
  if (el.tagName === "INPUT") return (el.value || "").trim();
  // textContent, not innerText: jsdom does not implement innerText, and
  // the difference (rendered vs. raw text) does not matter for a label.
  return (el.textContent || "").replace(/\s+/g, " ").trim();
}

/**
 * Does this environment report layout at all?
 *
 * In a real browser every laid-out element has a non-zero box, so we can
 * prefer a VISIBLE match -- which matters because GitHub ships a hidden
 * step-2 submit button in the initial DOM (see createPrButton's note).
 * jsdom has no layout engine and reports 0x0 for everything, so ranking by
 * visibility there would reject every candidate. Probing the body tells
 * the two apart without special-casing the test environment.
 */
function hasLayoutEngine(doc) {
  const rect = doc.body?.getBoundingClientRect?.();
  return Boolean(rect && (rect.width > 0 || rect.height > 0));
}

function isVisible(el) {
  const rect = el.getBoundingClientRect?.();
  return Boolean(rect && rect.width > 0 && rect.height > 0);
}

export const SELECTORS = {
  prTitle: ["bdi.js-issue-title", '[data-testid="issue-title"]', "h1.gh-header-title", "h1"],
  createPrButton: [
    // Real live github.com (2026) splits PR creation into two steps, and
    // BOTH buttons already exist in the initial DOM -- verified directly
    // against a live compare page via getBoundingClientRect(): the step-2
    // "real" submit button (matched by the older candidates below, e.g.
    // `button.btn-primary[type="submit"]`) sits inside a not-yet-revealed
    // container and has a 0x0 layout box until the user clicks THIS
    // button to open it. Disabling/anchoring onto the hidden one leaves
    // the developer with no visible signal at all, even though
    // findElement "succeeded" -- so the visible reveal button (type=
    // "button", no data-testid) must be tried FIRST, not last.
    ".js-details-target.btn-primary.btn",
    '[data-testid="create-pull-request-button"]',
    "form#new_pull_request button.btn-primary[type=\"submit\"]",
    "button.btn-primary[type=\"submit\"]",
  ],
  // What GitHub renders in place of "Create pull request" once a pull
  // request already EXISTS for this branch pair. Not a fallback for the
  // create button -- a signal that this compare page has already been
  // turned into a PR, which is a different page state, not a DOM change.
  existingPrLink: [
    'a[href*="/pull/"][class*="Button"]',
    ".gh-header-actions a[href*='/pull/']",
    'a[data-testid="view-pull-request"]',
  ],
  mergeButton: [
    // Real live github.com (2026) renders the merge control as an
    // async-loaded React "mergebox" partial with hashed CSS-module class
    // names (e.g. "MergeStatusButton-module__mergeStatusButton__CAjUA")
    // that are expected to change across GitHub deploys -- verified
    // directly against a live PR page, where it was still showing a
    // loading skeleton ("View status", data-loading="true") well after
    // page load. `data-testid="mergebox-partial"` on the WRAPPER is the
    // one attribute in that markup not tied to a build-specific hash, so
    // scope through it instead of matching hashed classes directly.
    '[data-testid="mergebox-partial"] button',
    '[data-testid="merge-button"]',
    "button.merge-message",
    "button[data-details-container=\".merge-pr\"]",
  ],
  filesChangedContainer: ["#files", '[data-testid="diff-layout"]', ".diff-view", ".js-diff-progressive-container"],
};

let missReporter = null;

/**
 * Registers the (chrome.runtime-dependent) function called whenever a
 * selector fails to match every candidate. Kept separate from this
 * module's own logic so tests never need a chrome mock just to exercise
 * selector matching. `reporter(key, details)` may return a Promise; it is
 * never awaited here (a slow/failed report must never block page
 * rendering).
 */
export function setSelectorMissReporter(reporter) {
  missReporter = reporter;
}

/**
 * The labels of the clickable controls actually present, so a miss report
 * says what the page DOES have instead of only what it lacks. Without
 * this, diagnosing a GitHub redesign means asking the participant to open
 * devtools and read markup back.
 *
 * Deliberately narrow: button LABELS only (never markup, attributes, or
 * page text), capped, truncated, de-duplicated. These are GitHub's own
 * chrome -- "Create pull request", "Close pull request" -- not
 * participant code, which app/services/redaction.py governs separately.
 */
export function visibleButtonLabels(doc, limit = 12) {
  const labels = [];
  for (const el of doc.querySelectorAll(CLICKABLE)) {
    const name = accessibleName(el).slice(0, 40);
    if (name && !labels.includes(name)) labels.push(name);
    if (labels.length >= limit) break;
  }
  return labels;
}

function reportMiss(key, doc) {
  const candidates = SELECTORS[key] || [];
  const present = visibleButtonLabels(doc);
  // eslint-disable-next-line no-console
  console.warn(
    `[AICodeQuest] selector miss: "${key}" matched none of ${JSON.stringify(candidates)}, ` +
      `and nothing was labelled like ${BUTTON_TEXT_PATTERNS[key]} either. ` +
      `Controls present: ${JSON.stringify(present)}. ` +
      "GitHub may have shipped a DOM change. Reporting to the backend."
  );
  if (missReporter) {
    try {
      missReporter(key, {
        page_url: doc.location ? doc.location.href : "",
        candidates_tried: candidates,
        text_pattern_tried: String(BUTTON_TEXT_PATTERNS[key] || ""),
        controls_present: present,
      });
    } catch (err) {
      // A failed report must never throw into the caller's rendering path.
      // eslint-disable-next-line no-console
      console.warn("[AICodeQuest] selector-miss reporter itself threw:", err);
    }
  }
}

/** Every element matching `key`'s CSS candidates, in candidate order. */
function cssMatches(doc, key) {
  const found = [];
  for (const selector of SELECTORS[key] || []) {
    let elements;
    try {
      elements = doc.querySelectorAll(selector);
    } catch {
      continue; // an invalid selector string should never crash the page
    }
    for (const el of elements) {
      if (!found.includes(el)) found.push(el);
    }
  }
  return found;
}

/** Every clickable whose accessible name matches `key`'s text pattern. */
export function textMatches(doc, key) {
  const pattern = BUTTON_TEXT_PATTERNS[key];
  if (!pattern) return [];
  const found = [];
  for (const el of doc.querySelectorAll(CLICKABLE)) {
    if (pattern.test(accessibleName(el))) found.push(el);
  }
  return found;
}

/**
 * Finds the best matching element for `key` in `doc`. Returns null (never
 * throws) and reports the miss if nothing matches at all.
 *
 * Resolution order, and why:
 *   1. CSS candidates, in order -- fast and precise WHEN they hold.
 *   2. Accessible name -- survives the class/testid churn that has broken
 *      (1) repeatedly. See BUTTON_TEXT_PATTERNS.
 * Within that, a VISIBLE match wins over an invisible one, because
 * GitHub ships a hidden step-2 submit button in the initial DOM and
 * anchoring the gate icon onto a 0x0 element leaves the participant with
 * no visible signal even though this function "succeeded".
 */
/**
 * Resolves where the gate UI should attach, and what it may do there.
 *
 *   { element, mode: "block" }       the real action button -- disable it
 *   { element, mode: "anchor-only" } a pull request ALREADY exists for
 *                                    this compare, so GitHub shows "View
 *                                    pull request" instead of "Create".
 *                                    There is nothing to block here (the
 *                                    PR exists; enforcement now belongs to
 *                                    the merge button on the PR page), but
 *                                    the participant still needs a way in
 *                                    to the panel.
 *   { element: null, mode: "none" }  genuinely nothing -- report the miss.
 *
 * This distinction exists because the extension previously reported "GitHub
 * may have shipped a DOM change" when it met an already-created PR --
 * blaming GitHub for a page state it simply had not been taught to
 * recognise, and dropping the icon into the corner of the screen.
 */
export function findGateTarget(doc, key) {
  const direct = findElementQuiet(doc, key);
  if (direct) return { element: direct, mode: "block" };

  if (key === "createPrButton") {
    const existing = findElementQuiet(doc, "existingPrLink");
    if (existing) return { element: existing, mode: "anchor-only" };
  }

  reportMiss(key, doc);
  return { element: null, mode: "none" };
}

/** findElement without the miss report -- for callers that have another
 *  candidate to try before concluding anything is wrong. */
export function findElementQuiet(doc, key) {
  if (!SELECTORS[key]) {
    throw new Error(`unknown selector key: ${JSON.stringify(key)}`);
  }
  const byCss = cssMatches(doc, key);
  const byText = textMatches(doc, key).filter((el) => !byCss.includes(el));
  const ranked = [...byCss, ...byText];
  if (ranked.length === 0) return null;
  if (hasLayoutEngine(doc)) {
    const visible = ranked.find(isVisible);
    if (visible) return visible;
  }
  return ranked[0];
}

export function findElement(doc, key) {
  if (!SELECTORS[key]) {
    throw new Error(`unknown selector key: ${JSON.stringify(key)}`);
  }

  const byCss = cssMatches(doc, key);
  const byText = textMatches(doc, key).filter((el) => !byCss.includes(el));
  const ranked = [...byCss, ...byText];
  if (ranked.length === 0) {
    reportMiss(key, doc);
    return null;
  }

  if (hasLayoutEngine(doc)) {
    const visible = ranked.find(isVisible);
    if (visible) return visible;
  }
  return ranked[0];
}

// --- URL parsing ---
//
// The URL is far more stable than DOM structure for identifying WHICH
// PR/compare we're looking at, so it's the primary source of truth here;
// DOM selectors above are only for elements the URL can't encode
// (buttons, titles).

const PR_URL_RE = /^\/([^/]+)\/([^/]+)\/pull\/(\d+)/;
// The head branch name can itself contain "/" (e.g. "fix/payment-retry"),
// so its capture group must be greedy up to "?" or end-of-path, not "/".
const COMPARE_URL_RE = /^\/([^/]+)\/([^/]+)\/compare\/([^.]+)\.\.\.(?:([^:/]+):)?([^?]+)/;

/**
 * @returns {{context: "pr_conversation"|"new_pr"|"none", owner?, repo?,
 *   pr_number?, base?, head?}}
 */
export function parseLocation(url) {
  let pathname;
  try {
    pathname = new URL(url).pathname;
  } catch {
    return { context: "none" };
  }

  const prMatch = pathname.match(PR_URL_RE);
  if (prMatch) {
    const [, owner, repo, prNumber] = prMatch;
    return { context: "pr_conversation", owner, repo, pr_number: Number(prNumber) };
  }

  const compareMatch = pathname.match(COMPARE_URL_RE);
  if (compareMatch) {
    const [, owner, repo, base, headOwner, head] = compareMatch;
    return {
      context: "new_pr",
      owner,
      repo,
      base,
      head: headOwner ? `${headOwner}:${head}` : head,
    };
  }

  return { context: "none" };
}

/* ---------- who is at the keyboard, and whose pull request is this ----------
 *
 * Phase 29, at the study lead's request: on a shared machine or a shared
 * browser profile, participant B can open participant A's pull request and
 * play A's gate. The turns are then recorded against B's AICodeQuest
 * account while the code is A's -- which corrupts the measure quietly,
 * because nothing about the screen looks wrong.
 *
 * Both reads are BEST-EFFORT and both are allowed to return null. GitHub's
 * markup is not a contract (this project has already been bitten once by
 * the Primer React rewrite breaking every button selector), and a WRONG
 * warning is worse than no warning: it would tell a participant playing
 * their own pull request that they are playing someone else's. Callers
 * must therefore treat null as "unknown, say nothing", never as "mismatch".
 *
 * This warns; it never blocks. Locking someone out of their own gate on
 * the strength of a scraped username is not a trade worth making.
 */

/** The GitHub account signed in to this browser, or null. */
export function signedInLogin(doc) {
  for (const name of ["user-login", "octolytics-actor-login"]) {
    const content = doc.querySelector(`meta[name="${name}"]`)?.getAttribute("content");
    if (content && content.trim()) return content.trim();
  }
  return null;
}

/** The GitHub account that opened this pull request, or null. */
export function prAuthorLogin(doc) {
  // GitHub renders "<author> wants to merge N commits into base from head"
  // on a pull request page; `.author` is the long-standing hook for the
  // name in it, and is what the saved fixture pages in
  // backend/tests/fixtures/pr_pages/ contain.
  for (const selector of ['a.author', '.author', '[data-testid="pr-author"]']) {
    const el = doc.querySelector(selector);
    const text = (el?.textContent || "").trim();
    if (text) return text;
  }
  return null;
}

/**
 * null when the two are the same, or when either is unknown. Otherwise
 * {author, viewer} -- the caller shows a warning, and does not block.
 */
export function authorMismatch(doc) {
  const author = prAuthorLogin(doc);
  const viewer = signedInLogin(doc);
  if (!author || !viewer) return null;
  if (author.toLowerCase() === viewer.toLowerCase()) return null;
  return { author, viewer };
}
