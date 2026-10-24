// The AI-authorship self-tagging panel required by the Phase 2 backend
// contract (/gate/evaluate's `ai_tagged_ranges` field is mandatory, even
// if empty -- see app/api/gate.py's module docstring: "the study always
// captures an explicit developer self-tag rather than silently defaulting
// to nothing is AI-generated"). Nothing about the gate proceeds until
// every hunk is tagged.
//
// Deliberately vanilla DOM, not React -- this sits directly in
// github.com's page (isolated world), on the critical path before the
// developer can even see whether the gate fires. Pulling in React here
// would be dead weight for ~10 lines of markup and would slow down
// exactly the interaction the phase brief calls out as dropout-risk
// friction. React only shows up inside the iframe (src/game/), a
// separate document that doesn't share this budget.
//
// Phase 24 restyles it to match the panel (src/game/ui.css): the same
// card, radii, brand colour and dark treatment, because this is the FIRST
// thing a participant sees and it previously looked like a different,
// older tool than the game it leads into. One behavioural addition aimed
// at friction rather than the measure: a progress counter.
//
// Phase 36 restores bulk tagging PER FILE, at the study lead's request,
// after a real pull request of 52 changed files made per-hunk radios a
// dropout wall. The Phase 29 objection still stands and shaped the form
// it came back in: a whole-diff "all of it" is what someone clicks to
// DISMISS a dialog, so there is no whole-diff button. A file is the unit
// people actually remember authoring, so the bulk action is per file --
// 52 judgements instead of 200, but still a judgement per file rather
// than one for everything.
//
// The measurement risk does not vanish, it becomes VISIBLE: every answer
// records how it was given (`per_hunk` / `per_file` / `mixed`), so an
// analysis can check whether bulk-tagged pull requests behave differently
// instead of the question being unanswerable. Same discipline as Phase
// 33's grading_method.

export const TAGGING_STYLE_ID = "aicodequest-tagging-style";
/** Addressed by tests and the e2e helper -- see the note at its creation. */
export const CONTINUE_BUTTON_ID = "aicodequest-tag-continue";

