"""Lanzamiento de aplicaciones de Windows definidas en la configuración."""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)


def launch(command: str) -> None:
    """Ejecuta ``command`` de forma desacoplada, sin abrir una ventana de consola.

    Se pasa por ``cmd /c start`` para aceptar tanto rutas de ejecutables como
    alias del sistema (``ms-settings:``, ``shell:``, URLs) con la misma sintaxis.
    """
    log.info("Lanzando: %s", command)
    subprocess.Popen(
        f'start "" {command}',
        shell=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
