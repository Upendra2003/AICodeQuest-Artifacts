// Minimal unified-diff parsing for the extension side. Extracts changed
// file paths and a best-effort reconstruction of each file's new content
// (context + added lines only) -- mirrors
// backend/app/services/diff_scope.py's `_reconstruct_new_content`
// exactly, including the SAME documented limitation: only accurate when
// the diff's hunks happen to cover the whole file (a full-file add, or a
// single hunk with full-file context). There is still no PR-ingestion
// pipeline to supply real file contents (the same open question flagged
// in every gate-related phase so far), so this is the extension-side
// equivalent of that same reconstruction, not a new design decision.

// Extension -> language, INLINED AT BUILD TIME from config/params.yaml's
// `languages` table (see scripts/build.js) -- the same table
// backend/app/services/diff_scope.py's _extension_language_map() builds
// from, so the two sides can never disagree about which languages exist.
//
// This was hardcoded here, covering only the 4 languages Phase 2 shipped
// with, and silently went stale when Phase 28 added cpp/rust/html/css to
// config. The consequence was NOT a graceful skip: a .cpp pull request
// got language=null all the way into POST /dev-gate/<pr>/start, which
// handed tree-sitter an empty grammar name and 500'd. The fallback below
// exists only for unit tests, which import this module directly and so
// never see the build's `define`.
const EXT_LANGUAGE_MAP =
  typeof __LANGUAGE_EXTENSIONS__ !== "undefined"
    ? __LANGUAGE_EXTENSIONS__
    : {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".java": "java",
        ".c": "c",
        ".h": "c",
      };

export function detectLanguage(filePath) {
  const dot = filePath.lastIndexOf(".");
  if (dot === -1) return null;
  return EXT_LANGUAGE_MAP[filePath.slice(dot).toLowerCase()] || null;
}

/** @returns {{path: string, language: string|null, newContent: string}[]} */
export function parseUnifiedDiff(diffText) {
  const files = [];
  let current = null;

  for (const line of diffText.split("\n")) {
    const fileMatch = line.match(/^\+\+\+ b\/(.+)$/);
    if (fileMatch) {
      current = { path: fileMatch[1], lines: [] };
      files.push(current);
      continue;
    }
    if (!current) continue;
    if (
      line.startsWith("+++") ||
      line.startsWith("---") ||
      line.startsWith("diff --git") ||
      line.startsWith("index ") ||
      line.startsWith("@@")
    ) {
      continue;
    }
    if (line.startsWith("+")) {
      current.lines.push(line.slice(1));
    } else if (line.startsWith("-")) {
      continue; // removed line -- not part of the new content
    } else if (line.startsWith(" ")) {
      current.lines.push(line.slice(1));
    }
  }

  return files.map((f) => ({
    path: f.path,
    language: detectLanguage(f.path),
    newContent: f.lines.join("\n"),
  }));
}

/**
 * Reconstructs one specific function's source from a diff, given the
 * {file, start_line, end_line} the backend's tree-sitter extraction
 * (app/services/extraction.py, returned by /gate/evaluate's `functions`)
 * already computed authoritatively -- line numbers are 1-indexed,
 * inclusive, and consistent with this module's own newContent
 * reconstruction (both mirror diff_scope.py's _reconstruct_new_content).
 * Returns null if the file isn't in the diff or the range is out of
 * bounds (a stale/mismatched call, not expected in normal use).
 */
export function extractFunctionSource(diffText, file, startLine, endLine) {
  const target = parseUnifiedDiff(diffText).find((f) => f.path === file);
  if (!target) return null;
  const lines = target.newContent.split("\n");
  if (startLine < 1 || endLine > lines.length || startLine > endLine) return null;
  return {
    file: target.path,
    language: target.language,
    source: lines.slice(startLine - 1, endLine).join("\n"),
  };
}

