; Inno Setup script for Squadrone Drone Coder.
;
; Build it through .\build.ps1, which runs the tests, produces dist\comp1 with
; PyInstaller, passes the version in as /DAppVersion, and writes SHA256SUMS.txt
; next to the installer. Compiling this file on its own works too, as long as
; dist\comp1 already exists.
;
; Three decisions here are load-bearing:
;
;   PrivilegesRequired=admin — because the drone's camera needs a firewall
;   rule. Video arrives on UDP 11111 unasked, and a managed laptop on the
;   Tello's Wi-Fi (a Public network) drops it; the app's own hole-punch gets
;   through some firewalls, not all (docs/plans/2026-10-12-tello-udp-client.md).
;   Only an elevated installer can add the rule, so this one asks, and installs
;   for every user under Program Files. Up to 0.2.1 it was
;   PrivilegesRequired=lowest, installed per-user under %LOCALAPPDATA%\Programs:
;   PrepareToInstall below removes that copy so a machine never ends up with two.
;
;   The program itself never runs elevated. It keeps the user's settings in
;   *their* %LOCALAPPDATA%, so every [Run] entry that starts it carries
;   runasoriginaluser.
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
; Named, so the uninstaller (and a reinstall) can find exactly this rule again.
#define FirewallRule "Squadrone Drone Coder - drone camera (UDP 11111)"

[Setup]
AppId={{4C0F5A93-2E7B-4C1D-9E3A-8B7C6D5E4F21}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Squadrone Australia
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\comp1
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
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
; Let the drone's video in: UDP 11111, for this program only, on every network
; profile (Windows files the Tello's Wi-Fi as Public). Deleted first so that a
; reinstall or an update replaces the rule rather than piling up copies; the
; delete fails, harmlessly, the first time. A third-party firewall (Kaspersky,
; Bitdefender, ...) ignores Windows rules and needs its own.
Filename: "{sys}\netsh.exe"; \
  Parameters: "advfirewall firewall delete rule name=""{#FirewallRule}"""; \
  Flags: runhidden waituntilterminated; \
  StatusMsg: "Allowing the drone's camera through the firewall..."
Filename: "{sys}\netsh.exe"; \
  Parameters: "advfirewall firewall add rule name=""{#FirewallRule}"" dir=in action=allow protocol=UDP localport=11111 program=""{app}\{#AppExeName}"" profile=any"; \
  Flags: runhidden waituntilterminated; \
  StatusMsg: "Allowing the drone's camera through the firewall..."
Filename: "{app}\{#AppExeName}"; Description: "Start {#AppName}"; \
  Flags: nowait postinstall skipifsilent runasoriginaluser
; The in-app updater's path. It runs Setup silently with /FORCECLOSEAPPLICATIONS
; because the Restart Manager cannot politely close a window-less server
; process — and a force-closed application is not one the Restart Manager will
; bring back, so the program is started again from here instead.
Filename: "{app}\{#AppExeName}"; Flags: nowait postinstall runasoriginaluser; Check: RelaunchRequested

[UninstallRun]
Filename: "{sys}\netsh.exe"; \
  Parameters: "advfirewall firewall delete rule name=""{#FirewallRule}"""; \
  Flags: runhidden waituntilterminated; RunOnceId: "DeleteCameraFirewallRule"

; Nothing in [UninstallDelete]: %LOCALAPPDATA%\comp1 holds the venue's colour
; calibration and saved settings, and those are worth more than a tidy
; uninstall — a reinstall picks them straight back up.

[Code]
const
  { Where a per-user install (0.2.1 and earlier) registered its uninstaller. }
  OldPerUserKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{4C0F5A93-2E7B-4C1D-9E3A-8B7C6D5E4F21}_is1';

function RelaunchRequested: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RELAUNCH') = 0 then
      Result := True;
end;

{ Remove a per-user copy left by 0.2.1 or earlier. It has the same AppId, but
  its uninstall entry is under HKCU, where an admin install never looks, so
  without this the machine keeps both: two Start Menu entries, and the old copy
  fighting the new one for port 8765.

  Only the current account's copy can be seen. When IT elevates with an account
  of their own, a student's old copy is out of reach and stays put. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Uninstaller: String;
  Code: Integer;
begin
  Result := '';
  if not RegQueryStringValue(HKCU, OldPerUserKey, 'UninstallString', Uninstaller) then
    exit;
  Uninstaller := RemoveQuotes(Uninstaller);
  if not FileExists(Uninstaller) then
    exit;
  { The old copy may be the very program that started this update. Closing it
    is safe for the same reason the updater's /FORCECLOSEAPPLICATIONS is: an
    update is refused while anything is flying. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExeName}', '',
       SW_HIDE, ewWaitUntilTerminated, Code);
  { Its settings live in %LOCALAPPDATA%\comp1, which no uninstall touches, so
    the new copy picks them straight back up. A failure here is not fatal: the
    worst case is the duplicate this tries to prevent. }
  Exec(Uninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '',
       SW_HIDE, ewWaitUntilTerminated, Code);
end;
