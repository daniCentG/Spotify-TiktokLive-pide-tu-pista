from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Deque, Dict, Optional, Tuple


# Controla ventanas de regalos, cooldown y límite global.
class AntiSpam:
    def __init__(
        self,
        cooldown_sec: int = 10,
        max_songs_per_user: int = 2,
        play_window_sec: int = 60,
        skip_window_sec: int = 60,
        play_attempts: int = 3,
        global_limit: int = 5,
        global_window_sec: int = 10,
    ) -> None:
        self.cooldown_sec = cooldown_sec
        self.max_songs_per_user = max_songs_per_user
        self.play_window_sec = play_window_sec
        self.skip_window_sec = skip_window_sec
        self.play_attempts = max(1, play_attempts)
        self.global_limit = global_limit
        self.global_window_sec = global_window_sec

        self._last_command: Dict[str, float] = {}
        self._play_windows: Dict[str, Tuple[float, bool, int, int]] = {}
        self._skip_windows: Dict[str, float] = {}
        self._global_events: Deque[float] = deque()
        self._lock = Lock()

    # Limpia entradas expiradas para evitar crecimiento indefinido.
    def _prune_locked(self, now: float) -> None:
        if self._play_windows:
            expired_play = [
                k
                for k, (exp, _, attempts, _) in self._play_windows.items()
                if exp < now or attempts <= 0
            ]
            for key in expired_play:
                del self._play_windows[key]

        if self._skip_windows:
            expired_skip = [k for k, exp in self._skip_windows.items() if exp < now]
            for key in expired_skip:
                del self._skip_windows[key]

        while self._global_events and (now - self._global_events[0]) > self.global_window_sec:
            self._global_events.popleft()

        if self._last_command:
            stale_after = max(
                self.cooldown_sec, self.play_window_sec, self.skip_window_sec, self.global_window_sec
            )
            cutoff = now - stale_after
            if cutoff > 0:
                stale_users = [k for k, ts in self._last_command.items() if ts < cutoff]
                for key in stale_users:
                    del self._last_command[key]

    # Abre ventana para !play y marca si es prioridad.
    def grant_play(self, user_id: str, priority: bool = False) -> None:
        now = time.time()
        expires_at = now + self.play_window_sec
        with self._lock:
            self._prune_locked(now)
            self._play_windows[user_id] = (expires_at, priority, self.play_attempts, 0)

    # Abre ventana para !skip.
    def grant_skip(self, user_id: str) -> None:
        now = time.time()
        expires_at = now + self.skip_window_sec
        with self._lock:
            self._prune_locked(now)
            self._skip_windows[user_id] = expires_at

    # Consume el permiso de !play (solo un uso).
    def consume_play(self, user_id: str) -> Optional[bool]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            entry = self._play_windows.get(user_id)
            if not entry:
                return None
            expires_at, priority, attempts_left, invalid_count = entry
            if now > expires_at or attempts_left <= 0:
                del self._play_windows[user_id]
                return None
            del self._play_windows[user_id]
            return priority

    # Solo valida si existe permiso vigente de !play.
    def peek_play(self, user_id: str) -> Optional[bool]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            entry = self._play_windows.get(user_id)
            if not entry:
                return None
            expires_at, priority, attempts_left, invalid_count = entry
            if now > expires_at or attempts_left <= 0:
                del self._play_windows[user_id]
                return None
            return priority

    # Registra un intento fallido de !play y reduce intentos restantes.
    def fail_play_attempt(self, user_id: str) -> Optional[int]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            entry = self._play_windows.get(user_id)
            if not entry:
                return None
            expires_at, priority, attempts_left, invalid_count = entry
            if now > expires_at:
                del self._play_windows[user_id]
                return None
            attempts_left -= 1
            if attempts_left <= 0:
                del self._play_windows[user_id]
                return 0
            self._play_windows[user_id] = (expires_at, priority, attempts_left, invalid_count)
            return attempts_left

    # Registra formato inválido. Los dos primeros no consumen intento.
    def record_invalid_format(self, user_id: str) -> Optional[int]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            entry = self._play_windows.get(user_id)
            if not entry:
                return None
            expires_at, priority, attempts_left, invalid_count = entry
            if now > expires_at:
                del self._play_windows[user_id]
                return None
            if invalid_count < 2:
                invalid_count += 1
                self._play_windows[user_id] = (expires_at, priority, attempts_left, invalid_count)
                return attempts_left
            attempts_left -= 1
            if attempts_left <= 0:
                del self._play_windows[user_id]
                return 0
            # Mantiene invalid_count en 2 para que los siguientes inválidos consuman intento.
            self._play_windows[user_id] = (expires_at, priority, attempts_left, invalid_count)
            return attempts_left

    # Consume el permiso de !skip (solo un uso).
    def consume_skip(self, user_id: str) -> bool:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            expires_at = self._skip_windows.get(user_id)
            if not expires_at:
                return False
            if now > expires_at:
                del self._skip_windows[user_id]
                return False
            del self._skip_windows[user_id]
            return True

    # Solo valida si existe permiso vigente de !skip.
    def peek_skip(self, user_id: str) -> bool:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            expires_at = self._skip_windows.get(user_id)
            if not expires_at:
                return False
            if now > expires_at:
                del self._skip_windows[user_id]
                return False
            return True

    # Controla el cooldown por usuario.
    def check_cooldown(self, user_id: str) -> bool:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            last = self._last_command.get(user_id)
            if last is None:
                return True
            return (now - last) >= self.cooldown_sec

    # Controla el límite global por ventana de tiempo.
    def check_global_rate(self) -> bool:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            return len(self._global_events) < self.global_limit

    # Registra que un comando fue aceptado.
    def record_command(self, user_id: str) -> None:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            self._last_command[user_id] = now
            self._global_events.append(now)

    # Máximo de canciones en cola por usuario.
    def can_request_song(self, current_user_count: int) -> bool:
        return current_user_count < self.max_songs_per_user