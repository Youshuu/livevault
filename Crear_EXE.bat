@echo off
setlocal
chcp 65001 >nul
title Crear LiveVault.exe
cd /d "%~dp0"

echo.
echo  ==========================================================
echo    Creando LiveVault.exe   (tarda de 3 a 6 minutos)
echo  ==========================================================
echo.
echo  Para el release publico no hace falta esto: el .exe se compila
echo  solo en GitHub (Actions ^> release) y se descarga desde Releases.
echo  Este script es para probar el empaquetado en tu PC.
echo.

rem --- el programa no puede estar abierto: no se puede reemplazar un .exe en uso
tasklist /fi "imagename eq LiveVault.exe" 2>nul | find /i "LiveVault.exe" >nul
if not errorlevel 1 (
  echo LiveVault.exe esta abierto. Cierralo primero:
  echo clic derecho en su icono de la bandeja ^(junto al reloj^) ^> Salir.
  echo Luego vuelve a ejecutar este archivo.
  pause
  exit /b 1
)

rem --- buscar Python (se prefiere 3.13, la version con la que se prueba en CI)
set "PY="
py -3.13 -c "pass" >nul 2>nul && set "PY=py -3.13"
if not defined PY (py -3.12 -c "pass" >nul 2>nul && set "PY=py -3.12")
if not defined PY (py -3 -c "pass" >nul 2>nul && set "PY=py -3")
if not defined PY (python -c "pass" >nul 2>nul && set "PY=python")
if not defined PY (
  echo No se encontro Python. Instalalo desde https://www.python.org/downloads/
  echo y marca la casilla "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Usando: %PY%

if not exist ".buildvenv\Scripts\python.exe" (
  echo Preparando entorno de compilacion...
  %PY% -m venv .buildvenv
  if errorlevel 1 (
    echo No se pudo crear el entorno de compilacion.
    pause
    exit /b 1
  )
)
set "VPY=.buildvenv\Scripts\python.exe"

echo Instalando dependencias...
"%VPY%" -m pip install -r requirements.txt -r requirements-build.txt -c constraints.txt --disable-pip-version-check
if errorlevel 1 (
  echo Fallo la instalacion de dependencias. Revisa tu conexion a internet.
  pause
  exit /b 1
)

echo Compilando...
"%VPY%" -m PyInstaller --noconfirm --clean LiveVault.spec
if errorlevel 1 (
  echo.
  echo La compilacion fallo. Copia el texto de arriba y envialo.
  pause
  exit /b 1
)

if not exist "dist\LiveVault.exe" (
  echo No se genero el .exe. Copia el texto de arriba y envialo.
  pause
  exit /b 1
)
copy /Y "dist\LiveVault.exe" "LiveVault.exe" >nul
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q LiveVault.spec.bak 2>nul

echo.
echo  ==========================================================
echo    LISTO:  LiveVault.exe  (en esta misma carpeta)
echo  ==========================================================
echo.
echo  - Dejalo en esta carpeta para conservar usuarios, ajustes y grabaciones.
echo  - La primera vez Windows puede mostrar "Windows protegio su PC":
echo    pulsa "Mas informacion" ^> "Ejecutar de todas formas".
echo.
pause
