from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple, List

import spotipy
import requests
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheHandler
from spotipy.exceptions import SpotifyException


# Cache en memoria para evitar escribir tokens en disco.
class MemoryCacheHandler(CacheHandler):
    def __init__(self) -> None:
        self._cache = None

    def get_cached_token(self):
        return self._cache

    def save_token_to_cache(self, token_info):
        self._cache = token_info


# Envoltorio async para Spotify Web API.
class SpotifyController:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scope: str,
        poll_interval_sec: int = 5,
    ) -> None:
        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope,
            cache_handler=MemoryCacheHandler(),
            open_browser=True,
        )
        self._sp = spotipy.Spotify(auth_manager=auth_manager)
        self._poll_interval = poll_interval_sec
        self._watch_task: Optional[asyncio.Task] = None
        self._last_playing_uri: Optional[str] = None
        self._priority_task: Optional[asyncio.Task] = None
        self._priority_active = False
        self._priority_lock = asyncio.Lock()
        self._last_playback_error_log = 0.0
        self._suppressed_playback_errors = 0

    # Errores transitorios de red/servicio para reintentos.
    class SpotifyTransientError(RuntimeError):
        pass

    # Determina si un error es transitorio (red/rate limit/5xx).
    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        if isinstance(exc, SpotifyException):
            if exc.http_status in (429, 500, 502, 503, 504):
                return True
        if isinstance(exc, requests.exceptions.RequestException):
            return True
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return True
        return False

    async def _run_with_retries(self, func: Callable[[], Any]) -> Any:
        delays = [5, 15, 60]
        attempt = 0
        while True:
            try:
                return await asyncio.to_thread(func)
            except Exception as exc:
                if not self._is_transient_error(exc):
                    raise
                if attempt >= len(delays):
                    raise self.SpotifyTransientError(str(exc)) from exc
                logging.warning("Spotify no responde, reintentando...")
                await asyncio.sleep(delays[attempt])
                attempt += 1

    # Búsqueda simple por texto.
    async def search_track(self, query: str) -> Optional[Dict[str, Any]]:
        def _search():
            return self._sp.search(q=query, type="track", limit=5)

        try:
            result = await self._run_with_retries(_search)
        except self.SpotifyTransientError:
            raise
        except Exception:
            logging.exception("Spotify search failed")
            return None

        items = (result or {}).get("tracks", {}).get("items", [])
        if not items:
            return None

        # Pick the most popular result as the best match
        items = sorted(items, key=lambda x: x.get("popularity", 0), reverse=True)
        return items[0]

    # Búsqueda flexible usando nombre y artista, con tolerancia a errores.
    async def search_track_precise(
        self,
        track_name: str,
        artist_name: str,
        min_title_ratio: float = 0.6,
        min_artist_ratio: float = 0.55,
    ) -> Optional[Dict[str, Any]]:
        query = f"track:{track_name} artist:{artist_name}"

        def _search():
            return self._sp.search(q=query, type="track", limit=10)

        try:
            result = await self._run_with_retries(_search)
        except self.SpotifyTransientError:
            raise
        except Exception:
            logging.exception("Spotify search failed")
            return None

        items = (result or {}).get("tracks", {}).get("items", [])
        if not items:
            return None

        target_title = self._normalize_text(track_name)
        target_artist = self._normalize_text(artist_name)
        target_title_compact = self._compact_text(target_title)
        target_artist_compact = self._compact_text(target_artist)

        best_item: Optional[Dict[str, Any]] = None
        best_score = 0.0

        for item in items:
            title = self._normalize_text(item.get("name", ""))
            title_compact = self._compact_text(title)
            title_ratio = difflib.SequenceMatcher(None, title_compact, target_title_compact).ratio()

            artist_names = [self._normalize_text(a.get("name", "")) for a in item.get("artists", [])]
            artist_score, _ = self._best_artist_match(
                artist_names, target_artist, target_artist_compact
            )

            if title_ratio < min_title_ratio or artist_score < min_artist_ratio:
                continue

            combined = (title_ratio * 0.6) + (artist_score * 0.4)
            if combined > best_score:
                best_score = combined
                best_item = item

        if best_item is not None:
            return best_item

        # Fallback: broader search, pick most popular
        broad_query = f"{track_name} {artist_name}".strip()

        def _search_broad():
            return self._sp.search(q=broad_query, type="track", limit=5)

        try:
            broad_result = await self._run_with_retries(_search_broad)
        except self.SpotifyTransientError:
            raise
        except Exception:
            logging.exception("Spotify broad search failed")
            return None

        broad_items = (broad_result or {}).get("tracks", {}).get("items", [])
        if not broad_items:
            return None
        broad_items = sorted(broad_items, key=lambda x: x.get("popularity", 0), reverse=True)
        return broad_items[0]

    # Normaliza texto para comparaciones.
    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.lower()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # Compacta texto (quita espacios).
    @staticmethod
    def _compact_text(text: str) -> str:
        return text.replace(" ", "")

    # Busca el artista más parecido entre los créditos.
    @staticmethod
    def _best_artist_match(
        artist_names: List[str],
        target_artist: str,
        target_artist_compact: str,
    ) -> Tuple[float, bool]:
        best_ratio = 0.0
        best_contains = False
        for name in artist_names:
            name_compact = name.replace(" ", "")
            ratio = difflib.SequenceMatcher(None, name_compact, target_artist_compact).ratio()
            contains = False
            if ratio > best_ratio:
                best_ratio = ratio
                best_contains = contains
        return best_ratio, best_contains

    # Encola en el dispositivo activo.
    async def add_to_queue(self, uri: str) -> bool:
        try:
            await self._run_with_retries(lambda: self._sp.add_to_queue(uri))
            return True
        except self.SpotifyTransientError:
            raise
        except Exception:
            logging.exception("Failed to add to Spotify queue")
            return False

    # Salta a la siguiente pista.
    async def skip(self) -> bool:
        try:
            await self._run_with_retries(self._sp.next_track)
            return True
        except self.SpotifyTransientError:
            raise
        except Exception:
            logging.exception("Failed to skip track")
            return False

    # Reproduce inmediatamente una pista.
    async def start_playback(self, uri: str) -> bool:
        try:
            await self._run_with_retries(lambda: self._sp.start_playback(uris=[uri]))
            return True
        except self.SpotifyTransientError:
            raise
        except Exception:
            logging.exception("Failed to start playback")
            return False

    # Obtiene el estado de reproducción actual.
    async def current_playback(self) -> Optional[Dict[str, Any]]:
        try:
            return await asyncio.to_thread(self._sp.current_playback)
        except Exception as exc:
            # Evita spamear la consola por errores de red temporales.
            now = time.time()
            if now - self._last_playback_error_log > 30:
                if self._suppressed_playback_errors:
                    logging.warning(
                        "Failed to get current playback (%d errors suppressed): %s",
                        self._suppressed_playback_errors,
                        exc,
                    )
                    self._suppressed_playback_errors = 0
                else:
                    logging.warning("Failed to get current playback: %s", exc)
                self._last_playback_error_log = now
            else:
                self._suppressed_playback_errors += 1
            return None

    # Inicia un loop que detecta cambios de pista.
    async def start_watch(
        self,
        on_track_start: Callable[[str], Any],
        on_playback: Optional[Callable[[Optional[Dict[str, Any]]], Any]] = None,
    ) -> None:
        if self._watch_task is not None:
            return
        self._watch_task = asyncio.create_task(self._watch_loop(on_track_start, on_playback))

    # Indica si hay una reproducción prioritaria activa.
    def is_priority_active(self) -> bool:
        return self._priority_active

    # Loop de polling para cambios de canción.
    async def _watch_loop(
        self,
        on_track_start: Callable[[str], Any],
        on_playback: Optional[Callable[[Optional[Dict[str, Any]]], Any]] = None,
    ) -> None:
        while True:
            try:
                playback = await self.current_playback()
                if playback is not None and on_playback is not None:
                    result = on_playback(playback)
                    if asyncio.iscoroutine(result):
                        await result
                if playback and playback.get("is_playing"):
                    item = playback.get("item") or {}
                    uri = item.get("uri")
                    if uri and uri != self._last_playing_uri:
                        self._last_playing_uri = uri
                        result = on_track_start(uri)
                        if asyncio.iscoroutine(result):
                            await result
            except Exception:
                logging.exception("Playback watch loop error")
            await asyncio.sleep(self._poll_interval)

    # Reproduce prioridad e interrumpe la pista actual.
    async def play_priority(self, request, queue_manager) -> bool:
        async with self._priority_lock:
            if self._priority_active:
                return False
            self._priority_active = True

        try:
            started = await self.start_playback(request.uri)
        except self.SpotifyTransientError:
            async with self._priority_lock:
                self._priority_active = False
            raise
        if not started:
            async with self._priority_lock:
                self._priority_active = False
            return False

        queue_manager.remove_by_uri(request.uri)

        if self._priority_task:
            self._priority_task.cancel()
        self._priority_task = asyncio.create_task(
            self._priority_finish_after(request.duration_ms, queue_manager)
        )
        return True

    # Espera al final de la canción prioritaria y luego salta.
    async def _priority_finish_after(self, duration_ms: int, queue_manager) -> None:
        await asyncio.sleep(max(1, int(duration_ms / 1000) + 1))
        try:
            await self.skip()
        except self.SpotifyTransientError:
            pass

        async with self._priority_lock:
            self._priority_active = False

        next_priority = queue_manager.pop_next_priority()
        if next_priority:
            started = await self.play_priority(next_priority, queue_manager)
            if not started:
                # Re-add if playback failed
                queue_manager.add(next_priority, priority=True)
