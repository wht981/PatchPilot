import unittest

from greeting import greet
from names import normalize_name


class GreetingTests(unittest.TestCase):
    def test_normalize_case(self):
        self.assertEqual(normalize_name("  ALICE  "), "alice")

    def test_normalize_whitespace(self):
        self.assertEqual(normalize_name("  Ada\t \nLovelace  "), "ada lovelace")

    def test_normalize_unicode(self):
        self.assertEqual(normalize_name("Straße"), "strasse")

    def test_greet(self):
        self.assertEqual(greet("  ADA  LOVELACE "), "Hello, ada lovelace!")

    def test_empty_name(self):
        self.assertEqual(greet(" \t "), "Hello, !")
