# Control de Windows por gestos

Reconocimiento de gestos de la mano con **MediaPipe Tasks** y **OpenCV** para
manejar Windows 11 sin tocar el teclado: volumen, multimedia, pestañas,
escritorios virtuales, lanzador de aplicaciones y un modo ratón completo.

La cámara es un móvil a través de **Iriun Webcam**.

---

## Por qué esto no corre dentro de WSL

El código se edita desde WSL pero **se ejecuta con el Python nativo de Windows**.
No es una preferencia, son tres bloqueos:

1. **La cámara.** WSL2 no expone dispositivos de vídeo del anfitrión. Iriun ni
   siquiera es un dispositivo USB que se pudiera adjuntar con `usbipd-win`: es un
   dispositivo DirectShow virtual que recibe el vídeo del móvil por red. Desde
   Linux no existe.
2. **El control del sistema.** Subir el volumen o cambiar de escritorio virtual
   exige hablar con `user32.dll` y con Core Audio. Desde WSL habría que delegar
   en `powershell.exe` por cada acción, con 100–200 ms de sobrecoste y sin
   control fino sobre las teclas.
3. **La GPU.** Aunque el equipo tenga una RTX 5060, los *wheels* de MediaPipe
   para Windows se compilan **sin soporte de GPU**; pedir `delegate: gpu` falla
   con `GPU processing is disabled in build flags` y el programa cae a CPU. No
   es una pérdida: el modelo pesa 8 MB y la inferencia tarda **≈6 ms** con
   XNNPACK, muy por debajo de los 33 ms que dura un fotograma a 30 FPS. El
   cuello de botella real es la latencia de red de Iriun, no el cómputo.

La opción `delegate: gpu` sigue existiendo en la configuración: intenta GPU,
avisa por consola y continúa en CPU si no está disponible.

---

## Dónde está el proyecto visto desde Windows

Si el repositorio vive en el sistema de archivos de WSL, Windows lo ve a través
de un recurso de red. Esa es la ruta que hay que usar en todos los comandos:

```
\\wsl.localhost\Ubuntu\home\<usuario>\gesture_recognition
```

PowerShell puede situarse ahí con `Set-Location` y a partir de entonces las
rutas relativas funcionan con normalidad. `cmd.exe` **no** puede: si prefieres
cmd, usa `pushd` para que le asigne una letra de unidad.

## Instalación

En **PowerShell de Windows** (no en WSL). Los scripts se localizan solos, así
que da igual desde dónde los lances mientras les pases su ruta completa:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\<usuario>\gesture_recognition\scripts\setup.ps1
```

Eso crea el entorno virtual en `C:\venvs\gesture`, instala las dependencias y
descarga el modelo. Manualmente sería:

```powershell
Set-Location \\wsl.localhost\Ubuntu\home\<usuario>\gesture_recognition
py -3 -m venv C:\venvs\gesture
C:\venvs\gesture\Scripts\python.exe -m pip install -r requirements.txt
C:\venvs\gesture\Scripts\python.exe scripts\download_model.py
```

Requiere Python 3.10–3.14 en Windows.

## Uso

Abre **Iriun Webcam** en el PC y en el móvil, y luego:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\<usuario>\gesture_recognition\scripts\run.ps1
```

O invocando Python con la ruta completa del lanzador:

```powershell
C:\venvs\gesture\Scripts\python.exe \\wsl.localhost\Ubuntu\home\<usuario>\gesture_recognition\run.py
```

Si prefieres comandos cortos, sitúate antes en el proyecto y usa rutas
relativas:

```powershell
Set-Location \\wsl.localhost\Ubuntu\home\<usuario>\gesture_recognition
C:\venvs\gesture\Scripts\python.exe run.py
```

Un error como `can't open file '...\Scripts\run.py': [Errno 2] No such file or
directory` significa exactamente esto: Python resolvió `run.py` contra el
directorio actual, que no era el del proyecto.

