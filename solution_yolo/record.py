"""Fly a Tello by keyboard and record its camera for YOLO training data.

    python -m solution_yolo.record                      # real Tello (join its Wi-Fi first)
    python -m solution_yolo.record --drone sim          # the simulator, no hardware
    python -m solution_yolo.record --out D:\\footage --format jpg

Each recording lands in ``<out>/<session>/`` as one MP4 clip (or one folder
of JPG frames) plus a small ``.json`` sidecar. ``real_label`` takes either
next: pass the clips themselves (``<out>/<session>/*.mp4``), since a folder
argument is only searched for images. Only *new* camera frames are written, so
a stalled stream never fills a clip with copies of one picture. The REC marker
and the status text are drawn on the preview only, never into what is saved.

Keys (click the video first if a text box has the focus):

    T take off      L land        Space hover (centre the sticks)
    W / S forward / back          A / D left / right
    R / F up / down               Q / E turn left / right
    Enter start / stop recording  P save one still
    Esc  EMERGENCY: cuts the motors, the drone falls

The sticks are sent as ``rc`` 20 times a second while airborne and drop to
centre the moment a key is released or the window loses focus, so a setpoint
never outlives the key that set it.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "raw"
#: MP4 header rate when the stream rate is not yet known. The Tello streams
#: at about 30 fps; otherwise the measured rate is used.
CLIP_FPS = 30.0
JPG_QUALITY = 95
#: How often the sticks are re-sent while airborne. Also keeps the Tello from
#: auto-landing, which it does after 15 s without a command.
RC_PERIOD_MS = 50
#: Linux autorepeat sends release+press pairs; a release is only believed if
#: no press of the same key follows within this long.
RELEASE_DEBOUNCE_MS = 60
STATUS_PERIOD_S = 4.0
PREVIEW_W, PREVIEW_H = 800, 600

#: key -> (axis, sign). Axes are the rc order: lr, fb, ud, yaw.
STICK_KEYS = {
    "a": (0, -1),
    "d": (0, +1),
    "w": (1, +1),
    "s": (1, -1),
    "r": (2, +1),
    "f": (2, -1),
    "q": (3, -1),
    "e": (3, +1),
}


def sticks(pressed: set[str], speed: int, yaw_speed: int) -> tuple[int, int, int, int]:
    """rc setpoint for the held keys. Opposite keys cancel; values in -100..100."""
    out = [0, 0, 0, 0]
    for key in pressed:
        if key in STICK_KEYS:
            axis, sign = STICK_KEYS[key]
            out[axis] += sign * (yaw_speed if axis == 3 else speed)
    return tuple(max(-100, min(100, int(v))) for v in out)


def safe_session(name: str) -> str:
    """A session name usable as a folder name (and by real_label)."""
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip())
    return cleaned.strip("_") or "session"


def clip_stem(session: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    return f"{safe_session(session)}_{when:%Y%m%d-%H%M%S}"


def same_frame(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    """Cheap test for a repeated frame: compare a sparse grid of pixels."""
    if a is None or b is None or a.shape != b.shape:
        return False
    return np.array_equal(a[::16, ::16], b[::16, ::16])


@dataclass
class Recording:
    """One clip being written. Only the capture thread touches it."""

    stem: Path
    fmt: str  # "mp4" or "jpg"
    fps: float = CLIP_FPS
    started: float = field(default_factory=time.time)
    frames: int = 0
    writer: cv2.VideoWriter | None = None
    size: tuple[int, int] | None = None

    @property
    def target(self) -> Path:
        return self.stem.with_suffix(".mp4") if self.fmt == "mp4" else self.stem

    def write(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        if self.size is None:
            self.size = (w, h)
            if self.fmt == "mp4":
                self.writer = cv2.VideoWriter(
                    str(self.target), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h)
                )
                if not self.writer.isOpened():
                    raise OSError(f"cannot open {self.target} for writing")
            else:
                self.target.mkdir(parents=True, exist_ok=True)
        if (w, h) != self.size:
            frame = cv2.resize(frame, self.size)
        if self.writer is not None:
            self.writer.write(frame)
        else:
            path = self.target / f"{self.stem.name}_{self.frames:06d}.jpg"
            cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, JPG_QUALITY])
        self.frames += 1

    def close(self, drone_mode: str) -> dict:
        if self.writer is not None:
            self.writer.release()
        meta = {
            "file": self.target.name,
            "format": self.fmt,
            "frames": self.frames,
            "fps": self.fps,
            "seconds": round(time.time() - self.started, 2),
            "size": list(self.size) if self.size else None,
            "drone": drone_mode,
            "started": datetime.fromtimestamp(self.started).isoformat(
                timespec="seconds"
            ),
        }
        if self.frames:
            self.stem.with_suffix(".json").write_text(json.dumps(meta, indent=2))
        elif self.fmt == "mp4" and self.target.exists():
            self.target.unlink()  # nothing arrived: do not leave an empty clip
        return meta


def make_drone(mode: str):
    """Build (unconnected) the adapter for ``mode``. Imported lazily: the Tello
    adapter needs PyAV and the simulator needs the comp2 package."""
    if mode == "tello":
        from comp2.drone.tello import TelloDrone

        return TelloDrone()
    if mode == "sim":
        from comp2.sim.drone import SimDrone

        return SimDrone()
    raise ValueError(f"unknown drone {mode!r}")


class RecorderApp:
    def __init__(self, root, args):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.root = root
        self.mode = args.drone
        self.drone = None
        self.airborne = False
        self.busy = False  # a takeoff/land/connect is in progress

        self.pressed: set[str] = set()
        self._release_jobs: dict[str, str] = {}
        self._last_rc = (0, 0, 0, 0)

        # capture thread state
        self._frame_lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._fps = 0.0
        self._capture_stop = threading.Event()
        self._capture: threading.Thread | None = None
        self._record_lock = threading.Lock()
        self._recording: Recording | None = None
        self._still_requested = False

        # one worker serialises every blocking aircraft command
        self._jobs: queue.Queue = queue.Queue()
        self._events: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()
        self._next_status = 0.0

        root.title(f"Tello recorder ({self.mode})")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        bar = ttk.Frame(root, padding=6)
        bar.pack(fill="x")
        self.connect_btn = ttk.Button(
            bar, text="Connect", command=self.connect, takefocus=0
        )
        self.connect_btn.pack(side="left")
        ttk.Button(bar, text="Take off (T)", command=self.takeoff, takefocus=0).pack(
            side="left", padx=2
        )
        ttk.Button(bar, text="Land (L)", command=self.land, takefocus=0).pack(
            side="left", padx=2
        )
        self.rec_btn = ttk.Button(
            bar, text="● Record (Enter)", command=self.toggle_record, takefocus=0
        )
        self.rec_btn.pack(side="left", padx=8)
        ttk.Button(bar, text="Still (P)", command=self.request_still, takefocus=0).pack(
            side="left"
        )
        tk.Button(
            bar,
            text="EMERGENCY (Esc)",
            command=self.emergency,
            bg="#b00020",
            fg="white",
            takefocus=0,
        ).pack(side="right")

        opts = ttk.Frame(root, padding=(6, 0))
        opts.pack(fill="x")
        ttk.Label(opts, text="Session").pack(side="left")
        self.session = tk.StringVar(value=args.session)
        ttk.Entry(opts, textvariable=self.session, width=16).pack(side="left", padx=4)
        ttk.Label(opts, text="Folder").pack(side="left", padx=(8, 0))
        self.out_dir = tk.StringVar(value=str(args.out))
        ttk.Entry(opts, textvariable=self.out_dir, width=36).pack(side="left", padx=4)
        ttk.Button(opts, text="…", width=3, command=self.pick_folder, takefocus=0).pack(
            side="left"
        )
        self.fmt = tk.StringVar(value=args.format)
        ttk.Radiobutton(
            opts, text="MP4", value="mp4", variable=self.fmt, takefocus=0
        ).pack(side="left", padx=(10, 0))
        ttk.Radiobutton(
            opts, text="JPG frames", value="jpg", variable=self.fmt, takefocus=0
        ).pack(side="left")

        speeds = ttk.Frame(root, padding=(6, 4))
        speeds.pack(fill="x")
        ttk.Label(speeds, text="Speed").pack(side="left")
        self.speed = tk.IntVar(value=args.speed)
        tk.Scale(
            speeds,
            from_=10,
            to=100,
            orient="horizontal",
            variable=self.speed,
            length=180,
            takefocus=0,
        ).pack(side="left")
        ttk.Label(speeds, text="Turn").pack(side="left", padx=(10, 0))
        self.yaw_speed = tk.IntVar(value=args.yaw_speed)
        tk.Scale(
            speeds,
            from_=10,
            to=100,
            orient="horizontal",
            variable=self.yaw_speed,
            length=180,
            takefocus=0,
        ).pack(side="left")

        self._blank = tk.PhotoImage(width=PREVIEW_W, height=PREVIEW_H)
        self.video = tk.Label(root, bg="black", image=self._blank)
        self.video.pack(padx=6)
        self.video.bind("<Button-1>", lambda _e: root.focus_set())
        self._photo = None

        self.status = tk.StringVar(
            value="Not connected. Join the Tello's Wi-Fi, then Connect."
        )
        ttk.Label(root, textvariable=self.status, padding=6, anchor="w").pack(fill="x")
        ttk.Label(
            root,
            padding=(6, 0, 6, 6),
            foreground="#555",
            text="T take off · L land · Space hover · WASD move · R/F up/down · Q/E turn · "
            "Enter record · P still · Esc EMERGENCY",
        ).pack(fill="x")
        self.info = {"battery": "?", "height": "?"}

        root.bind_all("<KeyPress>", self.on_key_press)
        root.bind_all("<KeyRelease>", self.on_key_release)
        root.bind("<FocusOut>", lambda _e: self.clear_sticks())
        root.after(30, self._tick_video)
        root.after(RC_PERIOD_MS, self._tick_rc)
        root.after(100, self._tick_events)
        root.focus_set()

    # --- worker ----------------------------------------------------------

    def _worker(self):
        while True:
            name, fn = self._jobs.get()
            try:
                result = fn()
                self._events.put((name, True, result))
            except Exception as exc:  # reported, never fatal to the window
                self._events.put((name, False, exc))

    def _submit(self, name, fn):
        self._jobs.put((name, fn))

    def _tick_events(self):
        while True:
            try:
                name, ok, result = self._events.get_nowait()
            except queue.Empty:
                break
            self._on_event(name, ok, result)
        now = time.monotonic()
        if (
            self.drone is not None
            and not self.busy
            and self._jobs.empty()
            and now >= self._next_status
        ):
            self._next_status = now + STATUS_PERIOD_S
            self._submit("status", self._read_status)
        self.root.after(100, self._tick_events)

    def _on_event(self, name, ok, result):
        if name == "connect":
            self.busy = False
            if ok:
                self.drone = result
                self._start_capture()
                self.connect_btn.configure(text="Reconnect")
                self.say("Connected.")
            else:
                self.say(f"Connect failed: {result}")
        elif name == "takeoff":
            self.busy = False
            self.airborne = ok
            self.say("Airborne." if ok else f"Take-off failed: {result}")
        elif name == "land":
            self.busy = False
            if ok:
                self.airborne = False
            self.say("Landed." if ok else f"Land failed: {result}")
        elif name == "emergency":
            self.say(
                "EMERGENCY sent: motors cut." if ok else f"Emergency failed: {result}"
            )
        elif name == "record_error":
            self.rec_btn.configure(text="● Record (Enter)")
        elif name == "status" and ok:
            self.info.update(result)

    def _read_status(self):
        drone = self.drone
        info = {}
        try:
            info["battery"] = f"{drone.battery()}%"
        except Exception:
            info["battery"] = "?"
        try:
            info["height"] = f"{drone.height_cm():.0f} cm"
        except Exception:
            info["height"] = "?"
        return info

    def say(self, text):
        self._message = text

    # --- connection ------------------------------------------------------

    def connect(self):
        if self.busy:
            return
        if self.airborne:
            self.say("Land before reconnecting.")
            return
        self.busy = True
        self.say(f"Connecting to {self.mode}…")
        self._stop_capture()
        old, self.drone = self.drone, None
        mode = self.mode

        def job():
            if old is not None:
                old.close()
            drone = make_drone(mode)
            drone.connect()
            return drone

        self._submit("connect", job)

    def _start_capture(self):
        self._capture_stop.clear()
        self._capture = threading.Thread(
            target=self._capture_loop, args=(self.drone,), daemon=True
        )
        self._capture.start()

    def _stop_capture(self):
        self.stop_record()
        self._capture_stop.set()
        if self._capture is not None:
            self._capture.join(timeout=2.0)
        self._capture = None

    def _capture_loop(self, drone):
        """Owns the recording: pulls frames, writes the new ones."""
        prev = None
        stamps: list[float] = []
        while not self._capture_stop.is_set():
            try:
                frame = drone.get_frame()
            except Exception:
                frame = None
            if frame is None or same_frame(frame, prev):
                time.sleep(0.005)
                continue
            prev = frame
            now = time.monotonic()
            stamps = [t for t in stamps if now - t < 1.0] + [now]
            with self._frame_lock:
                self._latest = frame
                self._fps = len(stamps)
            with self._record_lock:
                rec = self._recording
                if rec is not None:
                    try:
                        rec.write(frame)
                    except Exception as exc:
                        self._recording = None
                        rec.close(self.mode)
                        self._events.put(("record_error", False, exc))
                        self.say(f"Recording stopped: {exc}")
                if self._still_requested:
                    self._still_requested = False
                    self._save_still(frame)

    # --- recording -------------------------------------------------------

    def _session_dir(self) -> Path:
        folder = Path(self.out_dir.get()).expanduser() / safe_session(
            self.session.get()
        )
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def toggle_record(self):
        if self._recording is not None:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self):
        if self.drone is None:
            self.say("Connect first.")
            return
        try:
            stem = self._session_dir() / clip_stem(self.session.get())
        except OSError as exc:
            self.say(f"Cannot create the folder: {exc}")
            return
        with self._record_lock:
            # the header rate follows the stream, so the clip plays at real speed
            fps = float(self._fps) if 5 <= self._fps <= 60 else CLIP_FPS
            self._recording = Recording(stem=stem, fmt=self.fmt.get(), fps=fps)
        self.rec_btn.configure(text="■ Stop (Enter)")
        self.say(f"Recording to {self._recording.target}")

    def stop_record(self):
        with self._record_lock:
            rec, self._recording = self._recording, None
        if rec is None:
            return
        meta = rec.close(self.mode)
        self.rec_btn.configure(text="● Record (Enter)")
        if meta["frames"]:
            self.say(
                f"Saved {meta['frames']} frames ({meta['seconds']} s) to {rec.target}"
            )
        else:
            self.say("No frames arrived; nothing saved.")

    def request_still(self):
        if self.drone is None:
            self.say("Connect first.")
            return
        self._still_requested = True

    def _save_still(self, frame):
        try:
            path = (
                self._session_dir()
                / f"{clip_stem(self.session.get())}_{int(time.time() * 1000) % 1000:03d}.png"
            )
            cv2.imwrite(str(path), frame)
            self.say(f"Still saved: {path.name}")
        except Exception as exc:
            self.say(f"Still failed: {exc}")

    def pick_folder(self):
        from tkinter import filedialog

        chosen = filedialog.askdirectory(initialdir=self.out_dir.get())
        if chosen:
            self.out_dir.set(chosen)
        self.root.focus_set()

    # --- flight ----------------------------------------------------------

    def takeoff(self):
        if self.drone is None or self.busy or self.airborne:
            return
        self.busy = True
        self.clear_sticks()
        self.say("Taking off…")
        self._submit("takeoff", self.drone.takeoff)

    def land(self):
        if self.drone is None or not self.airborne:
            return
        self.busy = True
        self.clear_sticks()
        self.say("Landing…")
        self._submit("land", self.drone.land)

    def emergency(self):
        """Bypasses the worker queue: must not wait behind a take-off."""
        drone = self.drone
        self.clear_sticks()
        if drone is None:
            return
        self.airborne = False

        def run():
            try:
                drone.emergency()
                self._events.put(("emergency", True, None))
            except Exception as exc:
                self._events.put(("emergency", False, exc))

        threading.Thread(target=run, daemon=True).start()

    def clear_sticks(self):
        self.pressed.clear()
        for job in self._release_jobs.values():
            self.root.after_cancel(job)
        self._release_jobs.clear()
        if self.drone is not None and self._last_rc != (0, 0, 0, 0):
            self._send_rc((0, 0, 0, 0))

    def _send_rc(self, rc):
        try:
            self.drone.send_rc(*rc)
            self._last_rc = rc
        except Exception as exc:
            self.say(f"rc failed: {exc}")

    def _tick_rc(self):
        if self.drone is not None and self.airborne and not self.busy:
            self._send_rc(sticks(self.pressed, self.speed.get(), self.yaw_speed.get()))
        self.root.after(RC_PERIOD_MS, self._tick_rc)

    # --- keyboard --------------------------------------------------------

    def _typing(self, event) -> bool:
        return event.widget.winfo_class() in ("Entry", "TEntry", "Spinbox", "TSpinbox")

    def on_key_press(self, event):
        key = event.keysym.lower()
        if key == "escape":
            self.emergency()
            return "break"
        if self._typing(event):
            if key == "return":
                self.root.focus_set()
            return None
        if key in STICK_KEYS:
            job = self._release_jobs.pop(key, None)
            if job is not None:
                self.root.after_cancel(job)
            self.pressed.add(key)
        elif key == "space":
            self.clear_sticks()
        elif key == "t":
            self.takeoff()
        elif key == "l":
            self.land()
        elif key == "return":
            self.toggle_record()
        elif key == "p":
            self.request_still()
        return "break"

    def on_key_release(self, event):
        key = event.keysym.lower()
        if key in STICK_KEYS and key in self.pressed:
            old = self._release_jobs.pop(key, None)
            if old is not None:
                self.root.after_cancel(old)
            self._release_jobs[key] = self.root.after(
                RELEASE_DEBOUNCE_MS, self._release, key
            )

    def _release(self, key):
        self._release_jobs.pop(key, None)
        self.pressed.discard(key)

    # --- display ---------------------------------------------------------

    def _tick_video(self):
        with self._frame_lock:
            frame, fps = self._latest, self._fps
        if frame is not None:
            view = cv2.resize(
                frame, (PREVIEW_W, PREVIEW_H), interpolation=cv2.INTER_AREA
            )
            rec = self._recording
            if rec is not None and int(time.time() * 2) % 2 == 0:
                cv2.circle(view, (24, 24), 10, (0, 0, 255), -1)
                cv2.putText(
                    view, "REC", (42, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2
                )
            rgb = cv2.cvtColor(view, cv2.COLOR_BGR2RGB)
            ppm = f"P6 {PREVIEW_W} {PREVIEW_H} 255 ".encode() + rgb.tobytes()
            self._photo = self.tk.PhotoImage(
                width=PREVIEW_W, height=PREVIEW_H, data=ppm, format="PPM"
            )
            self.video.configure(image=self._photo)
        self._update_status(fps)
        self.root.after(30, self._tick_video)

    def _update_status(self, fps):
        parts = []
        if self.drone is not None:
            link = getattr(self.drone, "link_ok", True)
            blocked = getattr(self.drone, "video_blocked", False)
            parts.append("link OK" if link else "LINK LOST")
            if blocked:
                parts.append("VIDEO BLOCKED (firewall?)")
            parts.append(f"{fps:.0f} fps")
            parts.append(f"battery {self.info['battery']}")
            parts.append(f"height {self.info['height']}")
            parts.append("airborne" if self.airborne else "on ground")
            rec = self._recording
            if rec is not None:
                parts.append(
                    f"REC {time.time() - rec.started:.0f} s, {rec.frames} frames"
                )
            if self.airborne:
                parts.append("rc " + " ".join(f"{v:+d}" for v in self._last_rc))
        message = getattr(self, "_message", "")
        self.status.set(
            "   |   ".join(parts + ([message] if message else [])) or message
        )

    # --- shutdown --------------------------------------------------------

    def on_close(self):
        self.clear_sticks()
        self.stop_record()
        drone = self.drone
        if drone is not None and self.airborne:
            try:
                drone.land()
            except Exception:
                pass
        self._stop_capture()
        if drone is not None:
            try:
                drone.close()
            except Exception:
                pass
        self.root.destroy()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--drone", choices=["tello", "sim"], default="tello")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="root folder for recordings"
    )
    parser.add_argument("--session", default=datetime.now().strftime("session_%Y%m%d"))
    parser.add_argument("--format", choices=["mp4", "jpg"], default="mp4")
    parser.add_argument(
        "--speed", type=int, default=40, help="stick for move keys, 10-100"
    )
    parser.add_argument(
        "--yaw-speed", type=int, default=50, help="stick for turn keys, 10-100"
    )
    args = parser.parse_args(argv)

    import tkinter as tk

    root = tk.Tk()
    RecorderApp(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
