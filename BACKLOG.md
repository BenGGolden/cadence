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

## Optional `merge` state between `review` and `done`

**Idea**: an opt-in workflow state that runs `gh pr merge` after the
human approves at the `review` gate, before the issue lands in `done`.
Today the bootstrap removes the `cadence-approve` label and moves the
Linear card to the gate's `on_approve` target with no awareness of PR
state — if the human approved without merging, the Linear card lands in
Done while the PR sits open.

**Why**: closes the "approved but PR not merged" gap that
[tick.md step 10b](./commands/tick.md) currently leaves to convention.
Linear and GitHub move together, end of story.

**The setting is the state itself.** Cadence's workflow YAML already
lets consumers add intermediate states. No new schema needed — a
consumer who wants the auto-merge behaviour adds a `merge` state and a
corresponding Linear column:

```yaml
review:
  type: gate
  linear_state: "In Review"
  on_approve: merge        # was: done
  on_rework: implement

merge:
  type: agent
  subagent: merger         # new subagent template; runs `gh pr merge`
  linear_state: "Merging"
  next: done
```

Consumers who prefer the status-only signal omit the state and leave the
gate pointed straight at `done` (today's behaviour). A status warning
(`approved but PR still open`) could be a separate, smaller change that
helps either camp without forcing the auto-merge path.

**Open questions**:

- Where does the `merger` subagent live — shipped as a template, or
  documented in README as a recipe consumers paste in? Shipping it
  makes the opt-in one YAML edit; recipe-only keeps the template set
  smaller.
- `gh pr merge` flag defaults: `--squash`? `--auto`? Configurable per
  consumer via the subagent's body, probably — same pattern as the
  rest of the agent templates.
- Failure handling: if `gh pr merge` fails (CI red, conflicts, branch
  protection), does the issue land in `cadence-needs-human` like any
  other agent failure, or is there a dedicated failure path? Reuse the
  `max_attempts_per_issue` escalation; it already covers this shape.
- Status reporter: should it cross-reference PR state on the gate row
  regardless of whether `merge` is in the workflow? A read-only "PR is
  still open" signal is useful in both camps.

**Why not now**: not blocking the current Phase 4 work; the
convention-based approach ("approve after you merge") works for teams
with a small reviewer set and tight feedback loops. Worth picking up
when the manual coordination starts producing "approved cards with
stale PRs" reports.

**Discussed in**: conversation on 2026-05-18 about Phase 4 smoke
testing — the question "does the approve flow expect the PR to be
merged first?" surfaced the gap.

