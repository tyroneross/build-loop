import os, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_paths import root, plugin_dir, pending_dir, manifest_path  # noqa: E402

class PathsTests(unittest.TestCase):
    def test_env_override(self):
        os.environ["BUILD_LOOP_EXTENSIONS_ROOT"] = "/tmp/blx-test"
        try:
            self.assertEqual(root(), Path("/tmp/blx-test"))
            self.assertEqual(plugin_dir(), Path("/tmp/blx-test/plugin"))
            self.assertEqual(pending_dir(), Path("/tmp/blx-test/pending"))
            self.assertEqual(manifest_path(), Path("/tmp/blx-test/plugin/.claude-plugin/plugin.json"))
        finally:
            del os.environ["BUILD_LOOP_EXTENSIONS_ROOT"]
    def test_default_is_hyphenated_home(self):
        os.environ.pop("BUILD_LOOP_EXTENSIONS_ROOT", None)
        self.assertEqual(root(), Path.home() / ".build-loop-extensions")

if __name__ == "__main__":
    unittest.main()
