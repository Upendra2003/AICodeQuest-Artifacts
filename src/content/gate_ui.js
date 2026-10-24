// Renders the two possible outcomes of /gate/evaluate into the page:
// a neutral "not required" badge, or the disabled-button + red-icon
// "gate required" treatment. Vanilla DOM, same reasoning as
// tagging_panel.js -- this is page-embedded UI in github.com's isolated
// world, not the iframe's React app.
//
// NON-NEGOTIABLE (per the phase brief): disabling the Create/Merge
// button via the DOM is COSMETIC. GitHub's own JS can still submit that
// form, a keyboard shortcut can still trigger it, and nothing here stops
// a direct API call. This file's copy says so PLAINLY (not just in a
// hover tooltip) -- real enforcement is the GitHub Checks API (Phase
// 10). See docs/CHROME-EXTENSION-PRIMER.md if that gets expanded there.

import { findGateTarget, SELECTORS, authorMismatch } from "./selectors.js";

export const ICON_ID = "aicodequest-gate-icon";
export const BADGE_ID = "aicodequest-gate-badge";
export const NOTICE_ID = "aicodequest-gate-notice";
export const STYLE_ID = "aicodequest-gate-style";
export const FLOATING_CONTAINER_ID = "aicodequest-floating-container";
export const MISMATCH_ID = "aicodequest-author-mismatch";

// Shown instead of the enforcement disclaimer when the pull request
// already exists: nothing on THIS page is being blocked, so claiming a
// cosmetic block would be false.
const PR_ALREADY_EXISTS_NOTICE =
  "This pull request already exists. Open the comprehension check here, " +
  "or from the pull request page before merging.";

const ENFORCEMENT_DISCLAIMER =
  "This is a prompt, not a lock: disabling this button is cosmetic. " +
  "Real merge protection is enforced separately by GitHub's Checks API.";

// A real github.com PR page is thousands of lines of markup with a large
// footer -- verified directly against a live PR -- so anything appended
// as the literal last child of <body> (the previous behavior here) lands
// far below the fold and is functionally invisible without scrolling past
// everything else on the page. Every element with no real anchor to
// insert next to (the badge, and the icon/notice fallback when the
// target button can't be found) goes into this single fixed-position
// container instead, so it's visible regardless of page length or where
// in the DOM it's attached.
function floatingContainer(doc) {
  let el = doc.getElementById(FLOATING_CONTAINER_ID);
  if (!el) {
    el = doc.createElement("div");
    el.id = FLOATING_CONTAINER_ID;
    el.style.cssText =
      "position:fixed;bottom:16px;right:16px;z-index:2147483647;" +
      "display:flex;flex-direction:column;align-items:flex-end;gap:4px;max-width:320px;";
    doc.body.appendChild(el);
  }
  return el;
}

// GitHub's real merge control loads asynchronously (a "mergebox" partial
// that can still be showing a loading skeleton when /gate/evaluate
// returns) -- verified directly against a live PR page. A single
// findElement() attempt at render time can genuinely miss a button that
// hasn't rendered yet, independent of whether the selector itself is
// correct. This does one bounded, silent retry pass rather than a second
// selectors.js "miss" report per attempt.
const LATE_BUTTON_WATCH_MS = 8000;
let activeLateButtonObserver = null;

function quietFind(doc, key) {
  for (const selector of SELECTORS[key] || []) {
    try {
      const el = doc.querySelector(selector);
      if (el) return el;
    } catch {
      // an invalid selector string should never crash the page
    }
  }
  return null;
}

function stopWatchingForLateButton() {
  if (activeLateButtonObserver) {
    activeLateButtonObserver.disconnect();
    activeLateButtonObserver = null;
  }
}

function watchForLateButton(doc, targetButtonKey) {
  if (typeof MutationObserver === "undefined") return;
  stopWatchingForLateButton();
  const observer = new MutationObserver(() => {
    const button = quietFind(doc, targetButtonKey);
    if (button && !button.hasAttribute("data-aicodequest-disabled")) {
      button.disabled = true;
      button.setAttribute("data-aicodequest-disabled", "true");
      stopWatchingForLateButton();
    }
  });
  observer.observe(doc.body, { childList: true, subtree: true });
  activeLateButtonObserver = observer;
  setTimeout(() => {
    if (activeLateButtonObserver === observer) stopWatchingForLateButton();
  }, LATE_BUTTON_WATCH_MS);
}