// Kept in sync by hand with src/game/ui.css's tokens. Not imported from
// it: this file is injected into github.com, where the panel's stylesheet
// is not loaded, and a build-time CSS import into a content script would
// pull the whole design system into the page for one small card.
function ensureStyle(doc) {
  if (doc.getElementById(TAGGING_STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = TAGGING_STYLE_ID;
  style.textContent = `
    .aicodequest-tagging-panel {
      --aq-surface: #ffffff; --aq-surface-2: #faf9f5; --aq-surface-3: #ecece4;
      --aq-border: #e0e0d6; --aq-text: #13343b; --aq-muted: #5c7379;
      --aq-brand: #114f56; --aq-brand-soft: #e6f2f3; --aq-brand-border: #b9d9dd;
      --aq-success: #0e7d8c; --aq-success-soft: #e2f6f9;
      position: fixed; bottom: 16px; right: 16px; z-index: 2147483647;
      width: 500px; max-width: calc(100vw - 32px); max-height: 76vh;
      display: flex; flex-direction: column; overflow: hidden;
      border: 1px solid var(--aq-border); border-radius: 14px;
      background: var(--aq-surface); color: var(--aq-text);
      box-shadow: 0 18px 48px rgba(19,52,59,.22);
      font: 13px/1.5 "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    }
    /* Two dark signals, because they disagree in practice: the OS
       preference, and GitHub's own theme switch (it sets data-color-mode
       on <html>, and a participant can run GitHub dark on a light OS).
       Following only the first left a white card glued to a dark page. */
    @media (prefers-color-scheme: dark) {
      .aicodequest-tagging-panel { --aq-surface: #13343b; --aq-surface-2: #173d45; --aq-surface-3: #1d4a53; --aq-border: #235058; --aq-text: #f3f3ee; --aq-muted: #9fbabf; --aq-brand: #1fb8cd; --aq-brand-soft: #0e3a42; --aq-brand-border: #1c5e69; --aq-success: #1fb8cd; --aq-success-soft: #0d3b43; box-shadow: 0 18px 48px rgba(0,0,0,.5); }
    }
    :root[data-color-mode="dark"] .aicodequest-tagging-panel { --aq-surface: #13343b; --aq-surface-2: #173d45; --aq-surface-3: #1d4a53; --aq-border: #235058; --aq-text: #f3f3ee; --aq-muted: #9fbabf; --aq-brand: #1fb8cd; --aq-brand-soft: #0e3a42; --aq-brand-border: #1c5e69; --aq-success: #1fb8cd; --aq-success-soft: #0d3b43; box-shadow: 0 18px 48px rgba(0,0,0,.5); }
    /* data-color-mode="auto" means GitHub itself follows the OS, so this
       must ALSO be gated on the OS preference. Matching the attribute
       alone forced a dark card onto a light page -- and "auto" is
       GitHub's default, so that was the common case, not the edge one.
       Caught by reading a real page's <html> tag. */
    @media (prefers-color-scheme: dark) {
      :root[data-color-mode="auto"] .aicodequest-tagging-panel { --aq-surface: #13343b; --aq-surface-2: #173d45; --aq-surface-3: #1d4a53; --aq-border: #235058; --aq-text: #f3f3ee; --aq-muted: #9fbabf; --aq-brand: #1fb8cd; --aq-brand-soft: #0e3a42; --aq-brand-border: #1c5e69; --aq-success: #1fb8cd; --aq-success-soft: #0d3b43; box-shadow: 0 18px 48px rgba(0,0,0,.5); }
    }
    .aicodequest-tagging-panel * { box-sizing: border-box; }
    .aq-tag-head {
      display: flex; align-items: center; gap: 8px;
      padding: 14px 16px; border-bottom: 1px solid var(--aq-border);
    }
    .aq-tag-brand { display: inline-flex; align-items: center; gap: 7px; font-weight: 700; font-size: 14px; }
    .aq-tag-brand svg { width: 16px; height: 16px; color: var(--aq-brand); }
    .aq-tag-brand span { font-weight: 500; }
    .aq-tag-count {
      margin-left: auto; padding: 3px 10px; border-radius: 999px;
      background: var(--aq-brand-soft); color: var(--aq-brand);
      font-size: 12px; font-weight: 600; white-space: nowrap;
    }
    .aq-tag-count[data-complete="true"] { background: var(--aq-success-soft); color: var(--aq-success); }
    .aq-tag-lede { padding: 12px 16px 0; margin: 0; }
    .aq-tag-lede strong { display: block; margin-bottom: 2px; font-size: 13.5px; }
    .aq-tag-lede span { color: var(--aq-muted); font-size: 12.5px; }
    .aq-tag-list { flex: 1 1 auto; overflow-y: auto; padding: 12px 16px; }
    .aq-tag-row {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
      padding: 10px 12px; margin-bottom: 8px;
      border: 1px solid var(--aq-border); border-radius: 10px; background: var(--aq-surface-2);
    }
    .aq-tag-row:last-child { margin-bottom: 0; }
    /* Phase 36: one header per file, carrying the per-file bulk actions.
       The rows beneath it are indented so the grouping reads without a
       second border. */
    .aq-tag-filehead {
      display: flex; align-items: center; justify-content: space-between;
      gap: 10px; flex-wrap: wrap; margin: 12px 0 6px;
    }
    .aq-tag-filehead:first-child { margin-top: 0; }
    .aq-tag-filehead .aq-tag-file { font-size: 12.5px; font-weight: 600; }
    .aq-tag-fileactions { display: flex; align-items: center; gap: 6px; }
    .aq-tag-filecount { color: var(--aq-muted); font-size: 11.5px; }
    .aq-tag-btn--bulk {
      padding: 3px 10px; font-size: 11.5px; border-radius: 999px;
      background: var(--aq-surface); color: var(--aq-text);
      border: 1px solid var(--aq-border); cursor: pointer;
    }
    .aq-tag-btn--bulk:hover { border-color: var(--aq-brand); }
    .aq-tag-where { flex: 1 1 190px; min-width: 0; }
    .aq-tag-file {
      display: block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .aq-tag-preview {
      display: block; color: var(--aq-muted); font-size: 11.5px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .aq-tag-choice { display: flex; gap: 6px; flex: 0 0 auto; }
    .aq-tag-opt {
      display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
      padding: 5px 11px; border: 1px solid var(--aq-border); border-radius: 999px;
      background: var(--aq-surface); color: var(--aq-muted);
      font-size: 12px; font-weight: 500; white-space: nowrap; user-select: none;
    }
    .aq-tag-opt:hover { background: var(--aq-surface-3); color: var(--aq-text); }
    .aq-tag-opt input { margin: 0; accent-color: var(--aq-brand); }
    .aq-tag-opt:has(input:checked) {
      border-color: var(--aq-brand-border); background: var(--aq-brand-soft);
      color: var(--aq-brand); font-weight: 600;
    }
    .aq-tag-opt:has(input:focus-visible) { outline: 2px solid var(--aq-brand); outline-offset: 1px; }
    .aq-tag-foot {
      display: flex; flex-direction: row-reverse; align-items: center; gap: 8px;
      padding: 12px 16px; border-top: 1px solid var(--aq-border); background: var(--aq-surface);
    }
    .aq-tag-btn {
      padding: 8px 16px; border: 1px solid transparent; border-radius: 7px;
      font: inherit; font-weight: 600; cursor: pointer;
    }
    .aq-tag-btn--primary { background: var(--aq-success); color: #fff; }
    .aq-tag-btn--primary:disabled { opacity: .5; cursor: default; }
    .aq-tag-btn--ghost {
      background: none; border-color: var(--aq-border); color: var(--aq-muted); font-weight: 500;
      padding: 7px 12px; font-size: 12px;
    }
    .aq-tag-btn--ghost:hover { background: var(--aq-surface-3); color: var(--aq-text); }
    .aq-tag-spacer { margin-right: auto; }
  `;
  doc.head.appendChild(style);
}

// Feather "zap", the same mark the panel's brand uses (src/game/Shell.jsx).
const BRAND_MARK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>';

/**
 * Renders the tagging panel into `container` (an element already
 * inserted into the page). Resolves with `ai_tagged_ranges` -- the
 * {file_path, start_line, end_line}[] shape app/services/diff_scope.py's
 * AiTaggedRange expects -- once every hunk has been tagged and the user
 * clicks Continue. The panel removes itself from `container` on
 * completion.
 *
 * @param {HTMLElement} container
 * @param {{path: string, hunks: {startLine: number, endLine: number, preview: string}[]}[]} filesWithHunks
 * @returns {Promise<{file_path: string, start_line: number, end_line: number}[]>}
 */
export function renderTaggingPanel(container, filesWithHunks) {
  return new Promise((resolve) => {
    const doc = container.ownerDocument;
    const allHunks = [];
    for (const file of filesWithHunks) {
      for (const hunk of file.hunks) {
        allHunks.push({ path: file.path, ...hunk, tag: null });
      }
    }

    ensureStyle(doc);

    const root = doc.createElement("div");
    root.className = "aicodequest-tagging-panel";
    root.setAttribute("role", "region");
    root.setAttribute("aria-label", "AICodeQuest: tag AI-generated changes");

    const head = doc.createElement("div");
    head.className = "aq-tag-head";
    const brand = doc.createElement("div");
    brand.className = "aq-tag-brand";
    brand.innerHTML = `${BRAND_MARK}<span>AI </span>CodeQuest`;
    const counter = doc.createElement("div");
    counter.className = "aq-tag-count";
    counter.setAttribute("role", "status");
    counter.setAttribute("aria-live", "polite");
    head.append(brand, counter);

    const lede = doc.createElement("p");
    lede.className = "aq-tag-lede";
    lede.innerHTML =
      "<strong>Which of these changes did AI write?</strong>" +
      "<span>Your answer is part of the study — it is recorded as you give it, and it decides which code the challenge is built from.</span>";

    const list = doc.createElement("div");
    list.className = "aq-tag-list";

    // Continue carries a STABLE ID rather than relying on DOM order.
    //
    // It used to be addressed as `panel button` / `querySelector("button")`
    // on the promise that it stayed the first <button> in the panel --
    // which Phase 36's per-file bulk buttons broke the moment they were
    // added, since those sit in the list ABOVE the footer. An identifier is
    // what a contract should have been in the first place; the ordering
    // promise was only ever true by accident.
    const foot = doc.createElement("div");
    foot.className = "aq-tag-foot";

    const continueBtn = doc.createElement("button");
    continueBtn.id = CONTINUE_BUTTON_ID;
    continueBtn.type = "button";
    continueBtn.className = "aq-tag-btn aq-tag-btn--primary";
    continueBtn.textContent = "Continue";
    continueBtn.disabled = true;
    continueBtn.setAttribute("aria-label", "Continue past AI-authorship tagging");
    foot.appendChild(continueBtn);

    // Phase 29: the "All mine" / "All AI" bulk buttons were REMOVED at the
    // study lead's request. They were added to reduce friction, but this
    // answer is a study MEASURE -- which code the challenge is built from --
    // and a one-click "all of it" is exactly the answer someone gives to
    // dismiss a dialog rather than to report what they did. Per-hunk radios
    // are now the only way to answer, which is slower and more honest.

    /** How this answer was given -- a study measure in its own right. */
    function taggingMethod() {
      if (bulkFiles.size === 0) return "per_hunk";
      return bulkFiles.size === byFile.size ? "per_file" : "mixed";
    }

    const tagged = () => allHunks.filter((h) => h.tag !== null).length;
    const allTagged = () => tagged() === allHunks.length;

    function refresh() {
      const done = tagged();
      counter.textContent = `${done} of ${allHunks.length} tagged`;
      counter.dataset.complete = String(done === allHunks.length);
      continueBtn.disabled = !allTagged();
    }

    function renderRow(hunk, index) {
      const row = doc.createElement("div");
      row.className = "aq-tag-row";

      const where = doc.createElement("div");
      where.className = "aq-tag-where";
      const file = doc.createElement("code");
      file.className = "aq-tag-file";
      file.textContent = hunk.path;
      const preview = doc.createElement("code");
      preview.className = "aq-tag-preview";
      preview.textContent = `:${hunk.startLine}-${hunk.endLine}  ${hunk.preview}`;
      where.append(file, preview);
      row.appendChild(where);

      const choice = doc.createElement("div");
      choice.className = "aq-tag-choice";
      const groupName = `aicodequest-tag-${index}`;
      // Option ORDER is also a contract: the unit test indexes radios as
      // [hunk0-ai, hunk0-human, hunk1-ai, ...].
      for (const [value, text] of [
        ["ai", "AI wrote it"],
        ["human", "I wrote it"],
      ]) {
        const optionLabel = doc.createElement("label");
        optionLabel.className = "aq-tag-opt";

        const input = doc.createElement("input");
        input.type = "radio";
        input.name = groupName;
        input.value = value;
        input.addEventListener("change", () => {
          hunk.tag = value;
          refresh();
        });

        optionLabel.appendChild(input);
        optionLabel.appendChild(doc.createTextNode(text));
        choice.appendChild(optionLabel);
      }
      row.appendChild(choice);

      return row;
    }

    // Which files were answered with a bulk button rather than hunk by
    // hunk. Recorded, not prevented -- see the module docstring.
    const bulkFiles = new Set();

    function renderFileHeader(path, hunks) {
      const header = doc.createElement("div");
      header.className = "aq-tag-filehead";

      const name = doc.createElement("code");
      name.className = "aq-tag-file";
      name.textContent = path;

      const actions = doc.createElement("div");
      actions.className = "aq-tag-fileactions";
      const count = doc.createElement("span");
      count.className = "aq-tag-filecount";
      count.textContent = hunks.length === 1 ? "1 change" : `${hunks.length} changes`;
      actions.appendChild(count);

      // Only worth offering where it saves anything. A single-hunk file
      // has nothing to bulk-apply, and showing the buttons anyway would
      // train the click before it is ever needed.
      if (hunks.length > 1) {
        for (const [value, text] of [
          ["ai", "All AI"],
          ["human", "All mine"],
        ]) {
          const btn = doc.createElement("button");
          btn.type = "button";
          btn.className = "aq-tag-btn aq-tag-btn--bulk";
          btn.dataset.bulk = value;
          btn.dataset.file = path;
          btn.textContent = text;
          btn.addEventListener("click", () => {
            for (const hunk of hunks) {
              hunk.tag = value;
              const radio = list.querySelector(
                `input[name="aicodequest-tag-${hunk.index}"][value="${value}"]`
              );
              if (radio) radio.checked = true;
            }
            bulkFiles.add(path);
            refresh();
          });
          actions.appendChild(btn);
        }
      }

      header.append(name, actions);
      return header;
    }

    // Index BEFORE grouping: the radio group names (and the unit test that
    // indexes them) follow the flat hunk order, not the grouped one.
    allHunks.forEach((hunk, index) => {
      hunk.index = index;
    });
    const byFile = new Map();
    for (const hunk of allHunks) {
      if (!byFile.has(hunk.path)) byFile.set(hunk.path, []);
      byFile.get(hunk.path).push(hunk);
    }
    for (const [path, hunks] of byFile) {
      list.appendChild(renderFileHeader(path, hunks));
      for (const hunk of hunks) list.appendChild(renderRow(hunk, hunk.index));
    }

    continueBtn.addEventListener("click", () => {
      if (!allTagged()) return; // belt-and-suspenders; button is disabled anyway
      const aiTaggedRanges = allHunks
        .filter((h) => h.tag === "ai")
        .map((h) => ({ file_path: h.path, start_line: h.startLine, end_line: h.endLine }));
      root.remove();
      resolve({ ranges: aiTaggedRanges, tagging_method: taggingMethod() });
    });

    // No hunks at all (e.g. a diff touching only unsupported-language
    // files) -- nothing to tag, so nothing blocks progression either.
    if (allHunks.length === 0) {
      resolve({ ranges: [], tagging_method: "none" });
      return;
    }

    refresh();
    root.append(head, lede, list, foot);
    container.appendChild(root);
  });
}
