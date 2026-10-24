import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BACKEND_DIR = path.resolve(__dirname, "../backend");

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  fullyParallel: false, // one shared persistent-context extension load at a time
  retries: 0,
  reporter: [["list"]],
  // Real, listening Flask server (mongomock + ScriptedLLM + run_locally)
  // for the gate-evaluation and full-playthrough tests -- see
  // backend/scripts/e2e_server.py's module docstring for why
  // page.route() can't substitute for this (it can't intercept the
  // service worker's own fetch() calls, only the page's).
  webServer: {
    command: `${path.join(BACKEND_DIR, "../.venv/bin/python")} scripts/e2e_server.py`,
    cwd: BACKEND_DIR,
    // Must match config/params.yaml's extension.backend_base_url port --
    // e2e_server.py binds the port parsed out of that same URL.
    port: 5055,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
