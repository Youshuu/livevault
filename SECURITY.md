# Política de seguridad

## Versiones con soporte

Solo la **última versión publicada** en [Releases](../../releases/latest) recibe correcciones.
Si usas una copia vieja, actualiza antes de reportar: muchos fallos de detección los arregla el
propio actualizador de motor (**Motor → Buscar y actualizar motor**).

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Usa la pestaña **Security → Report a vulnerability** del repositorio
(privado, solo lo ven los mantenedores). Si no está disponible, escribe al mantenedor indicado en el
perfil de GitHub del proyecto.

Incluye, si puedes:

- versión (`LiveVault.exe --version`) y Windows;
- qué consigue un atacante y qué necesita para ello;
- pasos para reproducirlo;
- si afecta a tus datos o solo los expone.

Respondemos lo antes posible y, si el fallo es real, se publica la corrección y se te acredita si
quieres. No se promete recompensa económica.

## Qué se considera una vulnerabilidad

Este programa se ejecuta en el PC del usuario y trata datos sensibles (cookie de TikTok, token de
Telegram, grabaciones). Interesan especialmente:

- **fuga de secretos** (`sessionid`, tokens, webhooks, proxy) al panel, a los logs, al reporte o al
  paquete de soporte;
- **acceso al panel desde fuera** del propio equipo, o desde otra web/proceso sin la clave;
- **ejecución de código** o escritura de archivos arbitraria (por ejemplo, a través del motor que se
  descarga o de una ruta configurable);
- **path traversal** al servir grabaciones, miniaturas, avatares o logs;
- **SSRF** (que el programa haga peticiones a la red interna por una URL de TikTok manipulada).

## Lo que ya está mitigado (para que no lo reportes como nuevo)

- El panel escucha **solo en `127.0.0.1`** y exige una **clave aleatoria por instalación**
  (`datos/panel.token`, servida solo dentro del panel). Además comprueba `Host`, `Origin` y
  `Sec-Fetch-Site` contra CSRF y DNS rebinding, y no manda cabeceras CORS.
- Los secretos se guardan **cifrados con DPAPI** (Windows). En Linux/macOS no hay cifrado del
  sistema y la app lo avisa en el log.
- Los logs, el reporte y el paquete de soporte **redactan** claves y **sustituyen las rutas locales**
  por etiquetas.
- Las imágenes de avatar solo se descargan por HTTPS de dominios de TikTok (evita SSRF).
- No se ejecuta nada a través del shell: todos los procesos usan listas de argumentos.
- Las descargas del motor usan **solo ruedas binarias** de PyPI por HTTPS.

## Riesgos conocidos y aceptados

- **El `.exe` no está firmado** y Windows SmartScreen avisará. Verificar la descarga con el
  `SHA256SUMS.txt` del release es la mitigación disponible.
- **Actualizaciones**: el programa se actualiza solo desde la Release oficial de este repositorio.
  Solo acepta URLs de `github.com`, descarga el `.exe` y **comprueba el SHA-256** que publica el
  propio CI en `SHA256SUMS.txt`; si no coincide, no instala nada. Esto protege frente a descargas
  corruptas o manipulación del archivo, pero **no** frente a que la cuenta de GitHub del proyecto se
  vea comprometida: quien controle la cuenta puede publicar una Release y su SHA-256. Si te preocupa
  ese escenario, desactiva **Ajustes → Actualizaciones** y compila desde el código fuente (que es lo
  que hace el CI, con `LiveVault.spec`).
- **El actualizador de motor instala versiones nuevas de TikTokLive y ffmpeg desde PyPI** cuando el
  usuario lo pide. Es una decisión de diseño (TikTok rompe la detección cada pocas semanas). Por
  defecto no se actualiza solo al arrancar. Un paquete comprometido en PyPI podría ejecutar código:
  es el mismo riesgo que tiene `pip install`.
- Cualquier proceso **del mismo usuario de Windows** puede hacer lo mismo que el usuario. La clave
  del panel protege frente a otros usuarios del equipo y frente a páginas web, no frente a malware
  que ya se ejecuta con tu cuenta.
- Si instalas el programa en una carpeta escribible por todos, la carpeta `datos` también lo será.
