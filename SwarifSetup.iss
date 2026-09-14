#define MyAppName "Swarif"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Swarif"
#define MyAppExeName "Swarif.exe"

[Setup]
AppId={{BCEBC1EF-6A7D-4EF1-9521-AF3D48AB5344}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Swarif
DefaultGroupName=Swarif
OutputDir=dist
OutputBaseFilename=SwarifSetup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible arm64
ArchitecturesInstallIn64BitMode=x64compatible arm64
WizardStyle=modern
SetupIconFile=static\Swarif_Logo_BG.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "dist\Swarif.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Swarif"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Swarif"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Swarif"; Flags: nowait postinstall skipifsilent
