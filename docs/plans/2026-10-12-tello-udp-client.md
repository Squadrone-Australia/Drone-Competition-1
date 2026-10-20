# Own Tello UDP client, firewall-proof control, and video on managed laptops

**Status:** Approved 2026-10-12 · **Prompted by:** field feedback (university laptop could not fly) and recurring `error Not joystick`

**Progress (2026-10-12):** Parts A, B and C are implemented, and djitellopy is
removed (`av` is now a direct dependency). The hole-punch (C.1) became part of
`FrameReader`, proven on Linux with ufw (field tests 3 and 4 below). Still open:

- **Windows Defender Firewall** has not been tested against the punch yet. This
  dev laptop's firewall is Bitdefender, so Windows rules aren't enforced on it.
- **C.2 (installer rule): not built, and probably not needed.** `installer/comp1.iss`
  installs with `PrivilegesRequired=lowest` (deliberately not admin), so it can't add a
  firewall rule without a UAC prompt, and silent in-app updates must never raise one.
  Revisit only if the punch fails under Windows Defender Firewall.
- **The volunteer's university laptop** is the final check.

**Field test 1 (2026-10-12, Tello EDU, `sdk?` = 30, `tools/tello_field_check.py`):**

- The new client flew a full program, with every command sent once and no
  `Not joystick`. Healthy times: cw 360 6.6 s (djitellopy's limit was 7 s),
  forward/back 200 3.6 s, up/down 50 2.4 s, takeoff 3.9–8.9 s, land 3.8 s. When a
  move is obstructed, the aircraft gives up on its own with `error Run timeout` at
  15.1 s, so every manoeuvre now waits at least 17 s (`MANOEUVRE_FLOOR_S`).
- **The drone only answered the local port that first sent `command`, until it was
  rebooted.** The first client used a random port per connection, so every later
  connection needed a reboot. It now always sends from port 8889 (`CONTROL_PORT`),
  and a busy port is reported plainly.
- Video comes from 192.168.10.1:**62512**, and status from 192.168.10.1:8889.
- **The second `streamon` in a session delivered no video**, every time, while
  status kept arriving. Not yet known whether that is caused by `streamoff` or by
  closing and reopening our socket. Until it is, a reconnect may lose video and be
  misreported as `video_blocked`.
- The test block rule did not block anything (video still arrived), so the
  hole-punch result is inconclusive.

**Field test 2 (2026-10-12):**

- **The fixed port works.** `reconnect` gave 1 ok, 2 ok, stranger NO REPLY,
  4 ok, so a program on another port can't take the drone over. Checks ran back
  to back with no reboot.
- **`streamoff` is what kills video, not our sockets.** In run 2, `--between
  close` and `--between keep` got no video, while `--between nostreamoff` did.
  So `TelloDrone.close()` no longer sends `streamoff`. The "no video" message now
  says to restart the drone first and then ask IT, since another program may
  have sent `streamoff`.
- Video source port was 62513 once and otherwise 62512, so `punch` now tries both.
- The flight was clean with the 17 s minimum wait.
- **This laptop can't simulate a managed firewall.** Kaspersky and Bitdefender
  are registered as the firewall products, so Windows Firewall rules aren't
  enforced. The block rule did nothing, and the hole-punch (C.1) is still
  untested. This also probably explains why djitellopy worked here and not on a
  university laptop. Next step: test on a laptop whose only firewall is Windows
  Defender Firewall, ideally the volunteer's university one.

**Field test 3 (2026-10-12, Linux laptop, ufw, the same Tello EDU):**

| ufw | Commands | Status / video, no punch | Status / video, punched |
| --- | --- | --- | --- |
| off | ok | arrive | arrive |
| on | ok | NOTHING | arrive (full stream) |

- **Commands work through a blocking stateful firewall**, so the core claim of
  Part A holds.
- **The hole-punch works** for both status and video. The firewall's entry then
  stays open for as long as the stream keeps flowing. Runs after the punch got
  video without one, because that entry was still live; with no `streamoff`,
  the stream never stops.
- **C.1 is implemented.** `FrameReader` now owns the :11111 socket. While no
  video is arriving it punches every second to 62512 and 62513, and it feeds the
  datagrams to PyAV's H.264 parser and decoder itself, with no FFmpeg `udp://`
  demuxer. Tests cover real chunked H.264, recovery from garbage, the punch
  leaving from the video socket, and a prompt port release.
- Still unproven: the punch through **Windows Defender Firewall** (expected to
  behave the same, since it is also stateful for UDP).

**Field test 4 (2026-10-12, Linux, ufw on, the full app):** `python -m comp1
--drone tello` connected, showed video, and flew with no errors, so a locked-down
laptop now gets the whole app with no firewall rule. As a result, C.2 (an
installer rule) and the "Fix camera" button are no longer needed unless Windows
Defender Firewall behaves differently. The README entry for IT stays as the
fallback.

## Context

Field feedback: on a (likely university-managed) laptop, Drone Coder "seems to connect but
doesn't run", and a djitellopy script in Jupyter failed too — while a raw-UDP script (the
volunteer's, one socket, send + read reply) flew fine. Separately, the developer sees constant
`error Not joystick` with djitellopy.

Investigation (reproduced against a fake Tello on loopback, real djitellopy 2.5.0 + our adapter):

1. **Firewall.** Command replies on :8889 are answers to our own datagrams, so Windows'
   stateful firewall passes them. State (:8890) and video (:11111) are *unsolicited* inbound UDP
   and are dropped without an allow rule (Tello Wi-Fi is a **Public** network; managed devices
   suppress the "Allow access" prompt). djitellopy's `connect()` demands a state packet within
   1 s → `TelloException: Did not receive a state packet`; `get_battery()` reads state too. In
   our app `TelloDrone.connect()` raises, `link_ok` never goes True, the watchdog
   (`server.py` `_link_loop`) retries forever, and every Run is refused "not connected".
2. **`error Not joystick`.** djitellopy waits a fixed 7 s per reply and then *re-sends* the
   command (3 retries). A manoeuvre longer than 7 s (`cw 360`, long moves) is sent again while
   still executing → the aircraft answers `error Not joystick` (busy); the late `ok` then
   sits in the queue and every later command reads its predecessor's reply. A lost reply
   makes the drone fly the move twice.

Outcome wanted: control and battery work with zero firewall rules; video (needed for the
vision pipeline) works on any laptop where it can be made to, and says plainly why when it
can't; djitellopy is removed.

## Part A — `comp1/drone/tello_link.py`: our own SDK command client

New module, a small class `TelloLink` (pure socket code, no comp1 imports, easy to fake):

- **One UDP socket**, bound to an ephemeral port (`("", 0)`), `connect`-less `sendto`
  `192.168.10.1:8889`. The Tello replies to the source port (proven by the volunteer's
  port-9000 script), so the reply is a solicited flow → firewall-proof. No :8890 listener
  at all; ephemeral port also removes "8889 already in use" from a stray Jupyter kernel.
- **One command in flight, one reply consumed, never re-sent.** `send(cmd, timeout)`:
  drain any queued datagrams first (stale = from a timed-out or dead command; log them),
  send once, wait for one reply with `socket.settimeout`. A movement is never retried —
  a re-sent manoeuvre is exactly how `Not joystick` and double moves happen.
- **Timeout scaled to the manoeuvre** instead of a flat 7 s:
  `base 7 s + cm / 20 cm·s⁻¹` for moves, `+ deg / 30 °·s⁻¹` for rotations, 20 s takeoff,
  flip ~10 s; always below `FlightConfig.command_timeout_s` (45 s) so the interpreter's
  backstop (`interpreter.py` `_call_drone`) still bounds everything.
- **After a timeout**, set a `_reply_overdue` flag: the next `send` waits
  `_STALE_RESPONSE_SETTLE_S` and drains before sending (generalises the existing reconnect
  logic in `tello.py:connect`). Timeouts raise; caller marks `link_ok=False` as today.
- **Replies:** `ok` → success; numeric → value (for `battery?`); `error…` → raise a
  `TelloError` whose message is plain language for the student, e.g. `error Not joystick`
  → "the drone was still busy with the previous move", flip refusal → low battery hint.
- **Only the "command" handshake may retry** (it is idempotent and the aircraft may still be
  booting): up to 3 tries × 2 s inside `connect()`.
- `close()` closes the socket; `send_no_reply(cmd)` for `streamoff`/`emergency` fallback.
- Optional `keepalive` not added now (noted as follow-up: SDK auto-lands after 15 s
  without a command; djitellopy never sent one either, so no regression).

## Part B — rewrite `TelloDrone` on top of it (`comp1/drone/tello.py`)

Keep the public adapter surface and `FrameReader` unchanged. Replace the djitellopy bits:

- `connect()`: `close()`, settle if a session was live (existing logic), new `TelloLink`,
  `command` handshake, `streamon`, video punch (Part C), `_open_reader()`. No state wait.
- `_cmd(name, *args)` → `_send(text, timeout)` mapping: `takeoff`, `land`, `emergency`,
  `forward/back/left/right/up/down <cm>`, `cw/ccw <deg>`, `flip <f|b|l|r>`, `battery?`.
  Keep the "not connected" RuntimeError and the `link_ok=False`-on-failure behaviour.
- `battery()` → `int(send("battery?"))`. The server's `_battery_loop` already avoids
  polling mid-mission, and now there is no positional queue to corrupt anyway.
- `close()`: fire-and-forget `streamoff`, close the link. **Delete** the djitellopy
  `__del__`-neutralising hack and `_drain_responses` (no global `drones` dict anymore).
- `FrameReader.open` takes the address string `udp://@0.0.0.0:11111?...` from a module
  constant instead of `tello.get_udp_video_address()`; `_VIDEO_OPEN_TIMEOUT_S` becomes a
  local constant (was `Tello.FRAME_GRAB_TIMEOUT` = 5).
- Remove `djitellopy` from `pyproject.toml`; check `comp1.spec` / `build.ps1` for hidden
  imports or data entries; regenerate `requirements.txt` per README process (do **not**
  pip install from it). Update comments that say "djitellopy pairs replies positionally"
  in `server.py` `_battery_loop` docstring and `config.py` `command_timeout_s` docs.

## Part C — video on firewalled laptops (the layered convention)

Changing the port alone does not help: the firewall drops *unsolicited* inbound UDP on any
port, and the `port` command exists only on Tello EDU (SDK 2.0). The standard approach for
desktop apps with inbound UDP (games, conferencing) is layered:

1. **No-admin attempt: UDP hole-punch.** Bind the video socket to local 11111 and send one
   small datagram from it to the Tello's video *source* port before `streamon` (and every
   few seconds while no frame arrives). That creates an outbound flow, so Windows treats
   the stream as replies. Needs the Tello's video source port; **capture it on real
   hardware first** (log `recvfrom` address with the firewall open; community reports
   62512, unverified). Feasibility hinges on PyAV/FFmpeg being able to read from a socket
   we already bound: implement as our own `socket` → `av.open(file-like)` reader of the
   raw H.264 bytes, or bind with `SO_REUSEADDR` and punch from a second socket — decide
   after a 30-minute spike on hardware. Ship behind `FlightConfig.video_punch: bool = True`
   so it can be disabled in the TOML on the day.
