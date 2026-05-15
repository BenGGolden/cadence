#!/usr/bin/env python3
"""Deterministically parse a Linear issue's comment list for Cadence.

Caller(s):
  - commands/tick.md step 9  (drift check — latest tracking comment)
  - commands/tick.md step 10c.1 (rework_count)
  - commands/tick.md step 10c.3 (rework_context)
  - commands/tick.md step 11 (attempt_count)
  - commands/status.md (per-issue attempt count / last state)

Failure modes eliminated:
  - "Counting errors": LLM bookkeeping in tick.md steps 10c.1 and 11 ("count
    prior attempt markers" / "count prior rework comments") drifts under
    context pressure. This script counts deterministically.
  - "JSON parsing errors": reading tracking-comment JSON back out of Linear
    comments in prose is fragile; this does it once, in code.

Legacy compatibility:
  Both `<!-- cadence:` and `<!-- stokowski:` prefixes are accepted. When
  parsing a stokowski: payload, `run` is treated as `attempt` and
  `timestamp` as `started_at`. Legacy comments are read, never rewritten.

CLI:
  python parse_comments.py --input PATH --target-state STATE [--gate-name STATE]

  --input         path to a temp file holding the issue's full comment list
                  as a JSON array. Each element is an object with id / body /
                  createdAt / user keys; camelCase and snake_case are both
                  tolerated since MCP vendors vary.
  --target-state  workflow state name being counted against (attempt_count).
  --gate-name     gate name for rework_count / rework_context; omit if not
                  in a gate context.

Exit code: 0 always. Errors surface as structured JSON on stdout (in
`parse_errors`), not as exit codes — the bootstrap needs the data to make
decisions either way.
"""

import argparse
import json
import sys

TRACKING_KINDS = ("state", "gate", "reconcile")
# A "tracking comment" for the latest-comment / boundary purposes.
TRACKING_KIND_SET = set(TRACKING_KINDS)


def _get(d, *keys, default=None):
    """First non-None value among keys (camelCase/snake_case tolerance)."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _author_name(comment):
    u = _get(comment, "user", "author", "creator", "createdBy", "created_by")
    if isinstance(u, dict):
        return _get(u, "displayName", "display_name", "name", "email",
                    default="(unknown)")
    if isinstance(u, str) and u.strip():
        return u
    return "(unknown)"


def _is_bot(comment):
    u = _get(comment, "user", "author", "creator", "createdBy", "created_by")
    if isinstance(u, dict):
        if u.get("isBot") or u.get("is_bot"):
            return True
    return False


def _is_cadence_comment(body):
    """True for any Cadence- or Stokowski-generated comment (state/gate/
    reconcile/sweep). Used to exclude bot comments from rework_context."""
    s = body.lstrip()
    return s.startswith("<!-- cadence:") or s.startswith("<!-- stokowski:")


def _extract_json_block(s):
    """Return the substring of the first balanced {...} block, or None.
    String-aware so braces inside JSON string values don't break matching."""
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
    return None


def _classify(body):
    """Return (kind, payload_dict, error_str).

    kind is one of 'state' / 'gate' / 'reconcile' for a tracking comment,
    or None if the body is not a state/gate/reconcile tracking comment.
    error_str is set only when the comment looks like a tracking comment
    but its JSON failed to parse.
    """
    s = body.lstrip()
    kind = None
    for k in TRACKING_KINDS:
        if s.startswith(f"<!-- cadence:{k}") or s.startswith(f"<!-- stokowski:{k}"):
            kind = k
            break
    if kind is None:
        return None, None, None

    block = _extract_json_block(s)
    if block is None:
        return kind, None, "no JSON object found in tracking comment"
    try:
        payload = json.loads(block)
    except (ValueError, TypeError) as e:
        return kind, None, f"JSON parse error: {e}"
    if not isinstance(payload, dict):
        return kind, None, "tracking-comment JSON is not an object"

    # Legacy stokowski normalisation.
    if "run" in payload and "attempt" not in payload:
        payload["attempt"] = payload["run"]
    if "timestamp" in payload and "started_at" not in payload:
        payload["started_at"] = payload["timestamp"]
    return kind, payload, None


def _coerce_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True,
                    help="Path to a JSON file holding the issue's comment list.")
    ap.add_argument("--target-state", required=True,
                    help="Workflow state name to count attempt markers for.")
    ap.add_argument("--gate-name", default=None,
                    help="Gate name for rework_count / rework_context.")
    args = ap.parse_args()

    parse_errors = []
    comments = []
    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            # Some MCP shapes wrap the array, e.g. {"comments": [...]} / {"nodes": [...]}.
            raw = _get(raw, "comments", "nodes", "data", "items", default=raw)
        if isinstance(raw, list):
            comments = raw
        else:
            parse_errors.append("input did not parse to a JSON array of comments")
    except (OSError, ValueError) as e:
        parse_errors.append(f"could not read --input file: {e}")

    # Normalise + sort oldest-first by createdAt (ISO-8601 sorts lexically).
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
            "is_bot": _is_bot(c),
        })
    norm.sort(key=lambda x: x["createdAt"])

    latest_tracking = None
    latest_tracking_idx = -1
    attempt_count = 0
    rework_count = 0

    for idx, c in enumerate(norm):
        kind, payload, err = _classify(c["body"])
        if kind is None:
            continue
        if err is not None:
            parse_errors.append(f"comment {c['id']}: {err}")
            # Still treat it as the boundary for rework_context purposes.
            latest_tracking_idx = idx
            continue

        latest_tracking_idx = idx
        latest_tracking = {
            "kind": kind,
            "state": payload.get("state"),
            "attempt": _coerce_int(payload.get("attempt")),
            "status": payload.get("status"),
            "raw_json": payload,
        }

        if kind == "state":
            if payload.get("state") == args.target_state and "status" not in payload:
                attempt_count += 1
        elif kind == "gate" and args.gate_name is not None:
            if payload.get("state") == args.gate_name and payload.get("status") == "rework":
                rework_count += 1

    # rework_context: comments after the most recent tracking comment, that
    # are not themselves Cadence/Stokowski comments, oldest-first, best-effort
    # human-only.
    rework_context = []
    if latest_tracking_idx >= 0:
        for c in norm[latest_tracking_idx + 1:]:
            if _is_cadence_comment(c["body"]):
                continue
            if c["is_bot"]:
                continue
            rework_context.append({
                "body": c["body"],
                "author": c["author"],
                "createdAt": c["createdAt"],
            })

    if latest_tracking is None:
        latest_tracking = {
            "kind": None,
            "state": None,
            "attempt": None,
            "status": None,
            "raw_json": None,
        }

    result = {
        "latest_tracking_comment": latest_tracking,
        "attempt_count": attempt_count,
        "rework_count": rework_count,
        "rework_context": rework_context,
        "parse_errors": parse_errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
