// The game/lead panel: an injected IFRAME pointing at
// chrome-extension://<id>/game.html -- NOT inline React in github.com's
// isolated world. Three reasons (per the phase brief): a clean document
// (no fighting GitHub's own CSS), CSP sanity (the extension page has its
// own CSP, unaffected by whatever github.com sets), and Monaco working
// without a fight (Monaco assumes it owns a chunk of DOM/CSS that an
// arbitrary host page's stylesheets could otherwise clobber).
//
// "Survive SPA navigation or close cleanly": the overlay is appended
// directly to <body>, OUTSIDE any region GitHub's own Turbo/pjax
// navigation swaps (verified against the fixture pages -- Turbo-style
// navigation replaces specific tracked containers, not the whole
// <body>), so navigating within the SAME PR (Conversation -> Files ->
// Conversation) leaves it untouched automatically. But its content is
// tied to ONE pr_id -- navigating to a DIFFERENT PR (or away from PR
// pages entirely) must close it explicitly rather than leave stale
// content dangling; that decision is the caller's (content/pr_page.js's
// orchestrator, which knows the OLD and NEW pr_id), not this module's --
// see closePanel().

export const OVERLAY_ID = "aicodequest-panel-overlay";

// Where the size preference lives. chrome.storage.local for the same
// reason theme_storage.js uses it: the panel is an extension page in an
// IFRAME on github.com, where third-party localStorage is partitioned or
// blockable by the top page's cookie settings.
export const SIZE_STORAGE_KEY = "aicodequest.panelSize";

// Two sizes, not a free resize. A drag handle would need pointer capture
// across an IFRAME boundary (the frame swallows the pointer events the
// moment the cursor crosses into it), which is a real amount of machinery
// for a preference that has exactly two useful values: the card, and all
// the room there is.
//
// `window` keeps the modal card the panel was designed as (Phase 23's
// shell: top bar, main column, context rail, action bar). `full` is for a
// large monitor and for Monaco, where a long function otherwise scrolls
// inside a 760px box. Both stay viewport-bounded, so neither can overflow
// a laptop screen.
//
// THE CONTROL ITSELF LIVES IN THE PANEL'S TOP BAR, beside its close
// button -- not out here in the overlay, where it first shipped and read
// as a stray browser control floating over github.com. It is the same
// split CLOSE_PANEL already uses: the button belongs in the modal's
// header row (and must follow the panel's theme), while the geometry it
// changes belongs to THIS document, because an iframe cannot resize
// itself. The panel asks (TOGGLE_SIZE); this module owns the change and
// reports the result back (PANEL_SIZE) so the button can label itself.
const SIZES = {
  window: "width:1160px;height:760px;max-width:94vw;max-height:92vh;",
  full: "width:100vw;height:100vh;max-width:100vw;max-height:100vh;",
};

const FRAME_CHROME =
  "border:none;background:transparent;overflow:hidden;" +
  "box-shadow:0 24px 64px rgba(12,36,41,0.5);";

/** @param {"window"|"full"} size */
function applySize(frame, size) {
  const chosen = SIZES[size] ? size : "window";
  frame.style.cssText =
    SIZES[chosen] +
    FRAME_CHROME +
    // Square corners at full size: a rounded card floating with no margin
    // reads as a rendering fault rather than a choice.
    (chosen === "full" ? "border-radius:0;" : "border-radius:14px;");
  frame.dataset.size = chosen;
}

async function readStoredSize() {
  try {
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      const stored = await chrome.storage.local.get(SIZE_STORAGE_KEY);
      const value = stored[SIZE_STORAGE_KEY];
      if (value === "window" || value === "full") return value;
    }
  } catch {
    // Storage unavailable -- the default is a working panel, not an error.
  }
  return "window";
}

function storeSize(size) {
  try {
    chrome?.storage?.local?.set?.({ [SIZE_STORAGE_KEY]: size });
  } catch {
    // A preference that cannot be saved must not break the click that set it.
  }
}

