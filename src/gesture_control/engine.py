"""Motor de gestos: convierte una secuencia de detecciones en acciones discretas.

El clasificador entrega una etiqueta por fotograma, y esa señal es ruidosa: en
las transiciones entre poses aparecen etiquetas espurias durante uno o dos
fotogramas. Traducir cada etiqueta directamente a una acción produciría
disparos fantasma constantes. Este módulo interpone una máquina de estados con
cuatro mecanismos:

* **Estabilidad** — una pose debe repetirse varios fotogramas para considerarse.
* **Enfriamiento** — tras disparar, la acción queda bloqueada un tiempo mínimo.
* **Puerta de movimiento** — los disparos por permanencia se inhiben mientras la
  mano se mueve rápido, para que un barrido no active además el gesto estático.
* **Normalización por tamaño de mano** — las distancias se miden en anchos de
  mano, de modo que un barrido funciona igual de cerca que de lejos.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

from . import landmarks as lmk
from .recognizer import HandResult

#: Etiquetas que produce el clasificador preentrenado de MediaPipe.
CANNED_GESTURES = (
    "None", "Closed_Fist", "Open_Palm", "Pointing_Up",
    "Thumb_Down", "Thumb_Up", "Victory", "ILoveYou",
)


@dataclass
class Binding:
    """Asociación entre un gesto y una acción, tal y como aparece en ``config.yaml``."""

    gesture: str
    trigger: str  # tap | hold | repeat | swipe
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    direction: str = ""        # solo para trigger 'swipe'
    duration: float = 1.0      # solo para trigger 'hold'
    interval: float = 0.15     # solo para trigger 'repeat'
    cooldown: float = 0.6

    @property
    def key(self) -> str:
        return f"{self.gesture}:{self.trigger}:{self.direction}:{self.action}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Binding:
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Claves desconocidas en el binding {data}: {sorted(unknown)}")
        return cls(**data)


@dataclass
class Event:
    """Una acción a ejecutar, ya resuelta a partir de un gesto."""

    binding: Binding
    direction: str = ""
    value: float | None = None


@dataclass
class EngineState:
    """Instantánea del motor para dibujar el HUD."""

    gesture: str = "None"
    score: float = 0.0
    stable: bool = False
    speed: float = 0.0
    hold_label: str = ""
    hold_progress: float = 0.0
    slider_active: bool = False
    slider_value: float = 0.0
    slider_label: str = ""


class GestureEngine:
    """Traduce resultados por fotograma en eventos de acción."""

    def __init__(self, cfg: dict[str, Any], bindings: dict[str, list[Binding]],
                 analog: dict[str, dict[str, Any]]) -> None:
        self.bindings = bindings
        self.analog = analog

        self.min_score = float(cfg.get("min_gesture_confidence", 0.6))
        self.stability_frames = int(cfg.get("stability_frames", 3))
        self.motion_gate = float(cfg.get("motion_gate", 2.5))
        self.default_cooldown = float(cfg.get("default_cooldown", 0.6))

        swipe = cfg.get("swipe", {})
        self.swipe_min_distance = float(swipe.get("min_distance", 1.2))
        self.swipe_min_speed = float(swipe.get("min_speed", 3.0))
        self.swipe_window = float(swipe.get("window", 0.45))
        self.swipe_axis_ratio = float(swipe.get("axis_ratio", 1.5))
        self.swipe_cooldown = float(swipe.get("cooldown", 0.7))

        self._candidate = "None"
        self._candidate_count = 0
        self._stable_gesture = "None"
        self._stable_since = 0.0
        self._score = 0.0

        self._history: deque[tuple[float, float, float, float, str]] = deque(maxlen=90)
        self._cooldowns: dict[str, float] = {}
        self._fired: set[str] = set()
        self._last_repeat: dict[str, float] = {}

        self._slider_since: float | None = None
        self._swipe_ready_at = 0.0

        self.state = EngineState()

    def reset(self) -> None:
        """Olvida el estado acumulado. Se llama al cambiar de modo o al pausar."""
        self._candidate = "None"
        self._candidate_count = 0
        self._stable_gesture = "None"
        self._history.clear()
        self._fired.clear()
        self._last_repeat.clear()
        self._slider_since = None
        self.state = EngineState()

    # ------------------------------------------------------------------ #
    # Medición de movimiento
    # ------------------------------------------------------------------ #

    def _track(self, hand: HandResult, now: float, gesture: str) -> None:
        if not hand.present:
            self._history.clear()
            return
        cx, cy = lmk.palm_center(hand.landmarks)
        # Se guarda también qué pose había en cada instante: un barrido se
        # identifica por la pose con la que se inició, no por la que queda al
        # terminarlo, que suele haberse deformado al girar la muñeca.
        self._history.append(
            (now, float(cx), float(cy), lmk.hand_scale(hand.landmarks), gesture)
        )

    def _speed(self, now: float) -> float:
        """Velocidad de la palma en anchos de mano por segundo.

        Se acumula la longitud del recorrido y no el desplazamiento neto: una
        mano que oscila vuelve al punto de partida, y medir extremo contra
        extremo daría velocidad cero justo cuando más agitada está. Los barridos
        sí necesitan desplazamiento neto, y eso se calcula aparte.
        """
        recent = [p for p in self._history if now - p[0] <= 0.15]
        if len(recent) < 2:
            return 0.0
        path = sum(
            ((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5
            for a, b in zip(recent, recent[1:])
        )
        dt = max(recent[-1][0] - recent[0][0], 1e-3)
        return path / max(recent[0][3], 1e-6) / dt

    def _detect_swipe(self, now: float) -> tuple[str, str] | None:
        """Detecta un barrido y devuelve ``(dirección, pose que lo originó)``.

        La pose se decide por mayoría dentro de la ventana en lugar de leerse del
        último fotograma. Al barrer, la muñeca gira y el clasificador pierde la
        pose justo al final del recorrido; mirando solo ese instante, los
        barridos horizontales se descartaban casi siempre.
        """
        if now < self._swipe_ready_at:
            return None
        window = [p for p in self._history if now - p[0] <= self.swipe_window]
        if len(window) < 4:
            return None
        t0, x0, y0, scale, _ = window[0]
        _, x1, y1, _, _ = window[-1]
        dt = max(now - t0, 1e-3)
        dx = (x1 - x0) / max(scale, 1e-6)
        dy = (y1 - y0) / max(scale, 1e-6)

        direction = None
        for delta, other, positive, negative in (
            (dx, dy, "right", "left"),
            (dy, dx, "down", "up"),  # 'y' crece hacia abajo en coordenadas de imagen
        ):
            if (abs(delta) >= self.swipe_min_distance
                    and abs(delta) >= abs(other) * self.swipe_axis_ratio
                    and abs(delta) / dt >= self.swipe_min_speed):
                direction = positive if delta > 0 else negative
                break
        if direction is None:
            return None

        seen = [p[4] for p in window if p[4] != "None"]
        if not seen:
            return None
        gesture, count = Counter(seen).most_common(1)[0]
        # Una pose testimonial no basta: debe dominar el recorrido para que el
        # barrido cuente como intencionado.
        if count < len(window) * 0.4:
            return None
        return direction, gesture

    # ------------------------------------------------------------------ #
    # Bucle principal
    # ------------------------------------------------------------------ #

    def update(self, hand: HandResult, mode: str, paused: bool,
               now: float | None = None) -> list[Event]:
        """Procesa un fotograma y devuelve los eventos disparados en él.

        En pausa solo se evalúan los bindings del grupo ``global``, que es lo que
        permite reanudar sin tocar el teclado.
        """
        now = time.monotonic() if now is None else now

        # --- Estabilización de la etiqueta ---------------------------------
        label = hand.gesture if (hand.present and hand.score >= self.min_score) else "None"
        if label == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = label
            self._candidate_count = 1

        if self._candidate_count >= self.stability_frames and self._stable_gesture != label:
            self._stable_gesture = label
            self._stable_since = now
            self._fired.clear()
            self._last_repeat.clear()

        self._score = hand.score
        gesture = self._stable_gesture
        self._track(hand, now, gesture)
        speed = self._speed(now)
        held_for = now - self._stable_since

        state = EngineState(gesture=gesture, score=hand.score, speed=speed,
                            stable=self._candidate_count >= self.stability_frames)

        events: list[Event] = []
        groups = ["global"] if paused else ["global", mode]
        active = [b for g in groups for b in self.bindings.get(g, [])]

        # --- Control analógico ---------------------------------------------
        analog_cfg = None if paused else self.analog.get(mode)
        slider_pose = bool(analog_cfg) and hand.present and lmk.is_slider_pose(hand.landmarks)
        if slider_pose:
            if self._slider_since is None:
                self._slider_since = now
            engaged = (now - self._slider_since) >= float(analog_cfg.get("engage_time", 0.35))
            if engaged:
                lo, hi = analog_cfg.get("range", [0.25, 1.45])
                raw = lmk.pinch_ratio(hand.landmarks)
                value = (raw - lo) / max(hi - lo, 1e-6)
                value = min(max(value, 0.0), 1.0)
                state.slider_active = True
                state.slider_value = value
                state.slider_label = analog_cfg.get("label", "Analógico")
                events.append(Event(
                    binding=Binding(gesture="Slider", trigger="analog",
                                    action=analog_cfg["action"],
                                    label=state.slider_label),
                    value=value,
                ))
                # Mientras el deslizador manda, ningún otro gesto compite por la mano.
                self.state = state
                return events
        else:
            self._slider_since = None

        # --- Barridos --------------------------------------------------------
        swipe = self._detect_swipe(now)
        if swipe:
            direction, swipe_gesture = swipe
            for b in active:
                if (b.trigger == "swipe" and b.gesture == swipe_gesture
                        and b.direction == direction):
                    if now >= self._cooldowns.get(b.key, 0.0):
                        self._cooldowns[b.key] = now + (b.cooldown or self.swipe_cooldown)
                        self._swipe_ready_at = now + self.swipe_cooldown
                        self._history.clear()
                        events.append(Event(binding=b, direction=direction))
                    break

        # --- Disparos estáticos ---------------------------------------------
        # Se inhiben con la mano en movimiento para no solaparse con los barridos,
        # y mientras se está formando la pose del deslizador: durante esos primeros
        # fotogramas el clasificador la etiqueta de forma inestable.
        if gesture != "None" and speed < self.motion_gate and not events and not slider_pose:
            for b in active:
                if b.gesture != gesture:
                    continue
                if now < self._cooldowns.get(b.key, 0.0):
                    continue

                if b.trigger == "tap" and b.key not in self._fired:
                    self._fired.add(b.key)
                    self._cooldowns[b.key] = now + (b.cooldown or self.default_cooldown)
                    events.append(Event(binding=b))

                elif b.trigger == "hold":
                    if b.key in self._fired:
                        continue
                    progress = min(held_for / max(b.duration, 1e-3), 1.0)
                    if progress >= 1.0:
                        self._fired.add(b.key)
                        self._cooldowns[b.key] = now + (b.cooldown or self.default_cooldown)
                        events.append(Event(binding=b))
                    elif progress > state.hold_progress:
                        state.hold_progress = progress
                        state.hold_label = b.label or b.action

                elif b.trigger == "repeat":
                    last = self._last_repeat.get(b.key, 0.0)
                    if now - last >= b.interval:
                        self._last_repeat[b.key] = now
                        events.append(Event(binding=b))

        self.state = state
        return events
