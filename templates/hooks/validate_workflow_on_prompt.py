#!/usr/bin/env python3
"""Cadence UserPromptSubmit hook: validate workflow.yaml before /cadence:tick.

Fires on every user prompt. If the prompt is `/cadence:tick` (with or without
arguments), this hook runs `validate_workflow.py` (sitting alongside this hook
in `.claude/hooks/`). A failure exits 2, which blocks the prompt and prints the
underlying error to stderr so the operator sees it before any Linear call.

Why this exists:
  `tick.md` step 3 already runs `validate_workflow.py` in-tick; this hook is a
  belt-and-braces second invocation at the prompt boundary. It catches bad
  configs half a second earlier with a clearer message, which matters most in
  /loop mode where the tick prose otherwise runs on the broken state for a
  short while before failing.

Behaviour:
  - Scope guard: no-op (exit 0) if `.claude/workflow.yaml` is absent.
  - Not a `/cadence:tick` prompt: no-op (exit 0).
  - validate_workflow.py exits 0: allow (exit 0).
  - validate_workflow.py exits non-zero: block (exit 2) with diagnostic.

Stdin payload (UserPromptSubmit):
  {"prompt": "..."}
"""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VALIDATE_SCRIPT = HERE / "validate_workflow.py"


def main():
    if not Path(".claude/workflow.yaml").is_file():
        sys.exit(0)

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        sys.exit(0)

    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        sys.exit(0)
    stripped = prompt.strip()
    if not stripped.startswith("/cadence:tick"):
        sys.exit(0)

    if not VALIDATE_SCRIPT.is_file():
        # The companion script was not scaffolded. Fail-open: the in-tick
        # check still runs.
        sys.exit(0)

    proc = subprocess.run(
        [sys.executable, str(VALIDATE_SCRIPT)],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        sys.exit(0)

    print(
        "Cadence: workflow.yaml validation failed; refusing to start tick.\n\n"
        f"{proc.stderr.strip()}\n\n"
        "Fix .claude/workflow.yaml and re-run /cadence:tick.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
