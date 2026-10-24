// Builds the extension into extension/dist/, ready to "load unpacked".
//
// Three separate Vite builds, not one multi-entry config, because MV3's
// three surfaces have different module-format rules:
//   - content scripts: MUST be classic (non-module) scripts -- Chrome
//     injects content_scripts entries as plain <script>-equivalents, and
//     will not execute an ES module content script declared in
//     manifest.json. So this must build as an IIFE.
//   - the background service worker CAN be an ES module (manifest.json's
//     background.type = "module"), but there's no benefit to it here
//     (single file, no code-splitting need) -- built as an IIFE too, for
//     the same simplicity as the content script.
//   - the popup is just a small web page loaded in its own tab-like
//     context -- ES modules work fine, and Vite's normal HTML-entry
//     handling (asset hashing, etc) is the right tool there.
//
// config/params.yaml is read here (build time) and the values the
// extension needs before it can talk to the backend at all are inlined as
// globals via esbuild `define` -- see scripts/config_defines.js, which
// vitest.config.js reuses so tests and the shipped bundle can never
// disagree about them.

import { build } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync, writeFileSync, mkdirSync, rmSync, cpSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { load as loadYaml } from "js-yaml";
import { buildDefines, PARAMS_PATH } from "./config_defines.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, ".."); // extension/
const REPO_ROOT = path.resolve(ROOT, ".."); // repo root
const OUT_DIR = path.join(ROOT, "dist");

const config = loadYaml(readFileSync(PARAMS_PATH, "utf8"));
const backendBaseUrl = config?.extension?.backend_base_url;

const define = buildDefines();

rmSync(OUT_DIR, { recursive: true, force: true });
mkdirSync(OUT_DIR, { recursive: true });

async function buildServiceWorker() {
  await build({
    root: ROOT,
    logLevel: "warn",
    define,
    build: {
      outDir: OUT_DIR,
      emptyOutDir: false,
      lib: {
        entry: path.join(ROOT, "src/background/service_worker.js"),
        formats: ["iife"],
        name: "AICodeQuestServiceWorker",
        fileName: () => "background/service_worker.js",
      },
    },
  });
}

async function buildContentScript() {
  await build({
    root: ROOT,
    logLevel: "warn",
    define,
    build: {
      outDir: OUT_DIR,
      emptyOutDir: false,
      lib: {
        entry: path.join(ROOT, "src/content/pr_page.js"),
        formats: ["iife"],
        name: "AICodeQuestContentScript",
        fileName: () => "content/pr_page.js",
        cssFileName: "content/pr_page",
      },
    },
  });
}

async function buildPopup() {
  // A dedicated `root` (src/popup/) so Vite's HTML-entry output lands at
  // dist/popup.html (flat), not nested under a path mirroring the source
  // tree -- simpler for manifest.json's default_popup to reference.
  await build({
    root: path.join(ROOT, "src/popup"),
    logLevel: "warn",
    define,
    build: {
      outDir: OUT_DIR,
      emptyOutDir: false,
      rollupOptions: {
        input: path.join(ROOT, "src/popup/popup.html"),
      },
    },
  });
}

async function buildGame() {
  // The game/lead panel: a React + Monaco page loaded in an IFRAME (see
  // content/gate_ui.js) pointing at chrome-extension://<id>/game.html --
  // NOT inline React injected into github.com's isolated world. An
  // extension page gets its own clean document (no fighting GitHub's
  // CSS), its own CSP, and Monaco's DOM/CSS requirements just work,
  // exactly like the popup. Same dedicated-root trick as buildPopup() so
  // the HTML lands flat at dist/game.html.
  await build({
    root: path.join(ROOT, "src/game"),
    logLevel: "warn",
    define,
    plugins: [react()],
    build: {
      outDir: OUT_DIR,
      emptyOutDir: false,
      rollupOptions: {
        input: path.join(ROOT, "src/game/game.html"),
      },
    },
  });
}

await buildServiceWorker();
await buildContentScript();
await buildPopup();
await buildGame();

// manifest.json's "host_permissions" is NOT hand-duplicated from
// config/params.yaml's extension.backend_base_url -- that would be a
// second place for the URL to drift out of sync. Instead the source
// manifest.json keeps host_permissions as an empty array (a placeholder
// build.js always overwrites), and the backend entry is computed here,
// from the same config value everything else in this build used.
//
// github.com/* is ALSO required as of Phase 9: the service worker fetches
// a PR's .diff URL directly from github.com (see shared/api.js's
// fetchDiff()) -- unlike content-script auto-injection (content_scripts.
// matches alone is enough for that, no host_permissions needed), an
// arbitrary cross-origin fetch from the background context needs an
// explicit host grant. Scoped to github.com only, per the phase 8
// brief's own preference ("host_permissions scoped to github.com over
// broad permissions") -- not <all_urls>.
//
// patch-diff.githubusercontent.com/* is ALSO required -- verified
// directly against a live repo, where a PR's .diff URL 302-redirects
// there and the actual response is served from THAT origin, not
// github.com. Chrome's host_permissions CORS bypass applies per-origin
// per-hop of a redirect chain, so granting github.com/* alone still left
// the redirected-to response blocked by ordinary CORS ("blocked by CORS
// policy: No 'Access-Control-Allow-Origin' header") even though the
// request started on an origin we did have permission for.
// The icon set, copied VERBATIM rather than run through Vite. Vite hashes
// and relocates the assets it processes, and manifest.json's "icons" keys
// have to be literal paths Chrome can resolve -- a hashed filename could
// not be written into the manifest without teaching this script to read
// Vite's output manifest back. A plain copy to a stable dist/icons/ keeps
// the manifest, the popup and the panel all referencing one known path.
//
// Generated from AICodeQuestLogo.png by scripts/make_icons.py, which is
// NOT run here: it needs Pillow, and the extension deliberately has no
// image-processing dependency. The PNGs are committed; re-run that script
// by hand if the logo changes.
const iconsSrc = path.join(ROOT, "icons");
if (!existsSync(iconsSrc)) {
  // A missing icon set is not a warning to scroll past: Chrome falls back
  // to the grey puzzle piece, which is exactly the state this replaced,
  // and the manifest would reference four files that are not there.
  throw new Error(
    `extension/icons/ is missing -- run: python extension/scripts/make_icons.py`
  );
}
cpSync(iconsSrc, path.join(OUT_DIR, "icons"), { recursive: true });

const manifestSrc = path.join(ROOT, "manifest.json");
const manifestDst = path.join(OUT_DIR, "manifest.json");
const manifest = JSON.parse(readFileSync(manifestSrc, "utf8"));
manifest.host_permissions = [
  `${new URL(backendBaseUrl).origin}/*`,
  "https://github.com/*",
  "https://patch-diff.githubusercontent.com/*",
];
writeFileSync(manifestDst, JSON.stringify(manifest, null, 2) + "\n");

console.log(`Built extension to ${OUT_DIR} (extension.backend_base_url=${backendBaseUrl})`);