Existe además un modo prueba que reconoce los gestos y los muestra en pantalla
pero **no ejecuta ninguna acción**, a propósito. Sirve para practicar las poses
sin cambiar el volumen ni las pestañas. Se anuncia con un rótulo permanente en
la parte superior, porque si no es fácil confundirlo con un programa averiado:

```powershell
C:\venvs\gesture\Scripts\python.exe run.py --dry-run
```

El archivo `config.yaml` y el modelo se localizan siempre a partir de la
ubicación del código, no del directorio actual, así que una vez que el
intérprete encuentra `run.py` todo lo demás funciona desde cualquier carpeta.

Opciones disponibles:

| Opción | Para qué sirve |
|---|---|
| `--dry-run` | Reconoce sin actuar sobre el sistema |
| `--list-cameras` | Lista los índices de cámara detectados |
| `--camera N` | Fuerza un índice de cámara |
| `--backend dshow\|msmf\|any` | Backend de captura de OpenCV |
| `--delegate cpu\|gpu` | Delegate de inferencia |
| `-c, --config RUTA` | Usa otro archivo de configuración |
| `-v, --verbose` | Registro detallado |

Teclado, mientras corre: **G** abre la guía visual de gestos, **H** muestra u
oculta la chuleta, **P** pausa, **M** alterna modo ratón, **Q** o **Esc** cierra.

La guía de **G** dibuja cada gesto con el mismo trazado con el que el programa
dibuja tu mano, junto a lo que hace en el modo actual. Es el sitio al que ir
cuando no tengas claro cómo se forma una pose.

---

## Gestos

El sistema tiene dos modos y un estado de pausa. Se arranca en modo **control**.
Pulsa **G** en cualquier momento para ver todo esto dibujado en pantalla.

### Las siete poses

Son las únicas que el modelo distingue, más la pinza, que se mide por geometría.

| Nombre | Cómo se hace |
|---|---|
| **PUÑO** | Mano cerrada, con el pulgar por delante de los dedos |
| **PALMA** | Los cinco dedos extendidos y separados, palma al frente |
| **ÍNDICE** | Solo el índice extendido hacia arriba; pulgar recogido |
| **PULGAR ARRIBA** | Puño cerrado con el pulgar apuntando hacia arriba |
| **PULGAR ABAJO** | Puño cerrado con el pulgar apuntando hacia abajo |
| **UVE** | Índice y corazón extendidos en uve; los demás recogidos |
| **CUERNOS** | Pulgar, índice y meñique extendidos; corazón y anular recogidos |
| **PINZA** | Pulgar e índice extendidos, los demás recogidos |

«Cuernos» es el signo del rock con el pulgar también fuera. Es el gesto que
MediaPipe llama `ILoveYou`, porque en la lengua de signos americana significa
eso; aquí se nombra por la forma de la mano, que es lo único que hace falta
saber para hacerlo.

### La regla que evita los disparos indeseados

**Ningún gesto tiene a la vez un disparo por permanencia y barridos.** Al
encadenar barridos la mano se queda quieta entre intento e intento, y un
temporizador de permanencia sobre esa misma pose se cumpliría antes de que el
barrido llegue a completarse: acabarías pausando el vídeo en lugar de cambiar de
pestaña. La configuración se valida al arrancar y el programa se niega a
ejecutarse si se reintroduce esa combinación.

Por eso cada pose hace una sola clase de cosa: el **puño** y la **uve** solo
barren, el **pulgar** solo se mantiene, y todo lo demás vive en la rueda.

### Modo control

