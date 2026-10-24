# Config Changelog

Every change to `config/params.yaml` is recorded here with a date and reason.
CONFIG_VERSION is computed automatically (short sha256 of the canonicalised
config, see backend/app/core/config.py) — it is not hand-assigned, but every
entry below should record the version that resulted from that change so
telemetry events can be traced back to a specific changelog entry.

This log is study-critical — do not skip entries.

## 2026-07-30 — CONFIG_VERSION d142ea210c0e

Initial defaults, unvalidated. Populated all Phase 0 knobs: trigger,
developer_gate, lead_gate, execution, llm, prefetch. Values are placeholders
based on typical ranges, not yet validated against real repos/LLM calls —
see reports/phase-0-review.md open questions.

## 2026-07-30 — CONFIG_VERSION 6bff1fa835a1

Added `auth` section for Phase 1 (jwt_access_token_expires_min,
argon2_time_cost, argon2_memory_cost_kib, argon2_parallelism). Values are
argon2-cffi's own recommended defaults plus a 60-minute token lifetime,
not yet validated against real study session lengths — see
reports/phase-1-review.md open questions.

## 2026-07-30 — CONFIG_VERSION da6892a950f9

Added to `trigger` for Phase 2: module_grouping_depth, ignore_globs,
generated_file_markers, generated_marker_scan_lines. ignore_globs/markers
are a starting placeholder list (common lockfiles + generic "generated"
banners), not yet validated against the actual study repos — see
reports/phase-2-review.md open questions.

## 2026-07-30 — CONFIG_VERSION a4ad0cf40d1c

Added to `execution` for Phase 3: max_processes_and_threads (fork-bomb
containment), poll_interval_sec/poll_timeout_buffer_sec (submission
polling, since wait=true is never used), max_infra_error_retries/
infra_error_retry_backoff_sec (retry policy for infra_error only). These
values also drive infra/judge0/judge0.conf via
infra/judge0/render_conf.py, so Judge0's own limits and our client's
config never drift apart — see reports/phase-3-review.md.

## 2026-07-30 — CONFIG_VERSION 90e409b62c80

Added `execution.language_name_patterns` for Phase 3: substrings matched
against a live Judge0 GET /languages response to resolve C/Python/
JavaScript/Java language_ids dynamically, never hardcoded. Verified
against a live Judge0 v1.13.1 instance's actual /languages output.

## 2026-07-30 — CONFIG_VERSION 0daaacc893c8

Phase 4. Changed `llm.provider` from the Phase 0 placeholder `groq`
(never implemented/tested, no API key available) to `ollama`, since a
local Ollama server with several models was found running in the dev
environment — this lets the LLM client be built and verified against a
real model instead of staying an untested assumption. Updated
`llm.model` to `qwen2.5:7b-instruct` and added `llm.ollama_base_url`,
`llm.opponent_model` (`mistral:latest` — a different model family, per
the leaderboard construct-validity concern), `llm.json_validation_max_retries`.
Added new sections `perturbation` (max_regen_attempts), `testgen`
(max_generated_cases, mutation_kill_set_size), `quizgen`
(self_consistency_k, self_consistency_temperature). See
reports/phase-4-review.md.

## 2026-07-30 — CONFIG_VERSION 9b4d5d5ba7ce

Phase 5. Added `developer_gate.max_replays` and
`developer_gate.attack_counts_toward_score` (default false). Note:
`developer_gate.max_quiz_tiers` (Phase 0) is now superseded by
`max_replays` for the actual gate state machine — Phase 5's locked design
is Defense/Attack turns with a scaffolded replay, not a quiz, which
Phase 0/1 assumed before this was specified. Kept rather than removed;
see reports/phase-5-review.md open questions.

## 2026-07-30 — CONFIG_VERSION 5257d3e1e5df

Phase 6. Added `prefetch.max_wait_ms`/`opponent_warmup_enabled`, new
sections `workers` (max_retries, retry_backoff_sec, result_ttl_sec,
job_timeout_sec — verified against real RQ/Redis behavior, not assumed)
and `latency_budget` (task_render/defense_verdict/attack_opponent/
nfr_scoring p95 targets, per the phase brief's suggested starting values
-- to be validated against the demo workload, not assumed correct). See
reports/phase-6-review.md.

## 2026-07-30 — CONFIG_VERSION 714876f3bf57

