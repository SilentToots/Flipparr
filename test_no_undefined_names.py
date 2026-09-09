"""No module may reference a name that does not exist.

`request_discovered_series` applied the issue list, created the acquisition
request and sent the grab to SABnzbd, and then its return statement read a
`status` that had stopped existing three commits earlier. Python does not
resolve free names until the line runs, so every test passed, the import
succeeded, and the failure only appeared for a user who had actually added a
series -- after the work had already been done.

pyflakes finds exactly this class in about a tenth of a second, so it is a
test rather than a thing to remember to run. It is the Python half of what
`npm run lint` does for the frontend, which was added for the same bug in the
other language on the same day.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
# The modules a user's request actually travels through.
CHECKED = ["app.py", "catalog_store.py"]


class NoUndefinedNamesTests(unittest.TestCase):
    def test_no_module_references_a_name_that_does_not_exist(self):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pyflakes", *CHECKED],
                cwd=ROOT, capture_output=True, text=True, timeout=120,
            )
        except FileNotFoundError:  # pragma: no cover
            self.skipTest("pyflakes is not installed")
        if "No module named pyflakes" in result.stderr:
            self.skipTest("pyflakes is not installed")

        # Only undefined names fail. Unused imports and f-string nits are
        # style, and a gate that also reports those is one people stop reading.
        undefined = [
            line for line in result.stdout.splitlines()
            if "undefined name" in line or "undefined local" in line
        ]
        self.assertEqual(
            undefined, [],
            "these lines reference names that do not exist and will raise "
            "NameError the moment they run:\n  " + "\n  ".join(undefined),
        )