| Gesto | Acción |
|---|---|
| **PULGAR ARRIBA** mantenido | Subir volumen (repite mientras se mantenga) |
| **PULGAR ABAJO** mantenido | Bajar volumen (repite) |
| **PINZA**, separando los dedos | **Volumen analógico**: la separación fija el nivel |
| **UVE** + barrer derecha / izquierda | Pestaña siguiente / anterior (`Ctrl+Tab`) |
| **UVE** + barrer arriba | Nueva pestaña (`Ctrl+T`) |
| **UVE** + barrer abajo | Cerrar pestaña (`Ctrl+W`) |
| **PUÑO** + barrer derecha / izquierda | Escritorio virtual siguiente / anterior |
| **PUÑO** + barrer arriba | Vista de tareas (`Win+Tab`) |
| **PUÑO** + barrer abajo | Mostrar escritorio (`Win+D`) |
| **CUERNOS** 0,5 s | Abrir la **rueda de comandos** |
| **ÍNDICE** 1 s quieto | Entrar en modo ratón |

Barrer significa mover la mano con decisión manteniendo la pose, no girarla.
La barra **MOV** del panel superior izquierdo indica la velocidad detectada. No
hace falta que la pose sobreviva al final del recorrido: el barrido se
identifica por la pose que dominó el trayecto, porque al mover el brazo la
muñeca gira y el clasificador suele soltar la pose justo al terminar.

### Modo ratón

| Gesto | Acción |
|---|---|
| **ÍNDICE** extendido | Mover el cursor |
| **PUÑO** cerrado | Clic izquierdo mantenido: agarrar y arrastrar |
| **PINZA**, juntando los dedos | Clic derecho |
| **UVE**, moviendo en vertical | Desplazamiento (scroll) |
| **CUERNOS** 0,5 s | Abrir la rueda (contiene «volver a control») |

Cerrar el puño agarra **donde está el cursor**, no donde apunte la palma: al
cambiar el punto de referencia del índice al centro de la mano se congela la
diferencia, de modo que el cursor no salta justo en el instante de agarrar.

### La rueda de comandos

Mantener **CUERNOS** medio segundo despliega un menú radial alrededor de la
mano. A partir de ahí se elige **apuntando con el índice** hacia la opción y
manteniéndola apuntada tres cuartos de segundo; un arco va marcando lo que falta
para confirmar. Cerrar la mano cancela sin ejecutar nada, y el centro es zona
muerta, para poder dudar sin activar nada.

Es la respuesta a que siete poses por cuatro direcciones se quedan cortas. En
lugar de inventar más gestos que memorizar, una sola pose abre un menú con las
opciones **escritas**, así que se puede usar sin conocerlas de antemano.

Trae reproducción y pausa, pista siguiente, silenciar, recorte de pantalla,
las aplicaciones y el cambio de modo. Todo ello se configura en la sección
`wheel` de `config.yaml` y admite hasta ocho opciones cómodamente.

### Siempre disponible

| Gesto | Acción |
|---|---|
| **PALMA** 1,5 s | Apagar / encender el control por gestos |

Cuidado con este último: **no pausa el vídeo que estés viendo, apaga el
programa**. Mientras está apagado la pantalla se atenúa y aparece
`CONTROL EN PAUSA` en rojo, y ningún otro gesto responde. Para pausar un vídeo
está la **UVE** mantenida.

Es la salida de emergencia del sistema: funciona en cualquier modo y es lo
primero que conviene practicar.

---

## Cómo funciona

```
Iriun (móvil) → DirectShow → OpenCV (hilo de captura)
                                  ↓
                  MediaPipe Tasks · GestureRecognizer (LIVE_STREAM)
                                  ↓
                     7 gestos + 21 landmarks por fotograma
                                  ↓
                  Motor de gestos: estabilidad, enfriamiento,
                  puerta de movimiento, barridos, analógico
                                  ↓
                 SendInput (user32) · Core Audio (pycaw) · start
```

El clasificador preentrenado solo distingue siete poses. Todo lo demás —
volumen continuo, pinza, apertura de la mano, dirección de los barridos— sale de
medir los 21 landmarks directamente, siempre **normalizando por el tamaño
aparente de la mano**, de modo que un gesto funciona igual de cerca que de lejos.

