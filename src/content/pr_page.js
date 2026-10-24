// Orchestrator: wires page_state (SPA-aware context detection) to the
// AI-authorship tagging panel, the gate evaluation call, and the
// badge/icon/panel UI. This file's job stops at "detect, tag, evaluate,
// show icon, open/close the panel" -- everything about actually PLAYING
// a gate (dev_gate or lead_gate's real API lifecycle) lives in the
// iframe's React app (src/game/), which talks to the service worker
// independently using the mode/prId/diffUrl passed via the iframe's URL.
// This keeps this file's surface small on purpose: it's the part that
// runs in github.com's isolated world on every matching page load, so it
// should stay boring and fast (see tagging_panel.js's module docstring
// on why the friction here matters for dropout).

import { watchPageState } from "./page_state.js";
import { setSelectorMissReporter, findElementQuiet } from "./selectors.js";
import { parseHunks, pickAiTaggedFunction, headShaFromPatch } from "./diff_utils.js";
import { renderTaggingPanel } from "./tagging_panel.js";
import {
  renderErrorBadge,
  renderNeutralBadge,
  renderFiredGateUi,
  removeGateUi,
  applyProvisionalBlock,
  releaseProvisionalBlock,
} from "./gate_ui.js";
import { openPanel, closePanel, isPanelOpen, panelPrId, togglePanelSize, reportPanelSize } from "./panel.js";
import { MESSAGE_TYPES, SIGNED_OUT_MESSAGE } from "../shared/messages.js";

setSelectorMissReporter((key, details) => {
  chrome.runtime.sendMessage({
    type: MESSAGE_TYPES.REPORT_SELECTOR_MISS,
    payload: { selector_key: key, ...details },
  });
});

/**
 * A single opaque session key the backend uses to correlate gate state
 * -- the backend never parses this string (see reports/phase-5-review.md
 * / phase-7-review.md: dev_gate/lead_gate are keyed by a caller-supplied
 * pr_id with no PR-ingestion pipeline behind it), so any stable, unique-
 * per-PR format works PROVIDED it contains no "/" -- verified directly
 * against a real running Flask server (see reports/phase-9-review.md)
 * that encodeURIComponent() alone is NOT enough: Werkzeug/WSGI decodes
 * "%2F" back to "/" before routing sees it, so <pr_id>'s default string
 * converter still fails to match a slash-containing value even
 * percent-encoded. owner/repo names and branch names routinely contain
 * "/" (e.g. "fix/payment-retry"), so every "/" is replaced with "__"
 * here -- a small, deliberate collision risk for the rare branch name
 * that already contains "__", flagged in reports/phase-9-review.md
 * rather than silently accepted.
 */
