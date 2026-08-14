"""Control del volumen maestro de Windows a través de la API Core Audio (pycaw).

Se prefiere Core Audio sobre las teclas multimedia porque permite fijar un valor
absoluto —necesario para el control analógico con la pinza— y leer el estado
actual para dibujarlo en el HUD.

La interfaz ``IAudioEndpointVolume`` se obtiene de un dispositivo concreto, no
del concepto «salida predeterminada». Al conectar unos auriculares Windows
cambia el dispositivo por defecto, pero la interfaz ya obtenida sigue apuntando
al anterior: los comandos se aplican a unos altavoces que ya nadie escucha y
parece que el volumen ha dejado de funcionar. Por eso se comprueba
periódicamente si el dispositivo ha cambiado y se vuelve a enganchar.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

#: Cada cuánto se comprueba si Windows ha cambiado de dispositivo de salida.
_RECHECK_SECONDS = 1.5


class VolumeController:
    """Envoltorio sobre ``IAudioEndpointVolume`` del dispositivo de salida activo.

    Si Core Audio no está disponible, degrada a teclas multimedia para que la
    aplicación siga siendo utilizable en lugar de fallar al arrancar.
    """

    def __init__(self) -> None:
        self._endpoint = None
        self._enumerator = None
        self._device_id: str | None = None
        self._checked_at = 0.0
        self.device_name = "teclas multimedia"
        self._acquire()

    # ------------------------------------------------------------------ #
    # Enganche con el dispositivo
    # ------------------------------------------------------------------ #

    @staticmethod
    def _default_device():
        import comtypes
        from pycaw.pycaw import AudioUtilities

        comtypes.CoInitialize()
        return AudioUtilities.GetSpeakers()

    def _default_device_id(self) -> str | None:
        """Identificador del dispositivo de salida actual.

        Consultar solo el identificador cuesta ~1 ms; construir el dispositivo
        completo cuesta ~13 ms, suficiente para provocar un tirón visible en el
        vídeo. Como la comprobación es periódica y el cambio de dispositivo raro,
        se paga lo barato siempre y lo caro solo cuando de verdad ha cambiado.
        """
        import comtypes
        from pycaw.pycaw import AudioUtilities

        if self._enumerator is None:
            comtypes.CoInitialize()
            self._enumerator = AudioUtilities.GetDeviceEnumerator()
        # eRender = 0 (salida), eMultimedia = 1 (rol multimedia).
        return self._enumerator.GetDefaultAudioEndpoint(0, 1).GetId()

    def _acquire(self) -> bool:
        """Obtiene la interfaz de volumen del dispositivo predeterminado actual."""
        try:
            speakers = self._default_device()
            # pycaw >= 2025 expone la interfaz ya activada en ``EndpointVolume``;
            # las versiones anteriores devuelven el IMMDevice crudo.
            endpoint = getattr(speakers, "EndpointVolume", None)
            if endpoint is None:
                from ctypes import POINTER, cast

                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume

                iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                endpoint = cast(iface, POINTER(IAudioEndpointVolume))

            self._endpoint = endpoint
            self._device_id = getattr(speakers, "id", None)
            self.device_name = getattr(speakers, "FriendlyName", "salida predeterminada")
            self._checked_at = time.monotonic()
            return True
        except Exception as exc:  # pragma: no cover - depende del equipo
            log.warning("Core Audio no disponible (%s); se usarán teclas multimedia", exc)
            self._endpoint = None
            self._checked_at = time.monotonic()
            return False

    def _ensure(self) -> None:
        """Reengancha la interfaz si el dispositivo predeterminado ha cambiado.

        La comprobación va limitada en frecuencia porque el HUD lee el volumen en
        cada fotograma y enumerar dispositivos de audio no es gratis.
        """
        now = time.monotonic()
        if now - self._checked_at < _RECHECK_SECONDS:
            return
        self._checked_at = now
        try:
            if self._default_device_id() == self._device_id and self._endpoint is not None:
                return
        except Exception:
            self._enumerator = None
        previous = self.device_name
        if self._acquire() and self.device_name != previous:
            log.info("Salida de audio cambiada: %s → %s", previous, self.device_name)

    def _retry(self, operation):
        """Ejecuta una operación COM y, si falla, reengancha y lo intenta una vez más."""
        try:
            return operation(self._endpoint)
        except Exception as exc:
            log.debug("Fallo en Core Audio (%s); reenganchando dispositivo", exc)
            if self._acquire() and self._endpoint is not None:
                try:
                    return operation(self._endpoint)
                except Exception:
                    log.warning("El dispositivo de audio no responde")
            return None

    # ------------------------------------------------------------------ #
    # Interfaz pública
    # ------------------------------------------------------------------ #

    @property
    def available(self) -> bool:
        return self._endpoint is not None

    def get(self) -> float:
        """Volumen actual en el rango 0.0–1.0 (``-1.0`` si no se puede leer)."""
        self._ensure()
        if self._endpoint is None:
            return -1.0
        value = self._retry(lambda e: e.GetMasterVolumeLevelScalar())
        return -1.0 if value is None else float(value)

    def set(self, value: float) -> float:
        value = max(0.0, min(1.0, value))
        self._ensure()
        if self._endpoint is None:
            return -1.0
        done = self._retry(lambda e: e.SetMasterVolumeLevelScalar(value, None) or True)
        return value if done else -1.0

    def step(self, delta_percent: float) -> float:
        """Suma ``delta_percent`` puntos porcentuales al volumen actual."""
        current = self.get()
        if current < 0:
            from . import input as win_input

            win_input.press("volumeup" if delta_percent > 0 else "volumedown")
            return -1.0
        return self.set(current + delta_percent / 100.0)

    def is_muted(self) -> bool:
        self._ensure()
        if self._endpoint is None:
            return False
        return bool(self._retry(lambda e: e.GetMute()))

    def toggle_mute(self) -> bool:
        self._ensure()
        if self._endpoint is None:
            from . import input as win_input

            win_input.press("volumemute")
            return False
        muted = not self.is_muted()
        self._retry(lambda e: e.SetMute(muted, None) or True)
        return muted
