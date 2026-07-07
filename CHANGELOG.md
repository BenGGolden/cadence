# Changelog

All notable changes to Cadence are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.1] — 2026-07-07

### Changed

- **The `merge_on_approve` PR-outcome decision is now a pure, tested helper.**
  The five-branch outcome selection that used to run as dispatch prose inside
  `tick.md`'s Merge-on-approve sub-phase (which comment to post, which labels to
  change, whether to advance to the terminal) moved into a new
  `classify_merge.py`, mirroring `classify_gate.py`. The two GitHub MCP calls
  (`get_pull_request` / `merge_pull_request`) stay in the bootstrap, which
  remains the sole Linear/GitHub writer and applies the helper's action list
  verbatim. Observable behavior is unchanged (1:1 with 0.5.0); the win is that
  the tick's most consequential branch is deterministic and unit-tested rather
  than re-reasoned by the dispatch model on every merge fire.

## [0.5.0] — 2026-07-06

### Changed

- **Parent context is now load-bearing shared spec, not decoration.** The epic
  description a sub-issue inherits as its **Parent Context** is no longer
  silently truncated — it is inherited **in full**. Over a **4000-char soft
  budget** the compose step emits a non-fatal `CADENCE_WARNING` (surfaced in the
  run log and prepended to the Linear summary) and still inherits everything;
  over a **16000-char hard ceiling** the fire fails with an authoring-error
  message rather than degrading. Both thresholds are constants
  (`PARENT_WARN_CHARS` / `PARENT_MAX_CHARS`) in `compose_lifecycle_context.py`.
- **A fire runs with its full intended context, or it fails.** A parent that
  should load but can't (the `get_issue` fetch errors) now fails the fire
  instead of proceeding on a silently-partial spec. Context that is legitimately
  absent by configuration (no parent, no global prompt, empty parent
  description) still proceeds normally. This replaces the old "shared context
  must never block or fail a fire" rule.
- `/cadence:plan-epic` now advises the operator at epic-authoring time to keep
  the shared description focused (moving project-wide rules to the global prompt,
  per-step detail into the child slices, verification into its own final child),
  warning when the draft approaches the soft budget or the hard ceiling.

## [0.4.0] — 2026-07-06

### Changed

- The three **headless subagents** (planner / implementer / reviewer) gained
  artifact-quality disciplines that close the acceptance-criteria loop end to
  end. The planner states cross-cutting constraints and cross-step contracts
  once, forbids placeholders, names a behavior test per step, and phrases
  proposed acceptance criteria as how-agnostic observable outcomes, with a
  pre-return self-review. The implementer writes a behavior test per AC at a
  self-chosen public seam (reproducing bugs with a failing test first) and
  follows an explicit verification cadence. The reviewer tags every finding
  with a severity and now treats an AC with a code change but no test as
  blocking. Prose-only — no deterministic Python or AC render format changed.
- The interactive front-door commands now **interview from the code**.
  `/cadence:create-ticket` reads the code a ticket touches before prompting,
  proposes candidate answers to confirm or revise, and probes genuine gaps one
  focused question at a time. `/cadence:plan-epic` frames each child as a
  vertical, tracer-bullet slice (allowing an optional prefactoring-first
  child), and turns dependency elicitation into an explicit granularity +
  dependency quiz while keeping the acyclic cycle guard. Both preserve their
  AC render format, confirm-before-write gate, and `disable-model-invocation`.

## [0.3.0] — 2026-06-23

### Added

- The **planner** now recognises issues too large to land as a single
  human-reviewable PR and, instead of a one-shot plan, returns a
  `## Recommendation: Decompose` section with a proposed sub-issue breakdown in
  dependency order. The issue still advances to `plan_review` and waits for a
  human, who can run `/cadence:plan-epic` on it to create the sub-issues. The
  planner stays read-only — it recommends; the human decides at the gate.