export function prIdFor(context) {
  let raw;
  if (context.context === "pr_conversation") {
    raw = `${context.owner}/${context.repo}#${context.pr_number}`;
  } else if (context.context === "new_pr") {
    // Phase 29: if a pull request ALREADY EXISTS for this branch pair, key
    // on the pull request, not on the branch pair.
    //
    // Reported from real use: "when there are multiple PRs it is not
    // working right". A compare page's identity was
    // `owner/repo@base...head`, and the study's own workflow is to keep
    // pushing to the same fork branch and open a NEW pull request each
    // time -- so every one of those pull requests collided on a single
    // pr_id and shared one gate session. The second pull request inherited
    // the first one's state: already cleared, or stuck mid-replay.
    //
    // Keying on the pull request number also makes the developer's session
    // and the lead's session on that same pull request agree on an id,
    // which is what persona_order.py already assumes.
    //
    // Phase 36 correction: "a pull request EXISTS for this branch" is not
    // the same as "this compare IS that pull request", and Phase 29
    // conflated them.
    //
    // Reported from real play: push more commits to a branch that already
    // has an open pull request, and GitHub's compare page offers BOTH
    // "View pull request" and "Create pull request". The participant is
    // raising a NEW one -- but `existingPrNumber()` found the OLD one's
    // link, so the gate bound to the OLD pull request's session, which is
    // usually already CLEARED. The gate then waves them through, or shows
    // a stale Cleared screen for work they have not done.
    //
    // `can_create` distinguishes the two. When the page offers a create
    // action this compare is a NEW pull request and must not inherit; when
    // it offers only "View pull request", this compare IS that pull
    // request and keying on its number is right (and is what makes the
    // developer's and the lead's sessions agree, as persona_order.py
    // assumes).
    const isNewPullRequest = context.can_create || !context.existing_pr_number;
    if (!isNewPullRequest) {
      raw = `${context.owner}/${context.repo}#${context.existing_pr_number}`;
    } else if (context.head_sha) {
      // The branch pair ALONE is not enough -- that is the collision
      // Phase 29 fixed, where two pull requests off one branch shared a
      // session. The head commit distinguishes them: it changes on every
      // push, and is stable across reloads of the same compare, so one
      // attempt is one gate.
      raw = `${context.owner}/${context.repo}@${context.base}...${context.head}@${context.head_sha.slice(0, 12)}`;
    } else {
      // No head SHA available (the patch fetch failed). Falls back to the
      // branch pair, which is the pre-Phase-29 behaviour and its known
      // collision -- worse than the line above, better than binding to
      // somebody else's cleared session.
      raw = `${context.owner}/${context.repo}@${context.base}...${context.head}`;
    }
  } else {
    return null;
  }
  return raw.replace(/\//g, "__");
}

/**
 * The pull-request number of an EXISTING pull request for this branch,
 * read off the "View pull request" link GitHub shows on a compare page
 * once one is open. Null when there isn't one (the ordinary "this branch
 * has no pull request yet" case).
 *
 * GitHub shows this link for the OPEN pull request on that branch pair,
 * which is the "choose the recent PR" behaviour asked for -- resolved from
 * the page GitHub already rendered rather than by querying its API, so it
 * needs no extra permission and no token.
 */
export function existingPrNumber(doc) {
  // Via the selector registry (accessible name "View pull request"), not a
  // bare href scan: a compare page has other /pull/ links in its nav and
  // timeline, and picking the wrong one would key the gate to somebody
  // else's pull request.
  const link = findElementQuiet(doc, "existingPrLink");
  const match = link?.getAttribute?.("href")?.match(/\/pull\/(\d+)/);
  return match ? Number(match[1]) : null;
}

/**
 * The `.patch` sibling of diffUrlFor's URL.
 *
 * Used for exactly one thing: its LAST `From <sha>` line is the head
 * commit SHA (git format-patch emits one per commit, in order), which is
 * what lets the panel fetch each changed file whole. Verified against a
 * real repository on both shapes -- the `.diff` cannot serve this, since
 * its `index` lines carry blob hashes, not commit hashes.
 */
export function patchUrlFor(context) {
  const diff = diffUrlFor(context);
  return diff ? diff.replace(/\.diff$/, ".patch") : null;
}

/**
 * The head commit SHA for this context, or null.
 *
 * Null is a normal outcome (a private repo the tab cannot read, a network
 * failure, a compare with no commits), and every caller treats it as "fall
 * back to the previous behaviour" rather than as an error -- nothing here
 * is worth blocking a participant for.
 */
export async function headShaFor(context, send = defaultSend) {
  const patchUrl = patchUrlFor(context);
  if (!patchUrl) return null;
  try {
    const res = await send({ type: MESSAGE_TYPES.GET_DIFF, diffUrl: patchUrl });
    // Same envelope fetchDiffText() unwraps: the router wraps every
    // handler's return value in { ok, result }.
    const patch = res?.ok ? res.result : null;
    return patch?.ok ? headShaFromPatch(patch.diff) : null;
  } catch {
    return null;
  }
}

function defaultSend(message) {
  return chrome.runtime.sendMessage(message);
}

export function diffUrlFor(context) {
  if (context.context === "pr_conversation") {
    return `https://github.com/${context.owner}/${context.repo}/pull/${context.pr_number}.diff`;
  }
  if (context.context === "new_pr") {
    return `https://github.com/${context.owner}/${context.repo}/compare/${context.base}...${context.head}.diff`;
  }
  return null;
}

/** new_pr = the DEVELOPER's gate, encountered before the PR exists.
 *  pr_conversation = the LEAD's gate, encountered before merging. */
function modeFor(context) {
  return context.context === "new_pr" ? "dev" : "lead";
}

function targetButtonKeyFor(context) {
  return context.context === "new_pr" ? "createPrButton" : "mergeButton";
}

async function fetchDiffText(diffUrl) {
  const response = await chrome.runtime.sendMessage({ type: MESSAGE_TYPES.GET_DIFF, diffUrl });
  if (!response.ok || !response.result.ok) {
    console.warn("[AICodeQuest] could not fetch diff:", response.error || response.result?.error);
    return null;
  }
  return response.result.diff;
}

// Phase 29: pull requests whose tagging answers we already have, so the
// panel is never shown twice for the same one.
//
// Reported from real use: "when asking ai or human written it is asking
// twice". Three separate ways that happened, all fixed here --
//   1. the flow could run twice concurrently (page_state fires on a
//      debounced MutationObserver; on a compare page the computed pr_id
//      changes as GitHub finishes rendering the branch selectors, which
//      trips the `newPrId !== currentPrId` guard a second time);
//   2. background/spa_navigation.js reloads the tab once so the content
//      script gets injected -- a reload the participant did not ask for,
//      after which an in-memory answer is gone;
//   3. nothing removed an existing panel before rendering another.
//
// The answer is study data (it decides which code the challenge is built
// from), so it is kept in `chrome.storage.session`: it survives a reload
// and the service worker being torn down, and it is cleared when the
// browser closes -- which is the right lifetime for "this visit's answer".
const TAGGED_STORE_KEY = "aicodequest.tagged";
const inFlight = new Set();

async function loadSavedTagging(prId) {
  try {
    const stored = await chrome.storage.session.get(TAGGED_STORE_KEY);
    return stored?.[TAGGED_STORE_KEY]?.[prId] ?? null;
  } catch {
    return null; // storage.session unavailable -- ask again rather than break
  }
}

async function saveTagging(prId, ranges) {
  try {
    const stored = await chrome.storage.session.get(TAGGED_STORE_KEY);
    const all = stored?.[TAGGED_STORE_KEY] ?? {};
    all[prId] = ranges;
    await chrome.storage.session.set({ [TAGGED_STORE_KEY]: all });
  } catch {
    /* not fatal: the participant is asked again next time */
  }
}

export async function runGateFlow(context, doc = document) {
  const prId = prIdFor(context);
  const diffUrl = diffUrlFor(context);

  // A second concurrent run would render a second panel over the first.
  if (inFlight.has(prId)) return;
  inFlight.add(prId);
  let fired = false;
  try {
    fired = await runGateFlowInner(context, doc, prId, diffUrl);
    return fired;
  } finally {
    inFlight.delete(prId);
    // Whatever happened, a button we blocked PROVISIONALLY must not stay
    // blocked unless the gate actually fired. Every path below the block
    // can bail out early -- the diff fetch fails, /gate/evaluate fails, the
    // participant closes the tagging panel -- and without this the
    // participant is left unable to open a pull request, with no icon and
    // no explanation, which is a far worse failure than the gate not
    // applying. A FIRED gate's block is a different attribute and is
    // untouched by this.
    if (!fired) releaseProvisionalBlock(doc);
  }
}

/**
 * Turns a /gate/evaluate failure into something the person looking at the
 * screen can act on -- or, where they cannot act, into a clear statement
 * that it is not theirs to fix.
 */
export function explainGateFailure(error, context) {
  const owner = context?.owner || "this repository's owner";
  if (String(error || "").includes("org_consent_missing")) {
    return {
      title: "AICodeQuest is not enabled for this repository.",
      detail:
        `The study has no organisation consent on record for "${owner}". ` +
        "This is separate from the consent you signed when creating your " +
        "account, and it can only be registered by a researcher — signing " +
        "again will not change it. Ask the study lead to enable this repo.",
    };
  }
  if (String(error || "").includes("participant_consent_missing")) {
    return {
      title: "You have not signed the study consent yet.",
      detail: "Open the AICodeQuest extension icon and sign it, then reload this page.",
    };
  }
  // Checked BEFORE "not logged in": both end in the same action, but this
  // one has to say the participant DID nothing wrong. Without it, a
  // rejected token reads as "you never signed in", which is what sent the
  // first person who hit it to the register form (eight 409s) rather than
  // the login form.
  if (String(error || "").includes(SIGNED_OUT_MESSAGE)) {
    return {
      title: "You have been signed out of AICodeQuest.",
      detail:
        "This is unusual — sign-ins do not time out. Nothing is wrong with " +
        "your account and your progress is saved. Open the extension icon, " +
        "sign in again, then reload this page. If it keeps happening, tell " +
        "the study lead.",
    };
  }
  if (String(error || "").includes("not logged in")) {
    return {
      title: "You are not signed in to AICodeQuest.",
      detail: "Open the extension icon to sign in, then reload this page.",
    };
  }
  return {
    title: "AICodeQuest could not check this pull request.",
    detail: `${error || "the backend did not respond"}. The button is not blocked.`,
  };
}

async function runGateFlowInner(context, doc, prId, diffUrl) {
  console.log(`[AICodeQuest] context detected: ${context.context}`, context);

  // BLOCK FIRST, ask second. Everything below this line is a network round
  // trip or a human decision, and until Phase 29 the button stayed live
  // through all of it -- so ignoring the tagging panel and clicking Create
  // was the fastest way past the gate. The safe state is now the default.
  applyProvisionalBlock(doc, targetButtonKeyFor(context));

  const diffText = await fetchDiffText(diffUrl);
  if (!diffText) return false;

  const filesWithHunks = parseHunks(diffText);
  const saved = await loadSavedTagging(prId);
  // Phase 36: the panel now reports HOW the answer was given as well as
  // what it was (per_hunk / per_file / mixed), because bulk tagging came
  // back and the measurement risk has to be visible rather than assumed
  // away. Older saved answers are a bare array -- normalised here so a
  // participant mid-session is not stranded by the shape change.
  const answer = saved ?? (await renderTaggingPanel(doc.body, filesWithHunks));
  const aiTaggedRanges = Array.isArray(answer) ? answer : answer.ranges;
  const taggingMethod = Array.isArray(answer) ? "unknown" : answer.tagging_method;
  if (!saved) await saveTagging(prId, answer);

  const evalResponse = await chrome.runtime.sendMessage({
    type: MESSAGE_TYPES.EVALUATE_GATE,
    payload: {
      repo: `${context.owner}/${context.repo}`,
      pr_number: context.context === "pr_conversation" ? context.pr_number : 0,
      diff: diffText,
      ai_tagged_ranges: aiTaggedRanges,
      tagging_method: taggingMethod,
      // Phase 13: lets the backend look up an existing dev_gate session
      // by (pr_id, user_id) to transition it straight to
      // SKIPPED_SIMILARITY on a genuinely cosmetic re-push -- see
      // app/api/gate.py's evaluate(). Harmless to always send; the
      // backend only acts on it when the caller is also authenticated.
      pr_id: prId,
    },
  });

  if (!evalResponse.ok) {
    console.warn("[AICodeQuest] /gate/evaluate failed:", evalResponse.error);
    // Phase 36: SAY something. This used to fail silently to the console,
    // so a participant on a repository the study has no org consent for
    // saw the extension simply do nothing -- and reasonably concluded the
    // consent they signed when creating their account should have covered
    // it. It cannot: those are two different consents (see
    // renderErrorBadge), and only a researcher can grant the second one.
    renderErrorBadge(doc, explainGateFailure(evalResponse.error, context));
    return false;
  }

  const decision = evalResponse.result.trigger_decision;
  if (!decision.fires) {
    renderNeutralBadge(doc, decision.reasons[0] || "below threshold");
    return false;
  }

  // Phase 13 fix: pick the function the trigger actually fired on (one
  // overlapping an AI-tagged range) rather than letting the panel fall
  // back to "whatever file comes first in the diff" -- see
  // diff_utils.js's pickAiTaggedFunction() docstring for why this
  // mattered in real multi-file-PR testing.
  const target = pickAiTaggedFunction(evalResponse.result.functions || [], aiTaggedRanges);

  renderFiredGateUi(doc, {
    targetButtonKey: targetButtonKeyFor(context),
    reason: decision.reasons[0] || "comprehension check required",
    onIconClick: () =>
      openPanel(doc, {
        mode: modeFor(context),
        prId,
        diffUrl,
        patchUrl: patchUrlFor(context),
        owner: context.owner,
        repo: context.repo,
        target,
      }),
  });
  return true;
}

/**
 * The iframe (src/game/, via notify_parent.js) posts this message when
 * its gate reaches CLEARED -- otherwise there is NO way for this
 * orchestrator to learn that and re-enable the button it disabled. Found
 * only by actually running a full playthrough end to end, not by
 * inspection -- see reports/phase-9-review.md.
 */
export function handleGateClearedMessage(event, doc = document) {
  if (event.data && event.data.source === "aicodequest" && event.data.type === "GATE_CLEARED") {
    removeGateUi(doc);
  }
}

/**
 * The panel's own top-bar close button (Phase 23). The button has to
 * render inside the iframe -- it sits in the modal's header row and
 * follows the panel's theme -- but the overlay it closes belongs to this
 * document, which the iframe cannot touch. See game/notify_parent.js.
 */
export function handleClosePanelMessage(event, doc = document) {
  if (event.data && event.data.source === "aicodequest" && event.data.type === "CLOSE_PANEL") {
    closePanel(doc);
  }
}

/**
 * The panel's full-screen button, same split as CLOSE_PANEL above: the
 * control belongs in the modal's header row beside Close, but the iframe's
 * dimensions belong to THIS document -- an iframe cannot resize itself.
 */
export function handleTogglePanelSizeMessage(event, doc = document) {
  if (!event.data || event.data.source !== "aicodequest") return;
  if (event.data.type === "TOGGLE_SIZE") {
    togglePanelSize(doc);
  } else if (event.data.type === "REQUEST_PANEL_SIZE") {
    // The panel's size button asking what it should call itself, because
    // the reply to openPanel's own restore raced the iframe load and was
    // always lost. See panel.js's reportPanelSize.
    reportPanelSize(doc);
  }
}

/**
 * Phase 26: answers the service worker's liveness ping.
 *
 * background/spa_navigation.js reloads a tab that navigated into a pull
 * request client-side, because Chrome injects content scripts only on a
 * document load. It must NOT reload a tab where this script is already
 * running -- page_state.js handles navigation fine from there, and a
 * reload would throw away a half-typed PR title or tagging panel. This
 * one-line reply is how a live tab says "leave me alone".
 */
export function installContentPingResponder(runtime = globalThis.chrome?.runtime) {
  if (!runtime?.onMessage) return;
  runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "AICODEQUEST_CONTENT_PING") {
      sendResponse({ alive: true });
      return true;
    }
    return undefined;
  });
}

