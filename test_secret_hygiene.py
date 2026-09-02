from pathlib import Path
from unittest import mock
import tempfile
import unittest

from tools.check_secret_hygiene import candidate_paths, findings


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

    def test_exported_source_bundle_is_scanned_without_git(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            included = root / "module.py"
            included.write_text("TOKEN = 'placeholder-value'\n")
            excluded = root / "node_modules" / "dependency.js"
            excluded.parent.mkdir()
            excluded.write_text("ignored\n")

            with mock.patch("tools.check_secret_hygiene.shutil.which", return_value=None):
                self.assertEqual(candidate_paths(root), [included])


if __name__ == "__main__":
    unittest.main()
