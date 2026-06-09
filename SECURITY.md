# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue.

- Preferred: [open a private vulnerability report](https://github.com/BenGGolden/cadence/security/advisories/new)
  via GitHub Security Advisories.
- Alternative: email **ben@bgolden.io**.

Include enough detail to reproduce (affected version, configuration, and the
behaviour observed). This is a small, solo-maintained project — expect an
initial response within a couple of weeks, and please allow time for a fix
before any public disclosure.

## Supported versions

Cadence is pre-1.0; only the latest release receives fixes. Pin a version you
have reviewed, and read the [CHANGELOG](./CHANGELOG.md) before upgrading.

## What to know before running Cadence

Cadence drives **autonomous Claude Code agents** against your repository and
your Linear board. Understand the trust model before installing it:

- **It acts with the access you grant it.** Cadence has no credentials of its
  own. It operates through the Linear MCP server and the bound GitHub
  repository connector you configure, plus the local tools you pre-allow. It
  can do whatever those grants permit — read code, post Linear comments, push
  branches, open and (with the opt-in `merge_on_approve` gate) **merge pull
  requests**. Grant only what your workflow needs.
- **Least privilege is the design stance.** The shipped templates pre-allow the
  minimum tool surface, and the docs recommend the narrow set, not a
  bulk-allow. See [Required permissions](./README.md#required-permissions) for
  the exact Linear/GitHub/local allowlists and how to tune them.
- **Subagents read; the bootstrap writes.** Subagents inspect code and return a
  Markdown summary; only the bootstrap performs Linear and GitHub writes. This
  keeps the write surface small and auditable — Linear's own activity history
  is the durable record of every change Cadence made.
- **Humans approve at gates.** Cadence is built to pause for human verdicts
  rather than run unattended end-to-end. Keep gates in your workflow for any
  step whose output you would not merge unreviewed.
- **Unattended `/schedule` routines cannot answer prompts.** A remote routine
  runs with whatever you pre-allowed and has no human in the loop for a
  permission prompt. Scope a routine's connectors and allowlist to exactly the
  board and repository it should touch.

Treat the issues Cadence acts on as untrusted input: a ticket body or comment
is text an agent will read and may act on. Don't put secrets in tickets, and
review what your gates let through.
