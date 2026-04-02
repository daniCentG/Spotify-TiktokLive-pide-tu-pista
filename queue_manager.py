from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import time
from threading import Lock
from typing import Deque, Dict, List, Optional, Tuple


# Representa una solicitud de canción.
@dataclass
class SongRequest:
    user_id: str
    user_name: str
    title: str
    artist: str
    uri: str
    duration_ms: int
    explicit: bool
    requested_at: float
    priority: bool = False

    @property
    def display_title(self) -> str:
        return f"{self.title} - {self.artist}"


# Maneja cola normal y prioritaria en memoria.
class QueueManager:
    def __init__(self, max_display: int = 5) -> None:
        self._priority: Deque[SongRequest] = deque()
        self._normal: Deque[SongRequest] = deque()
        self._recent: Deque[Tuple[str, str, float]] = deque()
        self._recent_keys: Dict[Tuple[str, str], float] = {}
        self._queued_counts: Dict[Tuple[str, str], int] = {}
        self._lock = Lock()
        self.max_display = max_display

    # Agrega una solicitud a la cola correcta.
    def add(self, request: SongRequest, priority: bool = False) -> None:
        request.priority = priority
        key = self._normalize_key(request.title, request.artist)
        with self._lock:
            if priority:
                self._priority.append(request)
            else:
                self._normal.append(request)
            self._queued_counts[key] = self._queued_counts.get(key, 0) + 1

    # Registra una canción aceptada para bloquear duplicados recientes.
    def record_recent(self, title: str, artist: str) -> None:
        now = time.time()
        key_title, key_artist = self._normalize_key(title, artist)
        with self._lock:
            self._recent.append((key_title, key_artist, now))
            self._recent_keys[(key_title, key_artist)] = now

    # Determina si una canción es duplicada en ventana reciente o en cola actual.
    def is_duplicate(self, title: str, artist: str, window_sec: int) -> bool:
        now = time.time()
        key_title, key_artist = self._normalize_key(title, artist)
        key = (key_title, key_artist)
        with self._lock:
            self._prune_recent_locked(now, window_sec)
            if key in self._queued_counts:
                return True
            if key in self._recent_keys:
                return True
        return False

    # Saca la siguiente canción prioritaria.
    def pop_next_priority(self) -> Optional[SongRequest]:
        with self._lock:
            if not self._priority:
                return None
            return self._priority.popleft()

    # Elimina una solicitud por URL si esta en cola.
    def remove_by_uri(self, uri: str) -> bool:
        with self._lock:
            for queue in (self._priority, self._normal):
                items = list(queue)
                for idx, item in enumerate(items):
                    if item.uri == uri:
                        del items[idx]
                        queue.clear()
                        queue.extend(items)
                        key = self._normalize_key(item.title, item.artist)
                        count = self._queued_counts.get(key, 0)
                        if count <= 1:
                            self._queued_counts.pop(key, None)
                        else:
                            self._queued_counts[key] = count - 1
                        return True
        return False

    # Cuenta cuantas canciones tiene un usuario en cola.
    def count_user(self, user_id: str) -> int:
        with self._lock:
            return sum(1 for r in list(self._priority) + list(self._normal) if r.user_id == user_id)

    # Si hay pendientes de prioridad.
    def has_priority_pending(self) -> bool:
        with self._lock:
            return len(self._priority) > 0

    # Cuenta total en cola.
    def count_total(self) -> int:
        with self._lock:
            return len(self._priority) + len(self._normal)

    # Devuelve la lista para el overlay (max_display).
    def get_display_queue(self) -> List[Dict[str, str]]:
        items: List[SongRequest] = []
        with self._lock:
            items.extend(list(self._priority))
            items.extend(list(self._normal))
        display = items[: self.max_display]
        result: List[Dict[str, str]] = []
        for item in display:
            result.append(
                {
                    "title": item.title,
                    "artist": item.artist,
                    "user": item.user_name,
                    "priority": "yes" if item.priority else "no",
                }
            )
        return result

    # Snapshot completo para debug.
    def snapshot(self) -> Dict[str, List[Dict[str, str]]]:
        with self._lock:
            priority = list(self._priority)
            normal = list(self._normal)
        return {
            "priority": [self._to_dict(r) for r in priority],
            "normal": [self._to_dict(r) for r in normal],
        }

    # Serializa una solicitud a dict.
    @staticmethod
    def _to_dict(req: SongRequest) -> Dict[str, str]:
        return {
            "title": req.title,
            "artist": req.artist,
            "user": req.user_name,
            "priority": "yes" if req.priority else "no",
            "uri": req.uri,
            "duration_ms": str(req.duration_ms),
        }

    @staticmethod
    def _normalize_key(title: str, artist: str) -> Tuple[str, str]:
        norm_title = " ".join((title or "").lower().split())
        norm_artist = " ".join((artist or "").lower().split())
        return norm_title, norm_artist

    def _prune_recent_locked(self, now: float, window_sec: int) -> None:
        while self._recent and (now - self._recent[0][2]) > window_sec:
            title_key, artist_key, ts = self._recent.popleft()
            key = (title_key, artist_key)
            if self._recent_keys.get(key) == ts:
                self._recent_keys.pop(key, None)
