# Cadence — Hardening Plan

Reduce the fragility of the prose-as-state-machine in `commands/tick.md`
without introducing a daemon. Push deterministic bookkeeping out of LLM
prose into small Python helper scripts, add Claude Code hooks as guard
rails around the parts that have to stay in prose, and tighten the
quality surfaces (ticket inputs, adversarial review, coordination caps)
that the harness — not the agent — should enforce.

This document is the single source of truth for the hardening work. It is
written so a fresh Claude session can implement any one of the six
phases (P1, P2, P3, P4, P5, P6) without rereading this entire conversation.

For the operational shape of the system this plan modifies, see
[README.md](./README.md). The canonical bootstrap prose lives in
[commands/tick.md](commands/tick.md). The principles that frame this
plan are captured in [GUIDEPOSTS.md](./GUIDEPOSTS.md).

---

## Design principles (charter)

Condensed from [GUIDEPOSTS.md](./GUIDEPOSTS.md). Every phase in this plan
exists to serve one or more of these. If a proposed change doesn't
clearly serve one, it doesn't belong here.

1. **Ticket quality is upstream of everything.** A vague ticket cannot be
   rescued by a good orchestrator. The ticket is the contract; treat
   acceptance criteria as the agent-facing test the work must pass.
   *(P3.)*
2. **Stage the work; don't lump it.** Investigate / implement / review
   are different concerns with different optimal models. Cadence already
   stages; P5 sharpens the staging by separating reviewer context and
   model class from the implementer's. *(P5.)*