/**
 * The same slice, taken out of a WHOLE file rather than the diff's
 * reconstruction.
 *
 * Worth having as its own function because the two are only accidentally
 * the same. `extractFunctionSource` indexes into hunk fragments stitched
 * together with the gaps removed, so its "line 40" is the 40th line WE
 * kept, not the 40th line of the file. That happens to agree when the
 * pull request ADDED the file (every line is in the diff) and disagrees
 * for every modified one -- which is why a small edit to a large file
 * used to yield a target function sliced from the wrong place.
 *
 * Language is resolved from the path, since a fetched file carries no
 * diff header to read it from.
 */
export function sliceLines(source, file, startLine, endLine) {
  const lines = String(source || "").split("\n");
  if (startLine < 1 || endLine > lines.length || startLine > endLine) return null;
  const language = detectLanguage(file);
  if (!language) return null;
  return { file, language, source: lines.slice(startLine - 1, endLine).join("\n") };
}

/**
 * Picks which of the backend's extracted functions (/gate/evaluate's
 * `functions`, each {file, qualified_name, start_line, end_line, ...})
 * to actually play the gate on -- prefers one overlapping an AI-tagged
 * range (aiTaggedRanges, from tagging_panel.js) over an untagged one, so
 * a multi-file PR plays the AI-authored code the gate fired on, not
 * whatever file happens to come first in diff order. Falls back to the
 * first extracted function if none overlap a tagged range (shouldn't
 * normally happen, since the trigger only fires on AI-tagged effective
 * lines, but a fallback beats silently returning nothing). Returns null
 * if `functions` is empty.
 *
 * This is the fix for a real gap found in live testing (Phase 13): the
 * OLD pickTargetFunction() below ignored the backend's per-function
 * extraction entirely and just grabbed the first changed file's WHOLE
 * reconstructed content, regardless of AI-tagging -- for a real
 * multi-file PR this could (and did) pick an unrelated file.
 */
export function pickAiTaggedFunction(functions, aiTaggedRanges) {
  if (!functions.length) return null;
  const overlapsTag = (f) =>
    aiTaggedRanges.some(
      (t) => t.file_path === f.file && !(t.end_line < f.start_line || t.start_line > f.end_line)
    );
  return functions.find(overlapsTag) || functions[0];
}

/**
 * Picks the first changed file with a recognized language and non-empty
 * reconstructed content, in the shape dev_gate.py's /start endpoint
 * expects: {file, language, source}. Returns null if nothing usable was
 * found (e.g. a diff touching only unsupported-language files).
 *
 * Fallback only, used by main.jsx when the panel wasn't given a specific
 * target function (e.g. /gate/evaluate returned no functions) -- prefer
 * pickAiTaggedFunction() + extractFunctionSource() when a real
 * per-function extraction is available.
 */
export function pickTargetFunction(diffText) {
  const files = parseUnifiedDiff(diffText).filter((f) => f.language && f.newContent.trim());
  if (!files.length) return null;
  const target = files[0];
  return { file: target.path, language: target.language, source: target.newContent };
}

/**
 * Phase 20: EVERY supported-language file in the diff, as
 * {file, language, source} with the file's full new content.
 *
 * The backend extracts individual functions from these (tree-sitter lives
 * there, not here), so a 7-function pull request yields 7 possible turn
 * subjects instead of the single function pickTargetFunction() chooses.
 * Sent alongside that single function, never instead of it -- the chosen
 * target still decides what the FIRST turn asks about.
 */
