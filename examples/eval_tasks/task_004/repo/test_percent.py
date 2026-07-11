import unittest

from percent import clamp_percent


class TestClampPercent(unittest.TestCase):
    def test_caps_at_100(self):
        self.assertEqual(clamp_percent(150), 100)
        self.assertEqual(clamp_percent(100), 100)

    def test_leaves_small_values_alone(self):
        self.assertEqual(clamp_percent(42), 42)
        self.assertEqual(clamp_percent(0), 0)


if __name__ == "__main__":
    unittest.main()
