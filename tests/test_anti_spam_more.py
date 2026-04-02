import os
import sys
import time
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from anti_spam import AntiSpam


class TestAntiSpamMore(unittest.TestCase):
    def test_fail_play_attempt_expires_window(self):
        spam = AntiSpam(play_attempts=2, play_window_sec=1)
        spam.grant_play("user", priority=False)
        time.sleep(1.1)
        self.assertIsNone(spam.fail_play_attempt("user"))

    def test_fail_play_attempt_decrements(self):
        spam = AntiSpam(play_attempts=2, play_window_sec=60)
        spam.grant_play("user", priority=False)
        self.assertEqual(spam.fail_play_attempt("user"), 1)
        self.assertEqual(spam.fail_play_attempt("user"), 0)
        self.assertIsNone(spam.fail_play_attempt("user"))

    def test_skip_window_consumption(self):
        spam = AntiSpam(skip_window_sec=1)
        spam.grant_skip("user")
        self.assertTrue(spam.peek_skip("user"))
        self.assertTrue(spam.consume_skip("user"))
        self.assertFalse(spam.peek_skip("user"))


if __name__ == "__main__":
    unittest.main()