export function pickAllFiles(diffText, sources = null) {
  return parseUnifiedDiff(diffText)
    .filter((f) => f.language && f.newContent.trim())
    .map((f) => ({
      file: f.path,
      language: f.language,
      // WHOLE FILE when we have it, hunk fragments otherwise.
      //
      // The fragments are what a unified diff actually gives us for a
      // MODIFIED file, and on a pull request of many small edits they
      // contain no complete function at all -- measured on a 12-file diff,
      // tree-sitter extracted zero functions from every one of them, while
      // the gate had already fired and disabled Create. `sources` comes
      // from api.js's fetchFileSources(), which reads the real file at the
      // head commit.
      //
      // The fallback is deliberate rather than lazy: a file that could not
      // be fetched (deleted, binary, private) still contributes whatever
      // the diff showed, which is what the tool did before and is never
      // worse than dropping the file entirely.
      source: (sources && sources[f.path]) || f.newContent,
      // Recorded so the backend and any later analysis can tell a real
      // file apart from a stitched fragment instead of guessing from its
      // shape. Phase 32's is_usable_context() cannot do this reliably --
      // it returns TRUE for concatenated Python fragments, because
      // tree-sitter is error-tolerant and cannot validate indentation.
      source_is_whole_file: Boolean(sources && sources[f.path]),
    }));
}

/** Every supported-language path in the diff -- what to fetch in full. */
export function changedPaths(diffText) {
  return parseUnifiedDiff(diffText)
    .filter((f) => f.language && f.newContent.trim())
    .map((f) => f.path);
}

/**
 * The head commit SHA of a pull request or compare, read off its `.patch`.
 *
 * git format-patch emits one `From <sha> ...` line per commit, in order,
 * so the LAST one is the head commit. Verified against the study's own
 * repository on both URL shapes (`/pull/N.patch` and
 * `/compare/base...head.patch`), because this project does not guess at
 * external behaviour.
 *
 * The `.diff` cannot be used for this: its `index` lines carry BLOB
 * hashes, not commit hashes, and nothing serves a file by blob hash.
 */
export function headShaFromPatch(patchText) {
  const matches = [...String(patchText || "").matchAll(/^From ([0-9a-f]{40})\b/gm)];
  return matches.length ? matches[matches.length - 1][1] : null;
}

// Unified diff hunk header: "@@ -oldStart[,oldLines] +newStart[,newLines] @@"
const HUNK_HEADER_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

/**
 * Per-hunk breakdown for the AI-authorship tagging panel (content/
 * tagging_panel.js): one entry per hunk, in NEW-file line-number terms --
 * exactly the {file_path, start_line, end_line} shape
 * app/services/diff_scope.py's AiTaggedRange model expects, plus a short
 * `preview` string for the UI (first non-blank added line, truncated).
 *
 * @returns {{path: string, hunks: {startLine: number, endLine: number, preview: string}[]}[]}
 */
export function parseHunks(diffText) {
  const files = [];
  let currentFile = null;
  let currentHunk = null;

  for (const line of diffText.split("\n")) {
    const fileMatch = line.match(/^\+\+\+ b\/(.+)$/);
    if (fileMatch) {
      currentFile = { path: fileMatch[1], hunks: [] };
      files.push(currentFile);
      currentHunk = null;
      continue;
    }
    if (!currentFile) continue;

    const hunkMatch = line.match(HUNK_HEADER_RE);
    if (hunkMatch) {
      const newStart = Number(hunkMatch[3]);
      const newLines = hunkMatch[4] !== undefined ? Number(hunkMatch[4]) : 1;
      currentHunk = {
        startLine: newStart,
        endLine: newStart + Math.max(newLines - 1, 0),
        addedLines: [],
      };
      currentFile.hunks.push(currentHunk);
      continue;
    }
    if (!currentHunk) continue;
    if (line.startsWith("+") && !line.startsWith("+++")) {
      currentHunk.addedLines.push(line.slice(1));
    }
  }

  return files
    .filter((f) => f.hunks.length > 0)
    .map((f) => ({
      path: f.path,
      hunks: f.hunks.map((h) => ({
        startLine: h.startLine,
        endLine: h.endLine,
        preview: (h.addedLines.find((l) => l.trim().length > 0) || "(no added lines)").trim().slice(0, 80),
      })),
    }));
}
