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
;   installs per-user under %LOCALAPPDATA%\Programs. The in-app updater runs this
;   same installer silently, so an elevation prompt here would also break updates
;   on a managed laptop. 0.2.2 briefly asked for admin in order to add a firewall
;   rule for the drone's camera (UDP 11111); it was taken back out. Do not add
;   netsh, [Run] rules or anything else that needs elevation: the video path has
;   its own no-admin fallback (see "Flying must not need a firewall rule" in
;   CLAUDE.md), and a blocked camera is something IT fixes, not the installer.
;
;   AppId is a fixed GUID — it is how Windows knows version 0.2 replaces
;   version 0.1 rather than installing beside it. Never change it. Changing it
;   strands every existing install, and the in-app updater would then leave two
;   copies on the machine.
;
; And one that is not load-bearing, by design — the WebView2 runtime. The native
; window needs Microsoft's WebView2 runtime; without it the program opens in the
; browser instead and works the same (comp1/window.py). Windows 11 ships the
; runtime and nearly every Windows 10 machine has it, so on most machines the
; "webview2" task is never even shown. Where it is missing, Setup offers to fetch
; Microsoft's Evergreen Bootstrapper and run it. Because this Setup is never
; elevated, neither is the bootstrapper, and Microsoft documents that a
; non-elevated run installs the runtime per-user — no administrator, no UAC
; prompt, so the rule above still holds. Every way that can fail (offline, a
; proxy, a blocked domain, a bad signature, a policy refusal) ends in "it opens
; in the browser", never in a failed install. It is skipped entirely when Setup
; runs silently: that is the in-app updater, and an update must not turn into a
; download of the whole runtime that nobody asked for at a venue.

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
; Only offered on a machine that lacks the runtime; see the header.
Name: "webview2"; \
  Description: "Download and install Microsoft WebView2, so Drone Coder opens in its own window (without it, it opens in your web browser)"; \
  GroupDescription: "Window:"; Check: WebView2Missing

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
const
  // Microsoft's client GUID for the Evergreen WebView2 runtime. Its `pv` value
  // is the installed version, per learn.microsoft.com/microsoft-edge/webview2/
  // concepts/distribution ("Detect if a WebView2 Runtime is already installed").
  WebView2ClientKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  // The Evergreen Bootstrapper: about 2 MB, it fetches the matching runtime itself.
  WebView2BootstrapperUrl = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  WebView2Bootstrapper = 'MicrosoftEdgeWebview2Setup.exe';

var
  DownloadPage: TDownloadWizardPage;
  WebView2Downloaded: Boolean;

function RelaunchRequested: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RELAUNCH') = 0 then
      Result := True;
end;

function WebView2VersionAt(RootKey: Integer): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, WebView2ClientKey, 'pv', Version)
    and (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2Installed: Boolean;
begin
  // Read-only, so no administrator is needed. HKLM32 is the WOW6432Node view on
  // 64-bit Windows and plain HKLM on 32-bit — the two per-machine locations —
  // and HKCU is a per-user install, which is the kind this Setup itself makes.
  Result := WebView2VersionAt(HKLM32) or WebView2VersionAt(HKCU);
end;

function WebView2Missing: Boolean;
begin
  Result := not WebView2Installed;
end;

function WantWebView2: Boolean;
begin
  // WizardSilent is the in-app updater (see the header). UsePreviousTasks would
  // otherwise carry a tick from the first install into every silent update.
  Result := (not WizardSilent) and WizardIsTaskSelected('webview2') and WebView2Missing;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage('Downloading Microsoft WebView2',
    'Drone Coder''s window needs it. This usually takes under a minute.', nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;  // whatever happens below, the install itself goes ahead
  if (CurPageID <> wpReady) or not WantWebView2 then
    Exit;
  DownloadPage.Clear;
  DownloadPage.Add(WebView2BootstrapperUrl, WebView2Bootstrapper, '');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      WebView2Downloaded := True;
    except
      // Offline, a proxy, a blocked domain, or Cancel: all of them mean
      // "opens in the browser", never "Drone Coder did not install".
      Log('WebView2 bootstrapper not downloaded: ' + GetExceptionMessage);
    end;
  finally
    DownloadPage.Hide;
  end;
end;

function SignedByMicrosoft(const FileName: String): Boolean;
var
  Quoted: String;
  ResultCode: Integer;
begin
  // A download is only run if Windows itself vouches for it: a valid Authenticode
  // signature from Microsoft. PowerShell rather than WinVerifyTrust because Pascal
  // Script cannot build that call's structures; if PowerShell is unavailable or
  // blocked, that is a "no", and the program opens in the browser.
  Quoted := FileName;
  StringChangeEx(Quoted, '''', '''''', True);  // a ' in the user name, in {tmp}
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "$s = Get-AuthenticodeSignature -LiteralPath '''
      + Quoted + '''; if ($s.Status -eq ''Valid'' -and $s.SignerCertificate.Subject'
      + ' -like ''*O=Microsoft Corporation*'') { exit 0 } else { exit 1 }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function InstallWebView2: Boolean;
var
  Bootstrapper: String;
  ResultCode: Integer;
begin
  Result := False;
  Bootstrapper := ExpandConstant('{tmp}\' + WebView2Bootstrapper);
  if not SignedByMicrosoft(Bootstrapper) then
  begin
    Log('WebView2 bootstrapper is not validly signed by Microsoft; not running it');
    Exit;
  end;
  WizardForm.StatusLabel.Caption := 'Installing Microsoft WebView2...';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    // Exec, never ShellExec 'runas': this Setup is not elevated, so neither is
    // the bootstrapper, and Microsoft's documented result is a per-user install.
    if not Exec(Bootstrapper, '/silent /install', '', SW_HIDE,
                ewWaitUntilTerminated, ResultCode) then
      Log('WebView2 bootstrapper could not start: ' + SysErrorMessage(ResultCode))
    else
      Log(Format('WebView2 bootstrapper exited with %d', [ResultCode]));
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
  // the registry, not the exit code, is what the program's window will look at
  Result := WebView2Installed;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep <> ssPostInstall) or not WantWebView2 then
    Exit;
  if WebView2Downloaded and InstallWebView2 then
    Exit;
  SuppressibleMsgBox('Microsoft WebView2 could not be installed, so Drone Coder will open in '
    + 'your web browser instead. Everything works the same there.' + #13#10#13#10
    + 'To get Drone Coder''s own window later, install the WebView2 Runtime from '
    + 'Microsoft''s website and start Drone Coder again.',
    mbInformation, MB_OK, IDOK);
end;

