"""Modo cursor: movimiento, clic, arrastre y elección de pantalla.

Tres decisiones gobiernan este módulo, y las tres nacen de que un cursor
manejado con la mano falla de formas que un ratón no conoce:

**Una sola pantalla a la vez.** Mapear la mano sobre el escritorio virtual
completo parece lo natural y es lo que arruina el control con varios monitores:
dos pantallas 16:9 en horizontal forman un rectángulo de relación 3:1, así que
el mismo desplazamiento de mano recorre casi el doble en horizontal que en
vertical. La proporción se rompe, el temblor se amplifica y los bordes —donde
viven la barra de tareas y los botones de ventana— quedan fuera de alcance. Se
trabaja sobre un monitor, y la zona activa se recorta para que su relación
coincida con la de ese monitor.

**Sobrebarrido en los bordes.** El seguimiento se degrada justo en el límite del
encuadre, que es donde habría que llevar la mano para tocar la barra de tareas.
La zona activa se mapea con un margen que se recorta contra el borde, de modo
que la última franja de recorrido aterriza siempre en el píxel del borde.

**Los clics se filtran en el tiempo.** Al mover la mano el clasificador lee
dedos recogidos durante uno o dos fotogramas, y cada lectura suelta era un clic:
de ahí los clics fantasma y las pestañas que se cerraban solas. Un botón solo se
pulsa tras varios fotogramas seguidos con la mano cerrada.
"""

from __future__ import annotations

import math
from typing import Any

from . import landmarks as lmk
from .recognizer import HandResult
from .win import input as win_input


