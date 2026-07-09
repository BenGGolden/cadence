#!/usr/bin/env python3
"""Enumerate candidate findings from a Cadence issue's comment list.

Caller:
  - commands/triage.md Step 2. The command writes the issue's `list_comments`
    result to `.cadence/comments.json`, runs this helper, and parses its JSON
    to build the candidate list `/cadence:triage` assesses. The helper makes
    **no** Linear write and **no** judgement — it deterministically surfaces
    what is in the comments; the command (with a human) decides which findings
    are real, which are already covered, and what to file.

What it surfaces:
  - **Reviewer findings**, parsed structurally from the reviewer subagent's
    output body (the `### Findings` block). This is the guaranteed-complete
    enumeration the command leads with.
  - **Planner + implementer output bodies**, returned verbatim so the command
    can scan them for concerns that are not reliably sectioned (planner "Risks /
    open questions", implementer caveats) — model-driven, not parsed here.
  - **Prior-triage markers** (`<!-- cadence:triage ... -->`), so a re-run can
    drop or flag findings an earlier triage already created / merged / dismissed.

Pairing:
  A subagent's returned Markdown is the non-cadence comment immediately
  following a `cadence:state` attempt marker (kind == "state", no "status")
  posted by the same author — the bootstrap posts the marker then the output
  back to back. A `cadence:warning` note may sit between the two (the
  oversized-parent warning); it is skipped, exactly as
  `parse_comments._find_implementer_summary` does. Unlike that function this
  pairing has **no** PR-URL condition, so it finds the planner and reviewer
  outputs too. The marker's `state` names the source (plan → planner,
  implement → implementer, agent_review → reviewer). The latest pairing per
  state wins — rework rounds re-emit and the newest overwrites.

CLI:
  python extract_findings.py --input PATH

  --input   path to the issue's comment list as a JSON array (what the Linear
            `list_comments` verb returns; connection-wrap shapes such as
            `{"nodes":[...]}` are tolerated via
            `parse_comments.coerce_comment_list`).

Output JSON (stdout, exit 0 always):
  {
    "reviewer":    {"present": bool, "createdAt": str|null,
                    "recommendation": str|null, "body": str|null,
                    "findings": [{"severity": str, "follow_up": bool,
                                  "location": str|null, "text": str}, ...]},
    "planner":     {"present": bool, "createdAt": str|null, "body": str|null},
    "implementer": {"present": bool, "createdAt": str|null, "body": str|null},
    "prior_triage": [{"createdAt": str, "created_ids": [...],
                      "merged_ids": [...], "raw": {...}|null}, ...],
    "parse_errors": [...]
  }

  A source with no paired output is `present: false` with null fields (and, for
  the reviewer, `findings: []`). Errors (unreadable input, non-array) surface in
  `parse_errors` — never as a non-zero exit; the command needs the data either
  way.
"""

import argparse
import io
import json
import re
import sys

from parse_comments import (
    coerce_comment_list,
    _classify,
    _is_cadence_comment,
    _is_context_warning,
    _extract_json_block,
    _author_name,
    _get,
)

# Force UTF-8 stdout with no newline translation so the (potentially non-ASCII)
# output is stable regardless of the parent locale (Windows defaults to cp1252
# + \r\n, which would corrupt bodies and break the golden test comparisons).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  newline="")

# A `cadence:state` attempt marker's `state` → the subagent source it pairs to.
_STATE_TO_SOURCE = {
    "plan": "planner",
    "implement": "implementer",
    "agent_review": "reviewer",
}

# The reviewer's `### Findings` heading (exact ### level, case-insensitive,
# trailing-space / \r tolerant via the pre-normalise below).
_FINDINGS_HEADING_RE = re.compile(r"^\s{0,3}###\s+findings\s*$", re.IGNORECASE)
# Any `##` / `###` heading — the block boundary that ends the Findings block.
_ANY_HEADING_RE = re.compile(r"^\s{0,3}#{2,3}\s+\S")
# A reviewer finding bullet. The `\[?...\]?` around the severity is
# load-bearing: it tolerates both the reviewer template's canonical bracketed
# form (`- **[minor]** ...`) and the bracket-less follow-up example
# (`- **minor** [follow-up] ...`). A regex that demanded bare `**minor**` would
# silently drop a `- **[minor]** ...` finding. The optional `[follow-up]` tag
# sits AFTER the closing `**`.
_FINDING_RE = re.compile(
    r"^\s*[-*]\s+\*\*\s*\[?\s*(blocking|major|minor)\s*\]?\s*\*\*\s*"
    r"(\[follow-up\])?\s*(.*)$",
    re.IGNORECASE,
)
# First back-ticked token in a finding's text — its `path:line` location.
_BACKTICK_RE = re.compile(r"`([^`]+)`")
# The recommendation banner: everything after `**Recommendation:` to EOL. The
# counts sit OUTSIDE the bold span (`**Recommendation: APPROVE** — 0 blocking,
# 2 major, 1 minor.`), so we grab to end-of-line and strip the `**` markers
# rather than capturing only the bold span (which would drop the counts).
_RECOMMENDATION_RE = re.compile(
    r"\*\*Recommendation:(.*)$", re.IGNORECASE | re.MULTILINE)


def _splitlines(text):
    """Split into lines tolerating CRLF and a missing trailing newline."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _normalise_comments(comments):
    """Reduce raw MCP comment dicts to sorted (oldest-first) normal form.

    Mirrors `parse_comments.parse_comment_list`'s normalise (id / body /
    createdAt / author, oldest-first) without changing its public output —
    this helper needs the author for the same-author pairing constraint."""
    norm = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        body = _get(c, "body", "content", default="")
        if not isinstance(body, str):
            body = str(body)
        created = _get(c, "createdAt", "created_at", "created", default="")
        norm.append({
            "id": _get(c, "id", "identifier", default=None),
            "body": body,
            "createdAt": created if isinstance(created, str) else str(created),
            "author": _author_name(c),
        })
    norm.sort(key=lambda x: x["createdAt"])
    return norm


def _pair_outputs(norm):
    """Pair each subagent output with its `cadence:state` attempt marker.

    Returns `{source: {"body": ..., "createdAt": ...}}` for whichever of
    planner / implementer / reviewer have a paired output. See the module
    docstring for the pairing rule. Latest-per-state wins (oldest-first
    iteration overwrites, so the newest pairing survives)."""
    paired = {}
    n = len(norm)
    for idx, c in enumerate(norm):
        kind, payload, _err = _classify(c["body"])
        if kind != "state" or not isinstance(payload, dict):
            continue
        if "status" in payload:
            # A failure / exit record, not an entry marker — no output pairs.
            continue
        source = _STATE_TO_SOURCE.get(payload.get("state"))
        if source is None:
            continue
        # Walk forward past any interleaved cadence:warning note(s).
        j = idx + 1
        while j < n and _is_context_warning(norm[j]["body"]):
            j += 1
        if j >= n:
            continue
        out = norm[j]
        if _is_cadence_comment(out["body"]):
            # The next comment is another tracking / cadence comment, not the
            # subagent's returned Markdown — this marker has no paired output.
            continue
        if out["author"] != c["author"]:
            continue
        paired[source] = {"body": out["body"], "createdAt": out["createdAt"]}
    return paired


def _parse_recommendation(body):
    """The reviewer's recommendation banner text, or None."""
    m = _RECOMMENDATION_RE.search(body)
    if m is None:
        return None
    text = m.group(1).replace("**", "").strip()
    return text or None


def _parse_findings(body):
    """Structured reviewer findings from the `### Findings` block, or []."""
    lines = _splitlines(body)
    start = None
    for i, line in enumerate(lines):
        if _FINDINGS_HEADING_RE.match(line):
            start = i + 1
            break
    if start is None:
        return []
    end = len(lines)
    for i in range(start, len(lines)):
        if _ANY_HEADING_RE.match(lines[i]):
            end = i
            break
    findings = []
    for line in lines[start:end]:
        m = _FINDING_RE.match(line)
        if m is None:
            continue
        rest = m.group(3).strip()
        loc_m = _BACKTICK_RE.search(rest)
        findings.append({
            "severity": m.group(1).lower(),
            "follow_up": bool(m.group(2)),
            "location": loc_m.group(1) if loc_m else None,
            "text": rest,
        })
    return findings


def _parse_prior_triage(norm):
    """Every `<!-- cadence:triage ... -->` marker, oldest-first."""
    out = []
    for c in norm:
        if not c["body"].lstrip().startswith("<!-- cadence:triage"):
            continue
        raw = None
        block = _extract_json_block(c["body"])
        if block is not None:
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict):
                    raw = parsed
            except (ValueError, TypeError):
                raw = None
        created = raw.get("created", []) if isinstance(raw, dict) else []
        merged = raw.get("merged", []) if isinstance(raw, dict) else []
        out.append({
            "createdAt": c["createdAt"],
            "created_ids": created if isinstance(created, list) else [],
            "merged_ids": merged if isinstance(merged, list) else [],
            "raw": raw,
        })
    return out


def _plain_source(paired, name):
    """A planner/implementer source block (present / createdAt / body)."""
    p = paired.get(name)
    if p is None:
        return {"present": False, "createdAt": None, "body": None}
    return {"present": True, "createdAt": p["createdAt"], "body": p["body"]}


def extract(comments, parse_errors=None):
    """Pure enumeration → the result dict the CLI prints. No I/O."""
    if parse_errors is None:
        parse_errors = []
    norm = _normalise_comments(comments)
    paired = _pair_outputs(norm)

    rp = paired.get("reviewer")
    if rp is None:
        reviewer = {"present": False, "createdAt": None,
                    "recommendation": None, "body": None, "findings": []}
    else:
        reviewer = {
            "present": True,
            "createdAt": rp["createdAt"],
            "recommendation": _parse_recommendation(rp["body"]),
            "body": rp["body"],
            "findings": _parse_findings(rp["body"]),
        }

    return {
        "reviewer": reviewer,
        "planner": _plain_source(paired, "planner"),
        "implementer": _plain_source(paired, "implementer"),
        "prior_triage": _parse_prior_triage(norm),
        "parse_errors": parse_errors,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True,
                    help="Path to the issue's comment list (JSON array).")
    args = ap.parse_args()

    parse_errors = []
    comments = []
    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        comments = coerce_comment_list(raw, parse_errors)
    except (OSError, ValueError) as e:
        parse_errors.append(f"could not read --input file: {e}")

    result = extract(comments, parse_errors=parse_errors)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
