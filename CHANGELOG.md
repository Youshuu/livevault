# Cambios de la 1.8.3

Arreglos a partir de un diagnóstico real (27/11/2026): TikTok dejó de devolver JSON durante una hora
y una grabación quedó con casi tres minutos de audio sin imagen.

## Cuando TikTok deja de responder

- Una respuesta **vacía o una página HTML** (lo que hace TikTok cuando frena las consultas) ya no se
  trata como un fallo puntual de cada usuario: se clasifica como límite temporal y se aplica una
  **pausa global**. Antes cada usuario reintentaba cada ~20 s por su cuenta, se reabrían los clientes
  y el bloqueo podía durar horas; ahora se espera 1, 2, 4, 8 y hasta 15 minutos entre intentos.
- Las consultas se reparten (0,35 s de separación mínima) para que 11 usuarios no salgan en ráfaga.
- Durante la pausa **no se marca a cada usuario en rojo**: el panel muestra un solo aviso y los
  contadores de fallo se reinician al volver la conexión.
- El registro guarda el cuerpo de la respuesta cuando el JSON no se puede leer, para diagnosticar.

## Grabaciones sin imagen al final

- El análisis distingue «marcas de tiempo irregulares» de «el video terminó antes que el audio».
  El aviso ahora dice cuánto metraje no tiene imagen en lugar del confuso «hueco 0 s».
- **🛠 ya no recodifica de más**: si el video se cortó antes que el audio, recorta el final sin
  imagen con copia directa (segundos, sin perder calidad). Si hay que regenerar tiempos, usa los fps
  reales del tramo de video, no `fotogramas / duración total` (que dejaba el video ~9 % lento).

## Avisos

- Después de una grabación correcta ya no sale «⚠ No se pudo grabar» por un fallo del monitor de
  estado: cada grabación lleva su propio error y solo avisa de lo que le pasó a ella.
- El botón 🛠 explica en el panel si va a recortar (rápido) o recodificar (lento).

---

# Cambios de la 1.8.2

Actualizaciones desde el propio programa.

## Ya no hace falta volver a GitHub

- **Aviso de versión nueva**: el programa mira la última Release al arrancar y cada ~6 horas. Si hay
  una más moderna, sale un aviso en el panel («Hay una versión nueva: LiveVault X.Y.Z») y una
  notificación de Windows. Se puede desactivar en **Ajustes → Actualizaciones**.
- **Actualizar con un clic**: el botón **Actualizar** descarga el `.exe` de la Release, **comprueba su
  SHA-256** contra el `SHA256SUMS.txt` publicado, lo sustituye y se reinicia ya actualizado. El panel
  se reconecta solo (la clave del panel se conserva), así que no hay que volver a abrirlo.
- **Con red de seguridad**: si el hash no coincide, el archivo no es un `.exe` de Windows o pesa
  demasiado poco, **no se instala nada** y se avisa. Si algo falla al reemplazar el archivo, se
  restaura el anterior. Si la carpeta del programa no admite escritura, dice que lo descargues a mano.
- Solo se aceptan descargas de `github.com` (y sus CDN): una respuesta de la API manipulada no puede
  apuntar a otro servidor.
- Aviso antes de instalar si hay grabaciones en curso: se detendrán.

## Ajustes y seguridad

- Nueva sección **Ajustes → Actualizaciones**: «Avisarme cuando haya una versión nueva» y botón
  «Buscar ahora» que dice si tienes la última.
- Mientras descarga se ve el progreso en el panel (MB y porcentaje), y los errores con «Reintentar».
- El `.old` de la versión anterior se borra en el siguiente arranque.
- 13 pruebas nuevas del actualizador (comparación de versiones, `SHA256SUMS.txt`, validación de URLs
  de GitHub, respuesta de la API y los casos en los que **no** debe instalar).

---

# Cambios de la 1.8.1

Identidad visual y un par de arreglos de interfaz.

## Marca

- **Logotipo**: cuadrado redondeado con anillo coral y triángulo de reproducción. Vale para el icono
  del `.exe` (multi-resolución 16-256 px, con una variante simplificada a 16-32 px para que se
  distinga en la barra de tareas), el icono de la bandeja y la cabecera del panel.
