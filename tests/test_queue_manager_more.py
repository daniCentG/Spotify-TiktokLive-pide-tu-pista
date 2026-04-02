import os
import sys
import time
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from queue_manager import QueueManager, SongRequest


class TestQueueManagerCounts(unittest.TestCase):
    def _make_request(self, title: str, artist: str, uri: str, user: str = "u1") -> SongRequest:
        return SongRequest(
            user_id=user,
            user_name=user,
            title=title,
            artist=artist,
            uri=uri,
            duration_ms=1000,
            explicit=False,
            requested_at=time.time(),
            priority=False,
        )

    def test_duplicate_clears_after_remove(self):
        manager = QueueManager()
        req = self._make_request("Song", "Artist", "uri:1")
        manager.add(req, priority=False)
        self.assertTrue(manager.is_duplicate("Song", "Artist", 120))
        manager.remove_by_uri("uri:1")
        self.assertFalse(manager.is_duplicate("Song", "Artist", 120))

    def test_duplicate_recent_ttl(self):
        manager = QueueManager()
        manager.record_recent("Song", "Artist")
        self.assertTrue(manager.is_duplicate("Song", "Artist", 120))
        self.assertFalse(manager.is_duplicate("Song", "Artist", 0))

    def test_count_user(self):
        manager = QueueManager()
        manager.add(self._make_request("Song", "Artist", "uri:1", user="u1"))
        manager.add(self._make_request("Song2", "Artist2", "uri:2", user="u1"))
        manager.add(self._make_request("Song3", "Artist3", "uri:3", user="u2"))
        self.assertEqual(manager.count_user("u1"), 2)
        self.assertEqual(manager.count_user("u2"), 1)


if __name__ == "__main__":
    unittest.main()