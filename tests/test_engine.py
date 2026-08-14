"""Pruebas del motor de gestos con manos sintéticas.

El motor es la pieza donde se concentran los errores sutiles — un enfriamiento
mal aplicado o una puerta de movimiento demasiado laxa se traducen en acciones
que se disparan solas — y es la única que se puede ejercitar sin cámara.

Ejecutar con:  python tests/test_engine.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from gesture_control import landmarks as lmk
from gesture_control import poses
from gesture_control.engine import Binding, GestureEngine
from gesture_control.recognizer import HandResult

BASE_CFG = {
    "stability_frames": 3,
    "min_gesture_confidence": 0.6,
    "motion_gate": 2.5,
    "default_cooldown": 0.6,
    "swipe": {"min_distance": 1.2, "min_speed": 3.0, "window": 0.45,
              "axis_ratio": 1.5, "cooldown": 0.7},
}


def hand(gesture: str, cx: float = 0.5, cy: float = 0.5, score: float = 0.9,
         slider: bool = False) -> HandResult:
    """Construye una mano sintética con una escala fija de 0.10 unidades."""
    lm = np.zeros((21, 3), np.float32)
    lm[lmk.WRIST] = (cx, cy + 0.05, 0)
    lm[lmk.MIDDLE_MCP] = (cx, cy - 0.05, 0)
    lm[lmk.INDEX_MCP] = (cx - 0.03, cy, 0)
    lm[lmk.RING_MCP] = (cx + 0.03, cy, 0)
    lm[lmk.PINKY_MCP] = (cx + 0.05, cy + 0.02, 0)
    if slider:
        # Se reutiliza la pose canónica del deslizador en lugar de inventar
        # landmarks: así la prueba ejercita la misma geometría que el programa
        # dibuja en la guía, con todas las falanges en su sitio.
        pose = poses.SLIDER.pose.copy()
        pose[:, :2] -= pose[lmk.WRIST, :2]
        pose[:, :2] *= 0.10 / lmk.hand_scale(poses.SLIDER.pose)
        pose[:, :2] += (cx, cy)
        return HandResult(gesture=gesture, score=score,
                          landmarks=pose.astype(np.float32))
    return HandResult(gesture=gesture, score=score, landmarks=lm)


def engine(bindings: dict[str, list[Binding]], analog=None) -> GestureEngine:
    return GestureEngine(BASE_CFG, bindings, analog or {})


def feed(eng: GestureEngine, h: HandResult, t: float, mode="control", paused=False):
    return eng.update(h, mode, paused, now=t)


# --------------------------------------------------------------------------- #

def test_tap_dispara_una_vez_y_respeta_el_enfriamiento() -> None:
    eng = engine({"control": [Binding("Victory", "tap", "media", cooldown=0.5)]})
    fired = 0
    for i in range(20):  # 20 fotogramas a 30 FPS = 0.66 s
        fired += len(feed(eng, hand("Victory"), i / 30))
    # Un disparo al estabilizarse; el segundo solo tras vencer el enfriamiento,
    # y para eso hace falta que el gesto se reinicie, cosa que aquí no ocurre.
    assert fired == 1, f"tap disparó {fired} veces, se esperaba 1"


def test_tap_necesita_estabilidad() -> None:
    """Dos fotogramas sueltos no bastan: así se filtran las etiquetas espurias."""
    eng = engine({"control": [Binding("Victory", "tap", "media")]})
    fired = 0
    for i in range(2):
        fired += len(feed(eng, hand("Victory"), i / 30))
    assert fired == 0, "un gesto de 2 fotogramas no debería disparar"


def test_confianza_baja_se_ignora() -> None:
    eng = engine({"control": [Binding("Victory", "tap", "media")]})
    fired = sum(len(feed(eng, hand("Victory", score=0.3), i / 30)) for i in range(10))
    assert fired == 0, "una detección por debajo del umbral no debería disparar"


def test_hold_dispara_al_cumplirse_la_duracion() -> None:
    eng = engine({"control": [Binding("Open_Palm", "hold", "toggle_pause", duration=1.0)]})
    events, times = [], []
    for i in range(45):  # 1.5 s
        t = i / 30
        got = feed(eng, hand("Open_Palm"), t)
        events += got
        times += [t] * len(got)
    assert len(events) == 1, f"hold disparó {len(events)} veces"
    # El gesto se estabiliza en el tercer fotograma, así que la cuenta arranca ahí.
    assert 1.0 <= times[0] <= 1.15, f"hold disparó en t={times[0]:.2f}s"


def test_hold_muestra_progreso() -> None:
    eng = engine({"control": [Binding("Open_Palm", "hold", "toggle_pause",
                                      duration=1.0, label="Pausa")]})
    for i in range(18):  # 0.6 s
        feed(eng, hand("Open_Palm"), i / 30)
    assert 0.3 < eng.state.hold_progress < 0.8, eng.state.hold_progress
    assert eng.state.hold_label == "Pausa"


def test_repeat_dispara_a_intervalos() -> None:
    eng = engine({"control": [Binding("Thumb_Up", "repeat", "volume_step", interval=0.15)]})
    fired = sum(len(feed(eng, hand("Thumb_Up"), i / 30)) for i in range(31))  # ~1 s
    # Con 1 s de gesto e intervalo de 0.15 s caben entre 6 y 8 disparos.
    assert 6 <= fired <= 8, f"repeat disparó {fired} veces en 1 s"


def test_swipe_derecha() -> None:
    eng = engine({"control": [Binding("Closed_Fist", "swipe", "hotkey", direction="right")]})
    for i in range(4):  # estabilizar la pose sin moverse
        feed(eng, hand("Closed_Fist", cx=0.3), i / 30)
    events = []
    for i in range(6):  # desplazamiento de 0.20 en 0.20 s = 2 anchos de mano
        events += feed(eng, hand("Closed_Fist", cx=0.3 + 0.04 * (i + 1)), (4 + i) / 30)
    assert len(events) == 1, f"el barrido produjo {len(events)} eventos"
    assert events[0].direction == "right"


def test_swipe_no_dispara_la_direccion_contraria() -> None:
    eng = engine({"control": [Binding("Closed_Fist", "swipe", "hotkey", direction="left")]})
    for i in range(4):
        feed(eng, hand("Closed_Fist", cx=0.3), i / 30)
    events = []
    for i in range(6):
        events += feed(eng, hand("Closed_Fist", cx=0.3 + 0.04 * (i + 1)), (4 + i) / 30)
    assert not events, "un barrido a la derecha activó el binding de la izquierda"


def test_barrido_sobrevive_a_perder_la_pose_al_final() -> None:
    """Al barrer, la muñeca gira y el clasificador suelta la pose en el tramo final.

    Si el barrido se identificase con la etiqueta del último fotograma, los
    barridos horizontales se descartarían casi siempre.
    """
    eng = engine({"control": [Binding("Closed_Fist", "swipe", "hotkey", direction="right")]})
    for i in range(4):
        feed(eng, hand("Closed_Fist", cx=0.3), i / 30)
    events = []
    for i in range(6):
        # La pose deja de reconocerse a mitad del recorrido.
        label = "Closed_Fist" if i < 2 else "None"
        events += feed(eng, hand(label, cx=0.3 + 0.04 * (i + 1)), (4 + i) / 30)
    assert len(events) == 1, f"el barrido produjo {len(events)} eventos"
    assert events[0].direction == "right"


def test_barrido_sin_ninguna_pose_no_dispara() -> None:
    """Mover la mano sin que se reconozca ninguna pose no es un gesto."""
    eng = engine({"control": [Binding("Closed_Fist", "swipe", "hotkey", direction="right")]})
    events = []
    for i in range(10):
        events += feed(eng, hand("None", cx=0.3 + 0.04 * i), i / 30)
    assert not events


def test_movimiento_lento_no_es_barrido() -> None:
    """Reposicionar la mano sin intención no debe interpretarse como un gesto."""
    eng = engine({"control": [Binding("Closed_Fist", "swipe", "hotkey", direction="right")]})
    events = []
    for i in range(60):  # 0.20 en 2 s → 1 ancho de mano por segundo
        events += feed(eng, hand("Closed_Fist", cx=0.3 + 0.0033 * i), i / 30)
    assert not events, "un desplazamiento lento se interpretó como barrido"


def test_puerta_de_movimiento_inhibe_el_hold() -> None:
    eng = engine({"control": [Binding("Open_Palm", "hold", "toggle_pause", duration=0.3)]})
    events = []
    for i in range(40):  # mano agitándose por encima del umbral de velocidad
        cx = 0.4 + 0.06 * (i % 2)
        events += feed(eng, hand("Open_Palm", cx=cx), i / 30)
    assert not events, "el hold disparó con la mano en movimiento"


def test_pausa_solo_permite_bindings_globales() -> None:
    eng = engine({
        "global": [Binding("Open_Palm", "tap", "toggle_pause")],
        "control": [Binding("Victory", "tap", "media")],
    })
    assert not [e for i in range(10) for e in feed(eng, hand("Victory"), i / 30, paused=True)]

    eng.reset()
    events = [e for i in range(10) for e in feed(eng, hand("Open_Palm"), i / 30, paused=True)]
    assert len(events) == 1, "el binding global no funcionó en pausa"


def test_los_modos_estan_aislados() -> None:
    eng = engine({
        "control": [Binding("Victory", "tap", "media")],
        "mouse": [Binding("Victory", "tap", "mouse_click")],
    })
    events = [e for i in range(10) for e in feed(eng, hand("Victory"), i / 30, mode="mouse")]
    assert len(events) == 1
    assert events[0].binding.action == "mouse_click"


def test_deslizador_analogico_emite_valores() -> None:
    analog = {"control": {"action": "volume_set", "engage_time": 0.2,
                          "range": [0.30, 1.50], "label": "Volumen"}}
    eng = engine({"control": []}, analog)
    events = [e for i in range(20) for e in feed(eng, hand("None", slider=True), i / 30)]
    assert events, "la pose del deslizador no produjo ningún evento"
    assert all(e.binding.action == "volume_set" for e in events)
    assert all(0.0 <= e.value <= 1.0 for e in events)
    assert eng.state.slider_active


def test_deslizador_bloquea_los_demas_gestos() -> None:
    """Mientras el deslizador manda, ningún otro binding debe competir por la mano."""
    analog = {"control": {"action": "volume_set", "engage_time": 0.2,
                          "range": [0.30, 1.50], "label": "Volumen"}}
    eng = engine({"control": [Binding("Pointing_Up", "hold", "set_mode", duration=0.5)]}, analog)
    events = [e for i in range(30) for e in feed(eng, hand("Pointing_Up", slider=True), i / 30)]
    assert all(e.binding.action == "volume_set" for e in events), \
        "un binding estático se coló mientras el deslizador estaba activo"


def test_reset_olvida_el_estado() -> None:
    eng = engine({"control": [Binding("Victory", "tap", "media")]})
    assert sum(len(feed(eng, hand("Victory"), i / 30)) for i in range(10)) == 1
    eng.reset()
    assert sum(len(feed(eng, hand("Victory"), 1 + i / 30)) for i in range(10)) == 1


def test_geometria_de_la_pose_del_deslizador() -> None:
    assert lmk.is_slider_pose(hand("None", slider=True).landmarks)
    assert not lmk.is_slider_pose(hand("Victory").landmarks)


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
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
