# LiveVault 1.8.0

Vigila usuarios de TikTok y graba sus lives automáticamente (varios a la vez).
Vive en la bandeja del sistema, te avisa cuando alguien entra en vivo y puede iniciar solo al encender el PC.

Descarga: **[última versión](../../releases/latest)** · Novedades: [CHANGELOG.md](CHANGELOG.md)

### Novedades de la 1.8

- **Nombre nuevo: LiveVault** (antes TikTok Live Recorder). El archivo pasa a llamarse
  `LiveVault.exe`; tus usuarios, ajustes y grabaciones se conservan (la carpeta `datos` sigue igual).
- **El panel ahora tiene clave**: se genera una aleatoria en tu PC y otra persona (u otro usuario de
  Windows) ya no puede abrir tus grabaciones ni cambiar los ajustes aunque el programa esté abierto.
- **Paquete de soporte y reporte más seguros**: además de ocultar las claves, sustituyen las rutas de
  tu PC (`C:\Users\...`) por etiquetas.
- **Arranque más claro**: si el programa no puede abrir el panel o falla al iniciar, ahora muestra un
  aviso en pantalla en vez de no hacer nada (el `.exe` no tiene consola).
- **El `.exe` se descarga ya compilado** desde Releases: se compila y se prueba en GitHub en un
  Windows limpio. Ya no hay que crear el `.exe` a mano.
- **Avisos y red**: si el programa está en una carpeta de OneDrive avisa en el registro (la
  sincronización puede bloquear la base de datos mientras graba).
- **El bot de Telegram** solo autoriza un chat nuevo con `/start` y el panel pide confirmación antes
  de guardarlo.

### Corrección 1.7.1

- El actualizador de motor ya acepta **TikTokLive 7.0.1**: la comprobación de `web.set_session`
  contaba mal el parámetro `self` y rechazaba un motor válido. Ahora se comprueba que acepte los dos
  argumentos que usa el programa. Probado con TikTokLive 7.0.1 (selftest `OK ... nuevo`).
- No hay otros cambios: todo lo de la 1.7 sigue igual.


El historial de versiones anteriores está en [CHANGELOG.md](CHANGELOG.md).



## Instalar y actualizar

1. Descarga **`LiveVault-<versión>.exe`** de [Releases](../../releases/latest).
2. Guárdalo en una **carpeta local** (por ejemplo `C:\LiveVault`). Evita OneDrive, el Escritorio
   sincronizado y `Archivos de programa`: si la carpeta no admite escritura, el programa se pasa solo
   a `%LOCALAPPDATA%\LiveVault\datos` y no verías tus usuarios.
3. Doble clic. Ya está: no hace falta Python ni ffmpeg.

**Para actualizar**: cierra el programa (**⏻ Salir**), sustituye el `.exe` viejo por el nuevo en la
misma carpeta y ábrelo. Usuarios, ajustes y grabaciones se conservan: están en `datos` y `grabaciones`,
no dentro del `.exe`.

La primera vez Windows puede decir "Windows protegió su PC" (el programa **no está firmado**):
**Más información → Ejecutar de todas formas**. Si el antivirus lo bloquea, es un falso positivo
habitual de los `.exe` de PyInstaller; agrégalo a las exclusiones. Puedes comprobar la descarga con el
`SHA256SUMS.txt` de cada release.

Si prefieres no ejecutar un binario ajeno, el programa también funciona con Python instalado
(ver [CONTRIBUTING.md](CONTRIBUTING.md)) y el código está a la vista.

Los `.exe` los compila y prueba GitHub en un Windows limpio en cada release; no se compilan en el PC
del autor. `Crear_EXE.bat`, `Crear_EXE_onedir.bat`, `instalador.iss` y `Firmar_EXE.bat` son solo para
desarrollo y para quien quiera un instalador propio.


## Uso diario

- Abre `LiveVault.exe`: se abre el panel y aparece un icono junto al reloj.
  En Windows 11 puede quedar oculto en la flecha **^**: arrástralo a la barra para tenerlo a la vista.
- **Cerrar la ventana no cierra el programa**: sigue grabando desde la bandeja.
  Clic en el icono = abrir el panel. Clic derecho = menú (carpeta de grabaciones, notificaciones, iniciar con Windows, **Salir**).
