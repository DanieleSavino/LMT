; Inno Setup script for LMT (Le Mans Telemetry).
; Turns the PyInstaller onedir build (dist\LMT\) into a normal
; Windows installer: Start Menu shortcut, optional desktop icon, uninstaller
; in "Add or Remove Programs" - the things non-technical users expect.
;
; Get Inno Setup (free): https://jrsoftware.org/isinfo.php
; Then: open this file in the Inno Setup Compiler and click "Compile",
; or from the command line:
;     iscc installer.iss
;
; Run this AFTER `pyinstaller lmt.spec` has produced
; dist\LMT\LMT.exe

#define MyAppName "LMT"
#define MyAppVersion "1.0.0"
#define MyAppExeName "LMT.exe"

[Setup]
AppId={{A6C8E1F0-3B2D-4E9A-9C1F-000LMT000001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=LMT_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; No admin rights needed - installs per-user, avoids UAC prompts
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; Pulls in the whole onedir build folder produced by PyInstaller
Source: "dist\LMT\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
