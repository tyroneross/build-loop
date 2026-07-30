import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_check import check_skill  # noqa: E402

def _skill(tmp, name, desc, body="ok"):
    p = Path(tmp) / name / "SKILL.md"; p.parent.mkdir(parents=True)
    p.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n"); return p

class CheckTests(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.TemporaryDirectory()
    def tearDown(self): self.tmp.cleanup()
    def test_passes_clean_namespaced_skill(self):
        p = _skill(self.tmp.name, "ext-alice-lint-yaml", "lint YAML files on save")
        self.assertEqual(check_skill(p, core_descriptions=[]), [])
    def test_flags_missing_namespace(self):
        p = _skill(self.tmp.name, "lint-yaml", "lint YAML")
        self.assertIn("namespace", [i["code"] for i in check_skill(p, core_descriptions=[])])
    def test_flags_pii(self):
        p = _skill(self.tmp.name, "ext-alice-x", "skill for /Users/alice/secret stuff")
        self.assertIn("privacy", [i["code"] for i in check_skill(p, core_descriptions=[])])
    def test_flags_missing_frontmatter(self):
        p = Path(self.tmp.name) / "ext-alice-y" / "SKILL.md"; p.parent.mkdir(parents=True)
        p.write_text("no frontmatter here")
        self.assertIn("schema", [i["code"] for i in check_skill(p, core_descriptions=[])])

if __name__ == "__main__":
    unittest.main()