- Para cerrarlo del todo: **Salir** en el menú del icono, o el botón **⏻ Salir** del panel.
  Las grabaciones en curso se guardan antes de cerrar.
- Si lo abres otra vez mientras ya está en marcha, simplemente te muestra el panel que ya está abierto.
- El panel solo se abre desde el programa: la dirección lleva una clave aleatoria de tu PC. Si escribes
  `http://127.0.0.1:8765` a mano verás un aviso; abre el panel desde el icono de la bandeja.

### Colores del icono
Gris = vigilando · Naranja = alguien en vivo (sin grabar) · Rojo = grabando.

### Notificaciones de Windows
- 🔴 *Grabando en vivo*: cuando detecta un live y la grabación realmente arranca (con espectadores).
- 🔴 *En vivo*: si el usuario tiene la grabación automática desactivada.
- ✅ *Grabación guardada*: al terminar, con duración y peso.
- 🏁 *Directo terminado*: resumen al acabar el directo (duración, peso, archivos y pico de espectadores).
- ⚠ *No se pudo grabar*: si está en vivo pero algo falla (p. ej. falta la sessionid).
- 💾 *Poco espacio*: cuando se pausan las grabaciones automáticas por falta de disco.

Se avisa una sola vez por directo. Se pueden apagar en el menú del icono o en Ajustes.
Si no ves ningún aviso, revisa que el modo "No molestar / Asistente de concentración" de Windows no los esté silenciando.

### Iniciar con Windows
Ajustes → **Avanzado** → **Iniciar con Windows, minimizado en la bandeja** (o en el menú del icono).
Arranca en segundo plano, sin abrir ventana, y espera a que haya internet antes de empezar a vigilar.
Si luego mueves el `.exe` de carpeta, se corrige solo la próxima vez que lo abras.

### Favoritas, borrar todas y ver mientras se graba

- En el cajón de un usuario, cada grabación tiene una **estrella**: ☆ pasa a ★. Las favoritas no se
  borran con **🧹 Borrar todas** ni con la limpieza automática por días o por tamaño. Sí se pueden
  borrar una a una, y sí se borran si eliminas al usuario con «borrar archivos».
- **🧹 Borrar todas** borra las grabaciones terminadas de ese usuario y conserva las favoritas; las
  que están grabándose o procesándose no se tocan. Pide confirmación y dice cuántas favoritas
  conservará.
- En una fila **Grabando**, el botón **👁 Ver** abre el archivo que se está escribiendo. Empieza
  desde el principio: si acabas de empezar, espera unos segundos y recarga la página del vídeo.
- La línea pequeña bajo la fecha (por ejemplo `h264 · 1920×1080 · 30 fps`) es el **análisis guardado**:
  no se vuelve a analizar al abrir el cajón. El botón 🔍 reanaliza y actualiza ese resultado.

### Comprobar la sessionid y de quién es

- En **Ajustes → sessionid** hay un botón **Comprobar**: guarda lo que hayas escrito (o usa la cookie
  guardada) y la comprueba al momento. Si TikTok devuelve la cuenta, verás `Cuenta: @usuario`.
- Si la sesión está vencida, el banner rojo del panel también tiene **Comprobar ahora**.
- La comprobación manual actualiza el estado; no hace falta esperar a la revisión automática de 30
  minutos. Si TikTok no responde, se avisa y se mantiene el estado anterior.


## Ajustes que conviene conocer

- **Revisar cada usuario cada**: 30–60 s es un buen equilibrio. Los usuarios que llevan horas sin emitir se revisan
  automáticamente más espaciado para no gastar peticiones.
- **Calidad**: "Máxima" suele ser 1080p; ocupa bastante. HD reduce disco y ancho de banda.
- **Disco**: si pones "Avisar/pausar si quedan menos de X GB", al bajar de ese espacio se detienen las grabaciones
  **automáticas** (las que tú inicias a mano se intentan igual) y se avisa una vez.
- **Retención**: se puede limitar por días o por GB totales. La limpieza corre sola cada 10 minutos y nunca toca
  la grabación en curso; borra primero las más antiguas.
- **Sessionid**: pega la cookie completa (`sessionid=...; tt-target-idc=...`) y se extrae sola. El botón **Borrar**
  elimina la que estuviera guardada; **Comprobar** la verifica al momento y muestra la cuenta.
