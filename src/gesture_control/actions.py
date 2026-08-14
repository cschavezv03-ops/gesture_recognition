"""Registro de acciones ejecutables y despachador de eventos del motor.

Las acciones se referencian por nombre desde ``config.yaml``; añadir una nueva
consiste en decorar una función con ``@action`` y mencionarla en la
configuración, sin tocar el motor de gestos.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from .engine import Event
from .win import apps as win_apps
from .win import input as win_input
from .win.volume import VolumeController

log = logging.getLogger(__name__)

#: Nombre de acción → función ``(router, **args) -> str | None`` (devuelve el aviso del HUD).
REGISTRY: dict[str, Callable[..., str | None]] = {}


def action(name: str):
    def decorator(fn: Callable[..., str | None]) -> Callable[..., str | None]:
        REGISTRY[name] = fn
        return fn
    return decorator


class ActionRouter:
    """Ejecuta los eventos del motor sobre el sistema y publica avisos al HUD."""

    def __init__(self, app, apps_config: dict[str, str]) -> None:
        self.app = app
        self.apps_config = apps_config
        self.volume = VolumeController()
        self.toast = ""
        self.toast_at = 0.0
        #: Evita reescribir el volumen en cada fotograma con el mismo valor.
        self._last_analog: float | None = None

    def notify(self, message: str) -> None:
        self.toast = message
        self.toast_at = time.monotonic()

    def dispatch(self, events: list[Event]) -> None:
        for event in events:
            fn = REGISTRY.get(event.binding.action)
            if fn is None:
                log.warning("Acción desconocida: %s", event.binding.action)
                continue
            args = dict(event.binding.args)
            if event.value is not None:
                args["value"] = event.value
            if event.direction:
                args.setdefault("direction", event.direction)
            try:
                message = fn(self, **args)
            except Exception:
                log.exception("Fallo al ejecutar la acción %s", event.binding.action)
                self.notify(f"Error en {event.binding.action}")
                continue
            if message:
                self.notify(message)


# --------------------------------------------------------------------------- #
# Audio
# --------------------------------------------------------------------------- #

@action("volume_step")
def _volume_step(router: ActionRouter, delta: float = 2.0, **_) -> str:
    level = router.volume.step(delta)
    return f"Volumen {round(level * 100)}%" if level >= 0 else "Volumen"


@action("volume_set")
def _volume_set(router: ActionRouter, value: float = 0.0, **_) -> None:
    # El deslizador emite en cada fotograma; solo se escribe si el cambio es audible.
    if router._last_analog is not None and abs(value - router._last_analog) < 0.01:
        return None
    router._last_analog = value
    router.volume.set(value)
    # Sin aviso: la barra del deslizador ya muestra el valor, y un aviso por
    # fotograma parpadearía sin aportar nada.
    return None


@action("volume_mute_toggle")
def _volume_mute(router: ActionRouter, **_) -> str:
    return "Silencio activado" if router.volume.toggle_mute() else "Silencio desactivado"


@action("media")
def _media(router: ActionRouter, key: str = "playpause", **_) -> str:
    win_input.press(key)
    return {"playpause": "Reproducir / pausar",
            "nexttrack": "Pista siguiente",
            "prevtrack": "Pista anterior"}.get(key, key)


# --------------------------------------------------------------------------- #
# Teclado y ventanas
# --------------------------------------------------------------------------- #

@action("hotkey")
def _hotkey(router: ActionRouter, keys: list[str] | None = None, label: str = "", **_) -> str:
    if not keys:
        raise ValueError("La acción 'hotkey' necesita una lista 'keys'")
    win_input.press(*keys)
    return label or "+".join(keys)


# --------------------------------------------------------------------------- #
# Aplicaciones
# --------------------------------------------------------------------------- #

@action("launch")
def _launch(router: ActionRouter, app: str = "", **_) -> str:
    command = router.apps_config.get(app)
    if not command:
        raise KeyError(f"No hay ninguna aplicación llamada {app!r} en la sección 'apps'")
    win_apps.launch(command)
    return f"Abriendo {app}"


# --------------------------------------------------------------------------- #
# Control de la propia aplicación
# --------------------------------------------------------------------------- #

@action("set_mode")
def _set_mode(router: ActionRouter, mode: str = "control", **_) -> str:
    router.app.set_mode(mode)
    return f"Modo {mode}"


@action("toggle_pause")
def _toggle_pause(router: ActionRouter, **_) -> str:
    return "En pausa" if router.app.toggle_pause() else "Activo"


@action("mouse_click")
def _mouse_click(router: ActionRouter, button: str = "left", **_) -> str:
    router.app.mouse.click(button)
    return f"Clic {button}"


@action("mouse_double_click")
def _mouse_double_click(router: ActionRouter, **_) -> str:
    router.app.mouse.double_click()
    return "Doble clic"


@action("open_wheel")
def _open_wheel(router: ActionRouter, **_) -> str | None:
    # Sin aviso al abrir: la propia rueda es la realimentación.
    router.app.open_wheel()
    return None


@action("open_switcher")
def _open_switcher(router: ActionRouter, **_) -> str | None:
    if not router.app.open_switcher():
        return "No hay otras ventanas abiertas"
    return None


@action("scroll")
def _scroll(router: ActionRouter, amount: int = 120, **_) -> None:
    # Sin aviso: el desplazamiento se ve en la pantalla que se está leyendo, y
    # un aviso por muesca convertiría el HUD en un parpadeo.
    win_input.scroll(int(amount))
    return None


@action("quit")
def _quit(router: ActionRouter, **_) -> str:
    router.app.stop()
    return "Cerrando"
