// MV3 background service worker.
//
// This file is ephemeral: Chrome can terminate and restart this worker
// at any time (idle timeout, browser resource pressure, etc), and does
// NOT preserve any module-level `let`/`const` state across that restart
// -- every handler below re-reads auth state from chrome.storage.local
// on EVERY call rather than caching it in memory. See
// docs/CHROME-EXTENSION-PRIMER.md's service-worker-lifecycle section and
// tests/unit/service_worker.test.js's restart-survival test.
//
// __BACKEND_BASE_URL__ is a build-time constant (see
// extension/scripts/build.js), inlined from config/params.yaml's
// extension.backend_base_url -- NOT read at runtime, since the extension
// has no base URL to fetch config FROM until it already has one.

import { createApiClient } from "../shared/api.js";
import { registerSpaNavigationWatcher } from "./spa_navigation.js";
import {
  createMessageRouter,
  MESSAGE_TYPES,
  SIGNED_OUT_MESSAGE,
  recordMessageLogEntry,
} from "../shared/messages.js";

export const BACKEND_BASE_URL =
  typeof __BACKEND_BASE_URL__ !== "undefined" ? __BACKEND_BASE_URL__ : "http://localhost:5055";

export const AUTH_STORAGE_KEY = "aicodequest.auth";
// Phase 13: keyed per pr_id, holds the LAST /gate/evaluate call's
// `functions` array so a re-push's evaluation can supply it back as
// `previous_functions` -- app/api/gate.py's re-push similarity-skip
// logic (Phase 2's original design) needs a caller-supplied baseline
// since this project has no PR-ingestion pipeline of its own; nothing in
// the extension ever provided one until now, so a cosmetic re-push could
// never actually trigger SKIPPED_SIMILARITY through the real UI. Content
// scripts never touch chrome.storage directly (same "one place" pattern
// as everything else that talks to the backend) -- this lives here.
export const PREVIOUS_FUNCTIONS_STORAGE_PREFIX = "aicodequest.previous_functions.";

/**
 * Builds the message router with real handler logic, given an API
 * client and a storage area to read/write auth state -- both injectable
 * so this is testable without any chrome.* mocking at all (see
 * tests/unit/service_worker.test.js). The real extension calls this with
 * no arguments, defaulting to the real backend + chrome.storage.local.
 */