Traducir cada etiqueta del clasificador directamente en una acción sería
inutilizable: en las transiciones entre poses el modelo emite etiquetas espurias
durante uno o dos fotogramas. El motor interpone cuatro mecanismos:

- **Estabilidad** — una pose debe repetirse 3 fotogramas para contar.
- **Enfriamiento** — cada acción se bloquea un tiempo mínimo tras dispararse.
- **Puerta de movimiento** — los gestos estáticos se inhiben mientras la mano se
  mueve rápido, para que un barrido no active además la pose que lo acompaña.
  Mide longitud de recorrido, no desplazamiento neto: una mano que oscila vuelve
  al punto de partida y medir extremo contra extremo la daría por quieta.
- **Prioridad del analógico** — mientras la pinza gobierna el volumen, ningún
  otro gesto compite por la mano.

### Estructura

```
config.yaml              mapa de gestos y todos los parámetros
run.py                   lanzador
src/gesture_control/
  camera.py              captura en hilo propio, descarta fotogramas atrasados
  recognizer.py          MediaPipe Tasks en LIVE_STREAM, con caída a CPU
  landmarks.py           geometría de la mano normalizada por su tamaño
  poses.py               nombres, descripciones y esquemas de los siete gestos
  engine.py              máquina de estados de gestos → eventos
  wheel.py               menú radial: apuntado y confirmación por permanencia
  actions.py             registro de acciones ejecutables
  mouse.py               cursor con suavizado adaptativo, clic, arrastre, scroll
  hud.py                 superposición visual
  app.py                 bucle principal
  win/input.py           SendInput por ctypes (teclas, cursor, botones, rueda)
  win/volume.py          volumen maestro por Core Audio
  win/apps.py            lanzador de aplicaciones
tests/test_engine.py     pruebas del motor con manos sintéticas
```

---

## Configuración

Todo se ajusta en [`config.yaml`](config.yaml). Para cambios propios que no
quieras versionar, crea un `config.local.yaml` con solo las claves a
sobreescribir; se fusiona encima del principal.

```yaml
# config.local.yaml
camera:
  index: 1
apps:
  navegador: firefox
```

Añadir un gesto es una entrada más en `bindings`. Por ejemplo, captura de
pantalla con un puño mantenido dos segundos:

```yaml
bindings:
  control:
    - gesture: Closed_Fist
      trigger: hold
      duration: 2.0
      action: hotkey
      args: {keys: [win, shift, s]}
      label: Recorte de pantalla
```

Los disparadores disponibles son `tap` (una vez al detectarse), `hold`
(permanencia), `repeat` (repite mientras se mantenga) y `swipe` (requiere
`direction`). En la configuración los gestos se nombran con la etiqueta interna
del modelo, que es la que aparece a la izquierda:

| En `config.yaml` | En pantalla |
|---|---|
| `Closed_Fist` | PUÑO |
| `Open_Palm` | PALMA |
| `Pointing_Up` | ÍNDICE |
| `Thumb_Up` | PULGAR ARRIBA |
| `Thumb_Down` | PULGAR ABAJO |
| `Victory` | UVE |
| `ILoveYou` | CUERNOS |

Las acciones son `volume_step`, `volume_set`, `volume_mute_toggle`, `media`,
`hotkey`, `launch`, `set_mode`, `toggle_pause`, `mouse_click` y `quit`. Para
crear una nueva basta decorar una función con `@action("nombre")` en
`actions.py`.

La configuración se valida al arrancar: gestos inexistentes, disparadores mal
escritos, aplicaciones sin definir o dos disparadores en conflicto sobre el
mismo gesto se reportan con un mensaje concreto en lugar de fallar en marcha.

### Parámetros que más se notan

