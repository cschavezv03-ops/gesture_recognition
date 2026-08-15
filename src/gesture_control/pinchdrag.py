"""Gatillo de pinza: enganchar, arrastrar por pasos y soltar.

Sustituye a los barridos. Un barrido no tiene enganche: se dispara en cuanto la
mano supera un umbral de velocidad con la pose puesta, de modo que levantar el
brazo para entrar en el encuadre cuenta como un barrido hacia arriba y bajarlo
para descansar cuenta como uno hacia abajo. Por eso se abrían pestañas solas.

Aquí hay tres momentos separados, como al usar un botón: juntar los dedos
**engancha**, moverse **arrastra**, separarlos **suelta**. Mientras no se
enganche no ocurre nada, así que el disparo accidental desaparece por
construcción en lugar de por afinar umbrales.

El eje se fija con el primer tramo de recorrido y no vuelve a cambiar hasta
soltar: sin eso, un arrastre horizontal con algo de deriva vertical alternaría
entre cambiar de pestaña y desplazar la página.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import landmarks as lmk
from .engine import Binding, Event
from .recognizer import HandResult


@dataclass
class AxisConfig:
    """Qué hace cada sentido de un eje, y cada cuánto recorrido emite un paso."""

    label: str = ""
    step: float = 0.0
    actions: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], senses: tuple[str, str]) -> AxisConfig:
        actions = {}
        for sense in senses:
            entry = data.get(sense)
            if entry:
                actions[sense] = (entry["action"], dict(entry.get("args", {})))
        return cls(label=data.get("label", ""), step=float(data.get("step", 0.0)),
                   actions=actions)


class PinchDrag:
    """Máquina de estados del arrastre con pinza."""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.engage_ratio = float(cfg.get("engage", 0.42))
        self.release_ratio = float(cfg.get("release", 0.62))
        self.axis_lock = float(cfg.get("axis_lock", 0.30))
        self.default_step = float(cfg.get("step", 0.55))
        self.tap_max_seconds = float(cfg.get("tap_max_seconds", 0.6))
        self.min_reach = float(cfg.get("min_index_reach", 0.80))

        self.modes: dict[str, dict[str, AxisConfig]] = {}
        self.taps: dict[str, tuple[str, dict[str, Any], str]] = {}
        for mode, entry in (cfg.get("modes") or {}).items():
            axes: dict[str, AxisConfig] = {}
            if "horizontal" in entry:
                axes[self.HORIZONTAL] = AxisConfig.from_dict(
                    entry["horizontal"], ("left", "right"))
            if "vertical" in entry:
                axes[self.VERTICAL] = AxisConfig.from_dict(
                    entry["vertical"], ("up", "down"))
            self.modes[mode] = axes
            tap = entry.get("tap")
            if tap:
                self.taps[mode] = (tap["action"], dict(tap.get("args", {})),
                                   tap.get("label", ""))

        self.engaged = False
        self.axis = ""
        self.label = ""
        self.steps = 0
        self.progress = 0.0
        self._origin = (0.0, 0.0)
        self._scale = 0.1
        self._engaged_at = 0.0
        self._mode = ""

    # ------------------------------------------------------------------ #

    def reset(self) -> None:
        self.engaged = False
        self.axis = ""
        self.label = ""
        self.steps = 0
        self.progress = 0.0

    @staticmethod
    def _pinch_point(lm) -> tuple[float, float]:
        """Punto medio entre las yemas del pulgar y el índice: lo que se «agarra»."""
        thumb = lm[lmk.THUMB_TIP, :2]
        index = lm[lmk.INDEX_TIP, :2]
        return (float(thumb[0] + index[0]) / 2.0, float(thumb[1] + index[1]) / 2.0)

    def update(self, hand: HandResult, mode: str, now: float) -> list[Event]:
        axes = self.modes.get(mode)
        if not hand.present or axes is None:
            return self._release(now, cancelled=True)

        lm = hand.landmarks
        ratio = lmk.pinch_ratio(lm)
        # Se mide cuánto sobresale el índice, no si está recto: al pinzar el dedo
        # se dobla para alcanzar el pulgar, y exigirlo recto impedía enganchar.
        # Lo que hay que descartar es el puño, donde las yemas también se tocan
        # pero el índice queda recogido contra la palma.
        index = lmk.index_reach(lm) >= self.min_reach

        if not self.engaged:
            if index and ratio < self.engage_ratio:
                self.engaged = True
                self._origin = self._pinch_point(lm)
                self._scale = max(lmk.hand_scale(lm), 1e-6)
                self._engaged_at = now
                self._mode = mode
                self.axis = ""
                self.label = ""
                self.steps = 0
                self.progress = 0.0
            return []

        if ratio > self.release_ratio or not index:
            return self._release(now)

        point = self._pinch_point(lm)
        dx = (point[0] - self._origin[0]) / self._scale
        dy = (point[1] - self._origin[1]) / self._scale

        if not self.axis:
            if max(abs(dx), abs(dy)) >= self.axis_lock:
                self.axis = self.HORIZONTAL if abs(dx) >= abs(dy) else self.VERTICAL
                config = axes.get(self.axis)
                self.label = config.label if config else ""
                # El recorrido consumido en decidir el eje no cuenta como avance.
                self._origin = point
                dx = dy = 0.0
            else:
                return []

        config = axes.get(self.axis)
        if config is None:
            return []
        step = config.step or self.default_step
        travelled = dx if self.axis == self.HORIZONTAL else dy
        self.progress = math.fmod(travelled / step, 1.0)

        target = int(travelled / step)
        events: list[Event] = []
        while self.steps != target:
            forward = target > self.steps
            self.steps += 1 if forward else -1
            if self.axis == self.HORIZONTAL:
                sense = "right" if forward else "left"
            else:
                sense = "down" if forward else "up"
            entry = config.actions.get(sense)
            if entry:
                action, args = entry
                events.append(Event(binding=Binding(
                    gesture="Pinch", trigger="pinch_drag", action=action,
                    args=dict(args), label=config.label, direction=sense)))
        return events

    def _release(self, now: float, cancelled: bool = False) -> list[Event]:
        """Suelta la pinza; emite el toque si nunca llegó a fijarse un eje."""
        if not self.engaged:
            return []
        was_tap = not self.axis and not cancelled
        held = now - self._engaged_at
        mode = self._mode
        self.reset()

        if was_tap and held <= self.tap_max_seconds and mode in self.taps:
            action, args, label = self.taps[mode]
            return [Event(binding=Binding(
                gesture="Pinch", trigger="pinch_tap", action=action,
                args=dict(args), label=label))]
        return []
