import unittest

from challenge import build_command


class ChallengeTest(unittest.TestCase):
    def test_formats_command(self):
        self.assertEqual(build_command("alpha"), "run4:alpha")

    def test_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            build_command("")


if __name__ == "__main__":
    unittest.main()