/**
 * @param {"dev"|"lead"} mode
 * @param {string} prId opaque id the backend uses to key gate sessions
 * @param {string} diffUrl the github.com .diff URL the iframe's React app
 *   will independently re-fetch (via GET_DIFF) -- passed through rather
 *   than postMessage'd after load, to avoid a load-timing handshake for
 *   something this cheap and idempotent to just re-fetch.
 * @param {{file: string, qualified_name?: string, start_line: number, end_line: number}} [target]
 *   the specific function (from /gate/evaluate's real tree-sitter
 *   extraction, picked via diff_utils.js's pickAiTaggedFunction) the dev
 *   gate should play on -- only its small line-range identifiers travel
 *   through the URL, not the source itself; main.jsx re-derives the
 *   actual source from its own diff re-fetch (see extractFunctionSource).
 *   Omitted for the lead gate (which plays the whole diff, not one
 *   function) or if no function could be picked.
 * @param {(url: string) => string} [buildUrl] injectable for tests --
 *   defaults to chrome.runtime.getURL, unavailable outside a real
 *   extension context.
 */
export function openPanel(
  doc,
  { mode, prId, diffUrl, patchUrl, owner, repo, target },
  buildUrl = (path) => chrome.runtime.getURL(path)
) {
  if (doc.getElementById(OVERLAY_ID)) {
    return; // already open -- never duplicate (same discipline as gate_ui.js's icon)
  }

  const overlay = doc.createElement("div");
  overlay.id = OVERLAY_ID;
  overlay.setAttribute("data-pr-id", prId);
  // A real modal treatment (Phase 23): a dimmed, blurred backdrop with
  // the panel as a floating card, rather than the previous flat sheet.
  // The backdrop deliberately does NOT close on click -- a misclick
  // beside a card the participant is mid-turn in would throw the turn
  // away; the panel's own close button and Escape are the ways out.
  overlay.style.cssText =
    "position:fixed;inset:0;z-index:2147483647;background:rgba(12,36,41,0.55);" +
    "backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px);" +
    "display:flex;align-items:center;justify-content:center;";

  const frameUrl = new URL(buildUrl("game.html"));
  frameUrl.searchParams.set("mode", mode);
  frameUrl.searchParams.set("prId", prId);
  if (diffUrl) frameUrl.searchParams.set("diffUrl", diffUrl);
  // Phase 36: what the panel needs to fetch each changed file WHOLE.
  // `patchUrl` yields the head commit SHA (its last `From <sha>` line);
  // owner/repo build the raw URL. Without these the panel falls back to
  // the diff's hunk fragments, which is the previous behaviour.
  if (patchUrl) frameUrl.searchParams.set("patchUrl", patchUrl);
  if (owner) frameUrl.searchParams.set("owner", owner);
  if (repo) frameUrl.searchParams.set("repo", repo);
  if (target) {
    frameUrl.searchParams.set("targetFile", target.file);
    frameUrl.searchParams.set("targetStartLine", String(target.start_line));
    frameUrl.searchParams.set("targetEndLine", String(target.end_line));
  }

  // The card is wider than it was: the panel now lays out as a real
  // application window (top bar, main column, context rail, action bar --
  // see src/game/Shell.jsx) and the rail needs the room. Still viewport-
  // bounded so it degrades on a laptop screen rather than overflowing.
  const frame = doc.createElement("iframe");
  frame.id = "aicodequest-panel-frame";
  frame.src = frameUrl.toString();
  frame.title = "AICodeQuest";
  applySize(frame, "window");

  // THERE IS NO OVERLAY-LEVEL CLOSE BUTTON. The panel's own top bar
  // carries the only one (Shell.jsx's CloseButton -> CLOSE_PANEL), which
  // is theme-aware and aligned with the header row; a second × floating in
  // the page corner read as duplicated chrome and as something belonging
  // to the browser rather than to the panel.
  //
  // What it was kept for was the case where game.html fails to load at all
  // and the in-panel button therefore never renders. **Escape covers
  // that**: the keydown listener below is registered on THIS document, not
  // inside the frame, so it does not depend on the panel having loaded.
  // (Its known limitation -- Escape only reaching this handler when the
  // top page, not the iframe, has focus -- does not apply in that
  // scenario, since a frame that never rendered is not holding focus.)

  function setSize(size, persist = true) {
    applySize(frame, size);
    if (persist) storeSize(frame.dataset.size);
    // Tell the panel what size it is now, so its top-bar button can label
    // itself. Without this the button would have to guess, and would guess
    // wrong every time a stored `full` preference was restored on open.
    frame.contentWindow?.postMessage(
      { source: "aicodequest", type: "PANEL_SIZE", size: frame.dataset.size },
      "*"
    );
  }
  overlay.__aicodequestSetSize = setSize;

  function onKeydown(event) {
    // Escape closes the panel, EXCEPT at full screen, where it steps back
    // to the card first. Full screen hides github.com completely, so a
    // single Escape there is much more likely to mean "give me the page
    // back" than "throw away the turn I am in the middle of".
    if (event.key !== "Escape") return;
    // A handler whose own frame is no longer in the document must do
    // NOTHING. closePanel() removes this listener, so in the normal flow
    // that cannot happen -- but if the overlay is ever torn out of the DOM
    // by something else, the stale handler would otherwise read a detached
    // frame's size and close whatever panel is open NOW.
    if (!doc.contains(frame)) return;
    if (frame.dataset.size === "full") {
      setSize("window");
      return;
    }
    closePanel(doc);
  }
  doc.addEventListener("keydown", onKeydown);
  overlay.__aicodequestCleanup = () => doc.removeEventListener("keydown", onKeydown);

  overlay.appendChild(frame);
  doc.body.appendChild(overlay);

  // The stored preference is applied AFTER the panel is in the DOM, not
  // awaited before it. Reading storage is async, and blocking the whole
  // panel on it would delay a participant's turn for a cosmetic setting;
  // if the panel is closed again before the read lands, the frame is gone
  // and there is nothing to resize.
  //
  // It also runs unconditionally rather than only for a stored `full`,
  // because the PANEL_SIZE reply is how the top-bar button learns what to
  // call itself -- a `window` panel that never heard back would be right
  // by luck rather than by being told.
  readStoredSize().then((size) => {
    if (doc.getElementById(OVERLAY_ID)) setSize(size, false);
  });
}

