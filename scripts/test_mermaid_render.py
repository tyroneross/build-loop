# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for mermaid_render: block extraction (pure) + no-block render path."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import mermaid_render as mr  # noqa: E402


def test_extract_single_block():
    md = "intro\n```mermaid\nflowchart LR\nA-->B\n```\nend\n"
    assert mr.extract_mermaid_blocks(md) == ["flowchart LR\nA-->B"]


def test_extract_multiple_blocks_in_order():
    md = "```mermaid\nX\n```\nmid\n```mermaid\nY\n```\n"
    assert mr.extract_mermaid_blocks(md) == ["X", "Y"]


def test_extract_none_when_absent():
    assert mr.extract_mermaid_blocks("plain text, no diagrams") == []


def test_ignores_non_mermaid_fences():
    md = "```python\nprint(1)\n```\n```mermaid\nZ\n```\n"
    assert mr.extract_mermaid_blocks(md) == ["Z"]


def test_tolerates_info_string_after_fence():
    md = "```mermaid theme=neutral\nflowchart TD\nA-->B\n```\n"
    assert mr.extract_mermaid_blocks(md) == ["flowchart TD\nA-->B"]


def test_render_file_no_blocks_is_clean(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("no diagrams here")
    result = mr.render_file(str(f), str(tmp_path / "out"))
    assert result["blocks"] == 0
    assert result["rendered"] == []
    assert result["errors"] == []
