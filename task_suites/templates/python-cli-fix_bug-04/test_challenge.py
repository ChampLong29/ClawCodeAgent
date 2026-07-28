import unittest

from challenge import parse_port


class ChallengeTest(unittest.TestCase):
    def test_parses_numeric_text(self):
        self.assertEqual(parse_port("8004"), 8004)

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            parse_port(8005)

    def test_rejects_non_numeric(self):
        with self.assertRaises(ValueError):
            parse_port("http")


if __name__ == "__main__":
    unittest.main()
