#!/usr/bin/env python3
"""Compose the full subagent user prompt for a Cadence /cadence:tick fire.

Caller(s):
  - commands/tick.md step 8 (Lifecycle Context block + globalPrompt append)
  - commands/tick.md step 0 (dry-run rendering, via --dry-run)

Failure modes eliminated:
  - Templating drift: the Lifecycle Context block was ~140 lines of prose
    every fire re-derived. Prose edits silently changed the contract
    subagents depend on. The script renders deterministically.
  - Adversarial-context branch confusion: the prose described two
    Lifecycle Context shapes (default vs adversarial) interleaved with
    rework branches. The script picks one based on the target state's
    `adversarial_context` config field.
  - globalPrompt read: the prose had a separate step ("Read
    .claude/prompts/global.md") whose only consumer was step 8. The
    script does the read.

CLI:
  python compose_lifecycle_context.py
    [--workflow-config <validatorJson> | --workflow-path <workflow.yaml>]
    --issue <issueJsonPath>
    --target-state <name>
    --attempt <int>
    --parse-comments-output <parseCommentsOutputPath>
    [--rework]
    [--parent <parentJsonPath>]
    [--parent-max-chars <int>]
    [--global-prompt-path .claude/prompts/global.md]
    [--default-branch main]
    [--dry-run]

Stdout: the full subagent user prompt (Lifecycle Context block + two blank
lines + globalPrompt, when present). When the issue has a parent, the block
carries a Parent Context section (after Description, before Transitions)
holding the parent issue's inherited description. Pass it as the Agent tool's
`prompt`.

Exit codes:
  0  success
  1  bad / missing required input
"""

import argparse
import io
import json
import re
import sys
from pathlib import Path

from _common import die

import validate_workflow

# The Lifecycle Context block contains non-ASCII characters (→, —) that
# crash on Windows when stdout's default encoding is cp1252. Force UTF-8
# regardless of the parent process's locale.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  newline="")

DEFAULT_GLOBAL_PROMPT_PATH = Path(".claude/prompts/global.md")
DEFAULT_BASE_BRANCH = "main"

# Parent Context: cap the inherited parent description so a large epic body
# can't dominate the subagent prompt. 0 disables the cap.
DEFAULT_PARENT_MAX_CHARS = 4000
PARENT_TRUNCATION_MARKER = "_(parent description truncated)_"

# Linear priority numeric → label. Linear's API uses 0..4.
PRIORITY_LABELS = {
    0: "No priority",
    1: "Urgent",
    2: "High",
    3: "Medium",
    4: "Low",
}

DRY_RUN_ATTEMPT = 1
DRY_RUN_ISSUE = {
    "identifier": "EXAMPLE-1",
    "title": "Hypothetical entry-state issue",
    "url": "https://linear.app/example/issue/EXAMPLE-1",
    "branchName": "example/example-1-hypothetical-entry-state-issue",
    "priority": 3,
    "labels": [],
    "description": None,
}
DRY_RUN_PARENT = {
    "id": "EXAMPLE-9",
    "title": "Hypothetical epic this issue belongs to",
    "description": (
        "Shared spec for the epic. Every sub-issue inherits this context\n"
        "automatically, so it lives once on the parent instead of being\n"
        "repeated on each ticket.\n\n"
        "## Shared Acceptance Criteria\n"
        "- [ ] All widgets use the shared theme tokens."
    ),
}


def _load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        die(f"Cadence: could not read {label} from {path}: {e}", 1)


def _format_priority(value):
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "(none)"
    if isinstance(value, int):
        label = PRIORITY_LABELS.get(value)
        return f"{value} ({label})" if label else f"{value}"
    return str(value)


def _format_labels(labels):
    # Tolerate the GraphQL connection shape: {"nodes": [...]}
    if isinstance(labels, dict):
        labels = labels.get("nodes")
    if not isinstance(labels, list) or not labels:
        return "(none)"
    names = []
    for entry in labels:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("title")
            if isinstance(name, str) and name:
                names.append(name)
        elif isinstance(entry, str) and entry:
            names.append(entry)
    if not names:
        return "(none)"
    return ", ".join(names)


