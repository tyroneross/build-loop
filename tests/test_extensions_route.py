import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extensions_init import ensure_scaffold  # noqa: E402
from extensions_route import route_draft  # noqa: E402
from extensions_pending_count import pending_count  # noqa: E402

class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["BUILD_LOOP_EXTENSIONS_ROOT"] = self.tmp.name
        ensure_scaffold(git_init=False)
    def tearDown(self): del os.environ["BUILD_LOOP_EXTENSIONS_ROOT"]; self.tmp.cleanup()
    def test_routes_draft_into_pending_and_counts(self):
        dst = route_draft("ext-alice-lint", "---\nname: ext-alice-lint\ndescription: lint\n---\nok\n")
        self.assertTrue(Path(dst).exists()); self.assertIn("pending", dst)
        self.assertEqual(pending_count(), 1)

if __name__ == "__main__":
    unittest.main()
