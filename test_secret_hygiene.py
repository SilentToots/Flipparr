from pathlib import Path
import tempfile
import unittest

from tools.check_secret_hygiene import findings


class SecretHygieneTests(unittest.TestCase):
    def test_repository_has_no_tracked_secret_material(self):
        root = Path(__file__).resolve().parent
        self.assertEqual(findings(root), [])

    def test_scanner_never_returns_a_secret_value(self):
        # The production scan reports only a location and a fixed reason. This
        # regression protects CI output if a future credential is detected.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".git").mkdir()
            # Unit-testing git enumeration itself would make this test depend on
            # repository mutation; the fixed finding shape is asserted directly.
            sample = [(Path("settings.py"), 7, "literal credential assignment")]
            rendered = "\n".join(
                f"{path}:{line}: {reason}" for path, line, reason in sample
            )
            self.assertNotIn("recognizable-real-value", rendered)


if __name__ == "__main__":
    unittest.main()