| Clave | Efecto |
|---|---|
| `engine.stability_frames` | Más alto: menos falsos positivos, más latencia |
| `engine.motion_gate` | Más bajo: cuesta más disparar gestos estáticos con la mano inquieta |
| `engine.swipe.min_distance` | Recorrido exigido a un barrido, en anchos de mano |
| `mouse.active_region` | Región del encuadre mapeada a la pantalla. Más pequeña: menos recorrido del brazo, más temblor amplificado |
| `mouse.min_smoothing` | Más bajo: cursor más estable pero más lento en reaccionar |

---

## Pruebas

```powershell
Set-Location \\wsl.localhost\Ubuntu\home\<usuario>\gesture_recognition
C:\venvs\gesture\Scripts\python.exe tests\test_engine.py
C:\venvs\gesture\Scripts\python.exe tests\test_wheel.py
C:\venvs\gesture\Scripts\python.exe tests\test_config.py
```

Son 41 pruebas con manos sintéticas, que es la única forma de ejercitar esto sin
cámara. Cubren el motor (estabilidad, enfriamientos, permanencia, repetición,
barridos en ambos sentidos, la puerta de movimiento, el aislamiento entre modos
y la prioridad del deslizador), la rueda (correspondencia entre ángulo y sector,
zona muerta, cancelación, corrección de la relación de aspecto) y la validación
de la configuración, incluida la regla que prohíbe mezclar permanencia y
barridos sobre un mismo gesto.

---

## Problemas frecuentes

**No se abre la cámara.** Abre Iriun en el PC *y* en el móvil, ambos en la misma
red. Comprueba los índices con `run.py --list-cameras`; Iriun suele registrar dos
dispositivos y solo uno entrega 1280×720. Con `camera.index: null` se elige
automáticamente el de mayor resolución.

**Vídeo con retardo creciente.** Es acumulación en el búfer de Iriun. Baja
`camera.width`/`height` a 640×480, o `camera.fps` a 24. La captura ya descarta
los fotogramas atrasados por su cuenta.

**Reconoce el gesto pero no ocurre nada.** Casi siempre es una de dos cosas, y
ambas se ven en pantalla. Si arriba pone `MODO PRUEBA · NO SE EJECUTA NINGUNA
ACCIÓN`, arrancaste con `--dry-run`: quítalo. Si la imagen está atenuada y pone
`CONTROL EN PAUSA` en rojo, el sistema está apagado: mantén la palma abierta
1,5 s para reanudarlo. Solo si no aparece ninguno de los dos hay un problema
real; entonces prueba con `-v` para ver en la consola qué acción se dispara.

**Los gestos se disparan solos.** Sube `engine.stability_frames` a 4 o 5 y
`min_gesture_confidence` a 0.7. Y usa la palma abierta para pausar cuando no
estés usando el sistema.

**El volumen deja de responder al conectar auriculares.** Ya no debería ocurrir.
La interfaz de volumen de Windows pertenece a un dispositivo concreto, no a «la
salida actual», así que al cambiar de dispositivo la que ya se tenía sigue
gobernando los altavoces anteriores. El programa comprueba el dispositivo
predeterminado una vez y media por segundo y se reengancha solo.

**Los barridos no se detectan.** Hay que mover con decisión: por defecto se pide
recorrer 1,2 anchos de mano a más de 3 anchos por segundo. Baja
`engine.swipe.min_distance` y `min_speed` si te resulta exigente. La barra de
velocidad del HUD muestra cuánto te falta.

**El cursor tiembla.** Sube `mouse.min_smoothing` o agranda `active_region`.

**Los atajos con Win no funcionan.** Si la ventana en foco es una aplicación
elevada (ejecutada como administrador), Windows bloquea el input sintético por
diseño (UIPI). Ejecuta el proyecto también como administrador o cambia de
ventana.

**Ruido de MediaPipe al arrancar.** Los mensajes `W0000 ... inference_feedback_manager`
y `Custom gesture classifier is not defined` son informativos de la capa C++ y no
indican ningún fallo.
