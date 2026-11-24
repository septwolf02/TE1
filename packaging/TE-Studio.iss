#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif

[Setup]
AppId={{D1D273CB-47D1-45A9-907A-1B6B3698CB91}
AppName=TE Studio
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\TE Studio
DefaultGroupName=TE Studio
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=TE-Studio-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\TE Studio.exe
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\TE Studio"; Filename: "{app}\TE Studio.exe"
Name: "{userdesktop}\TE Studio"; Filename: "{app}\TE Studio.exe"

[Run]
Filename: "{app}\TE Studio.exe"; Description: "Mở TE Studio"; Flags: nowait postinstall skipifsilent