2. **Installer allow rule (admin already granted).** In `installer/comp1.iss` `[Run]` add
   `netsh advfirewall firewall add rule name="Drone Coder video" dir=in action=allow
   program="{app}\comp1.exe" protocol=UDP localport=11111 profile=any`, and delete it in
   `[UninstallRun]`. Harmless if GPO ignores local rules. Run
   `tests/test_update_release.py` since the `.iss` is part of the release contract.
3. **IT path.** README section "University-managed laptops": the exact rule above, for IT
   to push via policy when `AllowLocalFirewallRules` is off.
4. **Say so plainly.** If the control link is up but no frame has arrived N s
   (`link_timeout_s`) after `streamon`, the adapter reports a distinct
   `video_blocked` state instead of marking the whole link dead: control stays usable,
   the watchdog must **not** reconnect-loop, and the UI shows "Connected to the drone, but
   this laptop's firewall is blocking the camera. Ask IT to allow Drone Coder (UDP 11111)."
   Concretely: `_note_silence` only clears `link_ok` if a frame was ever seen this session
   *or* the control link has also failed; otherwise sets `video_blocked`. Broadcast it in
   the existing `link` message (`server.py` `_publish_link`) and surface it in
   `comp1/frontend/app.js`'s link handler.

## Tests

- `tests/test_tello_link.py` (new): a fake Tello on `127.0.0.2:8889` like the
  repro script (busy → `error Not joystick`, delayed `ok`, optional dropped replies):
  - connect succeeds with **no state packets** at all;
  - a 9 s `cw 360` succeeds (scaled timeout) and is **sent exactly once**;
  - a timed-out command is never re-sent; the late `ok` is drained, not given to the next
    command;
  - `error …` raises with the plain-language message; `battery?` parses an int.
