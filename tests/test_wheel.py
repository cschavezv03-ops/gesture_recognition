"""Pruebas de la rueda de comandos con manos sintéticas.

La rueda decide qué acción se ejecuta a partir de un ángulo, y un error de
signo o de origen ahí se traduce en ejecutar la opción de enfrente. Es
exactamente el tipo de fallo que no se detecta leyendo el código.

Ejecutar con:  python tests/test_wheel.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from gesture_control import landmarks as lmk
from gesture_control.recognizer import HandResult
from gesture_control.wheel import CommandWheel, WheelItem

ITEMS = [WheelItem(label=name, action="hotkey", args={"keys": ["a"]})
         for name in ("ARRIBA", "DERECHA", "ABAJO", "IZQUIERDA")]

CFG = {"dwell": 0.5, "inner_radius": 1.15, "outer_radius": 2.7, "lost_timeout": 0.6}


def build_finger(lm, chain, origin, direction, length, curled: bool) -> None:
    """Coloca las cuatro falanges de un dedo, recto o enroscado.

    El dedo recogido tiene que doblarse de verdad —no acortarse—, porque la
    detección mide la rectitud del recorrido y un dedo corto pero recto se
    contaría como extendido.
    """
    ox, oy = origin
    norm = math.hypot(*direction) or 1.0
    dx, dy = direction[0] / norm, direction[1] / norm
    px, py = -dy, dx  # perpendicular, hacia donde se cierra el dedo
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


def hand(tip: tuple[float, float] | None = None, cx: float = 0.5, cy: float = 0.5,
         scale: float = 0.10, closed: bool = False) -> HandResult:
    """Mano apuntando con el índice hacia ``tip`` (o cerrada en puño)."""
    lm = np.zeros((21, 3), np.float32)
    lm[lmk.WRIST] = (cx, cy + scale / 2, 0)
    lm[lmk.MIDDLE_MCP] = (cx, cy - scale / 2, 0)

    knuckles = {
        lmk.INDEX_MCP: (cx - 0.02, cy),
        lmk.MIDDLE_MCP: (cx, cy - scale / 2),
        lmk.RING_MCP: (cx + 0.02, cy),
        lmk.PINKY_MCP: (cx + 0.03, cy),
    }
    chains = {
        lmk.INDEX_MCP: (lmk.INDEX_MCP, lmk.INDEX_PIP, lmk.INDEX_DIP, lmk.INDEX_TIP),
        lmk.MIDDLE_MCP: (lmk.MIDDLE_MCP, lmk.MIDDLE_PIP, lmk.MIDDLE_DIP, lmk.MIDDLE_TIP),
        lmk.RING_MCP: (lmk.RING_MCP, lmk.RING_PIP, lmk.RING_DIP, lmk.RING_TIP),
        lmk.PINKY_MCP: (lmk.PINKY_MCP, lmk.PINKY_PIP, lmk.PINKY_DIP, lmk.PINKY_TIP),
    }
    # Corazón, anular y meñique siempre recogidos: la pose es de apuntar.
    for mcp in (lmk.MIDDLE_MCP, lmk.RING_MCP, lmk.PINKY_MCP):
        build_finger(lm, chains[mcp], knuckles[mcp], (0.0, -1.0), scale, curled=True)

    origin = knuckles[lmk.INDEX_MCP]
    if closed or tip is None:
        build_finger(lm, chains[lmk.INDEX_MCP], origin, (0.0, -1.0), scale, curled=True)
    else:
        direction = (tip[0] - origin[0], tip[1] - origin[1])
        length = math.hypot(*direction) or scale
        build_finger(lm, chains[lmk.INDEX_MCP], origin, direction, length, curled=False)
    return HandResult(gesture="Pointing_Up", score=0.9, landmarks=lm)


def wheel_at(items=ITEMS, aspect: float = 16 / 9) -> CommandWheel:
    w = CommandWheel(CFG, {"control": list(items)})
    assert w.open(hand(tip=(0.5, 0.3)), "control", aspect)
    return w


def point_at(w: CommandWheel, degrees: float, radius_units: float = 2.0):
    """Mano apuntando a un ángulo dado (0 = arriba, sentido horario)."""
    rad = math.radians(degrees - 90.0)
    dx = math.cos(rad) * radius_units * w.scale / w.aspect
    dy = math.sin(rad) * radius_units * w.scale
    return hand(tip=(w.center[0] + dx, w.center[1] + dy))


def hold(w: CommandWheel, degrees: float, seconds: float, start: float = 0.0,
         step: float = 1 / 30):
    """Mantiene el apuntado y devuelve el evento confirmado, si lo hay."""
    t = start
    while t < start + seconds:
        event = w.update(point_at(w, degrees), t)
        if event:
            return event
        t += step
    return None


# --------------------------------------------------------------------------- #

def test_no_abre_sin_mano() -> None:
    w = CommandWheel(CFG, {"control": list(ITEMS)})
    assert not w.open(HandResult(), "control")
    assert not w.active


def test_no_abre_en_un_modo_sin_opciones() -> None:
    w = CommandWheel(CFG, {"control": list(ITEMS)})
    assert not w.open(hand(tip=(0.5, 0.3)), "mouse")


def test_cada_sector_corresponde_a_su_direccion() -> None:
    """El sector 0 está arriba y avanzan en sentido horario."""
    w = wheel_at()
    for degrees, expected in ((0, "ARRIBA"), (90, "DERECHA"),
                              (180, "ABAJO"), (270, "IZQUIERDA")):
        w.update(point_at(w, degrees), 0.0)
        assert w.items[w.selected].label == expected, (
            f"apuntando a {degrees}° se eligió {w.items[w.selected].label}")


def test_confirma_por_permanencia() -> None:
    w = wheel_at()
    event = hold(w, 90, seconds=0.9)
    assert event is not None, "no confirmó tras mantener el apuntado"
    assert event.binding.label == "DERECHA"
    assert not w.active, "la rueda debe cerrarse al confirmar"


def test_no_confirma_antes_de_tiempo() -> None:
    w = wheel_at()
    assert hold(w, 90, seconds=0.3) is None, "confirmó antes del tiempo de permanencia"
    assert w.active


def test_cambiar_de_sector_reinicia_la_permanencia() -> None:
    w = wheel_at()
    assert hold(w, 90, seconds=0.4, start=0.0) is None
    # Cambiar de opción justo antes de confirmar no debe arrastrar el progreso.
    assert hold(w, 180, seconds=0.4, start=0.4) is None
    assert w.active


def test_apuntar_hacia_abajo_no_cancela_la_rueda() -> None:
    """Regresión: la mitad inferior de la rueda era inalcanzable.

    La detección de dedo extendido comparaba la distancia de la punta a la
    muñeca; al apuntar hacia abajo la punta se acerca a la muñeca y el índice se
    leía como recogido, lo que la rueda interpretaba como «cancelar».
    """
    w = wheel_at()
    for degrees in (135, 180, 225):
        w.update(point_at(w, degrees), 0.0)
        assert w.active, f"apuntar a {degrees}° cerró la rueda"
        assert w.selected is not None, f"apuntar a {degrees}° no seleccionó nada"


def test_zona_muerta_central_no_selecciona() -> None:
    w = wheel_at()
    w.update(point_at(w, 90, radius_units=0.5), 0.0)
    assert w.selected is None
    assert w.dwell_progress == 0.0


def test_puno_cancela_sin_ejecutar() -> None:
    w = wheel_at()
    w.update(point_at(w, 90), 0.0)
    assert w.update(hand(closed=True), 0.1) is None
    assert not w.active, "cerrar la mano debe cancelar la rueda"


def test_se_cierra_si_se_pierde_la_mano() -> None:
    w = wheel_at()
    assert w.update(HandResult(), 0.1) is None
    assert w.active, "una pérdida breve no debe cerrar la rueda"
    w.update(HandResult(), 0.9)
    assert not w.active, "una pérdida prolongada debe cerrarla"


def test_corrige_la_relacion_de_aspecto() -> None:
    """Sin corregir el aspecto, apuntar a la derecha caía en un sector contiguo.

    Las coordenadas de MediaPipe se normalizan por eje, así que en 16:9 una
    unidad horizontal vale casi el doble que una vertical.
    """
    w = wheel_at(aspect=16 / 9)
    # Desplazamiento puramente horizontal en píxeles reales.
    dx_normalizado = 2.0 * w.scale / w.aspect
    w.update(hand(tip=(w.center[0] + dx_normalizado, w.center[1])), 0.0)
    assert w.items[w.selected].label == "DERECHA"


def test_ocho_opciones_se_reparten_el_circulo() -> None:
    items = [WheelItem(label=f"OP{i}", action="hotkey", args={"keys": ["a"]})
             for i in range(8)]
    w = wheel_at(items)
    for i in range(8):
        w.update(point_at(w, i * 45.0), 0.0)
        assert w.selected == i, f"a {i * 45}° se eligió el sector {w.selected}"


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
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


if __name__ == "__main__":
    raise SystemExit(main())