/**
 * Flips the open panel between the card and full screen.
 *
 * Called by content/pr_page.js when the panel's own top-bar button asks
 * (TOGGLE_SIZE). A no-op when no panel is open, so a late message from a
 * frame that has already been torn down cannot throw.
 */
export function togglePanelSize(doc) {
  const overlay = doc.getElementById(OVERLAY_ID);
  const frame = doc.getElementById("aicodequest-panel-frame");
  if (!overlay || !frame) return;
  overlay.__aicodequestSetSize?.(frame.dataset.size === "full" ? "window" : "full");
}

/**
 * Answers the panel's REQUEST_PANEL_SIZE with the size it is actually at.
 *
 * The panel has to ASK because the broadcast from openPanel's stored-
 * preference restore fires as soon as chrome.storage resolves -- always
 * before the iframe has loaded React and subscribed. Nothing here changes
 * the size; it only reports it.
 */
export function reportPanelSize(doc) {
  const overlay = doc.getElementById(OVERLAY_ID);
  const frame = doc.getElementById("aicodequest-panel-frame");
  if (!overlay || !frame) return;
  frame.contentWindow?.postMessage(
    { source: "aicodequest", type: "PANEL_SIZE", size: frame.dataset.size },
    "*"
  );
}

/** Closes the panel if open. Safe to call when it isn't (no-op). */
export function closePanel(doc) {
  const overlay = doc.getElementById(OVERLAY_ID);
  if (!overlay) return;
  overlay.__aicodequestCleanup?.();
  overlay.remove();
}

export function isPanelOpen(doc) {
  return Boolean(doc.getElementById(OVERLAY_ID));
}

export function panelPrId(doc) {
  return doc.getElementById(OVERLAY_ID)?.getAttribute("data-pr-id") || null;
}
