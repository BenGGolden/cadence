# CLAUDE.md

Orientation for an agent working **on the Cadence plugin itself**. For how
Cadence behaves once installed, read [README.md](./README.md).

## What Cadence is

A Claude Code plugin that turns Linear into a multi-agent workflow runner.
Issues flow through a state machine; subagents do the work; humans approve at
gates. There is no daemon — each tick is one shot, fired by `/schedule` or
`/loop`.

## Repo map

- `commands/` — the six slash commands (`tick`, `init`, `sweep`, `status`,
  `create-ticket`, `uninstall`). Each `.md` is **dispatch prose the harness
  executes**, not documentation. `tick.md`, `sweep.md`, and `status.md` are
  also copied to the consumer's `.claude/commands/cadence/` at init so
  `/schedule` cloud routines can dispatch them without resolving a plugin root.
  `uninstall.md` (`/cadence:uninstall`) reverses init — it is plugin-only and
  *not* scaffolded, since it's a deliberate local operation.
- `templates/` — **mirror of the consumer's `.claude/` tree.** Everything
  here is copied 1:1 to the same relative path under `.claude/` by
  `/cadence:init`: `workflow.yaml`, `prompts/global.md`, `ticket-template.md`,
  `agents/cadence/cadence-*.md` (nested + `cadence-` prefixed so the agents
  can't collide with a consumer's same-named agent), `cadence/hooks/*.py`,
  `worktrees/.gitignore` (ignores the harness's
  runtime subagent worktrees from the main checkout), `settings.json` (the
  last is merged into `.claude/settings.json` rather than copied verbatim).