def _slugify_title(title):
    if not isinstance(title, str):
        return ""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title.lower())
    slug = slug.strip("-")
    if len(slug) > 50:
        slug = slug[:50].rstrip("-")
    return slug


def _derive_branch(issue, team_key):
    suggested = issue.get("branchName")
    if isinstance(suggested, str) and suggested:
        return suggested
    identifier = issue.get("identifier") or ""
    identifier = identifier.lower() if isinstance(identifier, str) else ""
    title_slug = _slugify_title(issue.get("title"))
    team = team_key.lower() if isinstance(team_key, str) else ""
    tail_parts = [p for p in (identifier, title_slug) if p]
    tail = "-".join(tail_parts) if tail_parts else ""
    if team and tail:
        return f"{team}/{tail}"
    return tail or team


def _resolve_next_state(states, target_state):
    target = states.get(target_state) if isinstance(states, dict) else None
    if not isinstance(target, dict):
        return None, None, None
    next_name = target.get("next")
    if not isinstance(next_name, str):
        return None, None, None
    next_body = states.get(next_name) if isinstance(states, dict) else None
    if not isinstance(next_body, dict):
        return next_name, None, None
    return next_name, next_body.get("type"), next_body.get("linear_state")


def _render_default_transitions(next_name, next_type, next_linear):
    lines = [f"- On success → **{next_name}** (Linear: \"{next_linear}\")"]
    if next_type == "gate":
        lines.append(
            f"- Gate downstream: human will see this in Linear column "
            f"\"{next_linear}\" and decide approve/rework."
        )
    elif next_type == "terminal":
        lines.append(
            f"- Terminal state: the bootstrap will close the workflow at "
            f"\"{next_linear}\"."
        )
    return "\n".join(lines)


def _render_adversarial_transitions(next_name, next_linear):
    return (
        f"- On success → **{next_name}** (Linear: \"{next_linear}\")\n"
        f"- Your output is a Markdown findings comment. The bootstrap will post\n"
        f"  it on the issue and move the issue to {next_name}."
    )


def _render_rework_section(target_state, rework_comments):
    header = (
        "### Rework Context\n"
        "\n"
        f"This is a **rework run** at state `{target_state}`. A previous submission was\n"
        "reviewed and sent back. Address the feedback below before resubmitting."
    )
    if not rework_comments:
        fallback = ("(No human comments were left when this issue was sent "
                    "back; address whatever you can infer from the prior "
                    "review and proceed.)")
        return f"{header}\n\n{fallback}"
    chunks = []
    for c in rework_comments:
        body = c.get("body") if isinstance(c, dict) else ""
        if not isinstance(body, str):
            body = str(body) if body is not None else ""
        author = c.get("author") if isinstance(c, dict) else "(unknown)"
        if not isinstance(author, str) or not author:
            author = "(unknown)"
        created = c.get("createdAt") if isinstance(c, dict) else ""
        if not isinstance(created, str):
            created = ""
        if body:
            quoted = "\n".join(f"> {line}" for line in body.splitlines())
        else:
            quoted = ">"
        chunks.append(f"{quoted}\n> — {author} at {created}")
    return f"{header}\n\n" + "\n\n".join(chunks)


def _render_parent_section(parent, max_chars):
    if not isinstance(parent, dict):
        return ""
    description = parent.get("description")
    if not isinstance(description, str) or not description.strip():
        return ""
    identifier = parent.get("identifier") or parent.get("id")
    title = parent.get("title")
    if isinstance(identifier, str) and identifier:
        label = f"{identifier} — {title}" if isinstance(title, str) and title \
            else identifier
    elif isinstance(title, str) and title:
        label = title
    else:
        label = "parent issue"
    body = description
    if isinstance(max_chars, int) and max_chars > 0 and len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n\n" + PARENT_TRUNCATION_MARKER
    return (
        "### Parent Context\n"
        "\n"
        f"This issue belongs to the parent issue **{label}**. The shared spec\n"
        "below is inherited from that parent — it frames the epic this work is\n"
        "part of. It is **not** the task itself; your task is described above.\n"
        "\n"
        f"{body}"
    )


