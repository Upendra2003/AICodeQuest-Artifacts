// Makes the extension work after a GitHub client-side navigation
// (Phase 26).
//
// THE PROBLEM, reported as "I am manually refreshing the page to get the
// extension to work": Chrome injects a content script only on a document
// LOAD whose URL already matches `content_scripts.matches`. GitHub is a
// Turbo/pjax app -- clicking through from a repo page to a pull request,
// or from the PR list to a compare page, changes `location.href` with no
// document load at all. The script is therefore never injected, and
// `content/page_state.js` (which handles navigation perfectly WITHIN a
// tab that already has the script) never gets the chance to run. A manual
// refresh is a real document load, which is why that always worked.
//
// THE FIX: watch history-state navigations from the service worker, and
// when one lands on a gate-relevant URL in a tab with no content script,
// reload that tab once.
//
// Two properties this must have, both of which are the whole difficulty:
//
//   1. NEVER loop. `onHistoryStateUpdated` does not fire for a full
//      document load, so the reload itself cannot re-trigger this. The
//      per-tab guard below is belt-and-braces against a page that
//      pushState()s repeatedly on the same URL.
//   2. NEVER reload a page that is already working. A tab that HAS the
//      content script needs no reload -- page_state.js is already
//      watching, and reloading would throw away whatever the participant
//      had typed (a PR title, a tagging panel half filled in). So we ping
//      first and only reload on silence.

export const PR_URL_PATTERN = /^https:\/\/github\.com\/[^/]+\/[^/]+\/(pull|compare)\//;

// How long a reload decision for a given tab+URL is remembered. Long
// enough that a burst of pushState() calls during one navigation cannot
// reload twice; short enough that a genuine later navigation back to the
// same URL still works.
const REPEAT_GUARD_MS = 10_000;

/**
 * @param {object} deps injectable for tests -- the real chrome.* APIs are
 *   passed in by the caller (service_worker.js) rather than referenced
 *   directly, so this module is testable without a chrome mock.
 */
export function createSpaNavigationHandler({ sendMessageToTab, reloadTab, now = () => Date.now() }) {
  // Per-handler, NOT module-level. Module state would be shared by every
  // handler ever created, which is invisible in production (the service
  // worker makes exactly one) and wrong everywhere else -- a test's guard
  // entry leaked into the next test and suppressed a reload that should
  // have happened. Found by that test failing, which is the only reason
  // it was ever going to be found.
  const recentlyReloaded = new Map(); // tabId -> { url, at }

  function shouldSkipAsRepeat(tabId, url, at) {
    const previous = recentlyReloaded.get(tabId);
    return Boolean(previous && previous.url === url && at - previous.at < REPEAT_GUARD_MS);
  }

  async function handleHistoryStateUpdated({ tabId, url, frameId }) {
    // Sub-frames navigate constantly on GitHub (the game panel's own
    // iframe among them) and are never what this is about.
    if (frameId !== 0) return { action: "ignored", reason: "subframe" };
    if (!url || !PR_URL_PATTERN.test(url)) return { action: "ignored", reason: "not a gate URL" };

    const at = now();
    if (shouldSkipAsRepeat(tabId, url, at)) {
      return { action: "ignored", reason: "already reloaded this tab for this URL" };
    }

    // Is the content script already there? A tab that answers is a tab
    // whose page_state.js is watching, and reloading it would be both
    // pointless and destructive.
    let alive = false;
    try {
      const response = await sendMessageToTab(tabId, { type: "AICODEQUEST_CONTENT_PING" });
      alive = Boolean(response && response.alive);
    } catch {
      // "Could not establish connection. Receiving end does not exist."
      // -- exactly the case this exists for.
      alive = false;
    }
    if (alive) return { action: "ignored", reason: "content script already running" };

    recentlyReloaded.set(tabId, { url, at });
    await reloadTab(tabId);
    return { action: "reloaded", url };
  }

  handleHistoryStateUpdated.forgetTab = (tabId) => recentlyReloaded.delete(tabId);
  return handleHistoryStateUpdated;
}

/** Called from the service worker's top level. No-op where the API is
 *  absent (unit tests, and any context without the permission). */
export function registerSpaNavigationWatcher(chromeApi = globalThis.chrome) {
  if (!chromeApi?.webNavigation?.onHistoryStateUpdated) return null;

  const handler = createSpaNavigationHandler({
    sendMessageToTab: (tabId, message) => chromeApi.tabs.sendMessage(tabId, message),
    reloadTab: (tabId) => chromeApi.tabs.reload(tabId),
  });

  chromeApi.webNavigation.onHistoryStateUpdated.addListener(
    (details) => {
      handler(details).then((result) => {
        if (result.action === "reloaded") {
          console.info(`[aicodequest] reloading tab ${details.tabId} so the gate can run on ${details.url}`);
        }
      });
    },
    // A URL filter on the event itself, so the browser does not wake this
    // service worker for every SPA navigation anywhere on the web.
    { url: [{ hostEquals: "github.com", pathContains: "/pull/" }, { hostEquals: "github.com", pathContains: "/compare/" }] }
  );

  chromeApi.tabs?.onRemoved?.addListener((tabId) => handler.forgetTab(tabId));
  return handler;
}
