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
than a new `merge` workflow state + `merger` subagent. A PR merge reads no
code and makes no judgment, so it is not agentic work; instead the bootstrap
runs it as a transition-coupled side-effect (read PR state, merge if open,
advance to the terminal — escalate to `cadence-needs-human` on failure or a
closed-unmerged PR), mirroring the AC-promotion precedent. No new state,
subagent, or Linear column. The companion "PR still open" status warning was
explicitly dropped (unreliable — the human may have merged manually). See
[CHANGELOG.md](./CHANGELOG.md) and the `merge_on_approve` section in
[README.md](./README.md).

---

## PR operations for non-GitHub hosts (GitLab / Bitbucket)

**Status update**: GitHub PR operations are no longer `gh`-based. The
bootstrap now creates/reads/merges PRs via the **GitHub MCP** connector
(see CHANGELOG "PR operations via GitHub MCP"), and the implementer only
`git push`es. So this item is narrowed to **non-GitHub hosts only** — a
GitLab- or Bitbucket-hosted consumer has no equivalent connector path and
currently gets branch-pushed-but-no-MR.

**Idea**: route the bootstrap's PR create/read/merge to a host other than
GitHub when the consumer's remote isn't GitHub — either via that host's own
MCP connector (preferred, mirroring the GitHub path) or, failing that, a
configured CLI (`glab` / `bb`).

**Why**: a GitLab-hosted consumer running Cadence today has the implementer
push the branch, but the bootstrap's GitHub MCP `create_pull_request` won't
target GitLab. The branch lands; the MR doesn't. Everything downstream that
uses the PR URL — [parse_comments.py](./templates/cadence/hooks/parse_comments.py)'s
`latest_implementer_summary.pr_url`, the adversarial Lifecycle Context's
`PR:` line — has nothing to surface (the reviewer's `git diff` still works
but loses platform-side context: reviewers, conversation, CI status).

**Shape sketch**:

- An optional `tools.pr_host` (or `platforms:`) block selecting the host /
  connector the bootstrap's PR sub-phases target.
- The bootstrap's create / list / read / merge sub-phases resolve the
  host's MCP tool names (or a CLI invocation) from that config instead of
  assuming GitHub.
- `parse_comments`'s PR-URL regex generalises beyond `github.com/.../pull/N`
  to the host's MR/PR URL shape.
- Validator rejects malformed `tools.pr_host` shapes (P1.1-style rule).

**Open questions**:

- Do the major non-GitHub hosts expose MCP connectors with comparable
  create/merge tools? If yes, the GitHub path generalises cleanly; if not,
  a CLI fallback reintroduces the setup-script fragility we just removed.
- Does this overlap with the [Linear OAuth app](#linear-oauth-app-cadence-as-a-first-class-integration)
  backlog item — should the per-platform PR host live in a `platforms:`
  block alongside the Linear config?

**Why not now**: no consumer has hit this. Every current user is on GitHub,
which the MCP path now covers end-to-end. Pick this up when a
GitLab/Bitbucket operator surfaces the gap, or when host indirection becomes
part of a broader platforms refactor.

**Discussed in**: post-P9 review conversation on 2026-05-25 —
surfaced when checking Rule B's wording in the implementer template.

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

