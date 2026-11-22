# Avisos de terceros

LiveVault se distribuye bajo licencia **MIT** (ver [LICENSE](LICENSE)). El `.exe` publicado en
Releases **incluye** software de terceros, cada uno con su propia licencia. Esta página resume los
componentes principales; las licencias completas están en los paquetes originales.

| Componente | Uso | Licencia |
|---|---|---|
| [Python](https://www.python.org/) | intérprete | PSF License |
| [PyInstaller](https://pyinstaller.org/) | empaquetado del `.exe` | GPL-2.0 **con excepción** que permite distribuir programas empaquetados con cualquier licencia |
| [TikTokLive](https://github.com/isaackogan/TikTokLive) | detección de directos | MIT |
| [TikTokLiveProto](https://github.com/isaackogan/TikTokLiveProto) | protocolo de TikTok | MIT |
| [betterproto2](https://github.com/nicolas-van/betterproto2) | serialización protobuf | MIT |
| [protobuf](https://github.com/protocolbuffers/protobuf) | serialización | BSD-3-Clause |
| [httpx](https://www.python-httpx.org/) | HTTP | BSD-3-Clause |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | empaqueta ffmpeg | BSD-2-Clause |
| [FFmpeg](https://ffmpeg.org/) | grabación del stream | LGPL-2.1+ o GPL según la compilación incluida (ver abajo) |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | respaldo para obtener la URL | Unlicense |
| [pystray](https://github.com/moses-palmer/pystray) | icono de la bandeja | LGPL-3.0 |
| [Pillow](https://python-pillow.org/) | iconos e imágenes | MIT-CMU (HPND) |
| [pip](https://pip.pypa.io/) | actualización del motor desde el programa | MIT |
| [protobuf3-to-dict](https://pypi.org/project/protobuf3-to-dict/) | utilidades protobuf | dominio público |

## FFmpeg

El binario de FFmpeg que va dentro del `.exe` lo aporta `imageio-ffmpeg` y es una compilación de
terceros cuya licencia depende de las opciones con las que se compiló (habitualmente
**LGPL-2.1-or-later**, y GPL si incluye componentes como libx264). FFmpeg se ejecuta como **proceso
aparte**, no se enlaza con el programa.

Consulta el archivo de licencia que acompaña a la distribución de `imageio-ffmpeg` para la
compilación exacta, o sustituye el binario por uno propio con `Ajustes → Avanzado → ruta de ffmpeg`.

## pystray (LGPL-3.0)

`pystray` es LGPL-3.0. Se incluye **sin modificaciones** dentro del `.exe`. Para cumplir la licencia
puedes:

- obtener su código fuente en el enlace de la tabla y recompilar el programa (el repositorio incluye
  todo lo necesario: `LiveVault.spec` y `requirements.txt`), o
- ejecutar LiveVault desde el código fuente (`python app.py`), sustituyendo `pystray` por tu propia
  versión.

LiveVault no modifica `pystray` ni enlaza estáticamente con él en ningún punto más allá del
empaquetado que hace PyInstaller.

## Marcas

TikTok es una marca registrada de ByteDance Ltd. LiveVault **no está afiliado, patrocinado ni
respaldado** por TikTok ni por ByteDance. El nombre del programa no incluye la marca.
