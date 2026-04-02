import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import _parse_play_query


class TestParsePlayQuery(unittest.TestCase):
    def test_parse_valid(self):
        self.assertEqual(_parse_play_query("Song - Artist"), ("Song", "Artist"))
        self.assertEqual(_parse_play_query("Song-Artist"), ("Song", "Artist"))
        self.assertEqual(_parse_play_query("Song -Artist"), ("Song", "Artist"))
        self.assertEqual(_parse_play_query("Song- Artist"), ("Song", "Artist"))

    def test_parse_invalid(self):
        self.assertIsNone(_parse_play_query("Song Artist"))
        self.assertIsNone(_parse_play_query("Song - "))
        self.assertIsNone(_parse_play_query(" - Artist"))


if __name__ == "__main__":
    unittest.main()