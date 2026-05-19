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

---

## Surface routine failures to the operator

**Idea**: when a `/schedule` routine fails before producing any
Linear-visible side effect — hook block, container-setup error,
unhandled exception during `/cadence:tick` step 1-5 — give the
operator a signal somewhere they actually look. Today these failures
end the routine quietly.

**Why**: discovered during Phase 4 Smoke L. The
`validate_workflow_on_prompt.py` hook correctly blocked a legacy-schema
`/cadence:tick` prompt with a Rule 8 message on stderr. Locally this
renders in the terminal and is obvious. In a cloud `/schedule` routine
it goes nowhere:

- claude.ai/code/sessions does NOT show routine sessions.
- Routines do NOT have an exposed stderr view.
- The routine just ends; the operator sees "nothing happened" and has
  no way to find out why short of digging into internal logs.

This generalises beyond the hook case. Anything that aborts before the
bootstrap reaches its first Linear write — failed `claude mcp list`,
unreachable Linear MCP, broken `.claude/settings.json`, container setup
failure — has the same shape: invisible to the operator.

**Constraints**:

- Cadence runs inside someone else's compute (the routine platform).
  It cannot directly write to the platform's UI; whatever signal it
  produces has to ride out through a channel the platform exposes
  (exit code, stdout, an explicit notification API if one exists) or
  through an external channel the operator configures.
- Cloud containers are ephemeral. A local status file won't survive.

**Rejected alternatives** (settled in conversation, do not revisit):

- **Post a tracking comment on a recently-touched Linear issue.** The
  operator would have to hunt through Linear to find a maybe-comment
  on a maybe-issue. The signal has to live in a known location, not
  scattered across the workflow.

**Candidate paths** (not yet chosen):

1. **Routine-UI signal via exit code / stdout shape.** Investigate what
   the routine platform actually surfaces when a routine exits
   non-zero, or when it prints a recognisable pattern. If there's
   anything operator-visible, route hook-block and bootstrap-error
   output through it. This is the lowest-friction path *if* the
   platform cooperates.
2. **Configurable notification webhook.** Add an optional
   `notifications.webhook_url` (or `.email`) to `workflow.yaml`. On
   any fire-aborting failure, POST a small JSON payload (or send a
   minimal email). Operator points it at Slack / PagerDuty / a forwarder
   of their choice. Adds an external dependency but gives the operator
   full control over the channel.
3. **Claude Code in-session alert.** If the routine platform supports a
   "session notification" primitive (TBD whether it does), use it.
   Likely overlaps with path 1.
4. **Self-monitoring routine.** A separate, less-frequent `/cadence:health`
   that asserts the main tick routine has made forward progress
   recently and surfaces failure through paths 1-3 if not. Useful as a
   higher-level liveness check regardless of which of the others lands.

**Open questions**:

- What signals does the routine platform actually surface to operators
  today? (`/schedule` routine logs are findable somewhere, just not
  obviously — confirm before designing around them.)
- Should the hook block path differ from the
  bootstrap-error-during-`tick` path? Both have the same "no Linear
  side effect happened" shape from the operator's POV; arguably they
  should converge on one notification channel.
- Failure modes during a fire that *did* produce some Linear side
  effect (subagent crashed, attempt cap hit) are already comment- and
  label-visible on the issue. Out of scope for this item — it covers
  only the "fire produced nothing at all" class.

**Why not now**: the hook block is the only failure class with a known
trigger we hit during smoke testing. The wider gap exists but isn't
acute. Pick this up when a second operator gets bitten, or when
deciding the notification channel becomes part of the standard
`/cadence:init` flow.

**Discussed in**: conversation on 2026-05-18 — Phase 4 Smoke L: the
hook correctly rejected a legacy schema but the operator saw a silent
end on the cloud routine.

