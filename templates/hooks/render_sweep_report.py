#!/usr/bin/env python3
"""Render the /cadence:sweep summary report and classify stale vs fresh.

Caller(s):
  - commands/sweep.md step 4 (classification + report render)

Failure modes eliminated:
  - Time math drift: step 2 (cutoff = now - stale_after_minutes) and step 4
    (per-issue stale_minutes = floor((now - updated_at) / 60)) were inline
    prose; rounding and clamp-to-zero behaviour now lives in one place.
  - Report-shape drift: step 6 was ~30 lines of Markdown table templating,
    including the "(none cleared)" / "(none)" empty-table substitutions
    and the ascending-by-updatedAt ordering. Same shape every fire.
  - Title truncation drift: the ~60-char limit was prose; the script
    enforces it with a stable trailing `…`.

CLI:
  python render_sweep_report.py --input <path-to-input-json>

Input JSON shape (the bootstrap composes this from step 1's validator
output + step 3's MCP query):

  {
    "now": "2026-05-28T12:00:00Z",        // UTC ISO 8601
    "threshold_minutes": 30,              // limits.stale_after_minutes
    "locked_issues": [
      {
        "identifier": "ENG-1",
        "title": "...",
        "updated_at": "2026-05-28T11:00:00Z",
        "state_name": "Implementing"      // optional; carried for sweep
                                          // log lines, not rendered
      },
      ...
    ]
  }

Stdout: the full Markdown report — the `## Cadence sweep — <now>` block,
both `### Cleared` and `### Still locked` sections, with the "(none
cleared)" / "(none)" substitutions when a section is empty. Print verbatim.

Stderr: a JSON object with the classification, the per-issue stale_minutes,
and the stale issues the prose needs to iterate for step 5:

  {
    "cutoff": "2026-05-28T11:30:00Z",
    "stale": [
      {
        "identifier": "ENG-1",
        "title": "...",
        "updated_at": "...",
        "stale_minutes": 60,
        "state_name": "Implementing"
      },
      ...
    ],
    "fresh": [ ... same shape ... ]
  }

The dual-stream contract keeps stdout pure Markdown the prose just prints,
while still giving the prose a machine-readable list for the per-issue
MCP writes.

Exit codes:
  0  success
  1  bad / missing required input
"""

import argparse
import io
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _common import die

# Stdout includes the ellipsis character; on Windows the default stdout
# encoding is cp1252 and emitting it would crash. Force UTF-8.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  newline="")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  newline="")

TITLE_TRUNCATE = 60  # per sweep.md step 6 "title truncated to ~60 chars"


def _load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        die(f"Cadence: could not read {label} from {path}: {e}", 1)


