---
description: Cadence dispatch tick — runs one workflow step against the next eligible Linear issue. (Session A stub; full body lands in Session B.)
disable-model-invocation: true
---

# /cadence-tick (Session A stub)

This is the bootstrap heart of the Cadence plugin. The full dispatch prose
(workflow validation, Linear pickup, soft lock, state routing, subagent
invocation, tracking comments, lock release) is implemented in **Session B**
of the build plan and is not yet present.

For now, this command does nothing except print this notice and exit.

Print verbatim, then stop:

```
TODO: /cadence:cadence-tick is not yet implemented.

This is the Session A scaffolding stub. The full dispatch logic lands in
Session B per PLAN.md ("Bootstrap prompt" section). No Linear queries are
made, no labels are added, no subagents are invoked.

If you scheduled this command via /schedule or /loop, the routine is wired
up correctly — it just has no body yet. Disable the routine until Session B
is complete, or leave it running (every fire is a no-op).
```

Do not call any tools. Do not read Linear MCP. Do not write to any file.
Exit immediately after printing.
