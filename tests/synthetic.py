"""Constructor de manos sintéticas para las pruebas.

Sin cámara, la única forma de ejercitar la geometría es fabricar los 21
landmarks a mano. Es importante que las manos sean *realistas*: la detección
mide la rectitud de cada dedo, así que un dedo recogido tiene que doblarse de
verdad y no limitarse a ser corto, o las pruebas validarían algo que no ocurre.
"""

from __future__ import annotations

import math

import numpy as np

from gesture_control import landmarks as lmk
from gesture_control.recognizer import HandResult

CHAINS = {
    "index": (lmk.INDEX_MCP, lmk.INDEX_PIP, lmk.INDEX_DIP, lmk.INDEX_TIP),
    "middle": (lmk.MIDDLE_MCP, lmk.MIDDLE_PIP, lmk.MIDDLE_DIP, lmk.MIDDLE_TIP),
    "ring": (lmk.RING_MCP, lmk.RING_PIP, lmk.RING_DIP, lmk.RING_TIP),
    "pinky": (lmk.PINKY_MCP, lmk.PINKY_PIP, lmk.PINKY_DIP, lmk.PINKY_TIP),
    "thumb": (lmk.THUMB_CMC, lmk.THUMB_MCP, lmk.THUMB_IP, lmk.THUMB_TIP),
}


def build_finger(lm, chain, origin, direction, length, curled: bool) -> None:
    """Coloca las cuatro falanges de un dedo, recto o enroscado."""
    ox, oy = origin
    norm = math.hypot(*direction) or 1.0
    dx, dy = direction[0] / norm, direction[1] / norm
    px, py = -dy, dx  # perpendicular: hacia donde se cierra el dedo
    lm[chain[0]] = (ox, oy, 0)
    if curled:
        pip = (ox + dx * length * 0.45, oy + dy * length * 0.45)
        dip = (pip[0] + px * length * 0.35, pip[1] + py * length * 0.35)
        tip = (dip[0] - dx * length * 0.35, dip[1] - dy * length * 0.35)
        points = (pip, dip, tip)
    else:
        points = tuple((ox + dx * length * f, oy + dy * length * f)
                       for f in (0.45, 0.75, 1.0))
    for joint, (x, y) in zip(chain[1:], points):
        lm[joint] = (x, y, 0)


def _palm(lm, cx: float, cy: float, scale: float) -> dict[str, tuple[float, float]]:
    """Muñeca y nudillos. ``hand_scale`` queda valiendo exactamente ``scale``."""
    lm[lmk.WRIST] = (cx, cy + scale / 2, 0)
    lm[lmk.MIDDLE_MCP] = (cx, cy - scale / 2, 0)
    return {
        "index": (cx - 0.2 * scale, cy - 0.3 * scale),
        "middle": (cx, cy - scale / 2),
        "ring": (cx + 0.2 * scale, cy - 0.3 * scale),
        "pinky": (cx + 0.35 * scale, cy - 0.15 * scale),
    }


def pointing(tip: tuple[float, float] | None = None, cx: float = 0.5, cy: float = 0.5,
             scale: float = 0.10, closed: bool = False,
             open_hand: bool = False) -> HandResult:
    """Mano señalando hacia ``tip``, cerrada en puño o completamente abierta."""
    lm = np.zeros((21, 3), np.float32)
    knuckles = _palm(lm, cx, cy, scale)

    for name in ("middle", "ring", "pinky"):
        build_finger(lm, CHAINS[name], knuckles[name], (0.0, -1.0), scale,
                     curled=not open_hand)

    origin = knuckles["index"]
    if closed or (tip is None and not open_hand):
        build_finger(lm, CHAINS["index"], origin, (0.0, -1.0), scale, curled=True)
    else:
        target = tip if tip is not None else (origin[0], origin[1] - scale)
        direction = (target[0] - origin[0], target[1] - origin[1])
        build_finger(lm, CHAINS["index"], origin,
                     direction, math.hypot(*direction) or scale, curled=False)

    build_finger(lm, CHAINS["thumb"], (cx - 0.4 * scale, cy + 0.2 * scale),
                 (-1.0, -0.4), scale, curled=not open_hand)
    return HandResult(gesture="Pointing_Up", score=0.9, landmarks=lm)


def pinch(point: tuple[float, float], gap: float, scale: float = 0.10) -> HandResult:
    """Mano con la pinza centrada en ``point`` y separación ``gap`` anchos de mano.

    ``gap`` es exactamente lo que devuelve ``pinch_ratio``, de modo que las
    pruebas pueden cruzar los umbrales de enganche y suelta con precisión.
    """
    px, py = point
    half = gap * scale / 2.0
    index_tip = (px - half, py)
    thumb_tip = (px + half, py)

    lm = np.zeros((21, 3), np.float32)
    # La palma queda a 1,35 anchos de mano de la pinza. Medido sobre las poses
    # canónicas, la yema del índice sobresale 1,2–1,4 anchos respecto al centro
    # de la palma al pinzar; acercarla más produciría una mano que ningún gesto
    # real reproduce y que el detector tomaría por un puño.
    cx, cy = px, py + 1.35 * scale
    knuckles = _palm(lm, cx, cy, scale)
    for name in ("middle", "ring", "pinky"):
        build_finger(lm, CHAINS[name], knuckles[name], (0.0, -1.0), scale, curled=True)

    origin = knuckles["index"]
    direction = (index_tip[0] - origin[0], index_tip[1] - origin[1])
    build_finger(lm, CHAINS["index"], origin, direction,
                 math.hypot(*direction) or scale, curled=False)

    thumb_origin = (cx - 0.45 * scale, cy + 0.25 * scale)
    direction = (thumb_tip[0] - thumb_origin[0], thumb_tip[1] - thumb_origin[1])
    build_finger(lm, CHAINS["thumb"], thumb_origin, direction,
                 math.hypot(*direction) or scale, curled=False)
    return HandResult(gesture="None", score=0.9, landmarks=lm)


def run(namespace: dict) -> int:
    """Ejecuta las funciones ``test_*`` de un módulo e informa del resultado."""
    tests = [(n, f) for n, f in sorted(namespace.items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok    {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FALLO {name}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} pruebas correctas")
    return 1 if failed else 0
