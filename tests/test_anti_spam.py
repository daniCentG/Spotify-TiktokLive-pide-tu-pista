import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from anti_spam import AntiSpam


class TestAntiSpam(unittest.TestCase):
    def test_invalid_format_consumption(self):
        spam = AntiSpam(play_attempts=3, play_window_sec=60)
        spam.grant_play("user", priority=False)

        self.assertEqual(spam.record_invalid_format("user"), 3)
        self.assertEqual(spam.record_invalid_format("user"), 3)
        # Third invalid consumes one attempt
        self.assertEqual(spam.record_invalid_format("user"), 2)


if __name__ == "__main__":
    unittest.main()