"""Envoltorio sobre ``GestureRecognizer`` de MediaPipe Tasks en modo LIVE_STREAM.

LIVE_STREAM ejecuta la inferencia de forma asíncrona: se entrega el fotograma y
el resultado llega por callback. Así el bucle de dibujado nunca se bloquea
esperando a la red neuronal, que es lo que mantiene el HUD fluido aunque la
inferencia tarde más que un fotograma.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

log = logging.getLogger(__name__)

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/latest/gesture_recognizer.task"
)


@dataclass
class HandResult:
    """Resultado de una mano en un fotograma concreto."""

    gesture: str = "None"
    score: float = 0.0
    handedness: str = ""
    #: 21 landmarks normalizados (x, y, z) en el sistema de coordenadas de la imagen.
    landmarks: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.float32))

    @property
    def present(self) -> bool:
        return len(self.landmarks) == 21


class GestureStream:
    """Alimenta fotogramas al reconocedor y expone el último resultado disponible."""

    def __init__(
        self,
        model_path: str | Path,
        delegate: str = "cpu",
        num_hands: int = 1,
        min_hand_detection_confidence: float = 0.5,
        min_hand_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        model_path = Path(model_path)
        if not model_path.is_file():
            raise FileNotFoundError(
                f"No se encontró el modelo en {model_path}. "
                "Ejecuta: python scripts/download_model.py"
            )

        self._lock = threading.Lock()
        self._latest = HandResult()
        self._latest_stamp = 0
        self._inference_ms = 0.0
        self._sent_at: dict[int, float] = {}

        # Se carga el modelo a memoria en lugar de pasar la ruta: evita problemas
        # cuando el proyecto vive en un recurso de red (\\wsl.localhost\...).
        model_buffer = model_path.read_bytes()

        self.delegate = self._build(model_buffer, delegate, num_hands,
                                    min_hand_detection_confidence,
                                    min_hand_presence_confidence,
                                    min_tracking_confidence)

    def _build(self, buffer, delegate, num_hands, det, pres, track) -> str:
        """Crea el reconocedor; si se pide GPU y no está disponible, cae a CPU.

        Los wheels de MediaPipe para Windows se compilan sin soporte de GPU, así
        que ``delegate: gpu`` fallará ahí. El aviso lo deja explícito en vez de
        dar la falsa impresión de que se está acelerando.
        """
        order = ["gpu", "cpu"] if delegate.lower() == "gpu" else ["cpu"]
        last_error: Exception | None = None
        for name in order:
            enum = (mp_python.BaseOptions.Delegate.GPU if name == "gpu"
                    else mp_python.BaseOptions.Delegate.CPU)
            try:
                options = vision.GestureRecognizerOptions(
                    base_options=mp_python.BaseOptions(model_asset_buffer=buffer, delegate=enum),
                    running_mode=vision.RunningMode.LIVE_STREAM,
                    num_hands=num_hands,
                    min_hand_detection_confidence=det,
                    min_hand_presence_confidence=pres,
                    min_tracking_confidence=track,
                    result_callback=self._on_result,
                )
                self._recognizer = vision.GestureRecognizer.create_from_options(options)
                if name == "cpu" and delegate.lower() == "gpu":
                    log.warning(
                        "El delegate GPU no está disponible en este build de MediaPipe "
                        "(los wheels de Windows se compilan sin soporte GPU). "
                        "Se usará CPU con XNNPACK, suficiente para 30+ FPS con este modelo."
                    )
                log.info("Reconocedor iniciado con delegate %s", name.upper())
                return name
            except Exception as exc:
                last_error = exc
                log.debug("Delegate %s no disponible: %s", name, exc)
        raise RuntimeError(f"No se pudo inicializar el reconocedor: {last_error}")

    def _on_result(self, result, output_image, timestamp_ms: int) -> None:
        """Callback de MediaPipe; corre en un hilo interno de la librería."""
        hand = HandResult()
        if result.gestures and result.hand_landmarks:
            top = result.gestures[0][0]
            hand.gesture = top.category_name or "None"
            hand.score = float(top.score)
            if result.handedness and result.handedness[0]:
                hand.handedness = result.handedness[0][0].category_name or ""
            hand.landmarks = np.array(
                [(p.x, p.y, p.z) for p in result.hand_landmarks[0]], dtype=np.float32
            )
        sent = self._sent_at.pop(timestamp_ms, None)
        with self._lock:
            self._latest = hand
            self._latest_stamp = timestamp_ms
            if sent is not None:
                self._inference_ms = (time.monotonic() - sent) * 1000.0
            # Descarta marcas huérfanas por si algún resultado nunca llega.
            if len(self._sent_at) > 60:
                self._sent_at.clear()

    def submit(self, frame_bgr: np.ndarray, timestamp_ms: int) -> None:
        """Envía un fotograma BGR a la inferencia. Las marcas deben ser crecientes."""
        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        with self._lock:
            self._sent_at[timestamp_ms] = time.monotonic()
        self._recognizer.recognize_async(image, timestamp_ms)

    def latest(self) -> tuple[HandResult, float]:
        """Último resultado recibido junto al tiempo de inferencia en milisegundos."""
        with self._lock:
            return self._latest, self._inference_ms

    def close(self) -> None:
        self._recognizer.close()

    def __enter__(self) -> GestureStream:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
