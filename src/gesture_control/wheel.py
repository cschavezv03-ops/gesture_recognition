"""Rueda de comandos: un menú radial que se abre junto a la mano y se apunta con el dedo.

Siete poses estáticas por cuatro direcciones de barrido es un vocabulario
pequeño, y forzarlo lleva a colgar dos acciones distintas del mismo gesto, que
es justo de donde salen los disparos indeseados. La rueda rompe ese techo: una
sola pose abre un menú, y a partir de ahí se elige apuntando, sin necesidad de
memorizar más poses.

Además resuelve el problema de descubrimiento. Un barrido hay que conocerlo de
antemano; una rueda con las opciones escritas alrededor se lee.

La confirmación es por permanencia y no por un gesto de clic: cerrar la mano
para confirmar la desplaza y cambiaría la opción elegida justo al confirmarla.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import landmarks as lmk
from .engine import Binding, Event
from .recognizer import HandResult


@dataclass
class WheelItem:
    """Una opción de la rueda, con la acción que ejecuta."""

    label: str
    action: str
    args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WheelItem:
        unknown = set(data) - {"label", "action", "args"}
        if unknown:
            raise ValueError(f"Claves desconocidas en la opción {data}: {sorted(unknown)}")
        return cls(**data)


class CommandWheel:
    """Menú radial con selección por apuntado y confirmación por permanencia."""

    def __init__(self, cfg: dict[str, Any], items: dict[str, list[WheelItem]]) -> None:
        self.items_by_mode = items
        self.dwell = float(cfg.get("dwell", 0.75))
        # Radios expresados en anchos de mano: la rueda crece y encoge con la
        # distancia a la cámara, así que el gesto es el mismo de cerca y de lejos.
        self.inner = float(cfg.get("inner_radius", 1.15))
        self.outer = float(cfg.get("outer_radius", 2.7))
        self.lost_timeout = float(cfg.get("lost_timeout", 0.6))

        self.active = False
        self.mode = ""
        self.items: list[WheelItem] = []
        self.center = (0.5, 0.5)
        self.scale = 0.1
        # Las coordenadas de MediaPipe están normalizadas por eje, de modo que
        # una unidad horizontal y una vertical no miden lo mismo en pantalla. Sin
        # corregirlo, los sectores quedan torcidos respecto a lo que se ve.
        self.aspect = 16.0 / 9.0
        self.selected: int | None = None
        self.dwell_progress = 0.0
        self.pointer: tuple[float, float] | None = None
        self._dwell_start: float | None = None
        self._lost_since: float | None = None

    # ------------------------------------------------------------------ #

    def open(self, hand: HandResult, mode: str, aspect: float = 16.0 / 9.0) -> bool:
        """Despliega la rueda alrededor de la mano. Falso si no hay nada que mostrar."""
        items = self.items_by_mode.get(mode) or []
        if not items or not hand.present:
            return False
        self.aspect = aspect
        cx, cy = lmk.palm_center(hand.landmarks)
        # El centro se congela al abrir: si siguiera a la mano, apuntar hacia una
        # opción arrastraría la rueda y nunca se llegaría a ninguna.
        self.center = (float(cx), float(cy))
        # El tamaño de la mano fija cuánto hay que estirar el brazo para alcanzar
        # una opción, pero acotado: con la mano pegada a la cámara la rueda se
        # saldría del encuadre, y muy lejos quedaría tan pequeña que elegir sería
        # cuestión de milímetros.
        self.scale = min(max(lmk.hand_scale(hand.landmarks), 0.055), 0.15)
        self.items = items
        self.mode = mode
        self.active = True
        self.selected = None
        self.dwell_progress = 0.0
        self.pointer = None
        self._dwell_start = None
        self._lost_since = None
        return True

    def close(self) -> None:
        self.active = False
        self.selected = None
        self.dwell_progress = 0.0
        self.pointer = None
        self._dwell_start = None
        self._lost_since = None

    # ------------------------------------------------------------------ #

    def update(self, hand: HandResult, now: float) -> Event | None:
        """Procesa un fotograma. Devuelve el evento cuando una opción se confirma."""
        if not self.active:
            return None

        if not hand.present:
            # Se tolera una pérdida breve de seguimiento antes de cerrar, porque
            # el detector parpadea cuando la mano se estira hacia los bordes.
            if self._lost_since is None:
                self._lost_since = now
            elif now - self._lost_since > self.lost_timeout:
                self.close()
            self._reset_selection()
            return None
        self._lost_since = None

        lm = hand.landmarks
        _, index, middle, _, _ = lmk.fingers_extended(lm)
        if not index and not middle:
            # Mano cerrada: cancelar sin ejecutar nada.
            self.close()
            return None

        tip = lm[lmk.INDEX_TIP, :2]
        self.pointer = (float(tip[0]), float(tip[1]))
        dx = (float(tip[0]) - self.center[0]) * self.aspect
        dy = float(tip[1]) - self.center[1]
        distance = math.hypot(dx, dy) / max(self.scale, 1e-6)

        if distance < self.inner:
            # Zona muerta central: permite dudar sin activar nada.
            self._reset_selection()
            return None

        index_of = self._sector(dx, dy)
        if index_of != self.selected:
            self.selected = index_of
            self._dwell_start = now
            self.dwell_progress = 0.0
            return None

        # Comparación explícita contra None: un instante de inicio de 0.0 es
        # perfectamente válido y `or` lo trataría como ausente.
        elapsed = 0.0 if self._dwell_start is None else now - self._dwell_start
        self.dwell_progress = min(elapsed / max(self.dwell, 1e-3), 1.0)
        if self.dwell_progress >= 1.0:
            item = self.items[index_of]
            self.close()
            return Event(binding=Binding(
                gesture="Wheel", trigger="wheel", action=item.action,
                args=dict(item.args), label=item.label,
            ))
        return None

    def _reset_selection(self) -> None:
        self.selected = None
        self._dwell_start = None
        self.dwell_progress = 0.0

    def _sector(self, dx: float, dy: float) -> int:
        """Sector apuntado. El 0 está arriba y avanzan en sentido horario."""
        count = len(self.items)
        step = 360.0 / count
        # atan2 da 0 a la derecha y crece hacia abajo (la 'y' de la imagen).
        angle = (math.degrees(math.atan2(dy, dx)) + 90.0) % 360.0
        return int((angle + step / 2.0) // step) % count

    def angle_of(self, index: int) -> float:
        """Ángulo central de un sector, en grados de OpenCV (0 = derecha)."""
        return index * (360.0 / len(self.items)) - 90.0