export function init(doc = document, win = window) {
  let currentPrId = null;

  installContentPingResponder();

  win.addEventListener("message", (event) => {
    handleGateClearedMessage(event, doc);
    handleClosePanelMessage(event, doc);
    handleTogglePanelSizeMessage(event, doc);
  });

  // Bumped on every page-state event. A context resolution that involves a
  // network round trip can finish AFTER the participant has navigated
  // somewhere else, and applying it then would open a gate for the page
  // they just left.
  let generation = 0;

  return watchPageState(async (rawContext) => {
    const mine = ++generation;

    // Resolve an already-open pull request for this branch BEFORE deriving
    // the id, so a second pull request from the same branch is a different
    // gate rather than the first one's session again.
    let current = rawContext;
    if (rawContext.context === "new_pr") {
      current = {
        ...rawContext,
        existing_pr_number: existingPrNumber(doc),
        // Whether this page offers a CREATE action. When it does, the
        // participant is raising a NEW pull request and must not inherit
        // an existing one's session -- see prIdFor().
        can_create: Boolean(findElementQuiet(doc, "createPrButton")),
      };
    }
    if (current.context !== "none") {
      current = { ...current, head_sha: await headShaFor(current) };
      if (mine !== generation) return; // navigated away while fetching
    }

    const newPrId = current.context !== "none" ? prIdFor(current) : null;

    if (isPanelOpen(doc) && panelPrId(doc) !== newPrId) {
      closePanel(doc);
    }

    if (newPrId !== currentPrId) {
      removeGateUi(doc);
      currentPrId = newPrId;
      if (current.context !== "none") {
        runGateFlow(current, doc);
      }
    }
  }, { doc, win });
}

/* c8 ignore start */
if (typeof document !== "undefined" && typeof chrome !== "undefined") {
  init();
}
/* c8 ignore stop */
