import tempfile
import unittest
from pathlib import Path

from patchpilot.models import RepairPlan
from patchpilot.patch_runner import (
    CLASS_BOOLEAN,
    CLASS_CONSTANT,
    CLASS_OPERATOR,
    PatchRunner,
)


def _plan(candidates, summary=""):
    return RepairPlan(
        issue_summary=summary,
        suspected_root_cause="",
        candidate_files=candidates,
        proposed_changes="",
        test_strategy="",
        risk_notes="",
    )


class TestNewMutationClasses(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="patchpilot_rs_"))
        self.runner = PatchRunner(str(self.repo))

    def _candidates(self, source: str, failure: str):
        (self.repo / "module.py").write_text(source)
        return self.runner.propose_candidates(_plan(["module.py"]), failure)

    def test_boolean_and_or_swap_proposed(self):
        candidates = self._candidates(
            "def in_range(value, low, high):\n"
            "    return value >= low or value <= high\n",
            "FAIL: test_in_range",
        )
        boolean = [c for c in candidates if c.class_rank == CLASS_BOOLEAN]
        self.assertEqual(len(boolean), 1)
        self.assertIn("value >= low and value <= high", boolean[0].new_content)

    def test_off_by_one_constant_proposed(self):
        candidates = self._candidates(
            "def clamp_percent(value):\n    return min(value, 99)\n",
            "FAIL: test_clamp_percent",
        )
        constants = [c for c in candidates if c.class_rank == CLASS_CONSTANT]
        contents = [c.new_content for c in constants]
        self.assertTrue(any("min(value, 100)" in c for c in contents))
        self.assertTrue(any("min(value, 98)" in c for c in contents))

    def test_operator_swaps_ranked_before_new_classes(self):
        candidates = self._candidates(
            "def f(a, b):\n    return a - b or a > 1\n",
            "FAIL: test_f",
        )
        ranks = [c.class_rank for c in candidates]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(candidates[0].class_rank, CLASS_OPERATOR)

    def test_constants_in_identifiers_and_floats_untouched(self):
        candidates = self._candidates(
            "def f(x1):\n    return x1 * 2.5\n",
            "FAIL: test_f",
        )
        constants = [c for c in candidates if c.class_rank == CLASS_CONSTANT]
        self.assertEqual(constants, [])

    def test_candidates_stay_bounded(self):
        source = "def f(a, b):\n" + "".join(
            f"    x{i} = a + {i} or b - {i}\n" for i in range(10)
        )
        candidates = self._candidates(source, "FAIL: test_f")
        self.assertLessEqual(len(candidates), 16)


if __name__ == "__main__":
    unittest.main()
