"""Shared helpers for the Cadence dispatch-prose helpers.

Imported by validate_workflow.py and emit_tracking_comment.py (siblings in
templates/cadence/hooks/). The two event-hook scripts in this same directory
(validate_tracking_json.py, validate_workflow_on_prompt.py) deliberately do
NOT import this module — they stay self-contained so the consumer's hook
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

# Cadence's per-repo scratch / state directory. Holds the dispatch prose's
# transient JSON (validator output, comment lists, candidate lists, composed
# issue objects). Self-ignoring via a `.gitignore` of `*` so consumers never
# commit scratch — see ensure_cadence_dir().
CADENCE_DIR = Path(".cadence")
_CADENCE_GITIGNORE = CADENCE_DIR / ".gitignore"


def die(msg, code=1):
    """Print msg to stderr and exit with the given code."""
    print(msg, file=sys.stderr)
    sys.exit(code)


def ensure_cadence_dir():
    """Create `.cadence/` and its self-ignoring `.gitignore` if absent.

    Idempotent. Ensures the scratch directory is git-ignored even on
    read-only paths (e.g. a `/cadence:tick --dry-run`, which writes only
    transient JSON). Returns the directory path. Best-effort: filesystem
    errors are swallowed — scratch placement must never break a fire."""
    try:
        CADENCE_DIR.mkdir(parents=True, exist_ok=True)
        if not _CADENCE_GITIGNORE.is_file():
            _CADENCE_GITIGNORE.write_text("*\n", encoding="utf-8")
    except OSError:
        pass
    return CADENCE_DIR


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
