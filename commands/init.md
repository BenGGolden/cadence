---
description: Scaffold Cadence workflow config into the consumer repo — creates .claude/workflow.yaml, .claude/agents/*.md, .claude/prompts/global.md from plugin templates.
argument-hint: "[--force]"
disable-model-invocation: true
---

# /cadence:init

You are the Cadence scaffolder. Run exactly once and exit. Your job is to
populate the consuming repository's `.claude/` directory with the files
Cadence's bootstrap (`/cadence:tick`) expects to find on every fire.

The argument may be `--force` (case-insensitive) or absent. Capture it
before you begin. Treat any other argument as invalid input — print an
error and exit.

## Step 1 — Confirm working directory

You are running from the consumer's repository root. The plugin templates
live under `${CLAUDE_PLUGIN_ROOT}/templates/` and the agent templates under
`${CLAUDE_PLUGIN_ROOT}/agents/_template-{planner,implementer,reviewer}.md`.
Do NOT write anywhere outside the current working directory.

## Step 2 — Overwrite check

Check whether `.claude/workflow.yaml` already exists.

- **If it exists AND `--force` was NOT supplied**: print this and exit
  without writing anything:

  ```
  Cadence is already initialized: .claude/workflow.yaml exists.

  Re-run with --force to overwrite. This will replace:
    - .claude/workflow.yaml
    - .claude/prompts/global.md
    - .claude/agents/planner.md
    - .claude/agents/implementer.md
    - .claude/agents/reviewer.md
  ```

- **If it does not exist, or `--force` was supplied**: proceed to step 3.

## Step 3 — Create directories

Create (if not already present):

- `.claude/`
- `.claude/agents/`
- `.claude/prompts/`

## Step 4 — Copy templates

Use the Read tool to load each source file, then Write tool to create the
destination. Source paths use `${CLAUDE_PLUGIN_ROOT}` — substitute the
actual plugin root at runtime.

| Source                                                            | Destination                  |
|-------------------------------------------------------------------|------------------------------|
| `${CLAUDE_PLUGIN_ROOT}/templates/workflow.example.yaml`           | `.claude/workflow.yaml`      |
| `${CLAUDE_PLUGIN_ROOT}/templates/global-prompt.example.md`        | `.claude/prompts/global.md`  |
| `${CLAUDE_PLUGIN_ROOT}/agents/_template-planner.md`               | `.claude/agents/planner.md`  |
| `${CLAUDE_PLUGIN_ROOT}/agents/_template-implementer.md`           | `.claude/agents/implementer.md` |
| `${CLAUDE_PLUGIN_ROOT}/agents/_template-reviewer.md`              | `.claude/agents/reviewer.md` |

When copying the agent templates, **change the `name:` field in the
frontmatter** from `_template-planner` / `_template-implementer` /
`_template-reviewer` to `planner` / `implementer` / `reviewer`
respectively. The rest of the file is copied verbatim. The consumer's
`workflow.yaml` references these short names; the underscore-prefixed names
are reserved for the plugin's shipped templates.

If `--force` was supplied and a destination already exists, overwrite it.
If `--force` was NOT supplied (which means step 2 fell through because
`.claude/workflow.yaml` was absent), still avoid clobbering any of the
agent or prompt destinations that happen to exist already — print a warning
naming each one you skipped, but continue with the rest.

## Step 5 — Print next steps

After all writes succeed, print this verbatim:

```
Cadence initialised.

Files written:
  .claude/workflow.yaml
  .claude/prompts/global.md
  .claude/agents/planner.md
  .claude/agents/implementer.md
  .claude/agents/reviewer.md

Next steps:
  1. Edit .claude/workflow.yaml to point at your Linear team/project and
     set the Linear state names that map to each workflow stage. Every
     linear_state value must correspond to a real column on your Linear
     board.
  2. Edit .claude/prompts/global.md with the always-on instructions you
     want every Cadence subagent to receive (coding standards, repo
     conventions, secrets-handling rules).
  3. Tune .claude/agents/{planner,implementer,reviewer}.md — model, tools,
     and system prompt.
  4. Pick an invocation mode:
       • Remote: create a /schedule routine running /cadence:tick
         every minute, with Linear MCP and GH_TOKEN configured on it. Add
         a second routine for /cadence:sweep every 15 minutes.
       • Local: from an interactive Claude Code session in this repo, run
         `claude /loop 1m /cadence:tick` (after `gh auth login`).
  5. Smoke test with /cadence:tick dry-run before going live.

See the plugin README for the full Consumer Setup walkthrough.
```

## Errors

If any read or write fails, print which file failed and the error, then
exit. Partial scaffolding is acceptable — the consumer can re-run with
`--force` once they fix the underlying problem.

Never modify Linear, never invoke a subagent, never run shell commands
beyond what's needed to create directories and copy files.
