# Changelog

All notable changes to Cadence are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed — PR operations via GitHub MCP, owned by the bootstrap
- **GitHub pull-request operations moved from the `gh` CLI to the GitHub MCP
  connector, and from the subagents to the bootstrap.** The implementer now
  only `git push`es a branch and returns the PR title/body; the **bootstrap**
  creates the PR (reusing the open PR on a rework run), and — for a
  `merge_on_approve` gate — reads PR state and merges, all via GitHub MCP
  (`create_pull_request` / `list_pull_requests` / `get_pull_request` /
  `merge_pull_request`). PR creation/merge is a transition-coupled side-effect,
  so it belongs to the orchestrator (GUIDEPOSTS #6), like AC promotion and the
  merge step before it.
- **`gh` is gone entirely** — no `GH_TOKEN`, no `GH_REPO`, no `apt install gh`
  setup script, no remote-detection fragility. Both `git` push and the GitHub
  MCP tools authenticate via the routine's **bound GitHub repository** (or the
  local GitHub connector under `/loop`), and scope to that repo on their own,
  so **no repo config** is added. Connector tools auto-allow their writes
  during a run.
- The reviewer drops its supplementary `gh pr view` file-stats call; it reads
  the diff via `git diff` only (already its primary source).
- **`merge_args` → `merge_method`.** The merge gate's config field now takes a
  GitHub merge method (`merge` / `squash` / `rebase`, default `squash`) instead
  of raw `gh pr merge` flags. Validator Rule 9 validates `merge_method` and
  rejects a leftover `merge_args` key with a migration message.
- `/loop`-local users now also need the **GitHub connector / MCP** configured
  in their local Claude Code — there is no `gh` fallback.

### Added — opt-in `merge_on_approve` gate field
- A gate whose `on_approve` is a terminal may set `merge_on_approve: true`
  (with optional `merge_method`, default `squash`) to merge the issue's PR when
  a human approves, before the card advances to the terminal — closing the
  "approved but the PR is still open" gap. No new workflow state, subagent, or
  Linear column: the merge is a transition-coupled side-effect the bootstrap
  owns, mirroring AC promotion. The bootstrap reads PR state first
  (GitHub MCP `get_pull_request`) and is idempotent — an already-merged PR
  (e.g. merged manually) advances cleanly. A merge failure (CI red, conflicts,
  branch protection) or a closed-unmerged PR adds `cadence-needs-human`, posts
  a failure comment, and leaves the card in the gate's waiting column (no
  terminal move). The decision (`merged` → advance, `open` → merge, else →
  escalate) is made inline in the bootstrap from the REST `{state, merged}`
  shape. A `merge` tracking-comment kind in `emit_tracking_comment.py` records
  the outcome. Validator Rule 9 constrains the fields. Off by default — no
  shipped `workflow.yaml` sets `merge_on_approve`, so existing consumers see
  zero behavior change.

### Removed — redundant audit-log hook
- Removed the `audit_linear_writes.py` `PostToolUse` hook and `.cadence/audit.log`.
  Linear's native activity history (comment threads + issue activity feed)
  already is the durable, out-of-band forensic trail; the local log was a
  redundant, per-fire-ephemeral copy of data Linear stores permanently. Dropped
  the `PostToolUse` settings block, the scaffold row, and the merge-list entry.
  Also removed two BACKLOG items (durable audit log in `/schedule` mode; surface
  routine failures to the operator).

### Changed — planner authors acceptance criteria instead of refusing
- The planner subagent no longer refuses tickets that lack an
  `## Acceptance Criteria` block. Instead it produces the plan as normal and
  emits a `## Proposed Acceptance Criteria` section in its summary. When the
  operator already wrote some AC, it augments — proposing only the gap items,
  never rewriting existing AC. The old "Cannot plan — ticket missing
  acceptance criteria" refusal (which burned the whole attempt budget per
  malformed ticket) is gone. `templates/agents/planner.md`.
- **New deterministic helper** `templates/hooks/promote_acceptance_criteria.py`
  merges the planner's latest proposed AC into the description's
  `## Acceptance Criteria` block, idempotently (dedupes already-present items;
  appends a fresh block when none exists; first `## Acceptance Criteria` H2
  wins). It performs no Linear write — it only computes the new body.
- **Promote-on-approval timing.** The bootstrap promotes proposed AC into the
  issue description **only when the plan is approved at `plan_review`**, not at
  plan time. `route_fire.py` now emits a `promote_ac` flag (true exactly for a
  gate **approve** into a non-terminal agent state); `commands/tick.md` Step 6
  Execute runs the helper on that flag, writes the merged description back via
  the Linear MCP, and **re-reads the issue** so the implementer is composed
  against the promoted AC. A rework round leaves the description AC-free and the
  re-running planner re-proposes — no marker or distinguishing state needed.
- **New bootstrap Linear-write surface:** issue-description update (previously
  the bootstrap only created comments and moved state / labels). Logged by
  `templates/hooks/audit_linear_writes.py` (`description: …` summary line).
- `/cadence:create-ticket` is now an *optional* faster local AC-drafting path,
  not a precondition. README "Ticket quality" section rewritten accordingly.

### Changed — renumber tick.md steps to a contiguous sequence
- `commands/tick.md` now numbers its sections `Step 0` (dry-run) followed by a
  contiguous `Step 1`–`Step 13`. The two `Step N — (removed in determinism …)`
  placeholder stubs are deleted and the ranged `Steps 8–11 — Route the fire`
  heading becomes a single `Step 6`. Mapping (old → new, 0/1 unchanged):
  `4→2, 5→3, 6→4, 7→5, (8–11)→6, 12→7, 13→8, 14→9, 15→10, 16→11, 17→12, 18→13`.
- **No behavioural change.** tick.md is dispatch prose, not parsed by any code;
  routing logic and the Gather→Route→Execute structure are untouched. Every
  *live* cross-reference to a tick.md step number moved in lockstep — docstrings
  in `validate_workflow.py`, `compose_lifecycle_context.py`,
  `filter_candidates.py`, `emit_tracking_comment.py`, `classify_drift.py`,
  `route_fire.py`, plus `status.md` prose, `merge_settings_permissions.py`
  comments, and test comments. A stale `validate_workflow.py` reference to
  "step 3 (live validation)" (two refactors out of date) was corrected to step 1.
- The `route_fire.py` / `test_route_fire.py` `Step 8/9/10/11` decision taxonomy
  and all "old step N" historical references are deliberately left as-is — they
  name the pre-extraction routing decisions, not current tick.md headings.
- Clears the "tick.md contiguous step renumber" item from `BACKLOG.md` (the
  determinism pass that added `route_fire.py` deferred it to keep that diff
  focused on behaviour).

### Changed — fold the validator-output scratch hop into the consumers
- The bootstrap no longer writes `validate_workflow.py`'s JSON to
  `.cadence/validator-output.json` and threads it (`--workflow-config
  <validatorOutputPath>`) into the downstream deterministic helpers.
  `route_fire.py`, `filter_candidates.py`, and `compose_lifecycle_context.py`
  each gain a `--workflow-path` mode that runs the validator internally (by
  importing `validate_workflow`), so the model never materialises or couriers
  that artifact. Of the scratch files a live fire writes,
  `validator-output.json` was the only pure *script→script* handoff — the
  others (`comments` / `candidates` / `in-flight` / `issue`) are MCP-sourced
  and irreducible.
- `validate_workflow.py` exposes three reusable entrypoints —
  `validate(workflow_path) -> (result, evidence)`,
  `print_failures(evidence)`, and
  `load_config(workflow_config=None, workflow_path=None)` — and `main()`
  delegates to them (stdout / stderr / exit-code behaviour unchanged). The
  three consumers resolve their config through `load_config`, which bails
  exactly as the bootstrap does (rule failures to stderr, exit 2) on an
  invalid workflow. `--workflow-config <path>` is retained for the dry-run /
  test fixtures and wins when both flags are passed.
- Re-running the cheap validator per consumer (~3–4× per fire: one YAML read +
  pure rule checks) is *more* correct than reusing a model-held copy — it
  eliminates the cacheable-staleness risk that is the same reason `tick.md`
  already forbids the model reading `workflow.yaml` directly.
