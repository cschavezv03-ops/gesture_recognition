"""Captura de vídeo desde Iriun Webcam (o cualquier cámara DirectShow/MSMF).

Iriun transmite desde el móvil por red, así que su latencia es irregular: si se
lee la cámara de forma síncrona en el bucle principal, los fotogramas se
acumulan en el búfer del driver y el retardo percibido crece sin parar. Por eso
la captura vive en un hilo propio que descarta todo salvo el fotograma más
reciente.
"""

from __future__ import annotations

import logging
import threading
import time

import cv2

log = logging.getLogger(__name__)

BACKENDS = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "any": cv2.CAP_ANY,
}


def list_cameras(backend: str = "dshow", max_index: int = 6) -> list[tuple[int, int, int]]:
    """Sondea los índices disponibles y devuelve ``(índice, ancho, alto)``."""
    api = BACKENDS.get(backend, cv2.CAP_ANY)
    found: list[tuple[int, int, int]] = []
    for idx in range(max_index):
        cap = cv2.VideoCapture(idx, api)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                found.append((idx, frame.shape[1], frame.shape[0]))
        cap.release()
    return found


class CameraStream:
    """Lector de cámara en segundo plano que siempre entrega el último fotograma."""

    def __init__(
        self,
        index: int | None = 0,
        backend: str = "dshow",
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        mirror: bool = True,
    ) -> None:
        self.backend = backend
        self.mirror = mirror
        self.index = self._resolve_index(index, backend)

        api = BACKENDS.get(backend, cv2.CAP_ANY)
        self._cap = cv2.VideoCapture(self.index, api)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"No se pudo abrir la cámara {self.index} con backend '{backend}'. "
                "¿Está corriendo Iriun Webcam y conectado el móvil?"
            )

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, fps)
        # Búfer mínimo: reduce el retardo cuando el driver lo respeta.
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._frame = None
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

        # Espera a que llegue el primer fotograma antes de dar por lista la cámara.
        deadline = time.monotonic() + 5.0
        while self._seq == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._seq == 0:
            self.release()
            raise RuntimeError(
                f"La cámara {self.index} se abrió pero no entregó ningún fotograma."
            )

        h, w = self._frame.shape[:2]
        log.info("Cámara %d (%s) lista a %dx%d", self.index, backend, w, h)

    @staticmethod
    def _resolve_index(index: int | None, backend: str) -> int:
        """Con ``index: null`` elige automáticamente la cámara de mayor resolución.

        Iriun suele registrar dos dispositivos y solo uno entrega HD; la de mayor
        resolución es prácticamente siempre la correcta.
        """
        if index is not None:
            return index
        cameras = list_cameras(backend)
        if not cameras:
            raise RuntimeError(
                "No se detectó ninguna cámara. Abre Iriun Webcam en el PC y en el móvil."
            )
        best = max(cameras, key=lambda c: c[1] * c[2])
        log.info("Cámara autodetectada: índice %d (%dx%d)", *best)
        return best[0]

    def _loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok or frame is None:
                failures += 1
                if failures > 100:
                    log.error("La cámara dejó de entregar fotogramas.")
                    break
                time.sleep(0.01)
                continue
            failures = 0
            if self.mirror:
                # Espejo: mover la mano a la derecha debe mover el cursor a la derecha.
                frame = cv2.flip(frame, 1)
            with self._lock:
                self._frame = frame
                self._seq += 1

    def read(self):
        """Devuelve ``(seq, frame)``; ``seq`` permite saltar fotogramas repetidos."""
        with self._lock:
            if self._frame is None:
                return 0, None
            return self._seq, self._frame

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def release(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._cap.release()

    def __enter__(self) -> CameraStream:
        return self

    def __exit__(self, *exc) -> None:
        self.release()