function ensureStyle(doc) {
  if (doc.getElementById(STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    #${ICON_ID} {
      display: inline-flex; align-items: center; justify-content: center;
      width: 30px; height: 30px; margin-left: 8px; border-radius: 50%;
      border: none; background: #a84b2f; color: #fff;
      cursor: pointer; line-height: 1; padding: 0;
      box-shadow: 0 0 0 0 rgba(168, 75, 47, 0.55);
      animation: aicodequest-gate-pulse 2.4s ease-out infinite;
    }
    #${ICON_ID}:hover { background: #8d3d26; }
    #${ICON_ID} svg { width: 16px; height: 16px; display: block; }
    #${ICON_ID}:focus-visible {
      outline: 3px solid #1fb8cd; outline-offset: 2px;
    }
    @keyframes aicodequest-gate-pulse {
      0% { box-shadow: 0 0 0 0 rgba(168, 75, 47, 0.5); }
      70% { box-shadow: 0 0 0 9px rgba(168, 75, 47, 0); }
      100% { box-shadow: 0 0 0 0 rgba(168, 75, 47, 0); }
    }
    @media (prefers-reduced-motion: reduce) {
      #${ICON_ID} { animation: none; }
    }
    #${BADGE_ID} {
      display: inline-block; padding: 2px 10px; border-radius: 12px;
      background: #ecece4; color: #5c7379; font: 12px "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
      margin: 6px 0;
    }
    #${BADGE_ID}[data-kind="error"] {
      display: block; max-width: 320px; border-radius: 8px;
      background: #fbf1dd; color: #8a6320; border: 1px solid #e6cf9f;
      padding: 8px 10px; line-height: 1.45;
    }
    #${MISMATCH_ID} {
      font: 12px "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
      color: #8a6320; background: #fbf1dd; border: 1px solid #e6cf9f;
      border-radius: 6px; padding: 6px 10px; margin: 4px 0; max-width: 480px;
    }
    #${NOTICE_ID} {
      font: 12px "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif; color: #5c7379; margin: 4px 0;
      max-width: 480px;
    }
  `;
  doc.head.appendChild(style);
}

/** Removes any gate UI this module previously injected -- called before
 * re-rendering (a fresh /gate/evaluate result, or an SPA navigation away
 * from a gated page) so re-evaluation never leaves stale/duplicate
 * elements behind. */
export function removeGateUi(doc) {
  stopWatchingForLateButton();
  doc.getElementById(ICON_ID)?.remove();
  doc.getElementById(BADGE_ID)?.remove();
  doc.getElementById(NOTICE_ID)?.remove();
  doc.getElementById(MISMATCH_ID)?.remove();
  doc.getElementById(FLOATING_CONTAINER_ID)?.remove();
  const button = doc.querySelector("[data-aicodequest-disabled]");
  if (button) {
    button.disabled = false;
    button.removeAttribute("data-aicodequest-disabled");
    button.removeAttribute("data-aicodequest-provisional");
    button.removeAttribute("title");
  }
}

/**
 * Blocks the target button IMMEDIATELY, before we know whether the gate
 * will fire (Phase 29).
 *
 * Asked for directly: *"when we load the page, block the buttons
 * immediately, don't wait for the user to click on AI or human and then
 * disable -- that way we prevent the developers from raising a PR"*.
 *
 * The old order was: fetch the diff, ask who wrote what, call
 * /gate/evaluate, and only THEN disable. Every one of those steps is a
 * network round trip or a human decision, and the button was live
 * throughout -- so the fastest way past the gate was to ignore the panel
 * and click Create. Blocking first inverts that: the safe state is the
 * default, and the button is released only once we know the gate does not
 * apply.
 *
 * Deliberately quiet: no icon, no disclaimer, just the button disabled
 * with a note on hover. The participant has not been asked anything yet,
 * so there is nothing to explain -- and if evaluation says the gate does
 * not fire, releaseProvisionalBlock() puts it back with no UI having
 * flickered.
 */
export function applyProvisionalBlock(doc, targetButtonKey) {
  const { element: button, mode } = findGateTarget(doc, targetButtonKey);
  // mode "anchor" means the control is only a place to hang the icon (a
  // compare page whose PR already exists shows "View pull request"), so
  // there is nothing to block and blocking it would be wrong.
  if (!button || mode !== "block") {
    // The button often mounts after we run; the existing late-button
    // observer already handles that case and disables it on arrival.
    watchForLateButton(doc, targetButtonKey);
    return false;
  }
  if (!button.hasAttribute("data-aicodequest-disabled")) {
    button.disabled = true;
    button.setAttribute("data-aicodequest-disabled", "true");
    button.setAttribute("data-aicodequest-provisional", "true");
    button.title = "AICodeQuest is checking whether this change needs a comprehension gate.";
  }
  return true;
}

/**
 * Releases a provisional block -- used when evaluation comes back saying
 * the gate does NOT fire. Only touches buttons this module blocked
 * provisionally, so it can never re-enable one a fired gate is holding.
 */
export function releaseProvisionalBlock(doc) {
  stopWatchingForLateButton();
  const button = doc.querySelector("[data-aicodequest-provisional]");
  if (!button) return;
  button.disabled = false;
  button.removeAttribute("data-aicodequest-disabled");
  button.removeAttribute("data-aicodequest-provisional");
  button.removeAttribute("title");
}

/**
 * The gate could not be evaluated at all -- a badge that SAYS SO.
 *
 * Phase 36: reported as "I get org_consent_missing even though I gave
 * explicit consent after creating the account". Two separate consents
 * exist and only one of them is the participant's:
 *
 *   PARTICIPANT consent -- signed in the popup, per person. Covers what we
 *                          may keep about them.
 *   ORG consent         -- per repository OWNER, asserted by a researcher.
 *                          Covers whether this study may run on that
 *                          organisation's code at all.
 *
 * Signing the first can never satisfy the second, so a participant who
 * hits this cannot fix it by consenting again -- and until now the
 * extension told them nothing at all, failing silently to the console.
 * That silence is what turned a one-line setup step into a mystery.
 */
export function renderErrorBadge(doc, { title, detail }) {
  removeGateUi(doc);
  ensureStyle(doc);
  const badge = doc.createElement("div");
  badge.id = BADGE_ID;
  badge.dataset.kind = "error";
  const strong = doc.createElement("strong");
  strong.textContent = title;
  const rest = doc.createElement("span");
  rest.textContent = ` ${detail}`;
  badge.append(strong, rest);
  floatingContainer(doc).appendChild(badge);
}

/** fires === false: a small neutral badge, nothing else. */
export function renderNeutralBadge(doc, reason) {
  removeGateUi(doc);
  ensureStyle(doc);
  const badge = doc.createElement("div");
  badge.id = BADGE_ID;
  badge.textContent = `comprehension gate: not required — ${reason}`;
  floatingContainer(doc).appendChild(badge);
}

/**
 * fires === true: disables `targetButtonKey`'s button (a selectors.js
 * key -- "createPrButton" or "mergeButton"), inserts the red icon beside
 * it (or, if the button couldn't be found -- already reported as a
 * selector miss by findElement -- appends the icon to the page so the
 * developer isn't left with zero signal), and a plain-text disclaimer
 * that this is cosmetic.
 *
 * @param {(event: MouseEvent) => void} onIconClick
 */
export function renderFiredGateUi(doc, { targetButtonKey, reason, onIconClick }) {
  removeGateUi(doc);
  ensureStyle(doc);

  // Phase 27: `mode` distinguishes "disable this" from "just put the icon
  // next to this". A compare page whose pull request ALREADY exists shows
  // "View pull request" instead of "Create pull request" -- there is
  // nothing to block there (the PR exists; the merge button on the PR page
  // is where enforcement belongs), but the participant still needs a way
  // into the panel. Before this, that page produced a bogus "GitHub may
  // have shipped a DOM change" report and an icon in the screen corner.
  const { element: button, mode } = findGateTarget(doc, targetButtonKey);
  if (button && mode === "block") {
    button.disabled = true;
    button.setAttribute("data-aicodequest-disabled", "true");
  }

  const icon = doc.createElement("button");
  icon.id = ICON_ID;
  icon.type = "button";
  // An alert glyph rather than the bare "●" bullet it used to be: the
  // dot said "something is here", not "this needs you". Inline SVG, not
  // react-icons -- this file is vanilla DOM in github.com's isolated
  // world, with no React on the page -- but deliberately the SAME Feather
  // alert-triangle the panel uses (src/game/icons.js), so the icon that
  // summons the panel and the icons inside it are one vocabulary. Static
  // markup, no interpolation: nothing here is caller-supplied.
  icon.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' +
    '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>' +
    '<line x1="12" y1="9" x2="12" y2="13"></line>' +
    '<line x1="12" y1="17" x2="12.01" y2="17"></line></svg>';
  icon.setAttribute("aria-label", `AICodeQuest comprehension gate required: ${reason}. ${ENFORCEMENT_DISCLAIMER}`);
  icon.title = `Comprehension gate required: ${reason}`;
  icon.addEventListener("click", onIconClick);

  const notice = doc.createElement("div");
  notice.id = NOTICE_ID;
  notice.textContent =
    mode === "anchor-only" ? PR_ALREADY_EXISTS_NOTICE : ENFORCEMENT_DISCLAIMER;

  // Phase 29: whoever is signed in to GitHub is not who opened this pull
  // request. On a shared machine that means participant B is about to play
  // participant A's gate, and every turn would be recorded against B while
  // the code is A's -- wrong in a way nothing on screen would show.
  //
  // A warning, never a block: this is a scraped username, and locking
  // someone out of their own gate on the strength of that is a worse
  // failure than the one it prevents. Both reads return null when unsure,
  // and null means say nothing.
  const mismatch = authorMismatch(doc);
  let warning = null;
  if (mismatch) {
    warning = doc.createElement("div");
    warning.id = MISMATCH_ID;
    warning.textContent =
      `Heads up: this pull request was opened by @${mismatch.author}, ` +
      `but you are signed in to GitHub as @${mismatch.viewer}. ` +
      "Your answers are recorded against YOUR account.";
  }

  if (button && button.parentNode) {
    button.insertAdjacentElement("afterend", icon);
    icon.insertAdjacentElement("afterend", notice);
    if (warning) notice.insertAdjacentElement("afterend", warning);
  } else {
    const floating = floatingContainer(doc);
    floating.appendChild(icon);
    floating.appendChild(notice);
    if (warning) floating.appendChild(warning);
    // The real button may not exist yet (an async-loaded merge control
    // still mid-render) rather than the selector simply being wrong --
    // keep trying quietly for a while so it still gets disabled once it
    // appears, without leaving the developer with zero signal meanwhile.
    watchForLateButton(doc, targetButtonKey);
  }
}
