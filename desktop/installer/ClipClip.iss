; ClipClip.ai Windows x64 Per-User Installer (Inno Setup 7)
; Installs program binaries into %LOCALAPPDATA%\Programs\ClipClip.ai.
; User projects, models, and backend.env live separately in %LOCALAPPDATA%\ClipClip.ai
; and are never removed during upgrade or uninstall.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppDistDir
  #error "AppDistDir must be defined via /DAppDistDir=..."
#endif
#ifndef OutputDirPath
  #error "OutputDirPath must be defined via /DOutputDirPath=..."
#endif

[Setup]
AppId={{7B6F9C4E-8A2D-4C1B-9F53-2E4D1A8C6B09}
AppName=ClipClip.ai
AppVersion={#AppVersion}
AppVerName=ClipClip.ai {#AppVersion}
AppPublisher=ClipClip.ai
DefaultDirName={autopf}\ClipClip.ai
DefaultGroupName=ClipClip.ai
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDirPath}
OutputBaseFilename=ClipClip.ai-Setup-{#AppVersion}-x64
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\ClipClip.ai.exe
UninstallDisplayName=ClipClip.ai
WizardStyle=modern
WizardImageFile=..\assets\wizard-sidebar.bmp
WizardSmallImageFile=..\assets\wizard-small.bmp
Compression=lzma2/normal
SolidCompression=yes
CloseApplications=yes
CloseApplicationsFilter=ClipClip.ai.exe,PODCUTBackend.exe
RestartApplications=no
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany=ClipClip.ai
VersionInfoDescription=ClipClip.ai — Studio Klip Podcast Lokal Installer
VersionInfoProductName=ClipClip.ai
VersionInfoProductVersion={#AppVersion}.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#AppDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ClipClip.ai"; Filename: "{app}\ClipClip.ai.exe"; IconFilename: "{app}\ClipClip.ai.exe"; Comment: "ClipClip.ai — Studio Klip Podcast Lokal"
Name: "{autodesktop}\ClipClip.ai"; Filename: "{app}\ClipClip.ai.exe"; IconFilename: "{app}\ClipClip.ai.exe"; Tasks: desktopicon; Comment: "ClipClip.ai — Studio Klip Podcast Lokal"

[Run]
Filename: "{app}\ClipClip.ai.exe"; Description: "{cm:LaunchProgram,ClipClip.ai}"; Flags: nowait postinstall skipifsilent
