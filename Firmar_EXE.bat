@echo off
setlocal
chcp 65001 >nul
title Firmar LiveVault.exe
cd /d "%~dp0"

rem Firma de codigo con un certificado .pfx. NO se incluye ningun certificado:
rem consiguelo en una autoridad de certificacion (p. ej. Sectigo, DigiCert) o usa uno
rem de tu empresa. Windows SmartScreen solo deja de avisar con un certificado de confianza.
rem
rem Uso:
rem   set CERT_PFX=C:\ruta\certificado.pfx
rem   set CERT_PASS=la-contrasena
rem   Firmar_EXE.bat [ruta\al\exe]
rem
rem Tambien puedes usar un certificado del almacen de Windows cambiando las opciones
rem de signtool por /sha1 HUELLA /fd SHA256 /tr ... (mira "signtool sign /?").

if not defined CERT_PFX (
  echo Falta CERT_PFX: la ruta del certificado .pfx.
  pause
  exit /b 1
)
if not defined CERT_PASS (
  echo Falta CERT_PASS: la contrasena del certificado.
  pause
  exit /b 1
)
where signtool >nul 2>nul
if errorlevel 1 (
  echo No se encontro signtool. Instala el Windows SDK (herramientas de firma).
  pause
  exit /b 1
)

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=dist\LiveVault\LiveVault.exe"
if not exist "%TARGET%" (
  echo No existe "%TARGET%".
  echo Compila antes con Crear_EXE_onedir.bat.
  pause
  exit /b 1
)

echo Firmando "%TARGET%"...
signtool sign /f "%CERT_PFX%" /p "%CERT_PASS%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "%TARGET%"
if errorlevel 1 (
  echo Fallo la firma. Revisa el certificado y la contrasena.
  pause
  exit /b 1
)
signtool verify /pa "%TARGET%"
echo.
echo Listo. Si SmartScreen sigue avisando, el certificado debe ser de confianza publica
echo y la firma debe incluir sello de tiempo (este script ya lo pide).
pause
