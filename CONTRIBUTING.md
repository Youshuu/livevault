# Contribuir a LiveVault

Gracias por el interés. El proyecto es pequeño y se mantiene solo, así que lo más útil son los
**informes de fallos reproducibles** y las mejoras pequeñas y bien probadas.

## Antes de abrir un issue

1. Mira la versión que usas: chip de la cabecera del panel o `LiveVault.exe --version`.
2. Prueba **Motor → Buscar y actualizar motor** (TikTok cambia su API a menudo).
3. Adjunta **Diagnóstico → Descargar .txt**. Oculta solo, pero revísalo antes de publicarlo.
4. No pegues `sessionid`, tokens de Telegram, webhooks ni capturas con tu nombre de usuario de Windows.

## Entorno de desarrollo

Requisitos: Python **3.13** (es la versión con la que se prueba y se compila) y Windows para probar
la bandeja, el autostart y la grabación.

```powershell
git clone <url-del-repo>
cd livevault
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-build.txt -c constraints.txt
.venv\Scripts\python app.py
```

Estructura:

| Archivo | Qué es |
|---|---|
| `app.py` | Todo el programa: motor, grabación, API local, bandeja y bot de Telegram |
| `ui/index.html` | Panel web (sin dependencias externas: se sirve desde el propio programa) |
| `tests/` | Pruebas sin red y prueba de humo de la API |
| `LiveVault.spec` | Definición del `.exe` (la usan el CI y `Crear_EXE.bat`) |
| `.github/workflows/` | `tests.yml` (cada push) y `release.yml` (tags) |

## Pruebas

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python tests\smoke_api.py
.venv\Scripts\python -m ruff check .
```

- Las pruebas **no usan internet** y escriben en `tests/.test-data*` (ignorado por git).
- Las de grabación real se saltan solas si no hay ffmpeg.
- `smoke_api.py` arranca la aplicación de verdad, recorre la API y la cierra.

## Estilo

- Español sin tildes en los comentarios y mensajes del código (para que no falle nada con la consola
  de Windows); las cadenas que ve el usuario sí llevan tildes.
- Nada de dependencias nuevas si se puede resolver con la biblioteca estándar: cada paquete extra
  engorda el `.exe` y aumenta la superficie de riesgo.
- Todo lo que el usuario pueda configurar debe quedar **en Ajustes**, no en el código.
- Nunca escribir rutas absolutas ni nombres personales en el código, los tests o la documentación.

## Empaquetar y probar el `.exe` en local

```powershell
.\Crear_EXE.bat
```

Genera `LiveVault.exe` en la carpeta actual con el mismo `LiveVault.spec` que usa GitHub. Después,
la prueba que de verdad importa (detecta errores que solo aparecen dentro del binario):

```powershell
New-Item -ItemType Directory -Force $env:TEMP\motor | Out-Null
.\LiveVault.exe --selftest $env:TEMP\motor $env:TEMP\selftest.txt
Get-Content $env:TEMP\selftest.txt   # debe empezar por "OK <versión> ..."
```

Para una carpeta en lugar de un archivo único (instalador): `Crear_EXE_onedir.bat`.

## Publicar una versión

El `.exe` que descarga la gente **lo compila GitHub Actions**, no tu PC: así el binario no lleva tus
rutas personales dentro y sale siempre igual. El flujo es:

1. Sube la versión en estos sitios (el CI falla si no coinciden con el tag):
   - `APP_VERSION` en `app.py`
   - `MyAppVersion` en `instalador.iss`
   - título de `LEEME.md` y una sección nueva en `CHANGELOG.md` (`# Cambios de la X.Y.Z`)
2. Pasa las pruebas en local y `git push` a `main`.
3. Crea el tag y súbelo:

   ```powershell
   git tag -a v1.8.0 -m "LiveVault 1.8.0"
   git push origin v1.8.0
   ```

4. El workflow `release` compila, prueba el `.exe` empaquetado y publica la Release con
   `LiveVault-<versión>.exe` y `SHA256SUMS.txt`.
5. Comprueba la página del release y descarga el `.exe` como lo haría un usuario (Edge, SmartScreen,
   carpeta nueva) antes de darlo por bueno.

> **Las versiones no se reutilizan.** La app avisa y se actualiza sola cuando la Release tiene una
> versión **mayor** que la suya; si publicas con el mismo número, nadie recibirá el aviso. Y no borres
> nunca el `.exe` ni el `SHA256SUMS.txt` de una Release publicada: son lo que descargan los programas
> que aún no se han actualizado.

Si algo falla: pestaña **Actions** → el run → los logs (y `build.log`, que se guarda como artefacto).

## Seguridad

No abras issues públicos con vulnerabilidades: sigue [SECURITY.md](SECURITY.md).
