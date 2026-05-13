# Cadence — Hardening Plan

Reduce the fragility of the prose-as-state-machine in `commands/tick.md`
without introducing a daemon. Push deterministic bookkeeping out of LLM
prose into small Python helper scripts, then add Claude Code hooks as
guard rails around the parts that have to stay in prose.

This document is the single source of truth for the hardening work. It is
written so a fresh Claude session can implement any one of the two phases
(P1, P2) without rereading this entire conversation.

For the operational shape of the system this plan modifies, see
[README.md](./README.md). The canonical bootstrap prose lives in
[commands/tick.md](commands/tick.md).

---

## Goal

Make `commands/tick.md` more robust to LLM execution variance — specifically
the four failure modes the current prose is most exposed to:

1. **Counting errors.** "Count prior attempt markers" / "count prior rework
   comments" in steps 10c.1 and 11 are LLM bookkeeping; they will get wrong
   under context pressure.
2. **JSON emission errors.** Tracking-comment bodies embed JSON. An LLM that
   emits invalid JSON poisons attempt counting on every subsequent fire.
3. **JSON parsing errors.** Reading prior tracking comments back; same
   surface as (2) but on the read side.
4. **Validation skim.** The five rules in step 3 of `tick.md` are easy for
   an LLM to gloss as "passed" without showing work. Commit `0f0ad9b`
   already tightened dry-run; the live path has the same exposure.

Step ordering and high-level dispatch stay in prose. Anything mechanical
moves to scripts. Hooks act as a backstop for the parts that stay in prose.

## Non-goals

- A regression harness with fake-MCP fixtures and golden files. Deferred
  until there's a real consumer beyond the author. Mentioned in "Future
  work" only.
- Replacing subagents with scripts. Subagent invocation is the LLM's job
  and stays in prose.
- Changing the Linear soft-lock semantics, the workflow.yaml schema, the
  subagent contract, or the README operator setup. Hardening only.
- Multi-runner support. Cadence stays Claude-only.
- Cost controls / token budgeting. Out of scope for this plan.

## Decisions made up front

These were settled in conversation before this plan was written. Do not
revisit unless the implementing session hits a concrete blocker.

