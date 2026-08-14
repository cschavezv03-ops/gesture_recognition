"""Carga y validación de la configuración del proyecto."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from . import poses
from .actions import REGISTRY as ACTIONS
from .engine import CANNED_GESTURES, Binding
from .wheel import WheelItem

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"
LOCAL_CONFIG = PROJECT_ROOT / "config.local.yaml"

VALID_TRIGGERS = {"tap", "hold", "repeat", "swipe"}
VALID_DIRECTIONS = {"left", "right", "up", "down"}


def _deep_merge(base: dict, override: dict) -> dict:
    """Fusiona ``override`` sobre ``base`` recursivamente; las listas se sustituyen."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Config:
    """Configuración ya validada, con los bindings convertidos a objetos."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.raw = data
        self.camera = data.get("camera", {})
        self.recognizer = data.get("recognizer", {})
        self.engine = data.get("engine", {})
        self.mouse = data.get("mouse", {})
        self.hud = data.get("hud", {})
        self.apps = data.get("apps", {})
        self.analog = data.get("analog", {})

        self.bindings: dict[str, list[Binding]] = {}
        for group, items in (data.get("bindings") or {}).items():
            self.bindings[group] = [Binding.from_dict(item) for item in (items or [])]

        self.wheel = data.get("wheel", {})
        self.wheel_items: dict[str, list[WheelItem]] = {
            mode: [WheelItem.from_dict(item) for item in (items or [])]
            for mode, items in (self.wheel.get("items") or {}).items()
        }

        self.model_path = self._resolve_model_path()
        self._validate()

    def _resolve_model_path(self) -> Path:
        raw = self.recognizer.get("model_path", "models/gesture_recognizer.task")
        path = Path(raw)
        return path if path.is_absolute() else (PROJECT_ROOT / path)

    def _validate(self) -> None:
        errors: list[str] = []
        modes = set(self.bindings) - {"global"}

        for group, items in self.bindings.items():
            for b in items:
                where = f"bindings.{group} → {b.gesture}/{b.trigger}"
                if b.trigger not in VALID_TRIGGERS:
                    errors.append(f"{where}: trigger inválido {b.trigger!r} "
                                  f"(válidos: {sorted(VALID_TRIGGERS)})")
                if b.gesture not in CANNED_GESTURES:
                    errors.append(f"{where}: el modelo no reconoce el gesto {b.gesture!r} "
                                  f"(válidos: {list(CANNED_GESTURES)})")
                if b.trigger == "swipe" and b.direction not in VALID_DIRECTIONS:
                    errors.append(f"{where}: un barrido necesita 'direction' "
                                  f"entre {sorted(VALID_DIRECTIONS)}")
                errors += self._check_action(where, b.action, b.args, modes)

        # Un gesto no puede tener dos disparos estáticos distintos en el mismo grupo:
        # ambos competirían por la misma pose y el resultado sería impredecible.
        for group, items in self.bindings.items():
            seen: dict[tuple[str, str], str] = {}
            for b in items:
                if b.trigger == "swipe":
                    continue
                key = (b.gesture, b.trigger)
                if key in seen:
                    errors.append(f"bindings.{group}: {b.gesture} tiene dos triggers "
                                  f"'{b.trigger}' ({seen[key]} y {b.action})")
                seen[key] = b.action

        errors += self._check_static_swipe_conflicts()

        for mode, items in self.wheel_items.items():
            if mode not in modes:
                errors.append(f"wheel.items.{mode}: no existe ese modo")
            for item in items:
                errors += self._check_action(f"wheel.items.{mode} → {item.label}",
                                             item.action, item.args, modes)

        if errors:
            raise ValueError("Errores en config.yaml:\n  - " + "\n  - ".join(errors))

    def _check_action(self, where: str, action: str, args: dict,
                      modes: set[str]) -> list[str]:
        """Comprueba que una acción exista y que sus argumentos apunten a algo real."""
        if action not in ACTIONS:
            return [f"{where}: la acción {action!r} no existe "
                    f"(disponibles: {sorted(ACTIONS)})"]
        errors: list[str] = []
        if action == "launch" and args.get("app") not in self.apps:
            errors.append(f"{where}: la aplicación {args.get('app')!r} no está en 'apps'")
        if action == "set_mode" and args.get("mode") not in modes:
            errors.append(f"{where}: el modo {args.get('mode')!r} no tiene bindings definidos")
        if action == "hotkey" and not args.get("keys"):
            errors.append(f"{where}: 'hotkey' necesita una lista 'keys'")
        return errors

    def _check_static_swipe_conflicts(self) -> list[str]:
        """Prohíbe combinar en un mismo gesto un disparo estático y un barrido.

        Es el error que hacía imposible cambiar de pestaña: al encadenar intentos
        de barrido la mano se queda quieta entre uno y otro, y el temporizador de
        permanencia se cumple antes de que el barrido llegue a completarse. Las
        dos acciones compiten por la misma pose y gana la que no se pretendía.
        """
        errors: list[str] = []
        for group, items in self.bindings.items():
            statics = {b.gesture: b for b in items if b.trigger in ("hold", "repeat")}
            swipes = {b.gesture for b in items if b.trigger == "swipe"}
            for gesture in sorted(statics.keys() & swipes):
                other = statics[gesture]
                errors.append(
                    f"bindings.{group}: {gesture} combina '{other.trigger}' "
                    f"({other.action}) con barridos. Al encadenar barridos la mano "
                    f"se queda quieta y dispara el gesto estático; usa poses "
                    f"distintas o lleva una de las dos acciones a la rueda."
                )
        return errors

    def describe_bindings(self, group: str) -> list[tuple[str, str]]:
        """Pares ``(cómo se hace el gesto, qué hace)`` para la chuleta del HUD."""
        rows = [
            (poses.describe(b.gesture, b.trigger, b.direction, b.duration),
             b.label or b.action)
            for b in self.bindings.get(group, [])
        ]
        # El control analógico no es un binding, pero desde fuera se usa igual y
        # omitirlo de la chuleta lo dejaría invisible.
        analog = self.analog.get(group)
        if analog:
            rows.append((f"{poses.SLIDER.name} ◄►", analog.get("label", "Analógico")))
        return rows


def load(path: str | Path | None = None) -> Config:
    """Carga ``config.yaml`` y le superpone ``config.local.yaml`` si existe."""
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.is_file():
        raise FileNotFoundError(f"No se encontró la configuración en {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if path == DEFAULT_CONFIG and LOCAL_CONFIG.is_file():
        local = yaml.safe_load(LOCAL_CONFIG.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, local)
    return Config(data)
