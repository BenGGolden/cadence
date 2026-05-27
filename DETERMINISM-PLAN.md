# Cadence determinism refactor plan

Plan for the backlog item
[Move more slash-command logic into deterministic scripts](./BACKLOG.md).
Each phase shifts a slice of mechanical work — templating, filtering,
table lookups, report rendering — from LLM prose into stdlib Python
under `templates/hooks/` and `scripts/`. The dispatch prose stays the
orchestrator (only the harness calls MCP); each script removes a
slice of prose the model would otherwise re-derive every fire.

Operates under [GUIDEPOSTS #7 Prefer deterministic code to agent
prose](./GUIDEPOSTS.md). Retire this file in the same PR that ships
the last phase, mirroring the HARDENING-PLAN retirement after P9.

Phases land one per feature branch + PR (see `feedback_feature_branch_pr_workflow`).
Phase 1 is prerequisite for the rest because it adds the test
scaffolding every later phase requires; Phases 2–7 are independent
unless noted.

---

## Conventions (apply to every phase)

### Script locations

- `templates/hooks/` — runtime scripts the consumer's `.claude/hooks/`
  calls during dispatch. Scaffolded by `/cadence:init`
  ([commands/init.md](./commands/init.md) Step 4 + the init copy
  table). Stdlib + PyYAML only (PyYAML already permitted via
  [_common.py](./templates/hooks/_common.py)).
- `scripts/` — init-time helpers the plugin calls only during
  `/cadence:init`. Never scaffolded. Stdlib only.

### CLI shape

- `argparse` for required args.
- Structured inputs come in via `--input PATH` to a JSON file the
  bootstrap writes to a temp path (matches
  [parse_comments.py](./templates/hooks/parse_comments.py)).
- Outputs: Markdown to stdout when the prose just prints what comes
  back; JSON to stdout when the prose reads structured fields back
  (matches `validate_workflow.py --evidence`).
- Exit codes (from
  [_common.py](./templates/hooks/_common.py:10-15)): 0 success, 1 bad
  input, 2 validation failure, 3 internal error. Human-readable
  message to stderr on non-zero; the bootstrap prints stderr verbatim.
- All new scripts get a docstring naming their caller(s) — every
  existing helper in `templates/hooks/` already does this; match it.

### Test layout

- `tests/` at the repo root.
- One file per script (`tests/test_<script_name>.py`), stdlib
  `unittest` only.
- Run via `python -m unittest discover -s tests -v` locally and in CI.
- Subprocess invocation when the test cares about the exit-code
  path; direct `main()` call (capturing stdout via `io.StringIO`) when
  the test only cares about output.
- Fixtures under `tests/fixtures/` once inline literals grow past a
  few lines. Byte-identical golden files for templated output
  (Phases 3, 5, 7 all use this pattern).

### CI

Phase 1 adds a `python-tests` job to
[.github/workflows/validate.yml](./.github/workflows/validate.yml).
Every subsequent phase adds its tests and must keep CI green.

---

## Phase 1 — Test scaffolding and coverage for existing scripts

**Motivation.** `validate_workflow.py`, `parse_comments.py`, and
`emit_tracking_comment.py` ship today with zero automated coverage.
Every later phase modifies or sits next to them; a regression net
must exist before they change.

**Deliverables**

- `tests/` directory at the repo root.
- `tests/test_validate_workflow.py` — exercise rules 1–8 (pass + fail
  paths each), `--evidence` output shape, exit codes (1 unparseable
  YAML, 2 rule failure, 0 success), `workflow_linear_states` order.
- `tests/test_parse_comments.py` — empty list; `attempt_count`
  against `--target-state`; `rework_count` against `--gate-name`;
  `rework_context` excludes Cadence/Stokowski + bot comments and is
  oldest-first; `latest_implementer_summary.pr_url` + `branch`
  extraction; legacy `stokowski:`/`run`/`timestamp` normalisation;
  malformed tracking-comment JSON populates `parse_errors` without
  failing.
- `tests/test_emit_tracking_comment.py` — every documented
  `--kind` × `--status` combination; missing-required-arg paths exit
  1; error-string truncation at 400 chars; newline collapsing in
  errors; emitted JSON validates as JSON.
- `.github/workflows/validate.yml`: new job `python-tests` (Python
  3.12 + `pip install pyyaml`) running the discovery, blocking merge
  on failure.