- **Avisos**: elige cuáles quieres (en vivo, grabación iniciada, guardada, **directo terminado**, problemas y
  sessionid vencida). Si solo quieres uno, el de **directo terminado** es el más útil.
- **Avanzado**: ruta propia de ffmpeg, tamaño mínimo para conservar una grabación, respaldo con yt-dlp y
  actualización automática del motor al iniciar.


## Avisos al celular

Con **ntfy**, **Telegram**, un **webhook** o varios a la vez:

- *ntfy*: instala la app «ntfy» en el celular, suscríbete a un tema inventado largo (p. ej. `livevault-9fa3x7kq2m`)
  y escribe ese mismo tema en Ajustes.
- *Telegram*: crea un bot con @BotFather (`/newbot`), pega el token, escríbele `/start` a tu bot y pulsa «Detectar chat».
  Recibirás la miniatura al guardarse cada grabación, y podrás darle órdenes (`/add`, `/status`).
  Si no le respondes, revisa que el programa esté abierto y que el token siga siendo válido.
- *Webhook*: recibe un `POST` con `{"kind", "title", "message"}`. Útil para Discord/Slack con un proxy propio.

«Guardar y enviar prueba» comprueba que llega. Puedes elegir qué eventos avisan.


## Bot de Telegram (órdenes)

Además de avisarte, el bot **escucha** órdenes desde el chat configurado en Ajustes:

| Comando | Qué hace |
|---|---|
| `/list [página]` | Lista con estado; botones ◀ ▶ |
| `/user @usuario` | Detalle y botones (grabar, detener, pausar, últimas, quitar) |
| `/add @usuario` | Añade y comprueba al momento |
| `/del @usuario` | Quitar con confirmación (deja o borra los archivos) |
| `/pause @usuario` · `/resume @usuario` | Pausar o reanudar la vigilancia |
| `/auto on|off @usuario` | Grabación automática de ese usuario |
| `/record @usuario` | Grabar ahora |
| `/stop @usuario` | Detener la grabación en curso |
| `/last @usuario` | Últimas grabaciones, con miniatura |
| `/status` | Usuarios, en vivo, grabando, sesión, versiones y disco |
| `/id` | El id de ese chat (para configurarlo) |
| `/help` | Lista de comandos |
| `@usuario` (suelto) | Lo mismo que `/add @usuario` |

También acepta `/add usuario` sin arroba y los alias `/anadir`, `/lista`, `/usuario`, `/quitar`,
`/pausar`, `/reanudar`, `/grabar`, `/detener`, `/ultimas`, `/estado`, `/ayuda`.
Si mandas una palabra suelta que parece un usuario, el bot **no lo añade**: te propone
`/add palabra` para evitar altas por error.

- Solo el chat configurado puede dar órdenes. Si otro chat escribe, no puede tocar nada.
- `/start`, `/help` e `/id` responden a cualquier chat para que puedas copiar el id y configurarlo.
- El bot recuerda hasta dónde leyó: al reiniciar no repite órdenes antiguas.
- Funciona por *long polling*: no hay que abrir puertos ni exponer el panel a Internet.
- Solo responde **mientras el programa está abierto**. Si el PC está apagado o suspendido, no hay respuesta.
- En grupos, Telegram solo le muestra al bot los mensajes que empiezan por `/` (modo privacidad):
  para mandar un `@usuario` suelto, hazlo en el chat privado con el bot.


## Si algo falla

1. **Motor → Buscar y actualizar motor.** Funciona dentro del `.exe` sin Python: descarga las últimas versiones,
   comprueba que cargan **y que las funciones que usa el programa siguen existiendo**, y solo entonces reemplaza
   a las anteriores. Luego pulsa **Reiniciar**. Si falla, el motor anterior queda intacto.
2. **Diagnóstico → Descargar .txt** (o mejor el **paquete de soporte**, el ZIP completo) y compártelo al
   pedir ayuda. Las claves, la sessionid y las rutas de tu PC se ocultan solos, pero **revísalo antes de
   publicarlo**: incluye la lista de usuarios que vigilas. Ahí mismo puedes **ver las últimas líneas** de
   `errores.log` o `actividad.log` sin salir del programa y hacer una **copia de seguridad** de la base de datos.
