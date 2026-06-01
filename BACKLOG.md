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
[templates/workflow.yaml](./templates/workflow.yaml)
and [README.md](./README.md)) came out of the same conversation; this
one is the longer-horizon companion.

---

## Optional `merge` state between `review` and `done` — RESOLVED

Shipped (Unreleased) as the opt-in **`merge_on_approve`** gate field rather
than a new `merge` workflow state + `merger` subagent. A `gh pr merge` reads no
code and makes no judgment, so it is not agentic work; instead the bootstrap
runs it as a transition-coupled side-effect (read PR state, merge if open,
advance to the terminal — escalate to `cadence-needs-human` on failure or a
closed-unmerged PR), mirroring the AC-promotion precedent. No new state,
subagent, or Linear column. The companion "PR still open" status warning was
explicitly dropped (unreliable — the human may have merged manually). See
[CHANGELOG.md](./CHANGELOG.md) and the `merge_on_approve` section in
[README.md](./README.md).

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
`gh pr view`, [parse_comments.py](./templates/hooks/parse_comments.py)'s
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

## Regression harness (fake Linear MCP + golden files)

**Idea**: a fake-MCP fixture + golden-file comparison + CI step running
a representative `/cadence:tick` flow across multiple Claude model
versions, so a prose change in [commands/tick.md](./commands/tick.md)
that silently changes dispatch behaviour gets caught before it lands.

**Why**: the current verification model is operator-run smoke tests
against a real Linear project, one fire at a time. That worked for
shipping the hardening phases but doesn't scale to "did the model
upgrade subtly change how the Route step dispatches?" or "did the prose edit
in step 3 break the cap walk for a candidate-state shape we don't
hit in normal traffic?" A fake MCP that records what the bootstrap
would have written to Linear, plus a stored expected-output file per
scenario, would close that gap.

**Why not now**: build it when (a) a real consumer beyond the author
exists, OR (b) a prose change in `tick.md` ships and silently breaks
something in production. Not before — the cost of the harness is
non-trivial and the bug rate doesn't currently justify it.

**Discussed in**: hardening-plan "Out of scope / future work" — moved
here on 2026-05-25 when HARDENING-PLAN.md was retired.

