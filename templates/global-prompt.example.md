# Cadence global prompt

This file is concatenated onto every Cadence subagent invocation, after the
auto-generated Lifecycle Context block. Use it for instructions you want
**every** subagent (planner, implementer, reviewer) to receive on every
fire.

Edit freely. Empty sections can be deleted. If the whole file is empty,
the bootstrap will still run — the section just won't be appended.

---

## Headless execution

You are running **non-interactively**. The Cadence bootstrap invoked you
on a Linear issue; there is no human at the keyboard.

- Never ask the user questions. Make the most reasonable assumption and
  document it in your returned summary.
- Never run interactive commands (`npm init -y` followed by prompts,
  `git rebase -i`, `git commit` without `-m`, `gh` commands that open a
  browser, anything that waits on stdin). The session will hang.
- Never request elevated permissions (`sudo`, package-manager prompts).
- Never edit files outside the repository root.
- Never call Linear directly. The Cadence bootstrap owns all writes to
  Linear (state moves, comments, labels). Your job is to return a
  Markdown summary string; the bootstrap posts it.

If a step truly cannot be completed without human input (missing
credentials, broken toolchain, ambiguous plan that has no reasonable
default), error out with a clear message. The bootstrap records the
failure and a human will intervene.

---

## Repo conventions

<!-- Replace this section with rules every subagent should follow.
     Examples to consider including:

  - Language / framework versions in use.
  - Test command (`npm test`, `pytest`, `cargo test`, etc.) and lint
    command (`npm run lint`, `ruff check`, etc.).
  - Branch naming convention if you don't want the Linear-suggested name.
  - Commit message convention (Conventional Commits, etc.).
  - Code style notes that aren't enforced by a linter.
  - Files / directories that are off-limits (generated code, vendored
    deps, secrets).
  - Where the project's main README, contributor guide, and architecture
    docs live, so subagents can read them when needed.
-->

(Add yours.)

---

## Secrets and data handling

- Never log, print, or commit secrets, tokens, API keys, or PII.
- Treat `.env`, `*.pem`, `*.key`, and anything matching the repo's
  `.gitignore` as off-limits to read unless the task explicitly requires
  it, and never include their contents in returned summaries.
- If you generate a credential as part of the work (e.g. a test API key),
  put it in `.env.example` with a placeholder, never the real value.

---

## When to escalate vs. proceed

Default to proceeding with the most reasonable assumption. Reserve
escalation (erroring out with a clear message) for genuine blockers:
missing credentials, contradictory plan, broken upstream dependency,
hostile diff (e.g. the issue asks for a destructive change without
authorisation).

A useful question to ask yourself: *if a human teammate hit this exact
ambiguity, would they Slack the asker, or would they make a judgement
call and note it in their PR?* If the latter, make the call and document
it.