- La bandeja usa el mismo dibujo con el color del estado (gris / naranja / rojo) en el anillo.
- `tools/brand.py` genera todo (icono, `docs/logo.png`, `docs/logo.svg` y la imagen social de GitHub);
  `docs/capturas/` tiene las capturas del README, hechas con datos de demostración.

## Panel

- La cabecera muestra el logotipo y la versión en un chip (`v1.8.1`), sin repetir «LiveVault».
- El cajón de directos pasa de 760 a 900 px y con menos relleno: los botones de cada fila ya no se
  cortaban cuando había muchas columnas.
- El aviso que se ve al abrir la dirección del panel a mano (sin la clave) estaba mal redactado;
  ahora explica cómo abrirlo desde la bandeja y cómo cerrar el programa.

---

# Cambios de la 1.8.0

Primera versión pública. Cambia el nombre a **LiveVault** y se distribuye como `.exe` ya compilado.

## Se descarga, no se compila

- El `.exe` se compila en **GitHub Actions** (Windows limpio, Python 3.13 y `constraints.txt`) y se
  publica en Releases junto con `SHA256SUMS.txt`. Ya no hay que crear el `.exe` a mano.
- Antes de publicar, el propio workflow: pasa `ruff`, las pruebas y el smoke; **ejecuta el `.exe`
  empaquetado** con `--selftest` (comprueba imports, contrato de TikTokLive y ffmpeg); y **revisa que
  el binario no contenga rutas personales** de quien lo compiló.
- El tag `vX.Y.Z` debe coincidir con `APP_VERSION`, `instalador.iss`, `LEEME.md` y este archivo: si no,
  el workflow falla antes de publicar nada.
- `LiveVault.spec` es la única definición del contenido del `.exe` (la usan el CI y `Crear_EXE.bat`),
  con versión y descripción en las propiedades del archivo de Windows.

## Seguridad

- **Clave del panel**: el servidor local exige una clave aleatoria por instalación
  (`datos/panel.token`, 256 bits) además de las comprobaciones de `Host`, `Origin` y `Sec-Fetch-Site`.
  Antes, cualquier proceso local u otro usuario de Windows podía leer las grabaciones y cambiar los
  ajustes con una cabecera fija escrita en el HTML.
- **Reporte y paquete de soporte sin datos personales**: además de ocultar claves y `sessionid`, las
  rutas del PC (`C:\Users\...`) se sustituyen por etiquetas y el id del chat de Telegram se enmascara.
  Los logs servidos por la API también se limpian.
- **Avatares**: solo se descargan por HTTPS de dominios de TikTok (antes se seguía cualquier URL, lo que
  permitía SSRF hacia la red interna).
- **Bot de Telegram**: «Detectar chat» solo tiene en cuenta un `/start` (o el chat ya autorizado) y el
  panel pide confirmación antes de guardarlo. El aviso de error del bot pasa por la redacción de claves.
- **Cifrado**: si DPAPI no puede cifrar un secreto, ahora queda un aviso en el registro en vez de
  guardarlo en claro en silencio.
- El servidor rechaza cuerpos con `Transfer-Encoding` y el panel se sirve con `Content-Security-Policy`.

## Robustez

- Si el programa no arranca (base de datos, puertos ocupados, fallo inesperado) muestra un **aviso en
  pantalla**; el `.exe` no tiene consola y antes no se veía nada.
- **«Salir» cierra de verdad**: en el `.exe`, el proceso termina en 1–2 s aunque la bandeja del sistema
  deje un hilo suyo vivo o una grabación tarde en finalizar. Antes podía quedarse en el Administrador
  de tareas (parecía cerrado pero seguía ejecutándose).
- La creación del icono de la bandeja ya no puede impedir el arranque: si falla, se sigue sin bandeja.
- Se avisa en el registro si la carpeta de datos está en **OneDrive** o si el programa tuvo que usar
  `%LOCALAPPDATA%\LiveVault\datos`.
- Usuarios de TikTok con nombres reservados de Windows (`con`, `nul`, `com1`…) se rechazan al añadirlos.
- El mensaje de "falta ffmpeg" ya no pide instalar un paquete de Python a quien usa el `.exe`.
- Las pruebas fuerzan su propia carpeta de datos: nunca tocan los datos reales del usuario.

