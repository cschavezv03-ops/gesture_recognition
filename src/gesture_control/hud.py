"""Interfaz de superposición con estética de visor táctico.

El HUD no es decorativo. Un control por gestos sin realimentación es imposible
de usar: hay que ver qué pose se está reconociendo, cuánto falta para que un
gesto por permanencia dispare, y —sobre todo— si el sistema está activo. La
mayoría de los «no funciona» de un sistema así son en realidad «estaba en pausa»
o «estaba en modo prueba», de modo que esos dos estados se anuncian de forma
que no se puedan pasar por alto.

El texto se dibuja con Pillow porque las fuentes Hershey de OpenCV carecen de
acentos. Para que eso no cueste fotogramas hay tres decisiones de rendimiento:

* El fotograma BGR se entrega a Pillow **sin convertir canales**: Pillow lo lee
  como RGB, lo cual es indiferente mientras los colores se declaren ya en orden
  BGR, porque los bytes se escriben en el mismo orden en que se declaran.
* Todos los textos de un fotograma se vuelcan en una única pasada.
* Los paneles estáticos —la chuleta y la guía de gestos— se rasterizan una vez
  y se cachean como imagen.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import landmarks as lmk
from . import poses
from .engine import EngineState

# Paleta en BGR, el orden de canales de OpenCV.
COL_PRIMARY = (255, 224, 70)     # cian eléctrico: estructura del visor
COL_SOFT = (205, 165, 50)
COL_DIM = (120, 95, 32)
COL_ACCENT = (74, 194, 255)      # ámbar: acciones y valores
COL_ALERT = (48, 59, 255)        # rojo: pausa y avisos
COL_OK = (130, 240, 160)
COL_TEXT = (250, 245, 235)
COL_BG = (26, 18, 10)

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


class _Fonts:
    """Fuentes y medidas de texto cacheadas."""

    def __init__(self) -> None:
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._widths: dict[tuple[str, int, int], int] = {}

    def get(self, size: int) -> ImageFont.FreeTypeFont:
        font = self._fonts.get(size)
        if font is None:
            for candidate in _FONT_CANDIDATES:
                if Path(candidate).is_file():
                    font = ImageFont.truetype(candidate, size)
                    break
            else:
                font = ImageFont.load_default()
            self._fonts[size] = font
        return font

    def width(self, text: str, size: int, tracking: int = 0) -> int:
        key = (text, size, tracking)
        value = self._widths.get(key)
        if value is None:
            font = self.get(size)
            if tracking:
                value = int(sum(font.getlength(c) for c in text) + tracking * max(len(text) - 1, 0))
            else:
                value = int(font.getlength(text))
            self._widths[key] = value
        return value


def _draw_text(draw: ImageDraw.ImageDraw, xy, text, font, fill, tracking: int = 0) -> None:
    """Dibuja texto, opcionalmente con separación extra entre caracteres.

    El espaciado ancho es lo que da a los rótulos su aire de instrumento; solo
    se usa en cabeceras cortas, porque obliga a dibujar carácter a carácter.
    """
    if not tracking:
        draw.text(xy, text, font=font, fill=fill)
        return
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += font.getlength(char) + tracking


class _TextLayer:
    """Acumula textos y los vuelca en una única pasada de Pillow por fotograma."""

    def __init__(self, fonts: _Fonts) -> None:
        self.fonts = fonts
        self._pending: list[tuple] = []

    def add(self, x: int, y: int, text: str, size: int = 16,
            color: tuple[int, int, int] = COL_TEXT, tracking: int = 0) -> None:
        self._pending.append((int(x), int(y), text, size, color, tracking))

    def width(self, text: str, size: int, tracking: int = 0) -> int:
        return self.fonts.width(text, size, tracking)

    def right(self, x: int, y: int, text: str, size: int = 16,
              color: tuple[int, int, int] = COL_TEXT, tracking: int = 0) -> None:
        """Añade texto alineado a la derecha de ``x``."""
        self.add(x - self.width(text, size, tracking), y, text, size, color, tracking)

    def flush(self, frame: np.ndarray) -> np.ndarray:
        if not self._pending:
            return frame
        image = Image.fromarray(frame)
        draw = ImageDraw.Draw(image)
        for x, y, text, size, color, tracking in self._pending:
            _draw_text(draw, (x, y), text, self.fonts.get(size), color, tracking)
        self._pending.clear()
        # Se escribe sobre el mismo búfer para no reasignar 2,7 MB por fotograma.
        np.copyto(frame, np.asarray(image))
        return frame


# --------------------------------------------------------------------------- #
# Primitivas del visor
# --------------------------------------------------------------------------- #

def _tint(frame: np.ndarray, x: int, y: int, w: int, h: int, alpha: float = 0.62,
          color=COL_BG) -> tuple[int, int, int, int] | None:
    """Oscurece un rectángulo para que el texto sea legible sobre cualquier fondo."""
    x, y = max(int(x), 0), max(int(y), 0)
    w, h = min(int(w), frame.shape[1] - x), min(int(h), frame.shape[0] - y)
    if w <= 0 or h <= 0:
        return None
    roi = frame[y:y + h, x:x + w]
    overlay = np.empty_like(roi)
    overlay[:] = color
    cv2.addWeighted(overlay, alpha, roi, 1 - alpha, 0, dst=roi)
    return x, y, w, h


def _brackets(frame: np.ndarray, x: int, y: int, w: int, h: int,
              color=COL_PRIMARY, size: int = 16, thickness: int = 2) -> None:
    """Escuadras en las cuatro esquinas: el marco característico de un visor."""
    x, y, w, h = int(x), int(y), int(w), int(h)
    for cx, sx in ((x, 1), (x + w, -1)):
        for cy, sy in ((y, 1), (y + h, -1)):
            cv2.line(frame, (cx, cy), (cx + sx * size, cy), color, thickness, cv2.LINE_AA)
            cv2.line(frame, (cx, cy), (cx, cy + sy * size), color, thickness, cv2.LINE_AA)


def _panel(frame: np.ndarray, x: int, y: int, w: int, h: int,
           alpha: float = 0.62, color=COL_PRIMARY) -> None:
    """Bloque translúcido con escuadras y una guía superior."""
    if _tint(frame, x, y, w, h, alpha) is None:
        return
    _brackets(frame, x, y, w, h, color, size=14, thickness=1)
    cv2.line(frame, (int(x) + 16, int(y)), (int(x + w) - 16, int(y)), color, 1, cv2.LINE_AA)


def _ring(frame: np.ndarray, center: tuple[int, int], radius: int,
          start: float, sweep: float, color, thickness: int = 2) -> None:
    if sweep <= 0:
        return
    cv2.ellipse(frame, (int(center[0]), int(center[1])), (int(radius), int(radius)),
                0, start, start + sweep, color, thickness, cv2.LINE_AA)


def _fill_sector(frame: np.ndarray, center: tuple[int, int], r_in: int, r_out: int,
                 start: float, sweep: float, color, alpha: float = 0.55) -> None:
    """Rellena un sector de corona circular de forma translúcida.

    Se construye como polígono en lugar de usar un arco muy grueso: ``cv2.ellipse``
    con un grosor comparable al radio degenera en una mancha con forma de lente.
    """
    if sweep <= 0:
        return
    cx, cy = center
    steps = max(int(abs(sweep) / 4) + 2, 3)
    angles = [math.radians(start + sweep * i / (steps - 1)) for i in range(steps)]
    points = [(cx + r_out * math.cos(a), cy + r_out * math.sin(a)) for a in angles]
    points += [(cx + r_in * math.cos(a), cy + r_in * math.sin(a)) for a in reversed(angles)]
    polygon = np.array(points, dtype=np.int32)

    x, y, w, h = cv2.boundingRect(polygon)
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, frame.shape[1]), min(y + h, frame.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    roi = frame[y0:y1, x0:x1]
    layer = roi.copy()
    cv2.fillPoly(layer, [polygon - (x0, y0)], color, cv2.LINE_AA)
    cv2.addWeighted(layer, alpha, roi, 1 - alpha, 0, dst=roi)


def _ticks(frame: np.ndarray, center: tuple[int, int], r0: int, r1: int,
           count: int, color, start: float = 0.0, span: float = 360.0,
           thickness: int = 1) -> None:
    cx, cy = center
    for i in range(count):
        a = math.radians(start + span * i / max(count - 1, 1))
        ca, sa = math.cos(a), math.sin(a)
        cv2.line(frame, (int(cx + r0 * ca), int(cy + r0 * sa)),
                 (int(cx + r1 * ca), int(cy + r1 * sa)), color, thickness, cv2.LINE_AA)


def draw_hand(frame: np.ndarray, hand, color=COL_PRIMARY) -> None:
    """Dibuja el esqueleto de la mano detectada."""
    if not hand.present:
        return
    h, w = frame.shape[:2]
    pts = lmk.normalized_to_pixels(hand.landmarks, w, h)
    for a, b in lmk.CONNECTIONS:
        cv2.line(frame, tuple(pts[a]), tuple(pts[b]), color, 2, cv2.LINE_AA)
    for i, (px, py) in enumerate(pts):
        if i in (lmk.THUMB_TIP, lmk.INDEX_TIP):
            cv2.circle(frame, (int(px), int(py)), 7, COL_ACCENT, 2, cv2.LINE_AA)
        else:
            cv2.circle(frame, (int(px), int(py)), 3, color, -1, cv2.LINE_AA)
    # Une pulgar e índice: es la medida que gobierna la pinza y el deslizador.
    cv2.line(frame, tuple(pts[lmk.THUMB_TIP]), tuple(pts[lmk.INDEX_TIP]),
             COL_ACCENT, 1, cv2.LINE_AA)


def draw_pose(canvas: np.ndarray, pose: np.ndarray, x: int, y: int, w: int, h: int,
              color=COL_PRIMARY) -> None:
    """Dibuja una pose canónica centrada en un recuadro, para los esquemas de la guía.

    El escalado es uniforme: estirar una mano para llenar la caja la vuelve
    irreconocible, que es precisamente lo que el esquema debe evitar.
    """
    mins = pose[:, :2].min(axis=0)
    span = np.maximum(pose[:, :2].max(axis=0) - mins, 1e-6)
    scale = float(min(w / span[0], h / span[1]))
    origin = (x + (w - span[0] * scale) / 2.0, y + (h - span[1] * scale) / 2.0)
    pts = ((pose[:, :2] - mins) * scale + origin).astype(np.int32)
    # La palma rellena da volumen al esquema; sin ella el esqueleto se lee como
    # un abanico de líneas y cuesta reconocer la mano.
    cv2.fillConvexPoly(canvas, pts[list(lmk.PALM_POINTS)], (52, 40, 20), cv2.LINE_AA)
    for a, b in lmk.CONNECTIONS:
        cv2.line(canvas, tuple(pts[a]), tuple(pts[b]), color, 2, cv2.LINE_AA)
    for px, py in pts:
        cv2.circle(canvas, (int(px), int(py)), 3, color, -1, cv2.LINE_AA)


def _wrap(fonts: _Fonts, text: str, size: int, max_width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        probe = f"{current} {word}".strip()
        if fonts.width(probe, size) <= max_width or not current:
            current = probe
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------- #

class Hud:
    """Dibuja toda la superposición de un fotograma."""

    def __init__(self, config) -> None:
        self.config = config
        self.fonts = _Fonts()
        self.text = _TextLayer(self.fonts)
        self.show_help = bool(config.hud.get("show_help_on_start", True))
        self.show_guide = False
        self.toast_duration = float(config.hud.get("toast_duration", 1.8))
        self.scanlines = bool(config.hud.get("scanlines", True))
        self._help_cache: dict[str, np.ndarray] = {}
        self._guide_cache: dict[tuple[str, int, int], np.ndarray] = {}
        self._scan: np.ndarray | None = None
        self._t = 0.0

    def toggle_help(self) -> None:
        self.show_help = not self.show_help

    def toggle_guide(self) -> None:
        self.show_guide = not self.show_guide

    # ------------------------------------------------------------------ #

    def render(self, frame: np.ndarray, hand, state: EngineState, *, mode: str,
               paused: bool, volume: float, muted: bool, toast: str, toast_age: float,
               fps: float, inference_ms: float, delegate: str, dry_run: bool = False,
               mouse_status: str = "", now: float = 0.0, wheel=None) -> np.ndarray:
        h, w = frame.shape[:2]
        self._t = now

        if self.scanlines:
            self._apply_scanlines(frame)

        if self.show_guide:
            self._blit_guide(frame, w, h, mode)
            self.text.add(w // 2 - self.text.width("G PARA VOLVER", 14, 3) // 2, h - 34,
                          "G PARA VOLVER", 14, COL_SOFT, tracking=3)
            return self.text.flush(frame)

        _brackets(frame, 10, 10, w - 20, h - 20, COL_DIM, size=34, thickness=1)
        draw_hand(frame, hand, COL_ALERT if paused else COL_PRIMARY)

        if paused:
            # En pausa se retira todo lo accesorio: si el sistema no va a
            # obedecer, mostrar la chuleta de gestos solo induce a error.
            self._draw_status(frame, mode, paused, state)
            self._draw_paused(frame, w, h)
            return self.text.flush(frame)

        if wheel is not None and wheel.active:
            self._draw_wheel(frame, w, h, wheel)
            self._draw_status(frame, mode, paused, state)
            self._draw_telemetry(frame, w, fps, inference_ms, delegate, mouse_status)
            return self.text.flush(frame)

        if mode == "mouse":
            self._draw_active_region(frame, w, h)
        if hand.present:
            self._draw_reticle(frame, w, h, hand, state)

        self._draw_status(frame, mode, paused, state)
        self._draw_telemetry(frame, w, fps, inference_ms, delegate, mouse_status)
        self._draw_reactor(frame, w, h, volume, muted)

        if dry_run:
            self._draw_banner(frame, w, "MODO PRUEBA · NO SE EJECUTA NINGUNA ACCIÓN", COL_ACCENT)

        # Avisos y deslizador van arriba al centro: la mitad inferior la ocupan
        # la chuleta y el indicador de volumen, y ahí se solaparían. Nunca
        # coinciden entre sí, porque el deslizador ya muestra su propio valor.
        top = 70 if dry_run else 28
        if state.slider_active:
            self._draw_slider(frame, w, top, state)
        elif toast and toast_age < self.toast_duration:
            self._draw_toast(frame, w, top, toast, toast_age)

        if self.show_help:
            self._blit_help(frame, w, h, mode)

        return self.text.flush(frame)

    # ------------------------------------------------------------------ #

    def _apply_scanlines(self, frame: np.ndarray) -> None:
        """Oscurece una de cada tres filas. Una resta de imagen completa cuesta
        menos que centenares de llamadas a ``cv2.line``."""
        if self._scan is None or self._scan.shape != frame.shape:
            self._scan = np.zeros_like(frame)
            self._scan[::3] = 16
        cv2.subtract(frame, self._scan, dst=frame)

    def _draw_active_region(self, frame, w, h) -> None:
        x0, y0, x1, y1 = self.config.mouse.get("active_region", [0.2, 0.15, 0.8, 0.75])
        _brackets(frame, x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h,
                  COL_SOFT, size=22, thickness=1)
        self.text.add(int(x0 * w), int(y0 * h) - 22, "ZONA ACTIVA", 13, COL_SOFT, tracking=2)

    def _draw_reticle(self, frame, w, h, hand, state: EngineState) -> None:
        """Retícula de seguimiento sobre la mano, con el arco de permanencia."""
        cx, cy = lmk.palm_center(hand.landmarks)
        center = (int(cx * w), int(cy * h))
        # El radio sigue al tamaño real de la mano en pantalla, pero acotado: sin
        # tope, una mano cerca de la cámara genera un aro que se sale del encuadre.
        px = hand.landmarks[:, 0] * w
        py = hand.landmarks[:, 1] * h
        extent = max(float(px.max() - px.min()), float(py.max() - py.min()))
        radius = int(min(max(extent * 0.62, 54), 132))

        spin = (self._t * 42) % 360
        color = COL_ACCENT if state.slider_active else COL_PRIMARY
        for i in range(4):
            _ring(frame, center, radius, spin + i * 90, 58, color, 2)
        _ring(frame, center, radius - 12, -spin * 0.6, 360, COL_DIM, 1)
        _ticks(frame, center, radius + 6, radius + 14, 4, color, start=spin + 45, span=270)

        # El arco de permanencia crece alrededor de la mano: se ve sin apartar
        # la vista de lo que se está haciendo.
        if state.hold_progress > 0:
            _ring(frame, center, radius + 20, -90, 360 * state.hold_progress, COL_ACCENT, 4)
            _ring(frame, center, radius + 20, -90, 360, COL_DIM, 1)

        if state.gesture != "None":
            label = poses.name_of(state.gesture)
            tone = COL_TEXT if state.stable else COL_DIM
            # El rótulo va encima de la retícula: debajo compite con la chuleta.
            label_y = max(center[1] - radius - 34, 150)
            self.text.add(center[0] - self.text.width(label, 17, 2) // 2,
                          label_y, label, 17, tone, tracking=2)
            if state.hold_label:
                self.text.add(
                    center[0] - self.text.width(state.hold_label, 14) // 2,
                    label_y + 22, state.hold_label, 14, COL_ACCENT)

    def _draw_wheel(self, frame, w, h, wheel) -> None:
        """Menú radial: sectores etiquetados y arco de permanencia del elegido."""
        # La imagen se atenúa para que las etiquetas destaquen sobre el vídeo.
        overlay = np.empty_like(frame)
        overlay[:] = COL_BG
        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, dst=frame)

        cx, cy = int(wheel.center[0] * w), int(wheel.center[1] * h)
        unit = max(wheel.scale * h, 26.0)
        r_in = int(unit * wheel.inner)
        r_out = int(unit * wheel.outer)
        r_mid = (r_in + r_out) // 2
        count = len(wheel.items)
        step = 360.0 / count

        _ring(frame, (cx, cy), r_in, 0, 360, COL_DIM, 1)
        _ring(frame, (cx, cy), r_out, 0, 360, COL_DIM, 1)
        # Separadores entre sectores, desplazados medio paso.
        for i in range(count):
            a = math.radians(wheel.angle_of(i) - step / 2.0)
            cv2.line(frame, (int(cx + r_in * math.cos(a)), int(cy + r_in * math.sin(a))),
                     (int(cx + r_out * math.cos(a)), int(cy + r_out * math.sin(a))),
                     COL_DIM, 1, cv2.LINE_AA)

        if wheel.pointer is not None:
            px, py = int(wheel.pointer[0] * w), int(wheel.pointer[1] * h)
            cv2.line(frame, (cx, cy), (px, py), COL_SOFT, 1, cv2.LINE_AA)
            cv2.circle(frame, (px, py), 8, COL_ACCENT, 2, cv2.LINE_AA)

        for i, item in enumerate(wheel.items):
            chosen = (i == wheel.selected)
            angle = wheel.angle_of(i)
            rad = math.radians(angle)
            if chosen:
                _fill_sector(frame, (cx, cy), r_in, r_out, angle - step / 2.0, step,
                             COL_ACCENT, alpha=0.5)
                # El progreso de confirmación crece por el borde del sector elegido.
                _ring(frame, (cx, cy), r_out + 9, angle - step / 2.0,
                      step * wheel.dwell_progress, COL_TEXT, 5)

            tx = cx + int(r_mid * math.cos(rad))
            ty = cy + int(r_mid * math.sin(rad))
            label = item.label.upper()
            size = 15
            tw = self.text.width(label, size, 1)
            # La etiqueta se centra en su sector y se recorta al encuadre para
            # que las opciones laterales no se salgan de la pantalla.
            lx = min(max(tx - tw // 2, 12), w - tw - 12)
            ly = min(max(ty - size // 2 - 2, 12), h - 30)
            _tint(frame, lx - 8, ly - 5, tw + 16, size + 12, 0.72 if chosen else 0.55)
            self.text.add(lx, ly, label, size,
                          COL_ACCENT if chosen else COL_TEXT, tracking=1)

        # La zona muerta central se marca con una cruz: es donde no pasa nada,
        # y sirve para dudar sin ejecutar.
        cv2.line(frame, (cx - 9, cy), (cx + 9, cy), COL_DIM, 1, cv2.LINE_AA)
        cv2.line(frame, (cx, cy - 9), (cx, cy + 9), COL_DIM, 1, cv2.LINE_AA)

        title = "RUEDA DE COMANDOS"
        self.text.add((w - self.text.width(title, 17, 5)) // 2, 26, title, 17,
                      COL_PRIMARY, tracking=5)
        hint = "Apunta con el dedo · mantenlo para confirmar · cierra la mano para cancelar"
        self.text.add((w - self.text.width(hint, 14)) // 2, h - 44, hint, 14, COL_SOFT)

    def _draw_status(self, frame, mode, paused, state: EngineState) -> None:
        x, y, w, h = 26, 26, 300, 118
        _panel(frame, x, y, w, h)

        if paused:
            title, tone = "EN PAUSA", COL_ALERT
        else:
            title, tone = f"MODO {poses.mode_name(mode)}", COL_PRIMARY
        self.text.add(x + 18, y + 14, title, 25, tone, tracking=3)

        gesture = poses.name_of(state.gesture) if state.gesture != "None" else "SIN GESTO"
        self.text.add(x + 18, y + 50, gesture, 16, COL_TEXT if state.stable else COL_DIM,
                      tracking=1)

        # Dos barras: confianza del clasificador y cuánto se mueve la mano.
        # La segunda explica por qué un gesto por permanencia no arranca.
        gate = float(self.config.engine.get("motion_gate", 2.5))
        for i, (name, ratio, tint) in enumerate((
            ("REC", state.score, COL_PRIMARY),
            ("MOV", min(state.speed / max(gate, 1e-6), 1.0), COL_ACCENT),
        )):
            by = y + 78 + i * 18
            self.text.add(x + 18, by - 4, name, 11, COL_DIM, tracking=1)
            cv2.rectangle(frame, (x + 52, by + 2), (x + w - 20, by + 8), COL_BG, -1)
            filled = int((w - 72) * min(max(ratio, 0.0), 1.0))
            shade = COL_ALERT if (name == "MOV" and ratio >= 1.0) else tint
            if filled > 0:
                cv2.rectangle(frame, (x + 52, by + 2), (x + 52 + filled, by + 8), shade, -1)
            cv2.rectangle(frame, (x + 52, by + 2), (x + w - 20, by + 8), COL_DIM, 1)

    def _draw_telemetry(self, frame, w, fps, inference_ms, delegate, mouse_status) -> None:
        rows = [("FPS", f"{fps:.0f}"), ("INFERENCIA", f"{inference_ms:.0f} ms"),
                ("CÓMPUTO", delegate.upper())]
        if mouse_status:
            rows.append(("RATÓN", mouse_status.upper()))
        x, y = w - 210, 26
        _panel(frame, x, y, 184, 24 + 20 * len(rows), alpha=0.55)
        for i, (key, value) in enumerate(rows):
            row_y = y + 14 + i * 20
            self.text.add(x + 16, row_y, key, 11, COL_DIM, tracking=1)
            self.text.right(x + 168, row_y - 2, value, 14, COL_SOFT)

    def _draw_reactor(self, frame, w, h, volume, muted) -> None:
        """Indicador de volumen circular, a modo de reactor."""
        if volume < 0:
            return
        center = (w - 104, h - 126)
        tone = COL_ALERT if muted else COL_ACCENT

        _ring(frame, center, 58, 0, 360, COL_DIM, 1)
        _ticks(frame, center, 62, 70, 12, COL_DIM, start=0, span=360)
        # Escala tipo aguja: 270° útiles, con la apertura abajo.
        _ring(frame, center, 46, 135, 270, COL_BG, 7)
        _ring(frame, center, 46, 135, 270 * min(max(volume, 0.0), 1.0), tone, 7)
        _ring(frame, center, 30, -self._t * 30 % 360, 90, COL_SOFT, 1)
        _ring(frame, center, 30, -self._t * 30 % 360 + 180, 90, COL_SOFT, 1)

        value = "MUTE" if muted else f"{round(volume * 100)}"
        size = 20 if muted else 30
        self.text.add(center[0] - self.text.width(value, size) // 2,
                      center[1] - size // 2 - 6, value, size, tone)
        self.text.add(center[0] - self.text.width("VOLUMEN", 11, 2) // 2,
                      center[1] + 74, "VOLUMEN", 11, COL_DIM, tracking=2)

    def _draw_slider(self, frame, w, top, state: EngineState) -> None:
        """Barra segmentada del control analógico."""
        segments, seg_w, gap = 28, 12, 4
        total = segments * (seg_w + gap) - gap
        x, y = (w - total) // 2, top + 40
        _tint(frame, x - 24, top, total + 48, 76, 0.6)
        _brackets(frame, x - 24, top, total + 48, 76, COL_ACCENT, size=12, thickness=1)
        self.text.add(x, top + 8, state.slider_label.upper(), 15, COL_ACCENT, tracking=3)
        self.text.right(x + total, top + 6, f"{round(state.slider_value * 100)}%", 20, COL_TEXT)
        active = int(round(segments * state.slider_value))
        for i in range(segments):
            sx = x + i * (seg_w + gap)
            on = i < active
            cv2.rectangle(frame, (sx, y), (sx + seg_w, y + 18),
                          COL_ACCENT if on else COL_BG, -1)
            if not on:
                cv2.rectangle(frame, (sx, y), (sx + seg_w, y + 18), COL_DIM, 1)

    def _draw_toast(self, frame, w, top, toast, age) -> None:
        # Se desvanece al final para no tapar la imagen más de lo necesario.
        fade = min(1.0, (self.toast_duration - age) / 0.45)
        label = toast.upper()
        tw = self.text.width(label, 24, 2)
        x, y = (w - tw) // 2 - 34, top
        _tint(frame, x, y, tw + 68, 52, 0.7 * fade)
        _brackets(frame, x, y, tw + 68, 52, COL_ACCENT, size=12, thickness=1)
        tone = tuple(int(c * fade + COL_BG[i] * (1 - fade)) for i, c in enumerate(COL_ACCENT))
        self.text.add(x + 34, y + 12, label, 24, tone, tracking=2)

    def _draw_banner(self, frame, w, message, tone) -> None:
        tw = self.text.width(message, 15, 3)
        x, y = (w - tw) // 2 - 24, 22
        _tint(frame, x, y, tw + 48, 32, 0.72)
        cv2.rectangle(frame, (x, y), (x + tw + 48, y + 32), tone, 1)
        self.text.add(x + 24, y + 7, message, 15, tone, tracking=3)

    def _draw_paused(self, frame, w, h) -> None:
        """En pausa la imagen se apaga: el estado tiene que ser inequívoco."""
        overlay = np.empty_like(frame)
        overlay[:] = COL_BG
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, dst=frame)
        pulse = 0.5 + 0.5 * math.sin(self._t * 3.0)
        tone = tuple(int(COL_ALERT[i] * (0.55 + 0.45 * pulse)) for i in range(3))
        cv2.rectangle(frame, (16, 16), (w - 16, h - 16), tone, 2)

        title = "CONTROL EN PAUSA"
        self.text.add((w - self.text.width(title, 46, 6)) // 2, h // 2 - 60, title, 46,
                      COL_ALERT, tracking=6)
        hint = "PALMA ABIERTA 1,5 s PARA REANUDAR"
        self.text.add((w - self.text.width(hint, 18, 3)) // 2, h // 2 + 6, hint, 18,
                      COL_TEXT, tracking=3)
        note = "No se ejecuta ninguna acción mientras el control está en pausa"
        self.text.add((w - self.text.width(note, 14)) // 2, h // 2 + 40, note, 14, COL_SOFT)

    # ------------------------------------------------------------------ #
    # Paneles estáticos, rasterizados una vez
    # ------------------------------------------------------------------ #

    def _render_help(self, mode: str) -> np.ndarray | None:
        rows = self.config.describe_bindings(mode) + self.config.describe_bindings("global")
        if not rows:
            return None

        line_h, size, header, pad, gap = 20, 13, 50, 16, 26

        # Con muchos gestos, una sola columna ocuparía la pantalla entera y
        # taparía justo la zona donde se mueve la mano.
        columns = 2 if len(rows) > 10 else 1
        per_column = -(-len(rows) // columns)
        chunks = [rows[i * per_column:(i + 1) * per_column] for i in range(columns)]

        col_widths = [
            max(self.fonts.width(g, size, 1) + self.fonts.width(d, size) + 22
                for g, d in chunk)
            for chunk in chunks if chunk
        ]
        pw = max(330, sum(col_widths) + gap * (len(col_widths) - 1) + pad * 2)
        ph = header + line_h * per_column + 10

        # Se construye en orden de canales BGR: Pillow escribe los bytes tal cual.
        image = Image.new("RGB", (pw, ph), COL_BG)
        draw = ImageDraw.Draw(image)
        _draw_text(draw, (pad, 12), f"GESTOS · {poses.mode_name(mode)}", self.fonts.get(15),
                   COL_PRIMARY, tracking=3)
        _draw_text(draw, (pad, 31), "G guía visual · H ocultar · Q salir",
                   self.fonts.get(12), COL_DIM)

        x = pad
        for chunk, width in zip(chunks, col_widths):
            for i, (gesture, description) in enumerate(chunk):
                y = header + i * line_h
                _draw_text(draw, (x, y), gesture, self.fonts.get(size), COL_SOFT, tracking=1)
                _draw_text(draw, (x + width - self.fonts.width(description, size), y),
                           description, self.fonts.get(size), COL_TEXT)
            x += width + gap
        return np.asarray(image)

    def _blit_help(self, frame: np.ndarray, w: int, h: int, mode: str) -> None:
        if mode not in self._help_cache:
            rendered = self._render_help(mode)
            if rendered is None:
                return
            self._help_cache[mode] = rendered
        panel = self._help_cache[mode]
        ph, pw = panel.shape[:2]
        x, y = 26, h - ph - 26
        if y < 0 or x + pw > w:
            return
        roi = frame[y:y + ph, x:x + pw]
        cv2.addWeighted(panel, 0.74, roi, 0.26, 0, dst=roi)
        _brackets(frame, x, y, pw, ph, COL_PRIMARY, size=14, thickness=1)

    def _render_guide(self, mode: str, w: int, h: int) -> np.ndarray:
        """Guía a pantalla completa: cada gesto dibujado junto a lo que hace.

        Es la respuesta a «no sé cómo se hace este gesto»: el esquema se genera
        con el mismo código que dibuja la mano real, así que lo que se ve en la
        guía es exactamente lo que el sistema espera reconocer.
        """
        canvas = np.empty((h, w, 3), np.uint8)
        canvas[:] = COL_BG

        entries = list(poses.GESTURES.values()) + [poses.SLIDER]
        actions = self._actions_by_gesture(mode)

        cols, rows = 4, 2
        margin_x, top = 40, 104
        cell_w = (w - margin_x * 2) // cols
        cell_h = (h - top - 40) // rows

        _draw_text(ImageDraw.Draw(Image.new("RGB", (1, 1))), (0, 0), "", self.fonts.get(10),
                   COL_TEXT)  # calienta la caché de fuentes antes del bucle
        image = Image.fromarray(canvas)
        draw = ImageDraw.Draw(image)
        _draw_text(draw, (margin_x, 34), "GUÍA DE GESTOS", self.fonts.get(30),
                   COL_PRIMARY, tracking=6)
        _draw_text(draw, (margin_x, 74), f"Acciones del modo {poses.mode_name(mode).lower()}", self.fonts.get(14),
                   COL_SOFT)
        canvas = np.asarray(image).copy()

        text_items: list[tuple[int, int, str, int, tuple, int]] = []
        for i, entry in enumerate(entries):
            cx = margin_x + (i % cols) * cell_w
            cy = top + (i // cols) * cell_h
            _brackets(canvas, cx + 6, cy, cell_w - 24, cell_h - 20, COL_DIM,
                      size=12, thickness=1)
            draw_pose(canvas, entry.pose, cx + 16, cy + 12, 96, 128, COL_PRIMARY)

            tx = cx + 124
            tw = cell_w - 152
            text_items.append((tx, cy + 16, entry.name, 17, COL_ACCENT, 2))
            for j, line in enumerate(_wrap(self.fonts, entry.how, 12, tw)[:3]):
                text_items.append((tx, cy + 40 + j * 15, line, 12, COL_SOFT, 0))

            # Las acciones arrancan por debajo del esquema, no a su altura.
            bound = actions.get(entry.key, [])
            for j, line in enumerate(bound[:4]):
                text_items.append((cx + 18, cy + 152 + j * 17, line, 13, COL_TEXT, 0))
            if not bound:
                text_items.append((cx + 18, cy + 152, "— sin acción en este modo —",
                                   12, COL_DIM, 0))

        image = Image.fromarray(canvas)
        draw = ImageDraw.Draw(image)
        for x, y, text, size, color, tracking in text_items:
            _draw_text(draw, (x, y), text, self.fonts.get(size), color, tracking)
        return np.asarray(image).copy()

    def _actions_by_gesture(self, mode: str) -> dict[str, list[str]]:
        """Agrupa por gesto las acciones disponibles, para el pie de cada esquema."""
        grouped: dict[str, list[str]] = {}
        for group in (mode, "global"):
            for b in self.config.bindings.get(group, []):
                if b.trigger == "swipe":
                    prefix = poses.ARROWS.get(b.direction, "•")
                elif b.trigger == "hold":
                    prefix = f"{b.duration:g}s".replace(".", ",")
                elif b.trigger == "repeat":
                    # Nada de flechas circulares: Segoe UI no trae ese glifo y
                    # se dibujaría como un rectángulo vacío.
                    prefix = "mant."
                else:
                    prefix = "•"
                grouped.setdefault(b.gesture, []).append(f"{prefix}  {b.label or b.action}")
        analog = self.config.analog.get(mode)
        if analog:
            grouped.setdefault("Slider", []).append(f"◄►  {analog.get('label', 'Analógico')}")
        return grouped

    def _blit_guide(self, frame: np.ndarray, w: int, h: int, mode: str) -> None:
        key = (mode, w, h)
        if key not in self._guide_cache:
            self._guide_cache[key] = self._render_guide(mode, w, h)
        np.copyto(frame, self._guide_cache[key])
