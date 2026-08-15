"""Enumeración, activación y acoplado de ventanas de Windows.

El conmutador de ventanas del proyecto no usa el Alt+Tab del sistema: lo dibuja
el propio visor. Eso exige la lista de ventanas y poder traer una al frente, que
es lo que hay aquí.

Enumerar ventanas «reales» tiene más truco del que parece: ``EnumWindows``
devuelve también ventanas de herramientas, ventanas propiedad de otras y —desde
Windows 10— aplicaciones de la Store suspendidas, que siguen siendo visibles
pero están *cloaked* y no deben aparecer en un conmutador.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
GW_OWNER = 4
DWMWA_CLOAKED = 14

SW_RESTORE = 9
SW_MINIMIZE = 6
SW_MAXIMIZE = 3

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SPI_GETWORKAREA = 0x0030

user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetWindow.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsWindow.argtypes = (wintypes.HWND,)

_get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_get_long.argtypes = (wintypes.HWND, ctypes.c_int)
_get_long.restype = ctypes.c_longlong if hasattr(user32, "GetWindowLongPtrW") else ctypes.c_long

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

try:
    _dwmapi = ctypes.WinDLL("dwmapi")
except OSError:  # pragma: no cover - Windows muy antiguo
    _dwmapi = None


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


@dataclass(frozen=True)
class Window:
    """Una ventana de nivel superior mostrable en el conmutador."""

    handle: int
    title: str

    @property
    def alive(self) -> bool:
        return bool(user32.IsWindow(self.handle))


def _is_cloaked(handle: int) -> bool:
    """Las aplicaciones de la Store suspendidas siguen «visibles» pero ocultas."""
    if _dwmapi is None:
        return False
    value = ctypes.c_int(0)
    result = _dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(handle), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
    )
    return result == 0 and bool(value.value)


def list_windows(limit: int = 12) -> list[Window]:
    """Ventanas de nivel superior con título, en el orden Z actual.

    El orden que devuelve ``EnumWindows`` es de delante hacia atrás, así que la
    primera entrada es la ventana activa y la segunda es la anterior: el mismo
    criterio que espera cualquiera que use Alt+Tab.
    """
    windows: list[Window] = []

    def callback(handle, _lparam):
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length == 0:
            return True
        if user32.GetWindow(handle, GW_OWNER):
            # Ventana propiedad de otra: diálogos, paletas y similares.
            return True
        style = _get_long(handle, GWL_EXSTYLE)
        if style & WS_EX_TOOLWINDOW and not style & WS_EX_APPWINDOW:
            return True
        if _is_cloaked(handle):
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        windows.append(Window(handle=int(handle), title=buffer.value))
        return len(windows) < limit

    try:
        user32.EnumWindows(_ENUM_PROC(callback), 0)
    except Exception:
        log.exception("No se pudieron enumerar las ventanas")
    return windows


def foreground() -> int:
    return int(user32.GetForegroundWindow())


def activate(handle: int) -> bool:
    """Trae una ventana al frente, sorteando el bloqueo de primer plano.

    Windows solo deja cambiar la ventana activa al proceso que ya la tiene. El
    rodeo habitual es adjuntar temporalmente la cola de entrada del hilo dueño
    de la ventana en primer plano, con lo que el sistema nos considera parte de
    esa interacción.
    """
    if not user32.IsWindow(handle):
        return False
    if user32.IsIconic(handle):
        user32.ShowWindow(handle, SW_RESTORE)

    if user32.SetForegroundWindow(handle) and foreground() == handle:
        return True

    current = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    other = user32.GetWindowThreadProcessId(current, ctypes.byref(pid))
    mine = kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(other, mine, True))
    try:
        user32.SetForegroundWindow(handle)
        user32.BringWindowToTop(handle)
    finally:
        if attached:
            user32.AttachThreadInput(other, mine, False)
    return foreground() == handle


def window_rect(handle: int) -> tuple[int, int, int, int] | None:
    """Rectángulo de una ventana, ``(x, y, ancho, alto)``."""
    rect = _RECT()
    if not user32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def scroll_foreground(amount: int) -> bool:
    """Desplaza la ventana activa, llevando el cursor sobre ella si hace falta.

    La rueda del ratón la recibe la ventana que está **bajo el cursor**, no la
    que tiene el foco. En modo control el cursor no se mueve, así que el
    desplazamiento acababa en cualquier ventana que hubiera quedado debajo —a
    menudo en la otra pantalla— y parecía que no funcionaba. Aquí se comprueba
    primero y, si el cursor está fuera, se centra sobre la ventana activa.
    """
    from . import input as win_input

    rect = window_rect(foreground())
    if rect is not None:
        x, y, width, height = rect
        cx, cy = win_input.cursor_position()
        if not (x <= cx < x + width and y <= cy < y + height):
            win_input.move_cursor(x + width // 2, y + height // 2)
    win_input.scroll(amount)
    return True


def work_area() -> tuple[int, int, int, int]:
    """Área utilizable del escritorio, ``(x, y, ancho, alto)``, sin la barra de tareas."""
    rect = _RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def snap(handle: int, where: str) -> bool:
    """Acopla una ventana a media pantalla, la maximiza o la minimiza.

    ``where`` puede ser ``left``, ``right``, ``top`` (maximizar) o ``bottom``
    (minimizar). Se usa ``SetWindowPos`` en vez de las combinaciones Win+flecha
    porque estas actúan sobre la ventana activa y aquí hay que colocar una
    ventana concreta, que puede no ser la que está en primer plano.
    """
    if not user32.IsWindow(handle):
        return False
    if where == "bottom":
        user32.ShowWindow(handle, SW_MINIMIZE)
        return True
    if where == "top":
        user32.ShowWindow(handle, SW_RESTORE)
        user32.ShowWindow(handle, SW_MAXIMIZE)
        return True

    x, y, width, height = work_area()
    if user32.IsIconic(handle):
        user32.ShowWindow(handle, SW_RESTORE)
    half = width // 2
    left = x if where == "left" else x + half
    user32.SetWindowPos(handle, 0, left, y, half, height, SWP_NOZORDER | SWP_NOACTIVATE)
    return True