- New **`/cadence:plan-epic`**: interactively decompose an epic into ordered
  sub-issues in Linear. It creates or identifies the parent epic in a
  **non-workflow state** (so the epic is never picked up as a task), files the
  children in the workflow's pickup state under the epic, and sets `blockedBy`
  links only where a step must merge before another — so unblocked steps flow
  first on the next `/cadence:tick`. Each child's acceptance criteria are
  validated to the same bar `/cadence:create-ticket` enforces, and every child
  inherits the epic's description as its Parent Context (0.2.0). All Linear
  writes happen only after a single confirmation preview. Plugin-only,
  invokes no subagent, and requires no new permission (`save_issue` is already
  in the consumer's pre-allowed set).

### Changed

- `/cadence:create-ticket` now **creates the issue directly in Linear** (in
  the workflow's pickup state, under the configured team/project) after a
  confirmation preview, so a drafted ticket is immediately eligible for the
  next `/cadence:tick`. It falls back to the previous paste-ready Markdown
  output when Linear can't be written — no usable `workflow.yaml`, missing
  team / pickup-state config, the operator declines, or no Linear MCP write
  verb is available. No new permission is required (`save_issue` is already in
  the consumer's pre-allowed set).

### Fixed

- `/cadence:tick` no longer forces the bootstrap to hand-translate Linear MCP
  fields when writing `candidates.json`. `filter_candidates.py` now accepts the
  MCP's native field names directly — `id` for the human key, `status` for the
  column, and the `{"value": int, "name": str}` priority object — alongside the
  canonical `identifier` / `current_linear_state` / int `priority`, and step 3
  gives the bootstrap an exact literal example to copy. Previously the prose
  named fields the MCP doesn't return, so the bootstrap reconstructed the schema
  from memory and could transcribe a field wrong (a wasted round-trip when
  caught; a silently mis-sorted or dropped candidate when not).

## [0.2.0] — 2026-06-16

### Added

- Subagent prompts now inherit a parent issue's description as a **Parent
  Context** section (rendered after Description, before Transitions, in both
  the default and adversarial variants), so an epic's shared spec lives once
  on the parent issue instead of being repeated on every sub-issue. Best-effort
  and always-on: when an issue has no parent, nothing changes. The inherited
  body is capped (default 4000 chars) to keep it from dominating the prompt.

## [0.1.0] — 2026-06-09

First public release. Cadence is a Claude Code plugin that turns a Linear
board into a multi-agent workflow runner: issues flow through a state machine,
subagents do the work, and humans approve at gates. There is no daemon — each
tick is one shot, fired by a remote `/schedule` routine (the design target) or
a local `/loop`.

### Commands

- **`/cadence:init`** — scaffolds Cadence's config and runtime into the
  consumer's `.claude/` (`workflow.yaml`, agents, prompts, hooks, settings),
  auto-detects the Linear MCP namespace, merges the required permissions, and
  prints an operator handoff with next steps.
- **`/cadence:tick`** — the single-shot bootstrap. Picks one eligible Linear
  issue, acquires a soft lock, reconciles its state, dispatches the matching
  subagent, posts a tracking comment, and exits. `--dry-run` previews the
  fire without writing.
- **`/cadence:sweep`** — clears stale `cadence-active` soft locks left behind
  by interrupted fires.
- **`/cadence:status`** — read-only, human-facing view of every issue in the
  workflow, with per-state summaries, gate verdicts, and concurrency.
- **`/cadence:create-ticket`** — optional local helper for drafting a
  well-formed ticket with acceptance criteria.
- **`/cadence:uninstall`** — reverses `/cadence:init`: removes the scaffolded
  files, unmerges Cadence's settings entries, and prints a checklist for the
  manual Linear cleanup. `--dry-run` previews; `--force` also removes
  user-edited config.

### Workflow engine

- **Linear column ↔ workflow state is 1:1** — no aliasing. Issues advance by
  moving between columns.
- **Three state kinds:** `agent` states (a subagent runs), `gate` states (work
  parks for a human verdict), and `terminal` states.
- **Gates via labels** — humans approve or reject with `cadence-approve` /
  `cadence-rework`, with `max_rework` escalation to `cadence-needs-human`.
  Opt-in `merge_on_approve` merges the issue's PR when a terminal gate is
  approved (configurable merge method).
- **Soft locking** via the `cadence-active` label so concurrent fires don't
  collide; `/cadence:sweep` reclaims abandoned locks.
- **Concurrency caps** (`max_in_flight`) on agent and gate states, with a
  bounded reachability walk so each gate's queue is throttled independently.
- **Acceptance-criteria promotion** — the planner proposes acceptance criteria
  in its summary; they are promoted into the issue description when the plan is
  approved at the gate.
- **Drift handling** — the bootstrap reconciles issues that were moved or
  relabelled out of band.

### Subagents

- Starter **planner / implementer / reviewer** agents, namespaced under
  `agents/cadence/` and `cadence-`-prefixed so they can't collide with a
  consumer's own agents.
- **The bootstrap is the sole Linear writer** — subagents read code, make
  changes, and return a Markdown summary the bootstrap posts verbatim.
- **The bootstrap owns all GitHub PR operations via the GitHub MCP connector**
  — create, reuse-on-rework, and merge. The implementer only `git push`es a
  branch. No `gh`, no `GH_TOKEN`, no repo config: the connector scopes to the
  bound repository.

### Determinism

- The slash-command dispatch prose delegates its non-trivial logic to
  deterministic Python helpers (workflow validation, comment parsing, fire
  routing, candidate filtering, lifecycle-context composition, and status /
  sweep rendering) so behaviour is reproducible and testable rather than
  re-derived by the model each fire.
- Two event hooks guard correctness at runtime (workflow validation on prompt,
  tracking-JSON validation on write).

### Integrations & modes

- **Linear MCP** with namespace auto-detection (bare, `linear-server`, and the
  claude.ai workspace connector display-name form).
- **GitHub MCP** for all PR operations, authenticated via the routine's bound
  repository (or the local connector under `/loop`).
- **Mode A — remote `/schedule`** cloud routine (the design target) and
  **Mode B — local `/loop`**.

### Quality & docs

- `unittest` suite covering the runtime helpers and init-time scripts, run in
  CI alongside plugin-manifest schema and command-frontmatter validation.
- `README.md` (operational shape), `GUIDEPOSTS.md` (design principles), and
  `scripts/README.md` (helper-script contract).
- Installable via `/plugin` from the bundled `marketplace.json`, or from a
  local checkout with `--plugin-dir`.

[0.5.1]: https://github.com/BenGGolden/cadence/releases/tag/v0.5.1
[0.5.0]: https://github.com/BenGGolden/cadence/releases/tag/v0.5.0
[0.4.0]: https://github.com/BenGGolden/cadence/releases/tag/v0.4.0
[0.3.0]: https://github.com/BenGGolden/cadence/releases/tag/v0.3.0
[0.2.0]: https://github.com/BenGGolden/cadence/releases/tag/v0.2.0
[0.1.0]: https://github.com/BenGGolden/cadence/releases/tag/v0.1.0