3. **Adversarial review with no shared context.** Reviewers that share
   context with implementers are sycophantic. Cadence's subagent-per-
   stage architecture already gives a fresh context window per stage
   (verified against Stokowski's `session: fresh`); P5 leverages that by
   composing a minimal reviewer prompt and using a different model
   class. *(P5.)*
4. **Humans gate where machines can't judge.** The `review` gate and
   `max_rework` escalation already implement this. *(Preserve. P4
   changes the gate's decision-signaling mechanism — a label, not a
   column move — without weakening the gate itself.)*
5. **The tracker IS the workflow.** State, locks, attempt history,
   escalation flags — all in Linear. No external DB, no in-memory state,
   no parallel dashboard. *(Preserve. P6 stays inside this constraint:
   per-state concurrency caps read Linear, not a sidecar store.)*
6. **Mechanical guardrails beat agent discipline.** Anything that
   depends on the agent remembering will eventually fail under context
   pressure. Bake guardrails into helpers (P1) and hooks (P2).
7. **Build for forensic debugging.** Audit log (P2.3), dry-run mode
   (already shipped), caps and escalation (already shipped), drift
   reconciliation (already shipped). Failures must be reconstructable
   after the fact.
8. **The codebase teaches the agent.** Subagent templates, CLAUDE.md,
   and the ticket-template scaffold (P3) are first-class engineering
   artifacts.

**Anti-goals** (also from GUIDEPOSTS.md):
- More concurrent agents ≠ more software shipped. Per-state caps (P6)
  exist for coordination, not throughput.
- End-to-end autonomy is not the goal. The human gates are the quality
  mechanism, not friction to remove.
- No custom UI/dashboard. Linear and GitHub are the UIs.
- No expressive workflow DSL. `workflow.yaml` should fit on one screen.

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
- Changing the Linear soft-lock semantics. Caps (P6) coordinate at the
  Linear-column-count level; they do not replace or strengthen the
  per-issue soft lock.
- Multi-runner support (Codex / cross-provider review). Cadence stays
  Claude-only. The guideposts' "adversarial review from a different
  provider" leg is out of scope.
- Cost controls / token budgeting. Out of scope for this plan.
- Re-architecting the state machine. The workflow.yaml schema gains
  fields (`max_in_flight` in P6), loses two gate fields
  (`approved_linear_state` / `rework_linear_state` in P4), and the
  default template gains a state (`agent_review`) and a second gate
  (`plan_review`) in P5 — but the agent-vs-gate-vs-terminal taxonomy and
  the soft-lock / drift-reconciliation flow are preserved unchanged. The
  `plan_review` gate adds no new mechanism: it is an ordinary `type: gate`
  state handled by the same label-based dispatch P4 builds. P4 changes
  how a gate's verdict is signaled, not the gate's place in the flow.
- Custom UI / dashboard. Linear and GitHub remain the operator UIs.

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
    tick.md                ← edited in P1 (call scripts), P2 (init scaffolds hooks), P4 (gate-signaling: Steps 3/5/8/9/10), P5 (Step 13 adversarial variant), P6 (Step 5 cap enforcement)
    init.md                ← edited in P2 (copy hooks), P3 (copy ticket-template)
    sweep.md               ← edited in P1 (use parse-comments)
    status.md              ← edited in P1 (use parse-comments), P4 (gate verdict via label), P6 (Concurrency table)
    create-ticket.md       ← NEW (P3) — interactive ticket drafter
  scripts/                 ← NEW (P1) — invoked directly from command prose via Bash
    validate_workflow.py   ← edited in P4 (rule 1 narrowed; rule 8: legacy gate keys), P6 (rule 6: max_in_flight)
    parse_comments.py      ← edited in P5 (latest_implementer_summary)
    emit_tracking_comment.py
    _common.py             ← shared helpers (workflow.yaml-loader)
  templates/
    workflow.example.yaml  ← edited in P4 (gate: drop approved/rework columns, add decision labels), P5 (plan_review gate + agent_review state + adversarial_context flag, human_review rename), P6 (max_in_flight example)
    global-prompt.example.md  ← unchanged
    ticket-template.md     ← NEW (P3) — paste-into-Linear skeleton
    agents/
      planner.md           ← edited in P3 (ticket-quality gate)
      implementer.md       ← edited in P3 (AC marking)
      reviewer.md          ← edited in P3 (AC verification) and P5 (adversarial rewrite, model: opus)
    hooks/                 ← NEW (P2) — copied into consumer's .claude/hooks/ by /cadence:init
      validate_tracking_json.py
      validate_workflow_on_prompt.py
      audit_linear_writes.py
    settings.example.json  ← NEW (P2) — the hooks block /cadence:init merges into consumer's settings.json
  HARDENING-PLAN.md        ← this file
  GUIDEPOSTS.md             ← design-principles charter referenced by the preamble
```

Consumer repo after `/cadence:init` (with P2 + P3 landed):

```
<consumer-repo>/
  .claude/
    workflow.yaml          ← P0; gate loses approved/rework columns + gains decision labels (P4); may gain plan_review gate + agent_review state + adversarial_context flag (P5) and/or max_in_flight (P6)
    prompts/global.md      ← P0
    agents/{planner,implementer,reviewer}.md  ← P0; updated bodies after P3/P5
    ticket-template.md     ← P3 — copied from templates/ticket-template.md
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
  "workflow_linear_states": ["Todo", "Planning", "Implementing", "In Review", "Approved", "Needs Rework", "Done"],
  "pickup_state": "Todo",
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

# Phase 3 — Ticket-quality scaffolding

**Prerequisite**: P1 has landed. P2 is *not* required.

**Outcome**: Cadence treats the Linear ticket as a contract with
machine-checkable acceptance criteria. A new `/cadence:create-ticket`
command produces well-formed tickets on the happy path; the `planner`
subagent refuses to plan tickets without acceptance criteria as a
backstop. Acceptance-criteria status flows through the workflow:
implementer marks them, reviewer verifies them.

This phase serves Design Principle 1 ("ticket quality is upstream of
everything") and Principle 8 ("the codebase teaches the agent"). It is
the largest behavioural change in the plan but does not alter the state
machine or the soft-lock semantics.

## P3.0 — Acceptance-criteria contract (the format)

The contract is a Markdown checkbox list inside an `## Acceptance
Criteria` H2 block in the Linear issue description. Format:

```markdown
## Acceptance Criteria

- [ ] **AC-1** — Saving the form posts `{name, email}` to `POST /users`
      and renders the success toast on a 200 response.
- [ ] **AC-2** — Submitting an invalid email shows the inline error
      "Please enter a valid email." under the email field, and the
      submit button stays disabled.
- [ ] **AC-3** — Network failure displays "Couldn't save. Try again."
      and re-enables the submit button.
```

Rules:
- Exactly one `## Acceptance Criteria` H2 per ticket. Subagents look it
  up by that literal heading.
- Each item starts with `- [ ]` (unchecked) or `- [x]` (checked).
- Each item begins with a bold `**AC-N**` identifier. IDs are unique
  within the ticket and stable for the ticket's lifetime (never renumbered
  on edit).
- Each criterion is **independently verifiable** by reading the diff +
  running the test suite. Vague items ("works well", "is fast") are
  invalid; the planner should reject them.

The choice of Markdown checkboxes over a JSON block (Stokowski's choice)
is deliberate: Linear renders checkboxes as interactive UI, humans can
toggle them when triaging, and the format degrades gracefully if a
subagent forgets the convention. A future phase can add a JSON sidecar
if mechanical parsing of complex AC trees becomes necessary.

## P3.1 — `templates/ticket-template.md`

**Purpose**: a paste-able Markdown skeleton operators copy into the
Linear "Description" field for new tickets. Also serves as the document
the `planner` subagent points humans at when refusing an under-specified
ticket.

**Lives at**: `templates/ticket-template.md` in the plugin; copied to
`.claude/ticket-template.md` in the consumer by `/cadence:init` (new
step).

**Content** (the full file):

```markdown
<!--
  Cadence ticket template. Copy-paste into the Linear "Description"
  field, then fill in. Cadence's planner subagent refuses to plan
  tickets that don't have the `## Acceptance Criteria` block below
  with at least one independently-verifiable item.
-->

## Context

One or two paragraphs. What is the user-facing or system-level problem?
What is the current behaviour? Why does it need to change?

## Acceptance Criteria

<!-- Each item must be independently verifiable from the diff + tests.
     Vague items ("works well", "is fast") are not acceptable. -->

- [ ] **AC-1** — _Describe a specific, testable behaviour._
- [ ] **AC-2** — _Another._

## Out of scope

Anything the implementer might be tempted to do but shouldn't, in this
ticket. Reduces scope creep at review time.

## Notes / pointers

Links to related issues, screenshots, log excerpts, prior art. Optional.
```

## P3.2 — `commands/create-ticket.md`

**Purpose**: an interactive command an operator runs in their local
Claude Code session to draft a Cadence-shaped ticket. The command does
NOT post to Linear directly; it produces a Markdown blob the operator
copy-pastes into Linear's "New issue" form. This keeps the command
mode-agnostic (no Linear MCP required in the local session) and avoids
duplicating Linear's title/assignee/label UI.

**Front-matter** (mirrors `/cadence:tick`):

```yaml
---
description: Drafts a Cadence-shaped Linear ticket (title + body) interactively. Produces Markdown the operator pastes into Linear's New Issue form. No Linear writes.
argument-hint: "<one-line summary or '-' for interactive>"
disable-model-invocation: true
---
```

**Behaviour** (the prose to put in the command body — implementing
session writes this with exactly the level of step-by-step detail
`/cadence:tick` uses):

1. Read `.claude/ticket-template.md`. If absent, print an error pointing
   at `/cadence:init` and exit.
2. If `$ARGUMENTS` is non-empty and not `-`, treat it as the one-line
   summary; otherwise ask the operator: "One-line summary?".
3. Walk the operator through filling each section of the template:
   - **Context**: ask "What is the current behaviour? What needs to
     change and why?" Echo back the operator's answer rephrased into a
     short paragraph; ask for confirmation before moving on.
   - **Acceptance Criteria**: ask "What are the independently verifiable
     outcomes? Give one per line." For each line, validate it against
     these rules and surface any failures back to the operator before
     continuing:
       - Not empty after trimming.
       - Not a vague platitude. Heuristic: reject items whose only
         testable verbs are `work`, `be`, `function`, `handle`, `feel`,
         `look`, with no concrete subject or object. If unsure, ask.
       - Specifies *what* changes and *how it can be checked* (a UI
         outcome, an API response, a log line, a test assertion).
   - **Out of scope**: ask "Anything the implementer should NOT touch in
     this ticket?" Accept "(none)" as a valid answer.
   - **Notes / pointers**: optional; ask "Any links or prior art?"
4. Render the full ticket body by substituting the operator's answers
   into the template, with AC items numbered AC-1, AC-2, ... in order
   given.
5. Print a final block:

   ```
   --- Cadence ticket draft ---

   Title: <one-line summary>

   Description:

   <rendered Markdown body>

   --- End ---

   Paste the title into Linear's "Title" field and the description into
   the "Description" field. After creating, the issue is eligible for
   pickup once it lands in the workflow's pickup_state column.
   ```

6. Exit. Do **not** touch Linear; do **not** invoke any subagent.

**Why interactive prose rather than a script**: Cadence's design call is
"prose where prose serves quality, scripts where determinism serves
quality". Ticket-drafting is judgment-heavy (deciding whether an AC is
specific enough is exactly the work an LLM should do); doing it in
prose lets the operator iterate naturally and lets future model
upgrades raise the quality bar without code changes.

## P3.3 — Planner subagent enforcement

Update `templates/agents/planner.md` so the planner refuses to plan a
ticket without acceptance criteria. This is the backstop for tickets
that bypass `/cadence:create-ticket` (created directly in Linear's UI,
or imported from another tracker).

**Edit**: insert a new section after "## How to investigate" titled
"## Ticket-quality gate". Verbatim content:

```markdown
## Ticket-quality gate

Before producing a plan, verify the ticket meets Cadence's quality bar:

1. The Lifecycle Context block's **Description** must contain a literal
   `## Acceptance Criteria` H2.
2. Under that heading, there must be at least one Markdown checkbox item
   starting with `- [ ]` or `- [x]` and containing a bold `**AC-N**` ID
   somewhere in the line.
3. Each AC item must be independently verifiable from the diff and the
   test suite. If an item is vague ("works well", "is fast", "handles
   errors gracefully" with no specifics), treat it as failing.

If any check fails, **do not produce a plan**. Instead, return this
summary verbatim (substituting the missing/failing items):

    ## Cannot plan — ticket missing acceptance criteria

    This ticket cannot be planned because:

    - <bullet per failing rule>

    A Cadence-compatible ticket needs an `## Acceptance Criteria`
    section with at least one `- [ ] **AC-N** — <specific outcome>`
    item. See `.claude/ticket-template.md` for the expected shape, or
    run `/cadence:create-ticket` locally to draft a fresh one.

The Cadence bootstrap will post that summary as a Linear comment, mark
this attempt as failed, and (per `max_attempts_per_issue`) eventually
escalate with the `cadence-needs-human` label so a human rewrites the
ticket.
```

**No tick.md changes required**: the planner's "cannot plan" return
flows through the normal subagent-result-posting path (Step 15). The
attempt counts toward `max_attempts_per_issue`, so a permanently
malformed ticket will land in `cadence-needs-human` after the configured
number of fires — exactly the behaviour we want.

## P3.4 — Implementer & reviewer changes (verify AC)

Implementer and reviewer subagents must read the AC list, address each,
and surface their status. The visible side-effect is that the
implementer's return summary explicitly enumerates AC verification.

**Edit `templates/agents/implementer.md`** (file already exists — locate
the section that describes the return-summary shape and add this block):

```markdown
### Acceptance criteria

Before returning, walk the ticket's `## Acceptance Criteria` block. For
each item, include in your summary:

- [x] **AC-N** — <criterion text>
  - **Verified by:** <test file:line, manual smoke step, or "covered by
    existing test <path>">
- [ ] **AC-N** — <criterion text>
  - **Not addressed because:** <reason — out of scope, blocked by
    another ticket, etc.>

If you cannot mark every AC `[x]`, say so prominently at the top of your
summary. The reviewer will check this list against the diff.
```

**Edit `templates/agents/reviewer.md`** — add to the "How to review"
section, as a new numbered step before the existing step about reading
files:

```markdown
0. Locate the ticket's `## Acceptance Criteria` block in the Lifecycle
   Context. For each AC item, verify the implementer's claim against the
   actual diff. An AC marked `[x]` whose verification artefact does not
   exist (the cited test doesn't assert what's claimed, or the manual
   smoke can't be reproduced) is a **blocking** finding.
```

## P3.5 — `/cadence:init` changes

Update `commands/init.md`:

1. **Copy ticket-template.md.** Add one row to the template-copy table:

   | Source | Destination |
   |---|---|
   | `${CLAUDE_PLUGIN_ROOT}/templates/ticket-template.md` | `.claude/ticket-template.md` |

2. **Post-init guidance.** Append to the "Next steps" output:

   ```
   To create well-formed tickets, run `/cadence:create-ticket` in your
   local Claude Code session and paste the output into Linear's New
   Issue form. The planner subagent will refuse tickets that lack an
   `## Acceptance Criteria` block.
   ```

3. **Mode-agnostic.** No `settings.json` changes (P3 adds no hooks). No
   `.gitignore` changes.

## P3 acceptance criteria

- [ ] `templates/ticket-template.md` present, matching the content
      in P3.1 above byte-for-byte (it ships as documentation, so drift
      from the plan is itself a bug).
- [ ] `commands/create-ticket.md` present, with the front-matter and
      step-by-step prose from P3.2.
- [ ] `templates/agents/planner.md` contains the "## Ticket-quality
      gate" section verbatim from P3.3.
- [ ] `templates/agents/implementer.md` and
      `templates/agents/reviewer.md` updated per P3.4.
- [ ] `commands/init.md` copies `ticket-template.md` and updates its
      "Next steps" output.
- [ ] `.claude-plugin/plugin.json` version bumped (`0.3.0` if P2 has
      already shipped `0.2.0`; otherwise `0.2.0`).
- [ ] CHANGELOG.md entry under `## [Unreleased]`.
- [ ] README.md "Ticket quality" subsection added under Mode A setup,
      naming the ticket template path and `/cadence:create-ticket`.
- [ ] Smoke G — **planner refusal**: create a Linear issue whose
      description is just a paragraph (no `## Acceptance Criteria`
      block). Run `/cadence:tick`. Confirm the planner returns the
      "Cannot plan — ticket missing acceptance criteria" summary, the
      bootstrap posts it as a comment, and the attempt counts toward
      `max_attempts_per_issue`.
- [ ] Smoke H — **happy path with AC**: create a Linear issue using the
      template, with two ACs. Run `/cadence:tick` through the plan
      state. Confirm the planner produces a plan that references the
      AC IDs.
- [ ] Smoke I — **`/cadence:create-ticket` interactive flow**: run the
      command in a local Claude Code session. Confirm it walks through
      every template section, validates the ACs against the heuristics,
      and emits a paste-ready block. Do not actually create a Linear
      issue.

## P3 commit guidance

- One commit per file (ticket template, create-ticket command, planner
  update, implementer/reviewer updates, init update).
- Do not bundle P3 with P1, P2, P4, P5, or P6 work.

---

# Phase 4 — Label-based gate signaling

**Prerequisite**: P1 has landed (this phase amends `validate_workflow.py`
from P1.1). P2/P3 are independent.

**Outcome**: a human's approve/rework verdict at a `gate` state is
signaled with a **label**, not a column move. The gate keeps a single
Linear column (its `linear_state`, the waiting queue). Two new
config-defined labels — `cadence_approve` and `cadence_rework` — carry
the verdict. The `approved_linear_state` and `rework_linear_state`
fields are removed from the gate schema.

This collapses the per-gate Linear-column cost from **three columns to
one**. A workflow with three gates drops from nine gate columns to
three columns plus two globally-shared labels. It also keeps each
gate's waiting column a clean review queue instead of having cards hop
to an "Approved" column and back.

## P4.0 — Why labels, not columns

The bootstrap's core architecture is **Linear column = workflow-state
pointer** ([commands/tick.md](commands/tick.md) Steps 8–10 derive
"where is this issue" purely from its column). A gate today needs three
columns because the column has to encode three distinguishable facts:
*undecided*, *approved*, *needs rework*. The column move is
simultaneously the signal and the durable record — it survives across
fires with no extra mechanism. That is why it was built this way; the
cost is column proliferation.

Labels are the right replacement because:

1. **Cadence already uses labels as out-of-band control signals.** The
   `cadence_active` soft lock and `cadence_needs_human` escalation are
   both labels. A gate-verdict label is the same pattern — operators
   already learn "Cadence speaks label."
2. **Two labels total, not two per gate.** The column the issue sits in
   already identifies *which* gate; the label only needs to encode the
   *verdict*. So the label vocabulary does not grow with the number of
   gates.
3. **The waiting column stays a clean queue.** `In Review` remains the
   review queue; nothing hops out of it and back.

The durability property is preserved: a label persists on the issue
across fires exactly as a column does, so a verdict left between fires
is still there on the next pickup.

Rejected alternatives (settled in conversation, do not revisit):
- **Reuse the target states' columns** (drop the gate's extra columns;
  human drags the card straight to the `on_approve` / `on_rework`
  target's column). Zero new vocabulary, but it makes the human perform
  a workflow-internal move (e.g. dragging to "Implementing" to mean
  "rework"), which reads as muddier than a labelled verdict, and loses
  the explicit review-verdict semantics.
- **Comment command** (`/approve`, `/rework` in a comment). Most
  flexible, but comment parsing is fragile (ordering, authorship,
  legacy prefixes) and off-grain — Cadence uses labels, not chat-ops,
  for control signals.

## P4.1 — `templates/workflow.example.yaml` — gate block + label section

Two edits to the example template.

**Edit the `label:` section** — add the two verdict labels alongside the
existing control labels:

```yaml
label:
  # Soft lock label. Added at the start of every fire and removed at the
  # end. Stale ones are cleared by /cadence:sweep.
  cadence_active: "cadence-active"

  # Set when an issue exceeds max_attempts_per_issue or max_rework.
  # Excludes the issue from pickup until a human removes the label.
  cadence_needs_human: "cadence-needs-human"

  # Gate verdict labels. A human adds one of these to an issue sitting
  # in a gate's waiting column to signal their decision. The bootstrap
  # reads the label, acts on it, and removes it. Two labels cover every
  # gate in the workflow — the gate's waiting column identifies which
  # gate; the label only carries the verdict.
  cadence_approve: "cadence-approve"
  cadence_rework: "cadence-rework"
```

**Edit the `review` gate block** — remove the two `*_linear_state`
fields. The gate now declares only its single waiting column:

```yaml
  # ----------- human review gate -----------
  review:
    type: gate
    # The single Linear column this gate uses — the waiting queue.
    # Must exist as a column on your Linear board and be unique across
    # the whole workflow config.
    linear_state: "In Review"

    # A human signals their verdict by adding label.cadence_approve or
    # label.cadence_rework to the issue while it sits in "In Review".
    on_approve: done                  # next state when cadence_approve is seen
    on_rework: implement              # next state when cadence_rework is seen

    # Optional. Max rework rounds before the bootstrap escalates with
    # cadence_needs_human. Omit for unlimited rework.
    max_rework: 2
```

Update the validation-rules comment at the top of the file: the
uniqueness rule no longer mentions `approved_linear_state` /
`rework_linear_state` (they no longer exist); it covers `linear_state`
values and `linear.pickup_state` only.

## P4.2 — `commands/tick.md` — gate-signaling prose

The gate columns are gone, so the bootstrap reads the verdict from
labels instead. Edits, by step:

**Step 3 — Validation rules.** Rule 1 (uniqueness) now collects only
`linear_state` values plus `linear.pickup_state` — drop the
`approved_linear_state` / `rework_linear_state` clauses. Add a new
prose rule pointing at `validate_workflow.py` rule 8 (legacy gate keys
rejected — see P4.3).

**Step 4 (dry-run report) / Step 0.** Rule 1's evidence block no longer
lists `approved_linear_state` / `rework_linear_state` bullets. The
script owns this; the prose just needs the stale field names removed.

**`workflowLinearStates` set construction** (the catalog used by Step 5's
query filter and Step 8's column→state mapping). Remove the "every
gate's `approved_linear_state` and `rework_linear_state`" lines. The set
is now: `linear.pickup_state` + every state's `linear_state`.

**Step 8 — Match the workflow state.** A gate now maps from exactly one
column (its `linear_state`). Delete the two "(for a gate)
`approved_linear_state` equals it" / "`rework_linear_state` equals it"
branches.

**Step 9 — Drift check.** Delete the "Special case — gate sitting in
approved/rework" bullet. A gate's issue is only ever in the gate's
single column now, so that drift case cannot arise.

**Step 10 — Gate handling.** This is the substantive rewrite. Replace
the "branch on which of the gate's three Linear columns the issue is
in" dispatch with a label check:

```markdown
If it **is** a gate, fetch the issue's current labels and branch:

### 10a — Neither verdict label present (waiting)

The human has not decided yet. Remove the `cadence_active` label and
exit. Do not invoke a subagent. Do not post any comment.

### 10b — `label.cadence_approve` is present

The human approved. Look up `<gate>.on_approve`; call it `approveTarget`.

1. Remove the `cadence_approve` label from the issue.
2. Move the issue to `approveTarget`'s `linear_state`.
3. If `approveTarget` is `type: terminal`: remove the `cadence_active`
   label and exit. No subagent invocation.
4. Otherwise: set the **target state** to `approveTarget` and continue
   at step 11.

### 10c — `label.cadence_rework` is present

The human is sending the work back. Look up `<gate>.on_rework`; call it
`reworkTarget`. `<gate>.max_rework` may or may not be defined.

1. Remove the `cadence_rework` label from the issue.
2. Count prior `cadence:gate` rework markers for this gate (unchanged —
   `parse_comments.py` `rework_count`, keyed on `--gate-name`).
3. Apply the `max_rework` escalation check (unchanged from the current
   step 10c.2).
4. Gather rework context (unchanged from the current step 10c.3).
5. Post the `cadence:gate` rework tracking comment (unchanged).
6. Move the issue to `reworkTarget`'s `linear_state` and continue at
   step 11.

### Both verdict labels present

Treat as **rework** (the safer verdict — it routes back for another
human pass rather than advancing). Remove both labels. Proceed as 10c.
```

**Step 12 (or wherever `next` is a gate).** When the bootstrap moves an
issue *into* a gate, it still posts the `cadence:gate` waiting marker
and moves the issue to the gate's `linear_state`. No change there — the
issue lands in the single waiting column and waits for a label.

## P4.3 — `scripts/validate_workflow.py` — rule changes

Two changes to the P1.1 validator:

**Amend Rule 1 (uniqueness).** Collect only every `linear_state` value
plus `linear.pickup_state` — drop `approved_linear_state` /
`rework_linear_state` from the collection. Update the `--evidence`
Rule 1 block accordingly.

**Add Rule 8 — legacy gate keys rejected.** For every `type: gate`
state, the keys `approved_linear_state` and `rework_linear_state` must
**not** be present. If either is found, fail (exit `2`) with a message
naming the state and pointing the operator at the CHANGELOG migration
note:

```
Rule 8 (legacy gate keys) FAILED:
  states.review.approved_linear_state is no longer supported (removed in P4).
  Gates now signal verdicts via the cadence_approve / cadence_rework labels.
  See CHANGELOG "Upgrading to label-based gates".
```

Add a Rule 8 block to the `--evidence` output in the same shape as the
other rules. (Rules 6 and 7 are defined by later phases — P6.2 and
P5.4a respectively; rule numbers are not ship-order.)

## P4.4 — Linear column scaffolding + docs

The gate's `approved_linear_state` / `rework_linear_state` columns are
gone. Update the consumer-facing docs:

**README.md** Mode A setup — the "Required Linear columns" list drops
`Approved` and `Needs Rework`:

```
Required Linear columns (default workflow):
- Todo (or whatever your pickup_state is)
- Planning
- Implementing
- In Review
- Done
```

And add a short "Gate labels" note next to the column list: the
operator must create the `cadence-approve` and `cadence-rework` labels
in Linear (same as they already create `cadence-active` /
`cadence-needs-human`), and reviewers approve/reject by adding one of
those labels to an issue in the gate's waiting column.

**MIGRATION.md** (and a CHANGELOG entry) — an "Upgrading to label-based
gates" section:
1. Create the `cadence-approve` and `cadence-rework` Linear labels.
2. In `.claude/workflow.yaml`, delete `approved_linear_state` and
   `rework_linear_state` from every `type: gate` state. Add the
   `cadence_approve` / `cadence_rework` entries to the `label:` section.
3. The `Approved` and `Needs Rework` Linear columns can be deleted (or
   left as foreign columns — Cadence will simply ignore them).
4. In-flight issues sitting in a former `Approved` / `Needs Rework`
   column when the upgrade lands must be handled once by hand: move
   them back to the gate's waiting column and apply the matching label,
   or move them straight to the target state.

## P4.5 — `commands/status.md`

`/cadence:status` summarizes issues by Linear column. With gates now on
a single column, the status output no longer needs the
`approved_linear_state` / `rework_linear_state` rows. Confirm at
implementation time that nothing in `status.md` enumerates the removed
fields; if it surfaces gate state, it should show the gate's single
column plus, optionally, a count of issues there carrying a
`cadence-approve` / `cadence-rework` label (a "verdicts waiting to be
processed on next fire" signal). Keep that addition minimal.

## P4 acceptance criteria

- [ ] `templates/workflow.example.yaml`: `label:` section has
      `cadence_approve` and `cadence_rework`; the `review` gate block
      has `linear_state`, `on_approve`, `on_rework`, `max_rework` only —
      no `approved_linear_state` / `rework_linear_state`. The
      validation-rules comment no longer names the removed fields.
- [ ] `scripts/validate_workflow.py`: Rule 1 collects only
      `linear_state` + `pickup_state`; Rule 8 rejects
      `approved_linear_state` / `rework_linear_state` on any gate;
      `--evidence` includes a Rule 8 block.
- [ ] `commands/tick.md`: Step 10 branches on `cadence_approve` /
      `cadence_rework` labels, not gate columns; the bootstrap removes
      the verdict label after acting; Steps 3, 8, 9 and the
      `workflowLinearStates` construction no longer reference
      `approved_linear_state` / `rework_linear_state`.
- [ ] `README.md` "Required Linear columns" list drops `Approved` and
      `Needs Rework`, and documents the two new gate labels.
- [ ] `MIGRATION.md` + `CHANGELOG.md` have an "Upgrading to label-based
      gates" section.
- [ ] `.claude-plugin/plugin.json` version bumped.
- [ ] Smoke J — **approve path**: `/cadence:init` into a throwaway
      consumer repo. Create the `cadence-approve` / `cadence-rework`
      labels. Push an issue to the `review` gate. Add `cadence-approve`.
      Run `/cadence:tick`. Confirm: the label is removed, the issue
      moves to `done`'s `linear_state`, and no `Approved` column is
      needed anywhere.
- [ ] Smoke K — **rework path + counter**: with an issue at the gate,
      add `cadence-rework`. Run `/cadence:tick`. Confirm: the label is
      removed, a `cadence:gate` rework tracking comment is posted, the
      issue routes to `on_rework`'s target, and a second rework round
      increments the count (and escalates at `max_rework`).
- [ ] Smoke L — **validator rejects legacy schema**: take a
      `workflow.yaml` whose gate still has `approved_linear_state`. Run
      `/cadence:tick`. Confirm the validation step exits with a Rule 8
      failure naming the state, with no Linear writes.

## P4 commit guidance

- One commit for the workflow template (gate block + label section).
- One commit for `validate_workflow.py` (Rule 1 amendment + Rule 8).
- One commit for `commands/tick.md` (Step 10 rewrite + Steps 3/8/9 +
  `workflowLinearStates`).
- One commit for the docs (README + MIGRATION + CHANGELOG).
- Do not bundle P4 with P1, P2, P3, P5, or P6.

---

# Phase 5 — Plan-review gate + adversarial review stage

**Prerequisite**: P1 has landed. P4 must have landed (both gate blocks in
P5.1 — `plan_review` and `human_review` — build on the label-based gate
shape from P4). P3 is recommended but not strictly required (P5 still
adds value with un-AC'd tickets; the reviewer just falls back to "does
the diff match the plan").

**Outcome**: the default workflow gains two review points, both mirroring
[workflow-diagram.md](./workflow-diagram.md):

1. A `plan_review` **human gate** between `plan` and `implement` — the
   planner's output is approved before implementation burns budget.
   `plan.next` becomes `plan_review`; the gate routes `on_approve` to
   `implement` and `on_rework` back to `plan`.
2. An automated `agent_review` **state** between `implement` and the
   final human gate (the former `review`, renamed `human_review` to match
   the diagram). It runs the (currently unwired) reviewer subagent with
   an adversarial prompt, a minimal Lifecycle Context (no implementer
   narrative carried forward), and an Opus-class model by default. The
   mechanism for "minimal context" is a new workflow.yaml field,
   `adversarial_context: true`, set on states that want it.

`plan_review` adds no new bootstrap or script logic — it is an ordinary
`type: gate` state, dispatched by the same label-based gate handling P4
builds (Step 10 of `tick.md`). The only P5 cost it carries is template
content, one Linear column, and docs. The agent-review machinery (P5.3–
P5.5) is the substantive engineering in this phase.

## P5.0 — Why an explicit agent state, not "fresh session" on the gate

Stokowski's `session: fresh` directive is a CLI-orchestrator concept:
its runner threads stages via `claude --resume <session_id>` by default;
`session: fresh` opts out so the reviewer starts a new headless session
with no prior turns. **Cadence does not need this.** Subagents invoked
via the Claude Code Task tool receive a fresh context window every
time — they see only their own system prompt and the user message the
bootstrap composes. This was confirmed by reading `orchestrator.py`
lines 919–930 and `runner.py`'s `build_claude_args` in Stokowski; the
flag literally controls whether `--resume <id>` is appended, nothing
more.

Source-of-truth quote, [GUIDEPOSTS.md](./GUIDEPOSTS.md) Principle 3:
> "A clean session with no prior thread, ideally a different model or
> provider, catches things the implementer missed."

The Cadence equivalent is therefore:
1. **A distinct workflow state** that runs the reviewer subagent (the
   fresh-context property is automatic — subagents always start fresh).
2. **A minimal Lifecycle Context** for that subagent: no implementer
   tracking-comment narrative, no plan summary, just ticket +
   acceptance criteria + PR pointer + rework context if any.
3. **A different model class** (`opus` for review, `sonnet` for
   implementation) configured per-subagent in the existing `.claude/
   agents/<name>.md` front-matter.

The "different provider" leg of the guidepost (Codex / another vendor)
is out of scope for Cadence and stays in the "future work" list.

## P5.0b — Why a plan-review gate

[GUIDEPOSTS.md](./GUIDEPOSTS.md) Principle 4 is explicit that the human
gate belongs in two places, not one:

> "gate after investigation (before implementation burns the budget),
> after implementation (before merge)"

The pre-P5 default template only has the *second* gate. A planner can
produce a confident, well-formatted plan that solves the wrong problem,
or solves it in a shape the operator would never endorse — and nothing
catches that until `human_review`, after `implement` and `agent_review`
have already spent tokens on it. The plan gate is the cheapest possible
intervention point: one human glance, before any code exists.

This is not a state-machine re-architecture (see Non-goals). `plan_review`
is an ordinary `type: gate` state. Once P4 has landed, `tick.md` Step 10
dispatches *any* gate from the `cadence_approve` / `cadence_rework`
labels — it does not enumerate gates by name. So adding a second gate to
the template requires **zero** changes to `tick.md`, `validate_workflow.py`,
or any helper script: the existing validation rules already check that
`plan.next`, `plan_review.on_approve`, and `plan_review.on_rework`
resolve, and that `plan_review.linear_state` is unique. The only cost is
template content, one new Linear column, and the docs/upgrade note.

The cost side, stated honestly: every ticket now stops once more for a
human. For Cadence's single-operator design target that is the intended
trade — the gates *are* the quality mechanism (Principle 4), not friction
to remove. An operator who wants the old behaviour can delete the
`plan_review:` block from their `.claude/workflow.yaml` and point
`plan.next` straight at `implement`; the validator accepts both shapes.

## P5.1 — `templates/workflow.example.yaml` — add `plan_review` gate, `agent_review` state, rename `review` → `human_review`

State names mirror [workflow-diagram.md](./workflow-diagram.md). The
full edited `states:` block:

```yaml
states:
  # ----------- planning -----------
  plan:
    type: agent
    subagent: planner
    linear_state: "Planning"
    next: plan_review                # ← changed from `implement`

  # ----------- plan review gate (human approves the plan before code) -----------
  plan_review:
    type: gate
    linear_state: "Plan Review"      # NEW Linear column (see P5.2)
    on_approve: implement
    on_rework: plan
    max_rework: 2

  # ----------- implementation -----------
  implement:
    type: agent
    subagent: implementer
    linear_state: "Implementing"
    next: agent_review               # ← changed from `review`

  # ----------- automated code review (adversarial, no implementer narrative) -----------
  agent_review:
    type: agent
    subagent: reviewer               # → .claude/agents/reviewer.md
    linear_state: "Reviewing"        # NEW Linear column (see P5.2)
    adversarial_context: true        # Step 13 strips implementer narrative from Lifecycle Context (see P5.4)
    next: human_review

  # ----------- human review gate -----------
  human_review:                      # ← renamed from `review` to match workflow-diagram.md
    type: gate
    linear_state: "In Review"        # single waiting column (gate signaling is label-based since P4)
    on_approve: done
    on_rework: implement
    max_rework: 2

  # ----------- terminal -----------
  done:
    type: terminal
    linear_state: "Done"
```

Both gate blocks (`plan_review` and `human_review`) carry the label-based
shape from P4 (single `linear_state`, no `approved_linear_state` /
`rework_linear_state`). This phase inserts `plan_review` after `plan`
(repointing `plan.next`), inserts `agent_review` after `implement`, and
renames the former `review` gate to `human_review`.

Also update the **comment** block above `states:` so the example
documents all six workflow states (`plan`, `plan_review`, `implement`,
`agent_review`, `human_review`, `done`). Keep the validation-rules comment
intact. The `adversarial_context` field is new; document it inline next
to the field with a one-line comment naming the behaviour and pointing
at the Step 13 reference (see the YAML above).

## P5.2 — Linear column scaffolding

P5 adds **two** new Linear columns: `plan_review.linear_state`
(`"Plan Review"`) and `agent_review.linear_state` (`"Reviewing"`). Both
must exist as distinct columns on the operator's Linear board. The human
gate's column (`"In Review"`) is unchanged — only the workflow state name
in `workflow.yaml` changed (`review` → `human_review`). Update
[README.md](./README.md) Mode A setup to add the new columns to the
required columns checklist:

```
Required Linear columns (default workflow):
- Todo (or whatever your pickup_state is)
- Planning
- Plan Review        ← NEW; add this before upgrading
- Implementing
- Reviewing          ← NEW; add this before upgrading
- In Review
- Done
```

The two gate-verdict labels from P4 (`cadence-approve` /
`cadence-rework`) are reused as-is for `plan_review` — no new labels are
needed; the column an issue sits in identifies *which* gate.

**Upgrade path for pre-P5 consumers** (document in CHANGELOG):
1. Add the `Plan Review` and `Reviewing` Linear columns.
2. In `.claude/workflow.yaml`:
   - Insert a `plan_review:` gate block (`type: gate`,
     `linear_state: "Plan Review"`, `on_approve: implement`,
     `on_rework: plan`, `max_rework: 2`) and change `plan.next` from
     `implement` to `plan_review`.
   - Rename `review:` → `human_review:`, update `implement.next` from
     `review` (or whatever it was) to `agent_review`, and insert the new
     `agent_review:` block above `human_review:` with
     `adversarial_context: true`.

No Linear column renames are required — `"In Review"` stays as the human
gate column. Consumers who want to keep the old single-gate flow can skip
the `plan_review:` block and leave `plan.next: implement`; the validator
accepts both.

## P5.3 — Reviewer subagent template: adversarial hardening

Rewrite the top half of `templates/agents/reviewer.md` to be explicit
about the adversarial stance and the minimal-context contract. Keep the
existing "What to return" / "Style" sections intact.

**Bump `model: sonnet` → `model: opus`** in the front matter. Keep
`tools: [Read, Grep, Glob, WebFetch, Bash]` — Bash is added so the
reviewer can run `git diff main...HEAD` and `gh pr view` against the
implementer's branch.

**Replace the "## Your role" and "## How to review" sections with**:

```markdown
## Your role

You are an **independent code reviewer with no prior context**. The
Cadence bootstrap has invoked you on a freshly-merged-to-branch
implementation. Your job is to find problems the implementer missed,
not to confirm their work.

Treat the implementer's prior Linear comments and plan summaries as
**unreliable narrative** — do not read them, and do not let them shape
your reading of the diff. Your inputs are:

1. The Lifecycle Context block at the top of this prompt (ticket
   title, description, acceptance criteria, PR URL, branch name).
2. The diff itself, read directly via `git diff <base>...<branch>`.
3. The repository on disk, for reading surrounding code.

You do NOT have approval authority — the `review` gate that follows
this state is a human's call. Your output is a findings comment that
sharpens that human's review.

## How to review

1. **Read the ticket, including `## Acceptance Criteria`.** These are
   the contract. Every blocking finding should tie back to either an
   AC violation, a plan-vs-diff scope mismatch, or a correctness /
   security defect not anticipated by either.

2. **Read the diff cold.** Run (via Bash):
   - `git fetch origin` — make sure the branch is up to date locally.
   - `git diff origin/<base-branch>...origin/<implementer-branch>` —
     where `<base-branch>` is the repo's default (typically `main`)
     and `<implementer-branch>` comes from the Lifecycle Context's
     "Branch" field.
   - If the PR URL is present and you have `gh` available,
     `gh pr view <url> --json files,additions,deletions` for a
     summary.
   Do not run any other git commands — no `git log`, no `git blame` of
   the implementer's commits, no inspection of the implementer's
   commit messages. Read the diff as if the author is anonymous.

3. **For each acceptance criterion, verify it in the diff.** An AC is
   verified only if you can point at a test assertion, a code path, or
   a runtime behaviour in the diff that establishes it. "The
   implementer said they covered AC-3" is not verification.

4. **Catch the things implementers miss.** Error paths, null/empty
   boundary conditions, race conditions, secret/credential handling,
   input validation, scope creep beyond the plan, missing tests for
   non-trivial branches.

5. **Be adversarial without being uncharitable.** Point at problems;
   don't speculate about motive. If a choice could be intentional, file
   it as a `[question]` rather than a `[blocking]`.
```

## P5.4 — Tick.md: minimal Lifecycle Context when `adversarial_context: true`

The current Step 13 composes the same Lifecycle Context block for
every subagent. P5 introduces one branch keyed on a single workflow
state field: `adversarial_context: true`. When set, the bootstrap omits
anything that smells like the implementer's narrative.

**Why a workflow.yaml flag rather than a subagent-side or convention-
based marker**: the workflow shape lives in `workflow.yaml`. Keeping
the toggle there means (a) one file holds the workflow contract,
(b) the bootstrap doesn't need to crack open a second file to decide
context shape, and (c) the rule is explicit to anyone reading the
workflow definition.

**Edit Step 13** (the file at [commands/tick.md](commands/tick.md):373).
Add the following clause after the "Always finish with the footer"
paragraph but before the "After the block, append two blank lines"
paragraph:

```markdown
**Adversarial-context variant**: if the target state's config has
`adversarial_context: true`, compose the Lifecycle Context block
differently:

- The **Description** section is the ticket description verbatim — same
  as the default.
- The **Acceptance Criteria** are guaranteed to be in the description
  (P3 makes this a planner-enforced contract); the subagent reads them
  directly out of the description text.
- **No "Plan summary" or implementer-narrative section is included.**
  Even if prior tracking comments contain a plan summary or
  implementation notes, do NOT lift them into the Lifecycle Context.
- The **Branch** line is replaced with two lines:
  - **Branch (under review):** the implementer's branch name (same
    derivation as the default).
  - **Base branch:** `main` unless the repo's default is something else
    (read from `gh repo view --json defaultBranchRef -q
    .defaultBranchRef.name` if available; otherwise default to `main`).
- **PR URL**, if discoverable from `parse_comments.py`'s
  `latest_implementer_summary.pr_url` field (see P5.5), is included as
  a separate **PR:** line. If not discoverable, omit the line (the
  subagent will fall back to `git diff`).
- The **Transitions** section reads:

  ```
  ### Transitions

  - On success → **<nextState>** (Linear: "<nextState's linear_state>")
  - Your output is a Markdown findings comment. The bootstrap will post
    it on the issue and move the issue to <nextState>.
  ```

- The "When Done" footer is unchanged.

The Rework Context section, if any, is **included** for adversarial-
context subagents (a rework run still needs the human's prior rework
reasoning); it is the only narrative-style content carried into the
context, and it comes from humans, not the implementer.
```

If `adversarial_context` is absent or false (the default for all
existing states), the bootstrap composes the original Lifecycle Context
unchanged — no behaviour change for non-review states or for older
consumer workflow.yamls that haven't opted in.

## P5.4a — `validate_workflow.py` rule 7

Add a seventh validation rule to `scripts/validate_workflow.py`
(P1.1 + P6.2's rule 6 pattern):

**Rule 7 — adversarial_context type and scope.**
- For every state with an `adversarial_context` key:
  - The value must be a boolean (`true` or `false`). Reject strings,
    integers, null.
  - The state's `type` must be `agent`. Reject on `gate` or `terminal`
    (the flag controls how the subagent is invoked; gates and terminals
    don't invoke subagents).

Update the `--evidence` output to include a Rule 7 block in the same
shape as the other rules. Failures exit code stays `2`.

Update [commands/tick.md](commands/tick.md) Step 3's "Validation rules"
prose to reference rule 7 alongside rule 6.

## P5.5 — `parse_comments.py` extension

P1.2's `parse_comments.py` already extracts the latest tracking comment
and rework context. P5 needs one additional output: the most recent
implementer summary's PR URL, so Step 13 can include it.

**Edit `scripts/parse_comments.py`**: add a new top-level key to the
output JSON:

```json
{
  ...existing keys...,
  "latest_implementer_summary": {
    "pr_url": "https://github.com/org/repo/pull/123" | null,
    "branch": "team/eng-456-add-foo" | null
  }
}
```

**Derivation**:
- Scan comments newest-first.
- Find the first one whose body contains a substring matching
  `/https:\/\/github\.com\/[^\s)]+\/pull\/\d+/` AND was posted by the
  bootstrap (heuristic: the comment immediately follows a
  `cadence:state {"state":"implement", ...}` attempt marker by the same
  author). Capture the PR URL.
- If a `**Branch:**` line is present in the same comment, capture the
  branch name.
- If no such comment exists, return nulls.

The bootstrap calls `parse_comments.py` once per fire (already does
this for attempt-counting); the additional key is free.

## P5.6 — Workflow-state catalog update

[workflow-diagram.md](./workflow-diagram.md) already shows the P5 shape
(`plan_review` gate between `plan` and `implement`; `agent_review`
between `implement` and `human_review`). The implementing session should:

1. Verify the existing diagram still matches the P5.1 YAML byte-for-byte
   (state names, transitions, gate-vs-agent shapes — including the
   `plan_review` approve→`implement` / reject→`plan` edges).
2. If a README "Default workflow" section embeds or links the diagram,
   confirm the link still resolves.

No diagram regeneration is required unless the implementing session
chooses different state names than P5.1.

## P5 acceptance criteria

- [ ] `templates/workflow.example.yaml` includes the `plan_review` gate
      with `type: gate`, `linear_state: "Plan Review"`,
      `on_approve: implement`, `on_rework: plan`, `max_rework: 2`. The
      `plan` state's `next` is updated from `implement` to `plan_review`.
- [ ] `templates/workflow.example.yaml` includes the `agent_review`
      state with `subagent: reviewer`, `linear_state: "Reviewing"`,
      `adversarial_context: true`, `next: human_review`. The `implement`
      state's `next` is updated to `agent_review`. The former `review`
      state is renamed to `human_review` (its `linear_state`,
      `on_approve`, `on_rework`, and `max_rework` values are unchanged;
      the gate already has no `approved_linear_state` /
      `rework_linear_state` — those were removed in P4).
- [ ] No `commands/tick.md` or `scripts/*.py` changes are required for
      `plan_review` — confirm Step 10's gate dispatch and
      `validate_workflow.py`'s rules are gate-name-agnostic and handle
      the second gate with no edits.
- [ ] `templates/agents/reviewer.md` has the adversarial "## Your role"
      and "## How to review" sections from P5.3, with `model: opus` in
      the front matter and `Bash` added to `tools`.
- [ ] `commands/tick.md` Step 13 contains the adversarial-context
      variant clause from P5.4, keyed on
      `targetState.adversarial_context === true`.
- [ ] `scripts/validate_workflow.py` enforces Rule 7
      (`adversarial_context` is bool, agent-states only); `--evidence`
      output includes a Rule 7 block.
- [ ] `scripts/parse_comments.py` emits a `latest_implementer_summary`
      key with `pr_url` and `branch` fields.
- [ ] README.md "Required Linear columns" list updated to include
      `Plan Review` and `Reviewing`.
- [ ] `workflow-diagram.md` continues to reflect the workflow (the
      existing diagram already uses `agent_review` / `human_review`
      labels — no update required, but verify it still matches).
- [ ] `.claude-plugin/plugin.json` version bumped.
- [ ] CHANGELOG.md entry under `## [Unreleased]`, with a clear
      "Upgrading from pre-P5: add `Plan Review` and `Reviewing` Linear
      columns; in `.claude/workflow.yaml`, insert a `plan_review:` gate
      block and point `plan.next` at it, rename `review:` to
      `human_review:`, change `implement.next` to `agent_review`, and
      insert the new `agent_review:` block (see
      `templates/workflow.example.yaml`)" note.
- [ ] Smoke M — **fresh install**: `/cadence:init` into a throwaway
      consumer repo. Confirm `workflow.example.yaml` ships with six
      states (plan, plan_review, implement, agent_review, human_review,
      done). Add the Linear `Plan Review` and `Reviewing` columns. Push
      an issue through plan → plan_review → implement → agent_review.
      Confirm:
      - After `plan`, the issue lands in `Plan Review` and waits;
        adding `cadence-approve` advances it to `implement` on the next
        fire (and `cadence-rework` routes it back to `plan`).
      - The `agent_review` state's tracking comment appears.
      - The reviewer's findings comment appears.
      - The reviewer's Lifecycle Context (visible in the cloud session
        transcript) contains the ticket AC but does NOT contain the
        implementer's prior summary text.
      - The issue lands in `In Review` for the human gate.
- [ ] Smoke N — **upgrade path**: take a pre-P5 consumer repo whose
      `.claude/workflow.yaml` still names its gate `review:`, has
      `plan.next: implement`, and has no `plan_review` or `agent_review`
      state. Run `/cadence:tick`. Confirm it still works (the workflow
      validation does not require `plan_review`, `agent_review`, or
      `adversarial_context` to be present; only the example template
      gains them). The CHANGELOG documents the upgrade steps for
      consumers who want the new behaviour.

## P5 commit guidance

- One commit for the workflow template + Linear-column README update.
- One commit for the reviewer template rewrite.
- One commit for the tick.md Step 13 change + parse_comments.py
  extension.
- Do not bundle P5 with P1, P2, P3, P4, or P6.

---

# Phase 6 — Per-state concurrency caps

**Prerequisite**: P1 has landed. P2/P3/P4/P5 are independent.

**Outcome**: workflow states can declare `max_in_flight: N`. The
bootstrap respects the cap at pickup time: if `N` issues are already in
that state's Linear column, candidates that would target that state are
skipped. This is **coordinational**, not a throughput optimisation — it
exists to prevent (e.g.) six concurrent `implement` runs when there is
only one human-review slot available downstream.

Per Design Principle 5 ("the tracker IS the workflow"), the cap is
derived from Linear column counts on every fire. No sidecar state.

## P6.0 — Semantics

- `max_in_flight: N` is an **optional** field on `type: agent` states.
  Absent → no cap (current behaviour).
- The cap counts issues whose **current Linear column equals the
  state's `linear_state`**, regardless of whether they carry the
  `cadence_active` lock label. (An issue paused mid-fire still occupies
  a slot from a downstream coordination perspective.)
- The cap does NOT apply to `type: gate` states (gates are
  human-driven; a backlog of issues in `In Review` is fine and is the
  human's signal that they need to act). Validation enforces this.
- The cap does NOT apply to `type: terminal` states.
- The cap is checked at **pickup** time. An in-progress fire whose
  target state is over-cap is not aborted; the cap only affects the
  *next* candidate selection.

This is intentionally weak coordination — strong coordination would
require a hard lock at the state level, which violates the "tracker is
the workflow" principle. Operators get the right shape by setting caps
that match their human-review bandwidth; drift is self-correcting on
the next fire.

## P6.1 — `workflow.yaml` schema extension

Update `templates/workflow.example.yaml` to add a commented-out example:

```yaml
  # ----------- implementation -----------
  implement:
    type: agent
    subagent: implementer
    linear_state: "Implementing"
    next: agent_review
    # Optional: cap concurrent issues at this state. Useful when a
    # downstream gate (human review) has bounded throughput. Omit for
    # unlimited.
    # max_in_flight: 3
```

## P6.2 — `validate_workflow.py` (P1.1) extension

Add a **sixth validation rule** to `scripts/validate_workflow.py`:

**Rule 6 — max_in_flight type and scope.**
- For every state with a `max_in_flight` key:
  - The value must be a positive integer (>= 1). Reject `0`, negative
    numbers, strings, floats, null.
  - The state's `type` must be `agent`. Reject on `gate` or `terminal`.

Update the script's `--evidence` output to include a Rule 6 block in
the same shape as Rules 1–5. Failures exit code stays `2`.

Update [commands/tick.md](commands/tick.md) Step 3's "Validation rules"
prose to reference rule 6 in passing (the script is the source of
truth, but the prose listing rule 1–5 needs the new rule appended for
reader-orientation).

## P6.3 — `tick.md` Step 5: enforce caps at pickup

Edit Step 5 ("Pick work") to add a per-candidate cap check. After the
existing filter list and sort, insert a new bullet *before* "If
`candidates` is empty":

```markdown
Apply per-state concurrency caps. For each state in the workflow config
that has a `max_in_flight` key:

1. Query the Linear MCP for issues in `linear.team` /
   `linear.project_slug` whose current Linear column equals this
   state's `linear_state`. Count the result; call it `inFlightCount`.
2. If `inFlightCount >= max_in_flight`, mark this state as **over-cap**
   for this fire.

Then filter `candidates`:
- For each candidate, determine which state it would target if picked
  up (in most cases this is the workflow state matching the candidate's
  current Linear column; for issues sitting in `pickup_state` it is
  the entry state).
- If the target state is over-cap, drop the candidate from
  `candidates` and continue with the next one.

If `candidates` becomes empty after this filtering, print
`No eligible issues (caps reached for: <state names>).` and exit
cleanly. Otherwise proceed to step 6 with the filtered list.
```

**Implementation note for the implementing session**: the cap-counting
query is O(states-with-caps) extra Linear MCP calls. For the default
workflow that's 0–2 extra calls per fire. If this becomes a cost
concern, batch into a single list-issues call grouped by state — but
defer that until usage data justifies it.

## P6.4 — `commands/status.md` extension

Update `/cadence:status` to surface per-state in-flight counts and caps
in its output. Add a new table after the existing state-summary table:

```markdown
### Concurrency

| State                | In flight | Cap    | Status   |
|----------------------|-----------|--------|----------|
| plan                 | 1         | (none) |          |
| implement            | 2         | 3      |          |
| agent_review         | 1         | 2      |          |
| human_review (gate)  | 4         | n/a    |          |
| done                 | 12        | n/a    |          |
```

`Status` column values:
- empty / `OK` when below cap (or no cap).
- `AT CAP` when `inFlightCount == max_in_flight`.
- `OVER CAP` when `inFlightCount > max_in_flight` (a human moved
  issues manually; the next pickup will skip this state until drained).

## P6.5 — `commands/sweep.md` — no changes

The sweeper does not pick up work and does not need cap awareness.
Confirm by re-reading [commands/sweep.md](commands/sweep.md) at
implementation time. (If a future change has the sweeper acting on
issue assignment, revisit.)

## P6 acceptance criteria

- [ ] `templates/workflow.example.yaml` shows a commented-out
      `max_in_flight` line on the `implement` state.
- [ ] `scripts/validate_workflow.py` enforces Rule 6; `--evidence`
      output includes a Rule 6 block.
- [ ] `commands/tick.md` Step 5 includes the cap-enforcement
      sub-section from P6.3.
- [ ] `commands/status.md` includes the "Concurrency" table.
- [ ] CHANGELOG.md entry under `## [Unreleased]`.
- [ ] README.md "Workflow tuning" section gains a paragraph naming
      `max_in_flight` and when to use it (the typical reason: bounded
      human-review bandwidth downstream).
- [ ] Smoke O — **cap enforcement**: in a throwaway consumer repo, set
      `implement.max_in_flight: 1`. Create three Linear issues in
      `pickup_state`. Manually move one to `Implementing`. Run
      `/cadence:tick`. Confirm the bootstrap prints the "caps reached"
      message and exits without claiming a new issue. Move the
      first issue to `Reviewing` (out of `Implementing`). Run again —
      confirm pickup now proceeds.
- [ ] Smoke P — **validate rejects bad caps**: set
      `implement.max_in_flight: 0`. Run `/cadence:tick`. Confirm the
      validation step exits with a Rule 6 failure naming the offending
      state. No Linear writes.
- [ ] Smoke Q — **gate cap rejection**: set
      `review.max_in_flight: 5`. Run `/cadence:tick dry-run`. Confirm
      Rule 6 fails (caps not allowed on gates).

## P6 commit guidance

- One commit for the schema extension + validator (P6.1 + P6.2).
- One commit for tick.md and status.md (P6.3 + P6.4).
- Do not bundle P6 with P1, P2, P3, P4, or P5.

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
| P3 acceptance-criteria format drifts (some tickets use checkboxes, some use a JSON block, some use plain prose). | Planner enforcement is the backstop — non-conforming tickets fail to plan and escalate to `cadence-needs-human`. `/cadence:create-ticket` is the happy-path scaffold; the template is the documented shape. Drift on a single ticket fails closed (no plan produced), not silently. |
| P3 planner rejects a ticket the operator considers good enough. | Operator either (a) reformats the ticket to match the template (fastest), (b) sets `cadence-needs-human` themselves and works the issue manually outside the workflow, or (c) edits `templates/agents/planner.md` in their consumer copy to relax the gate. All three are user-controllable. The default is strict because the cost of a bad plan downstream is high. |
| P4 gate verdict labels are less glanceable on a Linear board than a dedicated column was — a reviewer scanning the board can't see "approved vs needs-rework" at a glance. | Accepted trade-off. The gain (one column per gate instead of three) outweighs it, and the verdict is still visible on the issue itself. Operators who want column-level visibility can keep their old `Approved` / `Needs Rework` columns as foreign columns and move cards there *in addition to* labelling — Cadence ignores foreign columns, so it costs nothing. |
| P4 bootstrap forgets to remove the verdict label after acting, so the next fire re-processes the same verdict. | The label removal is an explicit step in the 10b / 10c prose (mirrors how `cadence_active` is already removed every fire). If it is missed, the failure mode is benign-ish: the issue is no longer in a gate column after the move, so the stale label is inert until the issue returns to a gate. `/cadence:sweep` can be extended to clear stale verdict labels if this proves a real problem. |
| P5 reviewer's adversarial stance misses context that a "narrative-aware" reviewer would catch (e.g. the implementer flagged a tradeoff in the plan summary). | Acceptable. The point of adversarial review is to catch what the implementer's narrative obscures; the rework loop's human gate is the place where narrative reconciliation happens. If a project repeatedly hits this case, operators can downgrade the reviewer to share context by editing their consumer-copy `reviewer.md` — explicit choice, not a default. |
| Consumer sets `adversarial_context: true` on a state that doesn't have a review-class subagent and is surprised when their implementer gets a stripped-down context. | The flag is documented in the example as paired with the reviewer subagent. Validator rule 7 only constrains type (must be bool, must be on `type: agent`); it does NOT enforce that the subagent is "review-class" because there's no such taxonomy. Operator choice; failure mode is recoverable (just remove the flag). |
| P6 cap counts drift from reality when humans manually move issues between fires. | Counts are recomputed every fire from live Linear column membership — drift self-corrects on the next pickup. The cap is documented as "approximate coordination, not a hard lock"; operators should not treat it as one. |
| P6 cap-counting query inflates Linear MCP call volume per fire. | Default workflow has 0–2 cap'd states, so 0–2 extra MCP calls per fire — negligible. If a high-cardinality workflow drives this up, batch into a single list-issues call grouped by state at implementation time. |

---

# Implementation order

The phases are independent in spirit but the order matters in practice.
P1 unblocks P2, P4, P5, and P6. P3 is independent of P2/P4/P5/P6. P4
(gate signaling) requires only P1.1. P5 requires P4 — its gate block
builds on the label-based gate shape — and benefits from P3 but does
not require it.

**P1 — Helper scripts**

1. **P1.1** (`validate_workflow.py`) — unlocks the dry-run cleanup and is
   the basis for the consumer-copy used by P2.2, the Rule 1 / Rule 8
   changes in P4.3, and the rule-6 extension in P6.2.
2. **P1.3** (`emit_tracking_comment.py`) — independent; ship next.
3. **P1.2** (`parse_comments.py`) — most surface area; ship last in P1.
4. **Land P1** as one PR (or split per-script PRs at the implementer's
   discretion). Run the manual smoke before merging.

**P2 — Hooks**

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

**P3 — Ticket-quality scaffolding** *(independent of P2; can run in
parallel with P2 implementation)*

10. **P3.1** (`templates/ticket-template.md`) — pure docs; ship first.
11. **P3.2** (`commands/create-ticket.md`) — independent of P3.3-P3.5.
12. **P3.3** (planner enforcement) — the behavioural change.
13. **P3.4** (implementer/reviewer AC verification) — depends on the AC
    format being decided; ship after P3.0 is settled.
14. **P3.5** (`init.md` ticket-template copy + next-steps text).
15. **Land P3** as one PR. Run smoke tests G, H, I.

**P4 — Label-based gate signaling** *(requires P1.1 for the validator
amendment; independent of P2/P3)*

16. **P4.1** (workflow.example.yaml — gate block loses the two
    `*_linear_state` fields; `label:` section gains `cadence_approve` /
    `cadence_rework`) — ship first; it defines the new shape.
17. **P4.3** (`validate_workflow.py` — narrow Rule 1, add Rule 8
    rejecting legacy gate keys) — ship next so the validator matches
    the new template.
18. **P4.2** (tick.md — Step 10 label-branch rewrite, plus Steps 3/8/9
    and `workflowLinearStates`) — the behavioural change.
19. **P4.4** (README + MIGRATION + CHANGELOG — column list shrinks, gate
    labels documented, "Upgrading to label-based gates" section).
20. **P4.5** (status.md — drop the removed-field rows; optional
    verdict-label count).
21. **Land P4** as one PR. Run smoke tests J, K, L. L is load-bearing:
    confirm the validator rejects a legacy 3-column gate schema.

**P5 — Plan-review gate + adversarial review** *(requires P4 for the
label-based gate shape; recommended after P3; requires P1.1 for the
rule 7 validator extension and P1.2 for the `latest_implementer_summary`
extension)*

22. **P5.3** (reviewer template rewrite) — independent of the wiring;
    ship first so the template is reviewable in isolation.
23. **P5.1** (workflow.example.yaml `plan_review` gate + `agent_review`
    state + `human_review` rename + `adversarial_context` flag) +
    **P5.2** (README Linear-column update) — ships together.
24. **P5.4a** (`validate_workflow.py` rule 7 — `adversarial_context` is
    bool, agent-states only).
25. **P5.5** (`parse_comments.py` extension for
    `latest_implementer_summary`).
26. **P5.4** (tick.md Step 13 adversarial-context variant) — last,
    because it depends on P5.3 (the subagent shape), P5.4a (validator
    accepts the flag), and P5.5 (the new helper-script output).
27. **P5.6** (workflow-diagram verification) — docs only; trivial.
28. **Land P5** as one PR. Run smoke tests M and N. N is load-bearing:
    confirm pre-P5 consumer repos still work with their unchanged
    `workflow.yaml`.

**P6 — Per-state concurrency caps** *(independent of P3/P4/P5)*

29. **P6.1** (workflow.example.yaml example) + **P6.2** (validator
    rule 6) — ship together.
30. **P6.3** (tick.md Step 5 cap enforcement).
31. **P6.4** (status.md Concurrency table).
32. **Land P6** as one PR. Run smoke tests O, P, Q. P and Q together
    verify validator rejection; O verifies live cap behaviour.

Each phase should land independently. Do not bundle.