3. Si el problema es de espacio, el panel lo dice en un aviso amarillo y en el chip **💾 GB libres**.
4. Para restaurar una copia: cierra el programa, guarda `datos/tkrec.db` por si acaso y copia el archivo
   elegido de `datos/backups/tkrec-...db` como `datos/tkrec.db`. Al abrirlo se migra solo si hace falta.

Los logs están en `datos/logs/` (`errores.log`, `actividad.log`, y `motor-pip.log` de la última actualización).


## Carpetas

- `grabaciones/<usuario>/` — los videos (cambiable en Ajustes).
- `datos/` — base de datos, logs, copias de seguridad (`datos/backups/`), fotos y motor actualizado
  (`motor-pyXY/`). Para volver al motor original del `.exe`, borra esa carpeta `motor-pyXY`.
- Si la carpeta del programa no permite escribir (por ejemplo `Archivos de programa`), se usa
  `%LOCALAPPDATA%\LiveVault\datos` y las grabaciones van junto a ella. Lo dice el registro
  (`datos/logs/errores.log`) al arrancar.
- Si la carpeta está dentro de **OneDrive**, el programa funciona, pero la sincronización puede
  bloquear la base de datos mientras graba y subir muchos GB. Mejor una carpeta local.


## Cómo graba

- Detecta el live y obtiene el stream con TikTokLive; si no lo consigue, prueba con yt-dlp como respaldo.
- Graba con ffmpeg copiando el stream tal cual (sin recodificar: no gasta CPU ni pierde calidad),
  directamente en **MP4 fragmentado** (o MKV si lo eliges).
- Si el stream se congela 60 s lo reinicia; si se corta pero el usuario sigue en vivo, continúa en otro archivo.
- Si el programa se cierra de golpe, al abrirlo recupera lo que quedó grabado y vuelve a apuntar en la base de datos
  los archivos que se quedaron sin registrar.
- Cada directo es una **sesión**: los cortes y reconexiones quedan agrupados y se pueden unir con 🔗 en «Directos».
- El análisis posterior avisa si la imagen se quedó congelada o si las marcas de tiempo son raras, y ofrece
  una copia reparada con 🛠 (recodifica; puede tardar en directos largos, con progreso en el panel).


## Secretos y privacidad

- La `sessionid`, el token de Telegram, las credenciales del proxy y el webhook se guardan **cifrados con DPAPI**
  (tu usuario de Windows). Si copias la carpeta `datos` a otro PC u otro usuario, tendrás que volver a escribirlos.
- El panel solo escucha en `127.0.0.1` (tu propio PC) y **exige una clave aleatoria** que se genera en tu
  equipo (`datos/panel.token`): otra web, otro programa u otro usuario de Windows no puede ver tus
  grabaciones ni cambiar los ajustes. Además se comprueban `Host`, `Origin` y `Sec-Fetch-Site` (anti-CSRF).
- Los reportes de diagnóstico y el paquete de soporte ocultan claves y sustituyen las rutas de tu PC por
  etiquetas, también en los logs.
- No hay telemetría: solo se conecta a TikTok y a los avisos que tú configures (ntfy, Telegram, webhook).


## Sin .exe (con Python instalado)

`Iniciar.bat` (Windows) o `./iniciar.sh`. Funciona igual, incluida la bandeja. Necesita Python 3.13 y
conexión a internet la primera vez para instalar las dependencias.


## Para desarrollar

Todo (entorno, pruebas, empaquetado del `.exe` y cómo publicar una versión) está en
[CONTRIBUTING.md](CONTRIBUTING.md).

- Estructura: `app.py` (motor, grabación, API), `ui/index.html` (panel), `tests/`, `LiveVault.spec`.
- **Versiones probadas**: `constraints.txt`. Los scripts instalan con `-c constraints.txt`.
- **Pendiente**: WebSocket de TikTokLive (fin de live/chat), dividir `app.py` en módulos y
  actualización automática de la app con firma. La actualización del motor sí está incluida.


## Notas

- Lives +18: hace falta tu `sessionid` de TikTok en Ajustes (se guarda cifrada en tu PC y no sale en los reportes).
- El panel solo es accesible desde tu propio PC.
- Graba únicamente lives a los que tu cuenta podría acceder. Respeta los derechos de autor y la privacidad de los
  creadores si vas a compartir o publicar lo grabado.