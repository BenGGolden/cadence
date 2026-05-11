---
description: Cadence human-facing status view — prints a Markdown table of issues in the workflow. (Session A stub; full body lands in Session C.)
disable-model-invocation: true
---

# /cadence-status (Session A stub)

The read-only status reporter is implemented in **Session C** of the build
plan. This stub is a placeholder so the slash command exists and the plugin
loads.

Print verbatim, then stop:

```
TODO: /cadence:cadence-status is not yet implemented.

This command will query Linear for all issues currently in workflow states,
render a Markdown table (Identifier | Title | State | Attempt | Locked? |
Needs Human?), and print summary counts. Lands in Session C per PLAN.md
("/cadence-status semantics").
```

Do not call any tools. Exit immediately after printing.
