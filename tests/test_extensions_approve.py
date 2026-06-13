import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_init import ensure_scaffold  # noqa: E402
from extensions_approve import list_pending, approve  # noqa: E402

class ApproveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["BUILD_LOOP_EXTENSIONS_ROOT"] = self.tmp.name
        ensure_scaffold(git_init=False)
        d = Path(self.tmp.name) / "pending" / "skills" / "ext-alice-lint"; d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: ext-alice-lint\ndescription: lint YAML on save\n---\nok\n")
    def tearDown(self): del os.environ["BUILD_LOOP_EXTENSIONS_ROOT"]; self.tmp.cleanup()

    def test_list_shows_pending(self):
        self.assertIn("ext-alice-lint", list_pending())
    def test_approve_moves_to_plugin(self):
        res = approve("ext-alice-lint", core_descriptions=[])
        self.assertTrue(res["approved"])
        r = Path(self.tmp.name)
        self.assertTrue((r / "plugin" / "skills" / "ext-alice-lint" / "SKILL.md").exists())
        self.assertFalse((r / "pending" / "skills" / "ext-alice-lint").exists())
    def test_approve_blocked_by_checks(self):
        bad = Path(self.tmp.name) / "pending" / "skills" / "no-namespace"; bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("---\nname: no-namespace\ndescription: x\n---\n")
        res = approve("no-namespace", core_descriptions=[])
        self.assertFalse(res["approved"]); self.assertTrue(res["issues"])

if __name__ == "__main__":
    unittest.main()
