import os
import sys
import time
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from queue_manager import QueueManager, SongRequest


class TestQueueManagerDuplicates(unittest.TestCase):
    def _make_request(self, title: str, artist: str, uri: str) -> SongRequest:
        return SongRequest(
            user_id="u1",
            user_name="user",
            title=title,
            artist=artist,
            uri=uri,
            duration_ms=1000,
            explicit=False,
            requested_at=time.time(),
            priority=False,
        )

    def test_duplicate_in_queue_and_recent(self):
        manager = QueueManager()
        req = self._make_request("Song", "Artist", "uri:1")

        manager.add(req, priority=False)
        self.assertTrue(manager.is_duplicate("Song", "Artist", 120))

        manager.remove_by_uri("uri:1")
        self.assertFalse(manager.is_duplicate("Song", "Artist", 120))

        manager.record_recent("Song", "Artist")
        self.assertTrue(manager.is_duplicate("Song", "Artist", 120))


if __name__ == "__main__":
    unittest.main()