- `templates/cadence/hooks/` — Python files copied to the consumer's
  `.claude/cadence/hooks/`. Some are PreToolUse / UserPromptSubmit
  hooks (`validate_tracking_json.py`, `validate_workflow_on_prompt.py`);
  the rest are deterministic helpers the dispatch
  prose invokes via Bash (`validate_workflow.py`, `_common.py`,
  `parse_comments.py`, `emit_tracking_comment.py`, `classify_drift.py`,
  `classify_gate.py`, `route_fire.py`,
  `compose_lifecycle_context.py`,
  `filter_candidates.py`, `render_status_report.py`,
  `render_sweep_report.py`, `promote_acceptance_criteria.py`).
  `route_fire.py` is the tick.md routing
  orchestrator (the fire's routing decision core) — it imports
  `parse_comments`, `classify_drift`, `classify_gate`, and
  `emit_tracking_comment`'s formatters to emit one routing plan; the
  bootstrap still executes every Linear write. All are always
  overwritten on init — they are plugin-owned executables, not user config.
  The canonical, count-free copy list lives in
  [`scripts/scaffold_files.py`](./scripts/scaffold_files.py)'s
  `SCAFFOLD_PLAN` (the single source of truth — add a hook = add one row).
- `scripts/` — plugin-only command-time helpers (`scaffold_files.py` is the
  Step 2 copy driver and owns `SCAFFOLD_PLAN`; `merge_settings_hooks.py`,
  `merge_settings_permissions.py`, `detect_linear_mcp_namespace.py`,
  `render_next_steps.py`; and `configure_linear.py`, the Step 4c
  orchestrator over the last three). `unscaffold_files.py` (reverses
  `SCAFFOLD_PLAN`) and `render_uninstall_steps.py` (the Linear-cleanup
  checklist) back `commands/uninstall.md`, and the two `merge_settings_*`
  scripts gained a `--remove` unmerge mode for it. Never scaffolded to the
  consumer; invoked only from `commands/init.md` and `commands/uninstall.md`.
  Contract documented in [`scripts/README.md`](./scripts/README.md).
- `.claude-plugin/plugin.json` — plugin manifest (name, version, metadata).
- `.github/workflows/validate.yml` — CI: manifest schema + command frontmatter.
- Root docs: `README.md` (operational shape), `GUIDEPOSTS.md` (design
  principles / the *why*), `CHANGELOG.md` (what shipped, in order),
  `BACKLOG.md` (ideas / deferred work).

## Load-bearing invariants — do not break these

- **`commands/tick.md` prose IS the dispatch logic.** Editing it is a spec
  change, not a doc edit. Step ordering and wording are behavior.
- **The bootstrap is the sole Linear writer.** Subagents read code, make
  changes, and return a Markdown string; the bootstrap posts it verbatim.
- **The bootstrap owns all GitHub PR operations, via GitHub MCP — not `gh`,
  not the subagents.** The implementer only `git push`es a branch and returns
  the PR title/body; the bootstrap creates the PR (reusing the open PR on
  rework) and, for a `merge_on_approve` gate, reads state + merges
  (`create_pull_request` / `list_pull_requests` / `get_pull_request` /
  `merge_pull_request`). The connector scopes to the bound repo, so there is
  **no repo config** and no `GH_TOKEN` / `gh` anywhere.
- **Linear column ↔ workflow state is 1:1.** No aliasing.
- **`commands/*.md` are invoked independently** as slash commands and must
  stay self-contained — do not factor shared prose into a file a command
  would have to `Read` at runtime.
- **Plugin owns logic, consumer owns config.** Don't bake consumer-specific
  assumptions into `commands/` or `templates/`.

## Conventions

- Commit messages: conventional-commit style, no AI-attribution trailer
  (match the existing `git log`).
- Every file in `commands/` needs YAML frontmatter with a `description` and
  `disable-model-invocation: true` — CI enforces this.
- `templates/` files are examples the consumer edits and commits; `commands/`
  files are the plugin's own executable prose. Keep the boundary clean.

## Releasing

**Installers do not track `main`.** The plugin entry in
`.claude-plugin/marketplace.json` pins the source to a git tag via a `url`
source with a `ref` (currently `v0.1.0`). A commit on `main` reaches new
installers only once the steps below bump that `ref`. The `version` in
`.claude-plugin/plugin.json` is the *update label* — already-installed users
are pulled forward only when it changes. **Never also set `version` in the
marketplace entry** (plugin.json silently wins, masking it).

To cut version `X.Y.Z`:

1. Land all the release's changes on `main` (feature branches → PRs, as usual).
2. In one PR: bump `version` in `.claude-plugin/plugin.json` to `X.Y.Z`, and add
   a `## [X.Y.Z] — <date>` section to `CHANGELOG.md` with a matching
   `[X.Y.Z]: https://github.com/BenGGolden/cadence/releases/tag/vX.Y.Z` link
   reference. Merge it.
3. Tag + release from the merge commit: `git tag -a vX.Y.Z -m "Cadence X.Y.Z"`,
   `git push origin vX.Y.Z`, then `gh release create vX.Y.Z --latest` with the
   CHANGELOG section as the notes.
4. In a second PR (or direct push): bump `ref` in
   `.claude-plugin/marketplace.json` to `vX.Y.Z`. This commit lands on `main`
   *after* the tag — that is correct and expected: the marketplace catalog is
   read from `main` HEAD, never from the tag, so the post-tag bump is what
   actually points installers at the new code.

Validate either manifest with `claude plugin validate --strict <path>` (CI
covers `plugin.json`; `claude plugin tag --dry-run` cross-checks that
plugin.json and the marketplace entry agree). Consumers update with
`claude plugin marketplace update cadence` then `claude plugin update
cadence@cadence` (restart to apply).

Two gotchas, both verified empirically:

- Use the **`url`** source (clones over https). The `github` source form
  clones over **ssh** and fails for any user without GitHub SSH keys — do not
  switch to it.
- The install materializes at `…/cache/cadence/cadence/<version>/`, where
  `<version>` is plugin.json's string. So the `ref` pins the *code* and
  `version` pins the *label*: keep them in lockstep (tag `vX.Y.Z` ⇔
  `version: X.Y.Z`), or the reported version will lie about the installed code.

## Where to look

| Need | Doc |
|---|---|
| How the system behaves when installed | `README.md` |
| Why it's designed this way | `GUIDEPOSTS.md` |
| What has shipped, in order | `CHANGELOG.md` |
| Deferred ideas / known gaps | `BACKLOG.md` |
| Helper-script contract (exit codes, stdin/stdout shape) | `scripts/README.md` |
