import unittest

from validator import is_adult, is_minor


class TestValidator(unittest.TestCase):
    def test_exactly_18_is_adult(self):
        self.assertTrue(is_adult(18))

    def test_older_is_adult(self):
        self.assertTrue(is_adult(30))

    def test_younger_is_not_adult(self):
        self.assertFalse(is_adult(17))

    def test_minor_is_consistent(self):
        self.assertTrue(is_minor(10))
        self.assertFalse(is_minor(40))


if __name__ == "__main__":
    unittest.main()
