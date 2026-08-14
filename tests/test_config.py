"""Pruebas de la validación de configuración.

La validación existe para que los errores de mapeo salgan al arrancar y no como
comportamiento errático a mitad de uso. La regla más importante es la que
prohíbe combinar permanencia y barrido en un mismo gesto: es el fallo que hacía
imposible cambiar de pestaña, porque entre barrido y barrido la mano se queda
quieta y se dispara el gesto estático.

Ejecutar con:  python tests/test_config.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gesture_control import config as config_module

BASE = {
    "apps": {"navegador": "chrome"},
    "bindings": {
        "control": [
            {"gesture": "Victory", "trigger": "swipe", "direction": "right",
             "action": "hotkey", "args": {"keys": ["ctrl", "tab"]}},
        ],
        "mouse": [
            {"gesture": "ILoveYou", "trigger": "hold", "duration": 0.5,
             "action": "open_wheel"},
        ],
    },
}


def build(**overrides):
    data = copy.deepcopy(BASE)
    for key, value in overrides.items():
        data[key] = value
    return config_module.Config(data)


def expect_error(fragment: str, **overrides) -> None:
    try:
        build(**overrides)
    except ValueError as exc:
        assert fragment in str(exc), (
            f"se esperaba un error sobre {fragment!r}, se obtuvo:\n{exc}")
        return
    raise AssertionError(f"no se rechazó una configuración inválida ({fragment})")


# --------------------------------------------------------------------------- #

def test_configuracion_minima_valida() -> None:
    cfg = build()
    assert len(cfg.bindings["control"]) == 1


def test_rechaza_permanencia_y_barrido_en_el_mismo_gesto() -> None:
    bindings = copy.deepcopy(BASE["bindings"])
    bindings["control"].append(
        {"gesture": "Victory", "trigger": "hold", "duration": 1.4,
         "action": "media", "args": {"key": "playpause"}})
    expect_error("combina 'hold'", bindings=bindings)


def test_rechaza_repeticion_y_barrido_en_el_mismo_gesto() -> None:
    bindings = copy.deepcopy(BASE["bindings"])
    bindings["control"].append(
        {"gesture": "Victory", "trigger": "repeat", "interval": 0.15,
         "action": "volume_step", "args": {"delta": 2}})
    expect_error("combina 'repeat'", bindings=bindings)


def test_permite_permanencia_y_barrido_en_gestos_distintos() -> None:
    bindings = copy.deepcopy(BASE["bindings"])
    bindings["control"].append(
        {"gesture": "Open_Palm", "trigger": "hold", "duration": 1.5,
         "action": "toggle_pause"})
    build(bindings=bindings)


def test_rechaza_accion_inexistente() -> None:
    bindings = copy.deepcopy(BASE["bindings"])
    bindings["control"][0]["action"] = "hacer_cafe"
    expect_error("no existe", bindings=bindings)


def test_rechaza_gesto_inexistente() -> None:
    bindings = copy.deepcopy(BASE["bindings"])
    bindings["control"][0]["gesture"] = "Spock"
    expect_error("no reconoce el gesto", bindings=bindings)


def test_rechaza_barrido_sin_direccion() -> None:
    bindings = copy.deepcopy(BASE["bindings"])
    del bindings["control"][0]["direction"]
    expect_error("necesita 'direction'", bindings=bindings)


def test_rechaza_aplicacion_desconocida_en_la_rueda() -> None:
    expect_error("no está en 'apps'", wheel={"items": {"control": [
        {"label": "Editor", "action": "launch", "args": {"app": "vim"}}]}})


def test_rechaza_accion_desconocida_en_la_rueda() -> None:
    expect_error("no existe", wheel={"items": {"control": [
        {"label": "Magia", "action": "abracadabra"}]}})


def test_rechaza_modo_inexistente_en_la_rueda() -> None:
    expect_error("no existe ese modo", wheel={"items": {"dibujo": [
        {"label": "Algo", "action": "toggle_pause"}]}})


def test_la_configuracion_del_proyecto_es_valida() -> None:
    """La comprobación que más vale: que el config.yaml entregado arranca."""
    cfg = config_module.load(ROOT / "config.yaml")
    assert cfg.bindings["control"], "el modo control se quedó sin gestos"
    assert cfg.wheel_items["control"], "la rueda del modo control está vacía"
    assert cfg.model_path.is_file(), f"falta el modelo en {cfg.model_path}"


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
