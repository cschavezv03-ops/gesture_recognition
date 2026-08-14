"""Pruebas del gatillo de pinza.

Es la pieza que sustituye a los barridos, así que lo que hay que demostrar es
justo lo contrario de lo que fallaba: que **nada** se dispara mientras no se
enganche, por mucho que se mueva la mano.

Ejecutar con:  python tests/test_pinchdrag.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic
from gesture_control.pinchdrag import PinchDrag
from gesture_control.recognizer import HandResult

CFG = {
    "engage": 0.42,
    "release": 0.62,
    "axis_lock": 0.30,
    "step": 0.55,
    "tap_max_seconds": 0.6,
    "modes": {
        "control": {
            "horizontal": {"label": "Pestañas",
                           "right": {"action": "hotkey", "args": {"keys": ["ctrl", "tab"]}},
                           "left": {"action": "hotkey", "args": {"keys": ["ctrl", "shift", "tab"]}}},
            "vertical": {"label": "Desplazar", "step": 0.20,
                         "up": {"action": "scroll", "args": {"amount": 120}},
                         "down": {"action": "scroll", "args": {"amount": -120}}},
        },
        "mouse": {
            "tap": {"action": "mouse_click", "args": {"button": "right"},
                    "label": "Clic derecho"},
        },
    },
}

OPEN, CLOSED = 0.90, 0.20   # separaciones por encima y por debajo de los umbrales


def drag(pinch: PinchDrag, path, mode="control", gap=CLOSED, start=0.0, step=1 / 30):
    """Arrastra la pinza por una serie de puntos y acumula los eventos."""
    events, t = [], start
    for point in path:
        events += pinch.update(synthetic.pinch(point, gap), mode, t)
        t += step
    return events


def line(origin, delta, samples=12):
    return [(origin[0] + delta[0] * i / samples, origin[1] + delta[1] * i / samples)
            for i in range(1, samples + 1)]


# --------------------------------------------------------------------------- #

def test_mover_la_mano_abierta_no_dispara_nada() -> None:
    """El fallo original: recorrer media pantalla sin enganchar no es un gesto."""
    pinch = PinchDrag(CFG)
    events = drag(pinch, line((0.3, 0.5), (0.4, 0.0), samples=20), gap=OPEN)
    assert not events, f"se dispararon {len(events)} acciones sin enganchar"
    assert not pinch.engaged


def test_juntar_los_dedos_engancha() -> None:
    pinch = PinchDrag(CFG)
    pinch.update(synthetic.pinch((0.5, 0.5), CLOSED), "control", 0.0)
    assert pinch.engaged


def test_el_puno_no_engancha() -> None:
    """En un puño las yemas también se tocan; sin el índice extendido no cuenta."""
    pinch = PinchDrag(CFG)
    pinch.update(synthetic.pointing(closed=True), "control", 0.0)
    assert not pinch.engaged


def test_arrastre_a_la_derecha_cambia_de_pestana() -> None:
    pinch = PinchDrag(CFG)
    events = drag(pinch, line((0.4, 0.5), (0.20, 0.0)))
    assert events, "el arrastre no emitió ningún paso"
    assert all(e.binding.direction == "right" for e in events)
    assert all(e.binding.args["keys"] == ["ctrl", "tab"] for e in events)


def test_arrastre_a_la_izquierda_va_al_otro_sentido() -> None:
    pinch = PinchDrag(CFG)
    events = drag(pinch, line((0.6, 0.5), (-0.20, 0.0)))
    assert events and all(e.binding.direction == "left" for e in events)


def test_el_eje_queda_fijado_y_no_cambia() -> None:
    """Una deriva vertical a mitad de un arrastre horizontal no debe desplazar."""
    pinch = PinchDrag(CFG)
    path = line((0.4, 0.5), (0.12, 0.0)) + line((0.52, 0.5), (0.04, 0.20))
    events = drag(pinch, path)
    assert pinch.axis == "horizontal"
    assert all(e.binding.action == "hotkey" for e in events), \
        "la deriva vertical se coló como desplazamiento"


def test_arrastre_vertical_desplaza() -> None:
    pinch = PinchDrag(CFG)
    events = drag(pinch, line((0.5, 0.3), (0.0, 0.20)))
    assert events, "el arrastre vertical no emitió nada"
    assert all(e.binding.action == "scroll" for e in events)
    assert all(e.binding.direction == "down" for e in events)


def test_los_pasos_son_proporcionales_al_recorrido() -> None:
    """Recorrer el doble debe saltar el doble de pestañas."""
    corto = drag(PinchDrag(CFG), line((0.4, 0.5), (0.11, 0.0)))
    largo = drag(PinchDrag(CFG), line((0.4, 0.5), (0.22, 0.0)))
    assert len(largo) > len(corto), f"{len(corto)} y {len(largo)} pasos"


def test_volver_atras_deshace_los_pasos() -> None:
    """Sin esto, dudar a mitad de arrastre dejaría pestañas de más."""
    pinch = PinchDrag(CFG)
    ida = drag(pinch, line((0.4, 0.5), (0.20, 0.0)))
    vuelta = drag(pinch, line((0.6, 0.5), (-0.20, 0.0)), start=1.0)
    assert ida and vuelta
    assert all(e.binding.direction == "left" for e in vuelta)
    assert len(vuelta) >= len(ida) - 1


def test_soltar_sin_moverse_es_un_toque() -> None:
    pinch = PinchDrag(CFG)
    pinch.update(synthetic.pinch((0.5, 0.5), CLOSED), "mouse", 0.0)
    events = pinch.update(synthetic.pinch((0.5, 0.5), OPEN), "mouse", 0.2)
    assert len(events) == 1, "el toque no se emitió"
    assert events[0].binding.action == "mouse_click"


def test_un_arrastre_no_cuenta_como_toque() -> None:
    """Si se movió, al soltar no debe además hacer clic."""
    pinch = PinchDrag(CFG)
    drag(pinch, line((0.5, 0.4), (0.0, 0.20)), mode="mouse")
    events = pinch.update(synthetic.pinch((0.5, 0.6), OPEN), "mouse", 1.0)
    assert not events, "soltar tras arrastrar emitió un clic"


def test_toque_demasiado_largo_no_cuenta() -> None:
    pinch = PinchDrag(CFG)
    pinch.update(synthetic.pinch((0.5, 0.5), CLOSED), "mouse", 0.0)
    events = pinch.update(synthetic.pinch((0.5, 0.5), OPEN), "mouse", 2.0)
    assert not events, "una pinza mantenida dos segundos no es un toque"


def test_histeresis_al_soltar() -> None:
    """Entre los umbrales de enganche y suelta la pinza sigue enganchada."""
    pinch = PinchDrag(CFG)
    pinch.update(synthetic.pinch((0.5, 0.5), CLOSED), "control", 0.0)
    pinch.update(synthetic.pinch((0.5, 0.5), 0.50), "control", 0.1)
    assert pinch.engaged, "se soltó dentro de la banda de histéresis"
    pinch.update(synthetic.pinch((0.5, 0.5), 0.70), "control", 0.2)
    assert not pinch.engaged


def test_perder_la_mano_suelta_sin_disparar() -> None:
    pinch = PinchDrag(CFG)
    pinch.update(synthetic.pinch((0.5, 0.5), CLOSED), "mouse", 0.0)
    events = pinch.update(HandResult(), "mouse", 0.1)
    assert not events, "perder la mano emitió una acción"
    assert not pinch.engaged


def test_un_modo_sin_ejes_no_hace_nada() -> None:
    pinch = PinchDrag(CFG)
    assert not drag(pinch, line((0.4, 0.5), (0.20, 0.0)), mode="dibujo")


if __name__ == "__main__":
    raise SystemExit(synthetic.run(globals()))
