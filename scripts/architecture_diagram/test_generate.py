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


def test_model_is_identical_before_and_after_source_commit(tmp_path, monkeypatch):
    """Git attribution must not make a pre-commit generated model stale."""
    import subprocess
    import sys
    from scripts.architecture_diagram.generate import build_model, main
    root = Path(__file__).resolve().parents[2]
    for relative in ("architecture/ARCHITECTURE.md", "agents/build-orchestrator.md",
                     "skills/build-loop/SKILL.md", "hooks/hooks.json",
                     "docs/build-loop-flow-mockup.html"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / relative).read_bytes())
    def git(*arguments):
        subprocess.run(["git", "-C", str(tmp_path), *arguments], check=True,
                       capture_output=True, text=True)
    git("init")
    git("config", "user.name", "Test Fixture")
    git("config", "user.email", "fixture@example.invalid")
    monkeypatch.setattr(sys, "argv", ["generate.py", "--repo", str(tmp_path)])
    assert main() == 0
    before = build_model(tmp_path)
    git("add", ".")
    git("commit", "-m", "fixture source commit")
    assert build_model(tmp_path) == before
    monkeypatch.setattr(sys, "argv", ["generate.py", "--repo", str(tmp_path), "--check"])
    assert main() == 0
