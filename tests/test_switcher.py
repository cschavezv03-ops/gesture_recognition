"""Pruebas del conmutador de ventanas.

Aquí lo delicado es la correspondencia entre dónde señala el dedo y qué ventana
queda marcada, y que llevar el dedo a un borde acople la ventana que se había
señalado antes y no otra. Un error de índice significa saltar a la ventana
equivocada, que es de las cosas más molestas que puede hacer un programa así.

Ejecutar con:  python tests/test_switcher.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic
from gesture_control import switcher as switcher_module
from gesture_control.recognizer import HandResult
from gesture_control.switcher import WindowSwitcher
from gesture_control.win.windows import Window

CFG = {
    "region": [0.22, 0.18, 0.78, 0.74],
    "max_windows": 9,
    "edge_band": 0.13,
    "lost_timeout": 0.7,
    "confirm_frames": 3,
    "snap": True,
}


class FakeWindow(Window):
    """Ventana que siempre se considera viva, sin tocar la API de Windows."""

    @property
    def alive(self) -> bool:
        return True


def fake_windows(count: int):
    def _list(limit=12):
        return [FakeWindow(handle=1000 + i, title=f"Ventana {i}")
                for i in range(min(count, limit))]
    return _list


def make(count: int = 6) -> WindowSwitcher:
    switcher_module.win.list_windows = fake_windows(count)
    sw = WindowSwitcher(CFG)
    assert sw.open(), "el conmutador no se abrió"
    return sw


def aim(sx: float, sy: float) -> HandResult:
    """Mano señalando a la posición ``(sx, sy)`` en coordenadas 0-1 de pantalla."""
    x0, y0, x1, y1 = CFG["region"]
    return synthetic.pointing(tip=(x0 + sx * (x1 - x0), y0 + sy * (y1 - y0)))


def confirm(sw: WindowSwitcher, start: float = 1.0):
    """Abre la mano los fotogramas necesarios y devuelve el resultado."""
    for i in range(CFG["confirm_frames"] + 1):
        result = sw.update(synthetic.pointing(open_hand=True), start + i / 30)
        if result:
            return result
    return None


# --------------------------------------------------------------------------- #

def test_no_abre_con_una_sola_ventana() -> None:
    switcher_module.win.list_windows = fake_windows(1)
    assert not WindowSwitcher(CFG).open()


def test_preselecciona_la_ventana_anterior() -> None:
    """Soltar sin apuntar debe llevar a la ventana anterior, como Alt+Tab."""
    assert make().selected == 1


def test_senalar_marca_la_casilla_correcta() -> None:
    sw = make(6)
    gx, gy, gw, gh = sw.grid
    for position in range(6):
        cx, cy, cw, ch = sw.cell(position)
        sw.update(aim(cx + cw / 2, cy + ch / 2), 0.0)
        assert sw.selected == position, (
            f"señalando a la casilla {position} se marcó la {sw.selected}")


def test_abrir_la_mano_confirma_lo_senalado() -> None:
    sw = make(6)
    cx, cy, cw, ch = sw.cell(4)
    sw.update(aim(cx + cw / 2, cy + ch / 2), 0.0)
    result = confirm(sw)
    assert result is not None, "abrir la mano no confirmó"
    assert result.action == "activate"
    assert result.window.title == "Ventana 4"
    assert not sw.active


def test_un_solo_fotograma_abierto_no_confirma() -> None:
    """Al pasar de señalar a soltar los dedos se extienden de uno en uno."""
    sw = make()
    assert sw.update(synthetic.pointing(open_hand=True), 0.0) is None
    assert sw.active


def test_los_bordes_acoplan() -> None:
    sw = make(4)
    cx, cy, cw, ch = sw.cell(2)
    sw.update(aim(cx + cw / 2, cy + ch / 2), 0.0)
    sw.update(aim(0.03, 0.5), 0.1)          # banda izquierda
    assert sw.edge == "left"
    result = confirm(sw)
    assert result.action == "snap"
    assert result.edge == "left"
    assert result.window.title == "Ventana 2", \
        "acopló una ventana distinta de la que estaba señalada"


def test_el_borde_superior_maximiza() -> None:
    sw = make(4)
    sw.update(aim(0.5, 0.03), 0.0)
    assert sw.edge == "top"


def test_la_rejilla_no_llega_a_las_bandas() -> None:
    """Si una casilla tocara el borde, señalar la esquina acoplaría sin querer."""
    sw = make(6)
    gx, gy, gw, gh = sw.grid
    assert gx >= CFG["edge_band"] - 1e-6
    assert gx + gw <= 1.0 - CFG["edge_band"] + 1e-6


def test_salir_del_borde_devuelve_la_seleccion() -> None:
    sw = make(6)
    sw.update(aim(0.03, 0.5), 0.0)
    assert sw.edge == "left"
    cx, cy, cw, ch = sw.cell(3)
    sw.update(aim(cx + cw / 2, cy + ch / 2), 0.1)
    assert sw.edge == "", "el borde quedó pegado al volver a la rejilla"
    assert sw.selected == 3


def test_perder_la_mano_cancela_tras_el_margen() -> None:
    sw = make()
    assert sw.update(HandResult(), 0.1) is None
    assert sw.active, "una pérdida breve no debe cerrar el conmutador"
    result = sw.update(HandResult(), 1.2)
    assert result is not None and result.action == "cancel"
    assert not sw.active


def test_el_puno_no_confirma() -> None:
    """Al abrirse con el puño, mantenerlo no debe saltar a ninguna ventana."""
    sw = make()
    for i in range(10):
        assert sw.update(synthetic.pointing(closed=True), i / 30) is None
    assert sw.active


if __name__ == "__main__":
    raise SystemExit(synthetic.run(globals()))
