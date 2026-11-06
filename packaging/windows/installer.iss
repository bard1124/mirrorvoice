; Inno Setup script -> dist\MirrorVoice-<ver>-windows-x64-setup.exe
; Built by GitHub Actions (windows-latest) after PyInstaller produced dist\MirrorVoice\.
#define AppName "镜声"
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{B6A5F1E2-6C1B-4C5D-9E52-7A1F0D3C2B11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=MirrorVoice contributors
DefaultDirName={autopf}\MirrorVoice
; always offer the destination page (the default "auto" hides it on upgrades); silent one-click
; updates still reuse the existing folder
DisableDirPage=no
UsePreviousAppDir=yes
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=MirrorVoice-{#AppVersion}-windows-x64-setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\MirrorVoice.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
LicenseFile=..\..\LICENSE

[Languages]
; Chinese is official in newer Inno Setup 6 releases and "Unofficial" in older ones
#if FileExists(CompilerPath + "Languages\ChineseSimplified.isl")
Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#elif FileExists(CompilerPath + "Languages\Unofficial\ChineseSimplified.isl")
Name: "chs"; MessagesFile: "compiler:Languages\Unofficial\ChineseSimplified.isl"
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\MirrorVoice\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\MirrorVoice.exe"; AppUserModelID: "io.github.mirrorvoice"
Name: "{group}\{#AppName}（命令行模式）"; Filename: "{app}\MirrorVoiceCLI.exe"; Parameters: "cli"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\MirrorVoice.exe"; Tasks: desktopicon; AppUserModelID: "io.github.mirrorvoice"

[Run]
Filename: "{app}\MirrorVoice.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; one-click update runs the installer with /SILENT: relaunch the app afterwards
Filename: "{app}\MirrorVoice.exe"; Flags: nowait skipifnotsilent
