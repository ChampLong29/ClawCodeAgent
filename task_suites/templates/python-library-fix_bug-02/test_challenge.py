import unittest

from challenge import stable_unique


class ChallengeTest(unittest.TestCase):
    def test_preserves_first_seen_order(self):
        values = ["z", "item-2", "z", "a", "item-2"]
        self.assertEqual(stable_unique(values), ["z", "item-2", "a"])

    def test_empty_list(self):
        self.assertEqual(stable_unique([]), [])


if __name__ == "__main__":
    unittest.main()
