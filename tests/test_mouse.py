"""Pruebas del modo cursor: mapeo a un monitor, alcance de bordes y clics.

Los tres fallos que se corrigen aquí eran invisibles leyendo el código y muy
visibles usándolo: con dos pantallas el cursor se movía deformado, no llegaba a
la barra de tareas, y soltaba clics fantasma al mover la mano.

Ejecutar con:  python tests/test_mouse.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic
from gesture_control import landmarks as lmk
from gesture_control import mouse as mouse_module
from gesture_control.recognizer import HandResult

UN_MONITOR = [(0, 0, 1920, 1080)]
DOS_MONITORES = [(-1366, 0, 1366, 768), (0, 0, 1920, 1080)]

CFG = {
    "active_region": [0.20, 0.15, 0.80, 0.75],
    "edge_overscan": 0.07,
    "min_smoothing": 1.0,      # sin inercia: las pruebas comprueban el mapeo
    "max_smoothing": 1.0,
    "deadzone_px": 0.0,
    "press_frames": 4,
    "release_frames": 2,
    "min_index_reach": 0.80,
}


class FakeInput:
    """Sustituto de win.input que registra en vez de tocar el sistema."""

    def __init__(self, screens):
        self.screens = screens
        self.cursor = (0, 0)
        self.events: list[str] = []

    def enable_dpi_awareness(self): pass
    def monitors(self): return list(self.screens)
    def virtual_screen(self): return self.screens[0]
    def move_cursor(self, x, y): self.cursor = (x, y)
    def mouse_down(self, button="left"): self.events.append(f"down:{button}")
    def mouse_up(self, button="left"): self.events.append(f"up:{button}")
    def click(self, button="left"): self.events.append(f"click:{button}")
    def scroll(self, amount): self.events.append(f"scroll:{amount}")


def make(screens=UN_MONITOR, **overrides):
    fake = FakeInput(screens)
    mouse_module.win_input = fake
    cfg = dict(CFG)
    cfg.update(overrides)
    controller = mouse_module.MouseController(cfg)
    controller.set_frame_aspect(16 / 9)
    return controller, fake


def point_in_region(controller, rx: float, ry: float):
    """Mano señalando a una fracción ``(rx, ry)`` de la zona activa.

    La palma se coloca por debajo de la yema y la acompaña, como en una mano
    real: dejarla fija haría que al señalar cerca de ella el índice pareciera
    recogido y la mano se leyera como un puño.
    """
    x0, y0, x1, y1 = controller.region
    tip = (x0 + rx * (x1 - x0), y0 + ry * (y1 - y0))
    return synthetic.pointing(tip=tip, cx=tip[0], cy=tip[1] + 0.13)


# --------------------------------------------------------------------------- #

def test_el_puno_se_distingue_por_el_alcance_del_indice() -> None:
    """Es el umbral del que dependen el clic y el enganche de la pinza."""
    cerrado = lmk.index_reach(synthetic.pointing(closed=True).landmarks)
    abierto = lmk.index_reach(synthetic.pointing(tip=(0.5, 0.3)).landmarks)
    assert cerrado < 0.80 < abierto, f"puño={cerrado:.2f} índice={abierto:.2f}"


def test_la_zona_activa_se_ajusta_a_la_relacion_del_monitor() -> None:
    """Sin esto, el movimiento se estira en un eje y se comprime en el otro."""
    ancho, _ = make([(0, 0, 1024, 768)])          # monitor 4:3
    x0, y0, x1, y1 = ancho.region
    relacion = ((x1 - x0) * (16 / 9)) / (y1 - y0)
    assert abs(relacion - 1024 / 768) < 0.02, f"relación resultante {relacion:.2f}"


def test_una_zona_ya_proporcionada_no_se_toca() -> None:
    controller, _ = make(UN_MONITOR)              # monitor 16:9, zona 16:9
    assert controller.region == tuple(CFG["active_region"])


def test_se_alcanzan_las_cuatro_esquinas() -> None:
    """El fallo de la barra de tareas: el borde inferior era inalcanzable."""
    controller, fake = make(UN_MONITOR)
    sx, sy, sw, sh = controller.screen
    esquinas = {(0.0, 0.0): (sx, sy), (1.0, 0.0): (sx + sw - 1, sy),
                (0.0, 1.0): (sx, sy + sh - 1), (1.0, 1.0): (sx + sw - 1, sy + sh - 1)}
    for (rx, ry), esperado in esquinas.items():
        controller.reset()
        controller.update(point_in_region(controller, rx, ry))
        assert fake.cursor == esperado, (
            f"la esquina {(rx, ry)} llegó a {fake.cursor}, se esperaba {esperado}")


def test_el_borde_se_alcanza_antes_del_limite_del_encuadre() -> None:
    """El sobrebarrido evita tener que llevar la mano donde el seguimiento falla."""
    controller, fake = make(UN_MONITOR)
    controller.update(point_in_region(controller, 0.05, 0.5))
    sx, _, _, _ = controller.screen
    assert fake.cursor[0] == sx, "al 5% de la zona ya debería tocar el borde"


def test_el_centro_de_la_zona_es_el_centro_de_la_pantalla() -> None:
    controller, fake = make(UN_MONITOR)
    controller.update(point_in_region(controller, 0.5, 0.5))
    sx, sy, sw, sh = controller.screen
    assert abs(fake.cursor[0] - (sx + sw / 2)) < 3
    assert abs(fake.cursor[1] - (sy + sh / 2)) < 3


def test_el_cursor_no_sale_del_monitor_elegido() -> None:
    """Con dos pantallas, el cursor debe quedarse en la que se está usando."""
    controller, fake = make(DOS_MONITORES)
    sx, sy, sw, sh = controller.screen
    for rx in (0.0, 0.25, 0.5, 0.75, 1.0):
        for ry in (0.0, 0.5, 1.0):
            controller.reset()
            controller.update(point_in_region(controller, rx, ry))
            x, y = fake.cursor
            assert sx <= x <= sx + sw and sy <= y <= sy + sh, \
                f"el cursor se fue a {(x, y)}, fuera de {controller.screen}"


def test_cambiar_de_pantalla_recorre_los_monitores() -> None:
    controller, _ = make(DOS_MONITORES)
    assert controller.monitor == 0
    assert controller.next_monitor() == 2
    assert controller.screen == DOS_MONITORES[1]
    assert controller.next_monitor() == 1


def test_el_clic_exige_varios_fotogramas_cerrados() -> None:
    """Una lectura suelta de puño al mover la mano no debe pulsar el botón."""
    controller, fake = make(UN_MONITOR)
    controller.update(point_in_region(controller, 0.5, 0.5))
    for _ in range(CFG["press_frames"] - 1):
        controller.update(synthetic.pointing(closed=True))
    assert "down:left" not in fake.events, "pulsó antes de tiempo"
    controller.update(synthetic.pointing(closed=True))
    assert "down:left" in fake.events


def test_el_boton_se_suelta_al_abrir_la_mano() -> None:
    controller, fake = make(UN_MONITOR)
    for _ in range(6):
        controller.update(synthetic.pointing(closed=True))
    assert controller.button_down
    for _ in range(CFG["release_frames"]):
        controller.update(point_in_region(controller, 0.5, 0.5))
    assert not controller.button_down
    assert fake.events.count("up:left") == 1


def test_perder_la_mano_suelta_el_boton() -> None:
    """Si no, el botón se quedaría pulsado arrastrando lo que hubiera debajo."""
    controller, fake = make(UN_MONITOR)
    for _ in range(6):
        controller.update(synthetic.pointing(closed=True))
    controller.update(HandResult())
    assert not controller.button_down
    assert fake.events[-1] == "up:left"


def test_congelado_no_mueve_el_cursor() -> None:
    controller, fake = make(UN_MONITOR)
    controller.update(point_in_region(controller, 0.5, 0.5))
    antes = fake.cursor
    controller.update(point_in_region(controller, 0.9, 0.9), frozen=True)
    assert fake.cursor == antes, "el cursor se movió mientras se desplazaba"


def test_agarrar_no_teletransporta_el_cursor() -> None:
    """Cerrar el puño cambia de referencia; el cursor debe quedarse donde estaba."""
    controller, fake = make(UN_MONITOR)
    controller.update(point_in_region(controller, 0.3, 0.3))
    antes = fake.cursor
    controller.update(synthetic.pointing(closed=True, cx=0.5, cy=0.5))
    salto = max(abs(fake.cursor[0] - antes[0]), abs(fake.cursor[1] - antes[1]))
    assert salto < 5, f"el cursor saltó {salto} px al agarrar"


if __name__ == "__main__":
    raise SystemExit(synthetic.run(globals()))
