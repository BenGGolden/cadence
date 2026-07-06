# Cadence backlog

Ideas and follow-ups that aren't currently scheduled but should not be
lost. Pull items into a feature branch when they become priorities.

The design principles behind Cadence live in
[GUIDEPOSTS.md](./GUIDEPOSTS.md).

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

---

## PR operations for non-GitHub hosts (GitLab / Bitbucket)

**Scope**: GitHub PR operations run through the **GitHub MCP** connector —
the bootstrap creates/reads/merges PRs and the implementer only `git push`es.
This item covers **non-GitHub hosts only** — a GitLab- or Bitbucket-hosted
consumer has no equivalent connector path and currently gets
branch-pushed-but-no-MR.

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
- Validator rejects malformed `tools.pr_host` shapes (a new validator rule).

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

---

## Ancestor-walk for inherited issue context (beyond depth 1)

**Status**: single-parent (depth 1) **landed** — a fired issue with a parent
now inherits the parent's description as a Parent Context section in the
subagent prompt (see [compose_lifecycle_context.py](./templates/cadence/hooks/compose_lifecycle_context.py)
and [commands/tick.md](./commands/tick.md) step 8). This item is the remaining
extension.

**Idea**: walk more than one level up the parent chain, inheriting context from
grandparents and higher ancestors, not just the immediate parent.

**Shape sketch**:

- A configurable `ancestor_context_depth` knob (default 1, preserving today's
  behaviour) plus a total size budget across all inherited sources. (Per-source
  warn/fail on the immediate parent already exists — `PARENT_WARN_CHARS` /
  `PARENT_MAX_CHARS` in `compose_lifecycle_context.py`; the *total* budget across
  all inherited sources remains the open piece here.)
- Nearest-ancestor-closest rendering (the immediate parent rendered last /
  nearest the task), with a cycle guard.
- Per-section size budgeting so one large ancestor can't crowd out the rest.

**Why not now**: the actionable feature spec lives on the immediate parent;
higher ancestors trend toward roadmap/status prose, add sequential MCP
round-trips per level, and widen the silent-drift surface. Make depth a config
knob only if a real multi-level need appears. Pulling a Linear **project**
description as context was considered and rejected (human status prose,
mutable, unreviewed — a prompt-injection surface).

---

## Regression harness (fake Linear MCP + golden files)

**Idea**: a fake-MCP fixture + golden-file comparison + CI step running
a representative `/cadence:tick` flow across multiple Claude model
versions, so a prose change in [commands/tick.md](./commands/tick.md)
that silently changes dispatch behaviour gets caught before it lands.

**Why**: the current verification model is operator-run smoke tests
against a real Linear project, one fire at a time. That worked for
the initial build but doesn't scale to "did the model
upgrade subtly change how the Route step dispatches?" or "did the prose edit
in step 3 break the cap walk for a candidate-state shape we don't
hit in normal traffic?" A fake MCP that records what the bootstrap
would have written to Linear, plus a stored expected-output file per
scenario, would close that gap.

**Why not now**: build it when (a) a real consumer beyond the author
exists, OR (b) a prose change in `tick.md` ships and silently breaks
something in production. Not before — the cost of the harness is
non-trivial and the bug rate doesn't currently justify it.