- `commands/tick.md`: the five consumer invocations (step 0 dry-run compose,
  step 5 `--plan` + filter, the Route step's `route_fire`, step 13 compose)
  now pass `--workflow-path`; step 1 still runs the validator once and keeps
  the parsed JSON in memory as the config source for its own lookups. The
  `validatorOutputPath` variable and the `.cadence/validator-output.json`
  scratch file are fully removed.

### Added — Determinism Phase 8: extract tick.md routing into route_fire.py
- New `templates/hooks/route_fire.py` collapses `commands/tick.md` steps 8–11
  — the matched-state lookup + unmapped-column release (8), drift
  classification (9), gate verdict routing (10), and attempt-cap check (11) —
  from model-executed branching prose into one pure orchestrator. It answers
  *"given where this issue sits and its history, what should this fire do to
  it?"* as a pure function of `(config, current Linear column, present labels,
  comment history)` and emits a single routing **plan** (pre-actions, target
  state + attempt, or an exit plan with summary). The bootstrap remains the
  sole Linear writer — `route_fire.py` makes no MCP/network/shell call; it
  only decides, and `tick.md` executes every post/move/label-op.
- New `templates/hooks/classify_drift.py` and `classify_gate.py` — the two
  pure sub-decisions (`route_fire.py` wires them). `classify_drift` reproduces
  the ordered step-9 branch (null / Match / forward-progression-via-`next` /
  drift-otherwise); `classify_gate` reproduces the step-10 verdict routing
  (waiting / approve / rework, both-labels→rework, `max_rework` escalation).
- `parse_comments.py` gains an importable `parse_comment_list()` (and
  `coerce_comment_list()`); `route_fire.py` calls it directly so the parse
  runs **exactly once** per fire — the old step-11 second `parse_comments.py`
  invocation (re-counting attempts against the gate-resolved target) is gone.
  The router counts attempts against the *resolved* target internally. CLI
  output is byte-identical (existing fixtures unchanged).
- `route_fire.py` imports `emit_tracking_comment`'s `build_gate` /
  `build_reconcile` formatters, so the plan carries finished reconcile / gate
  rework / gate escalation comment bodies — no tracking-comment templates are
  re-inlined in the router.
- New `tests/test_route_fire.py` (decision-parity matrix for every step 8–11
  branch, plus the double-run-subsumption and legacy-`stokowski:` cases),
  `tests/test_classify_drift.py`, and `tests/test_classify_gate.py`.
- `commands/tick.md` steps 8–11 collapse to **Gather → Route → Execute**: the
  bootstrap re-reads the column / labels / comments, runs one `route_fire.py`
  Bash call, and applies the returned plan. The early-exit paths (unmapped /
  waiting / approve-terminal / escalate / cap-hit) are driven by the router's
  `exit_plan` + `exit_summary`; the bootstrap re-derives no verdict.
- `SCAFFOLD_PLAN` gains the three plugin-owned rows; `CLAUDE.md` repo-map
  lists the new helpers.
- The dispatch prose's transient JSON (comment lists, candidate / in-flight
  lists, the composed issue object) now lands under `.cadence/` with stable
  names instead of an unspecified "temporary file" the model was placing at
  the repo root.
  `validate_workflow.py` — the first Bash call in every `/cadence:*` fire,
  dry-run included — now creates `.cadence/` and a self-ignoring
  `.cadence/.gitignore` (`*`) via a new `_common.ensure_cadence_dir()`, so
  scratch never shows up in the consumer's `git status` (the audit hook only
  created that `.gitignore` on a Linear write, which a dry-run never makes).

### Added — init scaffold driver + Linear-config orchestrator
- New `scripts/scaffold_files.py` collapses `commands/init.md` Steps 2 + 3 + 4
  (overwrite check, directory creation, the 20-row source→destination copy
  plan and its per-file overwrite policy) into one subprocess. The prose
  used to tell the model to "Read each source, then Write the destination,"
  which on a `--force` re-init invited it to read the destination, diff it
  against the source, and *patch* — silently leaving stale plugin code on
  disk. The driver does `Path.read_bytes()` → `Path.write_bytes()`
  unconditionally (modulo policy), so plugin-owned files are byte-identical
  to the installed plugin on every run. The canonical copy plan lives in one
  module-level constant, `SCAFFOLD_PLAN`: `plugin-owned` rows (hooks,
  `/cadence:*` commands) always overwrite; `user-config` rows (`workflow.yaml`,
  prompts, agents) overwrite only with `--force`. Exit 2 = already
  initialized (verbatim abort message on stdout); exit 1 = read/write error.
- New `scripts/configure_linear.py` folds Steps 4c + 5 into one orchestrator.
  The detect-namespace / branch-on-exit-code / capture-`permissionsBlock` /
  thread-it-into-the-render plumbing that used to live as model-driven prose
  is now a single process: it reads `claude mcp list` on stdin (same shell
  pipe as before — no new CLI nesting), detects the Linear MCP namespace
  (with `.mcp.json` fallback), merges the allowlist into
  `.claude/settings.local.json` (placeholder path on detection failure), and
  renders the "Next steps" block on stdout. It reuses
  `detect_linear_mcp_namespace`, `merge_settings_permissions`, and
  `render_next_steps` by import — no detection/merge/render logic duplicated.
- New `tests/test_scaffold_files.py` (16 cases) covers the clean-tree happy
  path (every destination byte-identical, five dirs, success summary fixture),
  `--force` idempotence, the abort path (workflow.yaml present → exit 2,
  writes nothing), the defensive user-config skip, plugin-owned always-
  overwrite, the **re-init byte-for-byte regression guard** (every destination
  pre-scrambled, `--force`, then sha256-compared to source), error paths
  (missing plugin root, missing source, missing arg), and plan-integrity
  assertions (policy tagging, destination uniqueness, parity with
  `render_next_steps`'s file list).
- New `tests/test_configure_linear.py` (8 cases) covers the four branches the
  4c prose enumerated — detected-via-stdin, detected-via-`.mcp.json` fallback,
  multiple-servers stderr note, and detection-failed placeholder — each
  asserting the rendered "Next steps" stdout against a byte-identical fixture
  (reusing `next_steps_success.md` / `next_steps_failure.md`), plus a
  malformed-`settings.local.json` graceful-degradation case.
- New fixtures `tests/fixtures/init/scaffold_abort.txt`,
  `scaffold_success_no_skips.txt`, `scaffold_success_with_skip.txt`.

### Changed — init.md shrinks; render_next_steps sources its file list
- `commands/init.md` collapses from seven steps (1 → 2 → 3 → 4 → 4b → 4c → 5)
  to four cleanly-numbered ones: 1 (confirm cwd) → 2 (scaffold driver) → 3
  (hooks merge) → 4 (Linear-config pipe). The old 4b/4c sub-letters are gone
  — they only made sense as branches of a Step 4 that no longer exists. The
  ~25-line verbatim abort block, the 20-row copy table, the per-file policy
  paragraph, the detection exit-code branching, the "Detection failed"
  subsection, the `--print-only` second invocation, and the
  `permissionsBlock` / `detectionNote` variable-threading no longer appear in
  the dispatch. The hooks merge (now Step 3) keeps its stop-on-failure
  contract.
- `scripts/render_next_steps.py` now derives `_FILES_WRITTEN` from
  `scaffold_files.SCAFFOLD_PLAN`'s destination column instead of declaring a
  parallel literal tuple — killing one of the four hand-synced copies of the
  file list. Output is byte-identical (existing fixtures unchanged).
- `scripts/README.md` table grows from four rows to six (`scaffold_files.py`,
  `configure_linear.py` documented); `CLAUDE.md`'s repo-map drops the
  hard-coded `templates/hooks/` count in favour of a pointer to
  `SCAFFOLD_PLAN`, closing the last of the four duplicate file lists.

### Fixed — detect the claude.ai Linear connector's display-name format
- `scripts/detect_linear_mcp_namespace.py` now recognises the claude.ai
  workspace connector in `claude mcp list` output. That connector lists by
  display name (`claude.ai Linear: https://mcp.linear.app/mcp - ✓ Connected`)
  rather than a bare namespace token, so the line-anchored `_NAME_RE` matched
  `claude`, hit the `.` before reaching `linear`, and fell through to the
  "detection failed" placeholder — even though the connector's tools are
  plainly namespaced `mcp__claude_ai_Linear__*`. A new per-line matcher tries
  the bare token first (unchanged for `linear` / `linear-server` /
  `claude_ai_Linear` keys), then maps the `claude.ai <Name>:` display form to
  its underscore namespace (`claude.ai Linear` → `claude_ai_Linear`). Four new
  cases in `tests/test_detect_linear_mcp_namespace.py` cover the display-name
  line alone, among other servers, and the non-Linear claude.ai connector that
  must *not* match. Surfaced during this PR's manual-test setup (the operator
  here runs the claude.ai connector); the matching `BACKLOG.md` item is
  removed in the same commit.