FOOTER = (
    "### When Done\n"
    "\n"
    "Do the work described by your subagent definition. When you are finished, return\n"
    "a Markdown summary of:\n"
    "- What you changed (files, branch, PR URL if relevant).\n"
    "- What you verified (tests passed, lints clean, etc.).\n"
    "- Anything the next state will need.\n"
    "\n"
    "Do NOT post anything to Linear yourself. Do NOT modify Linear state. The\n"
    "bootstrap will handle those.\n"
    "\n"
    "<!-- END CADENCE LIFECYCLE -->"
)


def _render_description(value):
    if isinstance(value, str) and value.strip():
        return value
    return "No description provided."


def compose_block(*, issue, target_state, attempt, next_name, next_type,
                  next_linear, adversarial, rework, rework_comments,
                  branch, base_branch, pr_url, parent, parent_max_chars):
    parts = []
    parts.append("<!-- AUTO-GENERATED BY CADENCE — DO NOT EDIT -->")
    parts.append("")
    parts.append("## Lifecycle Context")
    parts.append("")
    identifier = issue.get("identifier") or ""
    title = issue.get("title") or ""
    parts.append(f"- **Issue:** {identifier} — {title}")
    parts.append(f"- **URL:** {issue.get('url') or ''}")
    parts.append(f"- **State:** {target_state}")
    parts.append(f"- **Attempt:** {attempt}")
    parts.append(f"- **Priority:** {_format_priority(issue.get('priority'))}")
    if adversarial:
        parts.append(f"- **Branch (under review):** {branch}")
        parts.append(f"- **Base branch:** {base_branch}")
        if pr_url:
            parts.append(f"- **PR:** {pr_url}")
    else:
        parts.append(f"- **Branch (Linear suggested):** {branch}")
        parts.append(f"- **Base branch:** {base_branch}")
    parts.append(f"- **Labels:** {_format_labels(issue.get('labels'))}")
    parts.append("")
    parts.append("### Description")
    parts.append("")
    parts.append(_render_description(issue.get("description")))
    parts.append("")
    parent_section = _render_parent_section(parent, parent_max_chars)
    if parent_section:
        parts.append(parent_section)
        parts.append("")
    parts.append("### Transitions")
    parts.append("")
    if adversarial:
        parts.append(_render_adversarial_transitions(next_name, next_linear))
    else:
        parts.append(_render_default_transitions(next_name, next_type, next_linear))
    if rework:
        parts.append("")
        parts.append(_render_rework_section(target_state, rework_comments or []))
    parts.append("")
    parts.append(FOOTER)
    return "\n".join(parts)


