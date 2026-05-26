# Cadence backlog

Ideas and follow-ups that aren't currently scheduled but should not be
lost. Pull items into a feature branch when they become priorities.

The original hardening track (P1–P9) shipped and is captured in
[CHANGELOG.md](./CHANGELOG.md); the design principles those phases
served live in [GUIDEPOSTS.md](./GUIDEPOSTS.md).

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
The label-group recommendation (now in
[templates/workflow.example.yaml](./templates/workflow.example.yaml)
and [README.md](./README.md)) came out of the same conversation; this
one is the longer-horizon companion.

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

---

## Durable audit log in `/schedule` mode

**Idea**: mirror the per-fire audit-log entries from
[templates/hooks/audit_linear_writes.py](./templates/hooks/audit_linear_writes.py)
into a Linear comment at the end of each fire, so the forensic trail
survives session teardown in `/schedule` mode.

**Why**: today the hook writes JSONL to `.cadence/audit.log` in the
working tree. In `/schedule` mode the working tree is a fresh clone
per fire and is discarded when the cloud session ends, so the log
evaporates with the session.
[GUIDEPOSTS Principle 7](./GUIDEPOSTS.md) names "audit log of every
tracker write" as load-bearing for forensic debugging, and `/schedule`
is the [design-target mode](./README.md) — so the principle-7 gap
binds where it matters most. The hardening track flagged this
explicitly when it shipped the audit hook ("if durable audit history
matters, the right home is a Linear comment") and deferred. With all
nine phases shipped, this is now the largest remaining principle-7
gap.

**Shape sketch**:

- Keep the existing per-write hook — it's useful in `/loop` and as a
  per-fire stream operators tail.
- Add a step 17.5 in [commands/tick.md](./commands/tick.md) (or a
  small helper script invoked from step 18) that reads the fire's
  `.cadence/audit.log` and posts a single `<!-- cadence:audit ... -->`
  tracking comment summarising the writes.
- Mark the comment with its own prefix so
  [parse_comments.py](./scripts/parse_comments.py) ignores it on
  future fires (audit comments are write-only — never read back by
  the bootstrap).

**Open questions**:

- One audit comment per fire, or one rolling comment per issue with
  appends? Per-fire matches the rest of the tracking-comment shape;
  per-issue is more compact but adds an edit-vs-append branch.
- Failure-path coverage: a fire that aborts before the audit-post
  step leaves no audit comment. Acceptable (the failure record plus
  the absence of an audit summary is itself a signal), or does the
  failure path need its own truncated audit emit?
- Rendering: a busy fire writes ~5–15 audit lines. Fenced JSONL block
  or pretty-rendered table?

**Why not now**: no operator has hit the missing-audit-trail case yet
— the design-target mode hasn't run at high volume. When the first
incident requires reconstructing what a `/schedule` fire did, this
becomes load-bearing.

**Discussed in**: post-P9 review conversation on 2026-05-25 about
which GUIDEPOSTS principles still have material gaps.

---

## Configurable PR-creation tool (beyond `gh`)

**Idea**: today [templates/agents/implementer.md](./templates/agents/implementer.md)
Rule B and [templates/agents/reviewer.md](./templates/agents/reviewer.md)
both hardcode `gh` as the PR tool. The "or the configured PR-creation
tool" hedge in the prose has no configuration path. Add an optional
schema field that names the tool and the minimal invocation surface,
so a GitLab or Bitbucket consumer can route the implementer to `glab`
/ `bb` / etc. instead of silently bailing.

**Why**: a GitLab-hosted consumer running Cadence today has the
implementer push the branch, hit Rule B (`gh` missing), and bail
without opening a merge request. The branch lands; the MR doesn't.
Everything downstream that uses the PR URL — the reviewer's
`gh pr view`, [parse_comments.py](./scripts/parse_comments.py)'s
`latest_implementer_summary.pr_url`, the adversarial Lifecycle
Context's `PR:` line — falls back to `git diff`, which works but
loses platform-side context (reviewers, conversation, CI status).

**Shape sketch**:

- Workflow.yaml gains an optional `tools.pr_create` block naming the
  command, a URL-extraction pattern, and any required env var.
- Implementer Rule B branches on `which $TOOL` instead of `which gh`.
- Reviewer's `gh pr view` fallback in step 2 of "How to review"
  follows the same lookup.
- Validator rejects malformed `tools.pr_create` shapes (P1.1-style
  rule).
- README documents the GitHub default and a GitLab example.

**Open questions**:

- How much of the PR-tool surface needs to be configurable? `gh pr
  create --title X --body Y` and `glab mr create --title X
  --description Y` are similar but not identical; a thin abstraction
  works only if the operator can supply the flag mapping.
- The reviewer's `gh pr view --json files,additions,deletions` is
  structured JSON; `glab` has its own JSON shape. Either Cadence
  stays oblivious to the JSON (just run the command and read what
  comes back) or it standardises a small wrapper.
- Does this overlap with the [Linear OAuth app](#linear-oauth-app-cadence-as-a-first-class-integration)
  backlog item — should the per-platform PR tool live in a
  `platforms:` block alongside the Linear config?

**Why not now**: no consumer has hit this. The implicit "Cadence is
`gh` / GitHub only" assumption is documented nowhere but holds for
every current user. Pick this up when a GitLab/Bitbucket operator
surfaces the gap, or when the PR-tool indirection becomes part of a
broader platforms refactor.

**Discussed in**: post-P9 review conversation on 2026-05-25 —
surfaced when checking Rule B's wording in the implementer template.

---

## Decommission path / `/cadence:uninstall`

**Idea**: a documented (and ideally scripted) way to remove Cadence
from a consumer repo. Today [/cadence:init](./commands/init.md)
scaffolds files into `.claude/`, merges hook entries into
`.claude/settings.json`, writes permissions into
`.claude/settings.local.json`, copies dispatch prose into
`.claude/commands/cadence/`, and tells the operator to create Linear
labels. Reversing this is currently a manual cleanup with no
checklist.

**Why**: a consumer that decides Cadence isn't a fit needs a clean
exit. The hook scope-guard handles the case where they delete
`.claude/workflow.yaml` but leave hooks behind (the hooks silently
no-op), so the worst case isn't broken builds — it's slow
accumulation of dead files in the repo. But the Linear side
(`cadence-active`, `cadence-needs-human`, `cadence-approve`,
`cadence-rework` labels; the workflow columns) is invisible to the
plugin and stays unless the operator cleans it manually.

**Shape sketch**:

- New `/cadence:uninstall` command, or a documented runbook in
  [README.md](./README.md).
- Removes the scaffolded `.claude/` files, the merged hooks block
  from `.claude/settings.json`, the Cadence permissions from
  `.claude/settings.local.json`, and `.cadence/`.
- Prints a checklist of Linear-side cleanup the plugin can't do for
  the operator: which labels are safe to delete, which workflow
  columns are no longer needed.
- Optionally, a dry-run mode that lists what *would* be removed
  without touching anything.
- Idempotent (re-running on a half-uninstalled repo finishes the
  job).

**Open questions**:

- Hard delete or move-aside? A `.claude/cadence.uninstalled/`
  quarantine directory is safer for a panicky operator but adds
  clutter; a hard delete is cleaner but irreversible.
- Should it also offer to remove the Linear labels via MCP?
  Possible, but mixes plugin-managed state (files) with
  consumer-managed state (Linear configuration) in ways the rest of
  Cadence carefully avoids.
- Does this surface a `.claude/cadence/` namespacing question —
  would future Cadence be cleaner if all its files lived under one
  parent dir instead of scattered across `.claude/hooks/`,
  `.claude/commands/cadence/`, and `.claude/agents/{planner,implementer,reviewer}.md`?

**Why not now**: no operator has decommissioned yet (Cadence is new,
the design-target user is the author). When the first consumer churn
happens, this becomes important — the alternative is "ask in Slack
which files Cadence put where."

**Discussed in**: post-P9 review conversation on 2026-05-25.

---

## Ticket-quality enforcement for Linear-native ticket creation

**Idea**: [/cadence:create-ticket](./commands/create-ticket.md) is a
local-only slash command. Tickets created directly in Linear (the web
app, mobile, copy-paste from elsewhere, the "duplicate issue" button)
bypass the drafter entirely. The
[planner.md ticket-quality gate](./templates/agents/planner.md)
catches them on first fire and refuses to plan — correctly — but each
refusal costs a fire and an attempt counter. Bridge the gap so common
Linear-side ticket creation paths produce Cadence-shaped tickets
without requiring the operator to run a local slash command first.

**Why**: the design-target operator creates tickets across multiple
surfaces (laptop, phone, the Linear web UI). Requiring
`/cadence:create-ticket` to be the entry point for every ticket is
unrealistic. The planner gate is the only enforcement today, which
means the cost of a malformed ticket is one wasted fire per attempt
(capped at `max_attempts_per_issue`, then escalated). Cheap per
ticket, but accumulates if the operator routinely creates "just one
line" tickets and expects Cadence to handle them.

**Candidate paths** (not yet chosen):

1. **Linear native issue template.** Linear supports issue templates
   per team. Documenting how to register the Cadence ticket template
   as a Linear template would make the happy path one Linear UI
   selection rather than a local slash command. No plugin code
   change — pure docs.
2. **Tighter planner backstop.** Today the planner refuses with one
   summary shape and counts the refusal toward
   `max_attempts_per_issue`. It could (a) include a paste-ready
   rewritten ticket body in the refusal comment, (b) escalate with
   `cadence-needs-human` immediately on the first refusal instead of
   waiting for the attempt cap, or both. Lower-cost UX on an
   enforcement mechanism that already works.
3. **Linear webhook + validator service.** A small service receives
   Linear's `Issue.create` webhook and posts a comment on malformed
   tickets pointing at the template. Adds infrastructure Cadence has
   deliberately avoided.

Path 1 is the cheapest and most in-grain with Cadence's
"consumer owns Linear configuration" stance. Path 2 sharpens the
existing mechanism without new infrastructure. Path 3 is the
heaviest and crosses into the dashboard/UI territory the project
has explicitly avoided.

**Open questions**:

- Does Linear's template feature support markdown bodies with
  checkboxes and bold AC-N markers the way the Cadence ticket
  template needs? Confirm before recommending the docs path.
- If path 1 lands, does `/cadence:create-ticket` still pull its
  weight, or is the local command obsolete once the Linear template
  covers the happy path?
- Should the planner's "Cannot plan" comment include a paste-ready
  corrected ticket body? Adds value but blurs the planner's role
  from "judge" into "co-author."

**Why not now**: the planner gate is doing its job — malformed
tickets fail closed. The cost is per-operator-tolerance; if the
operator finds the failure loop annoying they will reach for
`/cadence:create-ticket` themselves. Pick this up when the
malformed-ticket rate or the operator's frustration crosses a
threshold.

**Discussed in**: post-P9 review conversation on 2026-05-25.

---

## Regression harness (fake Linear MCP + golden files)

**Idea**: a fake-MCP fixture + golden-file comparison + CI step running
a representative `/cadence:tick` flow across multiple Claude model
versions, so a prose change in [commands/tick.md](./commands/tick.md)
that silently changes dispatch behaviour gets caught before it lands.

**Why**: the current verification model is operator-run smoke tests
against a real Linear project, one fire at a time. That worked for
shipping the hardening phases but doesn't scale to "did the model
upgrade subtly change how step 10 dispatches?" or "did the prose edit
in step 5 break the cap walk for a candidate-state shape we don't
hit in normal traffic?" A fake MCP that records what the bootstrap
would have written to Linear, plus a stored expected-output file per
scenario, would close that gap.

**Why not now**: build it when (a) a real consumer beyond the author
exists, OR (b) a prose change in `tick.md` ships and silently breaks
something in production. Not before — the cost of the harness is
non-trivial and the bug rate doesn't currently justify it.

**Discussed in**: hardening-plan "Out of scope / future work" — moved
here on 2026-05-25 when HARDENING-PLAN.md was retired.

---

## Cost telemetry / token-budget reporting

**Idea**: a `--report-cost` flag (or always-on instrumentation) on
`/cadence:tick` that estimates token spend per fire, broken down by
subagent. The model returns approximate token counts via the Agent
tool result; a small script aggregates per-fire totals into the audit
log or a Linear comment.

**Why**: useful if cost becomes a complaint or a signal that a
subagent is running away (the kind of "29 tool calls on a no-op
ticket" scenario that surfaced during P8 Smoke V would be visible in
this telemetry).

**Why not now**: cost has not been a complaint. Not pre-emptively
necessary; instrument when an operator wants the visibility or when
the audit log gains durability (the
[durable audit log backlog item](#durable-audit-log-in-schedule-mode)
above) and cost lines fit naturally alongside the write log.

**Discussed in**: hardening-plan "Out of scope / future work" — moved
here on 2026-05-25.

---

## Multi-runner support (non-Claude subagents)

**Idea**: a `runner:` field on a workflow state that lets the state
invoke Codex (or another provider) instead of a Claude subagent.
Useful primarily for the
[adversarial reviewer leg of GUIDEPOSTS Principle 3](./GUIDEPOSTS.md)
— "ideally a different model or provider" — which Cadence currently
satisfies by model class (opus reviewing sonnet-class implementation)
but not by provider.

**Why not now**: a distinct architectural shift. The Claude Code
plugin model assumes Claude. Wiring a second provider's CLI / SDK
into the harness, mapping its return shape to the Markdown summary
Cadence expects, and reconciling permission scopes across providers
is substantial. Out of scope until the model-class adversarial leg
proves insufficient in practice.

**Discussed in**: hardening-plan "Out of scope / future work" — moved
here on 2026-05-25.

---

## Workflow visualization (`/cadence:graph`)

**Idea**: a `/cadence:graph` command that reads `.claude/workflow.yaml`
and emits a Mermaid flowchart of the configured workflow — agent
states as rounded rectangles, gates as diamonds, terminals as
stadiums, with edges labelled `next` / `on_approve` / `on_rework`.

**Why**: the default workflow's diagram is in
[README.md](./README.md), but a consumer who has customised their
workflow has no rendered view of their own state machine.
`/cadence:status` shows column counts, not topology.

**Why not now**: sugar. The workflow YAML fits on one screen by
design (per [GUIDEPOSTS.md](./GUIDEPOSTS.md) anti-goals), so a
contributor can read it directly in 30 seconds. Pick this up when
the workflow visibly grows past one screen or when an operator
specifically asks for it.

**Discussed in**: hardening-plan "Out of scope / future work" — moved
here on 2026-05-25.
