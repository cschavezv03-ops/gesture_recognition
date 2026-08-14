"""Conmutador de ventanas propio: agarrar, apuntar y soltar.

Windows no deja apuntar a una miniatura concreta de su Alt+Tab —solo recorrer la
fila con las flechas—, así que el conmutador se dibuja aquí. Eso permite lo que
de verdad se pretende: señalar directamente la ventana que quieres, en dos
dimensiones, igual que se señala un objeto.

El ciclo es físico de principio a fin. Cerrar el puño **agarra** el escritorio y
despliega la rejilla; el índice **señala**; abrir la mano **suelta** sobre lo
señalado. Y si en vez de soltar sobre una ventana se lleva el dedo a un borde,
la ventana se acopla a esa mitad de la pantalla: es el gesto de lanzarla a un
lado. Enganche y liberación son explícitos, de modo que nada puede dispararse
por moverse.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from . import landmarks as lmk
from .recognizer import HandResult
from .win import windows as win

#: Bandas de los bordes que actúan como destinos de acoplado.
EDGE_LEFT = "left"
EDGE_RIGHT = "right"
EDGE_TOP = "top"

EDGE_LABELS = {
    EDGE_LEFT: "MITAD IZQUIERDA",
    EDGE_RIGHT: "MITAD DERECHA",
    EDGE_TOP: "MAXIMIZAR",
}


@dataclass
class SwitcherResult:
    """Lo que ocurrió al soltar la mano."""

    action: str            # 'activate' | 'snap' | 'cancel'
    window: win.Window | None = None
    edge: str = ""

    @property
    def message(self) -> str:
        if self.action == "activate" and self.window:
            return self.window.title[:42]
        if self.action == "snap" and self.window:
            return f"{EDGE_LABELS.get(self.edge, self.edge)} · {self.window.title[:28]}"
        return "Cancelado"


class WindowSwitcher:
    """Rejilla de ventanas seleccionable apuntando con el dedo."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.region = tuple(cfg.get("region", [0.22, 0.18, 0.78, 0.74]))
        self.max_windows = int(cfg.get("max_windows", 9))
        self.edge_band = float(cfg.get("edge_band", 0.13))
        # Franja inferior reservada: si la última fila llegara al borde, el texto
        # de ayuda quedaría escrito encima de una ventana seleccionable.
        self.bottom_margin = float(cfg.get("bottom_margin", 0.10))
        self.lost_timeout = float(cfg.get("lost_timeout", 0.7))
        self.confirm_frames = int(cfg.get("confirm_frames", 3))
        self.snap_enabled = bool(cfg.get("snap", True))

        self.active = False
        self.windows: list[win.Window] = []
        self.selected: int | None = None
        self.edge = ""
        self.pointer = (0.5, 0.5)
        self.columns = 1
        self.rows = 1
        self._open_frames = 0
        self._lost_since: float | None = None

    # ------------------------------------------------------------------ #

    def open(self) -> bool:
        """Despliega la rejilla con las ventanas abiertas. Falso si no hay ninguna."""
        self.windows = win.list_windows(self.max_windows)
        if len(self.windows) < 2:
            # Con una sola ventana no hay nada que conmutar.
            return False
        self.columns = min(len(self.windows), 3 if len(self.windows) <= 6 else 4)
        self.rows = math.ceil(len(self.windows) / self.columns)
        self.active = True
        # Se preselecciona la ventana anterior, que es a la que se salta el 90 %
        # de las veces; así soltar la mano sin apuntar hace lo esperado.
        self.selected = 1
        self.edge = ""
        self.pointer = (0.5, 0.5)
        self._open_frames = 0
        self._lost_since = None
        return True

    def close(self) -> None:
        self.active = False
        self.selected = None
        self.edge = ""
        self._open_frames = 0
        self._lost_since = None

    # ------------------------------------------------------------------ #

    def update(self, hand: HandResult, now: float) -> SwitcherResult | None:
        """Procesa un fotograma. Devuelve el resultado cuando se suelta la mano."""
        if not self.active:
            return None

        if not hand.present:
            if self._lost_since is None:
                self._lost_since = now
            elif now - self._lost_since > self.lost_timeout:
                self.close()
                return SwitcherResult(action="cancel")
            return None
        self._lost_since = None

        lm = hand.landmarks
        _, index, middle, ring, pinky = lmk.fingers_extended(lm)
        open_hand = index and middle and ring and pinky

        if open_hand:
            # La mano abierta se exige durante varios fotogramas: al pasar de
            # señalar a soltar, los dedos se extienden de uno en uno y una
            # lectura suelta confirmaría antes de tiempo.
            self._open_frames += 1
            if self._open_frames >= self.confirm_frames:
                return self._commit()
            return None
        self._open_frames = 0

        if index:
            self._aim(lm)
        return None

    def _aim(self, lm) -> None:
        """Traduce la punta del índice en una casilla de la rejilla o un borde."""
        x, y = lmk.remap_to_screen(
            float(lm[lmk.INDEX_TIP, 0]), float(lm[lmk.INDEX_TIP, 1]), self.region
        )
        self.pointer = (x, y)

        if self.snap_enabled and x < self.edge_band:
            self.edge = EDGE_LEFT
            return
        if self.snap_enabled and x > 1.0 - self.edge_band:
            self.edge = EDGE_RIGHT
            return
        if self.snap_enabled and y < self.edge_band:
            self.edge = EDGE_TOP
            return

        # Fuera de los bordes se elige casilla, y la selección queda fijada:
        # al desplazarse luego hacia un borde hay que seguir sabiendo sobre qué
        # ventana se está actuando.
        self.edge = ""
        gx0, gy0, gw, gh = self.grid
        column = min(max(int((x - gx0) / gw * self.columns), 0), self.columns - 1)
        row = min(max(int((y - gy0) / gh * self.rows), 0), self.rows - 1)
        position = row * self.columns + column
        if position < len(self.windows):
            self.selected = position

    def _commit(self) -> SwitcherResult:
        """Decide qué hacer, sin hacerlo.

        La ejecución queda en manos de quien llama para que el modo de prueba
        pueda enseñar la decisión sin mover ninguna ventana de verdad.
        """
        window = (self.windows[self.selected]
                  if self.selected is not None and self.selected < len(self.windows)
                  else None)
        edge = self.edge
        self.close()

        if window is None or not window.alive:
            return SwitcherResult(action="cancel")
        if edge:
            return SwitcherResult(action="snap", window=window, edge=edge)
        return SwitcherResult(action="activate", window=window)

    # ------------------------------------------------------------------ #

    @property
    def grid(self) -> tuple[float, float, float, float]:
        """Zona de la rejilla en coordenadas 0-1, dejando fuera las bandas de acoplado.

        La rejilla y las bandas no se solapan a propósito: si una casilla llegara
        hasta el borde, apuntar a la ventana de la esquina acoplaría sin querer.
        """
        band = self.edge_band if self.snap_enabled else 0.0
        return (band, band, 1.0 - band * 2.0, 1.0 - band - self.bottom_margin)

    def cell(self, position: int) -> tuple[float, float, float, float]:
        """Casilla ``(x, y, ancho, alto)`` en coordenadas 0-1 de pantalla."""
        gx, gy, gw, gh = self.grid
        column = position % self.columns
        row = position // self.columns
        width, height = gw / self.columns, gh / self.rows
        return (gx + column * width, gy + row * height, width, height)