def _read_global_prompt(path):
    p = Path(path) if path else DEFAULT_GLOBAL_PROMPT_PATH
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workflow-config", default=None,
                    help="Path to a pre-built validator JSON dict (dry-run/tests).")
    ap.add_argument("--workflow-path", default=None,
                    help="Path to workflow.yaml; validated internally "
                         "(default: .claude/workflow.yaml).")
    ap.add_argument("--issue",
                    help="Path to a JSON file with the MCP issue object.")
    ap.add_argument("--target-state",
                    help="Workflow state name to compose for.")
    ap.add_argument("--attempt", type=int,
                    help="Attempt number for this fire.")
    ap.add_argument("--parse-comments-output",
                    help="Path to parse_comments.py's JSON output.")
    ap.add_argument("--rework", action="store_true",
                    help="Include the Rework Context section (this fire "
                         "entered via tick.md step 10c).")
    ap.add_argument("--parent", default=None,
                    help="Path to a JSON file with the parent issue's MCP "
                         "object. Optional; omitted when the issue has no "
                         "parent. Renders the Parent Context section.")
    ap.add_argument("--parent-max-chars", type=int,
                    default=DEFAULT_PARENT_MAX_CHARS,
                    help=f"Truncate the inherited parent description to this "
                         f"many chars (default: {DEFAULT_PARENT_MAX_CHARS}; "
                         f"0 disables truncation).")
    ap.add_argument("--global-prompt-path", default=None,
                    help=f"Path to the global prompt (default: "
                         f"{DEFAULT_GLOBAL_PROMPT_PATH}).")
    ap.add_argument("--default-branch", default=DEFAULT_BASE_BRANCH,
                    help=f"Base branch for adversarial-context fires "
                         f"(default: {DEFAULT_BASE_BRANCH}).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Render with EXAMPLE-1 placeholders for the "
                         "validator's entry state. Ignores --issue / "
                         "--parse-comments-output / --target-state / "
                         "--attempt.")
    args = ap.parse_args()

    config = validate_workflow.load_config(args.workflow_config, args.workflow_path)
    states = config.get("states") or {}

    if args.dry_run:
        target_state = config.get("entry_state_name")
        if not isinstance(target_state, str) or not target_state:
            die("Cadence: --dry-run requires the validator config to name "
                "an entry state.", 1)
        attempt = DRY_RUN_ATTEMPT
        issue = dict(DRY_RUN_ISSUE)
        rework = False
        rework_comments = []
        pr_url = None
        parent = dict(DRY_RUN_PARENT)
    else:
        missing = []
        if not args.issue:
            missing.append("--issue")
        if not args.target_state:
            missing.append("--target-state")
        if args.attempt is None:
            missing.append("--attempt")
        if not args.parse_comments_output:
            missing.append("--parse-comments-output")
        if missing:
            die(f"Cadence: required argument(s) missing: {', '.join(missing)}. "
                f"(Pass --dry-run for the entry-state placeholder render.)", 1)
        target_state = args.target_state
        attempt = args.attempt
        issue = _load_json(args.issue, "--issue")
        if not isinstance(issue, dict):
            die("Cadence: --issue must be a JSON object.", 1)
        pc_output = _load_json(args.parse_comments_output,
                               "--parse-comments-output")
        if not isinstance(pc_output, dict):
            die("Cadence: --parse-comments-output must be a JSON object.", 1)
        rework = args.rework
        rework_comments = pc_output.get("rework_context") or []
        if not isinstance(rework_comments, list):
            rework_comments = []
        summary = pc_output.get("latest_implementer_summary") or {}
        pr_url = summary.get("pr_url") if isinstance(summary, dict) else None
        parent = None
        if args.parent:
            parent = _load_json(args.parent, "--parent")
            if not isinstance(parent, dict):
                die("Cadence: --parent must be a JSON object.", 1)

    target_body = states.get(target_state) if isinstance(states, dict) else None
    adversarial = bool(
        isinstance(target_body, dict)
        and target_body.get("adversarial_context") is True
    )
    next_name, next_type, next_linear = _resolve_next_state(states, target_state)

    team_key = ""
    linear_block = config.get("linear")
    if isinstance(linear_block, dict):
        team = linear_block.get("team")
        if isinstance(team, str):
            team_key = team
    branch = _derive_branch(issue, team_key)

    block = compose_block(
        issue=issue,
        target_state=target_state,
        attempt=attempt,
        next_name=next_name,
        next_type=next_type,
        next_linear=next_linear,
        adversarial=adversarial,
        rework=rework,
        rework_comments=rework_comments,
        branch=branch,
        base_branch=args.default_branch,
        pr_url=pr_url,
        parent=parent,
        parent_max_chars=args.parent_max_chars,
    )

    global_prompt = _read_global_prompt(args.global_prompt_path)

    sys.stdout.write(block)
    if global_prompt:
        sys.stdout.write("\n\n\n")
        sys.stdout.write(global_prompt)
    else:
        sys.stdout.write("\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
