import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import _action_from_gift_name, _normalize_gift_text


class TestGiftMapping(unittest.TestCase):
    def test_normalize_gift_text(self):
        self.assertEqual(_normalize_gift_text("Coraz\u00f3n coreano"), "corazon coreano")
        self.assertEqual(_normalize_gift_text("ROSA"), "rosa")

    def test_action_from_name(self):
        self.assertEqual(_action_from_gift_name("Rosa"), "play")
        self.assertEqual(_action_from_gift_name("Korean Heart"), "skip")
        self.assertEqual(_action_from_gift_name("Rosquilla"), "priority")


if __name__ == "__main__":
    unittest.main()