| Decision | Choice | Reason |
|---|---|---|
| Helper-script language | **Python 3.11+** | Standard library covers everything needed (yaml, json, datetime, argparse). Available on Anthropic's `/schedule` Linux env and on Windows via the standard installer. No third-party deps. |
| Hook placement | **Scaffolded into the consumer's `.claude/settings.json` by `/cadence:init`** (hook scripts ship in `templates/hooks/` and are copied to `.claude/hooks/` alongside the existing `.claude/agents/` scaffolding). | Repo-committed `.claude/settings.json` hooks fire in cloud routines per [claude-code-on-the-web](https://code.claude.com/docs/en/claude-code-on-the-web.md) ("Your repo's `.claude/settings.json` hooks → Yes → Part of the clone"). Plugin-shipped `hooks/hooks.json` cloud-routine activation is **not documented** either way; scaffolding is the path with a paper trail. Scope guard becomes implicit — hook files only exist in repos that ran `/cadence:init`. |
| Phasing | **One PR per phase** (P1, P2) | Each phase is independently valuable and revertible. P3 (regression harness) is deferred entirely. |
| Layer 3 | **Omit from this plan** | Build it when a real regression hits or a second user shows up. |
| Hooks in `/schedule` mode | **Fire in cloud routines when committed to the cloned repo's `.claude/settings.json`** ([claude-code-on-the-web](https://code.claude.com/docs/en/claude-code-on-the-web.md): "user-level settings don't carry over to cloud sessions. In the cloud, only hooks committed to the repo run"). | Drives the "scaffold via init" decision above. Plugin-shipped `hooks/hooks.json` is in an undocumented zone for cloud routines, so the plan avoids it. |
| Python in `/schedule` cloud env | **Available by default** (Ubuntu 24.04 image ships Python 3.x + pip/poetry/uv/black/mypy/pytest/ruff per [claude-code-on-the-web](https://code.claude.com/docs/en/claude-code-on-the-web.md)). No fallback needed. | Confirmed; safe to depend on. |
| MCP server availability in `/schedule` | Cloud routines do NOT inherit local `claude mcp add` servers. Use account-level claude.ai connectors **or** a repo-committed `.mcp.json` ([routines](https://code.claude.com/docs/en/routines.md)). | Doesn't affect this plan directly (the plan doesn't add new MCP servers), but documented in [README.md](./README.md) consumer setup so operators aren't surprised. |

---

## Architecture

### Where files live

```
<plugin-repo-root>/
  .claude-plugin/
    plugin.json
  commands/
    tick.md                ← edited in P1 (call scripts) and again in P2 (init scaffolds hooks)
    init.md                ← edited in P2 (copy hook scripts + merge settings.json)
    sweep.md               ← edited in P1 (use parse-comments)
    status.md              ← edited in P1 (use parse-comments)
  scripts/                 ← NEW (P1) — invoked directly from command prose via Bash
    validate_workflow.py
    parse_comments.py
    emit_tracking_comment.py
    _common.py             ← shared helpers (workflow.yaml-loader)
  templates/
    workflow.example.yaml  ← unchanged
    global-prompt.example.md  ← unchanged
    agents/                ← unchanged
    hooks/                 ← NEW (P2) — copied into consumer's .claude/hooks/ by /cadence:init
      validate_tracking_json.py
      validate_workflow_on_prompt.py
      audit_linear_writes.py
    settings.example.json  ← NEW (P2) — the hooks block /cadence:init merges into consumer's settings.json
  HARDENING-PLAN.md        ← this file
```

Consumer repo after `/cadence:init` (with P2 landed):

```
<consumer-repo>/
  .claude/
    workflow.yaml          ← P0
    prompts/global.md      ← P0
    agents/{planner,implementer,reviewer}.md  ← P0
    hooks/                 ← P2 — copied from templates/hooks/
      validate_tracking_json.py
      validate_workflow_on_prompt.py
      audit_linear_writes.py
    settings.json          ← P2 — created or merged by /cadence:init
```

The plugin manifest at `.claude-plugin/plugin.json` does NOT need to
declare any hooks (the plan no longer ships hooks via plugin metadata —
they're scaffolded into the consumer repo). Manifest changes in P2 are
limited to a version bump and an updated `keywords` / `description` if
desired.

### Script invocation contract

All scripts are stdout-stdin-stderr pure:

- Args via `argparse`. Positional for required, `--flag` for optional.
- Inputs that may exceed CLI arg limits (Linear comment payloads) come
  via a `--input PATH` arg pointing at a temp file the bootstrap wrote.
- Successful output is **JSON on stdout** unless the script's job is to
  produce a Linear comment body (in which case stdout is the comment body
  verbatim).
- Errors go to **stderr** as human-readable text.
- Exit code: `0` on success, `1` on bad input, `2` on validation failure,
  `3` on internal error. The bootstrap maps these to user-visible messages
  in `tick.md`.

### Hook scope-guard contract (defense in depth)

Because the hook scripts ship into the consumer repo via `/cadence:init`,
they only exist where Cadence is in use — scope is implicit. The original
"silent no-op when `.claude/workflow.yaml` is absent" guard is no longer
load-bearing for the global-pollution concern.

Implementing sessions should still include a lightweight scope guard at
the top of each hook script:

```python
from pathlib import Path
import sys

if not Path.cwd().joinpath(".claude/workflow.yaml").is_file():
    sys.exit(0)  # workflow.yaml deleted while hooks still installed — no-op
```

This handles the edge case where a consumer removes their `workflow.yaml`
(decommissioning Cadence) but forgets to remove the hook entries from
`settings.json`. Cheap and obvious; keep it.

### `_common.py`

A single small shared module to avoid copy-paste:

```python
# scripts/_common.py
import sys, yaml
from pathlib import Path

WORKFLOW_PATH = Path(".claude/workflow.yaml")

def load_workflow():
    """Read and yaml.safe_load .claude/workflow.yaml. On failure, print
    to stderr and exit 1. Returns the parsed dict."""
    ...

def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)
```

Imported by every script in `scripts/`. Hooks in `hooks/` do not import
from `scripts/` — they should remain self-contained so the plugin's hook
loader doesn't need to resolve relative paths.

---

# Phase 1 — Helper scripts

**Outcome**: `commands/tick.md` delegates validation, comment parsing, and
comment emission to deterministic scripts. The prose left in `tick.md` is
high-level dispatch and Linear MCP calls. Sweep and status reuse the same
parse-comments helper.

## P1.1 — `scripts/validate_workflow.py`

**Purpose**: enforce the five validation rules from `tick.md` step 3 in code.

**CLI**:
```
python scripts/validate_workflow.py [--workflow-path PATH] [--evidence]
```

- `--workflow-path` defaults to `.claude/workflow.yaml`.
- `--evidence` makes the script also print the structured per-rule evidence
  that the dry-run report requires (see `tick.md` step 0 / commit `0f0ad9b`).

**Exit codes**:
- `0` — all five rules pass.
- `2` — one or more rules fail.
- `1` — could not read or parse the YAML at all.

**Stdout on success (without `--evidence`)**:
```json
{
  "valid": true,
  "entry_state_name": "plan",
  "entry_subagent": "planner",
  "workflow_linear_states": ["Backlog", "Planning", "Implementing", "In Review", "Approved", "Needs Rework", "Done"],
  "pickup_state": "Backlog",
  "states": { "plan": {...}, "implement": {...}, ... }
}
```

**Stdout on success (with `--evidence`)**: same JSON, plus an additional
top-level key `"evidence"` containing one block per rule with the shape
already documented in `tick.md` step 0 rules 1-5.

**Stderr on failure**: human-readable, names the offending keys and the
failing rule number. Format example:
```
Rule 1 (Linear-state uniqueness) FAILED:
  states.plan.linear_state and states.review.linear_state both = "In Review"
```

**Rules to implement** (verbatim from `tick.md` step 3):
1. Uniqueness across every `linear_state`, `approved_linear_state`, `rework_linear_state`.
2. `entry` references a defined `type: agent` state.
3. Every `next` / `on_approve` / `on_rework` resolves.
4. Every `state.subagent` resolves to `.claude/agents/{name}.md` on disk.
5. `linear.pickup_state` non-empty.

**Where it gets called**:
- `commands/tick.md` step 3 — replace the prose validation with:
  > Invoke Bash: `python ${CLAUDE_PLUGIN_ROOT}/scripts/validate_workflow.py`.
  > If exit code is non-zero, print stderr verbatim and exit (no Linear writes).
  > If exit code is zero, parse stdout JSON and use it as the validated
  > workflow config for the rest of the fire.
- `commands/tick.md` step 0 (dry-run) — replace the per-rule evidence prose
  with `python ${CLAUDE_PLUGIN_ROOT}/scripts/validate_workflow.py --evidence`.
  The current per-rule evidence text becomes the script's responsibility.
- `commands/sweep.md` step 1 — same swap (it currently reads workflow.yaml
  for the lock label and pickup state).
- `commands/status.md` step 1 — same swap.

## P1.2 — `scripts/parse_comments.py`

**Purpose**: replace LLM counting / classification of Linear comments with a
deterministic pass.

**CLI**:
```
python scripts/parse_comments.py --input PATH --target-state STATE [--gate-name STATE]
```

- `--input` is a path to a temp file containing the issue's full comment list
  as JSON (an array of objects with `id`, `body`, `createdAt`, `user` keys —
  the canonical Linear MCP shape; the script must be tolerant of camelCase
  vs snake_case keys since MCP vendors vary).
- `--target-state` is the workflow state name being counted against.
- `--gate-name` is the gate name (used for rework-count and rework-context
  gathering); omit if not in a gate context.

**Exit code**: `0` always (errors surface as structured JSON, not exit
codes — the bootstrap needs the data to make decisions either way).

**Stdout (always JSON)**:
```json
{
  "latest_tracking_comment": {
    "kind": "state" | "gate" | "reconcile" | null,
    "state": "plan",
    "attempt": 2,
    "status": null | "failed" | "waiting" | "rework" | "escalated",
    "raw_json": {...}
  },
  "attempt_count": 2,
  "rework_count": 1,
  "rework_context": [
    { "body": "...", "author": "...", "createdAt": "..." }
  ],
  "parse_errors": []
}
```

**Counting rules** (from `tick.md` steps 10c.1 and 11):
- `attempt_count`: number of `cadence:state` (or legacy `stokowski:state`)
  comments whose JSON has `state == target_state` AND has no `status` field.
  Comments with `status: "failed"` do NOT count.
- `rework_count`: number of `cadence:gate` comments whose JSON has
  `state == gate_name` AND `status == "rework"`.
- `rework_context`: comments posted AFTER the most recent tracking comment,
  whose body does not start with a tracking-comment prefix, oldest-first.
  Author identity is best-effort; the script includes everything that isn't
  obviously a bot.

**Legacy compatibility**: accept both `<!-- cadence:` and `<!-- stokowski:`
prefixes. When parsing `stokowski:` JSON, treat `run` as `attempt` and
`timestamp` as `started_at`. Document this in the script's module docstring.

**Where it gets called**:
- `commands/tick.md` step 9 (drift check) — read latest tracking comment
  from the script output instead of grepping comments in prose.
- `commands/tick.md` step 10c.1 — `rework_count` from the script output.
- `commands/tick.md` step 10c.3 — `rework_context` from the script output.
- `commands/tick.md` step 11 — `attempt_count` from the script output.
- `commands/status.md` step 3 — replace the comment-grep prose with this.
- `commands/sweep.md` — does not need this (sweep operates on label state
  and `updatedAt`, not comment counting).

## P1.3 — `scripts/emit_tracking_comment.py`

**Purpose**: produce canonical tracking-comment bodies so JSON is guaranteed
well-formed.

**CLI**:
```
python scripts/emit_tracking_comment.py \
  --kind {state|gate|reconcile} \
  --state STATE \
  [--attempt N] \
  [--started-at ISO8601] \
  [--status {failed|waiting|rework|escalated}] \
  [--error TEXT] \
  [--rework-to STATE] \
  [--from STATE] \
  [--observed-linear-state TEXT] \
  [--expected-state TEXT] \
  [--reason TEXT]
```

Required args depend on `--kind`:
- `state`: `--state` and (if no `--status`) `--attempt`, `--started-at`.
- `gate`: `--state` and `--status`.
- `reconcile`: `--observed-linear-state`, `--expected-state`, `--reason`.

**Stdout**: the full comment body, e.g.:
```
<!-- cadence:state {"state": "plan", "attempt": 1, "started_at": "2026-05-12T14:23:01Z"} -->
**[Cadence]** Entering state: **plan** (attempt 1)
```

**Behaviour**:
- Build the JSON dict in Python, then `json.dumps(d, ensure_ascii=False,
  separators=(", ", ": "))` to get clean canonical output.
- For `--error`, collapse newlines to spaces, truncate to 400 chars, and
  pass through `json.dumps`'s native escaping. Do NOT manually escape.
- Visible markdown line is the shape documented in `tick.md` steps 12,
  10c.4, 16, 9, and the Failure path.

**Where it gets called**:
- `commands/tick.md` step 12 (attempt marker) — replace inline JSON with
  Bash invocation, capture stdout, pass to Linear comment-create.
- `commands/tick.md` step 10c.4 (rework gate comment) — same pattern.
- `commands/tick.md` step 16 (waiting gate comment) — same pattern.
- `commands/tick.md` step 9 (reconcile comment) — same pattern.
- `commands/tick.md` Failure path (failure record) — same pattern.

## P1 acceptance criteria

- [ ] All three scripts present under `scripts/`, with `_common.py`.
- [ ] Every script has a module docstring naming its caller(s) and
      the failure modes it eliminates.
- [ ] `python scripts/validate_workflow.py` against
      `templates/workflow.example.yaml` (with the template's placeholder
      `linear.project_slug` accepted as valid — the script does not
      validate Linear-side existence) exits 0.
- [ ] `python scripts/validate_workflow.py --workflow-path /dev/null`
      exits non-zero with a clear error to stderr.
- [ ] `commands/tick.md` step 3 contains exactly one Bash invocation of
      `validate_workflow.py` and no remaining prose-driven rule checks.
- [ ] `commands/tick.md` step 0 (dry-run) uses `validate_workflow.py --evidence`
      and no longer requires the LLM to compose per-rule evidence by hand.
- [ ] `commands/tick.md` steps 9, 10c.1, 10c.3, 11 each contain a Bash
      invocation of `parse_comments.py` and no remaining prose counting.
- [ ] `commands/tick.md` steps 9, 10c.4, 12, 16 and the Failure path each
      contain a Bash invocation of `emit_tracking_comment.py` and no
      inline JSON-in-prose templates.
- [ ] `commands/sweep.md` and `commands/status.md` use `validate_workflow.py`
      and (status only) `parse_comments.py`.
- [ ] README.md "Required permissions" Bootstrap table has a line added
      noting that the bootstrap also invokes Python via Bash to run the
      three helper scripts. The `Bash` permission already covers this; the
      doc update is informational only.
- [ ] CHANGELOG.md has an entry under `## [Unreleased]` describing P1.
- [ ] Manual smoke: run `/cadence:tick dry-run` against
      `templates/workflow.example.yaml` (after `/cadence:init` into a
      throwaway repo, edit `team` and `project_slug` to placeholder values).
      Confirm the report shows validation evidence sourced from the script.

## P1 commit guidance

- One commit per script, or one commit for `scripts/` + one for the
  command edits. Either is fine. Commits should not bundle P1 + P2.
- Use conventional commit messages (`feat:`, `refactor:`, `docs:`). No
  `Co-Authored-By: Claude` trailers per repo convention.

---

# Phase 2 — Hooks

**Prerequisite**: P1 has landed.

**Outcome**: three hooks fire in both `/schedule` and `/loop` modes,
scaffolded into the consumer's `.claude/settings.json` by `/cadence:init`.
Each hook script lives in the consumer's `.claude/hooks/` directory,
copied from the plugin's `templates/hooks/` on init. Scope is implicit
(the files only exist in repos that ran `/cadence:init`).

## P2.0 — Facts to build against

The docs research has confirmed everything P2 needs. Recording inline so
the implementing session doesn't have to re-derive:

1. **Hook activation in `/schedule`**: hooks in the consumer repo's
   `.claude/settings.json` fire in cloud routines because the routine
   clones the repo and runs from inside it. Per
   [claude-code-on-the-web](https://code.claude.com/docs/en/claude-code-on-the-web.md):
   > "Your repo's `.claude/settings.json` hooks → Yes → Part of the clone"
   >
   > "user-level settings don't carry over to cloud sessions. In the
   > cloud, only hooks committed to the repo run."

   This is the documented activation path. Plugin-shipped
   `hooks/hooks.json` cloud-routine behaviour is **not addressed by the
   current docs** (neither [routines](https://code.claude.com/docs/en/routines.md)
   nor [plugins-reference](https://code.claude.com/docs/en/plugins-reference.md)
   mention it either way). This plan deliberately uses the documented
   path instead of betting on plugin-hook activation that might or might
   not work in the cloud.

2. **Settings.json hooks block shape** ([hooks](https://code.claude.com/docs/en/hooks.md)):
   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "tool-name-pattern",
           "hooks": [{ "type": "command", "command": "..." }]
         }
       ],
       "UserPromptSubmit": [
         { "hooks": [{ "type": "command", "command": "..." }] }
       ],
       "PostToolUse": [
         {
           "matcher": "tool-name-pattern",
           "hooks": [{ "type": "command", "command": "..." }]
         }
       ]
     }
   }
   ```
   Each `command` is a shell string. Use `$CLAUDE_PROJECT_DIR` to
   reference paths relative to the consumer repo root (e.g.
   `"command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validate_tracking_json.py"`).
   Do NOT use `$CLAUDE_PLUGIN_ROOT` — it's documented for slash commands
   but not for hooks, and the scripts live in the consumer repo anyway.

3. **Stdin payload shapes** ([hooks](https://code.claude.com/docs/en/hooks.md)):
   - `PreToolUse`: `{"tool_name": "...", "tool_input": {...}, "tool_use_id": "..."}`
   - `UserPromptSubmit`: `{"prompt": "..."}`
   - `PostToolUse`: includes the tool result; the implementing session
     should confirm the exact key against the version of the docs at
     implementation time.

4. **MCP server availability**: not directly relevant to P2 (the hooks
   don't add new MCP servers), but consumers will need either an
   account-level claude.ai connector or a committed `.mcp.json` to reach
   Linear from a routine. Documented in [README.md](./README.md) Mode A
   setup.

## P2.1 — `templates/hooks/validate_tracking_json.py`

**Purpose**: catch malformed JSON in `cadence:state` / `cadence:gate` /
`cadence:reconcile` tracking comments before they reach Linear. Highest-
value hook of the three because it catches a failure mode P1's scripts
can't cover — an LLM that bypasses `emit_tracking_comment.py` and
hand-writes a Linear comment.

**Lives at**: `templates/hooks/validate_tracking_json.py` in the plugin;
copied to `.claude/hooks/validate_tracking_json.py` in the consumer by
`/cadence:init`. Settings.json `command` entry:
`python "$CLAUDE_PROJECT_DIR"/.claude/hooks/validate_tracking_json.py`.

**Event**: `PreToolUse`.

**Matcher**: tool name matching any Linear comment-create tool. The set of
known names (document at the top of the script):
```
mcp__linear__create_comment
mcp__claude_ai_Linear__save_comment
save_comment
create_comment
```
The script should match by name; if a vendor uses a different name, the
hook simply doesn't fire (fail-open, since the worst case is "Cadence is
no harder to operate than it was before this hook existed").

**Behaviour**:
1. Scope guard: bail with exit 0 if `.claude/workflow.yaml` does not exist.
2. Read tool input JSON from stdin (per the format confirmed in P2.0).
3. Extract the comment `body` field.
4. If the body does not start with `<!-- cadence:` or `<!-- stokowski:`,
   exit 0 (not a tracking comment; not our problem).
5. Regex-extract the JSON block between the first `{` and the matching `}`.
   `json.loads` it.
6. On success: exit 0 (allow).
7. On JSON parse failure: print to stderr:
   ```
   Cadence hook: tracking comment JSON failed validation.
   Comment kind: <prefix>
   Parser error: <exception message>
   First 200 chars of body: <truncated body>
   ```
   Exit 2 (block).

**No false-positive risk**: a non-Cadence comment never starts with
`<!-- cadence:` / `<!-- stokowski:`, so step 4 short-circuits cleanly.

## P2.2 — `templates/hooks/validate_workflow_on_prompt.py`

**Purpose**: validate `.claude/workflow.yaml` before `/cadence:tick` even
starts, so the live path doesn't waste a fire on a broken config.

**Lives at**: `templates/hooks/validate_workflow_on_prompt.py` in the
plugin; copied to `.claude/hooks/validate_workflow_on_prompt.py` in the
consumer by `/cadence:init`. Settings.json `command` entry:
`python "$CLAUDE_PROJECT_DIR"/.claude/hooks/validate_workflow_on_prompt.py`.

**Relationship to P1.1**: still redundant with P1.1's in-tick check, but
both now fire in `/schedule` and `/loop`. The hook catches the bad config
half a second earlier with a clearer message before the tick prose even
starts. Cheap value-add, not load-bearing.

**Note on the script invocation**: this hook needs to call
`scripts/validate_workflow.py` from P1.1. Since the helper scripts live in
the plugin repo (not the consumer), the hook needs to find them. Resolve
this with either:

- (a) Pass the plugin root path via an environment variable set in
  `/cadence:init`'s generated settings.json — e.g. the settings.json
  command becomes:
  `CADENCE_PLUGIN_ROOT="..." python "$CLAUDE_PROJECT_DIR"/.claude/hooks/validate_workflow_on_prompt.py`
  with the value baked in at init time from `${CLAUDE_PLUGIN_ROOT}`.
- (b) Copy `validate_workflow.py` (and `_common.py`) into `.claude/hooks/`
  too, so the hook can call its sibling without leaving the consumer repo.

**Recommendation: (b).** Self-contained, no plugin-path resolution at hook
runtime, survives plugin reinstalls. Costs ~200 lines of duplicated Python
in the consumer repo. The duplication is acceptable because the consumer
files are scaffolded — they get re-copied on `/cadence:init --force`.

If (b) is chosen, the consumer-repo layout becomes:

```
.claude/
  hooks/
    validate_tracking_json.py
    validate_workflow_on_prompt.py
    audit_linear_writes.py
    validate_workflow.py     ← copy of scripts/validate_workflow.py
    _common.py               ← copy of scripts/_common.py
```

And `/cadence:init` is responsible for keeping the consumer's copies in
sync with the plugin's on each (re-)init.

**Event**: `UserPromptSubmit`.

**Matcher**: no tool matcher (UserPromptSubmit fires on every prompt; the
script itself matches the prompt content).

**Behaviour**:
1. Scope guard: bail with exit 0 if `.claude/workflow.yaml` does not exist.
2. Read prompt text from stdin (per the format confirmed in P2.0).
3. Strip leading/trailing whitespace. If the prompt does not start with
   `/cadence:tick`, exit 0.
4. Run `scripts/validate_workflow.py` (via `subprocess.run`).
5. On exit 0: exit 0 (allow the prompt to proceed).
6. On non-zero: print to stderr:
   ```
   Cadence: workflow.yaml validation failed; refusing to start tick.

   <stderr from validate_workflow.py>

   Fix .claude/workflow.yaml and re-run /cadence:tick.
   ```
   Exit 2 (block the prompt).

**Effect on `commands/tick.md`**: step 3 still calls `validate_workflow.py`
(P1.1); the hook is a belt-and-braces second invocation that catches bad
configs at the prompt boundary. Do not remove the in-tick check — `/loop`
operators may set `validate_workflow.py` aside accidentally and the in-tick
check is still load-bearing for `/schedule` mode if the hook doesn't fire
there. **If P2.0 confirmed hooks don't fire in `/schedule`, this hook is
`/loop`-only redundancy — still useful, lower priority.**

## P2.3 — `templates/hooks/audit_linear_writes.py`

**Purpose**: append a structured log line to `.cadence/audit.log` for every
Linear write call, so operators can reconstruct what a fire actually did
when something goes wrong. Most valuable in `/schedule` mode, where there's
no live terminal to watch.

**Lives at**: `templates/hooks/audit_linear_writes.py` in the plugin;
copied to `.claude/hooks/audit_linear_writes.py` in the consumer by
`/cadence:init`. Settings.json `command` entry:
`python "$CLAUDE_PROJECT_DIR"/.claude/hooks/audit_linear_writes.py`.

**Note on `.cadence/` directory in cloud sessions**: cloud routines run on
a clone of the consumer repo. The `.cadence/` directory will be created
fresh each fire and discarded with the session, so the audit log lives
only for the duration of one fire. Useful for debugging that fire in the
session viewer at claude.ai/code/sessions, but NOT persistent across
fires. If durable audit history matters, the right home is a Linear
comment (which P1's `emit_tracking_comment.py` could be extended to emit
per fire — out of scope for this plan).

In `/loop` mode the audit log is persistent in the local working tree
because the same tree is reused across fires.

**Event**: `PostToolUse`.

**Matcher**: tool name matching any Linear write tool. Known names to match
(document at the top):
```
mcp__linear__create_comment
mcp__linear__update_issue
mcp__linear__add_label
mcp__linear__remove_label
mcp__claude_ai_Linear__save_comment
mcp__claude_ai_Linear__save_issue
save_comment
save_issue
update_issue
add_label
remove_label
create_attachment
```

**Behaviour**:
1. Scope guard: bail with exit 0 if `.claude/workflow.yaml` does not exist.
2. Read tool result JSON from stdin (per the format confirmed in P2.0).
3. Compute current UTC timestamp.
4. Compose a single audit line — one JSON object per line (JSONL), with
   keys: `ts`, `tool`, `issue_id` (best-effort extraction from tool input),
   `success` (bool, derived from the tool result's success indicator), and
   `summary` (a short string — for comments, the first 80 chars of the
   body; for state moves, `state: <before> → <after>` if derivable;
   otherwise empty).
5. `mkdir -p .cadence/` (the `.cadence/` directory is consumer-local;
   ensure `.gitignore` covers `.cadence/audit.log` — add this to the
   `/cadence:init` template if it's not already there).
6. Append the line. Exit 0 always (audit failure must never block a Linear
   write).

**`.gitignore` change** (in `/cadence:init` scaffolding): add a snippet
to `.gitignore` so consumers don't accidentally commit their audit log.
Update `commands/init.md` step 4 to append `.cadence/` to `.gitignore`
(creating it if absent), or to print a one-line instruction telling the
operator to do so. The simpler path is to have the hook itself create
`.cadence/.gitignore` containing `*` on first write — no consumer action
required. Prefer that approach.

## P2.4 — `/cadence:init` changes

`commands/init.md` needs to grow three new responsibilities. Update it in
place; do not fork it.

### Copy hook scripts

Step 4 of `init.md` (template copy) gains five new rows:

| Source                                                                | Destination                                |
|-----------------------------------------------------------------------|--------------------------------------------|
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/validate_tracking_json.py`     | `.claude/hooks/validate_tracking_json.py`     |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/validate_workflow_on_prompt.py`| `.claude/hooks/validate_workflow_on_prompt.py`|
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/audit_linear_writes.py`        | `.claude/hooks/audit_linear_writes.py`        |
| `${CLAUDE_PLUGIN_ROOT}/scripts/validate_workflow.py`                  | `.claude/hooks/validate_workflow.py`          |
| `${CLAUDE_PLUGIN_ROOT}/scripts/_common.py`                            | `.claude/hooks/_common.py`                    |

Step 3 (create directories) gains `.claude/hooks/` to the list.

### Merge into `.claude/settings.json`

After copying hooks, `init.md` must update `.claude/settings.json`. The
merge rules:

1. If `.claude/settings.json` does NOT exist:
   - Create it with `{"hooks": <cadence-block>}` from
     `templates/settings.example.json`.
2. If it exists and has no `"hooks"` key:
   - Add the full cadence hooks block.
3. If it exists with a `"hooks"` key:
   - For each event (`PreToolUse`, `UserPromptSubmit`, `PostToolUse`):
     - Look for existing entries whose `command` contains
       `/.claude/hooks/` AND whose script name matches one of the three
       Cadence hook scripts.
     - If found, replace (idempotent re-init).
     - If not found, append.
4. On `--force`: same merge logic; existing Cadence entries are replaced.
   Non-Cadence hook entries are left alone.

The implementing session should write a small Python helper inside
`init.md` (invoked via Bash) that does the merge:
`${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_hooks.py --settings-path
.claude/settings.json --template-path
${CLAUDE_PLUGIN_ROOT}/templates/settings.example.json`. This is a fourth
script that lives only in the plugin (not scaffolded to the consumer).

### `templates/settings.example.json` — the canonical block

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__linear__create_comment|mcp__claude_ai_Linear__save_comment|save_comment|create_comment",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validate_tracking_json.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validate_workflow_on_prompt.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "mcp__linear__create_comment|mcp__linear__update_issue|mcp__linear__add_label|mcp__linear__remove_label|mcp__claude_ai_Linear__save_comment|mcp__claude_ai_Linear__save_issue|save_comment|save_issue|update_issue|add_label|remove_label",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/audit_linear_writes.py"
          }
        ]
      }
    ]
  }
}
```

The implementing session should verify the matcher-as-regex semantics
against current Claude Code hooks docs at implementation time. If matchers
are exact-match only (not regex), each event's matcher entry needs to be
split into multiple entries — one per tool name — sharing the same `hooks`
list.

### Bump version

`.claude-plugin/plugin.json`: bump to `0.2.0`. No hook declarations in
the manifest (the plan no longer ships hooks via plugin metadata).

## P2 acceptance criteria

- [ ] All three hook scripts present under `templates/hooks/`.
- [ ] `templates/settings.example.json` present with the canonical hooks
      block.
- [ ] `scripts/merge_settings_hooks.py` present (plugin-only, not
      scaffolded to consumers).
- [ ] `commands/init.md` updated: copies the five new files into
      `.claude/hooks/`, runs the settings.json merge, and reports the
      changes in its "next steps" output.
- [ ] Re-running `/cadence:init` with no `--force` leaves an existing
      consumer's `.claude/hooks/` and `.claude/settings.json` alone (apart
      from the existing init refuse-to-overwrite behaviour). Re-running
      with `--force` re-copies the hook scripts and re-merges settings.json
      idempotently (no duplicate hook entries).
- [ ] Plugin manifest `.claude-plugin/plugin.json` version bumped to
      `0.2.0`. CI (`.github/workflows/validate.yml`) still passes.
- [ ] Smoke A — **fresh init in a new throwaway consumer repo**: run
      `/cadence:init`. Confirm `.claude/hooks/` exists with all five files
      and `.claude/settings.json` contains the three hook event blocks
      pointing at `$CLAUDE_PROJECT_DIR/.claude/hooks/*`.
- [ ] Smoke B — **idempotent re-init**: run `/cadence:init --force` again.
      Confirm no duplicate hook entries in settings.json, hook files
      timestamped newer.
- [ ] Smoke C — **`/loop` validation block**: corrupt
      `.claude/workflow.yaml` (e.g. duplicate `linear_state`). Run
      `/cadence:tick`. Confirm the `UserPromptSubmit` hook blocks the
      run with a clear error before any Linear call.
- [ ] Smoke D — **JSON validator block**: manually trigger a Linear
      comment-create whose body is `<!-- cadence:state {invalid json -->`
      (e.g. by hand-pasting into a Claude Code prompt during a `/loop`
      session). Confirm `validate_tracking_json.py` blocks with exit 2
      and a diagnostic to stderr.
- [ ] Smoke E — **audit log present**: after a successful `/cadence:tick`
      fire in `/loop` mode, confirm `.cadence/audit.log` exists with one
      JSON object per Linear write made during the fire.
- [ ] Smoke F — **`/schedule` activation**: create a throwaway routine
      running `/cadence:tick`, fire it once against a test Linear project,
      and inspect the resulting cloud session in
      claude.ai/code/sessions. Confirm at least one of the hooks ran
      (look for `validate_workflow_on_prompt.py` invocation in the session
      transcript, or for `.cadence/audit.log` entries written during the
      fire). If hooks do NOT fire in `/schedule` despite the docs
      suggesting they should, escalate to README troubleshooting and
      reconsider plugin-shipped activation as fallback.
- [ ] README.md "Required permissions" section updated to mention the
      audit log path (`.cadence/audit.log`) and the new hook scripts.
- [ ] CHANGELOG.md entry under `## [Unreleased]`.

## P2 commit guidance

- One commit per hook, or one for `hooks/` + one for the manifest update.
- Do not bundle P2 with P1 reverts or unrelated cleanup.

---

# Out of scope / future work

**Layer 3 — Regression harness.** A fake Linear MCP fixture + golden-file
comparison + CI step running on multiple Claude model versions. Build this
when (a) a real consumer beyond the author exists, OR (b) a prose change
in `tick.md` ships and silently breaks something in production. Not before.

**Cost telemetry.** A `--report-cost` flag on tick that estimates token
spend per fire. Useful if cost becomes a complaint; not pre-emptively
necessary.

**Multi-runner.** A `runner:` field on workflow states that lets a state
use Codex instead of Claude. Distinct architectural shift. Out of scope.

**Workflow visualization.** A `/cadence:graph` command that emits Mermaid
from `workflow.yaml`. The user already has `workflow-diagram.md` for the
current shape; a generator is sugar.

---

# Risks and mitigations

| Risk | Mitigation |
|---|---|
| Python isn't on the `/schedule` cloud image. | **Resolved.** Per [claude-code-on-the-web](https://code.claude.com/docs/en/claude-code-on-the-web.md) the Ubuntu 24.04 image ships Python 3.x + pip/poetry/uv. No fallback needed. |
| Hooks fire in non-Cadence repos. | **Resolved by construction.** Scaffolding into the consumer repo means the hook files only exist where Cadence is in use. Defense-in-depth scope guard at the top of each script handles the edge case of a consumer deleting `workflow.yaml` without uninstalling. |
| `/schedule` doesn't run repo-committed hooks. | **Documented to work** ([claude-code-on-the-web](https://code.claude.com/docs/en/claude-code-on-the-web.md): "Your repo's `.claude/settings.json` hooks → Yes → Part of the clone"). Verified by P2 Smoke F. If the documented behaviour doesn't hold in practice, the in-tick checks from P1 still cover the validation surface; only the JSON-validator and audit-log hooks lose value, and the plan's risk surface drops from "hooks broken" to "P2 value reduced." |
| Settings.json merge in `/cadence:init` corrupts a consumer's existing hooks. | The merge script identifies Cadence entries by their `command` containing `/.claude/hooks/<known-script-name>.py` and only replaces those. Non-Cadence entries are passed through untouched. Tested by P2 Smoke A + B with a settings.json containing pre-existing third-party hooks. |
| Hook matchers as regex vs exact-match. | Implementing session must verify against current docs at P2 implementation time. If exact-match only, split each `matcher` field into one entry per known tool name. Documented in the P2.4 section. |
| Plugin-script copies and the original drift out of sync. | `/cadence:init --force` is the documented upgrade path. The acceptance criteria require that re-init keeps the consumer's `.claude/hooks/` files current. |
| Linear MCP tool names vary by vendor. | Hooks match by name against a documented list and fail-open if no match. New vendor → add a line to the match list. |
| `parse_comments.py` is sensitive to MCP comment-shape variance. | The script tolerates camelCase vs snake_case keys and ignores unknown keys. Anything more exotic surfaces as `parse_errors` in the output for the bootstrap to act on. |
| LLM forgets to call a helper script and reverts to in-prose counting. | Hook B (validate_workflow_on_prompt) catches workflow-validation skips. Counting/emission skips are caught downstream by the tracking-JSON validator (Hook A) — malformed inline JSON gets blocked. Not a complete shield, but the bulk of the risk surface is covered. |

---

# Implementation order

The phases are independent in spirit but the order matters in practice:

1. **P1.1** (`validate_workflow.py`) — unlocks the dry-run cleanup and is
   the basis for the consumer-copy used by P2.2.
2. **P1.3** (`emit_tracking_comment.py`) — independent; ship next.
3. **P1.2** (`parse_comments.py`) — most surface area; ship last in P1.
4. **Land P1** as one PR (or split per-script PRs at the implementer's
   discretion). Run the manual smoke before merging.
5. **P2.1** (`validate_tracking_json.py`) — write the hook script.
6. **P2.3** (`audit_linear_writes.py`) — purely additive; write next.
7. **P2.2** (`validate_workflow_on_prompt.py`) — write last among hooks
   because it depends on P1.1's script being available for the consumer
   copy.
8. **P2.4** (`init.md` changes + `merge_settings_hooks.py` + template
   settings.json) — the wiring that makes the hooks active.
9. **Land P2** as one PR. Run all six smoke tests (A–F) before merging.
   Smoke F is the load-bearing one: confirm hooks fire in `/schedule`
   against a real (throwaway) routine. If they don't, escalate before
   merging.

Each phase should land independently. Do not bundle.
