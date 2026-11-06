; Inno Setup script -> dist\TongChuan-<ver>-windows-x64-setup.exe
; Built by GitHub Actions (windows-latest) after PyInstaller produced dist\TongChuan\.
#define AppName "同传助手"
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{B6A5F1E2-6C1B-4C5D-9E52-7A1F0D3C2B11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=TongChuan contributors
DefaultDirName={autopf}\TongChuan
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=TongChuan-{#AppVersion}-windows-x64-setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\TongChuan.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
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
Source: "..\..\dist\TongChuan\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\TongChuan.exe"
Name: "{group}\{#AppName}（命令行模式）"; Filename: "{app}\TongChuanCLI.exe"; Parameters: "cli"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\TongChuan.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TongChuan.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
