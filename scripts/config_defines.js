// The build-time constants inlined into every extension bundle, derived
// from config/params.yaml.
//
// Lives in its own module (rather than inline in build.js) because
// vitest.config.js needs the IDENTICAL set: a unit test that exercised a
// different extension->language map than the shipped bundle would go
// green on exactly the staleness it exists to catch.
//
// These are `define`s rather than a runtime fetch because each is needed
// before the extension can talk to the backend at all -- the base URL
// most obviously, but also the language map, which the content script
// uses to classify a diff's files.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { load as loadYaml } from "js-yaml";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
export const PARAMS_PATH = path.join(REPO_ROOT, "config", "params.yaml");

/** Extension -> language, flattened from config's `languages` table --
 *  the same table backend/app/services/diff_scope.py's
 *  _extension_language_map() builds from. */
export function languageExtensionMap(config) {
  const languages = config?.languages;
  if (!languages || !Object.keys(languages).length) {
    throw new Error(`config/params.yaml is missing the \`languages\` table (read from ${PARAMS_PATH})`);
  }
  const map = {};
  for (const [language, spec] of Object.entries(languages)) {
    for (const ext of spec.extensions || []) {
      map[ext.toLowerCase()] = language;
    }
  }
  return map;
}

export function buildDefines() {
  const config = loadYaml(readFileSync(PARAMS_PATH, "utf8"));

  // The extension has no way to fetch config before it has a base URL to
  // fetch it FROM, so this can't be a runtime fetch.
  const backendBaseUrl = config?.extension?.backend_base_url;
  if (!backendBaseUrl) {
    throw new Error(`config/params.yaml is missing extension.backend_base_url (read from ${PARAMS_PATH})`);
  }

  // POST /consent/participant REJECTS any version that isn't the
  // currently-active one (consent.ConsentVersionMismatchError), so the
  // consent dialog has to send the exact string the backend expects.
  const consentVersion = config?.consent?.current_version;
  if (!consentVersion) {
    throw new Error(`config/params.yaml is missing consent.current_version (read from ${PARAMS_PATH})`);
  }

  // Whether finishing a ROUND fires confetti, win or lose. A build-time
  // constant like the two above rather than a field on an API response,
  // because round completion is a STATE the panel already knows it has
  // reached -- adding a config echo to `/state` would be plumbing a value
  // through three layers to reach the component that renders it.
  //
  // The cost is the same one `__CONSENT_VERSION__` carries and it is
  // stated here so nobody is surprised: changing this needs
  // `npm run build` plus a Reload, not just a backend restart.
  const confettiOnRoundComplete = Boolean(
    config?.gamification?.confetti_on_round_complete
  );

  // The score a developer has to reach to clear a gate, so the panel can
  // say "clears at 50%" beside the score itself rather than leaving the
  // participant to infer the bar from whether they got through. Same
  // build-time-constant argument as the three above: it is a study
  // parameter the panel only ever DISPLAYS, and threading it through
  // /state to reach one stat card is more plumbing than it is worth.
  //
  // Required, not defaulted: a wrong threshold on screen is worse than a
  // build that refuses to finish, because it tells every participant the
  // wrong thing about whether they are close.
  const passThresholdPct = config?.developer_gate?.pass_threshold_pct;
  if (passThresholdPct === undefined || passThresholdPct === null) {
    throw new Error(`config/params.yaml is missing developer_gate.pass_threshold_pct (read from ${PARAMS_PATH})`);
  }

  // K, the pretend-missed turns the leaderboard adds to each phase before
  // blending (leaderboard.score_confidence_turns). Displayed only, in the
  // score's explanation; required for the same reason as the threshold.
  const confidenceTurns = config?.leaderboard?.score_confidence_turns;
  if (confidenceTurns === undefined || confidenceTurns === null) {
    throw new Error(`config/params.yaml is missing leaderboard.score_confidence_turns (read from ${PARAMS_PATH})`);
  }

  return {
    __BACKEND_BASE_URL__: JSON.stringify(backendBaseUrl),
    __LEADERBOARD_CONFIDENCE_TURNS__: JSON.stringify(confidenceTurns),
    __GATE_PASS_THRESHOLD_PCT__: JSON.stringify(passThresholdPct),
    __CONSENT_VERSION__: JSON.stringify(consentVersion),
    __LANGUAGE_EXTENSIONS__: JSON.stringify(languageExtensionMap(config)),
    __CONFETTI_ON_ROUND_COMPLETE__: JSON.stringify(confettiOnRoundComplete),
  };
}
