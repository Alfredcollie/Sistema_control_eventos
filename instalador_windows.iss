; Instalador de Windows (Inno Setup) para Control de Eventos
; Se compila en el CI: ISCC.exe instalador_windows.iss
;
; La versión se puede sobrescribir desde el workflow con:  /DMyAppVersion=x.y.z

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "Control de Eventos"
#define MyAppPublisher "Collie Software"
#define MyAppExeName "Instalador-ControlEventos-Windows.exe"

[Setup]
AppId={{EAEA72A4-F091-4CF5-971D-4CA034F63AB4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=ControlEventos-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=Ico_Collie_Software.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "dist\Instalador-ControlEventos-Windows\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Iniciar {#MyAppName}"; Flags: nowait postinstall skipifsilent
