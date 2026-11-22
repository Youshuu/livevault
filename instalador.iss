; Instalador OPCIONAL de LiveVault. Requiere Inno Setup 6 (https://jrsoftware.org/isinfo.php).
;   1) Crear_EXE_onedir.bat  ->  dist\LiveVault\
;   2) Abrir este archivo con Inno Setup y compilar (F9).
; Genera dist\LiveVault_Setup_<version>.exe
;
; La descarga normal para el publico es el .exe unico de la seccion Releases; el instalador
; solo aporta acceso directo en el menu Inicio y desinstalador.

#ifndef MyAppVersion
  #define MyAppVersion "1.8.0"
#endif

#define MyAppName "LiveVault"
#define MyAppExeName "LiveVault.exe"

[Setup]
AppId={{B7C4A9E2-3D6F-4A1B-9E5C-7F2D8A6B1C34}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=LiveVault
DefaultDirName={localappdata}\Programs\LiveVault
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=LiveVault_Setup_{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "dist\LiveVault\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent
