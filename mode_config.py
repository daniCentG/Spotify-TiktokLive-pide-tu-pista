from __future__ import annotations

import json
import os
from threading import Lock
from typing import Optional


VALID_MODES = {"donation", "free"}

# Configuración de Modo: Free - Donation
class ModeConfig:
    def __init__(self, path: str, default: str = "donation") -> None:
        self._path = path
        self._lock = Lock()
        self._mode = default if default in VALID_MODES else "donation"
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return
        mode = data.get("mode")
        if mode in VALID_MODES:
            self._mode = mode

    def get_mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> bool:
        if mode not in VALID_MODES:
            return False
        with self._lock:
            self._mode = mode
            try:
                with open(self._path, "w", encoding="utf-8") as fh:
                    json.dump({"mode": mode}, fh, ensure_ascii=True, indent=2)
            except Exception:
                return False
        return True

    def to_dict(self) -> dict:
        return {"mode": self.get_mode()}


def normalize_mode(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lowered = value.strip().lower()
    return lowered if lowered in VALID_MODES else None
