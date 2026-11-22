@echo off
setlocal
chcp 65001 >nul
title Crear LiveVault (carpeta, --onedir)
cd /d "%~dp0"

echo.
echo  ==========================================================
echo    Creando dist\LiveVault\  (onedir, para instalador)
echo  ==========================================================
echo.

tasklist /fi "imagename eq LiveVault.exe" 2>nul | find /i "LiveVault.exe" >nul
if not errorlevel 1 (
  echo LiveVault.exe esta abierto. Cierralo primero.
  pause
  exit /b 1
)

set "PY="
py -3.13 -c "pass" >nul 2>nul && set "PY=py -3.13"
if not defined PY (py -3.12 -c "pass" >nul 2>nul && set "PY=py -3.12")
if not defined PY (py -3 -c "pass" >nul 2>nul && set "PY=py -3")
if not defined PY (python -c "pass" >nul 2>nul && set "PY=python")
if not defined PY (
  echo No se encontro Python. Instalalo desde https://www.python.org/downloads/
  pause
  exit /b 1
)
echo Usando: %PY%

if not exist ".buildvenv\Scripts\python.exe" (
  echo Preparando entorno de compilacion...
  %PY% -m venv .buildvenv
  if errorlevel 1 ( echo No se pudo crear el entorno. & pause & exit /b 1 )
)
set "VPY=.buildvenv\Scripts\python.exe"

echo Instalando dependencias...
"%VPY%" -m pip install -r requirements.txt -r requirements-build.txt -c constraints.txt --disable-pip-version-check
if errorlevel 1 ( echo Fallo la instalacion. & pause & exit /b 1 )

echo Compilando...
set "LIVEVAULT_ONEDIR=1"
"%VPY%" -m PyInstaller --noconfirm --clean LiveVault.spec
if errorlevel 1 ( echo La compilacion fallo. & pause & exit /b 1 )

if not exist "dist\LiveVault\LiveVault.exe" (
  echo No se genero la carpeta. & pause & exit /b 1
)
copy /Y "LEEME.md" "dist\LiveVault\LEEME.md" >nul
copy /Y "CHANGELOG.md" "dist\LiveVault\CHANGELOG.md" >nul

echo.
echo  LISTO: dist\LiveVault\
echo  - Para instalarlo: abre instalador.iss con Inno Setup y compila.
echo  - O copia esa carpeta donde quieras y ejecuta LiveVault.exe.
echo.
pause
