"""Envío de teclas y eventos de ratón a Windows mediante ``SendInput`` (user32).

Se usa ctypes directamente en lugar de una librería de automatización porque
``SendInput`` es la única vía fiable para las combinaciones con la tecla Windows
(cambio de escritorio virtual, Task View) y porque permite mover el cursor en
coordenadas absolutas sobre el escritorio virtual completo, incluyendo
configuraciones multimonitor.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

# ULONG_PTR no está expuesto por ctypes.wintypes y su tamaño depende del build.
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

#: Códigos de tecla virtual usados por las acciones del proyecto.
VK: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11,
    "alt": 0x12, "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "nexttrack": 0xB0, "prevtrack": 0xB1, "stop": 0xB2, "playpause": 0xB3,
    "browserback": 0xA6, "browserforward": 0xA7, "browserrefresh": 0xA7,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}
VK.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"})
VK.update({c: ord(c) for c in "0123456789"})

#: Teclas que requieren el flag extendido para que Windows las interprete bien.
_EXTENDED = {
    0x25, 0x26, 0x27, 0x28,  # flechas
    0x21, 0x22, 0x23, 0x24,  # av/re pág, inicio, fin
    0x2D, 0x2E,              # insert, delete
    0x5B, 0x5C, 0x5D,        # win izq/der, menú contextual
    0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3,  # multimedia
}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def _send(*events: _INPUT) -> None:
    n = len(events)
    array = (_INPUT * n)(*events)
    sent = user32.SendInput(n, array, ctypes.sizeof(_INPUT))
    if sent != n:
        raise ctypes.WinError(ctypes.get_last_error())


def _key_event(vk: int, keyup: bool) -> _INPUT:
    flags = KEYEVENTF_KEYUP if keyup else 0
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    return _INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0))


def _resolve(key: str) -> int:
    vk = VK.get(key.strip().lower())
    if vk is None:
        raise KeyError(f"tecla desconocida: {key!r}")
    return vk


def press(*keys: str) -> None:
    """Pulsa una combinación: mantiene los modificadores y libera en orden inverso.

    ``press("ctrl", "shift", "tab")`` equivale a Ctrl+Shift+Tab.
    """
    codes = [_resolve(k) for k in keys]
    events = [_key_event(vk, keyup=False) for vk in codes]
    events += [_key_event(vk, keyup=True) for vk in reversed(codes)]
    _send(*events)


def key_down(key: str) -> None:
    _send(_key_event(_resolve(key), keyup=False))


def key_up(key: str) -> None:
    _send(_key_event(_resolve(key), keyup=True))


def enable_dpi_awareness() -> None:
    """Evita que Windows escale las coordenadas del cursor en pantallas con DPI alto."""
    try:  # Windows 10 1703+
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except AttributeError:
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except OSError:
            user32.SetProcessDPIAware()


def virtual_screen() -> tuple[int, int, int, int]:
    """Devuelve ``(x, y, ancho, alto)`` del escritorio virtual completo."""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def move_cursor(x: int, y: int) -> None:
    """Mueve el cursor a coordenadas absolutas de pantalla."""
    vx, vy, vw, vh = virtual_screen()
    # SendInput usa un rango normalizado de 0..65535 sobre el escritorio virtual.
    nx = int(round((x - vx) * 65535 / max(vw - 1, 1)))
    ny = int(round((y - vy) * 65535 / max(vh - 1, 1)))
    _send(_INPUT(
        type=INPUT_MOUSE,
        mi=_MOUSEINPUT(
            dx=nx, dy=ny, mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            time=0, dwExtraInfo=0,
        ),
    ))


def _mouse_flag(flag: int, data: int = 0) -> _INPUT:
    return _INPUT(
        type=INPUT_MOUSE,
        mi=_MOUSEINPUT(dx=0, dy=0, mouseData=data, dwFlags=flag, time=0, dwExtraInfo=0),
    )


def mouse_down(button: str = "left") -> None:
    _send(_mouse_flag(MOUSEEVENTF_RIGHTDOWN if button == "right" else
                      MOUSEEVENTF_MIDDLEDOWN if button == "middle" else MOUSEEVENTF_LEFTDOWN))


def mouse_up(button: str = "left") -> None:
    _send(_mouse_flag(MOUSEEVENTF_RIGHTUP if button == "right" else
                      MOUSEEVENTF_MIDDLEUP if button == "middle" else MOUSEEVENTF_LEFTUP))


def click(button: str = "left") -> None:
    mouse_down(button)
    mouse_up(button)


def scroll(amount: int) -> None:
    """Desplaza la rueda. ``amount`` positivo sube; 120 es un "clic" de rueda."""
    _send(_mouse_flag(MOUSEEVENTF_WHEEL, data=amount & 0xFFFFFFFF))
