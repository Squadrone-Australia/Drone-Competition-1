; Inno Setup script for Squadrone Drone Coder.
;
; Build it through .\build.ps1, which runs the tests, produces dist\comp1 with
; PyInstaller, passes the version in as /DAppVersion, and writes SHA256SUMS.txt
; next to the installer. Compiling this file on its own works too, as long as
; dist\comp1 already exists.
;
; Two decisions here are load-bearing:
;
;   PrivilegesRequired=lowest — a school laptop rarely gives a teacher admin
;   rights, and asking for them turns an installer into a support ticket. This
;   installs per-user under %LOCALAPPDATA%\Programs.
;
;   AppId is a fixed GUID — it is how Windows knows version 0.2 replaces
;   version 0.1 rather than installing beside it. Never change it. Changing it
;   strands every existing install, and the in-app updater would then leave two
;   copies on the machine.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Squadrone Drone Coder"
#define AppExeName "comp1.exe"

[Setup]
AppId={{4C0F5A93-2E7B-4C1D-9E3A-8B7C6D5E4F21}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Squadrone Australia
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\comp1
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=comp1-Setup-{#AppVersion}
SetupIconFile=comp1.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The in-app updater runs this silently while the program itself is open, so the
; installer has to be allowed to shut it down and put it back.
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; The whole PyInstaller onedir tree, exe and _internal alike.
Source: "..\dist\comp1\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Example configs, so an operator re-tuning on site has something to copy.
Source: "..\vision_config.example.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\flight_config.example.toml"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Start {#AppName}"; \
  Flags: nowait postinstall skipifsilent
; The in-app updater's path. It runs Setup silently with /FORCECLOSEAPPLICATIONS
; because the Restart Manager cannot politely close a window-less server
; process — and a force-closed application is not one the Restart Manager will
; bring back, so the program is started again from here instead.
Filename: "{app}\{#AppExeName}"; Flags: nowait postinstall; Check: RelaunchRequested

; Nothing in [UninstallDelete]: %LOCALAPPDATA%\comp1 holds the venue's colour
; calibration and saved settings, and those are worth more than a tidy
; uninstall — a reinstall picks them straight back up.

[Code]
function RelaunchRequested: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RELAUNCH') = 0 then
      Result := True;
end;