def _parse_iso(value, label):
    """Parse an ISO 8601 timestamp. Accepts trailing 'Z' and fractional
    seconds. Returns a timezone-aware datetime in UTC."""
    if not isinstance(value, str) or not value:
        die(f"Cadence: {label} must be a non-empty ISO 8601 string.", 1)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        die(f"Cadence: could not parse {label} as ISO 8601: {value!r}.", 1)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_iso(dt):
    """Format a UTC datetime as ISO 8601 with trailing Z, second precision."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stale_minutes(now_dt, updated_dt):
    """floor((now - updated_at) / 60 seconds). Clamps to zero for
    future-dated updates (clock skew between MCP and the bootstrap)."""
    delta = (now_dt - updated_dt).total_seconds()
    if delta < 0:
        return 0
    return int(math.floor(delta / 60))


def _escape_cell(value):
    """Escape a Markdown table cell: collapse newlines, escape pipes."""
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = s.replace("|", "\\|")
    return s


def _truncate_title(title):
    if not isinstance(title, str):
        return ""
    flat = title.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if len(flat) <= TITLE_TRUNCATE:
        return flat
    return flat[:TITLE_TRUNCATE - 1] + "…"


def classify(now_dt, threshold_minutes, locked_issues):
    """Compute cutoff, classify each locked issue, attach stale_minutes."""
    cutoff_dt = now_dt - timedelta(minutes=threshold_minutes)
    stale = []
    fresh = []
    for issue in locked_issues:
        if not isinstance(issue, dict):
            continue
        identifier = issue.get("identifier")
        updated_at = issue.get("updated_at")
        if not isinstance(identifier, str) or not identifier:
            die("Cadence: each locked_issues entry needs an identifier.", 1)
        if not isinstance(updated_at, str) or not updated_at:
            die(f"Cadence: locked_issues[{identifier}] needs updated_at.", 1)
        updated_dt = _parse_iso(updated_at,
                                f"locked_issues[{identifier}].updated_at")
        record = {
            "identifier": identifier,
            "title": issue.get("title") or "",
            "updated_at": updated_at,
            "stale_minutes": _stale_minutes(now_dt, updated_dt),
            "state_name": issue.get("state_name") or "",
        }
        # Stale iff updated_at <= cutoff. The strict inequality on the
        # other side ensures the boundary issue (exactly at cutoff) is
        # treated as stale, matching sweep.md step 4's "updatedAt <= cutoff".
        if updated_dt <= cutoff_dt:
            stale.append(record)
        else:
            fresh.append(record)
    # Sort each list by updated_at ascending (oldest first) — matches the
    # prose's "updatedAt ascending" rule and gives the user a stable
    # reading order across runs.
    stale.sort(key=lambda r: r["updated_at"])
    fresh.sort(key=lambda r: r["updated_at"])
    return cutoff_dt, stale, fresh


def _render_header(now_iso, threshold_minutes, cutoff_iso, stale, fresh):
    total = len(stale) + len(fresh)
    return "\n".join([
        f"## Cadence sweep — {now_iso}",
        "",
        (f"- Threshold: **{threshold_minutes}** minutes "
         f"(cutoff {cutoff_iso})"),
        (f"- Locked issues found: **{total}**  "
         f"(stale: **{len(stale)}**, fresh: **{len(fresh)}**)"),
    ])


def _render_cleared(stale):
    lines = ["", "### Cleared", ""]
    if not stale:
        lines.append("(none cleared)")
        return "\n".join(lines)
    lines.append("| Identifier | Title | Last activity | Stale (min) |")
    lines.append("|------------|-------|---------------|-------------|")
    for r in stale:
        ident = _escape_cell(r["identifier"])
        title = _escape_cell(_truncate_title(r["title"]))
        updated = _escape_cell(r["updated_at"])
        stale_min = r["stale_minutes"]
        lines.append(f"| {ident} | {title} | {updated} | {stale_min} |")
    return "\n".join(lines)


def _render_still_locked(fresh):
    lines = ["", "### Still locked (fresh — below threshold)", ""]
    if not fresh:
        lines.append("(none)")
        return "\n".join(lines)
    lines.append("| Identifier | Title | Last activity |")
    lines.append("|------------|-------|---------------|")
    for r in fresh:
        ident = _escape_cell(r["identifier"])
        title = _escape_cell(_truncate_title(r["title"]))
        updated = _escape_cell(r["updated_at"])
        lines.append(f"| {ident} | {title} | {updated} |")
    return "\n".join(lines)


def render(payload):
    if not isinstance(payload, dict):
        die("Cadence: --input must parse to a JSON object.", 1)

    now_raw = payload.get("now")
    now_dt = _parse_iso(now_raw, "--input.now")
    now_iso = _format_iso(now_dt)

    threshold = payload.get("threshold_minutes")
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        die("Cadence: --input.threshold_minutes must be an integer.", 1)
    if threshold < 0:
        die("Cadence: --input.threshold_minutes must be >= 0.", 1)

    locked = payload.get("locked_issues")
    if locked is None:
        locked = []
    if not isinstance(locked, list):
        die("Cadence: --input.locked_issues must be an array.", 1)

    cutoff_dt, stale, fresh = classify(now_dt, threshold, locked)
    cutoff_iso = _format_iso(cutoff_dt)

    parts = [
        _render_header(now_iso, threshold, cutoff_iso, stale, fresh),
        _render_cleared(stale),
        _render_still_locked(fresh),
    ]
    report = "\n".join(parts) + "\n"

    classification = {
        "cutoff": cutoff_iso,
        "stale": stale,
        "fresh": fresh,
    }
    return report, classification


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True,
                    help="Path to the JSON input file (see module "
                         "docstring for shape).")
    args = ap.parse_args()

    payload = _load_json(args.input, "--input")
    report, classification = render(payload)

    sys.stdout.write(report)
    sys.stderr.write(json.dumps(classification, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