- `scripts/README.md`: "Tests" section pointing at the command and
  the fixtures path.

**Acceptance criteria**

- [ ] **AC-1** `python -m unittest discover -s tests -v` passes locally
  (Windows + WSL/Linux) and in CI.
- [ ] **AC-2** The `python-tests` CI job appears alongside
  `plugin-manifest` and `command-frontmatter`, runs on push and PR,
  and blocks merge on failure.
- [ ] **AC-3** Removing `_rule6_max_in_flight` from
  [validate_workflow.py](./templates/hooks/validate_workflow.py)
  causes at least one `test_validate_workflow.py` test to fail. (The
  suite must detect rule loss, not just exercise rules.)
- [ ] **AC-4** Removing the implementer-summary author-match
  constraint in
  [parse_comments.py:197](./templates/hooks/parse_comments.py#L197)
  causes at least one `test_parse_comments.py` test to fail.
- [ ] **AC-5** `scripts/README.md` documents the discover command and
  the fixtures convention.

**Manual testing**

Not required — this phase changes no dispatch behaviour. Smoke-test
locally before pushing; CI does the rest.

---

## Phase 2 — Validator emits `linear_to_workflow` reverse map

**Motivation.** [tick.md step 8](./commands/tick.md) and
[status.md step 2](./commands/status.md) both re-derive a Linear-column
→ workflow-state lookup; status.md does it explicitly, tick.md does
it inline ("find the single workflow state whose `linear_state`
equals it"). The validator already has the data. Emitting it once
costs nothing and removes both ad-hoc derivations. Small phase, but
unblocks Phase 5's status cleanup.

**Deliverables**

- Extend
  [validate_workflow.py](./templates/hooks/validate_workflow.py) to
  include a `linear_to_workflow` field in its JSON output. Shape:

  ```json
  {
    "<Linear column name>": {
      "kind": "pickup" | "state" | "gate_waiting",
      "workflow_state": "<name>" | null,
      "linear_state_type": "agent" | "gate" | "terminal" | null
    }
  }
  ```

- Update [tick.md step 8](./commands/tick.md): replace "Find the single
  workflow state whose `linear_state` equals it" with a direct
  `linear_to_workflow` lookup on the validator's existing output.
- Update [status.md step 2](./commands/status.md): replace the manual
  `linearToWorkflow` construction with "use the validator's
  `linear_to_workflow` from step 1." Drop the conflict-detection
  prose — the validator's Rule 1 already catches duplicates.
- `tests/test_validate_workflow.py`: add cases asserting
  `linear_to_workflow` shape for the default workflow, a gate-only
  workflow, and a workflow with a custom pickup state name.

**Acceptance criteria**

- [ ] **AC-1** Validator output has the default workflow's
  `linear_state` for `implement` mapped to `{"kind": "state",
  "workflow_state": "implement", "linear_state_type": "agent"}`.
- [ ] **AC-2** Pickup column mapped to `{"kind": "pickup",
  "workflow_state": null, "linear_state_type": null}`.
- [ ] **AC-3** Each gate's waiting column mapped to `{"kind":
  "gate_waiting", "workflow_state": "<gate>", "linear_state_type":
  "gate"}`.
- [ ] **AC-4** [tick.md step 8](./commands/tick.md) no longer contains
  the prose phrase "Find the single workflow state whose
  `linear_state` equals it."
- [ ] **AC-5** [status.md step 2](./commands/status.md) no longer
  constructs `linearToWorkflow` manually.

**Manual testing**

- `/cadence:tick dry-run`: confirm the validation-section JSON now
  includes `linear_to_workflow`.
- `/cadence:status`: output unchanged byte-for-byte vs. pre-refactor
  (capture before merging, diff after).

---

## Phase 3 — Extract `compose_lifecycle_context.py`

**Motivation.** [tick.md step 13](./commands/tick.md) is the largest
pure-templating block in the codebase (~140 lines of prose
describing a Markdown render with branches for rework and
adversarial-context). Every fire pays tokens to re-derive the same
shape; prose edits silently change the contract subagents depend on.
The script absorbs the full subagent-prompt construction — not just
the Markdown render, but also the input shaping, the globalPrompt
append, and the dry-run placeholder issue — so the prose's job
collapses to "hand the data you already have to this script."

**Deliverables**

- `templates/hooks/compose_lifecycle_context.py`. CLI:

  ```
  compose_lifecycle_context.py
    --workflow-config <validatorOutputPath>
    --issue <issueJsonPath>
    --target-state <name>
    --attempt <int>
    --parse-comments-output <parseCommentsOutputPath>
    [--global-prompt-path .claude/prompts/global.md]
    [--default-branch main]
    [--dry-run]
  ```

  Reads everything internally:
  - From `--workflow-config`: `states[target_state]` (gives
    `linear_state`, `type`, `adversarial_context`,
    `next`/`on_approve`/`on_rework`) and the resolved `next_state`
    block. Plus `linear.team` for branch-name derivation.
  - From `--issue`: the raw MCP issue object (`identifier`, `title`,
    `url`, `branchName`, `priority`, `labels`, `description`). The
    bootstrap dumps the MCP response to a temp file once — no
    shaping required.
  - From `--parse-comments-output`: `rework_context` (the rework
    comments) and `latest_implementer_summary.pr_url` (for the
    adversarial-context variant).
  - From `--global-prompt-path`: appended verbatim after two blank
    lines. Missing file → empty string (matches today's step 2).
  - With `--dry-run`: ignores `--issue` / `--parse-comments-output`
    / `--attempt` and synthesises the EXAMPLE-1 placeholders from
    step 0 internally; everything else (globalPrompt append, etc.)
    still runs. The dry-run still requires `--workflow-config` so
    the `entry` state's `linear_state` is real, matching step 0's
    current behaviour.

  Stdout is the **full subagent user prompt** — Lifecycle Context
  block + two blank lines + globalPrompt — ready to hand to the
  Agent tool's `prompt` parameter.

- Update [tick.md step 13](./commands/tick.md): replace the ~140 lines
  with "Invoke Bash `python
  "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/compose_lifecycle_context.py
  --workflow-config <validatorOutputPath> --issue <issueJsonPath>
  --target-state <targetState> --attempt <attempt>
  --parse-comments-output <parseCommentsOutputPath>`. The script's
  stdout is the full subagent prompt; pass it as the Agent tool's
  `prompt` in step 14."

- **Delete [tick.md step 2 (Read global prompt)](./commands/tick.md)
  entirely.** Its only consumer was step 13; the script handles the
  read now. Renumber subsequent steps OR keep step numbers and mark
  step 2 as `(removed in <phase-shipped-CHANGELOG-ref>)` — keeping
  numbers stable is the safer call so external references (tests,
  commits) don't churn.

- Update [tick.md step 0 (Dry-run branch)](./commands/tick.md): the
  current "compose the Lifecycle Context block (see step 13) for a
  *hypothetical* issue" with the verbatim placeholder list collapses
  to "invoke `compose_lifecycle_context.py --workflow-config
  <validatorOutputPath> --dry-run`; the script's stdout is the
  rendered Lifecycle Context. Print it under the
  **Lifecycle Context (composed):** section." The hardcoded
  EXAMPLE-1 placeholder list moves into the script.

- Update [init.md](./commands/init.md) Step 4 copy table + Step 5
  "Files written" list with the new script.

- `tests/test_compose_lifecycle_context.py` matrix (each test writes
  the four input files into a `TemporaryDirectory` and invokes the
  script):
  - Default (non-adversarial, no rework, next state `type: agent`).
  - Default with rework context: 1 comment / 2 comments / 0
    comments-but-rework-marked (boilerplate fallback).
  - Default with `next_state.type == "gate"` (extra "Gate
    downstream" line); `next_state.type == "terminal"` (extra
    "Terminal state" line).
  - Adversarial-context: no PR URL → no PR: line; with PR URL → PR:
    line present.
  - Adversarial-context: `--default-branch` flag honoured; defaults
    to `main`.
  - Adversarial-context with rework section preserved
    (step 13's contract).
  - Branch derivation: `issue.branchName` present uses verbatim;
    absent derives `<team-key-lower>/<identifier-lower>-<title-slug>`
    with title-slug ≤ 50 chars (`team_key` comes from the workflow
    config's `linear.team`).
  - Priority rendering: "`2 (High)`" form when numeric; "(none)"
    when null.
  - Labels: comma-separated; "(none)" when empty.
  - `--global-prompt-path` pointing at a present file → contents
    appear after two blank lines; missing file → no appended content
    (and no trailing blank lines from a phantom append).
  - `--dry-run` → byte-identical to a stored
    `tests/fixtures/lifecycle_context/dry_run.md` for the default
    workflow config.

**Acceptance criteria**

- [ ] **AC-1** Output stdout for the default fixture is byte-identical
  to `tests/fixtures/lifecycle_context/default.md` (stored in this PR).
- [ ] **AC-2** Output stdout for the adversarial-context fixture is
  byte-identical to
  `tests/fixtures/lifecycle_context/adversarial.md`.
- [ ] **AC-3** Output stdout for the rework-with-two-comments fixture
  is byte-identical to `tests/fixtures/lifecycle_context/rework.md`.
- [ ] **AC-4** Output stdout for `--dry-run` against the default
  workflow config is byte-identical to
  `tests/fixtures/lifecycle_context/dry_run.md`.
- [ ] **AC-5** Missing required arg → exit 1 with clear stderr;
  malformed `--issue` JSON → exit 1.
- [ ] **AC-6** [tick.md](./commands/tick.md) no longer contains the
  literal strings `## Lifecycle Context`, `### Transitions`,
  `<!-- AUTO-GENERATED BY CADENCE`, or the hardcoded EXAMPLE-1
  placeholder list (`Hypothetical entry-state issue`).
- [ ] **AC-7** [tick.md](./commands/tick.md) no longer contains a
  step that reads `.claude/prompts/global.md`. The script owns that
  read.
- [ ] **AC-8** [init.md](./commands/init.md) Step 4 copy table
  includes the new script, and Step 5's "Files written" block lists
  `.claude/hooks/compose_lifecycle_context.py`.

**Manual testing**

- Capture `/cadence:tick dry-run` output against the default
  `workflow.yaml` **before** merging this PR. Apply Phase 3. Re-run
  the dry-run. Diff byte-for-byte — both the Lifecycle Context block
  and the appended globalPrompt should match.
- Live `/cadence:tick` fire (Mode B, `claude /loop`) against a test
  Linear issue that has gone through at least one rework round, so
  default + rework branches both exercise. The implementer
  subagent's first user message in its transcript must match the
  script's stdout exactly.

---

## Phase 4 — Extract `filter_candidates.py`

**Motivation.** [tick.md step 5](./commands/tick.md) holds the
candidate filter, priority sort, and bounded reachability walk for
over-cap states. The MCP queries themselves stay in prose
(constraint: scripts can't call MCP), but the script can also tell
prose **which** queries to run (plan-then-act) and render the empty-
candidates message itself — so prose's job shrinks to "run the
queries the script told you to, hand back the results." P8.2 shipped
a bounded-walk correctness fix specifically because the prose was
hard to audit; a tested script closes that gap permanently.

**Deliverables**

- `templates/hooks/filter_candidates.py` with two modes:

  **Plan mode** (`--plan --workflow-config <path>`) emits JSON:

  ```json
  {
    "pickup_query": {
      "team": "ENG",
      "project_slug": "cadence",
      "workflow_linear_states": ["Todo", "Planning", "In Progress", ...]
    },
    "in_flight_queries": [
      {"state_name": "plan_review", "linear_state": "Planning Review"},
      {"state_name": "implement",   "linear_state": "In Progress"}
    ]
  }
  ```

  `pickup_query.project_slug` is `null` when absent in the config (so
  prose knows to omit the project filter rather than pass an empty
  string). `in_flight_queries` is empty when no state declares
  `max_in_flight`. Prose runs the pickup query once with these
  parameters and one in-flight query per entry — no per-state
  scanning of the validator output in prose.

  **Filter mode** (`--workflow-config <path> --candidates <path>
  --in-flight <path>`) reads:
  - `--candidates`: JSON array of raw MCP results (`identifier`,
    `current_linear_state`, `labels`, `priority`, `createdAt`,
    optional `blockers` array of blocker-linear-state strings).
  - `--in-flight`: JSON map `{state_name: int}` of current in-flight
    counts (one entry per `in_flight_queries` element from plan mode).

  Emits JSON:

  ```json
  {
    "ordered_identifiers": ["ENG-3", "ENG-1", ...],
    "over_cap_states_that_blocked": ["plan_review"],
    "diagnostic_message": "No eligible issues.\n(caps reached for: plan_review)"
  }
  ```

  `diagnostic_message` is `null` when `ordered_identifiers` is
  non-empty; otherwise it's the multi-line message prose prints
  verbatim. The cap-reached parenthetical is omitted when no caps
  blocked anything (matches today's behaviour: a brand-new board
  with no candidates prints just `No eligible issues.`).

  Encapsulates: workflow-Linear-states membership filter, active /
  needs-human filter, blocker-resolved filter (skipped when blocker
  field absent), gate-waiting-without-verdict filter, priority +
  `createdAt` sort, per-candidate reachability walk bounded at the
  first gate or terminal, drain exemption for verdict-bearing
  candidates at their own gate (P8.2). The three "Examples for the
  default workflow" walk-through bullets currently in
  [tick.md step 5](./commands/tick.md) move into the script's
  docstring + tests.

- Update [tick.md step 5](./commands/tick.md). The new shape:

  1. Invoke `filter_candidates.py --plan --workflow-config
     <validatorOutputPath>` → query plan JSON.
  2. Run the MCP pickup query with `pickup_query.team`,
     `pickup_query.project_slug` (omit the project filter when
     null), and `pickup_query.workflow_linear_states`. The
     "verbatim / no-fallback / no-broader-retry" guardrails
     (current [tick.md:180-199](./commands/tick.md#L180-L199)) stay
     in prose — they constrain the LLM's MCP-calling behaviour, not
     the script.
  3. For each entry in `in_flight_queries`, run a per-state MCP
     query for the `linear_state` column and record the count.
  4. Write the pickup-query results and in-flight counts to temp
     files. Invoke `filter_candidates.py --workflow-config
     <validatorOutputPath> --candidates <candidatesPath> --in-flight
     <inFlightPath>`.
  5. If `diagnostic_message` is non-null, print it verbatim and exit.
     Otherwise `candidates = ordered_identifiers`; proceed to step 6.

- Update [init.md](./commands/init.md) copy table + next-steps file
  list.

- `tests/test_filter_candidates.py` matrix:

  Plan mode:
  - Workflow with no caps → empty `in_flight_queries`.
  - Workflow with caps on agent + gate → both surface, with correct
    `linear_state` names.
  - `linear.project_slug` absent → `pickup_query.project_slug` is
    `null` (not empty string).

  Filter mode:
  - Empty candidates → empty ordered list, `diagnostic_message ==
    "No eligible issues."` (no parenthetical when no caps blocked
    anything).
  - Candidates filtered out by `cadence_active` /
    `cadence_needs_human` labels.
  - Candidates in foreign Linear columns dropped.
  - Blockers absent vs present (present + unresolved → dropped).
  - Gate-waiting candidates without verdict dropped.
  - Priority + `createdAt` sort stable on ties (input order
    preserved within a tie).
  - Reachability walk: candidate at pickup, walk = `entry → next
    gate`; over-cap on a downstream agent drops candidate.
  - Reachability walk: verdict-bearing candidate at a capped gate is
    NOT dropped by its own gate's cap (drain exemption).
  - Reachability walk bounded at first gate/terminal; over-cap on a
    state *past* the boundary doesn't affect candidate.
  - `over_cap_states_that_blocked` reports only states that actually
    blocked ≥ 1 candidate.
  - Empty result from cap filtering → `diagnostic_message ==
    "No eligible issues.\n(caps reached for: <names>)"` with names
    matching `over_cap_states_that_blocked`.

**Acceptance criteria**

- [ ] **AC-1** Plan mode on the default workflow returns
  `in_flight_queries` containing exactly the states whose config has
  `max_in_flight` set, each with the correct `linear_state`.
- [ ] **AC-2** Plan mode with `linear.project_slug` absent from the
  config returns `pickup_query.project_slug == null` (asserted as
  JSON null, not `""`).
- [ ] **AC-3** Filter mode: default workflow + one `Todo` candidate +
  `plan_review` at cap → `ordered_identifiers == []`,
  `over_cap_states_that_blocked == ["plan_review"]`,
  `diagnostic_message ==
  "No eligible issues.\n(caps reached for: plan_review)"`.
- [ ] **AC-4** Filter mode: verdict-bearing candidate at
  `human_review` with `cadence_approve`, `human_review` at cap →
  candidate IS in `ordered_identifiers` (drain exemption).
- [ ] **AC-5** Filter mode: candidate at `plan_review` with
  `cadence_rework` (walk = `implement → agent_review → human_review`),
  `human_review` at cap → candidate dropped, `human_review` in the
  blocker list.
- [ ] **AC-6** Filter mode: two candidates with identical priority
  and `createdAt` appear in input order in `ordered_identifiers`,
  across 10 repeated runs.
- [ ] **AC-7** [tick.md step 5](./commands/tick.md) no longer contains
  the words "reachability walk", "over-cap", or the bulleted "Examples
  for the default workflow" block. Step 5 no longer iterates the
  workflow config looking for `max_in_flight` keys — that scan moves
  into the script's plan mode.
- [ ] **AC-8** [init.md](./commands/init.md) updated.

**Manual testing**

- Linear board with 3 issues queued in `Todo`, `plan_review` capped
  at 1, one issue already there. Run `/cadence:tick`. Confirm output
  reports `(caps reached for: plan_review)` and no issue is picked
  up. Add a fourth `Todo` issue — same result.
- Approve one `human_review` issue while `human_review` is at cap.
  Run `/cadence:tick`. Confirm the approval drains normally (drain
  exemption).
- Board with no candidates (everything in foreign columns). Run
  `/cadence:tick`. Confirm output is the bare `No eligible issues.`
  with no parenthetical.

---

## Phase 5 — Extract `render_status_report.py`

**Motivation.** [status.md step 5](./commands/status.md) is ~120
lines of prose describing Markdown table renders, per-state summary
lines, optional concurrency table, and config-warnings rendering.
Same shape every run — pure templating. Depends on Phase 2 for the
validator's `linear_to_workflow` to be clean.

**Deliverables**

- `templates/hooks/render_status_report.py` — `--input PATH` reads a
  JSON file the bootstrap writes containing: validator output
  (already has `states`, `linear_to_workflow`, optional `evidence`);
  issue list (each with `identifier`, `title`, `state_name`,
  `priority`, `updatedAt`, `labels`, `attempt_count`, `last_state`);
  `now` (UTC ISO 8601); `team`, `project_slug`, `pickup_state` for
  the header; optional `degraded_issues` list for the Config
  warnings section. Emits the full Markdown report to stdout.
- Update [status.md step 5](./commands/status.md): keep steps 1–4 (the
  data-gathering prose). Replace step 5 with "Write the report-input
  JSON (shape: see `render_status_report.py` docstring) to a temp
  file; invoke the script; print its stdout verbatim."
- Update [init.md](./commands/init.md) copy table.
- `tests/test_render_status_report.py`:
  - Empty issue set → "*No issues currently in workflow states.*"
    sentinel.
  - Mixed state types (agent + gate + terminal + pickup) render
    correctly per the workflow-state column table.
  - Gate verdict cells: none / approve only / rework only / both —
    all four shapes.
  - Gate per-state summary collapses to single-line when all in
    awaiting bucket.
  - Concurrency table: omitted when no state declares
    `max_in_flight`; AT CAP / OVER CAP rendering by count.
  - Config warnings: validator exit 2 surfaces `evidence` FAIL
    blocks; degraded parse hits surfaced.
  - Footer line always present.

**Acceptance criteria**

- [ ] **AC-1** Default-workflow + one-issue-per-state fixture →
  byte-identical to `tests/fixtures/status/default_full.md`.
- [ ] **AC-2** Empty-issue-set fixture → byte-identical to
  `tests/fixtures/status/empty.md` (contains the sentinel line, no
  Concurrency section).
- [ ] **AC-3** Fixture with one at-cap state and one over-cap state →
  Concurrency table surfaces `AT CAP` and `OVER CAP` correctly.
- [ ] **AC-4** [status.md step 5](./commands/status.md) no longer
  contains literal `| ID | Title | Linear column |`, `### Per-state
  counts`, or `### Concurrency` table headers — they move to the
  script.
- [ ] **AC-5** [init.md](./commands/init.md) updated.

**Manual testing**

- Capture `/cadence:status` output against a real test Linear board
  before merging. Apply Phase 5. Re-run. Diff byte-for-byte.
- Deliberately misconfigure the workflow (duplicate `linear_state`
  values). Run `/cadence:status`. Confirm Config warnings section
  surfaces the validator's failure blocks.

---

## Phase 6 — Extract sweep classification + reporting; extend `emit_tracking_comment.py` with `--kind sweep`

**Motivation.** [sweep.md](./commands/sweep.md) encodes time math
(steps 2 + 4), the `<!-- cadence:sweep ... -->` body shape inline in
prose (step 5), and the summary report (step 6). The sweep-comment
body bypasses the existing tracking-comment emitter; folding it in
keeps every Cadence-emitted comment going through one canonical
formatter.

**Deliverables**

- Extend
  [emit_tracking_comment.py](./templates/hooks/emit_tracking_comment.py)
  with `--kind sweep` plus `--cleared-at`, `--last-activity`,
  `--stale-minutes`, `--threshold-minutes`. Emits the existing
  `<!-- cadence:sweep ... -->` shape (matches today's
  [sweep.md:128-132](./commands/sweep.md#L128-L132)).
- `templates/hooks/render_sweep_report.py` — `--input PATH` reads a
  JSON file containing: list of locked issues (each with
  `identifier`, `title`, `updated_at`, `state_name`); `now` (UTC ISO
  8601); `threshold_minutes`. Classifies stale vs fresh, computes
  per-issue stale_minutes, emits the full summary Markdown to stdout
  (the `## Cadence sweep — <now>` block with both tables) and the
  classification (which issues are stale) as JSON to stderr (so
  prose iterates the right list for the per-issue MCP writes in
  sweep.md step 5).
  - Decision rationale: returning classification on stderr keeps
    stdout pure Markdown the prose just prints, while still giving
    the prose a machine-readable list of stale identifiers. Document
    this dual-stream contract in the docstring.
- Update [sweep.md](./commands/sweep.md):
  - Step 4 becomes "invoke `render_sweep_report.py`, read the JSON
    classification from stderr; hold stdout for step 6."
  - Step 5's sweep-comment body becomes "invoke `emit_tracking_comment.py
    --kind sweep ...`".
  - Step 6 becomes "print the stdout captured in step 4."
- Update [init.md](./commands/init.md) copy table.
- `tests/test_emit_tracking_comment.py` — add `--kind sweep` cases:
  required args, integer `stale_minutes` (rejects strings via
  argparse `type=int`), valid JSON in the emitted body.
- `tests/test_render_sweep_report.py`:
  - Empty locked list → "(none cleared)" and "(none)" tables.
  - All stale → Cleared table populated, Still locked empty.
  - All fresh → opposite.
  - Mixed → both populated, sorted by `updatedAt` ascending.
  - Title truncation at ~60 chars with trailing `…`.

**Acceptance criteria**

- [ ] **AC-1** `emit_tracking_comment.py --kind sweep --cleared-at
  2026-05-26T12:00:00Z --last-activity 2026-05-26T11:00:00Z
  --stale-minutes 60 --threshold-minutes 30` emits a body with the
  JSON HTML comment AND the visible "**[Cadence]** Stale lock
  cleared..." line. JSON validates.
- [ ] **AC-2** `render_sweep_report.py` with a 2-stale + 1-fresh
  fixture → Cleared table has 2 rows, Still locked has 1 row, both
  ordered by `updatedAt` ascending.
- [ ] **AC-3** `--threshold-minutes 0` (all stale) and
  `--threshold-minutes 99999` (none stale) both produce well-formed
  reports.
- [ ] **AC-4** [sweep.md](./commands/sweep.md) no longer contains the
  literal `<!-- cadence:sweep` prefix (moved to emitter) or the
  Markdown table headers from step 6 (moved to renderer).
- [ ] **AC-5** [init.md](./commands/init.md) updated.

**Manual testing**

- Linear board with 2 `cadence_active`-labelled issues — one
  updated 2 min ago, one updated 60 min ago, threshold 30. Run
  `/cadence:sweep`. Confirm: stale issue has label removed +
  sweep comment posted; fresh issue untouched; report renders
  both tables.

---

## Phase 7 — Init-time scripts: MCP namespace detection + next-steps render

**Motivation.** [init.md Step 4c](./commands/init.md) detects the
Linear MCP namespace by running `claude mcp list` and parsing
output with a regex described in prose, then falls back to
`.mcp.json`. [init.md Step 5](./commands/init.md) renders a ~50-line
verbatim block with conditional lines. Both are templating +
parsing — low individual impact (init runs once per setup) but
finishes the principle's coverage and improves the audit trail.

**Deliverables**

- `scripts/detect_linear_mcp_namespace.py` — `--mcp-list-stdin` reads
  the `claude mcp list` output from stdin, `--mcp-json-path` falls
  back to a `.mcp.json` path. Emits the detected namespace on
  stdout (exit 0), or empty stdout with exit 2 when detection fails.
- `scripts/render_next_steps.py` — emits the full "Cadence
  initialised." block to stdout. Args: `--settings-local-written
  {true|false}`, `--permissions-detection-note '<text>'`,
  `--permissions-block '<text>'`.
- Update [init.md Step 4c](./commands/init.md): replace the
  namespace-detection prose with "invoke `claude mcp list`, pipe its
  output to `detect_linear_mcp_namespace.py --mcp-list-stdin`. If
  the script exits 2, fall back via `--mcp-json-path .mcp.json`; if
  that also exits 2, treat detection as failed."
- Update [init.md Step 5](./commands/init.md): replace the literal
  block with "invoke `scripts/render_next_steps.py --settings-local-written
  {bool} --permissions-detection-note '<text>' --permissions-block
  '<text>'`; print its stdout verbatim."
- Update [scripts/README.md](./scripts/README.md) table with the
  two new scripts.
- `tests/test_detect_linear_mcp_namespace.py`:
  - Each of the three namespaces (`linear`, `linear-server`,
    `claude_ai_Linear`) detected from a representative `claude mcp
    list` fixture.
  - Multiple Linear servers → first match wins, extras reported on
    stderr.
  - Empty input + no `.mcp.json` → exit 2.
  - Fallback: empty `claude mcp list` but `.mcp.json` has a Linear
    server → detection succeeds via JSON path.
- `tests/test_render_next_steps.py`:
  - Success path → byte-identical to
    `tests/fixtures/init/next_steps_success.md`.
  - Failure path → byte-identical to
    `tests/fixtures/init/next_steps_failure.md` (omits the
    `.claude/settings.local.json` line).

**Acceptance criteria**

- [ ] **AC-1** `detect_linear_mcp_namespace.py --mcp-list-stdin` fed
  output containing `* linear-server` returns `linear-server` on
  stdout, exit 0.
- [ ] **AC-2** Empty stdin + missing `.mcp.json` → empty stdout, exit
  2.
- [ ] **AC-3** `render_next_steps.py` success-path call produces
  byte-identical output to the stored success fixture.
- [ ] **AC-4** `render_next_steps.py` failure-path call produces
  byte-identical output to the stored failure fixture, with the
  `.claude/settings.local.json` line omitted from "Files written".
- [ ] **AC-5** [init.md Step 4c](./commands/init.md) no longer
  contains the regex `^\s*([A-Za-z0-9_-]*[Ll]inear[A-Za-z0-9_-]*)\s`
  — it moves to the script.
- [ ] **AC-6** [init.md Step 5](./commands/init.md) no longer contains
  the literal "Cadence initialised." block.

**Manual testing**

- `/cadence:init` on a throwaway repo with the official Linear MCP
  under each of the three namespaces (or simulate by piping
  fixture `mcp list` output to the detection script directly).
  Confirm the next-steps block surfaces the right namespace each
  time.
- `/cadence:init` on a repo with no Linear MCP. Confirm the
  placeholder text appears and `.claude/settings.local.json` is
  NOT written.

---

## Out of scope

- **tick.md step 16** (advance Linear state). The dispatch
  (`next.type == agent` → state move; `gate` → emit waiting marker
  + state move; `terminal` → state move) is too small to justify
  a script: the dispatch already invokes `emit_tracking_comment.py`
  for the waiting marker, and the actual MCP write must stay in
  prose. Extracting saves ~8 lines at the cost of another file.
- **AC validation in [create-ticket.md Step 3b](./commands/create-ticket.md)**.
  The prose itself names this as the judgment work that
  justifies running in prose at all. GUIDEPOSTS #7 explicitly
  carves out judgment work.
- **Subagent template prose** (`templates/agents/*.md`). Subagents
  are LLM bodies by definition; not a refactor target.
- **The three event-hook scripts** (`validate_tracking_json.py`,
  `validate_workflow_on_prompt.py`, `audit_linear_writes.py`).
  Already deterministic. Phase 1 adds them to the coverage suite
  if time permits, but no extraction.

---

## Retirement

When the last phase ships and `BACKLOG.md`'s "Move more
slash-command logic into deterministic scripts" entry is removed,
delete this file in the same PR. CHANGELOG entry per phase records
what landed.
