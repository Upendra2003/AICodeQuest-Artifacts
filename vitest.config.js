import { defineConfig } from "vitest/config";
import { buildDefines } from "./scripts/config_defines.js";

export default defineConfig({
  // The SAME build-time constants scripts/build.js inlines into the
  // shipped bundle. Without this, a module under test silently fell back
  // to its own hardcoded defaults -- which is how content/diff_utils.js's
  // extension->language map stayed on Phase 2's four languages, passed
  // its unit tests, and then sent language=null to the backend for a
  // .cpp pull request.
  define: buildDefines(),
  test: {
    environment: "node",
    include: ["tests/unit/**/*.test.js"],
  },
});
