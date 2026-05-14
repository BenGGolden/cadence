"""Shared helpers for the Cadence helper scripts.

Imported by validate_workflow.py, parse_comments.py, and
emit_tracking_comment.py. The hook scripts under templates/hooks/ deliberately
do NOT import this module — they stay self-contained so the consumer's hook
loader never has to resolve a relative path.

Exit-code convention (shared across all scripts):
  0  success
  1  bad input (unreadable / unparseable file, missing required arg)
  2  validation failure
  3  internal error (missing dependency, unexpected exception)
"""

import sys
from pathlib import Path

DEFAULT_WORKFLOW_PATH = Path(".claude/workflow.yaml")


def die(msg, code=1):
    """Print msg to stderr and exit with the given code."""
    print(msg, file=sys.stderr)
    sys.exit(code)


def load_workflow(path=None):
    """Read and yaml.safe_load the workflow file.

    On any failure (missing file, unreadable, invalid YAML, non-mapping
    root) print a clear message to stderr and exit. Returns the parsed dict.
    """
    try:
        import yaml
    except ImportError:
        die(
            "Cadence: PyYAML is required to read .claude/workflow.yaml "
            "(pip install pyyaml).",
            3,
        )

    p = Path(path) if path else DEFAULT_WORKFLOW_PATH
    if not p.is_file():
        die(f"Cadence: workflow file not found or not a regular file: {p}", 1)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        die(f"Cadence: could not read {p}: {e}", 1)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        die(f"Cadence: invalid YAML in {p}: {e}", 1)
    if not isinstance(data, dict):
        die(f"Cadence: {p} did not parse to a mapping (got {type(data).__name__}).", 1)
    return data
