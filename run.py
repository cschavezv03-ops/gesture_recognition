"""Lanzador del proyecto: `python run.py [opciones]`.

Existe para que no haga falta instalar el paquete ni exportar PYTHONPATH; añade
`src/` al path y delega en `gesture_control.__main__`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Silencia el ruido de OpenCV y de la capa C++ de MediaPipe, que en cada arranque
# vuelca decenas de avisos sobre backends de captura y sobre el grafo del modelo.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from gesture_control.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
