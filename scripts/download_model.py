"""Descarga el modelo `gesture_recognizer.task` de MediaPipe si no está presente."""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "gesture_recognizer.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/latest/gesture_recognizer.task"
)


def main() -> int:
    if MODEL_PATH.is_file():
        print(f"El modelo ya existe: {MODEL_PATH} ({MODEL_PATH.stat().st_size / 1e6:.1f} MB)")
        return 0

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Descargando el modelo desde {MODEL_URL} ...")
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    except Exception as exc:
        print(f"No se pudo descargar el modelo: {exc}", file=sys.stderr)
        return 1
    print(f"Guardado en {MODEL_PATH} ({MODEL_PATH.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
