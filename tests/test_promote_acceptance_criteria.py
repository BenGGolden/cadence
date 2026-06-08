"""Integration tests for templates/cadence/hooks/promote_acceptance_criteria.py.

Invoked via subprocess (matching test_route_fire.py's style) so the full
argparse + I/O + `import parse_comments` path runs on every case. The helper
merges planner-proposed acceptance criteria into an issue description; these
cases pin its idempotency, augment-when-gaps, and block-boundary behaviour.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "cadence" / "hooks" / "promote_acceptance_criteria.py"


def _comment(body, t="2026-05-01T00:00:00Z", user="Bot"):
    return {"id": f"c-{t}", "body": body, "createdAt": t,
            "user": {"displayName": user}}


def _proposal(items, t="2026-05-01T00:00:00Z"):
    lines = ["## Proposed Acceptance Criteria", ""]
    for n, text in enumerate(items, start=1):
        lines.append(f"- [ ] **AC-{n}** — {text}")
    body = "## Plan\n\nSome plan prose.\n\n" + "\n".join(lines) + "\n"
    return _comment(body, t=t)


def _run(comments, description):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        com = td / "comments.json"
        desc = td / "description.md"
        com.write_text(json.dumps(comments), encoding="utf-8")
        desc.write_text(description, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--comments", str(com),
             "--description-file", str(desc)],
            capture_output=True, text=True, encoding="utf-8")
        return r


class PromoteACTests(unittest.TestCase):

    # ---------- AC-1: no AC in description, proposal present ----------

    def test_no_ac_appends_block_at_eof(self):
        comments = [_proposal(["POST /users returns 201",
                               "Invalid payload returns 400"])]
        description = "## Context\n\nWe need a users endpoint.\n"
        r = _run(comments, description)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["promote"])
        self.assertEqual(out["added_count"], 2)
        nd = out["new_description"]
        self.assertIn("## Acceptance Criteria", nd)
        self.assertIn("- [ ] **AC-1** — POST /users returns 201", nd)
        self.assertIn("- [ ] **AC-2** — Invalid payload returns 400", nd)
        # Original content preserved, AC block at the end.
        self.assertTrue(nd.startswith("## Context"))
        self.assertLess(nd.index("## Context"), nd.index("## Acceptance"))

    # ---------- AC-2: idempotent ----------

    def test_idempotent_second_run_is_noop(self):
        comments = [_proposal(["POST /users returns 201"])]
        description = "## Context\n\nUsers endpoint.\n"
        first = json.loads(_run(comments, description).stdout)
        self.assertTrue(first["promote"])
        # Feed the new description back in with the same proposal.
        second = json.loads(_run(comments, first["new_description"]).stdout)
        self.assertFalse(second["promote"])
        self.assertEqual(second["added_count"], 0)
        self.assertIsNone(second["new_description"])

    # ---------- AC-3: augment existing operator AC ----------

    def test_augment_appends_only_new_items(self):
        description = (
            "## Context\n\nExisting work.\n\n"
            "## Acceptance Criteria\n\n"
            "- [ ] **AC-1** — Endpoint exists\n"
            "- [ ] **AC-2** — Returns JSON\n\n"
            "## Notes\n\nSome trailing prose.\n"
        )
        # One duplicate (matches AC-2 normalised) + one genuinely new.
        comments = [_proposal(["returns json", "Rate limited to 100/min"])]
        r = _run(comments, description)
        out = json.loads(r.stdout)
        self.assertTrue(out["promote"])
        self.assertEqual(out["added_count"], 1)
        nd = out["new_description"]
        # New item numbered AC-3, inserted after the last existing checkbox.
        self.assertIn("- [ ] **AC-3** — Rate limited to 100/min", nd)
        # Operator lines unchanged byte-for-byte.
        self.assertIn("- [ ] **AC-1** — Endpoint exists\n", nd)
        self.assertIn("- [ ] **AC-2** — Returns JSON\n", nd)
        # The duplicate was not re-added.
        self.assertEqual(nd.count("Returns JSON"), 1)
        self.assertNotIn("AC-4", nd)
        # Trailing ## Notes section intact, after the new AC line.
        self.assertIn("## Notes", nd)
        self.assertLess(nd.index("AC-3"), nd.index("## Notes"))

    # ---------- AC-4: no proposal comment ----------

    def test_no_proposal_comment_is_noop(self):
        comments = [_comment("## Plan\n\nJust a plan, no proposed AC.\n")]
        description = "## Context\n\nNo AC here either.\n"
        r = _run(comments, description)
        out = json.loads(r.stdout)
        self.assertFalse(out["promote"])
        self.assertEqual(out["added_count"], 0)
        self.assertIsNone(out["new_description"])

    # ---------- multiple ## Acceptance Criteria H2s: first wins ----------

    def test_first_ac_block_wins_boundary_stops_at_next_h2(self):
        description = (
            "## Acceptance Criteria\n\n"
            "- [ ] **AC-1** — First criterion\n\n"
            "## Acceptance Criteria\n\n"
            "- [ ] **AC-1** — Second block criterion\n"
        )
        comments = [_proposal(["A brand new outcome"])]
        out = json.loads(_run(comments, description).stdout)
        self.assertTrue(out["promote"])
        nd = out["new_description"]
        # Inserted into the FIRST block (before the second H2), numbered AC-2.
        first_h2 = nd.index("## Acceptance Criteria")
        second_h2 = nd.index("## Acceptance Criteria", first_h2 + 1)
        new_item = nd.index("A brand new outcome")
        self.assertLess(new_item, second_h2)
        self.assertIn("- [ ] **AC-2** — A brand new outcome", nd)

    # ---------- latest proposal wins (rework round) ----------

    def test_latest_proposal_wins(self):
        comments = [
            _proposal(["Old proposed outcome"], t="2026-05-01T00:00:00Z"),
            _proposal(["New reworked outcome"], t="2026-05-02T00:00:00Z"),
        ]
        description = "## Context\n\nNothing yet.\n"
        out = json.loads(_run(comments, description).stdout)
        self.assertTrue(out["promote"])
        nd = out["new_description"]
        self.assertIn("New reworked outcome", nd)
        self.assertNotIn("Old proposed outcome", nd)

    # ---------- connection-wrap shape tolerated ----------

    def test_connection_wrap_nodes_shape(self):
        comments = {"nodes": [_proposal(["Wrapped outcome"])]}
        description = "## Context\n\nx\n"
        out = json.loads(_run(comments, description).stdout)
        self.assertTrue(out["promote"])
        self.assertIn("Wrapped outcome", out["new_description"])

    # ---------- CRLF / trailing-space markers tolerated ----------

    def test_crlf_and_trailing_space_markers(self):
        body = ("## Plan\r\n\r\n## Proposed Acceptance Criteria  \r\n\r\n"
                "- [ ] **AC-1** — CRLF outcome\r\n")
        comments = [_comment(body)]
        description = "## Context  \r\n\r\nbody\r\n"
        out = json.loads(_run(comments, description).stdout)
        self.assertTrue(out["promote"])
        self.assertIn("- [ ] **AC-1** — CRLF outcome", out["new_description"])

    # ---------- block with no checkboxes (template hint only) ----------

    def test_empty_ac_block_inserts_after_heading(self):
        description = (
            "## Acceptance Criteria\n\n"
            "<!-- Add - [ ] **AC-N** items here -->\n\n"
            "## Other\n\ntail\n"
        )
        comments = [_proposal(["First real criterion"])]
        out = json.loads(_run(comments, description).stdout)
        self.assertTrue(out["promote"])
        nd = out["new_description"]
        self.assertIn("- [ ] **AC-1** — First real criterion", nd)
        # The template hint is preserved.
        self.assertIn("<!-- Add", nd)
        # The new item lands before the next ## Other heading.
        self.assertLess(nd.index("First real criterion"), nd.index("## Other"))


if __name__ == "__main__":
    unittest.main()
