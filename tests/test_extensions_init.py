import os, sys, json, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_init import ensure_scaffold  # noqa: E402

class InitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_extensions_root = os.environ.get("BUILD_LOOP_EXTENSIONS_ROOT")
        os.environ["BUILD_LOOP_EXTENSIONS_ROOT"] = self.tmp.name
    def tearDown(self):
        if self.old_extensions_root is None:
            os.environ.pop("BUILD_LOOP_EXTENSIONS_ROOT", None)
        else:
            os.environ["BUILD_LOOP_EXTENSIONS_ROOT"] = self.old_extensions_root
        self.tmp.cleanup()

    def test_creates_structure_and_versioned_manifest(self):
        ensure_scaffold(git_init=False)
        r = Path(self.tmp.name)
        self.assertTrue((r / "plugin" / "skills").is_dir())
        self.assertTrue((r / "pending").is_dir())
        m = json.loads((r / "plugin" / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(m["name"], "build-loop-extensions")
        self.assertTrue(m.get("version"))
        self.assertEqual(json.loads((r / "graduated.json").read_text()), {"absorbed": []})

    def test_idempotent(self):
        ensure_scaffold(git_init=False); ensure_scaffold(git_init=False)

    def test_registers_only_plugin_root(self):
        import extensions_init
        home = tempfile.TemporaryDirectory()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = home.name
        try:
            ensure_scaffold(git_init=False)
            res = extensions_init.register_skills_dir()
            self.assertTrue(res["registered"])
            link = Path(home.name) / ".claude" / "skills" / "build-loop-extensions"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), (Path(self.tmp.name) / "plugin").resolve())
            self.assertEqual(extensions_init.register_skills_dir().get("noop"), True)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            home.cleanup()

if __name__ == "__main__":
    unittest.main()
