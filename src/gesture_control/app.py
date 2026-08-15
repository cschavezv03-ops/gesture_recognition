"""Bucle principal: une cámara, reconocedor, motor de gestos y acciones."""

from __future__ import annotations

import logging
import time

import cv2

from .actions import ActionRouter
from .camera import CameraStream
from .config import Config
from .engine import GestureEngine
from .hud import Hud
from .mouse import MouseController
from .pinchdrag import PinchDrag
from .recognizer import GestureStream, HandResult
from .switcher import WindowSwitcher
from .wheel import CommandWheel
from .win import windows as win

log = logging.getLogger(__name__)

WINDOW = "Control por gestos"

#: Acciones que siguen ejecutándose en modo de prueba: solo tocan la propia app.
SAFE_ACTIONS = {"set_mode", "toggle_pause", "quit", "open_wheel"}


class App:
    def __init__(self, config: Config, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        self.mode = config.raw.get("engine", {}).get("start_mode", "control")
        self.paused = bool(config.engine.get("start_paused", False))
        self.running = True

        self.engine = GestureEngine(config.engine, config.bindings, config.analog)
        self.mouse = MouseController(config.mouse)
        self.pinch = PinchDrag(config.pinch_drag)
        self.wheel = CommandWheel(config.wheel, config.wheel_items)
        self.switcher = WindowSwitcher(config.switcher)
        self.router = ActionRouter(self, config.apps)
        self.hud = Hud(config)

        self._hand = HandResult()
        self._aspect = 16.0 / 9.0

        self._fps = 0.0
        self._last_frame_at = time.monotonic()

    # -------------------------------------------------------------- #
    # Acciones sobre la propia aplicación
    # -------------------------------------------------------------- #

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        if self.mode == "mouse":
            self.mouse.reset()
        self.wheel.close()
        self.switcher.close()
        self.pinch.reset()
        self.mode = mode
        self.engine.reset()
        log.info("Modo: %s", mode)

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        if self.paused:
            self.mouse.reset()
        self.wheel.close()
        self.switcher.close()
        self.pinch.reset()
        self.engine.reset()
        log.info("Pausa: %s", self.paused)
        return self.paused

    def open_wheel(self) -> bool:
        """Despliega la rueda de comandos alrededor de la mano detectada."""
        if self.wheel.open(self._hand, self.mode, self._aspect):
            # Soltar el botón antes de abrir el menú: quedaría pulsado mientras
            # se navega por la rueda y se arrastraría lo que hubiera debajo.
            self.mouse.reset()
            return True
        return False

    def open_switcher(self) -> bool:
        """Despliega la rejilla de ventanas abiertas."""
        if self.switcher.open():
            self.mouse.reset()
            return True
        return False

    def _apply_switcher(self, result) -> None:
        """Ejecuta lo que decidió el conmutador al soltar la mano."""
        if result.action == "cancel" or result.window is None:
            return
        if self.dry_run:
            self.router.notify(f"[prueba] {result.message}")
            return
        if result.action == "snap":
            win.snap(result.window.handle, result.edge)
        win.activate(result.window.handle)
        self.router.notify(result.message)

    def stop(self) -> None:
        self.running = False

    # -------------------------------------------------------------- #

    def _dispatch(self, events) -> None:
        if not self.dry_run:
            self.router.dispatch(events)
            return
        safe = [e for e in events if e.binding.action in SAFE_ACTIONS]
        for event in events:
            if event.binding.action not in SAFE_ACTIONS:
                label = event.binding.label or event.binding.action
                self.router.notify(f"[prueba] {label}")
                log.info("[prueba] %s(%s)", event.binding.action, event.binding.args)
        self.router.dispatch(safe)

    def run(self) -> int:
        cam_cfg = self.config.camera
        rec_cfg = self.config.recognizer

        with CameraStream(
            index=cam_cfg.get("index", 0),
            backend=cam_cfg.get("backend", "dshow"),
            width=cam_cfg.get("width", 1280),
            height=cam_cfg.get("height", 720),
            fps=cam_cfg.get("fps", 30),
            mirror=cam_cfg.get("mirror", True),
        ) as camera, GestureStream(
            model_path=self.config.model_path,
            delegate=rec_cfg.get("delegate", "cpu"),
            num_hands=rec_cfg.get("num_hands", 1),
            min_hand_detection_confidence=rec_cfg.get("min_hand_detection_confidence", 0.5),
            min_hand_presence_confidence=rec_cfg.get("min_hand_presence_confidence", 0.5),
            min_tracking_confidence=rec_cfg.get("min_tracking_confidence", 0.5),
        ) as recognizer:

            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(WINDOW, cam_cfg.get("width", 1280), cam_cfg.get("height", 720))
            if self.dry_run:
                self.router.notify("Modo de prueba: no se ejecutan acciones")

            last_seq = -1
            start = time.monotonic()

            while self.running:
                if not camera.alive:
                    log.error("Se perdió la conexión con la cámara.")
                    break

                seq, frame = camera.read()
                # Todo el trabajo se hace una sola vez por fotograma nuevo. El
                # bucle gira mucho más rápido que la cámara, y sin esta puerta se
                # reprocesaría y redibujaría la misma imagen decenas de veces.
                if frame is None or seq == last_seq:
                    if not self._handle_keys():
                        break
                    continue

                last_seq = seq
                now = time.monotonic()
                # MediaPipe exige marcas de tiempo estrictamente crecientes.
                recognizer.submit(frame, int((now - start) * 1000))

                hand, inference_ms = recognizer.latest()
                self._hand = hand
                self._aspect = frame.shape[1] / max(frame.shape[0], 1)
                self.mouse.set_frame_aspect(self._aspect)

                # Conmutador y rueda son modales: mientras están desplegados la
                # mano está dedicada a elegir, y dejar el motor activo
                # dispararía acciones sueltas al apuntar.
                if self.switcher.active:
                    result = self.switcher.update(hand, now)
                    if result:
                        self._apply_switcher(result)
                    self.engine.reset()
                    self.pinch.reset()
                elif self.wheel.active:
                    chosen = self.wheel.update(hand, now)
                    if chosen:
                        self._dispatch([chosen])
                    self.engine.reset()
                    self.pinch.reset()
                else:
                    if not self.paused:
                        dragged = self.pinch.update(hand, self.mode, now)
                        if dragged:
                            self._dispatch(dragged)
                    if self.pinch.engaged:
                        # La pinza tiene la mano tomada: interpretar además su
                        # pose entraría en modo cursor a mitad de un arrastre.
                        self.engine.reset()
                    else:
                        events = self.engine.update(hand, self.mode, self.paused, now)
                        if events:
                            self._dispatch(events)
                    if self.mode == "mouse" and not self.paused and not self.dry_run:
                        self.mouse.update(hand, frozen=self.pinch.engaged)

                self._fps = 0.9 * self._fps + 0.1 / max(now - self._last_frame_at, 1e-6)
                self._last_frame_at = now

                canvas = self.hud.render(
                    frame.copy(), hand, self.engine.state,
                    mode=self.mode, paused=self.paused,
                    volume=self.router.volume.get(), muted=self.router.volume.is_muted(),
                    toast=self.router.toast, toast_age=now - self.router.toast_at,
                    fps=self._fps, inference_ms=inference_ms,
                    delegate=recognizer.delegate, dry_run=self.dry_run, now=now,
                    wheel=self.wheel, switcher=self.switcher, pinch=self.pinch,
                    mouse=self.mouse,
                    mouse_status=self.mouse.status if self.mode == "mouse" else "",
                )
                cv2.imshow(WINDOW, canvas)

                if not self._handle_keys():
                    break
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    break

            self.mouse.reset()
            cv2.destroyAllWindows()
        return 0

    def _handle_keys(self) -> bool:
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            return False
        if key == ord("c"):
            self.wheel.close()
        if key == ord("h"):
            self.hud.toggle_help()
        elif key == ord("g"):
            self.hud.toggle_guide()
        elif key == ord("p"):
            self.router.notify("En pausa" if self.toggle_pause() else "Activo")
        elif key == ord("m"):
            target = "control" if self.mode == "mouse" else "mouse"
            self.set_mode(target)
            self.router.notify(f"Modo {target}")
        return True