export function createHandlers(apiClient = createApiClient(BACKEND_BASE_URL), authStorage = chrome.storage.local) {
  async function readAuth() {
    const stored = await authStorage.get(AUTH_STORAGE_KEY);
    return stored[AUTH_STORAGE_KEY] || null;
  }

  async function writeAuth(auth) {
    await authStorage.set({ [AUTH_STORAGE_KEY]: auth });
  }

  const router = createMessageRouter();

  router.register(MESSAGE_TYPES.PING, async () => apiClient.ping());

  // Phase 25: register, then log the new participant straight in. One
  // round trip from the popup's point of view -- an account they cannot
  // immediately use is a dead end, and asking them to retype the same
  // credentials into a second form is the kind of friction the phase 8
  // brief called out as dropout risk.
  router.register(MESSAGE_TYPES.REGISTER, async (message) => {
    const { email, password, displayName, roleHint } = message;
    if (!email || !password || !displayName) {
      throw new Error("REGISTER requires email, password and displayName");
    }
    await apiClient.register(email, password, displayName, roleHint || "developer");
    const { access_token: token } = await apiClient.login(email, password);
    const user = await apiClient.getMe(token);
    await writeAuth({ token, user });
    return { loggedIn: true, user };
  });

  router.register(MESSAGE_TYPES.LOGIN, async (message) => {
    const { email, password } = message;
    if (!email || !password) {
      throw new Error("LOGIN requires email and password");
    }
    const { access_token: token } = await apiClient.login(email, password);
    const user = await apiClient.getMe(token);
    await writeAuth({ token, user });
    return { loggedIn: true, user };
  });

  // Sign-out. The token is cleared LOCALLY no matter what the backend says:
  // a participant who clicks Sign out must end up signed out, and an
  // unreachable backend, a token it already rejects, or any other failure
  // must not leave them stuck signed in. The backend call is what makes the
  // sign-out real (tokens never expire, so a forgotten-but-unrevoked copy
  // would stay valid), and whether it succeeded is reported, not hidden.
  router.register(MESSAGE_TYPES.LOGOUT, async () => {
    const auth = await readAuth();
    let revoked = false;
    if (auth?.token) {
      try {
        await apiClient.logout(auth.token);
        revoked = true;
      } catch {
        revoked = false;
      }
    }
    await authStorage.remove(AUTH_STORAGE_KEY);
    return { loggedIn: false, revoked };
  });

  router.register(MESSAGE_TYPES.GET_ME, async () => {
    // Reads from storage, not a cached variable -- this IS the
    // restart-survival behavior: a freshly (re)started worker with no
    // in-memory state still answers correctly because the token was
    // never anywhere BUT storage.
    const auth = await readAuth();
    if (!auth) return { loggedIn: false };
    return { loggedIn: true, user: auth.user };
  });

  router.register(MESSAGE_TYPES.EVALUATE_GATE, async (message) => {
    const prId = message.payload.pr_id;
    const storageKey = prId ? `${PREVIOUS_FUNCTIONS_STORAGE_PREFIX}${prId}` : null;
    let previousFunctions = [];
    if (storageKey) {
      const stored = await authStorage.get(storageKey);
      previousFunctions = stored[storageKey] || [];
    }

    // Goes through withAuth like every other authenticated call, so an
    // expired token clears here too. It used to read the token directly --
    // understandable, since it also needs `authStorage` for the
    // previous-functions baseline, but it meant the FIRST call on a pull
    // request page was the one path where a dead token stayed in storage
    // and the participant got "failed with status 401" against a blocked
    // button. withAuth is a function declaration, so it hoists above this.
    const result = await withAuth((token) =>
      apiClient.evaluateGate(token, {
        ...message.payload,
        previous_functions: previousFunctions,
      })
    );

    if (storageKey && Array.isArray(result.functions)) {
      await authStorage.set({ [storageKey]: result.functions });
    }
    return result;
  });

  router.register(MESSAGE_TYPES.GET_DIFF, async (message) => {
    // The ONE handler that does not need auth -- fetchDiff() talks to
    // github.com, not our backend, using the tab's own session cookies.
    return apiClient.fetchDiff(message.diffUrl);
  });

  router.register(MESSAGE_TYPES.GET_FILE_SOURCES, async (message) => {
    // Also unauthenticated, and for the same reason as GET_DIFF: this
    // talks to github.com using the tab's own session cookies, never to
    // our backend.
    return apiClient.fetchFileSources(message.payload || {});
  });

  router.register(MESSAGE_TYPES.REPORT_SELECTOR_MISS, async (message) => {
    const auth = await readAuth();
    if (!auth) {
      // A selector-miss report is diagnostic, not critical-path -- a
      // logged-out browsing session (just looking at a PR, not logged
      // into the extension) must not surface an error for this.
      return { reported: false, reason: "not logged in" };
    }
    await apiClient.reportSelectorMiss(auth.token, message.payload);
    return { reported: true };
  });

  /**
   * Runs an authenticated call, and TURNS A DEAD TOKEN INTO A LOGGED-OUT
   * STATE (2026-09-01).
   *
   * THE BUG THIS FIXES, from the study host's own telemetry: a participant
   * logged in at 09:36, signed consent successfully at 09:36:08, and at
   * 10:36:33 -- 60m29s later, i.e. the moment the then-60-minute
   * `auth.jwt_access_token_expires_min` elapsed -- every call began
   * returning 401 with `actor_id: "anonymous"`. Nothing noticed. The token
   * stayed in storage, so GET_ME kept answering `loggedIn: true`, the popup
   * kept hiding the login form, and the consent button kept being offered.
   * They clicked it 19 times and then tried to REGISTER A NEW ACCOUNT eight
   * times (all 409) before logging in again fixed it instantly.
   *
   * THAT PARTICULAR CAUSE IS GONE: tokens no longer expire at all
   * (`jwt_access_token_expires_min: 0`). This stays, and matters more for
   * it -- a 401 is now RARE and UNEXPECTED (a dropped database, a rotated
   * JWT_SECRET_KEY, corrupted storage), which is exactly when a silent
   * failure is hardest to diagnose. Without it, any of those returns to the
   * reported experience: the popup insisting you are signed in while every
   * call fails, and no way out but reinstalling the extension.
   *
   * So a 401 CLEARS the stored auth. The next GET_ME then reports
   * `loggedIn: false` truthfully and every surface falls back to its
   * logged-out state on its own -- no new signal for each caller to
   * remember to handle.
   *
   * ONLY 401. A 403 is a live session being refused something (org consent,
   * `not_a_reviewer`), and logging that participant out would destroy a
   * working session over a permissions answer.
   */
  async function withAuth(fn) {
    const auth = await readAuth();
    if (!auth) {
      // Deliberately UNCHANGED. content/pr_page.js matches this string to
      // decide which explanation to show, and five tests assert it; the
      // expired case gets its own message below rather than redefining
      // this one out from under them.
      throw new Error("not logged in");
    }
    try {
      return await fn(auth.token);
    } catch (err) {
      if (err?.status === 401) {
        await authStorage.remove(AUTH_STORAGE_KEY);
        // Rethrow with a message a PERSON can act on. The backend's own
        // body is no help: flask-jwt-extended answers `{"msg": ...}` while
        // shared/api.js reads `data.error`, so every one of those 401s
        // surfaced to the participant as the literal string
        // "POST /consent/participant failed with status 401".
        const signedOut = new Error(SIGNED_OUT_MESSAGE);
        signedOut.status = 401;
        signedOut.signedOut = true;
        throw signedOut;
      }
      throw err;
    }
  }

  router.register(MESSAGE_TYPES.DEV_GATE_START, (message) =>
    withAuth((token) => apiClient.devGateStart(token, message.prId, message.function, message.files))
  );
  router.register(MESSAGE_TYPES.DEV_GATE_RESTART, (message) =>
    withAuth((token) => apiClient.devGateRestart(token, message.prId))
  );
  router.register(MESSAGE_TYPES.DEV_GATE_STATE, (message) =>
    withAuth((token) => apiClient.devGateState(token, message.prId))
  );
  router.register(MESSAGE_TYPES.DEV_GATE_CURRENT_TASK, (message) =>
    withAuth((token) => apiClient.devGateCurrentTask(token, message.prId))
  );
  router.register(MESSAGE_TYPES.DEV_GATE_PROGRESS, (message) =>
    withAuth((token) => apiClient.devGateProgress(token, message.prId))
  );
  router.register(MESSAGE_TYPES.DEV_GATE_SUBMIT_DEFENSE, (message) =>
    withAuth((token) => apiClient.devGateSubmitDefense(token, message.prId, message.code))
  );
  router.register(MESSAGE_TYPES.DEV_GATE_SUBMIT_ATTACK, (message) =>
    withAuth((token) => apiClient.devGateSubmitAttack(token, message.prId, message.code))
  );
  router.register(MESSAGE_TYPES.DEV_GATE_SKIP_ATTACK, (message) =>
    withAuth((token) => apiClient.devGateSkipAttack(token, message.prId))
  );
  router.register(MESSAGE_TYPES.DEV_GATE_ANSWER_MCQ, (message) =>
    withAuth((token) =>
      apiClient.devGateAnswerMcq(token, message.prId, message.itemId, message.selectedIndex)
    )
  );
  router.register(MESSAGE_TYPES.DEV_GATE_ADVANCE, (message) =>
    withAuth((token) => apiClient.devGateAdvance(token, message.prId))
  );

  router.register(MESSAGE_TYPES.LEAD_GATE_NFRS, (message) =>
    withAuth((token) => apiClient.leadGateNfrs(token, message.prId))
  );
  router.register(MESSAGE_TYPES.LEAD_GATE_START, (message) =>
    withAuth((token) => apiClient.leadGateStart(token, message.prId, message.selected, message.custom, message.diff))
  );
  router.register(MESSAGE_TYPES.LEAD_GATE_STATE, (message) =>
    withAuth((token) => apiClient.leadGateState(token, message.prId))
  );
  router.register(MESSAGE_TYPES.LEAD_GATE_CURRENT_QUESTION, (message) =>
    withAuth((token) => apiClient.leadGateCurrentQuestion(token, message.prId))
  );
  router.register(MESSAGE_TYPES.LEAD_GATE_ANSWER, (message) =>
    withAuth((token) => apiClient.leadGateAnswer(token, message.prId, message.selectedIndex))
  );
  router.register(MESSAGE_TYPES.LEAD_GATE_PROGRESS, (message) =>
    withAuth((token) => apiClient.leadGateProgress(token, message.prId))
  );
  router.register(MESSAGE_TYPES.LEAD_GATE_ADVANCE, (message) =>
    withAuth((token) => apiClient.leadGateAdvance(token, message.prId))
  );

  // --- Lead review queue (Phase 35) ---
  router.register(MESSAGE_TYPES.MCQ_REVIEW_QUEUE, () =>
    withAuth((token) => apiClient.mcqReviewQueue(token))
  );
  router.register(MESSAGE_TYPES.MCQ_REVIEW_ITEM, (message) =>
    withAuth((token) => apiClient.mcqReviewItem(token, message.itemId))
  );
  router.register(MESSAGE_TYPES.MCQ_REVIEW_ANSWER, (message) =>
    withAuth((token) => apiClient.mcqReviewAnswer(token, message.itemId, message.selectedIndex))
  );

  router.register(MESSAGE_TYPES.CONSENT_GET, () => withAuth((token) => apiClient.getParticipantConsent(token)));
  router.register(MESSAGE_TYPES.CONSENT_SIGN, (message) =>
    withAuth((token) =>
      apiClient.signParticipantConsent(token, {
        version: message.version,
        retainCodeSnippets: message.retainCodeSnippets,
        retainTranscripts: message.retainTranscripts,
        allowQuotesInPublication: message.allowQuotesInPublication,
      })
    )
  );

  router.register(MESSAGE_TYPES.LEADERBOARD_PUBLIC, (message) =>
    withAuth((token) => apiClient.leaderboardPublic(token, message.persona))
  );
  router.register(MESSAGE_TYPES.LEADERBOARD_ME, () => withAuth((token) => apiClient.leaderboardMe(token)));
  router.register(MESSAGE_TYPES.LEADERBOARD_EXPORT, () => withAuth((token) => apiClient.leaderboardExport(token)));
  router.register(MESSAGE_TYPES.LEADERBOARD_REPORT, () => withAuth((token) => apiClient.leaderboardReport(token)));

  return router;
}

// Only wire up the real chrome.runtime listener when actually running as
// an extension -- createHandlers() has no chrome.* dependency of its own
// until it's called with no arguments, so importing this module in a
// unit test (which calls createHandlers(fakeApi, fakeStorage) directly)
// never touches chrome.* at all.
/* c8 ignore start */
if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
  const router = createHandlers();

  // Phase 26: reload a tab that navigated INTO a pull request without a
  // document load, because Chrome will not have injected the content
  // script there. See background/spa_navigation.js for why this is the
  // fix and why it can neither loop nor disturb a working page.
  registerSpaNavigationWatcher(chrome);

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    recordMessageLogEntry(chrome.storage.session, {
      direction: "in",
      type: message?.type,
    });

    router
      .dispatch(message, sender)
      .then((result) => {
        recordMessageLogEntry(chrome.storage.session, { direction: "out", type: message?.type, ok: true });
        sendResponse({ ok: true, result });
      })
      .catch((err) => {
        recordMessageLogEntry(chrome.storage.session, {
          direction: "out",
          type: message?.type,
          ok: false,
          error: err instanceof Error ? err.message : String(err),
        });
        sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) });
      });

    return true; // keep the message channel open for the async work above
  });
}
/* c8 ignore stop */