### Added — Determinism Phase 7: init-time scripts for MCP detection + next-steps render
- New `scripts/detect_linear_mcp_namespace.py` owns the Linear MCP
  namespace detection that used to live as parsing prose + a regex
  literal in `commands/init.md` Step 4c. CLI: `--mcp-list-stdin`
  (reads `claude mcp list` stdout from stdin), `--mcp-json-path PATH`
  (fallback against `.mcp.json`'s top-level `mcpServers` keys), or
  both together (stdin first, JSON fallback when stdin yields no hit).
  The script encapsulates: leading-bullet/whitespace stripping
  (`*`, `-`, spaces, tabs) per Claude Code CLI version, the case-
  insensitive substring match against the namespace charset
  (`[A-Za-z0-9_-]*[Ll]inear[A-Za-z0-9_-]*`), the first-match-wins rule
  with extras reported on stderr ("multiple Linear MCP servers found"),
  and the empty-stdout-with-exit-2 contract that init.md treats as
  detection failed. Stdlib only (no PyYAML); lives in `scripts/` per
  the init-time-helper convention.
- New `scripts/render_next_steps.py` owns the ~70-line "Cadence
  initialised." operator handoff block that used to live as verbatim
  prose in `commands/init.md` Step 5. CLI: `--settings-local-written
  {true|false}` (controls whether the `.claude/settings.local.json`
  line appears under "Files written"), `--permissions-detection-note
  '<text>'` (verbatim single-line note printed under the Permissions
  section), `--permissions-block '<text>'` (raw
  `merge_settings_permissions.py --print-only` stdout — the script
  indents each line two spaces). The renderer owns the full file list,
  the gate-label hint, the Permissions / `/schedule` reminder, the
  Next-steps checklist, and the create-ticket pointer; the dispatch
  supplies only the three runtime values above. Forces UTF-8 on stdout
  so the em dashes (`—`) and bullets (`•`) survive Windows shells.
- New `tests/test_detect_linear_mcp_namespace.py` (16 cases) covers
  the three known namespaces (`linear`, `linear-server`,
  `claude_ai_Linear`), bullet/dash CLI prefixes, multi-match warnings
  on stderr, the empty-input → exit 2 contract, the stdin-empty →
  `.mcp.json` fallback, the short-circuit when stdin already yields a
  hit, unparseable / missing JSON paths, and the no-flags error path.
- New `tests/test_render_next_steps.py` (11 cases) covers the
  settings.local.json toggle in both directions, case-insensitive
  bool parsing, the bad-bool exit-1 path, multi-line permissions
  block indentation, detection-note verbatim placement, and the
  argparse missing-arg path. Two byte-identical fixture comparisons
  (the broad-stroke acceptance criteria): `tests/fixtures/init/
  next_steps_success.md` (detection succeeded, settings.local.json
  written) and `tests/fixtures/init/next_steps_failure.md` (detection
  failed, settings.local.json omitted, placeholder namespace
  substituted into both the note and the verb list) — these guard
  against any future edit that changes the operator handoff shape.

### Changed — Determinism Phase 7: init.md / scripts/README.md
- `commands/init.md` Step 4c replaces the three-step detection prose
  (run `claude mcp list`; parse output via the literal regex
  `^\s*([A-Za-z0-9_-]*[Ll]inear[A-Za-z0-9_-]*)\s`; fall back to
  `.mcp.json`'s `mcpServers` keys) with two Bash invocations of
  `detect_linear_mcp_namespace.py`. The exit-2 contract lives in the
  script; the prose only routes between stdin and `--mcp-json-path`.
- `commands/init.md` Step 5 replaces the ~70-line verbatim "Cadence
  initialised." block plus the three interpolation-point definitions
  with a single Bash invocation of `render_next_steps.py`. The
  prose's job collapses to "print the script's stdout verbatim."
- `scripts/README.md` table grows from two rows to four — the two
  new init-time helpers documented alongside `merge_settings_hooks.py`
  and `merge_settings_permissions.py`. The "two helpers" intro
  updates to "four helpers."

### Removed — Determinism plan retired
- `DETERMINISM-PLAN.md` deleted. Every phase (P1 test scaffolding;
  P2 `linear_to_workflow` reverse map; P3 `compose_lifecycle_context.py`;
  P4 `filter_candidates.py`; P5 `render_status_report.py`; P6 sweep
  classification + reporting + `emit_tracking_comment.py --kind sweep`;
  P7 init-time scripts) shipped. The matching `BACKLOG.md` entry
  ("Move more slash-command logic into deterministic scripts") is
  removed in the same PR.

### Added — Determinism Phase 6: extract sweep classification + reporting
- `templates/hooks/emit_tracking_comment.py` gains `--kind sweep`. New
  required args: `--cleared-at`, `--last-activity`, `--stale-minutes`,
  `--threshold-minutes`. Emits the existing `<!-- cadence:sweep
  {"cleared_at":...,"last_activity":...,"stale_minutes":...} -->` JSON
  marker plus the visible `**[Cadence]** Stale lock cleared (last
  activity ..., N minutes ago, threshold M minutes).` line — same
  shape `commands/sweep.md` used to hand-author inline. Every Cadence
  comment now flows through one emitter; the sweep-comment body can no
  longer drift from the JSON the audit hook expects.
- New `templates/hooks/render_sweep_report.py` owns the entire
  `/cadence:sweep` summary that used to live as time math + Markdown
  templating in `commands/sweep.md` steps 2 + 4 + 6. CLI: `--input
  <path>`. Input JSON shape (documented in the script's docstring): the
  current `now` ISO timestamp, the configured `threshold_minutes`, and
  a `locked_issues` list (each with `identifier`, `title`, `updated_at`,
  `state_name`). The script encapsulates: cutoff computation (`now -
  threshold_minutes`), per-issue `stale_minutes = floor((now -
  updated_at) / 60)` with a clamp to zero for future-dated updates
  (MCP/bootstrap clock skew), the stale-vs-fresh classification at
  `updated_at <= cutoff`, ascending-`updated_at` ordering of both
  result lists, title truncation at 60 chars with a trailing `…`,
  newline collapsing and pipe escaping for table cells, and the
  `(none cleared)` / `(none)` empty-table substitutions.
- The script uses a **dual-stream contract**: stdout is the full Markdown
  report (`## Cadence sweep — <now>` header, both `### Cleared` and
  `### Still locked` sections); stderr is a JSON object `{"cutoff":
  "...", "stale": [...], "fresh": [...]}` carrying the per-issue
  `stale_minutes` the prose needs for the `emit_tracking_comment.py
  --kind sweep` invocation in step 5. The prose prints stdout verbatim
  in step 6 and iterates the stderr `stale` list in step 5; the
  pre-rendered report assumes every stale issue is swept, with per-issue
  failures appended as `Failed to sweep <ID>: <error>` lines below
  the report.
- `tests/test_emit_tracking_comment.py` extends to cover `--kind sweep`
  (7 new cases): the full happy path, every required-arg-missing exit-1
  path, argparse rejection of non-integer `--stale-minutes` and
  `--threshold-minutes`, the zero-stale-minutes edge case, and an
  added `sweep` entry in the JSON-validity round-trip matrix.
- New `tests/test_render_sweep_report.py` (27 cases) covers the
  classification (empty / all-stale / all-fresh / boundary at the
  cutoff / `stale_minutes` floor / future-update clamp), sort order
  (ascending `updated_at` on both lists), title truncation + newline
  + pipe handling, threshold-0 and threshold-99999 edge cases (AC-3),
  fractional-seconds + non-UTC-offset timestamp parsing (Linear
  commonly emits the former), header cutoff display + classification
  cutoff field, and the CLI error paths. One byte-identical fixture
  (`tests/fixtures/sweep/mixed.md`, 2 stale + 1 fresh) guards AC-2 —
  the broad-stroke acceptance criterion against future renderer drift.

### Changed — Determinism Phase 6: sweep.md / init.md / CLAUDE.md
- `commands/sweep.md` step 2 keeps only the `now` resolution; the
  cutoff math moves into the renderer. Step 3 keeps the MCP query but
  hands off to step 4 by writing `now` + `threshold_minutes` +
  `locked_issues` to a temp JSON instead of doing the sort inline.
  Step 4 replaces the inline "compare each `updatedAt` to `cutoff`"
  block with `render_sweep_report.py --input "$sweepInputPath"` — the
  prose reads the stderr classification for step 5 and holds stdout
  for step 6. Step 5's hand-authored `<!-- cadence:sweep ... -->`
  body (HTML-comment marker + visible line) collapses to an
  `emit_tracking_comment.py --kind sweep ...` invocation per stale
  issue. Step 6 prints the pre-rendered `sweepReport` verbatim and
  appends per-issue failure lines from step 5. The "Side-effect
  ordering" constraint block documents the new "pre-rendered report +
  failure-line append" contract.
- `commands/init.md` Step 4 copy table adds the new script;
  Step 2's overwrite-check block and Step 5's "Files written" block
  list `.claude/hooks/render_sweep_report.py`; the `ten files` /
  `seven .py files` references update to `eleven` / `eight`.
- `CLAUDE.md`'s `templates/hooks/` repo-map entry updates the same
  counts and adds `render_sweep_report.py` to the dispatch-prose
  helper list.

### Added — Determinism Phase 5: extract `render_status_report.py`
- New `templates/hooks/render_status_report.py` owns the entire
  `/cadence:status` Markdown render — header, issues table, per-state
  summary, optional Concurrency table, optional Config warnings, and
  the read-only footer — that used to live as ~120 lines of prose in
  `commands/status.md` step 5. CLI: `--input <path>`. Input JSON shape
  (documented in the script's docstring): the verbatim validator output
  (must include `states`, `linear_to_workflow`, `linear`, `label`; may
  include `evidence` when the validator exited 2), a per-issue list
  (`identifier`, `title`, `state_name` = Linear column, `priority`,
  `updatedAt`, `labels`, `attempt_count`, optional `last_state`), the
  current `now` ISO timestamp, `team` / `project_slug` / `pickup_state`
  for the header, and an optional `degraded_issues` list for the
  Config-warnings section.
- The script encapsulates: the workflow-state column formatting
  (`(pickup)` / `<state>` / `<gate> (waiting)`) via the validator's
  `linear_to_workflow` reverse map; the verdict cell logic (approve /
  rework / both-as-rework / empty on non-gates); the priority + updatedAt
  sort (null and `0`/"No priority" sort last; `updatedAt` descending
  within a priority tier); title truncation at ~50 chars with a trailing
  `…`; newline collapsing and pipe-escaping for table cells; the per-state
  summary's gate four-bucket breakdown (awaiting / 👍 approve / 👎 rework
  / ⚠️ both labels) with the "collapse to single line when all awaiting"
  rule and the 0-count omissions; the agent / terminal `🔒 N locked` /
  `🛑 N needs-human` suffix counts; the always-emitted `(pickup)` and
  terminal-with-zero-issues lines; the Concurrency table (AT CAP / OVER
  CAP / `n/a` for terminals / `(none)` for uncapped agents and gates),
  omitted entirely when no state declares `max_in_flight`; and the
  Config-warnings section that surfaces the validator's `evidence` FAIL
  blocks (rule title + failure string) plus any degraded per-issue
  fetches.
- New `tests/test_render_status_report.py` (32 cases) covers all of the
  above. Three byte-identical fixture comparisons (the broad-stroke
  acceptance criteria): `tests/fixtures/status/default_full.md` (one
  issue per workflow state, mixed verdict labels on the two gates),
  `tests/fixtures/status/empty.md` (empty issue set → sentinel line, no
  Concurrency section), and `tests/fixtures/status/concurrency.md`
  (one AT CAP state + one OVER CAP state). The remaining cases target
  the header, the issue-table column logic (pickup / state / gate_waiting
  cells, verdicts, title truncation, newline + pipe escaping, attempt-0
  `—` and degraded `?` rendering), the priority + updatedAt sort, the
  per-state summary (terminal-zero, lock + needs-human suffixes, gate
  collapse-when-all-awaiting, gate-omit-zero-buckets, four-bucket render,
  pickup line), the Concurrency table (omit / append / AT CAP / OVER
  CAP / terminal-`n/a` / gate + terminal markers), Config warnings
  (validator FAIL surface, degraded-fetch surface, clean run omits
  section), the footer, and the CLI error paths.

### Changed — Determinism Phase 5: status.md / init.md
- `commands/status.md` step 5 collapses from the ~120-line render block
  (Markdown table headers, workflow-state column formatting table,
  verdict-column rules, row ordering rule, per-state summary template,
  gate four-bucket breakdown template, Concurrency table template + cell
  rules, Config-warnings rules, and footer line) to a thin invocation:
  compose the input JSON from the data step 1-4 already gathered, call
  `render_status_report.py --input <path>`, and print the script's
  stdout verbatim.
- `commands/init.md` Step 4 copy table adds the new script; Step 2's
  overwrite-check block and Step 5's "Files written" block list
  `.claude/hooks/render_status_report.py`; the `nine files` /
  `six .py files` references update to `ten` / `seven`.
- `CLAUDE.md`'s `templates/hooks/` repo-map entry updates the same
  counts and adds `render_status_report.py` to the dispatch-prose
  helper list.

### Added — Determinism Phase 4: extract `filter_candidates.py`
- New `templates/hooks/filter_candidates.py` owns the pickup-side query
  plan, the candidate filter, the priority + `createdAt` sort, and the
  bounded reachability walk that used to live inline in
  `commands/tick.md` step 5. Two modes:
  - `--plan --workflow-config <path>` emits the parameters for the MCP
    pickup query (`team`, `project_slug` — JSON `null` when absent, not
    `""` — and `workflow_linear_states`) plus one `{state_name, linear_state}`
    entry per state declaring `max_in_flight` for the dispatch prose to
    query as in-flight counts.
  - `--workflow-config <path> --candidates <path> --in-flight <path>`
    reads the prose-returned pickup results + per-state counts and
    emits `{ordered_identifiers, over_cap_states_that_blocked, diagnostic_message}`.
    `diagnostic_message` is non-null only when `ordered_identifiers` is
    empty; the canonical `No eligible issues.` / `No eligible issues.\n(caps reached for: ...)`
    rendering moves from prose to the script.
- The script encapsulates: workflow-Linear-states membership filter;
  a new drop for issues sitting in a `type: terminal` state's Linear
  column (the workflow is complete for them — step 14 would have no
  subagent to invoke; the pre-refactor prose let this through because
  the bug only bites when an issue genuinely sits in Done while the
  pickup pipeline is otherwise capped); the `cadence_active` /
  `cadence_needs_human` label drops; the blocker-resolved filter
  (skipped when the `blockers` field is absent, per the
  MCP-data-availability fallback in step 5); the
  gate-waiting-without-verdict filter; the priority sort (null and
  `0`/"No priority" sort last); per-candidate effective-target
  resolution (pickup → entry, gate + approve / rework → `on_approve` /
  `on_rework`, otherwise the matched workflow state); the bounded walk
  (include target and every subsequent agent state, plus the first
  gate or terminal); and the P8.2 drain exemption (the candidate's own
  gate's cap is excluded from the over-cap check for verdict-bearing
  gate issues).
- New `tests/test_filter_candidates.py` (39 cases) covers both modes:
  plan-mode in-flight surfacing (no caps / agent + gate caps / only the
  AC-1 set), `project_slug` JSON-null behaviour for absent / empty /
  present configs; filter-mode pre-filters (`cadence_active`,
  `cadence_needs_human`, foreign columns, blockers absent vs present
  vs resolved, gate-waiting without verdict, gate-waiting with each
  verdict); priority sort (high-first, null and `0` sort last,
  `createdAt` tie-break, 10-run stable-sort sanity check on total
  ties); the AC walks (Todo blocked by `plan_review` cap; drain
  exemption at own gate; rework walk blocked by downstream
  `human_review` cap; walk bounded so downstream caps past the
  boundary do not bind; cap on a state that is not on any walk is not
  reported); both-verdict-labels routed as rework; terminal target
  (no caps bind); multiple blocking states reported together; the
  GraphQL `{"nodes": [...]}` label shape; and the CLI error paths
  (missing args, unreadable config, non-array candidates, non-object
  in-flight).

### Changed — Determinism Phase 4: tick.md / init.md
- `commands/tick.md` step 5 collapses from the ~125-line filter / sort /
  cap-walk block to a five-step shell: invoke `--plan`, run the MCP
  pickup query, run one MCP count per `in_flight_queries` entry, hand
  the results back via `--candidates` / `--in-flight`, then act on
  `diagnostic_message` / `ordered_identifiers`. The "reachability walk"
  paragraph, the "over-cap" loop, the bulleted "Examples for the
  default workflow" block, and the per-state `max_in_flight` scan all
  move to the script. The MCP-query guardrails (verbatim team /
  project_slug, no fallback on empty results, no broader retry) stay
  in prose — they constrain the LLM's MCP-calling behaviour, not the
  script's pure data work.
- `commands/tick.md` step 4 no longer asks the agent to track a
  `workflowLinearStates` variable; the script consumes
  `workflow_linear_states` directly via plan mode. `linearToWorkflow`
  is still kept in memory for step 8.
- `commands/init.md` Step 4 copy table adds the new script; Step 5
  "Files written" block lists `.claude/hooks/filter_candidates.py`;
  the overwrite-check block names the new file; the `eight files` /
  `five .py files` references update to `nine` / `six` to account for
  it.

### Added — Determinism Phase 3: extract `compose_lifecycle_context.py`
- New `templates/hooks/compose_lifecycle_context.py` renders the full
  subagent user prompt — Lifecycle Context block (default or
  adversarial-context variant), optional Rework Context section, and the
  appended `.claude/prompts/global.md` content — in deterministic Python
  instead of the ~140 lines of prose that previously lived in
  `commands/tick.md` step 13. CLI: `--workflow-config`, `--issue`,
  `--target-state`, `--attempt`, `--parse-comments-output`, plus
  `--rework`, `--global-prompt-path`, `--default-branch`, `--dry-run`.
  Stdout is the prompt; pass it to the Agent tool verbatim.
- The script absorbs the full subagent-prompt construction: input
  shaping (validator output, issue object, parse-comments output), the
  `linear.team` lookup for branch derivation, the title-slug derivation
  (lowercased, non-alphanumerics collapsed to hyphens, trimmed to 50
  chars), the default-vs-adversarial branch (the latter strips the
  implementer narrative and adds Branch (under review) / Base branch /
  optional PR lines), the rework-section rendering (one block-quoted
  entry per human comment, with a zero-comments fallback), the
  `.claude/prompts/global.md` read, and the two-blank-line separator
  between the block and the global prompt.
- New `tests/test_compose_lifecycle_context.py` (25 cases) covers every
  branch: byte-identical golden fixtures for default / adversarial /
  rework / dry-run; next-state-is-gate / terminal / agent transitions;
  branch derivation with and without `issue.branchName` (and the
  50-char slug truncation); priority and labels rendering (numeric vs
  null, comma-separated vs `(none)`); globalPrompt append (present vs
  missing); the `--rework` flag and the zero-comments fallback; and the
  required-arg / malformed-JSON / missing-entry-state error paths.
  Goldens live under `tests/fixtures/lifecycle_context/`.

### Changed — Determinism Phase 3: tick.md / init.md
- `commands/tick.md` step 13 collapses from ~140 lines of prose to a
  single Bash invocation of `compose_lifecycle_context.py`. The
  AUTO-GENERATED marker, the `## Lifecycle Context` heading, the
  `### Transitions` section, the rework template, the
  adversarial-context branch, the `<!-- END CADENCE LIFECYCLE -->`
  footer, the EXAMPLE-1 dry-run placeholder list, and the
  two-blank-lines + globalPrompt append are all owned by the script.
- `commands/tick.md` step 2 (Read global prompt) is removed; the script
  reads `.claude/prompts/global.md` itself. The step number is preserved
  as a removed-stub so external references to later steps don't shift,
  matching the P2 step-3 retirement.
- `commands/tick.md` step 0 (dry-run) invokes
  `compose_lifecycle_context.py --dry-run` to render the **Lifecycle
  Context (composed):** section. The hardcoded `EXAMPLE-1` /
  `Hypothetical entry-state issue` placeholder list moves into the
  script.
- `commands/tick.md` step 1 now also writes the validator JSON to a
  temporary file (`validatorOutputPath`) for step 0 / step 13.
- `commands/tick.md` step 9 now also writes the parse-comments output to
  a temporary file (`parseCommentsOutputPath`) for step 13. The file
  doesn't need refreshing in step 11 — `compose_lifecycle_context.py`
  only reads `rework_context` and `latest_implementer_summary.pr_url`,
  both of which are independent of `--target-state` and `--gate-name`.
- `commands/init.md` Step 4 copy table adds the new script; Step 5
  "Files written" block lists `.claude/hooks/compose_lifecycle_context.py`;
  the overwrite-check block names the new file; the `seven files` /
  `four .py files` references update to `eight` / `five` to account for
  it. `CLAUDE.md` repo map updates the same counts.

### Changed — Determinism Phase 2: validator emits `linear_to_workflow` reverse map and owns the YAML read
- `templates/hooks/validate_workflow.py` now includes a `linear_to_workflow`
  field in its JSON output: each Linear column name keyed to
  `{ "kind": "pickup" | "state" | "gate_waiting", "workflow_state": "<name>" | null, "linear_state_type": "agent" | "gate" | "terminal" | null }`.
- The validator also passes through the raw top-level `linear`, `label`,
  and `limits` blocks. The dispatch prose now reads team / project /
  label / limits values from the validator's JSON instead of doing its
  own Read of `.claude/workflow.yaml` — one read per fire, one
  cacheable artifact eliminated. Without this, the LLM would cache the
  earlier YAML Read across fires in the same conversation and miss
  edits made between fires.
- `commands/tick.md` step 1 (formerly "Read config" + step 3 "Validate
  config") now combines the two into a single validator invocation;
  step 3 becomes a removed-stub for step-number stability. Step 8
  (matched workflow state) replaces the inline "find the single workflow
  state whose `linear_state` equals it" derivation with a direct lookup
  against `linear_to_workflow`. Step 4 keeps `linearToWorkflow` from
  the validator output alongside the existing `workflowLinearStates`.
  The dry-run report (step 0) also renders the new map as bullets
  between "Workflow Linear states queried" and "Entry state".
- `commands/status.md` step 1 drops its standalone YAML Read; the
  validator invocation now reads + validates in one call. Step 2 drops
  its manual `linearToWorkflow` construction (and the conflict-detection
  prose Rule 1 already covers) in favour of the validator's map.
- `commands/sweep.md` step 1 likewise consolidates the read and the
  advisory validation into a single script invocation, with the same
  "do not read workflow.yaml directly" guardrail.
- `tests/test_validate_workflow.py` adds four cases pinning the
  `linear_to_workflow` map's shape for agent / gate-waiting / pickup /
  terminal columns, plus a custom pickup name and a sanity check that
  the map's keys equal `workflow_linear_states`. Five further cases
  cover the raw `linear` / `label` / `limits` pass-through, including
  absent-block defaults and non-mapping coercion.
- **Motivation**: two dispatch commands re-derived the same Linear-column
  → workflow-state lookup every fire. The validator already had the
  data — emitting it once collapses both ad-hoc derivations. The raw
  config pass-through addresses a separate determinism gap (the LLM
  caching the YAML Read), and unblocks the status renderer extraction
  in Phase 5.

### Added — Determinism Phase 1: test scaffolding for the runtime helpers
- New `tests/` directory at the repo root with `unittest`-based coverage
  for the three runtime helpers under `templates/hooks/`:
  `test_validate_workflow.py` (rules 1-8 pass + fail, `--evidence`
  output shape, exit codes 0/1/2, `workflow_linear_states` order),
  `test_parse_comments.py` (`attempt_count`, `rework_count` scoped to
  `--gate-name`, `rework_context` excludes Cadence/Stokowski + bots
  oldest-first, `latest_implementer_summary` author-match constraint,
  legacy `stokowski:` `run`/`timestamp` normalisation, malformed
  tracking-JSON surfaces in `parse_errors`),
  `test_emit_tracking_comment.py` (every `--kind` x `--status`
  combination, missing-required-arg paths exit 1, 400-char error
  truncation, newline collapsing).
- New `python-tests` job in `.github/workflows/validate.yml` runs
  `python -m unittest discover -s tests -v` on push and PR, blocking
  merge on failure.
- `scripts/README.md` documents the discover command and the
  `tests/fixtures/` convention.
- **Motivation**: the runtime helpers shipped with zero automated
  coverage. The next determinism phases all modify or sit next to them;
  this lands the regression net before any logic moves.

### Changed — Repo reorg: `templates/` now mirrors the consumer's `.claude/`
- `templates/` is now a 1:1 mirror of the consumer's `.claude/` tree.
  `/cadence:init` (commands/init.md Step 4) copies every file under
  `templates/` to the same relative path under `.claude/`. The
  `.example` suffixes are gone:
  - `templates/workflow.example.yaml` → `templates/workflow.yaml`
  - `templates/global-prompt.example.md` → `templates/prompts/global.md`
  - `templates/settings.example.json` → `templates/settings.json`
- The four dispatch-prose helpers move from `scripts/` to
  `templates/hooks/` (alongside the three event-hook scripts that were
  already there): `validate_workflow.py`, `_common.py`,
  `parse_comments.py`, `emit_tracking_comment.py`. They were already
  copied into `.claude/hooks/` at init; the source location now
  reflects that. Sibling imports (`from _common import ...`) keep
  working in the new directory.
- `scripts/` now contains only the two plugin-only init-time merge
  helpers (`merge_settings_hooks.py`, `merge_settings_permissions.py`)
  that are never scaffolded to the consumer. `scripts/README.md`
  rewritten to reflect this narrower scope.
- `commands/init.md` Step 4 — copy table updated to the new source
  paths; the merge_settings_hooks invocation now points at
  `templates/settings.json`. Destination paths in `.claude/` are
  unchanged, so this is a no-op for already-initialised consumers.
  `commands/{tick,sweep,status}.md` already invoked their helpers via
  `${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/...` and need no path
  changes.
- `CLAUDE.md` repo map rewritten to describe the new layout; docstrings
  in `templates/hooks/_common.py`, `templates/hooks/validate_tracking_json.py`,
  and `templates/hooks/audit_linear_writes.py` updated to cite the new
  paths. README, MIGRATION, and BACKLOG references updated.
- **Motivation**: the previous layout split the seven Python files that
  ship to `.claude/hooks/` across two source directories
  (`templates/hooks/` and `scripts/`) with no behavioural distinction,
  and the `.example` suffixes plus path renames (e.g.
  `global-prompt.example.md` → `prompts/global.md`) meant
  `commands/init.md`'s copy table was the only place the mapping was
  legible. With the new layout the rule is "every file under
  `templates/` lands at the same path under `.claude/`."

### Changed — Phase 9: subagent scope discipline + bootstrap silence
- `templates/agents/implementer.md` — adds a `## Short-circuits` section
  with two rules. **Rule A** (no-op short-circuit): when the acceptance
  criteria are already satisfied by the repo (or explicitly say "no
  files added, updated, or deleted"), the implementer skips branch
  push and PR creation entirely and returns a summary with blank PR /
  branch fields. The default contract demands a PR URL on every run,
  which is incompatible with no-op tickets and pushed the model into
  manufacturing a PR to honour an impossible contract. **Rule B**
  (`gh`-absence bail): when `gh` is not on PATH, the implementer pushes
  the branch and returns a summary noting that PR creation was skipped
  — explicitly forbidding the network probing, SSH-key / gitconfig /
  env-var scanning, and proxy-endpoint reverse engineering observed
  when the model improvised its way around a missing tool. A concrete
  example summary is included so the model has a pattern to imitate.
- `templates/agents/implementer.md` and `templates/agents/reviewer.md`
  — add a `## Sandbox boundaries` section forbidding probing of local
  HTTP proxies, in-sandbox endpoints, SSH keys, gitconfig credentials,
  and other-process env vars. Reinforces Rule B at the subagent
  contract layer; the credentials needed for the assigned work are
  already in the agent's environment via the routine's configured
  connectors.
- `templates/agents/reviewer.md` — `gh`-absence fallback paragraph in
  step 2 of `## How to review`. If `gh` is not on PATH, the reviewer
  falls back to `git diff` against the configured base branch and
  notes that the PR view was not consulted; the same prohibition on
  improvising alternative metadata-fetch paths applies. Defence in
  depth, since P5's reviewer template also invokes `gh pr view`.
- `commands/tick.md` Step 15 — adds a **Bootstrap silence** subsection.
  Between step 14 (subagent invocation) and step 18 (exit), the
  bootstrap's only user-facing output is the verbatim
  `subagentSummary` and the Linear writes steps 16 and 17 require.
  Explicitly forbids annotating subagent behaviour, describing what
  the subagent did during its turn, and raising security or safety
  concerns about subagent activity in user-facing text — the
  bootstrap has no access to the subagent's tool trace, so any such
  narration is necessarily fabricated. The `verbatim` requirement on
  the Linear post itself is unchanged; this tightens the prose around
  it. Surfaced during P8 Smoke V, where the bootstrap invented a
  credential-exfiltration narrative about the implementer's behaviour
  with no underlying observability into the subagent's turn.

### Changed — Phase 8 amendment: bound the reachability walk at the next gate
- `commands/tick.md` Step 5 — the reachability walk now **stops at the
  first gate or terminal** reached (inclusive), instead of traversing
  every state to the workflow's terminal. Each gate is a parking spot
  for a distinct human's attention; conflating gates in the walk meant
  an at-cap downstream gate (e.g. `human_review`, one reviewer's queue)
  silently blocked Todo pickups that would have parked at an upstream
  gate (`plan_review`, a different reviewer's queue with capacity).
  Under the bounded walk, each gate-owning reviewer gets an independent
  cap; backpressure into a specific gate requires capping that gate
  directly. The drain exception for verdict-bearing gate candidates
  (their own gate is excluded from the check) is unchanged. Step 5 also
  documents the worked example walks for the default workflow so the
  shape of the binding is obvious without re-deriving it from the
  prose. No script or schema changes.
- `templates/workflow.example.yaml` — gate-cap and agent-cap comments
  rewritten to describe the bounded walk explicitly (which candidates'
  walks reach a given cap; Todo pickups not affected by downstream-gate
  caps).
- `README.md` — "Workflow tuning" section rewritten with a "Why the
  walk stops at the first gate" paragraph and a worked-example table
  showing which caps bind for each candidate-state shape in the default
  workflow.

### Changed — Phase 8: gate-aware concurrency caps
- `scripts/validate_workflow.py` Rule 6 — relaxed to allow `max_in_flight`
  on `type: gate` states in addition to `type: agent`. Terminals are
  still rejected (no pickup to throttle). Rule 6's `--evidence` block,
  stderr messages, and docstring updated to reflect the new scope.
- `templates/workflow.example.yaml` — both gate states (`plan_review`,
  `human_review`) gain a commented-out `max_in_flight` example with a
  full gate-cap explanation (queue cap, not parallel-run cap; verdict-
  bearing issues drain regardless). The agent-state cap comments
  (`plan`, `implement`, `agent_review`) are revised to clarify that
  they throttle parallel subagent runs only — they do **not** control
  downstream gate pile-up, for which the gate cap is the right lever.
  The top-of-file validation-rules comment names both state types.
- `commands/tick.md` Step 5 — replaces P6.3's per-candidate cap check
  with a **reachability walk**. For each candidate the bootstrap
  determines the effective target state (entry for pickup-state
  issues; `on_approve` / `on_rework` for verdict-bearing gate issues;
  otherwise the workflow state matching the candidate's column), then
  walks the happy-path downstream following `next` / `on_approve`
  until a terminal. If any visited state is over its cap, the
  candidate is dropped. The walk tracks visited states and breaks on
  re-visit (insurance against pathological happy-path cycles). The
  bootstrap exempts a verdict-bearing gate candidate from its own
  gate's cap so verdict drainage isn't blocked by an at-cap queue.
  Step 3's validation-rules prose updated to describe Rule 6's new
  scope.
- `commands/status.md` — Concurrency table now includes capped gates
  alongside capped agents; the `(none)` Cap value covers either state
  type, `n/a` is reserved for terminals. The `AT CAP` / `OVER CAP`
  descriptions reference the reachability walk and the verdict-drain
  exception so the operator can read the table without re-deriving
  the dispatch behaviour.
- `README.md` — "Workflow tuning" section expanded with an Agent-caps
  vs. Gate-caps subsection: agent caps throttle parallel subagent
  runs; gate caps throttle the waiting queue and bind on upstream
  candidates. Recommends the gate cap as the right lever for
  controlling reviewer load (an upstream agent cap stops binding the
  moment the agent's column drains, even if the gate is overflowing).
- `.claude-plugin/plugin.json` — version bumped to `0.6.0`.

### Changed — Phase 7: skip gate-waiting issues without verdicts at pickup
- `commands/tick.md` Step 5 — added a fifth eligibility filter that excludes
  issues sitting in a gate state's waiting column without a verdict label
  (`label.cadence_approve` / `label.cadence_rework`). Without this filter, a
  verdict-less gate issue that sorted to the top of the candidate list (high
  priority or oldest `createdAt`) would consume the entire fire: the bootstrap
  would acquire the soft lock, reach Step 10a, see no verdict, release the
  lock, and exit — while real candidates in `pickup_state` sat untouched.
  Step 10a's branch is preserved unchanged as defence in depth for the rare
  case where a human removes a verdict label between the pickup query and the
  gate check. No script or template changes are needed: the set of gate
  `linear_state` values is derived from the validator's `states` output,
  filtered client-side against labels already present in the pickup query.
  Cross-references Phase 8: today's verdict-less-gate-issue behaviour
  provides incidental backpressure into gates (the queue can't grow much
  because the dispatch keeps tripping on the first item in it); removing
  that backpressure is correct on its own merits but leaves nothing limiting
  gate pile-up until P8 lands gate-aware `max_in_flight` support.

### Added — Phase 6: per-state concurrency caps
- `templates/workflow.example.yaml` — agent states can now declare an
  optional `max_in_flight: N` (positive integer). When set,
  `/cadence:tick` skips candidates that would target a state already at
  its cap. Shipped as a commented-out example on the `implement` state.
  The typical reason to set a cap is bounded human-review bandwidth
  downstream (e.g. a single reviewer who can clear three
  implementations per day).
- `scripts/validate_workflow.py` Rule 6 — every `max_in_flight` value
  must be a positive integer (>= 1) and may only appear on `type: agent`
  states. Rejects `0`, negatives, floats, strings, null, booleans, and
  any occurrence on `type: gate` or `type: terminal` states.
  `--evidence` output includes a Rule 6 block.

### Changed — Phase 6: per-state concurrency caps
- `commands/tick.md` Step 5 — after sorting candidates, the bootstrap
  queries the live Linear column count for every state with a
  `max_in_flight` and drops any candidate whose target state is over its
  cap. The cap is **coordination, not a hard lock**: counts are derived
  from Linear on every fire (no sidecar state), and the soft-lock and
  drift-reconciliation flows are unchanged. Step 3's validation prose
  references Rule 6.
- `commands/status.md` — when any state declares `max_in_flight`, the
  report gains a Concurrency table showing each state's in-flight
  count, cap, and `AT CAP` / `OVER CAP` status. The table is omitted
  for workflows that declare no caps.
- `README.md` — new "Workflow tuning" section documenting
  `max_in_flight`, the bounded-human-review-bandwidth use case, and the
  rule that caps are forbidden on gates / terminals.

### Added — Phase 5: plan-review gate + adversarial review stage
- `templates/workflow.example.yaml` gains two workflow states. A
  `plan_review` **gate** sits between `plan` and `implement` so a human
  approves the planner's output before implementation burns budget
  (`type: gate`, `linear_state: "Plan Review"`, `on_approve: implement`,
  `on_rework: plan`, `max_rework: 2`). An `agent_review` **agent state**
  sits between `implement` and the human gate; it runs the `reviewer`
  subagent (`linear_state: "Reviewing"`, `next: human_review`).
- `templates/workflow.example.yaml` — new state field
  `adversarial_context: true` (set on `agent_review`). When present, the
  bootstrap composes a minimal Lifecycle Context for that state's
  subagent: ticket + acceptance criteria + branch/PR pointers only, with
  no implementer narrative carried forward.
- `scripts/validate_workflow.py` Rule 7 — every `adversarial_context`
  value must be a boolean and may only appear on `type: agent` states.
  `--evidence` output includes a Rule 7 block.
- `scripts/parse_comments.py` — emits a new `latest_implementer_summary`
  key (`pr_url` / `branch`), derived from the most recent implementer
  summary comment, so the adversarial Lifecycle Context can carry a PR
  pointer without lifting implementer prose.

### Changed — Phase 5: plan-review gate + adversarial review stage
- `templates/workflow.example.yaml` — `plan.next` changes from
  `implement` to `plan_review`; `implement.next` changes to
  `agent_review`; the former `review` gate is renamed `human_review`
  (its `linear_state`, `on_approve`, `on_rework`, `max_rework` are
  unchanged). The states comment block above `states:` documents all six
  states. No `commands/tick.md` or script changes are needed for the
  `plan_review` gate itself — Step 10's gate dispatch and the validator
  rules are gate-name-agnostic.
- `templates/agents/reviewer.md` — rewritten as an independent
  adversarial reviewer. The `## Your role` and `## How to review`
  sections now instruct the reviewer to read the diff cold via
  `git diff`, treat implementer comments as unreliable narrative, and
  tie every blocking finding to an AC violation, scope mismatch, or
  defect. `model` bumped `sonnet` → `opus`; `Bash` added to `tools` so
  the reviewer can run `git` / `gh`.
- `commands/tick.md` — Step 13 gains an adversarial-context variant
  clause: when the target state has `adversarial_context: true`, the
  Lifecycle Context omits any plan-summary / implementer narrative,
  splits the branch line into "Branch (under review)" + "Base branch",
  and adds a PR line from `parse_comments.py`. Step 3's validation-rules
  prose references Rule 7.
- `README.md` — the required-columns list adds `Plan Review` and
  `Reviewing` (seven columns for the default workflow).
- `.claude-plugin/plugin.json` — version bumped to `0.5.0`.

Upgrading from pre-P5: add the `Plan Review` and `Reviewing` Linear
columns to your board. In `.claude/workflow.yaml`, insert a
`plan_review:` gate block (`type: gate`,
`linear_state: "Plan Review"`, `on_approve: implement`,
`on_rework: plan`, `max_rework: 2`) and point `plan.next` at it; rename
`review:` to `human_review:`, change `implement.next` to `agent_review`,
and insert the new `agent_review:` block above `human_review:` with
`adversarial_context: true` (see `templates/workflow.example.yaml`).
Consumers who want to keep the old single-gate flow can skip the
`plan_review` / `agent_review` blocks entirely — the validator does not
require them.

### Added — Phase 4: label-based gate signalling
- `templates/workflow.example.yaml` `label:` section gains
  `cadence_approve` / `cadence_rework` entries — the two new
  config-defined verdict labels. A human signals their decision at a
  gate by adding one of these labels to an issue sitting in the gate's
  waiting column; the next `/cadence:tick` fire reads the label, routes
  the issue accordingly, and removes the label.
- `scripts/validate_workflow.py` Rule 8 — rejects any gate state still
  carrying the legacy `approved_linear_state` / `rework_linear_state`
  keys (removed in this phase) and points the operator at the
  CHANGELOG migration note.
- `scripts/merge_settings_permissions.py` — plugin-only helper invoked
  by `/cadence:init` to perform an idempotent merge of the canonical
  Cadence Linear MCP verb list into the consumer's
  `.claude/settings.local.json` `permissions.allow` array. Three known
  Linear MCP namespaces (`linear`, `linear-server`, `claude_ai_Linear`)
  are supported via a `--namespace` argument the caller derives from
  `claude mcp list` or `.mcp.json`. A `--print-only` mode emits the
  block without writing, for the "Next steps" output `/schedule`
  operators paste into the routine's permissions panel.

### Changed — Phase 4: label-based gate signalling
- `templates/workflow.example.yaml` — the `review` gate block drops
  `approved_linear_state` and `rework_linear_state`. A gate now
  declares a single Linear column (its waiting queue) plus
  `on_approve` / `on_rework` targets and optional `max_rework`. This
  collapses the per-gate Linear-column cost from three columns to one;
  a workflow with three gates drops from nine gate columns to three
  plus the two globally-shared verdict labels.
- `scripts/validate_workflow.py` Rule 1 — uniqueness now collects only
  `linear_state` values plus `linear.pickup_state`; the per-gate
  approved/rework fields are no longer part of the set.
  `_build_linear_states_set` and the `workflow_linear_states` JSON
  field are amended in lockstep.
- `commands/tick.md` — Vocabulary, Step 3 (validation prose), Step 4
  (`workflowLinearStates` construction), Step 8 (matched state lookup),
  and Step 9 (drift check Match rule) are updated for the single-column
  gate. Step 10 is rewritten: 10a (neither label), 10b
  (`cadence_approve` present → route to `on_approve`), 10c
  (`cadence_rework` present → route to `on_rework`), plus a defensive
  "both labels present" branch that falls back to rework. The bootstrap
  removes the verdict label after acting.
- `commands/status.md` — the reverse-lookup map drops `gate_approved`
  and `gate_rework`; the table gains a **Verdict** column showing which
  verdict label (if any) is queued on each gate-waiting row; the
  per-state summary's gate rendering replaces the three-line breakdown
  with one waiting-column line plus a verdict-label breakdown.
- `commands/init.md` — adds **step 4c** (detect Linear MCP namespace +
  invoke `merge_settings_permissions.py`); step 5's "Next steps"
  output adds a **Gate labels** block (reminding the operator to create
  the two labels and recommending the label group) and a
  **Permissions for /schedule routines** block (the verb list in
  copy-pasteable form for routines, which do not read
  `.claude/settings.local.json`).
- `README.md` — Consumer setup section's "Required Linear columns" list
  drops `Approved` / `Needs Rework`; the sample `/cadence:status` output
  reflects the single-column gate plus Verdict column; a callout under
  "Linear MCP tools" notes that `/cadence:init` automates the local
  allowlist write but `/schedule` routines still need the manual paste.
- `MIGRATION.md` — Stokowski schema example drops the two legacy gate
  keys; adds an **Upgrading to label-based gates** section walking
  through the in-place migration.
- `.claude-plugin/plugin.json` — version bumped to `0.4.0`.

### Removed — Phase 4: label-based gate signalling
- `approved_linear_state` and `rework_linear_state` from the gate
  schema. `validate_workflow.py` Rule 8 rejects configs still carrying
  them; tick / status / docs no longer reference them.

### Added
- `templates/ticket-template.md` — paste-able skeleton an operator drops
  into Linear's "Description" field for a new ticket. Scaffolded into the
  consumer's `.claude/ticket-template.md` by `/cadence:init` (hardening
  plan, Phase 3).
- `commands/create-ticket.md` — new `/cadence:create-ticket` slash
  command that walks the operator through filling the template
  interactively and emits a paste-ready Markdown block. No Linear writes;
  no subagent invocation. Validates that each acceptance-criterion is
  specific enough to verify from the diff and the test suite.
- `templates/agents/planner.md` — adds a "## Ticket-quality gate" section.
  The planner now refuses to plan a ticket whose description lacks a
  `## Acceptance Criteria` H2 with at least one `- [ ] **AC-N** —`
  checkbox item, returning a fixed "Cannot plan" summary that the
  bootstrap posts as a Linear comment and counts toward
  `max_attempts_per_issue` (escalating to `cadence-needs-human` after the
  configured number of fires).
- `templates/agents/implementer.md` — adds an "### Acceptance criteria"
  block to the required return-summary shape. The implementer must walk
  the ticket's `## Acceptance Criteria` list and either tick each item
  with a `Verified by:` artefact, or leave it unticked with a
  `Not addressed because:` reason.
- `templates/agents/reviewer.md` — adds step 0 to "How to review": locate
  the ticket's `## Acceptance Criteria` block and verify the
  implementer's per-AC claims against the actual diff; a `[x]` whose
  verification artefact does not exist is a blocking finding.
- `scripts/` — three deterministic Python helper scripts plus a shared
  `_common.py` module, invoked from command prose via `Bash`
  (hardening plan, Phase 1):
  - `validate_workflow.py` — enforces the five `.claude/workflow.yaml`
    config rules in code; `--evidence` emits per-rule evidence for the
    dry-run report.
  - `parse_comments.py` — deterministic counting and classification of a
    Linear issue's tracking comments (attempt count, rework count, rework
    context, latest tracking comment).
  - `emit_tracking_comment.py` — produces canonical tracking-comment
    bodies so the embedded JSON is always well-formed.
- `templates/hooks/` — three Claude Code hook scripts, scaffolded into the
  consumer's `.claude/hooks/` directory by `/cadence:init` (hardening plan,
  Phase 2):
  - `validate_tracking_json.py` — `PreToolUse` hook that blocks Linear
    comment-create calls whose Cadence tracking-comment JSON does not parse.
  - `validate_workflow_on_prompt.py` — `UserPromptSubmit` hook that runs
    `validate_workflow.py` before `/cadence:tick` proceeds, blocking the
    prompt on a broken `.claude/workflow.yaml`.
  - `audit_linear_writes.py` — `PostToolUse` hook that appends one JSONL
    line per Linear write to `.cadence/audit.log`, for forensic debugging
    of a fire after the fact.
- `templates/settings.example.json` — canonical Cadence hooks block,
  merged into the consumer's `.claude/settings.json` by `/cadence:init`.
  Matchers are namespace-flexible regexes that catch any Linear MCP tool
  whose server namespace contains `linear` / `Linear` (e.g.
  `mcp__linear__*`, `mcp__linear-server__*`, `mcp__claude_ai_Linear__*`),
  plus bare-named variants. Unrelated MCP servers (GitHub, Notion, etc.)
  are excluded.
- `scripts/merge_settings_hooks.py` — plugin-only helper invoked by
  `/cadence:init` to perform an idempotent merge of the hooks block into
  the consumer's `.claude/settings.json`. Non-Cadence hook entries are
  preserved; existing Cadence entries are replaced rather than duplicated.

### Changed
- `commands/tick.md` — steps 0, 3, 4, 9, 10c, 11, 12, 16 and the Failure
  path now delegate config validation, comment counting, and tracking-comment
  emission to the `scripts/` helpers instead of doing the bookkeeping in LLM
  prose.
- `commands/sweep.md` and `commands/status.md` — now call
  `validate_workflow.py` for config validation; `status.md` also uses
  `parse_comments.py` for per-issue attempt counts.
- `commands/init.md` — creates `.claude/hooks/`, copies the three hook
  scripts plus `validate_workflow.py` and `_common.py` alongside them, and
  merges Cadence's hook entries into `.claude/settings.json`.
- `commands/init.md` — also copies `parse_comments.py` and
  `emit_tracking_comment.py` into `.claude/hooks/`, and copies the
  dispatch prose `commands/{tick,sweep,status}.md` into
  `.claude/commands/cadence/`. `commands/{tick,sweep,status}.md` now
  reference helper scripts via `$CLAUDE_PROJECT_DIR/.claude/hooks/...`
  rather than `${CLAUDE_PLUGIN_ROOT}/scripts/...`. This makes
  `/cadence:tick` runnable from a `/schedule` cloud routine, which
  previously had no path to find either the dispatch prose or the
  helper scripts (the plugin is not installed in the cloud session).
- `commands/init.md` — also copies `templates/ticket-template.md` to
  `.claude/ticket-template.md`, lists it in the overwrite-check block
  and the "Files written" output, and appends `/cadence:create-ticket`
  guidance to "Next steps" (hardening plan, Phase 3).
- `commands/tick.md` step 9 (drift detection) — no longer treats normal
  agent→agent forward progression as drift. After a successful fire that
  advanced Linear from state X to state X.next (an agent state), the next
  fire would see `latest_tracking_comment.state = X` and matched state =
  X.next and incorrectly post a reconcile comment claiming a human
  reassignment. Step 9 now treats `matched == config.states[latest.state].next`
  as the expected pattern. Gate transitions were already handled
  correctly because step 16's gate-next branch emits a fresh tracking
  comment when advancing into a gate; only the agent→agent path lacked
  the corresponding update, which the smarter drift check now compensates
  for without adding extra comments.
- `commands/tick.md` step 5 — added explicit query-shape requirements:
  pass `linear.project_slug` to the MCP project filter verbatim, do not
  transform it, and do not fall back to broader queries (team-only, ID
  lookups) if the filtered query returns empty. Without this, the
  bootstrap LLM would sometimes improvise a fallback that masked a
  misconfigured `project_slug`, producing inconsistent fire-to-fire
  behavior.
- `templates/workflow.example.yaml` — `project_slug` comment corrected
  to specify the project's name or UUID, and warn against using the URL
  suffix (Linear URLs end in `<name>-<hash>`, but the hashed form is not
  accepted as an MCP filter value).
- `linear.project_slug` is now **optional**. Absent → `/cadence:tick`,
  `/cadence:sweep`, and `/cadence:status` query team-wide; present →
  the query narrows to that project (existing behaviour). Linear's data
  model treats team as primary and project as an optional facet, so
  forcing a project meant operators either created a project they did
  not otherwise want or silently got zero pickup hits for issues that
  were not assigned to the configured project. Touches
  `templates/workflow.example.yaml` (placeholder commented out, doc
  rewritten as optional), `commands/tick.md` step 5, `commands/sweep.md`
  steps 1 and 3, and `commands/status.md` steps 1, 3, and 5. The
  validator already did not enforce `project_slug`, so no script change
  was needed.
- `commands/{tick,sweep,status}.md` — every helper-script Bash
  invocation now uses `"${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/...`
  instead of `"$CLAUDE_PROJECT_DIR"/.claude/hooks/...`. The variable is
  reliably set in hook subprocess environments (which
  `templates/settings.example.json` still depends on) but not always in
  the Bash tool environment of a local Claude Code session — when
  unset, Git Bash on Windows expanded the leading `/` against its own
  install root (`C:\Program Files\Git\.claude\hooks\...`) and the
  script failed to open. The fallback resolves to cwd `.`, which the
  harness keeps at the project root.
- `.claude-plugin/plugin.json` — version bumped to `0.3.0`.
- `README.md` — "Linear MCP tools" section now opens with a namespace
  primer (`mcp__linear__*` vs. `mcp__linear-server__*` vs.
  `mcp__claude_ai_Linear__*` vs. bare), and the read/write allowlist
  tables include the `mcp__linear-server__*` variants. Notes that
  Claude Code's permission allowlist matches by exact tool name (unlike
  the shipped hook regex), so operators must substitute the namespace
  their MCP server actually exposes.

## [0.1.0] — 2026-05-11

Initial scaffolding release. The plugin compiles, all four slash commands
exist and are implemented end-to-end in prose. End-to-end validation
against a live Linear project is tracked in [SMOKE.md](./SMOKE.md) and
happens per consuming repo.

### Added
- Plugin manifest at `.claude-plugin/plugin.json`.
- `/cadence:init` — scaffolds `.claude/workflow.yaml`, `.claude/agents/*.md`,
  and `.claude/prompts/global.md` into the consumer repo.
- `/cadence:tick` — single-shot bootstrap that picks one Linear issue,
  acquires a soft lock, reconciles state, dispatches the matching subagent,
  posts a tracking comment, and exits.
- `/cadence:sweep` — clears stale `cadence-active` labels left behind by
  killed `/schedule` fires.
- `/cadence:status` — read-only human-facing status view of all issues in
  the workflow.
- Starter subagent templates under `templates/agents/`
  (planner / implementer / reviewer).
- Workflow YAML and global-prompt examples under `templates/`.
- Stokowski → Cadence migration guide ([MIGRATION.md](./MIGRATION.md)).
- Smoke-test checklist ([SMOKE.md](./SMOKE.md)).
