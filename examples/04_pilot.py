"""Fly the drone from the keyboard, watch its camera, and record what it sees.

Run it:  python -m comp1 --script examples/04_pilot.py --drone sim

A window opens showing the drone's camera. Click on it, so that it and not
some other window has the keyboard, then:

    T  take off (press it twice: once to get ready, again to go)
    L  land
    W / S   forward / back            A / D   slide left / right
    R / F   up / down  (or Up / Down)  Q / E   turn left / right  (or Left / Right)
    + / -   bigger / smaller steps
    V  start / stop recording          P  save a snapshot
    Esc     land and end the program  (closing the window does the same)

Use it to look round the arena from the drone's own point of view, and to
collect video and stills of the markers from every angle and distance, to tune
and test your own vision code on. Nothing here finds a target or flies by
itself: that part is your entry.

It is also a worked example of the two things every vision program needs:

  * reading the camera while the drone flies: `drone.frame()`, in `Pilot.run`
  * flying without freezing the picture: every command goes to a thread of its
    own, `Pilot._fly`, so a slow move never holds up the video

Every key press is one step: forward 30 cm, turn 15 degrees and so on (+ and -
change the size). Hold a key down and it repeats, a new step each time the last
one finishes; let go and the drone stops after the step it is flying. The
cockpit's big red EMERGENCY STOP still works at any time, and ends this program
too. With a real Tello, clear the flight area before you take off.

Recordings go to the `recordings` folder in the folder you ran from (change
RECORD_DIR below). `flight_<time>.mp4` holds exactly the pixels `drone.frame()`
returned, without the text drawn over the picture here, and plays back in real
time. `snapshot_<time>.png` is a single raw frame.

Run it on its own, `python examples/04_pilot.py`, and it flies the built-in
simulator by itself: no cockpit, and so no emergency stop button either.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from comp1.api import Drone, EmergencyStop, current_session

RECORD_DIR = Path("recordings")
RECORD_FPS = 30.0
TELEMETRY_S = 3.0  # how often battery and height are read while nothing is flying
TARGET_S = 0.25  # how often the HUD asks the drone's own vision what it sees
DISPLAY_MAX_WIDTH = 960
TAKEOFF_ARM_S = 3.0  # the second T has to come this soon after the first
WINDOW = "Drone pilot - click here, then fly with the keys"

# (cm, deg) for one step, smallest first. 20 cm is the smallest move the
# drone will make; turns below 10 deg it tends to ignore.
STEPS = [(20, 10), (30, 15), (50, 30), (100, 45)]

# Each movement key calls one `Drone` method, by one step of distance or angle.
MOVES = {
    "w": ("forward", "cm"),
    "s": ("back", "cm"),
    "a": ("left", "cm"),
    "d": ("right", "cm"),
    "r": ("up", "cm"),
    "f": ("down", "cm"),
    "q": ("turn_left", "deg"),
    "e": ("turn_right", "deg"),
}
# cv2.waitKeyEx() codes for the arrow keys: Windows first, then Linux.
ARROWS = {
    2490368: "r", 2621440: "f", 2424832: "q", 2555904: "e",
    65362: "r", 65364: "f", 65361: "q", 65363: "e",
}  # fmt: skip
HELP_LINE = (
    "T take off  L land  WASD move  R/F up/down  Q/E turn  +/- step  V rec  P snap  Esc quit"
)
ESC ="\x1b"


def stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def say(text: str) -> None:
    # The packaged app has no terminal, so there may be nowhere to print to.
    if sys.stdout is not None:
        print(text)


def key_name(code: int) -> str | None:
    """Turn a cv2.waitKeyEx() code into "w", "+", ESC... or None for no key."""
    if code == -1:
        return None
    if code in ARROWS:
        return ARROWS[code]
    if 0 <= code < 256:
        return chr(code).lower()
    return None


class Recorder:
    """Writes camera frames to an MP4 that plays back at the speed it was flown.

    Frames do not arrive at a fixed rate, so each one is written as many times
    as the clock says are due since the recording began: a stall in the stream
    is a frozen picture in the video, not a jump cut. Handing it the same frame
    twice is harmless for the same reason.
    """

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path: Path | None = None
        self._lock = threading.Lock()
        self._active = False
        self._writer = None
        self._size: tuple[int, int] | None = None
        self._t0 = 0.0
        self._written = 0

    @property
    def recording(self) -> bool:
        return self._active

    def elapsed_s(self) -> float:
        return self._written / RECORD_FPS

    def start(self) -> Path:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.path = self.folder / f"flight_{stamp()}.mp4"
            self._writer, self._size, self._written = None, None, 0
            self._active = True
            return self.path

    def write(self, frame: np.ndarray, t: float) -> None:
        with self._lock:
            if not self._active:
                return
            if self._writer is None:
                h, w = frame.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self._writer = cv2.VideoWriter(str(self.path), fourcc, RECORD_FPS, (w, h))
                self._size, self._t0 = (w, h), t
            if frame.shape[1::-1] != self._size:  # switched to a different camera
                frame = cv2.resize(frame, self._size)
            due = int((t - self._t0) * RECORD_FPS) + 1
            while self._written < due:
                self._writer.write(frame)
                self._written += 1

    def stop(self) -> Path | None:
        """Finish the file; its path, or None if no frame ever arrived."""
        with self._lock:
            was_active, self._active = self._active, False
            if self._writer is not None:
                self._writer.release()
                self._writer = None
            return self.path if was_active and self._written else None


class Pilot:
    def __init__(self, drone: Drone):
        self.drone = drone
        self.recorder = Recorder(RECORD_DIR)
        self.stop = threading.Event()  # tells the flying thread to finish
        self.jobs: queue.Queue = queue.Queue()  # this window -> the flying thread
        self.busy = threading.Event()  # a command is queued or being flown
        self._jobs_lock = threading.Lock()  # busy and jobs change together
        self.airborne = False
        self.step = 1  # index into STEPS
        self.status = "Click this window, then press T to take off."
        self.battery: int | None = None
        self.height_cm: float | None = None
        self.target_text = ""
        self._target_t = 0.0
        self._armed_until = 0.0  # when the first T press stops counting
        self._last_frame: np.ndarray | None = None
        self._flyer = threading.Thread(target=self._fly, name="pilot-fly", daemon=True)

    def _say(self, text: str) -> None:
        self.status = text
        say(text)

    # ------------------------------------------------------------ the window

    def run(self) -> None:
        """Show the camera and read the keyboard until Esc or the window closes.

        This is how to read the camera while the drone flies: ask
        `drone.frame()` for the newest picture every time round a loop. It
        returns a NumPy uint8 array of shape (height, width, 3) in BGR order,
        ready for OpenCV, and it is your own copy to draw on. Before the first
        picture, or while the stream is paused, it returns None: always handle
        None.

        `drone.frame()` also checks the emergency stop, like every `Drone`
        method: press STOP in the cockpit and it raises EmergencyStop, which
        ends this loop and the program wherever they have got to.
        """
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        self._flyer.start()
        landing = True
        try:
            while True:
                frame = self.drone.frame()
                if frame is not None:
                    # Your own vision code would go here, on `frame`. This
                    # example only records it and shows it.
                    self.recorder.write(frame, time.monotonic())
                    self._last_frame = frame
                self._update_target()
                cv2.imshow(WINDOW, self._view())
                key = key_name(cv2.waitKeyEx(15))  # also lets the window draw
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    break  # closed with its X
                if key == ESC:
                    break
                if key is not None:
                    self._key(key)
        except EmergencyStop:
            landing = False  # the cockpit already cut the motors: hands off
            raise
        finally:
            self.stop.set()
            self._flyer.join(timeout=15)  # let the step in flight finish first
            self._finish_recording()
            cv2.destroyWindow(WINDOW)
            cv2.waitKey(1)  # the window only really closes once events are pumped
            if landing and self.airborne:
                say("Landing...")
                self.drone.land()
                self.airborne = False

    def _update_target(self) -> None:
        """What the drone's own vision sees, a few times a second, for the HUD."""
        now = time.monotonic()
        if now - self._target_t < TARGET_S:
            return
        self._target_t = now
        target = self.drone.target()
        self.target_text = (
            f"target {target.distance_cm:.0f} cm  {target.bearing_deg:+.0f} deg"
            if target
            else "no target in view"
        )

    def _hud(self) -> list[str]:
        battery = "--" if self.battery is None else f"{self.battery}%"
        height = "--" if self.height_cm is None else f"{self.height_cm:.0f} cm"
        cm, deg = STEPS[self.step]
        lines = [
            f"battery {battery}   height {height}   step {cm} cm / {deg} deg",
            self.target_text,
            self.status,
        ]
        if self.recorder.recording:
            lines.append(f"REC {self.recorder.elapsed_s():.0f} s")
        return lines

    def _view(self) -> np.ndarray:
        """The picture to show: the camera, smaller if need be, with text on top."""
        frame = self._last_frame
        if frame is None:
            view = np.zeros((480, 640, 3), np.uint8)
            cv2.putText(view, "Waiting for the camera...", (150, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)  # fmt: skip
        else:
            h, w = frame.shape[:2]
            scale = min(1.0, DISPLAY_MAX_WIDTH / w)
            # Drawn on a copy only: the recording stays the raw camera.
            view = (
                cv2.resize(frame, (round(w * scale), round(h * scale)))
                if scale < 1
                else frame.copy()
            )
        lines = self._hud()
        band = view[: 10 + 22 * len(lines)]
        band[:] = band // 3  # darken behind the text so it reads on any scene
        for i, line in enumerate(lines):
            colour = (60, 60, 255) if line.startswith("REC") else (255, 255, 255)
            cv2.putText(view, line, (10, 22 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, colour, 1, cv2.LINE_AA)  # fmt: skip
        h = view.shape[0]
        view[h - 26 :] //= 3
        cv2.putText(view, HELP_LINE,(10, h - 9), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (220, 220, 220), 1, cv2.LINE_AA)  # fmt: skip
        return view

    # ------------------------------------------------------------ the keys

    def _key(self, key: str) -> None:
        if key in MOVES:
            self._move(key)
        elif key == "t":
            self._takeoff()
        elif key == "l":
            self._land()
        elif key in ("+", "="):
            self.step = min(self.step + 1, len(STEPS) - 1)
        elif key in ("-", "_"):
            self.step = max(self.step - 1, 0)
        elif key == "v":
            self._toggle_record()
        elif key == "p":
            self._snapshot()

    def _move(self, key: str) -> None:
        if not self.airborne:
            self._say("Take off first: press T.")
            return
        method, unit = MOVES[key]
        cm, deg = STEPS[self.step]
        amount = cm if unit == "cm" else deg
        # e.g. drone.forward(30) or drone.turn_left(15)
        self._submit(f"{method} {amount} {unit}", lambda d: getattr(d, method)(amount))

    def _takeoff(self) -> None:
        if self.airborne:
            return
        now = time.monotonic()
        if now > self._armed_until:
            # The first press only asks: a real drone needs a clear space.
            self._armed_until = now + TAKEOFF_ARM_S
            self._say("Is the flight area clear? Press T again to take off.")
            return
        self._armed_until = 0.0
        self._submit("take off", self._do_takeoff)

    def _do_takeoff(self, drone: Drone) -> None:
        drone.takeoff()
        self.airborne = True

    def _land(self) -> None:
        if not self.airborne:
            return
        # Not dropped while busy, unlike a move: it waits for the step in flight.
        self._submit("land", self._do_land, always=True)

    def _do_land(self, drone: Drone) -> None:
        drone.land()
        self.airborne = False

    def _submit(self, label: str, command, always: bool = False) -> None:
        """Hand one command to the flying thread.

        A move that arrives while the last one is still flying is dropped, not
        queued: holding a key down sends far more presses than the drone can
        fly, and a queue of them would keep it moving long after you let go.
        """
        with self._jobs_lock:
            if self.busy.is_set() and not always:
                return
            self.busy.set()
            self.jobs.put((label, command))
        self._say(f"{label}...")

    # ------------------------------------------------------- flying thread

    def _fly(self) -> None:
        """The one thread that sends commands, so they reach the drone in order.

        A move takes as long as the drone takes to fly it — a second or more on
        a real Tello. Doing that here, and not in `run`, is what keeps the
        picture moving while the drone does.
        """
        last_telemetry = 0.0
        while not self.stop.is_set():
            try:
                label, command = self.jobs.get(timeout=0.05)
            except queue.Empty:
                if time.monotonic() - last_telemetry >= TELEMETRY_S:
                    last_telemetry = time.monotonic()
                    try:
                        self.battery = self.drone.battery
                        self.height_cm = self.drone.height
                    except EmergencyStop:
                        return  # `run` gets its own EmergencyStop from drone.frame()
                    except Exception as exc:
                        say(f"could not read telemetry: {exc}")
                continue
            try:
                command(self.drone)
                self._say(f"{label}: done")
            except EmergencyStop:
                return
            except Exception as exc:  # a refused command, not the end of the flight
                self._say(f"{label} failed: {exc}")
            finally:
                with self._jobs_lock:
                    if self.jobs.empty():
                        self.busy.clear()

    # ----------------------------------------------------------- recording

    def _toggle_record(self) -> None:
        if self.recorder.recording:
            self._finish_recording()
        else:
            self._say(f"Recording to {self.recorder.start()}")

    def _finish_recording(self) -> None:
        if not self.recorder.recording:
            return
        path = self.recorder.stop()
        self._say(f"Saved {path}" if path else "Recording stopped before any frame arrived")

    def _snapshot(self) -> None:
        frame = self._last_frame
        if frame is None:
            return
        folder = self.recorder.folder
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"snapshot_{stamp()}_{int(time.time() * 1000) % 1000:03d}.png"
        cv2.imwrite(str(path), frame)
        self._say(f"Saved {path}")


def main() -> None:
    # Under `python -m comp1 --script` a plain Drone() is the cockpit's drone.
    # Run on its own there is no cockpit, so fly the built-in simulator.
    drone = Drone() if current_session() is not None else Drone(sim=True)
    Pilot(drone).run()


if __name__ == "__main__":
    main()
