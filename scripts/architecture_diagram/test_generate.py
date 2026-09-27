"""Frontmatter metadata must not become human-facing description text."""
from pathlib import Path
from scripts.architecture_diagram.generate import _extract_desc


def test_real_skill_descriptions_stop_before_hyphenated_metadata():
    root = Path(__file__).resolve().parents[2]
    for name in ("color-engine", "root-cause-analysis"):
        body = (root / "skills" / name / "SKILL.md").read_text()
        frontmatter = body.split("---", 2)[1]
        description = _extract_desc(frontmatter)
        assert "user-invocable" not in description
        assert description.startswith("Use ")
