from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import unicodedata
from typing import Optional, Tuple

# Configuración principal del bot y reglas de negocio.

from dotenv import load_dotenv
from TikTokLive.events import CommentEvent, GiftEvent

from anti_spam import AntiSpam
from queue_manager import QueueManager, SongRequest
from spotify_controller import SpotifyController
from tiktok_listener import TikTokListener
from overlay.server import create_app
from mode_config import ModeConfig

# Límites y reglas globales.
PLAY_WINDOW_SEC = 180
COOLDOWN_SEC = 10
MAX_SONGS_PER_USER = 2
PLAY_ATTEMPTS = 3
SKIP_WINDOW_SEC = 60
GLOBAL_RATE_LIMIT = 5
GLOBAL_WINDOW_SEC = 10
MAX_DURATION_MS = 5 * 60 * 1000
DUPLICATE_WINDOW_SEC = 20 * 60
MAX_QUERY_LEN = 200
MAX_TITLE_LEN = 120
MAX_ARTIST_LEN = 120

# Config editable de regalos.
GIFT_COIN_ACTIONS = {
    1: "play",
    5: "skip",
    30: "priority",
}

GIFT_NAME_ACTIONS = {
    "play": ["rose", "rosa"],
    "skip": ["korean heart", "korean hearts", "corazon coreano", "corazones coreanos", "coreano"],
    "priority": ["donut", "doughnut", "rosquilla", "rosquillas"],
}


