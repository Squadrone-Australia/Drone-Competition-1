"""What to *do* about an error, for the student reading the console.

An error message says what went wrong; on competition day the question at the
laptop is what to try next, and the answer is rarely in the error itself —
"the drone refused 'takeoff' (error)" is the aircraft's whole explanation, and
the cure (a hot drone, a flat battery, a loose guard) is knowledge the venue
crew carries in their heads. This table writes that knowledge down once.

The server attaches the matching fix as a ``hint`` field to every message the
console shows as a failure (``server._with_hint``), so the browser never has to
pattern-match English text the backend wrote.

Rules of the table:

* **Matched against the message text, first match wins**, so specific entries
  come before general ones (``'takeoff'`` before a bare refusal).
* **No hint is better than a wrong one.** A message that already says how to
  fix itself ("stop the mission before resetting") gets none, and an unknown
  message gets none rather than a guess.
* **Never raises.** It runs on the path that reports an error; failing there
  would swallow the error it was meant to explain.
"""

import re

#: How to get a drone that has stopped answering back. Shared by every
#: "silence" message, because silence has the same short list of causes.
_RECONNECT = (
    "Check this laptop is still on the drone's Wi-Fi (TELLO-…) and the drone is "
    "switched on. Drone Coder reconnects by itself; if it doesn't, press Reconnect. "
    "Still nothing? Turn the drone off and on again, wait for the yellow blinking "
    "light, then press Reconnect."
)

#: (pattern, fix). Patterns are case-insensitive regular expressions searched
#: anywhere in the message.
_HINTS: tuple[tuple[str, str], ...] = (
    # --- the video stream, on :11111 -----------------------------------------
    (
        r"another program .* video port",
        "Close any Jupyter notebook, Python script, other drone app or second "
        "copy of Drone Coder, then press Reconnect.",
    ),
    (
        r"no video is arriving",
        "Turn the drone off and on again and press Reconnect. If there is still "
        "no video, this laptop's firewall is blocking the camera: ask IT to allow "
        "Drone Coder (incoming UDP port 11111). See README → Troubleshooting.",
    ),
    # --- refusals: the aircraft answered, and said no ------------------------
    (
        r"not joystick|still busy",
        "Make sure nothing else is flying this drone at the same time — a phone "
        "app, a notebook, another laptop. If it keeps happening, turn the drone "
        "off and on again and press Reconnect.",
    ),
    (
        r"no valid imu|sensors are not ready",
        "If it keeps happening, calibrate the drone (IMU calibration) in the "
        "Tello phone app.",
    ),
    (
        r"auto ?land|landing by itself",
        "Charge or swap the battery. If the drone feels hot, let it cool down for "
        "a few minutes before flying again.",
    ),
    (
        r"motors are stopped|motor stop",
        "Turn the drone off and on again, then press Reconnect.",
    ),
    (
        r"refused 'takeoff'",
        "The drone won't take off when its battery is low, it is too hot, or it "
        "just crashed. Check: battery above 20%; the drone is not hot (let it cool "
        "a few minutes); propellers spin freely and the guard is firmly on; it "
        "sits on a flat surface. After a crash, turn it off and on again and press "
        "Reconnect.",
    ),
    (
        r"refused '",
        "Check the battery and that nothing blocks the drone. If it refuses again, "
        "land, turn the drone off and on again and press Reconnect.",
    ),
    # --- silence: nobody answered --------------------------------------------
    (
        r"did not answer 'command'",
        "Connect this laptop's Wi-Fi to the drone (TELLO-…) and switch the drone "
        "on. A drone only answers the first program that talked to it since it "
        "was switched on, so if another app or laptop used it, turn the drone off "
        "and on again, then press Reconnect.",
    ),
    (
        r"did not answer|could not send|link (is|was) closed|not connected"
        r"|lost contact",
        _RECONNECT,
    ),
)

_COMPILED = tuple((re.compile(p, re.IGNORECASE), fix) for p, fix in _HINTS)


def fix_for(message: object) -> str | None:
    """The fix for ``message``, or None if there is nothing useful to add."""
    try:
        text = str(message or "")
        for pattern, fix in _COMPILED:
            if pattern.search(text):
                return fix
    except Exception:
        pass
    return None
