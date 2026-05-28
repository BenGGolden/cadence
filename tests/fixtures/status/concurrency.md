## Cadence status — 2026-05-28T12:00:00Z

Team: **ENG**   Project: **cadence**   Pickup: **Todo**

### Issues in workflow

| ID | Title | Linear column | Workflow state | Attempt | Lock | Needs human | Verdict |
|----|-------|---------------|----------------|---------|------|-------------|---------|
| ENG-22 | A title | In Review | human_review (waiting) | 1 |  |  |  |
| ENG-21 | A title | In Review | human_review (waiting) | 1 |  |  |  |
| ENG-20 | A title | Plan Review | plan_review (waiting) | 1 |  |  |  |

### Per-state counts

- **plan** (`Planning`) — 0 issues
- **plan_review** (gate, `Plan Review`) — 1 issue
- **implement** (`Implementing`) — 0 issues
- **agent_review** (`Reviewing`) — 0 issues
- **human_review** (gate, `In Review`) — 2 issues
- **done** (`Done`) — 0 issues
- **(pickup)** (`Todo`) — 0 issues

### Concurrency

| State | In flight | Cap | Status |
|-------|-----------|-----|--------|
| plan | 0 | (none) |  |
| plan_review (gate) | 1 | 1 | AT CAP |
| implement | 0 | (none) |  |
| agent_review | 0 | (none) |  |
| human_review (gate) | 2 | 1 | OVER CAP |
| done (terminal) | 0 | n/a |  |

Read-only — no Linear writes performed.