## Cambios de nombre y compatibilidad

- Producto, ventana, bandeja, notificaciones, bot y `.exe`: **TikTok Live Recorder → LiveVault**.
- Tus datos no se mueven: la carpeta `datos`, la base de datos, los nombres de las copias de seguridad
  y las variables internas (`TKREC_DATA`, `datos/tkrec.db`) se mantienen igual a propósito.
- El inicio con Windows pasa a llamarse `LiveVault`; se elimina la entrada antigua si existía.
- Si en versiones anteriores los datos estaban en `%LOCALAPPDATA%\TikTokLiveRecorder`, se mueven solos a
  `%LOCALAPPDATA%\LiveVault` la primera vez.
- Nuevo `--version` y `--selftest` con mensaje claro si faltan argumentos.

## Documentación y repositorio

- `README.md` (portada, cómo descargar y empezar), `CONTRIBUTING.md`, `SECURITY.md`,
  `THIRD_PARTY_NOTICES.md`, plantillas de issue, `LICENSE` (MIT) y `.gitignore` que excluye `datos/`,
  `grabaciones/`, `dist/`, entornos virtuales, secretos y binarios.
- `LEEME.md` reorganizado para quien descarga el `.exe`: fuera las instrucciones de compilación, que
  pasan a `CONTRIBUTING.md`.
- `pip` fijado en `requirements-build.txt` (va dentro del `.exe`) y `ruff` con rango de versión.

---

# Cambios de la 1.7.1

Corrección del actualizador de motor.

- El chequeo de `web.set_session` contaba `self` en la firma. En TikTokLive 7.0.1 el método ya
  está ligado a la instancia y solo tiene dos parámetros (`session_id`, `tt_target_idc`), así que se
  rechazaba un motor válido con «web.set_session cambio de firma».
- Ahora se comprueba que acepte dos argumentos posicionales con `inspect.signature(...).bind(...)`,
  que funciona tanto con métodos ligados como con firmas antiguas.
- Verificado con TikTokLive 7.0.1: el selftest termina en `OK 7.0.1 ... nuevo`.
- 70 pruebas unitarias y 69 comprobaciones de smoke en verde.
- Sin otros cambios de comportamiento.

---

# Cambios de la 1.7.0

Base: 1.6.0. Mejoras de uso diario sobre las grabaciones y la sesión, más empaquetado e higiene.

## Grabaciones

- **Favoritas (★)**: una grabación marcada como favorita no se borra al usar «Borrar todas» ni
  con la retención automática por días o por tamaño. Sí se puede borrar a mano una a una, y sí
  desaparece al quitar el usuario con «borrar archivos» (eso es una acción explícita).
- **Borrar todas las grabaciones de un usuario** sin quitarlo de la lista: botón «🧹 Borrar todas»
  en el cajón. Conserva las favoritas y omite las que están grabándose o procesándose.
- **Ver mientras se graba**: botón «👁 Ver» en las filas en curso. El servidor sirve el MP4
  fragmentado que va creciendo; si todavía no hay datos devuelve un aviso claro en vez de un error.
- La retención y la unión de sesiones respetan las favoritas: al unir, los trozos favoritos no se
  borran.

## Sessionid y análisis

- **Comprobar ahora**: botón en el banner de sessionid vencida y en Ajustes. Comprueba la cookie en
  el momento y muestra **de qué cuenta es** (`@usuario`) cuando TikTok lo devuelve.
- `POST /api/session/check`; el chequeo manual actualiza el estado y la cuenta sin esperar al ciclo
  automático de 30 minutos.
- **Análisis guardado en la base de datos**: códec, resolución, fps, fotogramas, segundos congelados,
  tiempos irregulares y fecha (`recordings.codec`, `width`, `height`, `fps`, `frames`,
  `frozen_secs`, `ts_bad`, `analyzed_at`). El cajón lo muestra sin volver a analizar; el botón 🔍
  sigue disponible y actualiza el resultado.
- Esquema **5** con migración automática (columnas nuevas; no se pierde nada).

## Empaquetado e higiene

- `constraints.txt` con las versiones probadas: TikTokLive 7.0.1, imageio-ffmpeg 0.7.0,
  yt-dlp 2026.10.26, pystray 0.19.5, pillow 13.0.0 y PyInstaller 6.22.3. Los scripts de arranque y
  compilación usan `-c constraints.txt`.