Phase 7. Added to `lead_gate`: `max_question_regen_attempts` (diff-groundedness
regeneration cap), `max_retries` (mirrors `developer_gate.max_replays` for
the Lead gate's own retry-then-escalate cap), `k_fold_scoring` and
`score_variance_flag_band` (scoring-reliability self-check -- flag a
response for researcher review when repeated scoring passes disagree by
more than this many points), `calibration_tolerance_pct` (regression-test
tolerance for the hand-scored exemplar set), `rater_agreement_sample_size`
(default N for `backend/scripts/rater_agreement.py`'s blinded export). All
are new starting values per the phase brief, not yet validated against
real human-rater data -- see reports/phase-7-review.md.

## 2026-07-31 — CONFIG_VERSION bcb9bc45a5d1

Phase 8. Added new section `extension` (`backend_base_url`), read at
BUILD time by `extension/vite.config.js` (not fetched at runtime -- the
extension needs a base URL before it can fetch anything). Defaults to
`http://localhost:5000` (Flask's own default `flask run` port) -- the
backend has never actually been served outside `flask test_client()`
until now, so this is a reasonable default, not an established
convention. See reports/phase-8-review.md.

## 2026-07-31 — CONFIG_VERSION 6c2b96578081

Phase 10. Added two new sections. `github_app` (`app_jwt_expiry_sec`,
`installation_token_refresh_buffer_sec`, `api_base_url`,
`check_name_developer`, `check_name_lead`, `details_url_template`) --
everything actually secret (App ID, private key, webhook secret) is an
environment variable, never here, since this file is committed; see
docs/GITHUB-APP-SETUP.md. `enforcement` (`mode`, default `shadow`) is the
between-subjects study condition controlling whether a comprehension-gate
check run can ever actually block a merge -- see
reports/phase-10-review.md for why `advisory` and `enforced` are
byte-for-byte identical from this app's own code (the difference is an
org-side branch-protection setting this app has no permission to
configure or verify).

## 2026-07-31 — CONFIG_VERSION 763351bd71b5

Phase 11. Added `leaderboard` (`tiers` -- banded thresholds on the
rate-limited public total_gates_cleared count, never a raw score;
`max_completions_per_window`/`rate_limit_window_hours` -- anti-gaming cap
on how many gate completions count toward PUBLIC standings, guarding
against the caller-supplied-pr_id gaming vector flagged since Phase 5/7;
`reconciliation_drift_tolerance` -- how much a stored aggregate may
differ from a from-scratch recomputation before the nightly
reconciliation job logs it as real drift) and `badges` (a list of
`{id, name, tier, metric, op, value}` -- config-driven so a new badge
never needs a code change; `metric` must be a key
`app/services/leaderboard.py`'s `compute_raw_metrics()` produces, `op` is
one of a small fixed set, deliberately not a general expression
language). See reports/phase-11-review.md.

## 2026-08-01 — CONFIG_VERSION 35bd3ac9f784

Phase 12. Added `data` (`retain_source_snippets` -- when false,
`app/services/minimization.py` replaces raw source/diff text with
normalised AST fingerprints or content hashes once a session reaches a
terminal state, not at write time, since live turns still need real,
runnable code; `redaction.enabled`/`redaction.ticket_id_pattern` -- the
comment-scoped email/URL/ticket-id strip `app/services/redaction.py`
applies to every outbound LLM prompt regardless of the flag above, since
that governs what a third-party provider sees over the wire, not Mongo
persistence), `consent` (`current_version` -- a participant consent
submission naming any other version is rejected, so nobody is silently
grandfathered into an old consent text; `withdrawal_categories` -- the
fixed set `POST /consent/withdraw` accepts), and `study`
(`practice_mode_enabled` -- whether `start_gate()`'s `is_practice` flag is
honored at all; practice sessions are excluded from
`backend/scripts/export_study_data.py`'s analysis dataset). See
reports/phase-12-review.md.

## 2026-08-01 — CONFIG_VERSION 91d72d481d54

Phase 13. Added `observability` (`queue_depth_alert_threshold`,
`p95_check_window_minutes` -- both read by the new
`backend/scripts/check_alerts.py`, cron-able during the study, which
posts to `AICODEQUEST_ALERT_WEBHOOK_URL` (an env var, not committed, same
pattern as `JWT_SECRET_KEY`/`GITHUB_WEBHOOK_SECRET`) when current RQ
queue depth or recent p95 request latency breaches these). See
reports/phase-13-review.md.

## Phase 19 (2026-08-02)

### Added

- `perturbation.accept_unverified_when_execution_unavailable` (bool, default
  `true`) — serve an LLM-injected bug that could not be execution-verified
  rather than failing the turn. Verification requires a runnable harness,
  which is impossible for void / stdin-driven / context-dependent functions
  (i.e. much real code); failing meant the participant was served an
  unrelated Python substitute instead of their own code. Set `false` to
  restore strict Phase 4 behaviour.
- `grading.structural_equivalence_threshold` (float, default `0.98`) —
  normalized-token similarity between a submitted fix and the original,
  pre-perturbation function above which the fix passes without consulting
  the LLM.
- `grading.llm_judge_enabled` (bool, default `true`) — ask the LLM whether a
  structurally-different fix is nonetheless semantically equivalent, so a
  developer who fixed the bug a different but correct way is not failed.
- `grading.llm_judge_temperature` (float, default `0.0`) — temperature for
  that judgement; 0 because it is a classification that decides whether a
  participant passes.

### Changed (same day, Phase 20)

- **Removed** `grading.structural_equivalence_threshold`. It was unsound as a
  pass criterion: an injected bug is 1-2 tokens, so in a 20-line function the
  BUGGY version is ~99% similar to the original. Measured on a real
  participant function, `count++` -> `count--` scores 0.9921, and the 0.98
  threshold **passed the untouched buggy code**. Grading is now exact
  normalized match (pass), match-against-the-buggy-version (fail, "you
  changed nothing"), or LLM equivalence judge. Similarity is reported for
  diagnostics only and decides nothing.

## Phase 21 (2026-08-03)

Driven by a real Java playthrough: 27-70s per question, no visible test
cases, and an unwinnable Attack phase.

### Added

- `prefetch.inline_wait_ms` (45000) — how long `/current-task` waits for an
  ALREADY-RUNNING background generation before regenerating the same turn
  inline in the request thread. Only applies when no fallback rung matches
  the session's language (so the alternative really is a 27-70s inline
  generation) and only while a worker is actually consuming the queue.
  0 restores the previous behaviour.
- `testgen.min_surviving_cases` (10) — keep asking for more candidate cases,
  up to `perturbation.max_regen_attempts` rounds, until at least this many
  have survived validation against the original implementation. Cases now
  accumulate across attempts instead of each batch replacing the last.
  Participants are shown the suite they are graded against and asked for a
  substantial one; the cost is paid in the background prefetch worker.

### Changed

- `prefetch.lookahead_count` 1 -> 2. Generating one real (non-Python) turn
  measured 27-70s against the local Ollama model — longer than a
  participant spends on the question in front of them, so a single turn of
  lookahead never caught up.
- `testgen.max_generated_cases` 8 -> 16, to feed `min_surviving_cases`.

### Not a config change, but the reason the above matters

`prefetch` had never actually run: RQ pickles a function argument by import
path, and the dev server's runner pickled as `support.local_executor.
run_locally`, which the worker container cannot import. 100% of prefetch
jobs died at deserialization. Jobs now carry a runner NAME
(`app/workers/runners.py`). See reports/phase-21-review.md.

## 2026-08-03 — CONFIG_VERSION 65d53dfe7abf

### Added

- `developer_gate.manual_restart_enabled` (true) — whether the participant
  may abandon a playthrough and start the gate over from turn 1
  (`POST /dev-gate/<pr_id>/restart`, the panel's "Start over" button).
  Reported from real use: a panel opened on a pull request mid-game resumes
  at whatever turn the session left off at, and there was no route back to
  the beginning — the entire session lives in Mongo, so reopening the panel,
  hard-refreshing the page and re-tagging the diff all resume identically.
  Set false for a study arm that must not let participants reroll. The
  restart deliberately KEEPS `used_perturbation_signatures`, so a restarted
  round draws fresh perturbations rather than replaying ones the participant
  has already been shown the answer to. No cap on restart count — see the
  open question in reports/phase-22-review.md.

## 2026-08-03 — CONFIG_VERSION d953f1155284

### Added

- `prefetch.max_fallback_upgrade_attempts` (2) — how many times a turn that
  was served by a FALLBACK rung (static bank or template-only) may be
  discarded and regenerated from the participant's own code on a later
  `/current-task`.

  Reported from real play — *"the template is coming instead of the code"*,
  *"the defense is using template code instead of diff code"* — and confirmed
  from the live database: a round-2 A1 defense turn fell to `template_only`
  ONCE, was persisted as an ordinary attempt with
  `function_ref: static_bank_a1.py`, and from then on every `/current-task`
  for that turn hit the `cache` rung and re-served the stand-in. The ladder
  never reconsidered a turn it had already answered, so one transient
  generation failure permanently replaced the participant's own code with an
  unrelated Python snippet — which defeats the gate, since a comprehension
  check about code from nowhere near the pull request measures nothing.

  Capped because generation genuinely cannot succeed for some functions;
  without a cap every panel open would pay the full failed attempt again.
  Only spent while an RQ worker is alive, and never on a turn the
  participant has already submitted against. 0 restores the previous
  never-reconsider behaviour.

### Not a config change, but found in the same session

`_wait_for_prefetch`'s worker check read `Queue.workers`, which does not
exist in rq 2.x — it raised into a broad `except` on every call, so the
ladder always behaved as though the queue were dead and never waited for the
worker already generating the turn. The obvious repair
(`Worker.count(queue=...)`) is also wrong: both read the `rq:workers`
registry SET, which RQ populates once at worker startup and never
repopulates, so any FLUSHDB against the shared dev Redis makes a live worker
invisible for the rest of its life. Replaced with
`app/workers/queue.any_worker_alive()`, which checks the heartbeating
`rq:worker:*` keys directly. See reports/phase-22-review.md.

## 2026-08-05 — Phase 24: generation latency

### Added

- **`llm.keep_alive`** (`"30m"`): passed straight through as Ollama's
  `keep_alive` request field, which decides how long the model stays
  resident in VRAM. Ollama's own default is 5 minutes — shorter than a
  participant takes to read a single turn, so `/api/ps` was reporting no
  resident model at all and every turn paid a cold load (measured on the
  study machine's L40: 2.5s cold vs 0.3s warm, per call).

### Not a config change, but found and fixed in the same session

- The running `worker` container had **no `OLLAMA_BASE_URL`** — it predated
  the line in `infra/docker-compose.yml` that sets it and had never been
  recreated. Inside the container `localhost:11434` is the container, so
  every generation job that needed the model died with `Connection refused`,
  nothing was ever prefetched, and each turn fell through to inline
  generation with `prefetch.inline_wait_ms` (45s) of budget. Worker logs
  showed this still happening on 2026-08-05. `backend` had the same gap and
  now sets the variable too.

- **Code is no longer requested inside a JSON string.** `_llm_perturb` and
  the Attack opponent both asked for `{"perturbed_code": "..."}` /
  `{"fixed_code": "..."}` via `format:"json"`. Measured against this
  study's own Ollama on a real participant function: JSON mode took 10–15s
  and produced code that failed to parse 3/3 (a dropped quote, or the
  doubled backslash participants saw as a literal `\n` in the editor);
  the identical prompt as a fenced code block took 2.7s and parsed 5/5.
  `llm.json_validation_max_retries` now also caps the retries on that
  plain-text path.

- **Regeneration attempts were a no-op.** `llm_cache` is keyed on
  (prompt, model, temperature), and `_llm_perturb`'s prompt did not vary
  by attempt — so attempts 2 and 3 were served the identical cached
  response the structural check had just rejected. Retries now carry a
  cache-busting nonce.

- **`prefetch.prefer_real_turn_over_substitute`** (`true`): promoted from a
  literal `if True:` in `dev_gate._serve_defense_turn_via_ladder`. Phase 21
  decided that a participant should wait for a turn built from their own
  code rather than be handed a generic stand-in, and hardcoded it — which
  left four Phase 6 ladder tests red against behaviour no one could switch
  back. It is now a knob: `true` keeps Phase 21's decision, `false`
  restores Phase 6's latency-first degrade, and the ladder tests set
  `false` to exercise the lower rungs on purpose.

## 2026-08-08 — backend port moved off 5000

- **`extension.backend_base_url`: `http://localhost:5000` →
  `http://localhost:5055`.** Port 5000 on this study's Linux box
  (studyhost, 10.0.0.10) is already held by an unrelated `python app.py`
  belonging to another project, bound to `0.0.0.0:5000`. The backend
  either fails to bind or, if the other process is stopped and restarted
  later, the extension ends up talking to whichever one won the port —
  the same class of silent cross-talk as the Redis `SO_REUSEPORT`
  collision found in Phase 13.

  This value is the single definition of the backend's port: both
  `backend/scripts/run_dev_server.py` and `backend/scripts/e2e_server.py`
  parse the port back out of it, `extension/scripts/build.js` inlines it
  and computes `manifest.json`'s `host_permissions` from it, and
  `extension/playwright.config.js` waits on it. Changing it requires an
  extension rebuild (`cd extension && npm run build`) and a reload of the
  unpacked extension in Chrome — the URL is a build-time constant, not
  fetched at runtime (the extension has no base URL to fetch config from
  before it has this one).

  The container-internal port in `backend/Dockerfile` and
  `infra/Caddyfile` moved to 5055 as well, purely so one number means
  "the backend" everywhere. That port lives in the compose network
  namespace and was never able to collide with a host process.

## 2026-08-10 — Piston replaces Judge0 as the execution backend; language capability table; the Phase 28 test-suite pipeline

- **`execution.backend`: new, `"piston"`.** Judge0 1.13.1's isolate
  requires cgroup v1; every host this project has run on is cgroup v2
  only, so Judge0 has never executed a single submission anywhere in this
  project's life (judge0/judge0#554). Piston's documented requirement is
  the exact inverse — "cgroup v2 enabled, and cgroup v1 disabled" — and it
  runs here. Judge0's client and compose services are KEPT, not deleted:
  tested code, and a cgroup v1 host could still prefer it.

  This finally closed the gap CLAUDE.md has carried since Phase 3 as the
  single biggest one before real participants: the adversarial containment
  battery (fork bomb / infinite loop / high memory / network egress /
  filesystem escape) had never executed in any sandbox. Under Piston, at
  the limits below, **all five are contained** and the sandbox stays
  healthy after each. Verified directly, not assumed.

- **`execution.piston_url`**: `http://localhost:2000`. Self-hosted, and
  not negotiable: the public emkc.org API became whitelist-only on
  2026-02-15 and explicitly excludes university and research projects,
  and sending participant code to a third party would break
  docs/DATA-FLOW.md's guarantee that no participant code leaves
  self-hosted infrastructure.

- **`execution.piston_languages`**: our language key -> `{install, name,
  version}`. All three are needed and none is guessable: the PACKAGE name
  differs from the RUNTIME name (one `gcc` package installs the `c`,
  `c++`, `d` and `fortran` runtimes; our `javascript` is the `node`
  package), Piston calls C++ `c++` and not `cpp`, and passing the wrong
  name is a 400 rather than a fallback. Versions are pinned, never SemVer
  ranges — a suite's golden values are only reproducible against the
  runtime that produced them.

- **`execution.piston_output_max_bytes`**: `65536`. Piston's own default
  is **1024 bytes** per stdio buffer, which silently truncates output and
  returns status `OL`. A 3-case suite prints one `AICQ_RESULT` line per
  case carrying both actual and expected values, so the stock cap would
  have broken grading for reasons having nothing to do with the
  participant's code. Observed for real: a fork bomb's stderr hit the
  1024-byte cap immediately.

  Note that Piston's limits are SERVER-SIDE CAPS, not defaults: a
  submission above a cap is rejected with HTTP 400, never clamped. That is
  why `infra/piston/render_env.py` generates them from these same values —
  a params.yaml asking for 10s against a stock 3s cap fails EVERY
  submission with a message about limits that looks nothing like the cause.

- **`languages`: new top-level section.** Replaces `diff_scope.py`'s
  hardcoded `EXTENSION_LANGUAGE_MAP`, which had drifted into being a
  tunable living in source (rule 2). Adds rust and cpp, and adds html/css
  with `grading: "compare"` — they are not executable in any sense a unit
  test could assert on, so they grade by comparison with the original by
  DESIGN, not as a degradation. A `grading: "tests"` language can still
  fall back to comparison at runtime when no valid suite survives
  validation.

- **`testgen.cases_per_suite`** (3), **`max_suite_attempts`** (3),
  **`escalate_after_attempts`** (2), **`require_distinct_goldens`** (true).
  The Phase 4 generator asked the model for inputs AND expected outputs,
  which is precisely what a small local model is worst at — it must
  mentally execute the function. Measured: a generated suite failed on the
  CORRECT code 1 case in 3. That is why 79 of 110 `test_suites` in the dev
  database hold zero cases.

  The new pipeline never asks for expected values. The model supplies
  stubs and call inputs only; expected values come from EXECUTING the
  original, correct function, so they are right by construction — and a
  model-authored suite that hardcodes its own verdict becomes
  structurally impossible, because the model no longer writes the runner.
  Measured acceptance after the change: **10/10** across python and
  javascript at ~1.4s per generation, up from 2/5 on python.

  `require_distinct_goldens` earned its place immediately: a suite whose
  three cases all produce the same expected value is correct, passes the
  discrimination check, and is still worthless. Observed on the local
  model.

- **`llm.escalation_model`**: `glm-4.7-flash:latest`. Cheap-first, escalate
  on repeated failure — the same shape as Phase 6's fallback ladder, and
  affordable because the validation gate detects a bad suite in two
  sandbox runs. Measured on one real function asked for a whole test
  program: qwen2.5:7b 5-9s (syntax errors), glm-4.7-flash 42s, qwen3:32b
  193s. The 32b was the only one that wrote an honest self-checking
  program, and it is still too slow for a prefetch window — which is why
  the design stopped asking for a program at all.

## 2026-08-10 (later) — bug-injection reliability: the A1-A4 mutation-operator bank

Driven by two things reported from real play, not by a brief:

> "sometimes the LLM is not reliably generating bugs ... sometimes there is
> no bug at all, this is the worst thing"

> "For the A1 level it should be an A1 level bug right, not this — this bug
> looks like an algorithmic A3 rather than an A1 error"

Both trace to the same cause. Since Phase 22's `verify=False`, a
perturbation was checked only for "does it parse" and "have we shown it
before". Nothing checked that it changed BEHAVIOUR, and each level was one
loose sentence in a prompt ("a semantic bug (e.g. a flipped operator...)"),
so the model decided what the level meant.

- **`bug_bank`: new top-level section, 40 mutation operators (10 per
  level).** Each carries `{id, name, instruction, before, after}` — the
  same catalogue-in-config pattern as `badges`, so adding an operator needs
  no code change. Generation now names ONE concrete operator per turn and
  supplies its worked example as a few-shot, instead of asking for "a
  semantic bug" and hoping.

  The levels, as the question each asks the participant:
  A1 "can you see that something is structurally wrong?"; A2 "can you
  understand what this code is supposed to MEAN?"; A3 "can you tell whether
  the ALGORITHM is actually correct?"; A4 "can you recognise something that
  looks like legitimate AI-generated code but is fabricated?"

  A side benefit the study gets for free: every attempt records its
  `operator_id`, so "which KIND of bug gets past people" becomes
  answerable, which is finer than per-level accuracy. Operator selection
  also avoids kinds already used in the session, so a replay draws a
  genuinely different bug rather than a reworded one.

- **`perturbation.max_bug_verification_attempts`** (3). A perturbation whose
  injected "bug" changes no observable behaviour is discarded and
  regenerated. Proof comes from the generated suite: a suite that passes on
  the original AND on the perturbed code has demonstrated the two are
  behaviourally identical. That is not a bad suite — it is evidence there is
  nothing to find. Measured on a real participant function: a local-variable
  rename was rejected `no_discrimination` twice, while a genuinely dropped
  field built a suite first time in 1.8s (original 3/3 pass, buggy 2/3 fail).

- **`perturbation.enforce_level_shape`** (true). A deterministic check that
  the EDIT SHAPE matches the level asked for: A1/A2 edit an expression in
  place, A3 removes or restructures, A4 introduces names that did not exist.
  Templates are exempt (they are the level by construction). The reported
  A1-that-was-an-A3 is rejected by this check and accepted as A3, which is
  what it was. If the regeneration budget runs out the candidate is still
  served with a `perturbation_level_mismatch` event — a mislabelled turn
  beats no turn.

  Levels are this study's independent variable, so a turn labelled A1
  carrying an A3 bug corrupts the measure rather than merely reading oddly.

- **`prefetch.lookahead_count`: 2 → 7**, the whole of round 1's defense
  turns. A turn now costs a perturbation AND a validated suite; two turns of
  lookahead left no slack for one needing three attempts. Suites are
  generated ONLY on the prefetch path — measured, a turn whose suite
  generation failed took 13-21s inline against a 217ms median, so they are
  a background luxury and never on the critical path.

## 2026-08-10 (later still) — metrics that reflect play, and badges worth chasing

Driven by real use: *"I played many games but it is showing I played only
one game, and streak is also one but my streak would easily be 4-5"*.

The stored numbers were **correct**, which is why this took investigating
rather than fixing. `total_gates_cleared` counts DISTINCT pull requests
finished, so an account with **130 graded turns, 7 replays and 3 restarts
on a single PR** reads as `1` — and the streak counted gate CLEARS, so for
someone still working one gate it can only ever be 0 or 1. Both metrics
answer "how much have you finished". Nothing answered "how much have you
played", which is what a participant is looking at after an hour of turns.

- **New raw + public metrics**: `turns_played`, `turns_won`,
  `current_turn_streak`, `longest_turn_streak`. The turn streak is a WIN
  streak and is what "streak" means to someone playing turns — measured on
  the two real accounts it is **7** and **4**, matching the reported
  intuition almost exactly. The clear-based `streak` is unchanged and still
  reported; the two answer different questions.
- **New private metric `rounds_played`** (first attempt + every replay +
  every manual restart). Added to `FORBIDDEN_PUBLIC_KEYS`: how many times
  someone had to go round again is struggle, not achievement, and the same
  reasoning already keeps `replay_count` private.
- **New raw metric `levels_won`** — how many of the four bug classes have
  been beaten at least once. An aggregate COUNT, so it says how broad
  someone's play is without revealing which class they are weak at.
- **11 new badges.** The previous eight were almost all "did you clear a
  gate", so an account earned five of them with its first clear and could
  earn nothing further no matter how much it played. The new ones are
  TIERED so there is always a next target: Getting Started / Regular /
  Veteran (10/50/150 turns), On a Roll / Hot Streak / Unstoppable (3/5/10
  win streak), Sharp Eye / Eagle Eye (60%/80% detection), Trickster /
  Mastermind (40%/70% injection), Full Spectrum (all four levels beaten).
  On real data an account went from 5 badges to 10, with visible next
  steps. Deliberately NO badge for replays, restarts or time taken —
  a badge is a public artefact and those measure struggle.

## 2026-08-10 (later still) — pass threshold, and a chain-of-thought wait

- **`developer_gate.pass_threshold_pct`: 80 → 60.** Requested by the study
  lead. Grounded in real play: across the two study accounts, 90 turns were
  won out of 207 graded (44% and 40%), so an 80% bar cleared almost nobody
  and pushed participants round the replay loop instead of through the
  gate. 60% of round 1's 8 turns is 5 — comfortably above chance for a
  find-the-bug task, while still requiring more right than wrong.

  `lead_gate.pass_threshold_pct` is deliberately UNCHANGED at 80: it scores
  a written NFR answer against a rubric, which is a different construct
  measured a different way, and moving both because one was asked about
  would quietly change a second study variable.

- **`turn_progress` (new collection, no config knob).** Turn generation now
  records which pipeline step it has reached, so the "preparing your next
  challenge" screen can show the real chain of thought — reading the pull
  request, hiding a bug, working out the correct answer, checking the bug
  is catchable — instead of a spinner.

  It is in Mongo rather than memory because the participant is usually
  waiting on a job in the RQ worker, a different container from the one
  serving their request. The steps are defined ONCE in
  `dev_gate.PROGRESS_STEPS` and served to the panel, so the labels cannot
  drift from the code that performs them, and nothing is simulated on a
  timer: an unreported step stays pending rather than being animated.

## 2026-08-11 — the Lead gate becomes multiple choice, verified by a human

The whole `lead_gate` section was reshaped. The gate no longer asks the
lead for a paragraph and has an LLM score it against a generated rubric;
it asks ONE multiple-choice question per selected NFR, and then invites
the lead to say whether that question and its marked-correct option were
any good. The reason is the study lead's, and it is a measurement
objection rather than a preference: nothing established that LLM-as-judge
scoring of free text was correct, and a free-text score has no ground
truth to check it against. An MCQ has a committed answer key, and the
human verification gives every generated item a human judgement of its
validity — so "was the generator sound?" becomes a reported number
instead of an assumption.

**Removed** (with the code that read them — see reports/phase-30-review.md):

- `lead_gate.k_fold_scoring`, `lead_gate.score_variance_flag_band` — the
  free-text scorer's self-agreement check. There is no scorer any more;
  correctness is `selected_index == answer_index`.
- `lead_gate.calibration_tolerance_pct` — tolerance for the hand-scored
  rubric exemplars in `backend/tests/calibration/`, deleted.
- `lead_gate.rater_agreement_sample_size` — default sample size for
  `backend/scripts/rater_agreement.py`, deleted.

**Changed:**

- **`lead_gate.pass_threshold_pct`: 80 → 60.** Scoring is now
  all-or-nothing per question, so with the usual 3–5 NFRs an 80% bar means
  "miss none" (2 of 3 is 66%). 60% keeps a real bar — chance on four
  options is 25% — and matches the developer gate's own measured move in
  Phase 29. The note in that entry, that lead_gate was deliberately left at
  80 because it measured a different construct a different way, no longer
  applies: this IS that construct changing.
- **`lead_gate.max_question_regen_attempts`** now covers the structural
  and self-consistency checks too, not only diff-groundedness. Exhausting
  it no longer raises — the last candidate is served with `qc_passed=false`
  recorded, because the human verification step is the backstop for
  precisely that case, and stalling a participant is worse than showing an
  item whose weakness is being measured.

**Added:**

- **`lead_gate.options_per_question: 4`** — sets the guessing baseline
  (1/n), so it is a study-design knob, not a UI preference.
- **`lead_gate.self_consistency_k: 3`** and
  **`self_consistency_temperature: 0.0`** — the model is re-asked to answer
  its own question cold, k times, and the item is rejected unless every
  pass reproduces the committed answer key. Same mechanism `quizgen.py`
  has had since Phase 4; this is the first time it has had a caller.
- **`lead_gate.max_option_length_ratio: 3.0`** — reject an item whose
  longest option is more than 3× its shortest. A conspicuously long option
  is a giveaway independent of comprehension.
- **`lead_gate.verification.enabled: true`** — whether the form is shown at
  all. `false` gives a study arm a plain MCQ quiz with no human quality
  signal.
- **`lead_gate.verification.required: false`** — the form is OPTIONAL by
  design. Forcing an opinion out of a lead who has none pollutes exactly
  the measure the form exists to collect.
- **`lead_gate.verification.on_invalid_flag: none`** — what a lead's
  "this item is invalid" flag does to their own score. Only `none` is
  implemented, deliberately: any exclusion rule makes flagging the cheapest
  route past the gate. The flag is recorded, the attempt is marked for
  researcher review, and a researcher decides afterwards whether to drop
  the item from analysis.

## 2026-08-17 — `developer_gate.min_function_logic_nodes`

**Added** `developer_gate.min_function_logic_nodes: 1`.

Minimum number of branch/loop/call/operator nodes a function must contain
to be usable as a turn target (`extraction.can_host_a_bug`, applied by
`dev_gate._usable_turn_targets` where the pool is READ, so retuning it
reaches sessions that already exist).

Reported from real play on a C++ inventory program: "there are so many
functions in the code, but it is only choosing Product".
`_function_for_turn` rotates the pool by the turn's POSITION and the pool
is in source order, so round 1's eight turns took pool entries 1-8 —
which on that file were `getId`, `getName`, `getCategory`, `getPrice`,
`getQuantity`, `setPrice`, `setQuantity` and `display`. Seven three-line
accessors, while `findProduct`, `addStock`, `removeStock` and
`statistics` sat at indices 10-23 and were never reached at all.

An accessor is not a thin exercise, it is an impossible one: `int getId()
const { return id; }` has no boundary to shift, no condition to flip, no
guard to drop and no API to fake, so all 40 `bug_bank` operators are
inapplicable. Measured on that pull request's real stored pool: 24
functions → 15 usable, and round 1 goes from eight `Product` accessors to
`findProduct`, `addProduct`, `displayProducts`, `searchProduct`,
`updatePrice`, `addStock`, `removeStock`, `deleteProduct`.

Set to `0` to disable the filter entirely. If the filter would empty a
pool (a pull request genuinely made of nothing but accessors), the
unfiltered pool is used instead — a thin exercise on the participant's
own code still beats the generic stand-in.

## 2026-08-18 — test-suite generation: model, per-model timeout

**Added** `testgen.model` (default `null` = `llm.model`). Which model
generates test suites, as a setting rather than a code change.

**Added** `llm.timeouts_by_model` (`{"qwen3:32b": 300}`). Per-model
override for `llm.timeout_sec`. A big local model answering in 90s is
working, not hung, and one global timeout cannot tell those apart —
measured, the 60s default recorded qwen3:32b's *good* answers as
`llm_error: ReadTimeout` and discarded them mid-write. Raising the global
timeout instead would let a genuinely hung fast-model call block for
minutes.

**Changed** `llm.escalation_model`: `glm-4.7-flash:latest` → `qwen3:32b`.

Measured on four functions (3 python, 1 cpp), real Piston, real models:

| model | suites produced | avg |
|---|---|---|
| `qwen2.5:7b-instruct` (default) | 2/4 | 25.1s |
| `qwen3:32b` @ 60s timeout | 1/4 | 140.1s |
| `qwen3:32b` @ 300s timeout | **3/4** | 88.2s |

And from the new `suite_generation_rejected` telemetry, grouped by model:
`glm-4.7-flash` produced `bad_format` on 11 attempts; `qwen2.5:7b`
produced **zero**. The final attempt — a turn's last chance at a suite —
was being spent on the model least able to follow the response format.

qwen3:32b is NOT made the primary model. At ~88s per suite against a
prefetch lookahead of 7, the worker would take ~10 minutes to warm a
round, and every turn before that would degrade to comparison grading.
Escalation fires on the last attempt only, so the cost is bounded and
paid off the participant's critical path.

## 2026-08-19 — CONFIG_VERSION b344cf6de9f2

Added to `testgen` for Phase 32: `file_context_enabled` (default true),
`file_context_max_chars` (default 12000).

**Why.** The live database held 29 production suites — every one Python —
against 38 non-Python turns played (18 cpp, 13 java, 7 c), all silently
graded by comparison. A committed baseline bench
(`backend/scripts/bench_suitegen.py`,
`reports/assets/bench-suitegen-baseline-2026-08-19.json`; 31 functions,
5 languages, real Ollama + real Piston) measured the isolated assembly at:
c 100%, cpp 57%, python 43%, java 33%, **rust 0%** — and the compiled-
language failures were overwhelmingly `probe_compile_error` /
`probe_runtime_error`, i.e. the missing class shell/includes, not the
model's test cases. `file_context_enabled` switches suite compilation to
splice the variant into the function's OWN FILE (services/file_context.py);
it is a kill switch back to the old assembly. `file_context_max_chars`
bounds only what the MODEL sees of the file (12000 chars ≈ 3k tokens,
covering the whole 560-line inventory.cpp that motivated the phase); the
executed program always uses the full file.

## 2026-08-19 — CONFIG_VERSION 87750f03284f

Added for Phase 32, second round (all in the same phase, recorded
separately because they landed after the first measurements):

* `testgen.suite_infeasible_after` (default 3). Stop re-paying the suite
  attempt budget on a function that keeps failing for reasons intrinsic
  to the function. Measured motivation: one function, `statistics`,
  accounted for **81 of the 170 real-model rejection events** in the live
  `events` collection, failing the same way every time the turn rotation
  reached it. `no_discrimination` is deliberately NOT counted (it is a
  fact about the injected bug, not the function), the key is the
  normalised AST fingerprint so an edited function gets a fresh chance,
  and a success clears the record. 0 disables it — a wrong verdict here
  is a silent downgrade to comparison grading.
* `observability.suite_coverage_alert_pct` (default 50) and
  `observability.suite_coverage_min_sample` (default 10). Alert when
  executable suites cover less than X% of recent turns whose language is
  graded by tests. This closes a standing Phase 28/29 open question:
  suites are generated only on the prefetch path, so with the RQ worker
  down every turn silently degrades to comparison grading and nothing
  said so. That has happened three times in this project's history
  (`OLLAMA_BASE_URL`, `PISTON_URL`, a stale config cache), each time
  found by a person noticing rather than by a monitor. The threshold is
  50 rather than ~100 because measured per-language acceptance is
  58-100%; the sample floor stops the first turn of a session paging
  anyone.

## 2026-08-21 — CONFIG_VERSION 94f61c1d2a72

- **`extension.backend_base_url`: `http://localhost:5055` →
  `https://your-backend.example.com`** (an ngrok
  tunnel fronting the study host's port 5055), at the study lead's
  request. Reason: the backend runs on the Linux box (studyhost,
  10.0.0.10) while Chrome runs on a separate Windows desktop, so
  `localhost` named two different machines and the extension could only
  reach the backend through a hand-rolled SSH port-forward. Verified
  live before committing the change: `GET /healthz` through the tunnel
  returns `status: ok` with mongo/redis/piston/llm all reachable and 8
  Piston runtimes installed.

  The URL deliberately carries **no port**. `run_dev_server.py` and
  `e2e_server.py` parse the port back out of this value and fall back to
  5055 when there is none, which is the local port the tunnel forwards
  to — writing a port here would move where those servers bind and point
  the tunnel at nothing.

  Two knock-on effects, neither of them config:
  * `extension/scripts/build.js` derives `manifest.json`'s
    `host_permissions` from this value, so **the extension must be
    rebuilt and reloaded** or its fetches are blocked. Rebuilt and
    verified: `https://your-backend.example.com/*`.
  * An ngrok free tunnel answers any **browser**-User-Agent request —
    which every fetch from an MV3 service worker is — with an HTML
    interstitial page (ERR_NGROK_6024) carrying HTTP **200**, confirmed
    by hand against this tunnel. `request()` would have read that as
    "ok, but not JSON" and returned an empty object, i.e. a silent
    failure rather than an error. `extension/src/shared/api.js` now
    sends ngrok's documented `ngrok-skip-browser-warning` header on
    every backend request (not on the github.com `.diff` fetch). It is
    unconditional rather than sniffed from the base URL, since an
    unknown request header is inert to Flask and there is then nothing
    to remember to undo when the backend moves back.

## 2026-08-22 — CONFIG_VERSION fa3b0ab36962

- **`testgen.suite_eligibility_precheck`** (new, default `true`). Decide
  statically — no model call, no sandbox run — whether a function can
  support an executable test suite, and go straight to comparison grading
  when it cannot.

  The fallback itself is not new: `generate_suite` returning None has meant
  "grade against the original instead" since Phase 28, and that is the
  designed bottom rung. What was wrong was the PRICE of finding out.
  Incompatibility was discovered only by paying the whole attempt budget
  and then being told `probe_runtime_error`.

  Measured on the study lead's real React pull request (`task_react.jsx`,
  762 lines): **all 19 functions failed, at 40-100 seconds each**. The
  worker log shows one turn spending 94,021ms of its 103,542ms total inside
  suite generation — 91% of the turn — before falling back. The
  participant's own console shows a single `/current-task` taking 63
  seconds, and the turn before it gave up entirely and served a PYTHON
  exercise from the static bank. `testgen.suite_infeasible_after` (Phase
  32) already makes the SECOND such mistake cheap; this makes the first one
  free. On that same file the new check now reaches the same verdict for
  all six pool functions in **23ms total**.

  Two rules, both decidable from the syntax tree: `contains_jsx` (Piston's
  node has no npm packages and runs a submission as CommonJS, so
  `import React` is a SyntaxError before JSX is reached) and
  `closes_over_enclosing_scope` (a function nested inside another reads
  locals that will not exist when it is called from a fresh top-level
  `main` — this is the `ReferenceError` behind `probe_runtime_error`, and
  on task_react.jsx it alone accounts for every pool function).

  **A third rule was built, measured, and removed**, which is why the
  default is `true` rather than a wider net: "returns no value, so all
  goldens must be identical" is intuitive and wrong. It retired C++
  `void Inventory::addStock(...)` from the bench corpus, and putting that
  exact function through the real generator produced a valid 3-case suite
  on the first attempt — the model calls the void method and prints the
  state it mutated. The removal is recorded in the module docstring and
  guarded by a named regression test so it is not reintroduced from first
  principles.

  Conservative by construction: an unparseable body, an unknown language, a
  grammar tree-sitter will not supply, or any raised exception all resolve
  to ELIGIBLE. A wrong "no" is a silent, permanent downgrade that nothing
  revisits; a wrong "yes" costs one attempt the three-gate validator
  catches anyway. Verified against the whole bench corpus: **40 functions
  across 6 languages, 0 retired.** `false` restores the previous
  always-try behaviour, because a heuristic that decides how a turn is
  graded must be switchable off for the study.

## 2026-08-25 — CONFIG_VERSION 301156d5b126

Phase 34, part 1. Two changes, both driven by the same measurement problem:
the gate's own repair score was the ONLY comprehension measure in the
system, which makes "the gate measures comprehension" circular.

- **REMOVED `developer_gate.round2`.** The gate is now one round of eight
  turns (`round1.questions_per_level: 2` x four levels). Failing it goes
  straight to REPLAY and then ESCALATED — exactly what failing round 2 used
  to do; what is gone is the scaffolded round in between.

  The reason is measurement, not simplification. A round-2 turn was played
  AFTER a recap that named the bug class, so its turns are not exchangeable
  with round-1 turns: pooling them mixed assisted with unassisted
  performance inside one accuracy, the same category of error as pooling
  the two grading regimes (Phase 33). Comprehension is now measured once,
  unassisted, over eight turns.

  **What this gives up, on purpose:** round 2 was the tool's ONLY
  remediation step — the one place a participant was told what they had
  missed. Any claim that the gate IMPROVES comprehension (rather than
  measuring it) is weaker without it. Recorded in docs/STUDY-PROTOCOL.md.

  The `ROUND2_*` states remain in `GateState` and are still handled by
  `advance()`, because a session persisted mid-round-2 before this change
  is still sitting in one and a state with no branch would spin forever in
  advance()'s loop. Nothing transitions into them any more.
  `developer_gate` also lost `round2` from the schema's `required` list, or
  every config load would fail validation.

- **NEW section `mcq`** (`enabled`, `questions_per_turn`,
  `options_per_question`, `max_attempts`, `max_option_length_ratio`). An
  input/output prediction question attached to each Defense turn: given a
  call the participant has NOT been shown, what does this code return?

  The shape is evidence-led. Arvan et al. (ASE 2026) evaluated 14
  comprehension proxies against an expert-consensus ground truth and found
  input/output question proxies the MOST reliable and syntax-focused
  questions the LEAST. See docs/STUDY-PROTOCOL.md §2.

  **The answer key is executed, never authored.** The correct option is
  what the ORIGINAL function prints for the chosen input — Phase 28's rule
  ("never ask the model for an expected value") applied to a new artefact,
  for the same measured reason. The model writes only the call expression
  and the DISTRACTORS, where being wrong is the point.

  **Consequence, stated plainly:** an MCQ exists only where an executable
  suite exists, so on the shapes Phase 33 rules out (React handlers closing
  over component state, JSX bodies) there is NO question rather than a
  model-keyed one. The measure is therefore missing on exactly the code
  that is hardest to comprehend — biased missingness that any analysis must
  report rather than ignore.

  `questions_per_turn: 1` is the default and carries a measurement cost
  recorded beside it: eight dichotomous items put an individual's score at
  roughly +/-17 percentage points of standard error, which attenuates the
  correlations this measure exists to support. 2 halves that and doubles a
  reviewing lead's workload.

## 2026-08-25 — CONFIG_VERSION 6e7eb0931fe2

Phase 34, part 3. The developer flow is now fixed as: **8 Defense turns → 4
Attack turns → 5 comprehension questions → the gate clears.**

- **NEW `developer_gate.round1.attack_questions_per_level`** (1, giving 4
  Attack turns against Defense's 8). Attack tests whether a developer can
  PLANT a bug an AI cannot repair, which is a hypothesis-forming measure
  rather than the gate's primary one; Defense carries the comprehension
  claim and keeps its eight. Attack turns are optional to play anyway
  (skipping costs nothing), so this caps a phase a participant may skip
  through. Before this, both phases shared `questions_per_level` and a
  caller could not ask for different lengths.

- **`mcq.questions_per_turn` → `mcq.questions_per_gate` (5).** The questions
  moved out of the individual turns and became their own stage between
  Attack and scoring, with a new FSM state `ROUND1_COMPREHENSION`.

  Asking them once, in one state, rather than beside eight editors means
  the measure is taken under one condition instead of interleaved with
  repair work — and it fixes the gap recorded when they were per-turn:
  nothing forced an answer, so a participant could submit a repair and
  leave the question blank, losing the data point silently. **Every
  question must now be ANSWERED before the gate clears; none of them is
  SCORED.** Requiring an answer and grading it are different things, and
  only the first is needed to make the data exist.

  **Fewer than 5 is normal, not an error.** An MCQ needs an executable
  suite (the key is obtained by running the original), and a pull request
  may not have enough functions that produce one. The phase ends when
  nothing more can be built — recorded on the session as
  `comprehension_exhausted`, without which `advance()` would wait forever
  for a question that can never exist and the gate could never clear.

  **Measurement cost of 5, stated where it is set:** five dichotomous items
  put an individual's score at roughly ±22 percentage points of standard
  error, which attenuates the correlations this measure exists to support.
  Aggregate claims across participants survive that; per-individual claims
  need more items or an explicit attenuation correction.

## 2026-08-25 — no CONFIG_VERSION change

Phase 34, part 4. Two behaviour fixes from real play, neither of which
needed a config knob, recorded here because they change what the `mcq`
section's numbers mean in practice:

- `mcq.max_attempts` (2) is now spent per FUNCTION rather than per
  question. The comprehension phase tries every function that has an
  executable suite before giving up, so a pull request with five usable
  functions now gets up to 5x2 attempts at each question instead of 2. The
  first real run generated ONE question because a single function failed
  twice and ended the whole phase.
- An answer is revisable until the participant moves on, so
  `mcq.questions_per_gate` counts questions ANSWERED, not clicks. The item
  records `revision_count` alongside the first commitment's timing.

## 2026-08-25 — CONFIG_VERSION 7490064509bf

Phase 35: the lead's independent review of the developer's comprehension
items, and the third rater that makes a three-way agreement statistic
possible.

Added `mcq.llm_self_answer` (`enabled`, `passes: 3`, `temperature: 0.3`).
After an item is generated, the SAME model is asked to answer it cold —
shown the function, the call and the shuffled options, never the key. Two
purposes: it is the third rater (Fleiss' kappa needs >= 3, pairwise Cohen's
would otherwise be the ceiling), and it is a generator sanity check, because
a model that cannot solve what it just wrote has probably written a bad
item. `passes: 3` at a non-zero temperature so the passes are genuinely
independent; the modal choice is recorded and the across-pass agreement is
stored beside it, so an unstable answer stays visible rather than being
averaged away. Same model as the generator, deliberately — a DIFFERENT
model failing would be ambiguous between "hard item" and "models differ".

Added the `lead_review` section. `items_per_pr: 5` matches
`mcq.questions_per_gate`, so a lead is offered exactly what one gate
produced. `sample_fraction: 1.0` reviews everything; below 1.0 the selection
is deterministic on the item id rather than random at read time, so
refreshing the queue cannot resample. `reviewers_per_item: 1` is the study
default; 2 is the setting for a lead-vs-lead reliability subsample.
`quality_scale` is a 5-point Likert with written anchors — the anchors are
exported into the codebook so an analyst reads the same words the lead did.
`judgements` are the two separately analysable yes/no calls: one WITH a
definitive ground truth (`key_is_correct` — checkable by execution) and one
without (`question_is_well_formed`). An earlier draft's `is_relevant` was
dropped: it duplicated the quality rating and had no ground truth.
`allow_skip: true` because a skip is a real datum ("saw it and declined"),
distinct from never having reached the item — Phase 30's rule.

The order of operations is load-bearing and is enforced in code, not just
documented: the answer key is revealed to the lead only AFTER they commit to
their own answer, and the developer's answer is never shown to the lead at
all. Rating an item having already seen which option is marked correct is a
different and much easier task than answering it, and the lead's
independence from the developer is the whole value of the measure.

## 2026-08-27 — CONFIG_VERSION a60bef45ef1b

Phase 36: `consent.org_consent_mode` (`required` | `advisory`), default
**`advisory`**.

Reported plainly: *"the owner may be anyone but our extension should work
irrespective of the owner"*. Organisation consent is keyed on the repository
OWNER, and hand-registering a record per owner made the tool unusable the
moment a second organisation appeared — `/gate/evaluate` returned 403
`org_consent_missing`, the extension failed silently, and the participant
could not fix it, because the consent THEY signed is a different consent
entirely.

`required` is the previous behaviour and is what `docs/DATA-FLOW.md` and the
study protocol promise; it is the right setting once real participants bring
real repositories. `advisory` proceeds without a record and RECORDS that it
did — an `org_consent_absent` event, plus `org_consent_present: false` on the
gate evaluation.

**There is deliberately no third "off" mode.** Advisory is not "skip the
check": the absence is still recorded, so an analysis can always separate
sessions that ran with consent on file from sessions that did not. Silently
not knowing is the one outcome that is never acceptable — the same rule Phase
33 applied to an unknown grading regime.

Also added, in code rather than config: an OrgConsentDocument whose `org` is
`"*"` now covers every owner. That is the better answer where a blanket
agreement genuinely exists, because it keeps the mode at `required` and
states the permission as a governance RECORD carrying its own
`signed_agreement_ref`, rather than turning the check down globally.
Registered via `backend/scripts/register_org_consent.py --org '*'`.

**The default is `advisory` because this is a research prototype in active
testing.** Flip it to `required` before real participants use it, and grant
whatever wildcard or per-owner records the ethics approval actually covers.

## 2026-08-27 — CONFIG_VERSION cc90f8ce0a27

Phase 36 part 4: **TypeScript support.** `languages.typescript` —
extensions `.ts`/`.tsx`/`.mts`/`.cts`, `grammar: "tsx"`,
`grading: "compare"`.

Reported by a tester as "unsupported language", and Phase 33 had already
recorded why that is worse than it sounds: `evaluate_trigger` counts lines
regardless of language, so a 120-line `.tsx` pull request FIRED the gate,
disabled Create/Merge, and then had nothing to play.

**`grammar: "tsx"`, not `"typescript"`** — verified, not assumed. The `tsx`
grammar parses plain `.ts` AND `.tsx`-with-JSX without error; the
`typescript` grammar errors on JSX. Since a language key maps to a single
grammar, `tsx` is the one that covers the whole language.

**`grading: "compare"` is a fact about the sandbox, not a preference.**
Piston has no TypeScript runtime installed (verified against the live
instance: c, c++, d, fortran, java, javascript, python, rust), so nothing
could execute a suite even if one were generated. Comparison grading is the
designed bottom rung (Phase 19) and is what React-shaped JavaScript already
uses. Change to `"tests"` only after adding a `typescript` entry to
`execution.piston_languages` AND installing that runtime.

**The `grammar` field is now actually read.** It has existed since Phase 28
and **nothing had ever consulted it** — every language key happened to equal
its grammar name, so the field was documentation that looked authoritative.
`extraction.get_parser()` now resolves through it, which is what makes
`typescript` -> `tsx` work at all.

## 2026-08-29 — CONFIG_VERSION 00b8cbbccef1

Phase 37 (gamification). Added three `badges` entries forming the lead's
reviewer ladder: `reviewer_5` (bronze, "First Reviews"), `reviewer_10`
(silver, "Steady Reviewer"), `reviewer_50` (gold, "Review Veteran"), all on
a new metric `mcq_reviews_completed`.

**Why a new metric rather than the existing `lead_verifications_given`.**
That counts NFR verifications given inside the lead's OWN gate (Phase 30);
this counts comprehension items reviewed for somebody else (Phase 35). They
are different contributions and pointing the badges at the existing one
would have rewarded an activity nobody asked to reward, with a number that
does not match the Reviews screen.

`mcq_reviews_completed` counts RATED, NON-SKIPPED reviews. Rated, because a
review is finished when the quality rating is in and rewarding an
assignment would reward opening the queue. Non-skipped, because Phase 35
made a skip a real recorded datum precisely so it stays distinguishable
from work done — a badge that counted skips would pay for the fastest way
through the screen.

Three rungs rather than one, so a reviewer who has just earned a badge
always has a next target — the same reasoning behind the developer ladder.

**Not added: a `gamification.mode` switch.** `docs/STUDY-PROTOCOL.md` draft
2 makes gamification the only randomised factor (full vs. minimal) and
asserts the minimal arm is "a configuration, not a fork". No such switch
exists, and this change makes the gamified surface larger without one. The
study lead was shown this and chose to proceed, revising the research
questions and protocol instead. **Until that revision lands, the protocol's
RQ5 and §6 describe a design the code cannot support.**

## 2026-08-29 — CONFIG_VERSION 6805ce106457

Phase 37 follow-up. Added a REQUIRED `persona` field to every `badges`
entry (`developer` | `lead` | `any`), and stamped all 22 existing badges:
16 developer, 4 lead, 2 any (`first_clear` and `consistency_streak_3`,
which count across both personas).

**Why.** Reported from real use: a lead was shown "Cleared at A1" — a rung
of the DEVELOPER's Defense ladder, for a level a lead never plays and has
no way to interpret. The badge table had no notion of who a badge is for,
so every board showed every badge.

Required rather than optional, so a new badge cannot silently inherit the
wrong audience — the same reasoning behind `--agreement-ref` having no
default in `register_org_consent.py`. The panel still RENDERS a badge that
declares no persona: the field is schema-required, so a missing one means
old data rather than a deliberate exclusion, and hiding a badge somebody
has already earned is worse than showing one they cannot progress.

## 2026-08-29 — CONFIG_VERSION 9176b5d825f7

Phase 37 part 3. Repointed `consistency_streak_3` from `current_streak`
(consecutive gate CLEARS) to a new metric `current_day_streak`
(consecutive DAYS with recorded activity), renamed it "Three-Day Streak",
and added `consistency_streak_7` ("Week-Long Streak", silver) on the same
metric. Both `persona: any`.

**Why.** Reported as "the streak is not working — it should update when a
user opens day after day". The public `streak` was the clear-based one,
and clears are rare enough on this study's real data (60% pass threshold
against ~17% detection accuracy) that it sat at 0 or 1 permanently. A
badge called "Consistency Streak" pointed at a metric that could not move
was unreachable for everyone.

A day counts when something was RECORDED — a graded turn, an answered
comprehension question, a completed review — not when the panel was
opened. A streak that counts opening rewards opening, and this study
measures what people do with the gate. Dates are UTC, which is not
necessarily the participant's midnight; that is a known consequence of
storing every timestamp UTC and is recorded rather than silently
mis-binned.

## 2026-08-30 — CONFIG_VERSION 5190b6740d2f

Bug-level mislabelling, reported as *"sometimes the bugs of a particular
level is giving bugs of another level"*. Two causes; this entry covers the
taxonomy half. See `reports/bug-level-mislabelling-report.md` for the
diagnosis and `perturbation.py`'s `classify_change_level` for the other
half.

**Operators re-filed.** An operator's worked example is the few-shot the
model actually copies, so an example demonstrating another level's bug
produces that level's bug however the turn is labelled.

* `boundary_condition_alteration` **A2 → A1**. Its instruction — "alter a
  guard's BOUNDARY so singleton or empty inputs take the wrong path",
  `if (len > 1)` → `if (len >= 1)` — is A1's participant briefing almost
  word for word ("a comparison or index is off by one… the exact value a
  condition flips on"). Filed under A2 it produced an A1 bug on every draw.
* `misremembered_algorithm` **A4 → A3**, and re-exampled. A4 is
  hallucinated INTERFACES; getting a real algorithm subtly wrong belongs
  beside A3's `wrong_algorithm_variant`. Its old example was a **loop-bound
  rewrite** — an A1 — so the few-shot taught the model to write an A1 while
  the turn was labelled A4.
* `wrong_recursion_base_case` (stays A3) **re-exampled**. `n <= 1` →
  `n == 0` is a comparison swap, which is A2's shape. The example now turns
  on the base case's reachability instead.
* `comparison_replacement` (stays A2) **re-instructed and re-exampled** —
  found by the new taxonomy test, not by reading. It said "fails only at
  the BOUNDARY" and showed `x >= target` → `x > target`, a pure strictness
  change: an off-by-one, A1's concept. A2's own briefing is "an operator,
  condition, or pair of values has been FLIPPED", so the example now flips
  direction, which no A1 operator does.

**Pool sizes are now A1 11, A2 9, A3 11, A4 9**, not 10/10/10/10. This is
deliberate: no operators were invented to rebalance, because inventing
taxonomy is a study-design decision. Nothing depends on equal sizes —
`select_operator` picks uniformly within a level, and level assignment is
by turn position — so the only effect is how many turns pass before an
operator repeats, and at 2 turns per level per round that is not binding.

**New knob `perturbation.enforce_level_classifier`** (default `true`), the
real level check. `enforce_level_shape` is kept and still runs; it was
never a level check and its rejections are now logged as
`perturbation_no_op` and `perturbation_shape_mismatch` so the distinction
is visible in telemetry.

## 2026-08-31 — CONFIG_VERSION 5190b6740d2f -> 3b1db87b0e26

**Two review steps removed, at the study lead's direction.** Both asked a
participant to answer a generated question and, in the same sitting, to
grade the question. Judging a generated item is researcher work done
afterwards over the stored items; asking the person who has just answered
one to also grade it put two different tasks on one screen and made both
gates longer. See `reports/phase-40-review.md`.

**Removed `lead_gate.verification`** (`enabled`, `required`,
`on_invalid_flag`). It configured the form the lead saw after answering
each NFR question — "was this a fair question?" and "was the option marked
correct actually right?". `POST /lead-gate/<pr>/verify` is gone with it,
not aliased, so a stale extension build cannot keep writing data the study
no longer analyses (the rule Phase 30 applied to `/respond`).

**Removed `lead_review.quality_scale`, `lead_review.judgements` and
`lead_review.allow_skip`.** They configured the 5-point Likert and the two
yes/no judgements a lead gave a developer's comprehension item after
answering it. `POST /lead-review/items/<id>/rate` is gone. A review is now
the lead's cold ANSWER and nothing else — which is what
`services/agreement.py` consumes for the three-rater kappa, so the
measurement this queue exists to produce is untouched.

**What this gives up, recorded rather than absorbed:** there is no longer
any in-product human check on the quality of a generated item. For the Lead
gate that leaves `nfr.py`'s automated QC gate alone — and Phase 30 measured
it rejecting roughly one item in three as genuinely arguable, while a
QC-failed item is still SERVED (`qc_passed: false`) on the explicit premise
that the human verifier was the backstop. That premise no longer holds.

**Stored fields are KEPT and marked historical**, because reviews and
attempts recorded before this date carry real values:
`LeadAttemptDocument.verification`/`.flagged_for_review`,
`MCQReviewDocument.quality_rating`/`.judgements`/`.comment`/`.rated_at`/
`.skipped`. Every export column reading them says HISTORICAL and states
that an empty value means NOT COLLECTED rather than a negative judgement —
the rule Phase 33 set for an unknown `grading_method`.

**Two knock-on changes needed to stop a metric freezing**, each of which
would otherwise have silently locked at whatever it read today:

* `badges`' reviewer ladder (`reviewer_5`/`10`/`50`) counts
  `mcq_reviews_completed`, which now counts ANSWERED reviews rather than
  RATED ones. On the old definition no lead could ever earn one again.
* `lead_verifications_given` and `lead_items_flagged_invalid` left the
  PUBLIC lead board and its sort key, replaced by `mcq_reviews_completed`.
  They are still computed and still reachable privately via
  `/leaderboard/me` and the data export. A public column that can never
  move again reads as a live measure of contribution and is not one.

No values changed in this entry beyond the removals; the version moved
because keys were deleted.

## 2026-09-01 — CONFIG_VERSION 3b1db87b0e26 -> 21e6d5784d2a  (SUPERSEDED, see below)

**`auth.jwt_access_token_expires_min` 60 -> 480 (8 hours).** Reported as
"I am getting unauthorized when I give consent"; diagnosed from this
backend's own `http_request_latency` telemetry, which is the reason that
event exists.

The timeline, all from the `events` collection:

```
09:36:04  200  POST /auth/login              <- token minted
09:36:08  201  POST /consent/participant     <- consent SUCCEEDED
10:36:33  401  GET  /auth/me                 <- 60m29s later
10:36:35  401  POST /consent/participant     <- "unauthorized"
          ... 19x 401 on /consent/participant, all actor_id="anonymous"
10:37:26  409  POST /auth/register    x8     <- tried making a new account
10:38:45  200  POST /auth/login              <- signed in again
10:38:46  200  GET  /auth/me                 <- fine immediately
```

Consent was never the problem: it had already been signed successfully an
hour earlier, and every 401 carries `actor_id: "anonymous"`, meaning
flask-jwt rejected the token before any consent code ran. The token
expired exactly on schedule. **There is no refresh-token mechanism
anywhere in this backend**, so an expiry is a hard stop.

8h covers a study sitting end to end. **The trade is real and
deliberate**: a stolen token is now valid for eight hours rather than one.
Acceptable for a self-hosted research prototype whose tokens grant only
the participant's own gate data; it would not be acceptable everywhere,
and real refresh tokens remain the better answer if this study ever needs
one.

**This number is the smaller half of the fix and must not be read as
having solved it.** The defect was that the extension did not NOTICE:
`GET_ME` reported `loggedIn: true` for any token present in storage
without checking validity, the popup's `refreshProfile()` swallowed the
401 in a bare `catch {}`, and `shared/api.js` reads `data.error` while
flask-jwt-extended answers `{"msg": ...}` -- so the participant was told
`POST /consent/participant failed with status 401` while the popup
insisted they were signed in. The extension now clears the token on any
401 and every surface says "your session expired, sign in again". See
`reports/phase-41-review.md`.

No other values changed.

## 2026-09-01 — CONFIG_VERSION 21e6d5784d2a -> 805c7afe3269

**`auth.jwt_access_token_expires_min` 480 -> 0, and 0 now means NEVER
EXPIRES.** Supersedes the 8-hour value set earlier the same day, at the
study lead's direction: *"remove the session out thing, don't put
sessions"*. A participant signs in once and stays signed in.

`app/__init__.py` maps 0 to flask-jwt-extended's
`JWT_ACCESS_TOKEN_EXPIRES = False`. **Not a zero `timedelta`** -- that is
the obvious way to write "0 minutes" and it expires every token
*instantly*, locking every participant out on their first request. The two
are one keystroke apart, so
`test_a_token_never_expires_under_the_study_default` asserts on the minted
TOKEN (no `exp` claim, and `/auth/me` accepts it) rather than on the config
value, and was mutation-checked against exactly that mistake.

The schema's `exclusiveMinimum: 0` became `minimum: 0` to allow the
sentinel. **0 is a sentinel, not the removal of the knob**: any positive
value is still a real lifetime in minutes, pinned by
`test_a_positive_expiry_still_produces_a_real_lifetime`, so a study arm
that wants an expiry back can have one.

**THE TRADE, AND IT IS PERMANENT.** A token that never expires is valid
until the participant signs out or the signing key changes. There is no
revocation list in this backend, so a leaked token cannot be withdrawn
without rotating `JWT_SECRET_KEY`, which signs everyone out at once. That
is acceptable HERE -- a self-hosted research prototype whose tokens grant
only that participant's own gate data -- and would not be acceptable in a
system holding anything else. Revisit before this design is reused.

**The 401 handling from the previous entry STAYS, and matters more now.**
A 401 is no longer routine; it is rare and unexpected (the database has
been dropped before in this project, `JWT_SECRET_KEY` may be set on the
study host, storage can be corrupted) -- which is exactly when a silent
failure is hardest to diagnose. Its wording moved from "your session
expired" to "you have been signed out", because there is no timer any more
and telling someone their session timed out would send them looking for a
problem that does not exist.

No other values changed.

## 2026-09-01 — CONFIG_VERSION 805c7afe3269 -> 155f63b1c58a

**Gamification: an in-turn celebration, and a bug-catching badge ladder.**
Asked for as "players can actively engage with the game ... after they pass
the first defense question, show the badge ... when they successfully
detect bugs in the multiple of 10's show them badges with different
colors".

**New `gamification` section** (`milestone_every: 10`,
`celebrate_first_bug: true`, `toast_ms: 2000`, `confetti_enabled: true`).
Separate from the `badges` table on purpose: that table decides what
exists and what it is called, this decides when a celebration fires and
how long it holds. Adding a badge must not change pacing, and retuning
pacing must not silently mint or retire an award.

**Seven new badges on a NEW metric, `bugs_found`** — `first_bug` (1),
`bugs_10`, `bugs_20`, `bugs_30`, `bugs_40`, `bugs_50`, `bugs_100`, running
bronze -> bronze -> silver -> gold -> **platinum** -> **legend** ->
legend.

**`bugs_found` is deliberately NOT `won_defense_turns_a*`**, which every
other developer badge uses. Those are scoped to publicly-eligible CLEARED
gates, and measured on the live database before choosing: a participant
with 5 won turns scores **0** on them, and one with 19 scores **5** —
because the pass threshold is 60% while real bug-detection accuracy runs
at ~17%, so almost nobody clears. A ladder on the scoped count would sit
unearned for people who are visibly playing, which is the same defect
Phase 29 and Phase 37 part 3 were each raised over. `bugs_found` counts
every won Defense turn, cleared or not.

**Two new tiers, platinum and legend.** `tier` is a free string in the
schema, so no schema change was needed — but they need colours and icons,
and both were added (`--aq-tier-platinum`, `--aq-tier-legend`, defined in
BOTH themes; `FaGem` and `FaCrown`, so the tiers differ in SHAPE as well
as colour). The point of running past gold is that a long run keeps
producing a visibly different reward rather than a third gold.

**The ladder stops at 100 and the encouragement does not.** Every further
multiple of `milestone_every` (60, 70, 80 ...) still fires a toast and
confetti with no badge attached. A config table cannot hold an unbounded
ladder, and a badges page of forty near-identical cards is not a reward.

**`milestone_every: 0` disables count milestones** entirely, leaving only
the badge ladder — the switch to reach for if the celebration turns out to
be a distraction mid-study rather than an encouragement.

Schema: new `gamification` object, added to the top-level `required` list.
No existing values changed.

## 2026-09-09 — CONFIG_VERSION 155f63b1c58a -> 54c92406c9fd

**A shorter round, and a reward on every win.** Two study-lead requests
after playing the gate, plus the diagnosis of a report that no badge
appeared.

**`developer_gate.round1.questions_per_level` 2 -> 1** — Defense is four
turns, not eight. *"That will reduce the time to play and may keep playing
more games."* Every measure here is per-turn, so more short sessions is
MORE data, not less.

**TWO CONSEQUENCES, neither silent, both needing the study lead's eye:**

1. **The pass threshold got HARDER, which is the opposite of the intent.**
   `pass_threshold_pct` is 60; 60% of 4 is 2.4, so clearing now needs 3 of
   4 (75%) where it needed 5 of 8 (62.5%). Not retuned here — the
   threshold is a study parameter and moving it changes what "cleared"
   means across every session already recorded.
2. **Fewer comprehension questions are possible.** An MCQ needs a Defense
   turn that produced an executable suite, so four turns yield at most four
   distinct functions against `mcq.questions_per_gate: 5`. Phase 36 part 8
   widened the candidate pool across replays, so earlier rounds still
   contribute — but a FIRST round can now run short by construction.

**`gamification.celebrate_every_win: true`** — REVERSES Phase 42's
deliberate silence on ordinary wins, eight days later, and the reversal is
evidence-led rather than a change of taste. Reported as "I passed two and
the badges didn't appear". The database agreed with the report and with
Phase 42 at the same time: those were wins **#14 and #15**, and both rungs
that participant could have hit (`first_bug`, `bugs_10`) were crossed on
**2026-08-29**, before any of this existed. The ladder was behaving
correctly; the participant had already walked past every rung it had.

**The original objection stands and is answered by INTENSITY, not
abandoned.** An ordinary win gets a small toast carrying the running count
and **no confetti**; a ladder rung keeps the badge **and** the confetti.
Something happens every time; the rungs still feel bigger. `false` restores
Phase 42 exactly, and a test pins that.

**`gamification.confetti_on_round_complete: true`** — confetti when a round
FINISHES, win or lose. *"It's for completing the game."* The reward is for
finishing, which is the behaviour the study needs and the one a
participant controls; winning is not, at ~17% real detection against a 60%
threshold, so celebrating only a clear would mean almost nobody ever saw
it. Reaches the panel as a BUILD-TIME define
(`__CONFETTI_ON_ROUND_COMPLETE__`), like `__CONSENT_VERSION__` — so
changing it needs `npm run build` + Reload, not just a backend restart.

No other values changed.

## 2026-09-10 — CONFIG_VERSION 54c92406c9fd -> 4ea5f3e6b091

**`developer_gate.pass_threshold_pct` 60 -> 50**, set by the study lead
for the 40-participant developer study. With four Defense turns (Phase
43) and `attack_counts_toward_score: false`, the score is won / graded
Defense turns, so **50% means catching 2 of 4 bugs clears the gate**.
This resolves Phase 43's open question: cutting the round from 8 turns to
4 had silently raised the effective bar from 5 of 8 (62.5%) to 3 of 4
(75%).

**PROVISIONAL.** The study lead intends to re-decide it after a pilot pass
(informed by a participant form). It must then be **frozen before the
main run** — changing it mid-study changes what "cleared" means across
participants. None of the developer RQs read pass/fail; they use the
continuous Defense score, so the threshold governs the participant's
experience (how often they replay) rather than the analysis.

`lead_gate.pass_threshold_pct` is untouched (the lead persona is out of
scope for this study). No other values changed.

## 2026-09-24 — CONFIG_VERSION 4ea5f3e6b091 -> 3bac6eccfe72

Two changes from the study lead, both to the developer gate: the round's
**order** and how it is **scored**.

**`developer_gate.attack_counts_toward_score` REMOVED**, replaced by
**`developer_gate.score_weights`** (`{defense: 0.5, attack: 0.5}`) and
**`developer_gate.score_absent_phase`** (`renormalize`).

The score is now half bug-FINDING and half bug-PLANTING, and
`pass_threshold_pct: 50` (unchanged) is applied to that blend. The boolean
is gone rather than kept beside the weights because `attack: 0.0` expresses
exactly what `false` expressed, and two knobs governing one number is how
they drift apart. Phase 19 and Phase 20 each raised "should Attack count?"
as an open question and left it; this answers it.

**It is a weighted mean of the two RATES, not a pooled count of won turns.**
The two coincide only when both phases have the same number of graded turns
— which a single skipped Attack turn breaks. With 4 Defense and 4 Attack
turns fully played they agree; they diverge the moment a participant skips.

**`score_absent_phase` is the load-bearing half of this change.** Skipping
an Attack turn is allowed at every turn and `skip_attack` records
`"skipped"`, not `"lost"`, so a round can legitimately end with the Attack
half of the blend having no value to take.

- **`renormalize` (the default)** weights only the phases that actually
  have graded turns. An all-skipped Attack leaves the score on Defense
  alone, exactly as `attack_counts_toward_score: false` did. This KEEPS the
  promise the removed key's own comment made in as many words: a skipped
  Attack can never penalise the developer.
- **`zero`** gives an absent phase 0 at its full weight, which makes Attack
  effectively COMPULSORY: a flawless 4/4 Defense round with Attack skipped
  scores 50 (only just clearing at 50), and 3 of 4 Defense scores 37.5 and
  FAILS.

The two differ for exactly the participants who skip, so the choice changes
who clears. `renormalize` was chosen because it preserves existing
behaviour for the participants the blend cannot measure; **flipping to
`zero` is a study decision, not a tuning detail.**

**The same blend now ranks the leaderboard.** `combined_score` is a new
public metric and the developer board's second sort key (after
`dev_gates_cleared`), replacing `bug_detection_accuracy` — which stays as a
COLUMN, because it is half of what the new key is made of. The gate and the
board read one implementation, `backend/app/services/scoring.py`, and
`backend/tests/integration/test_score_agreement.py` drives both real call
paths over one pile of attempts and asserts they produce the same number.

**Not a config change, but found while making this one and worth recording
here because it moves a published number:** `compute_raw_metrics` counted
any attempt with a non-`None` outcome as graded, which let `"skipped"`
through — so every declined Attack turn had been landing in
`bug_injection_success_rate`'s DENOMINATOR as a turn the AI successfully
defended. `bug_injection_success_rate` and `graded_attack_turns` will both
change for any participant who has ever skipped an Attack turn. The new
value is the correct one.

**Flow order: 8 Defense -> 5 comprehension -> 4 Attack becomes 4 Defense ->
4 Attack -> 5 comprehension.** No config key governs the order (it is three
transitions in `dev_gate.advance()`), but it is recorded here because it
changes what a `config_version` means for analysis: a comprehension item
answered right after the Defense turns and one answered after four
intervening Attack turns are not exchangeable. **Pilot comprehension
answers must be split on `config_version` before being pooled.** Nothing
enforces that.

`__GATE_PASS_THRESHOLD_PCT__` was added to the extension's build-time
defines so the panel can print the bar beside the score. Like
`__CONSENT_VERSION__`, changing `pass_threshold_pct` now needs
`npm run build` + a Reload as well as a backend restart.

No other values changed.

## 2026-09-25 — CONFIG_VERSION 3bac6eccfe72 -> f7d369c7a085

**Correcting yesterday's entry**, which applied the 50/50 blend to the GATE.
The study lead's intent was two separate numbers, and this splits them.

**The gate clears on bug detection accuracy ALONE.**
`developer_gate.pass_threshold_pct` stays 50, and its comment now says what
it is applied to: won Defense turns over graded Defense turns, i.e. catching
**2 of 4** planted bugs. Attack does not enter the gate score at any point.
`developer_gate.score_weights` and `developer_gate.score_absent_phase`,
added yesterday, are **removed**.

The reason is a boundary worth stating once: **Attack is optional, and
declining it must never stop somebody raising a pull request.** Access to
their own work is not where this study applies pressure.

**`leaderboard.score_weights` (`{defense: 0.5, attack: 0.5}`) and
`leaderboard.score_absent_phase` are NEW**, and the blend now lives only on
the board — where the stated purpose is *"so that participants will also play
attack phase instead of skipping that"*. A skipped Attack turn produces no
datum at all, and rank is the right thing to put at stake for it.

**`score_absent_phase` defaults to `zero`, and this is the setting that makes
the incentive work rather than backfire.** For a developer with a 72%
defense rate:

| | `renormalize` | `zero` |
|---|---|---|
| skips all attack | **72%** | 36% |
| plays attack, wins 40% | 56% | **56%** |
| plays attack, wins 0% | 36% | 36% |

Under `renormalize` — the value shipped yesterday, when the blend was the
gate score — **skipping outranks playing attack well**, because a mediocre
attack rate drags the mean down while skipping leaves the defense rate
untouched. It would have taught participants that the way to protect their
standing is to skip, which is the precise opposite of the request. Under
`zero` playing is always at least as good as skipping, and strictly better
the moment one Attack turn is won. `renormalize` is kept only so the
trade-off is switchable and written down.

`zero` is safe **because the gate does not read this number**. It costs
rank, never access.

One guard the rule does not give for free, added in code rather than config:
a participant with **no graded turns at all** scores `None`, not 0%.
Otherwise both phases would claim their weight against an empty numerator
and somebody who has never played would be published as measurably bad at a
game they have not started. Skipping the attack phase is a choice the blend
is entitled to price; never having played is not a choice at all.

**Known edge, accepted:** a participant who has not yet REACHED the Attack
phase is indistinguishable from one who skipped it — both have zero graded
Attack turns — so both rank at half until they play. It resolves as soon as
they do, and it is the intended pressure.

Everything else from yesterday's entry stands: the flow order
(Defense -> Attack -> comprehension), `combined_score` as a public metric and
the developer board's second sort key, and the `"skipped"`-in-the-denominator
fix to `bug_injection_success_rate`.

No other values changed.

## 2026-10-03 — CONFIG_VERSION f7d369c7a085 -> 737f01d95403

**`leaderboard.score_confidence_turns: 4` ADDED.** Requested by the study
lead: the developer board should consider how many times a participant has
played, not only their rates.

Before this, a raw rate ranked one lucky turn (1 of 1 Defense, 1 of 1
Attack = 100%) above a long good record (30 of 40, 12 of 40 = 52.5%);
volume entered only as the third tiebreak (`turns_won`). Now each phase's
rate is `won / (graded + K)` before the 0.5/0.5 blend — every participant
starts with K pretend-missed turns per phase. With K = 4 those two score
20% and 47.7%, and the order flips.

Chosen over three alternatives the study lead was shown with worked boards:
a participation multiplier (`min(1, graded / TARGET)`), ranking on total
wins, and turns played as the second sort key. The latter two rank pure
volume above accuracy; this one makes volume raise a participant's CEILING
toward their true rate while a lost turn still lowers the score.

Pulled toward 0 deliberately; an anchor at 50% or the population mean lets
a single lucky turn outrank a long good record again. K = 4 is one round's
Defense turns — reasoned, not measured. K = 0 restores the plain rate.

**Ranking only.** `gate_score_pct` is untouched and the merge button is
still released on bug detection accuracy alone. The published
`combined_score` changes for every developer (it is the adjusted value, so
the "Score" column shows the number the board actually sorts on);
`bug_detection_accuracy` and `bug_injection_success_rate` stay plain rates.

## 2026-10-04 — CONFIG_VERSION 737f01d95403 -> 65ffec67da85

**`trigger.min_lines_changed` 100 -> 50**, at the study lead's request. A
pull request now fires the gate once it changes at least 50 lines (insertions
+ deletions, after `ignore_globs`) across at least `min_modules_changed: 3`
module groups, which is unchanged. More, smaller PRs will be gated; the
Phase 44 task design (each task ≥100 lines) no longer needs to be that large
to fire. No code change; backend suite 766 passed, 7 skipped.
