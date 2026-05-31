"""Focused tests for templates/hooks/audit_linear_writes.py `_summarize`.

The audit hook logs one JSON line per Linear write. A description-only
issue update (the promote-acceptance-criteria write surface) must produce a
non-empty summary so the audit log stays useful — this pins that fallback.
"""
import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "templates" / "hooks" / "audit_linear_writes.py"

_spec = importlib.util.spec_from_file_location("audit_linear_writes",
                                               MODULE_PATH)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


class SummarizeTests(unittest.TestCase):

    def test_description_only_update_summarised(self):
        s = audit._summarize(
            "update_issue",
            {"id": "ENG-1", "description": "## Acceptance Criteria\n- [ ] AC-1"})
        self.assertTrue(s)
        self.assertTrue(s.startswith("description: "))
        # Newlines collapsed to spaces.
        self.assertNotIn("\n", s)

    def test_body_takes_precedence_over_description(self):
        s = audit._summarize(
            "save_comment",
            {"body": "a comment", "description": "should not win"})
        self.assertEqual(s, "a comment")

    def test_empty_description_falls_through(self):
        s = audit._summarize("update_issue", {"description": ""})
        self.assertEqual(s, "")


if __name__ == "__main__":
    unittest.main()