class MouseController:
    """Traduce la posición y la pose de la mano en movimiento y botones."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.base_region = tuple(cfg.get("active_region", [0.20, 0.15, 0.80, 0.75]))
        self.min_alpha = float(cfg.get("min_smoothing", 0.10))
        self.max_alpha = float(cfg.get("max_smoothing", 0.55))
        self.accel_distance = float(cfg.get("accel_distance", 160.0))
        self.deadzone_px = float(cfg.get("deadzone_px", 2.0))
        self.overscan = float(cfg.get("edge_overscan", 0.07))
        self.press_frames = int(cfg.get("press_frames", 4))
        self.release_frames = int(cfg.get("release_frames", 2))
        self.min_index_reach = float(cfg.get("min_index_reach", 0.80))

        win_input.enable_dpi_awareness()
        self.screens = win_input.monitors()
        requested = cfg.get("monitor")
        self.monitor = 0 if requested is None else min(max(int(requested), 0),
                                                       len(self.screens) - 1)
        self.frame_aspect = 16.0 / 9.0
        self.region = self.base_region
        self._recompute()

        self._pos: tuple[float, float] | None = None
        self._reference = ""
        self._offset = (0.0, 0.0)
        self._button_down = False
        self._closed_frames = 0
        self._open_frames = 0
        self.status = ""

    # ------------------------------------------------------------------ #
    # Pantalla de destino
    # ------------------------------------------------------------------ #

    @property
    def screen(self) -> tuple[int, int, int, int]:
        return self.screens[self.monitor]

    @property
    def monitor_label(self) -> str:
        _, _, w, h = self.screen
        return f"PANTALLA {self.monitor + 1}/{len(self.screens)} · {w}×{h}"

    def next_monitor(self) -> int:
        """Pasa a la siguiente pantalla y devuelve su número (empezando en 1)."""
        self.monitor = (self.monitor + 1) % len(self.screens)
        self._recompute()
        self._pos = None
        self._reference = ""
        return self.monitor + 1

    def set_frame_aspect(self, aspect: float) -> None:
        if abs(aspect - self.frame_aspect) > 1e-3:
            self.frame_aspect = aspect
            self._recompute()

    def _recompute(self) -> None:
        """Recorta la zona activa para que su relación coincida con la pantalla.

        Sin esto, una zona 16:9 sobre una pantalla de otra relación estira el
        movimiento en un eje: la mano tendría que recorrer distinto para avanzar
        lo mismo en horizontal que en vertical.
        """
        x0, y0, x1, y1 = self.base_region
        _, _, sw, sh = self.screen
        target = sw / max(sh, 1)
        # Anchura y altura en unidades comparables (altura del fotograma).
        width = (x1 - x0) * self.frame_aspect
        height = y1 - y0
        if width / height > target:
            new_width = height * target / self.frame_aspect
            centre = (x0 + x1) / 2
            x0, x1 = centre - new_width / 2, centre + new_width / 2
        else:
            new_height = width / target
            centre = (y0 + y1) / 2
            y0, y1 = centre - new_height / 2, centre + new_height / 2
        self.region = (x0, y0, x1, y1)

    def reset(self) -> None:
        """Suelta cualquier botón pendiente. Imprescindible al salir del modo."""
        self._release_button()
        self._pos = None
        self._reference = ""
        self._offset = (0.0, 0.0)
        self._closed_frames = 0
        self._open_frames = 0
        self.status = ""

    # ------------------------------------------------------------------ #

    def update(self, hand: HandResult, frozen: bool = False) -> None:
        """Actualiza cursor y botones. Con ``frozen`` el cursor se queda quieto.

        Se congela mientras la pinza está enganchada: durante un desplazamiento
        el índice sigue fuera, y sin esto el cursor se arrastraría por la
        pantalla al mismo tiempo que se hace scroll.
        """
        if not hand.present:
            self.reset()
            self.status = "sin mano"
            return

        lm = hand.landmarks
        # El puño se reconoce por el índice recogido contra la palma, que es
        # invariante a la orientación de la mano.
        closed = lmk.index_reach(lm) < self.min_index_reach
        if closed:
            self._closed_frames += 1
            self._open_frames = 0
        else:
            self._open_frames += 1
            self._closed_frames = 0

        if closed:
            self._move(lm, "palm")
            if not self._button_down and self._closed_frames >= self.press_frames:
                win_input.mouse_down("left")
                self._button_down = True
            self.status = "arrastrando" if self._button_down else "cerrando…"
            return

        if self._button_down and self._open_frames >= self.release_frames:
            self._release_button()

        if frozen:
            self.status = "desplazando"
            return

        self._move(lm, "index")
        self.status = "arrastrando" if self._button_down else "cursor"

    # ------------------------------------------------------------------ #

    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        """Punto del encuadre a píxeles de la pantalla activa, con sobrebarrido."""
        nx, ny = lmk.remap_to_screen(x, y, self.region)
        span = max(1.0 - 2.0 * self.overscan, 1e-6)
        nx = min(max((nx - self.overscan) / span, 0.0), 1.0)
        ny = min(max((ny - self.overscan) / span, 0.0), 1.0)
        sx, sy, sw, sh = self.screen
        return (sx + nx * (sw - 1), sy + ny * (sh - 1))

    def _move(self, lm, reference: str) -> None:
        point = (lmk.palm_center(lm) if reference == "palm"
                 else lm[lmk.INDEX_TIP, :2])
        raw = self._to_screen(float(point[0]), float(point[1]))

        if reference != self._reference:
            # Al cambiar de referencia se congela la diferencia con la posición
            # actual: cerrar el puño agarra donde está el cursor, no lo teletransporta.
            self._offset = ((self._pos[0] - raw[0], self._pos[1] - raw[1])
                            if self._pos is not None else (0.0, 0.0))
            self._reference = reference

        sx, sy, sw, sh = self.screen
        target = (min(max(raw[0] + self._offset[0], sx), sx + sw - 1),
                  min(max(raw[1] + self._offset[1], sy), sy + sh - 1))

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

    @property
    def button_down(self) -> bool:
        return self._button_down

    def click(self, button: str = "left") -> None:
        self._release_button()
        win_input.click(button)

    def double_click(self) -> None:
        self._release_button()
        win_input.click("left")
        win_input.click("left")
