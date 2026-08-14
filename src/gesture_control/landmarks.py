"""Geometría sobre los 21 landmarks de la mano.

El modelo preentrenado solo distingue siete gestos discretos. Todo lo demás —
control continuo del volumen, detección de la pinza, apertura de la mano — sale
de medir directamente los landmarks, normalizando siempre por el tamaño aparente
de la mano para que las medidas no dependan de la distancia a la cámara.
"""

from __future__ import annotations

import numpy as np

# Índices de landmarks según la topología de MediaPipe Hands.
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

PALM_POINTS = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)

#: Pares de landmarks que forman el esqueleto de la mano, para dibujarlo.
CONNECTIONS: tuple[tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)


def hand_scale(lm: np.ndarray) -> float:
    """Tamaño de referencia de la mano: distancia muñeca → nudillo del corazón.

    Es la magnitud más estable frente a rotaciones, y sirve como unidad para
    normalizar el resto de distancias.
    """
    return float(max(np.linalg.norm(lm[MIDDLE_MCP, :2] - lm[WRIST, :2]), 1e-6))


def palm_center(lm: np.ndarray) -> np.ndarray:
    """Centro de la palma en coordenadas normalizadas ``(x, y)``."""
    return lm[list(PALM_POINTS), :2].mean(axis=0)


def pinch_ratio(lm: np.ndarray) -> float:
    """Separación pulgar–índice normalizada por el tamaño de la mano.

    Vale ~0.15 con los dedos juntos y ~1.8 con la pinza completamente abierta.
    """
    return float(np.linalg.norm(lm[THUMB_TIP, :2] - lm[INDEX_TIP, :2]) / hand_scale(lm))


#: Cadena de falanges de cada dedo, del nudillo a la punta.
_FINGER_CHAINS = (
    (INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP),
    (MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP),
    (RING_MCP, RING_PIP, RING_DIP, RING_TIP),
    (PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP),
)

#: Rectitud mínima (cuerda/arco) para dar un dedo por extendido.
_STRAIGHTNESS = 0.75


def _finger_extended(lm: np.ndarray, chain: tuple[int, int, int, int]) -> bool:
    """Un dedo está extendido si está recto, no si apunta lejos de la muñeca.

    Se compara la distancia en línea recta del nudillo a la punta con la suma de
    las tres falanges: en un dedo estirado ambas coinciden, y en uno recogido la
    cuerda es mucho más corta que el recorrido.

    La medida anterior comparaba distancias a la muñeca, y eso solo funciona con
    la mano vertical: al apuntar hacia abajo la punta queda más cerca de la
    muñeca que el nudillo y un dedo perfectamente estirado se leía como
    recogido. Esta versión no depende de la orientación de la mano.
    """
    points = lm[list(chain), :2]
    arc = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
    chord = float(np.linalg.norm(points[-1] - points[0]))
    return chord > arc * _STRAIGHTNESS


def thumb_extended(lm: np.ndarray) -> bool:
    """Mide si el pulgar está *separado* de la mano, no si está recto.

    El pulgar no sirve la prueba de rectitud de los demás dedos: dentro de un
    puño sigue estando casi recto, solo que pegado. Lo que lo distingue es la
    separación respecto al nudillo del índice.

    Deliberadamente es un umbral exigente, porque su único cometido es separar
    la pinza del gesto de apuntar, y confundirlos haría que el deslizador de
    volumen se activara al intentar entrar en modo ratón. Como efecto
    secundario, un pulgar arriba se clasifica aquí como «no separado»: es
    correcto para lo que se usa, y ningún gesto depende de lo contrario.
    """
    span = np.linalg.norm(lm[THUMB_TIP, :2] - lm[INDEX_MCP, :2])
    return bool(span / hand_scale(lm) > 0.75)


def fingers_extended(lm: np.ndarray) -> tuple[bool, bool, bool, bool, bool]:
    """Estado de los cinco dedos en orden pulgar, índice, corazón, anular, meñique."""
    return (thumb_extended(lm), *(_finger_extended(lm, c) for c in _FINGER_CHAINS))


def is_slider_pose(lm: np.ndarray) -> bool:
    """Pose del control analógico: pulgar e índice extendidos, resto recogidos.

    Se exige el pulgar extendido para distinguirla de ``Pointing_Up``, que el
    modelo reconoce con el pulgar pegado a la palma. Sin esa distinción el
    deslizador de volumen y el cambio a modo ratón se dispararían mutuamente.
    """
    thumb, index, middle, ring, pinky = fingers_extended(lm)
    return thumb and index and not middle and not ring and not pinky


def is_pinch_closed(lm: np.ndarray, threshold: float = 0.45) -> bool:
    """La punta del pulgar toca la del índice — el "clic" del modo ratón."""
    return pinch_ratio(lm) < threshold


def normalized_to_pixels(lm: np.ndarray, width: int, height: int) -> np.ndarray:
    """Convierte landmarks normalizados a píxeles enteros para dibujarlos."""
    pts = lm[:, :2] * np.array([width, height], dtype=np.float32)
    return pts.astype(np.int32)


def remap_to_screen(
    x: float,
    y: float,
    region: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Reescala un punto de la región activa del encuadre a todo el rango 0–1.

    La región activa evita tener que llevar la mano hasta los bordes físicos del
    encuadre, donde el seguimiento se degrada y el brazo se cansa.
    """
    x0, y0, x1, y1 = region
    nx = (x - x0) / max(x1 - x0, 1e-6)
    ny = (y - y0) / max(y1 - y0, 1e-6)
    return min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0)
