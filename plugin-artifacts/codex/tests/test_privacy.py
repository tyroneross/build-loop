import sys, json, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from privacy import scan_text, load_default_patterns  # noqa: E402

class PrivacyScanTests(unittest.TestCase):
    def test_flags_known_pii_and_secrets(self):
        pats = ["(?i)tyroneross", r"/Users/[^/\s`]+", r"gh[pousr]_[A-Za-z0-9_]{20,}"]
        hits = scan_text("path /Users/alice and ghp_ABCDEFGHIJKLMNOPQRSTUV", pats)
        self.assertTrue(any("/Users/alice" in h["match"] for h in hits))
        self.assertTrue(any(h["match"].startswith("ghp_") for h in hits))
    def test_clean_text_no_hits(self):
        self.assertEqual(scan_text("a generic skill that lints YAML", ["(?i)tyroneross"]), [])
    def test_loads_patterns_from_memory_manifest(self):
        pats = load_default_patterns()
        self.assertIn("(?i)tyroneross", pats)

if __name__ == "__main__":
    unittest.main()
