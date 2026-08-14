"""Catálogo de los siete gestos del modelo: nombre, cómo se hacen y cómo se dibujan.

Las etiquetas que devuelve MediaPipe (``ILoveYou``, ``Victory``) no le dicen nada
a quien usa el programa: no describen una postura, describen un significado
cultural. Aquí cada gesto recibe un nombre que nombra la *mano* y una frase que
explica qué dedos van extendidos.

Además se genera una pose canónica de 21 landmarks por gesto, lo que permite
dibujar un diagrama de cada uno reutilizando el mismo código que dibuja la mano
real. Un esquema resuelve en un vistazo lo que un párrafo no consigue.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import landmarks as lmk

# Anatomía esquemática en un cuadro normalizado 0-1, con la palma de frente y
# los dedos hacia arriba. Las proporciones importan: con los dedos cortos
# respecto a la palma el esquema deja de parecer una mano.
_WRIST = (0.50, 0.88)
_MCP = {
    lmk.INDEX_MCP: (0.36, 0.53),
    lmk.MIDDLE_MCP: (0.49, 0.50),
    lmk.RING_MCP: (0.61, 0.52),
    lmk.PINKY_MCP: (0.72, 0.57),
}
_FINGER_JOINTS = {
    lmk.INDEX_MCP: (lmk.INDEX_PIP, lmk.INDEX_DIP, lmk.INDEX_TIP),
    lmk.MIDDLE_MCP: (lmk.MIDDLE_PIP, lmk.MIDDLE_DIP, lmk.MIDDLE_TIP),
    lmk.RING_MCP: (lmk.RING_PIP, lmk.RING_DIP, lmk.RING_TIP),
    lmk.PINKY_MCP: (lmk.PINKY_PIP, lmk.PINKY_DIP, lmk.PINKY_TIP),
}

_THUMB_OUT = ((0.34, 0.79), (0.24, 0.71), (0.15, 0.64), (0.07, 0.58))
_THUMB_IN = ((0.34, 0.79), (0.31, 0.70), (0.34, 0.62), (0.41, 0.57))
_THUMB_UP = ((0.34, 0.79), (0.29, 0.66), (0.26, 0.51), (0.24, 0.34))


def _build(index: bool, middle: bool, ring: bool, pinky: bool,
           thumb: str = "in", spread: float = 0.0) -> np.ndarray:
    """Compone una mano esquemática a partir del estado de cada dedo.

    ``spread`` separa índice y corazón, que es lo único que distingue
    visualmente la uve de dos dedos simplemente extendidos.
    """
    lm = np.zeros((21, 3), np.float32)
    lm[lmk.WRIST] = (*_WRIST, 0)

    thumb_points = {"out": _THUMB_OUT, "in": _THUMB_IN, "up": _THUMB_UP}[thumb]
    for joint, point in zip((lmk.THUMB_CMC, lmk.THUMB_MCP, lmk.THUMB_IP, lmk.THUMB_TIP),
                            thumb_points):
        lm[joint] = (*point, 0)

    states = {lmk.INDEX_MCP: index, lmk.MIDDLE_MCP: middle,
              lmk.RING_MCP: ring, lmk.PINKY_MCP: pinky}
    for mcp, (x, y) in _MCP.items():
        lm[mcp] = (x, y, 0)
        pip, dip, tip = _FINGER_JOINTS[mcp]
        if states[mcp]:
            dx = 0.0
            if spread:
                if mcp == lmk.INDEX_MCP:
                    dx = -spread
                elif mcp == lmk.MIDDLE_MCP:
                    dx = spread
            lm[pip] = (x + dx * 0.4, y - 0.16, 0)
            lm[dip] = (x + dx * 0.75, y - 0.27, 0)
            lm[tip] = (x + dx, y - 0.36, 0)
        else:
            # Dedo recogido: la punta se enrosca hasta quedar sobre el nudillo.
            # Si se la dibuja por debajo parece un dedo extendido hacia abajo,
            # que es justo lo contrario de lo que hay que comunicar.
            lm[pip] = (x, y - 0.11, 0)
            lm[dip] = (x + 0.035, y - 0.07, 0)
            lm[tip] = (x + 0.05, y - 0.025, 0)
    return lm


def _flip_vertical(lm: np.ndarray) -> np.ndarray:
    """Voltea la mano. Un pulgar abajo es un pulgar arriba del revés."""
    out = lm.copy()
    out[:, 1] = 1.0 - out[:, 1]
    return out


@dataclass(frozen=True)
class GesturePose:
    """Un gesto del modelo con su presentación para el usuario."""

    key: str            # etiqueta que devuelve MediaPipe
    name: str           # nombre corto, el que se ve en pantalla
    how: str            # cómo se hace, en términos de dedos
    pose: np.ndarray    # 21 landmarks para dibujar el esquema


_THUMB_UP_POSE = _build(False, False, False, False, thumb="up")

GESTURES: dict[str, GesturePose] = {
    g.key: g for g in (
        GesturePose("Closed_Fist", "PUÑO",
                    "Mano cerrada, con el pulgar por delante de los dedos",
                    _build(False, False, False, False, thumb="in")),
        GesturePose("Open_Palm", "PALMA",
                    "Los cinco dedos extendidos y separados, palma al frente",
                    _build(True, True, True, True, thumb="out")),
        GesturePose("Pointing_Up", "ÍNDICE",
                    "Solo el índice extendido hacia arriba; pulgar recogido",
                    _build(True, False, False, False, thumb="in")),
        GesturePose("Thumb_Up", "PULGAR ARRIBA",
                    "Puño cerrado con el pulgar apuntando hacia arriba",
                    _THUMB_UP_POSE),
        GesturePose("Thumb_Down", "PULGAR ABAJO",
                    "Puño cerrado con el pulgar apuntando hacia abajo",
                    _flip_vertical(_THUMB_UP_POSE)),
        GesturePose("Victory", "UVE",
                    "Índice y corazón extendidos en uve; los demás recogidos",
                    _build(True, True, False, False, thumb="in", spread=0.07)),
        GesturePose("ILoveYou", "CUERNOS",
                    "Pulgar, índice y meñique extendidos; corazón y anular recogidos",
                    _build(True, False, False, True, thumb="out")),
    )
}

#: Pose del deslizador analógico. No la clasifica el modelo: se mide por geometría.
SLIDER = GesturePose(
    "Slider", "PINZA",
    "Pulgar e índice extendidos en pinza; la separación entre ellos fija el valor",
    _build(True, False, False, False, thumb="out"),
)

#: Nombre en pantalla de cada modo; las claves internas se quedan en inglés
#: porque son las que aparecen en config.yaml.
MODE_NAMES = {"control": "CONTROL", "mouse": "RATÓN"}

ARROWS = {"left": "◄", "right": "►", "up": "▲", "down": "▼"}
DIRECTION_NAMES = {"left": "izquierda", "right": "derecha",
                   "up": "arriba", "down": "abajo"}


def mode_name(mode: str) -> str:
    return MODE_NAMES.get(mode, mode.upper())


def name_of(gesture_key: str) -> str:
    """Nombre legible de un gesto; si no se conoce, se devuelve la clave."""
    pose = GESTURES.get(gesture_key)
    return pose.name if pose else gesture_key.replace("_", " ").upper()


def describe(gesture_key: str, trigger: str = "", direction: str = "",
             duration: float = 0.0) -> str:
    """Etiqueta compacta del gesto tal y como debe ejecutarse.

    Por ejemplo ``PUÑO ► barrer`` o ``PALMA · 1,5 s``.
    """
    label = name_of(gesture_key)
    if trigger == "swipe" and direction:
        return f"{label} {ARROWS.get(direction, '')} barrer"
    if trigger == "hold":
        return f"{label} · {duration:g} s".replace(".", ",")
    if trigger == "repeat":
        return f"{label} · mantener"
    return label
