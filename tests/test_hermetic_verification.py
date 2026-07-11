"""Regression tests for stale-bytecode-proof test verification.

A patch that keeps a file's size unchanged and lands within the same
mtime second can defeat CPython's timestamp-based .pyc validity check,
so tests would execute the pre-patch code and a correct fix would look
like a failure. The cache may live outside the repo entirely (Apple's
system Python uses a pycache prefix under ~/Library/Caches), so
run_tests must isolate every verification run behind a fresh, empty
PYTHONPYCACHEPREFIX in addition to purging in-repo __pycache__ dirs.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from patchpilot.test_runner import run_tests


PYTHON = sys.executable or "python3"


class TestHermeticVerification(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="patchpilot_hermetic_"))
        self.addCleanup(shutil.rmtree, self.repo, True)

    def test_stale_bytecode_cannot_mask_a_fix(self):
        """Faithful reproduction of the trap seen in a real run.

        The stale .pyc is planted exactly the way a baseline test run
        plants it: a plain import subprocess with no hermetic env. The
        'patch' then keeps the file size and mtime unchanged, which is
        the worst case for timestamp-based cache validation.
        """
        module = self.repo / "calculator.py"
        module.write_text("def add(a, b):\n    return a - b\n")
        (self.repo / "test_calculator.py").write_text(
            "import unittest\n"
            "from calculator import add\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n"
        )
        stat_before = module.stat()
        # Baseline-style import: lets the interpreter cache the buggy
        # bytecode wherever this platform caches it.
        subprocess.run(
            [PYTHON, "-c", "import calculator"],
            cwd=str(self.repo), check=True, capture_output=True,
        )
        # The "patch": same byte count, mtime forced back to the second
        # the stale bytecode was validated against.
        module.write_text("def add(a, b):\n    return a + b\n")
        os.utime(module, (stat_before.st_atime, stat_before.st_mtime))

        result = run_tests(
            str(self.repo), [f"{PYTHON} -m unittest discover"], timeout=60
        )
        self.assertTrue(
            result.passed,
            "stale bytecode masked a correct fix: "
            + (result.commands[0].stderr if result.commands else "no output"),
        )

    def test_pycache_is_purged_before_runs(self):
        (self.repo / "mod.py").write_text("x = 1\n")
        fake_cache = self.repo / "__pycache__"
        fake_cache.mkdir()
        (fake_cache / "mod.cpython-39.pyc").write_bytes(b"stale")
        run_tests(str(self.repo), ["true"], timeout=30)
        self.assertFalse(fake_cache.exists())

    def test_no_bytecode_left_in_repo(self):
        (self.repo / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
        (self.repo / "test_calculator.py").write_text(
            "import unittest\n"
            "from calculator import add\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(1, 1), 2)\n"
        )
        result = run_tests(
            str(self.repo), [f"{PYTHON} -m unittest discover"], timeout=60
        )
        self.assertTrue(result.passed)
        self.assertFalse((self.repo / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
