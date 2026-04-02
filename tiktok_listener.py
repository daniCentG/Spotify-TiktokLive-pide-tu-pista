from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from TikTokLive import TikTokLiveClient
from TikTokLive.events import CommentEvent, GiftEvent, ConnectEvent, DisconnectEvent
from TikTokLive.client.errors import (
    UserOfflineError,
    AlreadyConnectedError,
    WebcastBlocked200Error,
    SignatureRateLimitError,
)
from websockets.exceptions import ConnectionClosedError


# Enlaza eventos de TikTok Live con callbacks async.
class TikTokListener:
    def __init__(
        self,
        username: str,
        on_comment: Callable[[CommentEvent], Awaitable[None]],
        on_gift: Callable[[GiftEvent], Awaitable[None]],
    ) -> None:
        self._client = TikTokLiveClient(unique_id=username)
        self._on_comment = on_comment
        self._on_gift = on_gift
        self._last_offline_log = 0.0
        self._last_disconnect_log = 0.0
        self._last_connected_at: Optional[float] = None
        self._connecting = False
        self._base_backoff_sec = 5
        self._backoff_sec = self._base_backoff_sec
        self._max_backoff_sec = 120
        self._stable_connection_sec = 30
        self._next_connect_at = 0.0
        self._disconnect_cooldown_sec = 10
        self._last_connect_error_log = 0.0

        # Evento de conexión.
        @self._client.on(ConnectEvent)
        async def _on_connect(_: ConnectEvent):
            logging.info("Connected to TikTok Live")
            self._last_connected_at = time.time()

        # Evento de desconexión.
        @self._client.on(DisconnectEvent)
        async def _on_disconnect(_: DisconnectEvent):
            logging.warning("Disconnected from TikTok Live")
            self._adjust_backoff_on_disconnect()
            self._schedule_reconnect(max(self._disconnect_cooldown_sec, self._backoff_sec))

        # Comentarios del chat.
        @self._client.on(CommentEvent)
        async def _handle_comment(event: CommentEvent):
            try:
                await self._on_comment(event)
            except Exception:
                logging.exception("Comment handler failed")

        # Regalos del chat.
        @self._client.on(GiftEvent)
        async def _handle_gift(event: GiftEvent):
            # For streak gifts, only process when the streak ends
            streakable = getattr(event, "streakable", None)
            if streakable is None:
                streakable = getattr(event.gift, "streakable", False)
            if streakable:
                if not event.repeat_end:
                    return
            try:
                await self._on_gift(event)
            except Exception:
                logging.exception("Gift handler failed")

    # Reintenta conexión si falla.
    async def start(self) -> None:
        while True:
            now = time.time()
            if now < self._next_connect_at:
                await asyncio.sleep(max(1, int(self._next_connect_at - now)))
                continue
            if self._connecting:
                await asyncio.sleep(2)
                continue
            try:
                self._connecting = True
                # connect() bloquea hasta que la conexión termina; evita tareas sin await.
                await self._client.connect()
                # Si start() retorna, se asume desconexión.
                now = time.time()
                if now - self._last_disconnect_log > 10:
                    logging.warning("TikTok connection ended. Reconnecting soon...")
                    self._last_disconnect_log = now
                self._schedule_reconnect(self._disconnect_cooldown_sec)
            except asyncio.CancelledError:
                raise
            except AttributeError as exc:
                # Bug conocido en TikTokLive cuando no se inicializa el ping loop.
                if "_ping_loop" in str(exc):
                    logging.warning("TikTokLive internal error (ping loop). Reconnecting...")
                    await self._safe_disconnect()
                    self._bump_backoff()
                    self._schedule_reconnect(self._backoff_sec)
                else:
                    raise
            except UserOfflineError:
                # Usuario offline: evita spam de logs y reintenta más lento.
                now = time.time()
                if now - self._last_offline_log > 30:
                    logging.warning("TikTok user is offline. Waiting to retry...")
                    self._last_offline_log = now
                self._schedule_reconnect(20)
            except AlreadyConnectedError:
                logging.warning("TikTok client already connected. Waiting...")
                self._schedule_reconnect(5)
            except WebcastBlocked200Error:
                logging.warning("TikTok websocket blocked (DEVICE_BLOCKED). Waiting 60s...")
                await self._safe_disconnect()
                self._bump_backoff()
                self._schedule_reconnect(120)
            except SignatureRateLimitError:
                logging.warning("TikTok rate limit reached. Waiting 60s...")
                await self._safe_disconnect()
                self._bump_backoff()
                self._schedule_reconnect(120)
            except ConnectionClosedError:
                logging.warning("TikTok websocket closed. Reconnecting...")
                await self._safe_disconnect()
                self._bump_backoff()
                self._schedule_reconnect(self._backoff_sec)
            except Exception as exc:
                await self._safe_disconnect()
                self._bump_backoff()
                delay = self._backoff_sec
                self._schedule_reconnect(delay)
                now = time.time()
                if now - self._last_connect_error_log > 10:
                    summary = self._summarize_error(exc)
                    logging.warning(
                        "TikTok connection error: %s. Reintentando en %ss",
                        summary,
                        delay,
                    )
                    self._last_connect_error_log = now
            finally:
                self._connecting = False

    async def _safe_disconnect(self) -> None:
        try:
            result = self._client.disconnect()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass

    def _bump_backoff(self) -> None:
        self._backoff_sec = min(
            self._max_backoff_sec, max(self._backoff_sec * 2, self._base_backoff_sec)
        )

    def _schedule_reconnect(self, delay_sec: int) -> None:
        self._next_connect_at = max(self._next_connect_at, time.time() + delay_sec)

    def _adjust_backoff_on_disconnect(self) -> None:
        if not self._last_connected_at:
            # Desconexión antes de recibir eventos: aplica backoff.
            self._bump_backoff()
            return
        duration = time.time() - self._last_connected_at
        self._last_connected_at = None
        if duration >= self._stable_connection_sec:
            self._backoff_sec = self._base_backoff_sec
        else:
            self._bump_backoff()

    @staticmethod
    def _summarize_error(exc: Exception) -> str:
        message = str(exc).strip()
        if len(message) > 160:
            message = f"{message[:160]}..."
        name = type(exc).__name__
        return f"{name}: {message}" if message else name