- `tests/test_mock_drone.py`: rewrite the `fake_tello` / `reconnectable_tello` fixtures to
  fake `TelloLink` instead of djitellopy's `Tello`; keep every existing behavioural test
  (close never flies, reconnect starts clean, silent camera, command failure marks link
  down). Replace `test_a_reconnect_starts_with_an_empty_response_queue` with the
  link-level drain test.
- New: control-up/no-video yields `video_blocked`, not a reconnect loop (adapter + a
  `test_server.py` case for the `link` message).
- `tests/test_fault_recovery.py` must stay green unchanged (stop flag / e-stop bounds).

## Verification

1. `python -m pytest -q`, `python -m ruff check .`,
   `python -m pytest tests/test_update.py tests/test_update_release.py -q`,
   `node --test 'tests/js/**/*.test.js'` (if the frontend link message changes).
2. Loopback repro: rerun the scratch fake-Tello script's three cases against the new
   adapter — connect with state blocked succeeds; `cw 360` then `forward 50` then `land`
   all succeed and the drone log shows each command once.
3. Real hardware, own laptop: fly a program with long moves and a 360° turn; no
   `Not joystick`. Record the video source port (input to C.1).
4. Real hardware, firewall-on test: `netsh advfirewall set allprofiles firewallpolicy
   blockinbound,allowoutbound` with no python/comp1 rules (or a guest account) — confirm
   flying works, then confirm video via punch, or that the `video_blocked` message
   appears; then add the installer rule and confirm video. Restore policy afterwards.
5. Ask the volunteer to run the new build on their university laptop.
