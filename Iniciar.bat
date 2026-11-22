@echo off
chcp 65001 >nul
title LiveVault
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (where python >nul 2>nul && set PY=python)
if "%PY%"=="" (
  echo No se encontro Python. Instalalo desde https://www.python.org/downloads/
  echo y marca la casilla "Add python.exe to PATH" durante la instalacion.
  pause
  exit /b 1
)

rem wheels propios (paquetes que pueden faltar en PyPI o venian empaquetados)
set "LINKS="
if exist "wheels" set "LINKS=--find-links wheels"
set "CONS="
if exist "constraints.txt" set "CONS=-c constraints.txt"

if not exist ".venv\Scripts\python.exe" (
  echo Primera vez: preparando el programa, esto tarda un minuto...
  %PY% -m venv .venv
  if errorlevel 1 ( echo No se pudo crear el entorno. & pause & exit /b 1 )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt %LINKS% %CONS% --disable-pip-version-check
  if errorlevel 1 ( echo Fallo la instalacion. Revisa tu conexion a internet. & pause & exit /b 1 )
)

".venv\Scripts\python.exe" -c "import pystray, PIL" >nul 2>nul
if errorlevel 1 (
  echo Instalando la bandeja del sistema...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt %LINKS% %CONS% --disable-pip-version-check -q
)

echo Iniciando... se abrira el navegador. NO cierres esta ventana mientras quieras grabar.
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
