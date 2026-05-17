# Cadence backlog

Ideas and follow-ups that aren't on the active hardening track
([HARDENING-PLAN.md](./HARDENING-PLAN.md)) but should not be lost. Pull
items into the hardening plan or a feature branch when they become
priorities.

---

## Linear OAuth app (Cadence as a first-class integration)

**Idea**: register Cadence as a Linear OAuth app so the workspace sees
it as a named integration rather than as whichever user owns the MCP
token or personal API key.

**Why**: today every Linear write — tracking comments, label adds,
state moves — is attributed to the operator's user. An OAuth app would
make Linear show "Cadence" as the responsible actor in activity panels,
allow narrower API scopes than personal keys, and improve the audit
story. The mechanism (labels + comments) doesn't change; this is
polish, not architecture.

**Why not now**: introduces an OAuth app lifecycle (client ID / secret,
redirect URIs, app review) that the plugin currently avoids by riding
on whatever Linear MCP server the operator already has. Doesn't unblock
any active work.

**Open questions**:

- Does Linear OAuth interact cleanly with cloud `/schedule` routines,
  which already authenticate to Linear via the claude.ai connector?
- Single shared app vs. per-consumer registration?
- Minimum scopes (`issues:read`, `issues:write`, `comments:write`,
  `labels:write`?).

**Discussed in**: conversation on 2026-05-15 about whether Linear's
extension surface lets Cadence look less like a series of workarounds.
The label-group recommendation ([HARDENING-PLAN P4.4a](./HARDENING-PLAN.md))
came out of the same conversation; this one is the longer-horizon
companion.

---

## Move more slash-command logic into deterministic scripts

**Idea**: shift more of the slash-command logic from LLM prose into
Python scripts under `scripts/`. The bootstrap prose stays the
orchestrator (only the harness can call MCP tools), but mechanical
steps — filter / sort, schema match, templating, state-name lookup —
move to scripts the prose invokes via Bash.

**Why**: prose-as-logic is non-deterministic. Two `/cadence:tick` fires
reading the same Linear state can produce subtly different output if
the model re-interprets ambiguous prose. Scripts give us:

- Repeatability (same inputs → same outputs, every time).
- Testability (unit tests over pure functions, no LLM in the loop).
- Smaller prompt surface (less prose for the model to follow → fewer
  steps where it can drift).
- Easier forensic debugging (the script's stdout is the canonical
  derivation, not a model's interpretation of the prose).

**Already scripted** (extracted in P1): `validate_workflow.py`,
`parse_comments.py`, `emit_tracking_comment.py`.

**Plausible next candidates** in [commands/tick.md](./commands/tick.md):

- **Step 5 (pick work)** — filtering and sorting Linear query results
  is pure logic. The MCP call returns a list; a script ranks it.
- **Step 8 (match workflow state)** — column-name → workflow-state is
  a table lookup, not a reasoning task.
- **Step 13 (compose Lifecycle Context block)** — pure templating with
  a fixed schema. Currently ~60 lines of prose telling the model to
  render a Markdown block; a script could do it from JSON inputs.
- **Step 16 (advance Linear state)** — deciding which MCP write to
  make from `next.type` is deterministic dispatch.

Likely also applies to parts of `sweep.md` and `status.md`.

**Constraints**:

- The CLAUDE.md invariant `commands/tick.md` prose IS the dispatch
  logic stands. This refactor doesn't break it — the prose still owns
  step ordering and the orchestration spine, it just delegates
  mechanical bits to scripts instead of restating algorithms inline.
- Scripts cannot call MCP tools directly. Anything that requires a
  Linear read or write stays in the prose layer.
- Keep scripts stdlib-only, matching the existing helpers.

**Why not now**: no acute pain point. P1 already extracted the
highest-value pieces. Worth scoping when a specific step starts showing
flakiness, or when prose grows long enough to be hard to audit.

**Discussed in**: conversation on 2026-05-15.

---

## Scaffold the Linear permissions allowlist during `/cadence:init`

**Idea**: extend `/cadence:init` to detect the Linear MCP server in use
and pre-populate `.claude/settings.local.json` (or a chosen settings file)
with `permissions.allow` entries for the read/write tools Cadence calls.

**Why**: today an operator has to:

1. Run `/cadence:init`.
2. Read the README's "Linear MCP tools" section.
3. Figure out which MCP namespace their installation uses
   (`mcp__linear-server__*` vs. `mcp__linear__*` vs.
   `mcp__claude_ai_Linear__*`).
4. Hand-edit `.claude/settings.local.json` (or the routine's permissions
   panel in /schedule mode) with the right tool names.
5. Discover gaps the hard way — every missing tool surfaces as an
   auto-mode classifier denial at fire time, often mid-loop, sometimes
   inside a runaway cron that has to be killed manually.

This is the single biggest setup wart surfaced by Phase 2 smoke testing.
Smokes D, E, and F each tripped over a different facet of the same root
cause: the README tells operators what to allow, but nothing automates it.

**Sketch of the fix**:

- `/cadence:init` runs `claude mcp list` (or reads `.mcp.json`) to find
  the Linear server's namespace.
- Generates a `permissions.allow` list using that namespace plus the
  canonical Cadence verbs (`save_comment`, `save_issue`, `list_issues`,
  `get_issue`, `list_comments`).
- Writes/merges that list into `.claude/settings.local.json` (untracked,
  per-operator) with the same idempotent merge contract
  `merge_settings_hooks.py` already uses for the hooks block.
- For `/schedule`, prints the same list at the end of init so the operator
  can paste it into the routine's permissions panel — cloud routines
  don't read local settings.

**Open questions**:

- Detection: is `claude mcp list` machine-readable? Falls back to "ask
  the operator" if not.
- Should this live in `/cadence:init` or a dedicated
  `/cadence:permissions` command? The latter is more focused and can be
  re-run when an operator adds new MCP servers; the former keeps the
  one-command setup story.
- Account-level claude.ai connectors (the recommended /schedule path)
  expose tools to routines via the connector toggle, not via
  `permissions.allow` — does the scaffold still help, or just mislead?

**Why not now**: not in Phase 2 scope (which is hook plumbing only);
also wants verification that the detection path works against all three
known Linear MCP variants. Captured here so the next consumer doesn't
hit the same setup tax.

**Discussed in**: Phase 2 smoke testing, 2026-05-16. Auto-mode classifier
denials on `mcp__linear-server__save_issue` triggered the conversation.