# Detecta si el nombre del regalo contiene alguna palabra clave.
def _normalize_gift_text(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text


def _gift_matches(name: str, keywords) -> bool:
    if not name:
        return False
    lname = _normalize_gift_text(name)
    return any(k in lname for k in keywords)


def _action_from_gift_name(name: str) -> Optional[str]:
    for action, keywords in GIFT_NAME_ACTIONS.items():
        if _gift_matches(name, keywords):
            return action
    return None


def _get_gift_coin_value(event: GiftEvent) -> Optional[int]:
    gift = getattr(event, "gift", None)
    candidates = []
    for source in (event, gift):
        if not source:
            continue
        for attr in ("coin_count", "diamond_count", "price", "value", "cost"):
            val = getattr(source, attr, None)
            if isinstance(val, int) and val > 0:
                candidates.append(val)
            elif isinstance(val, str) and val.isdigit():
                candidates.append(int(val))
    return candidates[0] if candidates else None


# Normaliza identificadores de usuario.
def _get_user_id(event_user) -> str:
    return (
        getattr(event_user, "unique_id", None)
        or str(getattr(event_user, "user_id", ""))
        or "unknown"
    )


# Nombre visible para mostrar en overlay/logs.
def _get_user_name(event_user) -> str:
    return getattr(event_user, "nickname", None) or _get_user_id(event_user)


# Filtro de bots.
def _is_bot(event_user) -> bool:
    if getattr(event_user, "is_bot", False):
        return True
    unique_id = str(getattr(event_user, "unique_id", "")).lower()
    if unique_id.startswith("bot") or unique_id.endswith("bot"):
        return True
    return False


# Parsea "Cancion - Artista" con tolerancia a espacios y guiones largos.
def _parse_play_query(query: str) -> Optional[Tuple[str, str]]:
    if not query:
        return None
    normalized = query.replace("\u2013", "-").replace("\u2014", "-")
    if " - " in normalized:
        song_part, artist_part = [p.strip() for p in normalized.split(" - ", 1)]
    else:
        if "-" not in normalized:
            return None
        left, right = normalized.rsplit("-", 1)
        song_part, artist_part = left.strip(), right.strip()
    if not song_part or not artist_part:
        return None
    return song_part, artist_part


async def main() -> None:
    # Carga variables de entorno.
    load_dotenv()

    tiktok_username = os.getenv("TIKTOK_USERNAME")
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_url = os.getenv("SPOTIFY_REDIRECT_URL")

    if not tiktok_username:
        raise SystemExit("Missing TIKTOK_USERNAME in .env")
    if not client_id or not client_secret or not redirect_url:
        raise SystemExit("Missing Spotify credentials in .env")

    config_path = os.path.join(os.path.dirname(__file__), "mode_config.json")
    mode_config = ModeConfig(config_path)
    tiktok_username_lower = tiktok_username.lower()

    # Logging a consola.
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    # Estado en memoria: colas y anti-spam.
    queue_manager = QueueManager(max_display=5)
    anti_spam = AntiSpam(
        cooldown_sec=COOLDOWN_SEC,
        max_songs_per_user=MAX_SONGS_PER_USER,
        play_window_sec=PLAY_WINDOW_SEC,
        skip_window_sec=SKIP_WINDOW_SEC,
        play_attempts=PLAY_ATTEMPTS,
        global_limit=GLOBAL_RATE_LIMIT,
        global_window_sec=GLOBAL_WINDOW_SEC,
    )

    # Estado compartido del tema en reproducción.
    now_playing_lock = threading.Lock()
    now_playing_state = {"is_playing": False, "title": "", "artist": "", "cover_url": ""}

    def _set_now_playing(playback) -> None:
        with now_playing_lock:
            if playback and playback.get("is_playing"):
                item = playback.get("item") or {}
                title = item.get("name") or ""
                artists = item.get("artists") or []
                artist_name = artists[0].get("name") if artists else ""
                album = item.get("album") or {}
                images = album.get("images") or []
                cover_url = images[0].get("url") if images else ""
                now_playing_state["is_playing"] = True
                now_playing_state["title"] = title
                now_playing_state["artist"] = artist_name
                now_playing_state["cover_url"] = cover_url
            else:
                now_playing_state["is_playing"] = False
                now_playing_state["title"] = ""
                now_playing_state["artist"] = ""
                now_playing_state["cover_url"] = ""

    def _get_now_playing() -> dict:
        with now_playing_lock:
            return dict(now_playing_state)

    # Controlador de Spotify (OAuth en memoria).
    spotify = SpotifyController(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_url,
        scope="user-modify-playback-state user-read-playback-state",
        poll_interval_sec=5,
    )

    # Cuando inicia una canción, se elimina de la cola local.
    async def on_track_start(uri: str) -> None:
        removed = queue_manager.remove_by_uri(uri)
        if removed:
            logging.info("Song started, removed from queue: %s", uri)

    await spotify.start_watch(on_track_start, on_playback=_set_now_playing)

    # Servidor del overlay en un hilo aparte.
    app = create_app(queue_manager, now_playing_provider=_get_now_playing, mode_config=mode_config)
    def _run_overlay() -> None:
        try:
            app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
        except OSError:
            logging.exception("Overlay server failed to start (port in use?)")
        except Exception:
            logging.exception("Overlay server failed")

    server_thread = threading.Thread(
        target=_run_overlay,
        daemon=True,
    )
    server_thread.start()

    pending_tasks: set[asyncio.Task] = set()
    play_lock = asyncio.Lock()
    skip_lock = asyncio.Lock()

    def _track_task(task: asyncio.Task) -> None:
        pending_tasks.add(task)
        def _done(t: asyncio.Task) -> None:
            pending_tasks.discard(t)
            try:
                t.result()
            except Exception:
                logging.exception("Background task failed")
        task.add_done_callback(_done)

    # Manejo de regalos: abre ventanas de permiso por usuario.
    async def handle_gift(event: GiftEvent) -> None:
        if mode_config.get_mode() != "donation":
            return
        if _is_bot(event.user):
            return
        user_id = _get_user_id(event.user)
        gift_name = getattr(event.gift, "name", "")
        gift_count = int(getattr(event, "repeat_count", 1) or 1)

        action = None
        coin_value = _get_gift_coin_value(event)
        if coin_value is not None:
            action = GIFT_COIN_ACTIONS.get(coin_value)
        if not action:
            action = _action_from_gift_name(gift_name)

        if action == "play" and gift_count >= 1:
            anti_spam.grant_play(user_id, priority=False)
            logging.info("Gift -> play window granted to %s", user_id)
        elif action == "skip" and gift_count >= 1:
            anti_spam.grant_skip(user_id)
            logging.info("Gift -> skip window granted to %s", user_id)
        elif action == "priority" and gift_count >= 1:
            anti_spam.grant_play(user_id, priority=True)
            logging.info("Gift -> priority play window granted to %s", user_id)

    async def _process_play(user_id: str, user_name: str, text: str) -> None:
        async with play_lock:
            permission = anti_spam.peek_play(user_id)
            if permission is None:
                return

            query = text[5:].strip()
            if len(query) > MAX_QUERY_LEN:
                remaining = anti_spam.record_invalid_format(user_id)
                logging.info("Invalid !play format from %s (attempts left: %s)", user_id, remaining)
                return

            parsed = _parse_play_query(query)
            if not parsed:
                remaining = anti_spam.record_invalid_format(user_id)
                logging.info("Invalid !play format from %s (attempts left: %s)", user_id, remaining)
                return
            song_part, artist_part = parsed

            if len(song_part) > MAX_TITLE_LEN or len(artist_part) > MAX_ARTIST_LEN:
                remaining = anti_spam.record_invalid_format(user_id)
                logging.info("Invalid !play format from %s (attempts left: %s)", user_id, remaining)
                return

            if not anti_spam.check_cooldown(user_id):
                return
            if not anti_spam.check_global_rate():
                return
            if not anti_spam.can_request_song(queue_manager.count_user(user_id)):
                return

            try:
                track = await spotify.search_track_precise(
                    song_part,
                    artist_part,
                    min_title_ratio=0.58,
                    min_artist_ratio=0.50,
                )
            except SpotifyController.SpotifyTransientError:
                return

            if not track:
                remaining = anti_spam.fail_play_attempt(user_id)
                if remaining is not None:
                    logging.info("No Spotify match for: %s (attempts left: %s)", query, remaining)
                else:
                    logging.info("No Spotify match for: %s", query)
                return

            duration_ms = int(track.get("duration_ms", 0))
            explicit = bool(track.get("explicit", False))
            if duration_ms > MAX_DURATION_MS:
                remaining = anti_spam.fail_play_attempt(user_id)
                logging.info("Rejected track over 5 min: %s (attempts left: %s)", track.get("name"), remaining)
                return

            title = track.get("name", "Unknown")
            artists = track.get("artists", [])
            artist_name = artists[0].get("name") if artists else "Unknown"
            uri = track.get("uri")
            if not uri:
                remaining = anti_spam.fail_play_attempt(user_id)
                logging.info("Rejected track without URI: %s (attempts left: %s)", track.get("name"), remaining)
                return

            if queue_manager.is_duplicate(title, artist_name, DUPLICATE_WINDOW_SEC) and not permission:
                logging.info("Duplicate request blocked: %s - %s", title, artist_name)
                return

            request = SongRequest(
                user_id=user_id,
                user_name=user_name,
                title=title,
                artist=artist_name,
                uri=uri,
                duration_ms=duration_ms,
                explicit=explicit,
                requested_at=time.time(),
                priority=bool(permission),
            )

            if permission:
                if spotify.is_priority_active():
                    queue_manager.add(request, priority=True)
                    anti_spam.consume_play(user_id)
                    anti_spam.record_command(user_id)
                    queue_manager.record_recent(title, artist_name)
                    logging.info("Priority already active, queued for later: %s", request.display_title)
                    return

                started = False
                try:
                    started = await spotify.play_priority(request, queue_manager)
                except SpotifyController.SpotifyTransientError:
                    return

                if not started:
                    try:
                        started = await spotify.play_priority(request, queue_manager)
                    except SpotifyController.SpotifyTransientError:
                        return

                if started:
                    anti_spam.consume_play(user_id)
                    anti_spam.record_command(user_id)
                    queue_manager.record_recent(title, artist_name)
                    logging.info("Priority play: %s", request.display_title)
                    return

                try:
                    queued = await spotify.add_to_queue(request.uri)
                except SpotifyController.SpotifyTransientError:
                    return

                if queued:
                    queue_manager.add(request, priority=False)
                    anti_spam.consume_play(user_id)
                    anti_spam.record_command(user_id)
                    queue_manager.record_recent(title, artist_name)
                    logging.info("Queued (fallback): %s", request.display_title)
                else:
                    logging.warning("Priority failed and queue failed: %s", request.display_title)
            else:
                try:
                    queued = await spotify.add_to_queue(request.uri)
                except SpotifyController.SpotifyTransientError:
                    return

                if not queued:
                    logging.warning("Queue failed: %s", request.display_title)
                    return

                queue_manager.add(request, priority=False)
                anti_spam.consume_play(user_id)
                anti_spam.record_command(user_id)
                queue_manager.record_recent(title, artist_name)
                logging.info("Queued: %s", request.display_title)

    async def _process_skip(user_id: str, force: bool = False) -> None:
        async with skip_lock:
            if not force and not anti_spam.peek_skip(user_id):
                return
            if not anti_spam.check_cooldown(user_id):
                return
            if not anti_spam.check_global_rate():
                return

            try:
                skipped = await spotify.skip()
            except SpotifyController.SpotifyTransientError:
                return

            if skipped:
                if not force:
                    if not anti_spam.consume_skip(user_id):
                        return
                anti_spam.record_command(user_id)
                logging.info("Skip requested by %s", user_id)

    # Manejo de comandos en chat.
    async def handle_comment(event: CommentEvent) -> None:
        if _is_bot(event.user):
            return

        mode = mode_config.get_mode()
        is_host = _get_user_id(event.user).lower() == tiktok_username_lower

        text = (event.comment or "").strip()
        lower = text.lower()
        user_id = _get_user_id(event.user)
        user_name = _get_user_name(event.user)

        if lower.startswith("!play"):
            if mode == "free" and anti_spam.peek_play(user_id) is None:
                anti_spam.grant_play(user_id, priority=False)
            task = asyncio.create_task(_process_play(user_id, user_name, text))
            _track_task(task)
            return
        if lower.startswith("!skip"):
            if mode == "donation":
                task = asyncio.create_task(_process_skip(user_id))
                _track_task(task)
                return
            if is_host:
                task = asyncio.create_task(_process_skip(user_id, force=True))
                _track_task(task)
            return

    # Conecta al Live y queda escuchando eventos.
    listener = TikTokListener(
        username=tiktok_username,
        on_comment=handle_comment,
        on_gift=handle_gift,
    )

    await listener.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass




