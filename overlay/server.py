from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from mode_config import normalize_mode


# Crea el servidor del overlay
def create_app(queue_manager, now_playing_provider=None, mode_config=None) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    # Evita cache para que recargue cambios de estilo
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    @app.after_request
    def _no_cache(response):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    def _get_mode() -> str:
        if mode_config is None:
            return "donation"
        return mode_config.get_mode()

    def _commands_for_mode(mode: str) -> list[dict]:
        if mode == "donation":
            return [
                {"text": "1 Rosa -> !play Cancion - Artista", "icon": "/static/rosaregalo.png"},
                {"text": "1 Corazon coreano -> !skip", "icon": "/static/corazon-coreano-Regalo.png"},
                {"text": "1 Rosquilla -> !play Cancion - Artista (prioridad)", "icon": "/static/donutsRegalo.png"},
            ]
        return []

    # Página principal del overlay.
    @app.route("/")
    def index():
        return render_template("overlay.html")

    # Estado en JSON para refrescar cola en el navegador.
    @app.route("/state")
    def state():
        mode = _get_mode()
        payload = {
            "queue": queue_manager.get_display_queue(),
            "queue_total": queue_manager.count_total(),
            "commands": _commands_for_mode(mode),
            "mode": mode,
        }
        if now_playing_provider is not None:
            payload["now_playing"] = now_playing_provider()
        return jsonify(payload)

    # Panel para cambiar modo.
    @app.route("/panel", methods=["GET", "POST"])
    def panel():
        message = None
        current_mode = _get_mode()
        if request.method == "POST":
            selected = normalize_mode(request.form.get("mode"))
            if mode_config is None:
                message = "No hay configuracion activa."
            elif selected and mode_config.set_mode(selected):
                current_mode = selected
                message = "Modo actualizado."
            else:
                message = "Modo invalido."
        return render_template("panel.html", current_mode=current_mode, message=message)

    # Health check.
    @app.route("/health")
    def health():
        return "OK"

    return app
