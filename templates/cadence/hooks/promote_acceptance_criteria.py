#!/usr/bin/env python3
"""Merge planner-proposed acceptance criteria into a Linear issue description.

Caller:
  - commands/tick.md Step 6 "Execute → invoke_subagent true" branch, in the
    "Promote proposed acceptance criteria" sub-phase, gated on
    `plan.promote_ac` (a gate-approve fire). The bootstrap writes the locked
    issue's current description to a scratch file, runs this helper, and — if
    `promote` is true — writes `new_description` back to the issue via the
    Linear MCP. The helper itself performs **no** Linear write; it only
    computes the new body (the bootstrap remains the sole Linear writer).

Why this exists:
  The planner no longer refuses AC-less tickets; it authors a
  `## Proposed Acceptance Criteria` section in its summary comment. When a
  human approves the plan at `plan_review`, the bootstrap promotes those
  proposed criteria into the issue description's `## Acceptance Criteria`
  block so the implementer (composed against the description) sees them.

  Promotion happens on approval, not at plan time, so a rework round leaves
  the description untouched and the re-running planner re-proposes freshly.
  The merge augments — it appends only proposed items not already present and
  never rewrites operator-authored AC — which makes it idempotent across
  repeated approve fires (a re-run finds every item already present and
  no-ops).

CLI:
  python promote_acceptance_criteria.py --comments <commentsFile> \
      --description-file <descFile>

  --comments          path to the issue's full comment list as a JSON array
                      (the same `.cadence/comments.json` the Route step
                      writes). Connection-wrap shapes are tolerated via
                      parse_comments.coerce_comment_list.
  --description-file  path to a file holding the issue's current description.

Output JSON (stdout, exit 0):
  { "promote": true, "new_description": "<full new description>",
    "added_count": 2, "reason": "..." }

  `promote` is false (and `new_description` null) when there is nothing to do
  (no proposed-AC comment, no checkbox items in it, or every proposed item is
  already present). `reason` is a short human-readable diagnostic.

Exit code: 0 always. Errors surface as `{promote: false, reason: ...}` on
stdout — the bootstrap needs the verdict either way.
"""

import argparse
import io
import json
import re
import sys

import parse_comments

# Force UTF-8 so the (potentially non-ASCII) new description is stable on
# stdout regardless of the parent locale (Windows defaults to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  newline="")

# A `## Proposed Acceptance Criteria` H2 (exact `##` level, case-insensitive
# on the words, tolerant of trailing spaces and \r).
_PROPOSED_RE = re.compile(
    r"^\s{0,3}##\s+proposed\s+acceptance\s+criteria\s*$",
    re.IGNORECASE,
)
# A `## Acceptance Criteria` H2 (same tolerances).
_AC_RE = re.compile(
    r"^\s{0,3}##\s+acceptance\s+criteria\s*$",
    re.IGNORECASE,
)
# Any `## ` H2 (used to find a block's end). Exact `##` level.
_ANY_H2_RE = re.compile(r"^\s{0,3}##\s+\S")
# A Markdown checkbox line: `- [ ]` / `- [x]`, capturing the trailing text.
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s*(.*)$")
# Leading `**AC-N**` (optionally followed by `—` / `:` / `-`) to strip when
# extracting the bare criterion text.
_AC_PREFIX_RE = re.compile(
    r"^\*\*\s*AC-?\d+\s*\*\*\s*(?:[—\-:.]\s*)?",
    re.IGNORECASE,
)


def _splitlines(text):
    """Split into lines, tolerating CRLF and a missing trailing newline.
    Preserves no line endings (re-joined with \\n by the renderer)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _checkbox_text(line):
    """Return the bare criterion text for a checkbox line, or None."""
    m = _CHECKBOX_RE.match(line)
    if m is None:
        return None
    text = m.group(1).strip()
    text = _AC_PREFIX_RE.sub("", text).strip()
    return text


def _normalise(text):
    """Lowercase + whitespace-collapse a criterion for dedupe comparison."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _block_bounds(lines, header_idx):
    """Given the index of an H2 line, return the [start, end) line range of
    the block body (the lines after the heading up to the next `## ` or EOF).
    `end` is exclusive."""
    end = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if _ANY_H2_RE.match(lines[i]):
            end = i
            break
    return header_idx + 1, end


def _extract_proposed_criteria(comments):
    """Return the list of proposed criterion strings from the newest comment
    containing a `## Proposed Acceptance Criteria` H2, or [] if none."""
    # Normalise + sort oldest-first (ISO-8601 sorts lexically), mirroring
    # parse_comments' normalisation so we scan in chronological order.
    norm = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        body = c.get("body")
        if body is None:
            body = c.get("content")
        if not isinstance(body, str):
            body = "" if body is None else str(body)
        created = c.get("createdAt")
        if created is None:
            created = c.get("created_at")
        if created is None:
            created = c.get("created")
        if not isinstance(created, str):
            created = "" if created is None else str(created)
        norm.append({"body": body, "createdAt": created})
    norm.sort(key=lambda x: x["createdAt"])

    # Scan newest-first; the newest proposal wins (rework rounds re-emit).
    for c in reversed(norm):
        lines = _splitlines(c["body"])
        header_idx = None
        for i, line in enumerate(lines):
            if _PROPOSED_RE.match(line):
                header_idx = i
                break
        if header_idx is None:
            continue
        start, end = _block_bounds(lines, header_idx)
        criteria = []
        for line in lines[start:end]:
            text = _checkbox_text(line)
            if text:
                criteria.append(text)
        if criteria:
            return criteria
    return []


def _merge(description, proposed):
    """Augment `description`'s AC block with the non-duplicate `proposed`
    items. Returns (new_description_or_None, added_count, reason)."""
    lines = _splitlines(description)

    # Locate the first `## Acceptance Criteria` H2 (first wins).
    ac_header_idx = None
    for i, line in enumerate(lines):
        if _AC_RE.match(line):
            ac_header_idx = i
            break

    if ac_header_idx is None:
        # No AC block at all → append a fresh one at EOF, numbered from AC-1.
        rendered = [f"- [ ] **AC-{n}** — {text}"
                    for n, text in enumerate(proposed, start=1)]
        base = description.rstrip("\n")
        new_block = "## Acceptance Criteria\n\n" + "\n".join(rendered)
        new_description = (base + "\n\n" + new_block + "\n") if base \
            else (new_block + "\n")
        return new_description, len(proposed), (
            f"appended new ## Acceptance Criteria block with "
            f"{len(proposed)} item(s)")

    start, end = _block_bounds(lines, ac_header_idx)

    # Parse existing checkbox lines: collect normalised text for dedupe, the
    # max existing AC number, and the index of the last checkbox line.
    existing_norms = set()
    max_n = 0
    last_checkbox_idx = None
    for i in range(start, end):
        text = _checkbox_text(lines[i])
        if text is None:
            continue
        last_checkbox_idx = i
        existing_norms.add(_normalise(text))
        nm = re.search(r"\bAC-?(\d+)\b", lines[i], re.IGNORECASE)
        if nm:
            n = int(nm.group(1))
            if n > max_n:
                max_n = n

    # Dedupe proposed against existing.
    surviving = [t for t in proposed if _normalise(t) not in existing_norms]
    if not surviving:
        return None, 0, "all proposed AC already present"

    rendered = [f"- [ ] **AC-{max_n + offset}** — {text}"
                for offset, text in enumerate(surviving, start=1)]

    if last_checkbox_idx is not None:
        insert_at = last_checkbox_idx + 1
    else:
        # No checkbox lines in the block (e.g. only a template hint). Insert
        # after the heading's trailing blank line, if any.
        insert_at = start
        if insert_at < end and lines[insert_at].strip() == "":
            insert_at += 1

    new_lines = lines[:insert_at] + rendered + lines[insert_at:]
    new_description = "\n".join(new_lines)
    # Preserve a trailing newline if the original had one.
    if description.endswith("\n") and not new_description.endswith("\n"):
        new_description += "\n"
    return new_description, len(surviving), (
        f"appended {len(surviving)} proposed AC item(s) after the existing "
        f"acceptance criteria")


def promote(comments, description):
    """Pure promotion decision. Returns the result dict the CLI prints."""
    proposed = _extract_proposed_criteria(comments)
    if not proposed:
        return {
            "promote": False,
            "new_description": None,
            "added_count": 0,
            "reason": "no planner ## Proposed Acceptance Criteria comment found",
        }

    new_description, added_count, reason = _merge(description, proposed)
    if new_description is None:
        return {
            "promote": False,
            "new_description": None,
            "added_count": 0,
            "reason": reason,
        }
    return {
        "promote": True,
        "new_description": new_description,
        "added_count": added_count,
        "reason": reason,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--comments", required=True,
                    help="Path to the issue's comment list (JSON array).")
    ap.add_argument("--description-file", required=True,
                    help="Path to a file holding the issue's current "
                         "description.")
    args = ap.parse_args()

    parse_errors = []
    comments = []
    try:
        with open(args.comments, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        comments = parse_comments.coerce_comment_list(raw, parse_errors)
    except (OSError, ValueError) as e:
        print(json.dumps({
            "promote": False, "new_description": None, "added_count": 0,
            "reason": f"could not read --comments file: {e}",
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    try:
        with open(args.description_file, "r", encoding="utf-8") as fh:
            description = fh.read()
    except OSError as e:
        print(json.dumps({
            "promote": False, "new_description": None, "added_count": 0,
            "reason": f"could not read --description-file: {e}",
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    result = promote(comments, description)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