- `Crear_EXE_onedir.bat` para generar una carpeta `dist\TikTokLiveRecorder\` (`--onedir`) e
  `instalador.iss` para Inno Setup 6. El instalador no se ha compilado aquí porque Inno no está
  instalado.
- `Firmar_EXE.bat`: plantilla de firma con `signtool`; **no incluye certificado**. Sin un
  certificado de confianza no se puede eliminar el aviso de SmartScreen.
- CI de Windows en `.github/workflows/tests.yml`: instala con constraints, pasa `ruff` (solo errores
  graves), las pruebas unitarias y el smoke.
- `ruff.toml` conservador (E9/F63/F7/F82).

## Pendiente / no incluido en esta versión

- **WebSocket de TikTokLive** para detectar el fin del live y guardar el chat: requiere probarlo
  contra un live real y elegir el modo de respaldo; se deja para una fase dedicada para no
  arriesgar la grabación estable.
- **Dividir `app.py` en módulos**: refactor grande, pendiente.
- **Actualización automática de la app** (no solo del motor): necesita un servidor de releases
  firmado y verificación de firma; no se implementa sin esa infraestructura.
- La firma de código y el instalador quedan preparados, pero no validados en este entorno.

## Pruebas

- **69 pruebas unitarias** (núcleo, medios y bot), **69 comprobaciones** de smoke y **2 pruebas de
  grabación real**.

---

# Cambios de la 1.6.0

Base: 1.5.0. Fase 2 del bot: gestión de usuarios y grabación desde Telegram, con botones y
confirmaciones.

## Comandos nuevos

- `/list [página]`: lista paginada (8 por página) con estado, espectadores, tiempo grabado o
  «pausado», y botones ◀ ▶ para cambiar de página.
- `/user @usuario`: detalle (estado, espectadores, título, tiempo grabando, última revisión,
  grabaciones y peso) con botones **⏺ Grabar / ⏹ Detener**, **📼 Últimas**, **⏸ Pausar / ▶ Reanudar**
  y **🗑 Quitar**.
- `/pause @usuario` y `/resume @usuario`: pausan o reanudan la vigilancia (si estaba grabando, se
  detiene y se indica).
- `/auto on|off @usuario`: activa o desactiva la grabación automática de ese usuario.
- `/record @usuario`: comprueba y empieza a grabar ahora.
- `/stop @usuario`: detiene la grabación en curso (el directo sigue).
- `/last @usuario`: últimas 3 grabaciones con fecha, duración, peso y calidad; envía la miniatura
  de la última como foto.
- `/del @usuario`: confirmación con tres botones (quitar dejando los archivos, quitar y borrar
  archivos, cancelar), con caducidad de 2 minutos y sin borrar nada con un solo toque.
- Alias en español: `/lista`, `/usuario`, `/quitar`, `/pausar`, `/reanudar`, `/grabar`,
  `/detener`, `/ultimas`.

## Cómo está hecho

- **Callbacks de Telegram**: se procesan `callback_query` además de mensajes, con
  `answerCallbackQuery` inmediato y `editMessageText` para actualizar la lista o quitar los botones.
- **Confirmaciones con nonce y caducidad**: el botón de borrar solo funciona en el chat que lo pidió,
  una vez, y durante 2 minutos.
- **Operaciones compartidas con el panel**: `App.remove_user`, `App.set_user_flag`,
  `App.record_user` y `App.stop_user_recording`; las rutas HTTP y el bot usan lo mismo, así que la
  validación y el comportamiento no se duplican.
- **Datos reales**: la lista y el detalle salen del mismo `snapshot` que usa el panel; las
  grabaciones de `/last` salen de la base de datos y la miniatura del mismo `thumbs/`.
- **Botones**: el «Detener» de `/user` y el de `/list` respetan el estado real; los callbacks de un
  chat no autorizado se rechazan.

## Pruebas

- 31 pruebas del bot (incluye paginación, botones, confirmaciones, nonce caducado, autorización de
  callbacks y el bucle real con transporte falso) + 64 pruebas de núcleo, medios y bot en total.
- 2 pruebas de grabación real y smoke test de la API con 63 comprobaciones, incluidos `/list`,
  `/user`, `/pause`, `/resume`, `/auto`, `/last` y el borrado con confirmación.

## Pendiente para la siguiente fase

- `/support`, `/backup`, `/disk`, `/notify`.
- `/engine`, `/update`, `/restart` con confirmación.
- Botones de acción en los propios avisos (detener desde la notificación de «grabando»).
- Emparejamiento con código para el primer chat.

---

# Cambios de la 1.5.0

Base: 1.4.0. El bot de Telegram pasa de ser solo de salida a **escuchar órdenes** (fase 1).

## Comandos nuevos

- `/add @usuario` (o mandar `@usuario` / un enlace de perfil): añade a la lista, hace una
  comprobación inmediata y responde con el resultado: en vivo, sin directo, no existe o no se pudo
  comprobar. También acepta `/add usuario` sin arroba.
- `/status`: usuarios, en vivo, grabando, sesión, versiones, disco y bloqueo de TikTok.
- `/help`, `/start`: ayuda y bienvenida.
- `/id`: id del chat, tipo y si está autorizado (útil para configurarlo).
- Alias en español: `/estado`, `/anadir`, `/agregar`, `/ayuda`, `/inicio`.
- Si mandas una palabra suelta que parece un usuario, el bot **no lo añade**: te propone
  `/add palabra` para evitar altas por error.

## Cómo escucha

- **Long polling** (`getUpdates`): no hay que abrir puertos ni exponer el panel a Internet.
- **Offset persistente** en la tabla `bot_state`: al reiniciar no se repiten órdenes antiguas.
- **Autorización**: solo el chat configurado en Ajustes puede dar órdenes. `/start`, `/help` e `/id`
  responden a cualquier chat para poder configurarlo; el resto contesta «no autorizado».
- **Un solo consumidor**: el botón «Detectar chat» ya no llama a `getUpdates` por su cuenta; usa el
  último mensaje que ha visto el bucle. Se evita el conflicto 409.
- Se arranca y se para con el programa y se reinicia solo si cambias el token o el chat.
- Errores de token inválido, conflicto de instancia o red se muestran en un aviso del panel y en
  el reporte de Diagnóstico; el bucle reintenta con espera.
- El bot responde **mientras el programa está abierto**; si el PC está apagado o suspendido, no hay
  respuesta.

## Integración

- El alta de usuarios es ahora una sola función (`App.add_user`) compartida por el panel y el bot:
  misma validación, mismos duplicados, mismos mensajes.
- `Manager.check_now` permite comprobar un usuario al momento sin esperar al siguiente sondeo.
- `/api/state` incluye el estado del bot; Ajustes muestra si está escuchando y avisa si hay error.
- Esquema de base de datos 4 (tabla `bot_state`); las bases anteriores se migran solas.

## Pruebas

- 18 pruebas del bot (comandos, autorización, offset, bucle con transporte falso) + 51 pruebas de
  núcleo y medios + 2 de grabación real + smoke test con 54 comprobaciones, todo en verde.

## Pendiente para la siguiente fase

- `/list`, `/user`, `/del`, `/pause`, `/resume`, `/auto`, `/record`, `/stop`, `/last`.
- `/support`, `/backup`, `/disk`, `/notify`, `/engine`, `/update`, `/restart`.
- Botones inline en los avisos y confirmaciones con caducidad.
- Emparejamiento con código para el primer chat.

---

# Cambios de la 1.4.0

Base: 1.3.0. Tres bloques pensados para el uso real: 8 usuarios vigilados 24/7, dos directos al día
y un solo disco.

## Un directo = una entrada (sesiones)

- Cada directo recibe un **`session_id`** al empezar a grabar. Si el stream se corta y vuelve, los
  trozos comparten sesión; si el directo termina y empieza otro, la sesión es nueva.
- El cajón de **Directos** agrupa los trozos: «🎬 Directo · 3 archivos · 2 h 10 min · 6,3 GB», con
  los archivos debajo. El resumen cuenta **directos**, no archivos, y añade el total de archivos.
- Botón **🔗 Unir**: crea un solo archivo con el demuxer `concat` y `-c copy` (sin recodificar, no
  pierde calidad ni recalienta la CPU). Si el MP4 falla por mezclar formatos, reintenta en MKV.
  Puedes elegir **borrar los trozos** al terminar para liberar espacio; el video unido queda como
  una entrada propia con la nota «Unido de N archivos».
- Los archivos ya existentes de la 1.3 y anteriores aparecen como entradas individuales (no tienen
  sesión); no se toca nada de lo que ya está grabado.

## Copias de seguridad, integridad y soporte

- **Copia diaria automática** de la base de datos en `datos/backups/` (`tkrec-FECHA.db`), conservando
  las 7 últimas. Se usa la API `backup()` de SQLite, que sí es consistente con WAL (copiar el archivo
  a mano puede dejar una copia rota).
- **Comprobación de integridad** (`PRAGMA quick_check`) al arrancar; el resultado sale en el reporte
  de Diagnóstico.
- **Copia manual** con el botón «Copia de seguridad» de Diagnóstico.
- **Paquete de soporte**: un ZIP con el reporte, los logs, los ajustes saneados, los usuarios y un
  CSV de grabaciones, sin claves. Un solo archivo para enviar si algo falla.

## Fin de directo y avisos sin duplicados

- Nuevo aviso **🏁 Directo terminado** (activable en Ajustes, también por ntfy/Telegram/webhook) con
  duración total, peso, número de archivos y pico de espectadores. Espera a que terminen de procesarse
  los archivos de la sesión para no dar cifras a medias.
- No se envía si detuviste tú la grabación (el directo sigue).
- El último directo avisado se guarda en la base de datos (`notified_room`): al reiniciar el programa
  no se repiten los avisos de un directo que ya estaba en curso.

## Esquema y compatibilidad

- `PRAGMA user_version` pasa a 3: se añaden `recordings.session_id`, `users.notified_room` y el índice
  `idx_rec_session` automáticamente al abrir una base de datos anterior.
- Los ajustes nuevos (`push_ended`) se añaden solos con su valor por defecto.
- No hay cambios en la forma de grabar: MP4 fragmentado, una fila por archivo, recuperación y
  retención igual que en la 1.3.

## Pruebas

- 33 pruebas de núcleo y medios (incluye migración de una base antigua, copias, integridad, unión de
  dos segmentos y agrupación por sesión) + 2 de grabación real + smoke test de la API con 45
  comprobaciones. Todo en verde; el ZIP se verificó desde una extracción limpia.

## Limitaciones

- La unión no recodifica: si los trozos tienen códecs o resoluciones distintas (por ejemplo, porque
  cambió la calidad a mitad de directo), puede fallar; en ese caso se conserva el original y se avisa.
- El aviso de fin de directo necesita que el programa siga abierto para detectar el final (es cuando
  se genera el resumen). Si lo cierras antes, los archivos quedan y se recuperan al abrir.

---

# Cambios de la 1.3.0

Base: 1.2.4. El objetivo era eficiencia, facilidad de uso y fiabilidad sin cambiar la forma de trabajar:
mismos archivos, mismos ajustes (se conservan) y misma bandeja.

## Fiabilidad

- **Nombres de archivo únicos** (microsegundos + comprobación de existencia + sufijo). Antes, dos intentos
  de grabación en el mismo segundo compartían archivo: el segundo truncaba el primero con `-y` y su
  finalización podía borrarlo mientras se grababa.
- **Sin finalizaciones duplicadas**: un registro en proceso no se vuelve a procesar (recuperación + cierre).
- **Sin ffmpeg huérfanos**: Job Object de Windows (`KILL_ON_JOB_CLOSE`) para los ffmpeg de grabación,
  reparación y análisis, más terminación explícita al cerrar. Si el programa crashea o lo matan, esos
  procesos mueren con él.
- **Fin de proceso controlado**: al cancelar una grabación se mata su ffmpeg; el apagado espera 60 s,
  cancela lo que quede y después mata hijos.
- **`self.users` con dueño único**: `load_users()` reconstruye el diccionario y lo intercambia de golpe.
  Antes se mutaba desde hilos HTTP mientras la bandeja lo recorría (`RuntimeError` intermitente).
- **Clientes TikTokLive con candado por usuario** y caché LRU de 12. Antes `drop()` podía cerrar el cliente
  que otra consulta estaba usando.
- **Un solo punto de salida a TikTok** (`NetGate`): concurrencia máxima 4, bloqueo global respetado y
  comprobación de sesión incluida. Antes `_watch` seguía consultando cada 10 s durante un bloqueo,
  alargándolo.
- **Excepciones de tareas en segundo plano registradas** (`_spawn`). En el `.exe` sin consola se perdían.
- **SQLite**: índices `recordings(user_id, id)` y `recordings(status)`, `synchronous=NORMAL`,
  `busy_timeout=10 s`, `PRAGMA user_version` y timeout de conexión.
- **"Vaciar errores"** rota el log con el handler (`doRollover`) en vez de truncar el archivo abierto,
  que dejaba bytes NUL. `setup_logging` cierra los handlers anteriores.
- **Carpeta de grabaciones por defecto** cae junto a `datos` si el programa no puede escribir donde está
  (antes apuntaba a una carpeta no escribible y fallaba con "Error interno").
- **Detección de instancia única sin proxy**: `probe_instance` desactiva el proxy; con `HTTP_PROXY`
  (VPN/empresa) se abrían dos instancias.
- **Actualizador del motor con contrato**: el `--selftest` ahora comprueba `fetch_user_room_data`,
  `fetch_room_info`, `fetch_room_id_from_api`, `web.get(extra_headers=...)`, `set_session`, que ffmpeg del
  motor se ejecute y que el motor nuevo tome prioridad. Si algo falla, no se activa.
- **Timeouts** en miniatura, análisis, remux y reparación; watchdog que mata un ffmpeg colgado.
- **Recuperación**: se adoptan `.mp4`/`.mkv` huérfanos de la carpeta del usuario (con umbral de tamaño y
  antigüedad), se limpian `.tmp.mp4` viejos y las filas cuyo archivo ya no existe. El `.mp4` se escribe a
  un temporal y se renombra; la BD se actualiza **antes** de borrar el original.
- **Disco lleno**: se detecta el error de ffmpeg y se muestra un mensaje accionable en vez de un log técnico.

## Eficiencia

- **Grabación directa en MP4 fragmentado** (`+frag_keyframe+empty_moov+default_base_moof`): se elimina el
  remux completo de cada grabación (~100 % de escritura extra), el archivo se puede reproducir mientras se
  graba y sobrevive a un cierre abrupto.
- **Detección de congelados solo en fotogramas clave** (`-skip_frame nokey`, `-threads 2`,
  `-filter_threads 1`): medido con ffmpeg 8 en 1080p30, ~1,2 s/min → ~0,1 s/min (≈12× menos CPU).
- **Análisis de tiempos por tubería**: sin archivos temporales, memoria constante (antes: ~430.000 líneas /
  ~27 MB de stdout para 4 h, más el parseo con el GIL tomado).
- **Sondeo adaptativo**: usuarios en vivo cada ≤25 s; offline normales según el intervalo; offline > 1 h cada
  hasta 5 min. Primer chequeo repartido al azar al cargar.
- **`_watch` consulta el estado cada 60 s** (antes 10 s) y usa `-progress` de ffmpeg (tamaño, bitrate,
  velocidad, segundos escritos) en vez de mirar solo el archivo.
- **`/api/state`**: totales cacheados 2 s (antes un `GROUP BY` en cada sondeo), `find_ffmpeg` cacheado.
- **Panel**: sondeo recursivo (no se solapan si el servidor tarda), pausa con la pestaña oculta, y solo
  se redibuja si cambia el estado.
- **Caché de clientes**: máximo 12 en vez de uno por usuario para siempre.
- **Cierre rápido**: los bucles largos (limpieza, sesión) se despiertan al salir; antes la limpieza podía
  retrasar el cierre hasta 90 s.

## Facilidad de uso

- **Disco**: ajustes de espacio libre mínimo, tamaño total máximo y retención por días; aviso y pausa de las
  grabaciones automáticas; chip con GB libres en la cabecera.
- **Cookie completa**: se acepta `sessionid=...; tt-target-idc=...` y se extrae el valor.
- **Borrar secretos**: botones junto a sessionid, proxy, tema de ntfy, token de Telegram y webhook
  (el servidor ya lo soportaba, la UI no lo enviaba).
- **Progreso de reparación**: la reparación ya no bloquea la petición; devuelve un trabajo con progreso real.
- **Visor de logs** dentro de Diagnóstico (errores / actividad / última actualización del motor).
- **Buscador y filtro** por estado, contador, "próxima revisión" por tarjeta y pausar/reanudar todos.
- **Bitrate y velocidad** en la tarjeta cuando está grabando.
- **Mensajes de error** en español orientados a la acción (p. ej. "Fallo inesperado… Motor → Actualizar").
- **Webhook genérico** de avisos, además de ntfy y Telegram; reintento de envíos.
- **Ajustes** agrupados por secciones y opciones avanzadas plegadas; se documentan ffmpeg propio y tamaño mínimo.
- **Cabeceras de seguridad** en el panel (`nosniff`, `X-Frame-Options`, `Referrer-Policy`), comprobación de
  `Origin`/`Sec-Fetch-Site` y límite de tamaño de cuerpo.

## Seguridad

- **Secretos cifrados en reposo** con DPAPI (`session_id`, `proxy`, `push_ntfy_topic`, `push_tg_token`,
  `push_webhook`). Compatible con bases de datos antiguas: al guardar se cifran.
- **Redacción centralizada** en el handler de logs (URLs con query, sessionid, tokens de bot, credenciales de
  proxy, cabeceras `Authorization`).

## Pruebas

- `tests/test_core.py`: parseo de usuarios/respuestas/candidatos/perfil, redacción, extracción de sessionid,
  nombres únicos, ajustes cifrados, índices, límites del `NetGate`.
- `tests/test_media.py`: duración, miniatura, análisis normal y fragmentado, remux de MKV antiguo a MP4,
  reparación con progreso (requiere ffmpeg).
- `tests/smoke_api.py`: arranca la app real y recorre API, Range, ajustes, retención, logs, borrado y trabajo
  de reparación; verifica el cierre ordenado.

## Compatibilidad y cambios de comportamiento

- Los `.mkv` existentes se siguen reproduciendo, compartiendo y borrando con normalidad. Solo se convierten
  a MP4 si la grabación venía en MKV y el formato elegido es MP4.
- Al actualizar, `datos/tkrec.db` se migra sola (índices y columnas). No hay que borrar nada.
- Los secretos guardados en claro por una versión anterior se cifran automáticamente al abrir la app.
- `POST /api/recordings/<id>/repair` devuelve ahora `{"job": "<id>"}`; el panel consulta
  `GET /api/jobs/<id>`. Un script externo que esperase `{"ok": true, "id": ...}` debe adaptarse.
- Nuevos ajustes en la base de datos: `min_free_gb` (2 por defecto), `retention_days` (0), `max_total_gb` (0)
  y `push_webhook` (vacío). Con 2 GB libres mínimos, si el disco está por debajo se pausan las grabaciones
  automáticas (se puede poner 0 para desactivar la pausa).
- Si mueves `datos/` a otro PC u otro usuario de Windows, los secretos cifrados no se pueden descifrar:
  hay que volver a escribirlos (el resto de datos se conserva).

## Lo que NO se ha hecho (y por qué)

- **Token de API**: exigir un token obligaría a abrir siempre el panel con una URL especial o romper los
  marcadores; el riesgo principal (webs de terceros) ya queda cubierto por `Host`, `Origin`, `Sec-Fetch-Site`,
  `X-TKREC` y la ausencia de CORS. Sería el siguiente paso si se quiere proteger de otros procesos locales.
- **WebSocket para detectar el fin del live**: es la mejora grande pendiente; requiere integrar eventos de
  TikTokLive y cambiar el ciclo de grabación. El sondeo se ha reducido mientras tanto.
- **Empaquetado `--onedir`**: mejor arranque y menos falsos positivos de antivirus, pero cambia la entrega de
  "un solo .exe"; se deja para una versión con instalador.
- **División de `app.py` en módulos y dependencias fijadas**: recomendable, pero no aporta al usuario final y
  complica el diff de esta entrega.
- **Ajustes con `synchronous=FULL`**: se ha elegido `NORMAL` (WAL) por velocidad; el peor caso es perder los
  últimos commits ante un corte de luz, no corromper la base.
