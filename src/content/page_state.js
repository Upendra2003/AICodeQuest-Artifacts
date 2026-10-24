// Detects GitHub's client-side (Turbo/pjax) navigation, which changes
// `location.href` WITHOUT a full page reload or any single reliable
// event we can depend on.
//
// Why not just monkey-patch history.pushState/replaceState (the usual
// non-extension trick)? A content script's isolated world (see
// docs/CHROME-EXTENSION-PRIMER.md section 1.2) gets its own wrapper
// around several built-ins specifically to prevent cross-world
// interference -- patching `history.pushState` from here does NOT
// reliably intercept the PAGE's own calls to it. Verified against
// Refined GitHub and similar extensions' own documented approach: the
// robust technique is a MutationObserver (GitHub's navigation always
// swaps DOM content) plus `popstate` (covers back/forward), both
// DEBOUNCED, then just re-read `location.href` and see if it changed.

import { parseLocation } from "./selectors.js";

const DEBOUNCE_MS = 200;

/**
 * @param {(current: ReturnType<typeof parseLocation>, previous: ReturnType<typeof parseLocation> | null) => void} onContextChange
 *   Called whenever the URL's resolved context changes -- NOT once per
 *   mutation (many mutations can fire for one navigation; this is what
 *   the debounce collapses). May be called many times over a tab's
 *   lifetime; callers must not assume a single call.
 * @returns {() => void} stop() -- disconnects everything (tests/cleanup).
 */
export function watchPageState(onContextChange, { doc = document, win = window } = {}) {
  let lastHref = null;
  let debounceTimer = null;

  function checkForNavigation() {
    if (win.location.href === lastHref) return;
    const previous = lastHref !== null ? parseLocation(lastHref) : null;
    lastHref = win.location.href;
    const current = parseLocation(lastHref);
    onContextChange(current, previous);
  }

  function scheduleCheck() {
    if (debounceTimer !== null) win.clearTimeout(debounceTimer);
    debounceTimer = win.setTimeout(checkForNavigation, DEBOUNCE_MS);
  }

  const observer = new win.MutationObserver(scheduleCheck);
  observer.observe(doc.documentElement, { childList: true, subtree: true });
  win.addEventListener("popstate", scheduleCheck);

  // The very first evaluation must not wait for a mutation to happen to
  // occur -- fire once immediately for the page as initially loaded.
  checkForNavigation();

  return function stop() {
    observer.disconnect();
    win.removeEventListener("popstate", scheduleCheck);
    if (debounceTimer !== null) win.clearTimeout(debounceTimer);
  };
}
