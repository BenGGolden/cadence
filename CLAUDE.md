# CLAUDE.md

Orientation for an agent working **on the Cadence plugin itself**. For how
Cadence behaves once installed, read [README.md](./README.md).

## What Cadence is

A Claude Code plugin that turns Linear into a multi-agent workflow runner.
Issues flow through a state machine; subagents do the work; humans approve at
gates. There is no daemon — each tick is one shot, fired by `/schedule` or
`/loop`.

## Repo map

- `commands/` — the four slash commands (`tick`, `init`, `sweep`, `status`).
  Each `.md` is **dispatch prose the harness executes**, not documentation.
- `templates/` — files `/cadence:init` scaffolds into a consumer repo:
  `workflow.example.yaml`, `global-prompt.example.md`, `agents/*.md`.
- `.claude-plugin/plugin.json` — plugin manifest (name, version, metadata).
- `.github/workflows/validate.yml` — CI: manifest schema + command frontmatter.
- Root docs: `README.md` (operational shape), `GUIDEPOSTS.md` (design
  principles / the *why*), `HARDENING-PLAN.md` (active roadmap), `SMOKE.md`
  (how it's validated), `MIGRATION.md` (Stokowski → Cadence), `CHANGELOG.md`.

## Load-bearing invariants — do not break these

- **`commands/tick.md` prose IS the dispatch logic.** Editing it is a spec
  change, not a doc edit. Step ordering and wording are behavior.
- **The bootstrap is the sole Linear writer.** Subagents read code, make
  changes, and return a Markdown string; the bootstrap posts it verbatim.
- **Linear column ↔ workflow state is 1:1.** No aliasing.
- **`commands/*.md` are invoked independently** as slash commands and must
  stay self-contained — do not factor shared prose into a file a command
  would have to `Read` at runtime.
- **Plugin owns logic, consumer owns config.** Don't bake consumer-specific
  assumptions into `commands/` or `templates/`.

## Conventions

- Commit messages: conventional-commit style, no AI-attribution trailer
  (match the existing `git log`).
- Every file in `commands/` needs YAML frontmatter with a `description` and
  `disable-model-invocation: true` — CI enforces this.
- `templates/` files are examples the consumer edits and commits; `commands/`
  files are the plugin's own executable prose. Keep the boundary clean.

## Where to look

| Need | Doc |
|---|---|
| How the system behaves when installed | `README.md` |
| Why it's designed this way | `GUIDEPOSTS.md` |
| What's being hardened next, and how | `HARDENING-PLAN.md` |
| How a release is validated | `SMOKE.md` |
