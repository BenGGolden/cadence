# Decommission path (`/cadence:uninstall`) — Implementation Plan

## Context

`/cadence:init` scaffolds Cadence into a consumer repo across four kinds of
state:

1. **Files** — every destination in
   [`scripts/scaffold_files.py:SCAFFOLD_PLAN`](../../scripts/scaffold_files.py#L69-L94)
   (hooks, the three `/cadence:*` commands, the four user-config templates,
   `worktrees/.gitignore`).
2. **`.claude/settings.json`** — Cadence's hook entries, merged in by
   [`scripts/merge_settings_hooks.py`](../../scripts/merge_settings_hooks.py).
3. **`.claude/settings.local.json`** — the Linear-MCP permission allowlist,
   merged in by
   [`scripts/merge_settings_permissions.py`](../../scripts/merge_settings_permissions.py)
   (via the `configure_linear.py` orchestrator).
4. **`.cadence/`** — a runtime scratch dir created on every fire by
   [`templates/hooks/_common.py:ensure_cadence_dir`](../../templates/hooks/_common.py#L35-L48).

There is **no reverse operation today.** A consumer who decides Cadence isn't a
fit has to hand-remove all of the above with no checklist. The hook scope-guard
means leftover hooks silently no-op (no broken builds), so the cost is slow
accumulation of dead files — but it's still a manual, error-prone cleanup, and
the Linear side (labels + columns) is invisible to the plugin entirely.

This plan adds a **`/cadence:uninstall`** command that reverses 1–4
deterministically (mirroring the init architecture: thin dispatch prose over
`scripts/` helpers), plus prints the Linear-side cleanup the plugin can't safely
do for the operator.

The four design forks the backlog left open were resolved by the maintainer:

- **Delivery:** command + `scripts/` helpers (mirror init).
- **Removal mode:** hard delete, with a `--dry-run` preview flag.
- **File scope:** remove plugin-owned files unconditionally; remove
  user-edited config files only with `--force`, otherwise leave them and list
  them.
- **Linear cleanup:** print a checklist only — **never** call Linear MCP. This
  preserves Cadence's clean separation of plugin-managed state (files) from
  consumer-managed state (Linear), the same boundary the rest of the plugin
  keeps.

Backlog source: [`BACKLOG.md`](../../BACKLOG.md) "Decommission path /
`/cadence:uninstall`".

## Scope

**In scope**

- A new `commands/uninstall.md` dispatch-prose command (`/cadence:uninstall`).
- A new `scripts/unscaffold_files.py` Step-2 driver that reverses
  `SCAFFOLD_PLAN`, removes empty Cadence dirs and `.cadence/`, and supports
  `--dry-run` / `--force`.
- An **unmerge** (`--remove`) mode added to the two existing settings-merge
  scripts (`merge_settings_hooks.py`, `merge_settings_permissions.py`), reusing
  their existing Cadence-ownership detectors.
- A new `scripts/render_uninstall_steps.py` that emits the Linear-cleanup
  checklist (mirrors `render_next_steps.py`; see GUIDEPOSTS #7 below).
- Unit tests for the new removal logic.
- Docs: README, CHANGELOG, CLAUDE.md, scripts/README.md.

**Out of scope / explicit non-goals**

- Any Linear MCP call (label/column deletion). Checklist only.
- Move-aside / quarantine mode. Hard delete is the chosen strategy; git history
  is the safety net.
- A `.claude/cadence/` namespacing refactor (the backlog's third open question).
  That's a larger restructure of where Cadence files live; this plan removes
  files from their *current* scattered locations only.
- Scaffolding `uninstall.md` into the consumer (`tick`/`sweep`/`status` are
  scaffolded so `/schedule` can reach them; uninstall is a deliberate *local*
  operation run with the plugin still installed, so it stays plugin-only like
  `init.md` and `create-ticket.md`).
- A plugin-manifest version bump. Land under CHANGELOG `[Unreleased]`, matching
  how recent features (`merge_on_approve`, GitHub MCP) shipped.

## Affected areas

**New files**

- `commands/uninstall.md` — dispatch prose for `/cadence:uninstall`. YAML
  frontmatter with `description`, `argument-hint: "[--dry-run] [--force]"`,
  `disable-model-invocation: true` (CI enforces the latter two-of-three).
- `scripts/unscaffold_files.py` — Step-2 removal driver. Imports `SCAFFOLD_PLAN`
  from `scaffold_files.py` (single source of truth — never re-list the files).
- `scripts/render_uninstall_steps.py` — emits the Linear-cleanup checklist.
  Mirrors `render_next_steps.py` (same UTF-8-forcing import-time stdio setup).
  Per GUIDEPOSTS #7 ("Prefer deterministic code to agent prose"), text the
  command emits is rendered by a script, not reproduced from a prose block —
  exactly as the init handoff block already is.
- `tests/test_unscaffold_files.py` — coverage for the removal driver.
- `tests/test_render_uninstall_steps.py` — asserts the checklist block is
  emitted verbatim (golden-fixture compare, like `test_render_next_steps.py`).
- `tests/test_merge_settings_hooks.py` — **new** (there is currently no direct
  test for this script); covers the new unmerge mode plus baseline merge.
- `tests/test_merge_settings_permissions.py` — **new** (currently exercised only
  indirectly via `test_configure_linear.py`); covers the new unmerge mode.
- `tests/fixtures/uninstall/` — golden summary text for `unscaffold_files.py`
  (mirrors `tests/fixtures/init/`).

**Changed files**

- `scripts/merge_settings_hooks.py` — add a `--remove` mode that strips Cadence
  hook entries (reuse [`_entry_is_cadence`](../../scripts/merge_settings_hooks.py#L43-L52))
  and deletes the file if it reduces to `{}`. Add `--dry-run`.
- `scripts/merge_settings_permissions.py` — add a `--remove` mode that strips
  Cadence allowlist entries (reuse
  [`_is_cadence_owned`](../../scripts/merge_settings_permissions.py#L74-L91))
  and deletes the file if it reduces to `{}`. Add `--dry-run`.
- `README.md` — new "Uninstalling Cadence" section; cross-link from the "Files
  this plugin scaffolds" section ([README.md:575](../../README.md#L575)).
- `CHANGELOG.md` — `[Unreleased]` entry.
- `CLAUDE.md` — repo map: "five slash commands" → six; note the new scripts.
- `scripts/README.md` — the helper table says "six helpers ... invoked **only by
  `commands/init.md`**"; broaden to include the uninstall driver and the new
  `--remove` modes.

## Implementation steps

### 1. `scripts/unscaffold_files.py` (the removal driver)

Mirror the shape of `scaffold_files.py`. Reuse its plan:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # not needed if run with cwd=scripts; see note
from scaffold_files import SCAFFOLD_PLAN
```

> Note: `scaffold_files.py` is imported as a sibling module the same way
> `render_next_steps.py` already does it (`from scaffold_files import
> SCAFFOLD_PLAN`) — when run as `python scripts/unscaffold_files.py`, the
> script's own dir is `sys.path[0]`, so the bare import resolves. Match that
> exact pattern; no `sys.path` munging beyond what `render_next_steps.py` does
> (which is none).

CLI: `python unscaffold_files.py [--dry-run] [--force]` (no `--plugin-root` —
removal reads nothing from the plugin; it only deletes destinations relative to
cwd, which is the consumer repo root per the init Step-1 invariant).

Behavior:

- **File removal policy** (per `SCAFFOLD_PLAN` row's policy column):
  - `plugin-owned` → delete unconditionally (they're plugin executables).
  - `user-config` → delete only with `--force`; otherwise **skip and record**
    for the summary (the operator may have edited/committed these).
- Missing destinations are not errors (idempotent re-run on a half-uninstalled
  repo finishes the job) — count them separately as "already absent".
- **`.cadence/`** scratch dir: remove recursively (it's runtime scratch,
  gitignored, plugin-owned). Use `shutil.rmtree(".cadence", ignore_errors=...)`
  — but prefer an explicit walk so `--dry-run` can list contents; at minimum
  remove the dir tree on a real run and skip it on dry-run.
- **Empty-dir cleanup** (after file removal): remove these dirs **only if
  empty**, deepest-first:
  `.claude/commands/cadence`, `.claude/commands` (if it ends up empty),
  `.claude/hooks`, `.claude/worktrees`, `.claude/agents`, `.claude/prompts`.
  Never remove `.claude/` itself (consumer owns it). "Only if empty" means a
  skipped user-config file (no `--force`) correctly keeps `.claude/agents` and
  `.claude/prompts` alive — list them in the summary as left-behind.
- **`--dry-run`**: compute and print the full would-remove / would-skip /
  already-absent / would-leave summary and **write/delete nothing**. Exit 0.
- **Summary** (stdout, byte-stable for golden fixtures, UTF-8-forced via the
  same `_force_utf8_stdio()` helper `scaffold_files.py` uses): counts of
  removed, skipped-user-config, already-absent, plus the explicit list of
  user-config files left behind and any non-empty dirs left behind. On dry-run,
  prefix lines with a clear "[dry-run] would remove:" framing.

Exit codes: `0` success (including dry-run and idempotent re-run); `1`
read/write error (deletion failure on a real run — report which path and
continue best-effort, exiting 1 at the end if any deletion failed, matching the
init driver's "partial is acceptable" stance).

### 2. Unmerge mode for `merge_settings_hooks.py`

Add `_unmerge(existing)` that returns a copy with every Cadence hook entry
removed from each event in `EVENTS`, reusing `_entry_is_cadence`:

```python
def _unmerge(existing):
    merged = json.loads(json.dumps(existing))
    hooks = merged.get("hooks")
    if not isinstance(hooks, dict):
        return merged
    for event in EVENTS:
        entries = hooks.get(event) or []
        kept = [e for e in entries if not _entry_is_cadence(e)]
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:                       # no events left
        merged.pop("hooks", None)
    return merged
```

Wire a `--remove` flag in `main()`:

- If the settings file doesn't exist → nothing to do, exit 0 with a note.
- Parse-error / non-dict → same refuse-to-corrupt behavior as the merge path
  (print stderr, exit 1). **Do not** delete a file you can't parse.
- After unmerge, if the result is `{}` (the file was effectively Cadence-only),
  **delete the file**; otherwise write it back (same indent=2 + trailing
  newline as the merge path).
- `--remove` should not require `--template-path` (there's no template to read
  when removing) — make `--template-path` required only for the merge path.
- Add `--dry-run`: print what would change (entries removed / file deleted vs
  rewritten) and write nothing.

### 3. Unmerge mode for `merge_settings_permissions.py`

Symmetric. Add `_unmerge_allowlist(existing_allow)` (drop every
`_is_cadence_owned` entry) and an `--remove` mode in `main()`:

- Operate on `permissions.allow`. After removal, if `allow` is empty, delete the
  `allow` key; if `permissions` is then empty, delete it; if the whole dict is
  `{}`, delete the file.
- `--remove` does **not** need `--namespace` (the `_is_cadence_owned` heuristic
  already matches any `linear`-containing namespace, so removal is
  namespace-agnostic — this is why uninstall needs no MCP detection step). Make
  `--namespace` required only for the merge/`--print-only` paths.
- Same parse-error / non-dict refusal and `--dry-run` behavior as step 2.

### 4. `commands/uninstall.md` (dispatch prose)

Frontmatter:

```yaml
---
description: Remove Cadence from this repo — deletes scaffolded .claude/ files, unmerges hook + permission entries, and prints the Linear-side cleanup checklist.
argument-hint: "[--dry-run] [--force]"
disable-model-invocation: true
---
```

Prose structure (self-contained — do **not** factor shared text into a file the
command must `Read` at runtime; this is a load-bearing invariant):

- **Step 0 — capture args.** Accept any combination of `--dry-run` and
  `--force` (case-insensitive), in any order. Reject any other token with an
  error and stop. Forward both flags to the scripts in steps that accept them.
- **Step 1 — confirm working directory & intent.** State that this runs from the
  consumer repo root and that (without `--dry-run`) it permanently deletes
  files; git history is the recovery path. Per the user's global preference
  ("always confirm before destructive operations"), a real (non-`--dry-run`)
  run should confirm before proceeding — **but** note that under `/schedule`
  there's no human; uninstall is a local-only command, so an interactive
  confirm is acceptable. (Recommend: on a non-dry, non-`--force` run, suggest
  running `--dry-run` first.)
- **Step 2 — remove files:**
  `python "${CLAUDE_PLUGIN_ROOT}/scripts/unscaffold_files.py" [--dry-run] [--force]`
  — print stdout (the removal summary).
- **Step 3 — unmerge hooks:**
  `python "${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_hooks.py" --remove --settings-path .claude/settings.json [--dry-run]`
  — print stdout; on exit 1 (unparseable settings) print stderr and continue to
  step 5 (don't abort the whole uninstall over one file).
- **Step 4 — unmerge permissions:**
  `python "${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_permissions.py" --remove --settings-path .claude/settings.local.json [--dry-run]`
  — print stdout; same best-effort handling.
- **Step 5 — Linear cleanup checklist** (always, even on dry-run):
  `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_uninstall_steps.py"` — print its
  stdout verbatim. The checklist states that the `cadence-active`,
  `cadence-needs-human`, `cadence-approve`, `cadence-rework` labels are safe to
  delete once no issue still carries them; that the workflow columns mapped in
  `workflow.yaml` are no longer needed if Cadence was their only consumer; and
  that the plugin does **not** touch Linear, so the operator must do this by
  hand.
- **Errors / guardrails.** Never call Linear MCP, never invoke a subagent, never
  run shell beyond the four scripts above.

> The checklist is rendered by a script rather than embedded as a verbatim prose
> block, per GUIDEPOSTS #7 — text the command emits is deterministic code's job
> (the init handoff block sets this precedent via `render_next_steps.py`). The
> block currently has no interpolation points, but keeping it in a script keeps
> the prose thin and the output unit-testable.

### 5. `scripts/render_uninstall_steps.py` (the Linear checklist)

Mirror `render_next_steps.py`: force UTF-8 stdout at import (the checklist uses
em dashes / bullets), keep the block as a module-level `_TEMPLATE` string, and
expose a `render()` returning it (no interpolation points today, so `render()`
takes no args). `main()` writes `render()` to stdout and exits 0. Keep the
wording aligned with the gate-label hint already in `render_next_steps.py`'s
"Gate labels" section so the install/uninstall halves read consistently.

### 6. Ordering constraints

- Steps 2→3→4 are independent in effect but run in that order so the summary
  reads files-then-settings. Step 5 always runs last and always prints.
- `--dry-run` must be threaded to the three mutating scripts so the preview is
  complete; `render_uninstall_steps.py` takes no flags and always prints.

## Commit & PR plan

Per the repo's feature-branch + PR convention (and the maintainer's standing
preference), land on a feature branch via a single PR. Branch name:
`feat/decommission-path` (matches the `feat/marketplace-config` style in
`git log`). Conventional-commit messages, **no AI-attribution trailer**.

Suggested commit boundaries (each independently reviewable):

1. `feat: add --remove (unmerge) mode to settings-merge scripts` — steps 2–3
   above plus their new test files.
2. `feat: add unscaffold_files.py removal driver` — step 1 plus
   `tests/test_unscaffold_files.py` and fixtures.
3. `feat: add /cadence:uninstall command` — `commands/uninstall.md` (step 4)
   plus `scripts/render_uninstall_steps.py` and its test (step 5).
4. `docs: document the decommission path` — README/CHANGELOG/CLAUDE.md/
   scripts/README.md.

(1 and 2 could merge into one commit; keep 3 and 4 separate so the behavior
change and the docs are reviewable apart.)

## Docs to update

- **`README.md`** — add an "Uninstalling Cadence" section (near "Files this
  plugin scaffolds", README.md:575). Document `/cadence:uninstall`,
  `--dry-run`, `--force` (needed to remove user-edited config), what it removes
  (files, hook entries, permission entries, `.cadence/`), and that the
  Linear-side cleanup is a printed manual checklist. Cross-link from the
  scaffold-files section.
- **`CHANGELOG.md`** — `[Unreleased]` → new `### Added — Decommission path
  (/cadence:uninstall)` entry summarizing the command and the unmerge modes.
- **`CLAUDE.md`** — repo map: change "the five slash commands (`tick`, `init`,
  `sweep`, `status`, `create-ticket`)" to include `uninstall`; mention
  `scripts/unscaffold_files.py` and the new `--remove` modes in the `scripts/`
  bullet.
- **`scripts/README.md`** — the intro says the helpers are "invoked **only by**
  `commands/init.md`"; broaden to "invoked by `commands/init.md` and
  `commands/uninstall.md`", add rows for `unscaffold_files.py` and
  `render_uninstall_steps.py`, and note the `--remove` mode on the two merge
  scripts. Update the helper count if stated.

## Acceptance Criteria

- [ ] **AC-1** — A new `commands/uninstall.md` exists with YAML frontmatter
  containing `description`, `argument-hint`, and `disable-model-invocation:
  true`; the command-frontmatter CI job passes for it.
- [ ] **AC-2** — `scripts/unscaffold_files.py` imports `SCAFFOLD_PLAN` from
  `scaffold_files.py` and removes every `plugin-owned` destination
  unconditionally; the file list is never duplicated in the new script.
- [ ] **AC-3** — Without `--force`, `unscaffold_files.py` leaves every
  `user-config` destination in place and lists each one in its summary; with
  `--force`, it removes them too.
- [ ] **AC-4** — `unscaffold_files.py` removes the `.cadence/` scratch dir and
  removes Cadence-created `.claude/` subdirectories **only when they are empty**
  (so a skipped user-config file keeps its parent dir alive), and never removes
  `.claude/` itself.
- [ ] **AC-5** — `--dry-run` on all three scripts writes/deletes nothing
  (verified by asserting file contents are byte-identical before and after) and
  prints a would-remove summary; exit code 0.
- [ ] **AC-6** — Running `unscaffold_files.py` twice in a row succeeds both
  times (idempotent); the second run reports the destinations as already absent
  rather than erroring.
- [ ] **AC-7** — `merge_settings_hooks.py --remove` strips only Cadence hook
  entries (non-Cadence hook entries are preserved byte-for-byte) and deletes
  `.claude/settings.json` iff it reduces to `{}`; an unparseable settings file
  is left untouched and reported (exit 1), never corrupted.
- [ ] **AC-8** — `merge_settings_permissions.py --remove` strips only
  Cadence-owned `permissions.allow` entries (unrelated allow entries preserved)
  and deletes `.claude/settings.local.json` iff it reduces to `{}`; works
  without a `--namespace` argument.
- [ ] **AC-9** — `/cadence:uninstall` prose invokes exactly the four scripts
  (`unscaffold_files.py`, the two `--remove` merges, `render_uninstall_steps.py`),
  forwards `--dry-run`/`--force` to the three mutating scripts, prints each
  script's output, and ends with the rendered Linear-cleanup checklist naming the
  four `cadence-*` labels and the workflow columns; it makes no Linear MCP call
  and spawns no subagent.
- [ ] **AC-10** — The Linear-cleanup checklist is emitted by
  `scripts/render_uninstall_steps.py` (deterministic code, per GUIDEPOSTS #7),
  not embedded as a verbatim block in `commands/uninstall.md`.
- [ ] **AC-11** — `python -m unittest discover -s tests -v` passes, including new
  `test_unscaffold_files.py`, `test_merge_settings_hooks.py`,
  `test_merge_settings_permissions.py`, and `test_render_uninstall_steps.py`.
- [ ] **AC-12** — README, CHANGELOG, CLAUDE.md, and scripts/README.md are
  updated as listed in "Docs to update".

## Testing

### Unit tests

Follow the existing pattern (`tests/test_scaffold_files.py`): build inputs in a
`tempfile.TemporaryDirectory()`, invoke the script via `subprocess` with
`cwd` set to the consumer dir, force UTF-8, and compare stdout against golden
fixtures under `tests/fixtures/uninstall/`.

- **`tests/test_unscaffold_files.py`**
  - *Happy path*: scaffold a full tree (materialize every `SCAFFOLD_PLAN`
    destination + `.cadence/` + a couple of `.claude/settings*.json`), run
    without `--force`; assert all `plugin-owned` dests + `.cadence/` gone,
    `user-config` dests preserved, parent dirs of preserved files kept, empty
    Cadence dirs (`hooks`, `commands/cadence`, `worktrees`) removed, `.claude/`
    survives, summary == fixture.
  - *`--force`*: same setup; assert user-config dests also removed and their now
    empty dirs (`agents`, `prompts`) removed.
  - *`--dry-run`*: snapshot a content hash of the whole tree, run, assert tree
    byte-identical afterward and summary uses the dry-run framing.
  - *Idempotent re-run*: run twice; second run exits 0 and reports "already
    absent"; tree unchanged after second run.
  - *`.cadence/` with nested content*: ensure recursive removal.
  - *Plan integrity reuse*: assert the script removes exactly the
    `SCAFFOLD_PLAN` destinations (no hard-coded second list).
- **`tests/test_merge_settings_hooks.py`** (new file; cover merge **and**
  unmerge)
  - Merge baseline (currently untested): merging into an empty/missing file and
    into a file with non-Cadence hooks; idempotency.
  - Unmerge: removes Cadence entries, preserves a hand-added non-Cadence
    `PreToolUse` entry; deletes the file when it reduces to `{}`; leaves a file
    that still has non-Cadence content; refuses an unparseable file (exit 1, file
    unchanged); `--dry-run` writes nothing.
- **`tests/test_merge_settings_permissions.py`** (new file; focus on unmerge)
  - Unmerge across all three namespace shapes (`linear`, `linear-server`,
    `claude_ai_Linear`); preserves unrelated `allow` entries; deletes file when
    it reduces to `{}`; works with no `--namespace`; `--dry-run` writes nothing.
- **`tests/test_render_uninstall_steps.py`** (new file): assert
  `render_uninstall_steps.py` emits the checklist block byte-for-byte against a
  golden fixture (mirrors `test_render_next_steps.py`); confirm it names all four
  `cadence-*` labels.

### Manual testing

In a throwaway consumer repo (or a copy):

1. Run `/cadence:init`, confirm the full `.claude/` tree + `.cadence/` (after one
   tick) + the two settings files exist.
2. Run `/cadence:uninstall --dry-run`. **Expected:** a complete would-remove
   report; **no files change** (verify with `git status` / file mtimes).
3. Edit `.claude/agents/planner.md` (simulate operator customization). Run
   `/cadence:uninstall` (no `--force`). **Expected:** hooks + commands +
   `.cadence/` gone; `planner.md` (and `.claude/agents/`) preserved and listed
   as left-behind; the Linear checklist printed.
4. Run `/cadence:uninstall --force`. **Expected:** remaining user-config files
   removed; empty Cadence dirs gone; `.claude/` itself remains (it may still hold
   non-Cadence files); settings files either cleaned of Cadence entries or
   deleted if they were Cadence-only.
5. Run `/cadence:uninstall` again. **Expected:** clean exit, everything reported
   already absent.
6. Inspect `.claude/settings.json` after a run on a file that *also* had a
   non-Cadence hook — confirm the non-Cadence hook survived.

### Verification commands

Discovered for this repo (do not assume defaults):

- **Tests:** `python -m unittest discover -s tests -v`
  (from repo root; the only test runner this project uses — see
  `scripts/README.md` and the `python-tests` CI job).
- **Command-frontmatter check (local mirror of CI):** there is no committed
  local lint script; CI runs an inline Python snippet in
  `.github/workflows/validate.yml` (the `command-frontmatter` job) that parses
  every `commands/*.md` frontmatter and fails on a missing `description` /
  warns on `disable-model-invocation != true`. Easiest local check: open
  `commands/uninstall.md` and confirm the three frontmatter keys are present.
  (No `make`/`npm`/`tox` target exists — the repo has no JS/Python package
  manifest beyond `pip install pyyaml` for tests.)
- **Lint/type-check/build:** none exist in this repo (no linter config, no build
  step; the plugin is prose + stdlib Python). Nothing to run beyond the test
  suite and the frontmatter sanity check.

## Risks & open questions

- **Deleting committed user config** is the sharpest edge. The `--force`
  gate + the default-leave-and-list behavior mitigate it, and the prose should
  steer the operator to `--dry-run` first. Git history is the ultimate recovery
  path (called out in the prose).
- **Settings-file deletion when reduced to `{}`.** This assumes a `{}` settings
  file was effectively Cadence-only and safe to remove. If a consumer
  intentionally keeps an empty `.claude/settings.json`, uninstall removes it.
  This is judged acceptable (an empty settings file is a no-op), but flag it in
  the README so it isn't surprising. If the implementer prefers, the safer
  variant is to leave an empty `{}` file in place — decide during build, but the
  plan's default is to delete it for a cleaner exit.
- **`.claude/worktrees/`** may contain harness runtime worktrees (not just the
  scaffolded `.gitignore`). Removal of the dir is gated on "only if empty," so
  live worktrees keep it alive and it gets listed as left-behind rather than
  force-deleted. Confirm this is the desired behavior (it avoids nuking an
  in-flight subagent's worktree).
- **No regression harness** for the dispatch prose (that's a separate backlog
  item). The command's *prose* behavior is verified by manual testing only; the
  *scripts* it calls are unit-tested. Keep the prose thin (all four steps are
  one-line script invocations) so the untested surface stays minimal.
