"""Modo ratón: control del cursor, clic, arrastre y desplazamiento con la mano.

El cursor es la parte más exigente del proyecto: la señal de MediaPipe tiene un
temblor de uno o dos píxeles que, amplificado al mapear el encuadre sobre toda
la pantalla, hace imposible acertar a un botón. Se corrige con un suavizado de
factor adaptativo: mucha inercia cuando la mano está casi quieta (precisión) y
poca cuando se mueve rápido (respuesta).

El botón izquierdo es el puño, que es el gesto natural de agarrar. Eso obliga a
cambiar de punto de referencia sobre la marcha: con el puño cerrado no hay índice
al que seguir, así que el cursor pasa a seguir el centro de la palma. El cambio
se compensa con un desplazamiento para que el cursor no dé un salto justo en el
momento de agarrar, que es cuando más molesta.
"""

from __future__ import annotations

import math
from typing import Any

from . import landmarks as lmk
from .recognizer import HandResult
from .win import input as win_input


class MouseController:
    """Traduce la posición y la pose de la mano en movimiento y botones del ratón."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.region = tuple(cfg.get("active_region", [0.2, 0.15, 0.8, 0.75]))
        self.min_alpha = float(cfg.get("min_smoothing", 0.12))
        self.max_alpha = float(cfg.get("max_smoothing", 0.65))
        self.accel_distance = float(cfg.get("accel_distance", 120.0))
        self.deadzone_px = float(cfg.get("deadzone_px", 1.5))

        win_input.enable_dpi_awareness()
        self.screen_x, self.screen_y, self.screen_w, self.screen_h = win_input.virtual_screen()

        self._pos: tuple[float, float] | None = None
        self._reference = ""             # 'index' o 'palm'
        self._offset = (0.0, 0.0)
        self._button_down = False
        self.status = ""

    def reset(self) -> None:
        """Suelta cualquier botón pendiente. Imprescindible al salir del modo."""
        self._release_button()
        self._pos = None
        self._reference = ""
        self._offset = (0.0, 0.0)
        self.status = ""

    # ------------------------------------------------------------------ #

    def update(self, hand: HandResult, frozen: bool = False) -> None:
        """Actualiza cursor y botones. Con ``frozen`` el cursor se queda quieto.

        Se congela mientras la pinza está enganchada: durante un desplazamiento
        el índice sigue extendido, y sin esto el cursor se arrastraría por la
        pantalla al mismo tiempo que se hace scroll.
        """
        if not hand.present:
            self.reset()
            self.status = "sin mano"
            return

        lm = hand.landmarks
        _, index, middle, _, _ = lmk.fingers_extended(lm)

        # --- Puño: agarrar. El cursor pasa a seguir la palma ---------------
        if not index and not middle:
            self._move(lm, "palm")
            if not self._button_down:
                win_input.mouse_down("left")
                self._button_down = True
            self.status = "arrastrando"
            return

        # --- Índice extendido: apuntar -------------------------------------
        self._release_button()
        if not index:
            self.status = "cursor en pausa"
            return
        if frozen:
            self.status = "desplazando"
            return

        self._move(lm, "index")
        self.status = "cursor"

    # ------------------------------------------------------------------ #

    def _move(self, lm, reference: str) -> None:
        point = (lmk.palm_center(lm) if reference == "palm"
                 else lm[lmk.INDEX_TIP, :2])
        nx, ny = lmk.remap_to_screen(float(point[0]), float(point[1]), self.region)
        raw = (self.screen_x + nx * (self.screen_w - 1),
               self.screen_y + ny * (self.screen_h - 1))

        if reference != self._reference:
            # Al cambiar de referencia se congela la diferencia con la posición
            # actual: cerrar el puño agarra donde está el cursor, no lo teletransporta.
            self._offset = ((self._pos[0] - raw[0], self._pos[1] - raw[1])
                            if self._pos is not None else (0.0, 0.0))
            self._reference = reference

        target = (
            min(max(raw[0] + self._offset[0], self.screen_x), self.screen_x + self.screen_w - 1),
            min(max(raw[1] + self._offset[1], self.screen_y), self.screen_y + self.screen_h - 1),
        )

        if self._pos is None:
            self._pos = target
        else:
            distance = math.dist(self._pos, target)
            if distance < self.deadzone_px:
                return
            # Factor adaptativo: cuanto más lejos el objetivo, menos inercia.
            alpha = self.min_alpha + (self.max_alpha - self.min_alpha) * min(
                distance / max(self.accel_distance, 1e-6), 1.0
            )
            self._pos = (self._pos[0] + alpha * (target[0] - self._pos[0]),
                         self._pos[1] + alpha * (target[1] - self._pos[1]))

        win_input.move_cursor(int(round(self._pos[0])), int(round(self._pos[1])))

    def _release_button(self) -> None:
        if self._button_down:
            win_input.mouse_up("left")
            self._button_down = False

    def click(self, button: str = "left") -> None:
        self._release_button()
        win_input.click(button)

    def double_click(self) -> None:
        self._release_button()
        win_input.click("left")
        win_input.click("left")
