import unittest

from challenge import weighted_total


class ChallengeTest(unittest.TestCase):
    def test_applies_weight(self):
        self.assertEqual(weighted_total([1, 2, 3]), 36)

    def test_empty_list(self):
        self.assertEqual(weighted_total([]), 0)

    def test_rejects_non_list(self):
        with self.assertRaises(TypeError):
            weighted_total((1, 2))


if __name__ == "__main__":
    unittest.main()
