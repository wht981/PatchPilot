import unittest

from ranges import in_range


class TestInRange(unittest.TestCase):
    def test_inside_range(self):
        self.assertTrue(in_range(5, 1, 10))
        self.assertTrue(in_range(1, 1, 10))
        self.assertTrue(in_range(10, 1, 10))

    def test_above_range_rejected(self):
        self.assertFalse(in_range(20, 1, 10))

    def test_below_range_rejected(self):
        self.assertFalse(in_range(0, 1, 10))


if __name__ == "__main__":
    unittest.main()
