"""Runs every script in tests/scenarios in its own process, from an empty temp folder so nothing touches data/.

Each script asserts as it goes and prints "ok" lines. A script passes when it exits with code 0.
Run all tests from the repo root with:  python -m unittest discover tests
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCENARIOS = os.path.join(HERE, "scenarios")


def _make_test(filename):
    def test(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-W", "ignore", os.path.join(SCENARIOS, filename)],
                cwd=tmp, capture_output=True, text=True, timeout=300,
                env={**os.environ, "PYTHONPATH": ROOT},
            )
        self.assertEqual(result.returncode, 0, result.stdout[-1500:] + "\n" + result.stderr[-1500:])
    return test


class Scenarios(unittest.TestCase):
    pass


for _name in sorted(os.listdir(SCENARIOS)):
    if _name.endswith(".py"):
        setattr(Scenarios, "test_" + _name[:-3], _make_test(_name))


if __name__ == "__main__":
    unittest.main()
