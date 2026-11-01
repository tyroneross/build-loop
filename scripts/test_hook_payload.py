# SPDX-License-Identifier: Apache-2.0
import unittest
from hook_payload import edited_paths


class HookPayloadTests(unittest.TestCase):
    def test_claude_and_generic_paths(self):
        for name in ("Write", "Edit", "other-host-edit"):
            self.assertEqual(edited_paths({"tool_name": name, "tool_input": {"file_path": "a b.py"}}), ["a b.py"])

    def test_codex_multifile_move_and_delete(self):
        patch = "*** Begin Patch\n*** Update File: old.py\n*** Move to: new.py\n@@\n+x\n*** Delete File: gone.py\n*** Add File: added.py\n+x\n*** End Patch"
        self.assertEqual(edited_paths({"tool_name": "apply_patch", "tool_input": {"command": patch}}), ["old.py", "new.py", "gone.py", "added.py"])

    def test_shell_text_is_not_a_patch(self):
        self.assertEqual(edited_paths({"tool_name": "Bash", "tool_input": {"command": "*** Delete File: safe.py"}}), [])

    def test_invalid_inputs(self):
        for value in ({}, {"tool_input": "invalid"}, {"tool_input": {"file_path": 12}}):
            self.assertEqual(edited_paths(value), [])


def test_rally_scope_hint_understands_codex_patch():
    import io
    from rally_point.hooks import read_event_stdin
    event = {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Update File: nested/a.py\n*** End Patch"}, "session_id": "thread"}
    result = read_event_stdin(io.BytesIO(__import__("json").dumps(event).encode()))
    assert result["file_path"] == "nested/a.py"
    assert result["session_id"] == "thread"
