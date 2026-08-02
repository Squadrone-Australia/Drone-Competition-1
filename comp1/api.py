"""The Python pathway: a plain, synchronous drone API for students.

Both pathways drive the *same* engine. A ``Drone`` here talks to the same
``DroneAdapter``, reads the same OpenCV ``Detection``, and closes on a victim
with the same proportional controller and the same ``VisionConfig`` constants
as the ``approach marker`` block. Nothing here is a simulation of the blocks —
it is the blocks' machinery with a different front door.

A whole mission::

    from comp1.api import Drone

    drone = Drone()
    drone.takeoff()
    while not drone.sees_target():      # spin until a victim comes into view
        drone.turn_right(20)
    drone.approach_target()
    drone.mark_found()
    drone.land()

Run it with the server, so the video feed, telemetry and EMERGENCY STOP button
all stay live while your code flies::

    python -m comp1 --script my_mission.py --drone sim

Run it standalone (no window, no e-stop button — testing only)::

    Drone(sim=True)
"""

import asyncio
import runpy
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .drone.base import DroneAdapter
from .vision.config import VisionConfig, DEFAULT_CONFIG
from .vision.detector import Detection, Target, TargetTracker

MIN_MOVE_CM, MAX_MOVE_CM = 20, 500      # Tello firmware limits, not preferences
MIN_TURN_DEG, MAX_TURN_DEG = 1, 360


class EmergencyStop(BaseException):
    """Raised inside a student script when the stop flag is set.

    Deliberately a ``BaseException`` rather than an ``Exception``: a student who
    wraps their flight loop in ``try/except Exception`` must not be able to
    swallow the emergency stop and leave the drone flying.
    """


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


@dataclass
class Session:
    """What one script run shares with whoever is hosting it.

    Under ``--script`` the server builds this; standalone, ``Drone`` builds its
    own. Either way ``stop`` is the single flag every API call consults.
    """
    drone: DroneAdapter
    get_detection: Callable[[], Detection]
    emit: Callable[[dict], None] = lambda ev: None
    stop: threading.Event = field(default_factory=threading.Event)
    cfg: VisionConfig = field(default_factory=lambda: DEFAULT_CONFIG)
    settle_s: float = 0.0          # pause after a command so the video can catch up
    found_count: int = 0


# Single-session by construction, exactly like app.state.interp. Set by the
# script runner on the worker thread before the student's file is loaded, so a
# bare `Drone()` in their script finds the server's adapter, detector and stop
# flag with no ceremony.
_active: Session | None = None
_active_lock = threading.Lock()


def bind_session(session: Session) -> None:
    global _active
    with _active_lock:
        _active = session


def clear_session(expect: Session | None = None) -> None:
    """Unbind. ``expect`` guards against a stopped-but-still-running script
    clearing a session that a later run has since installed."""
    global _active
    with _active_lock:
        if expect is None or _active is expect:
            _active = None


def current_session() -> Session | None:
    with _active_lock:
        return _active


@dataclass(frozen=True)
class TargetView:
    """A victim marker in the units a student thinks in — no pixels."""
    distance_m: float
    distance_cm: float
    bearing_deg: float         # + = to the right of the drone's nose
    elevation_deg: float       # + = above the camera
    position: str              # "left" | "center" | "right"

    def __str__(self):
        return (f"victim {self.distance_cm:.0f} cm away, "
                f"{self.bearing_deg:+.0f} deg ({self.position})")


def _view(t: Target | None) -> TargetView | None:
    if t is None:
        return None
    return TargetView(distance_m=t.distance_m, distance_cm=t.distance_m * 100,
                      bearing_deg=t.bearing_deg, elevation_deg=t.elevation_deg,
                      position=t.position)


def _standalone_session(adapter=None, *, sim=False, seed=None,
                        cfg: VisionConfig = DEFAULT_CONFIG) -> Session:
    if adapter is None:
        if sim:
            from .sim.drone import SimDrone
            adapter = SimDrone(seed=seed)
        else:
            from .drone.mock import MockDrone
            adapter = MockDrone()
    adapter.connect()
    # no server video loop here, so detect on demand — hence settle_s stays 0
    tracker = TargetTracker(cfg)
    return Session(drone=adapter, cfg=cfg,
                   get_detection=lambda: tracker.update(adapter.get_frame()))


class Drone:
    """Your drone.

    ``Drone()`` on its own is what you want: launched with
    ``python -m comp1 --script yours.py --drone sim`` it picks up whichever
    drone the server connected to. ``Drone(sim=True)`` runs the simulator by
    itself, with no video window and no emergency-stop button.

    Every method checks the emergency stop before it does anything, so pressing
    STOP interrupts your program even from inside a ``while True:`` loop.
    """

    def __init__(self, adapter: DroneAdapter | None = None, *,
                 sim: bool = False, seed: int | None = None,
                 cfg: VisionConfig | None = None):
        session = current_session()
        if session is None:
            session = _standalone_session(adapter, sim=sim, seed=seed,
                                          cfg=cfg or DEFAULT_CONFIG)
        # captured, never re-read: a stopped script's Drone must stay pinned to
        # its own (stopped) session even after a later run binds a new one
        self._s = session
        self._d = session.drone

    # ------------------------------------------------------------------ safety

    def _check(self):
        if self._s.stop.is_set():
            raise EmergencyStop("emergency stop")

    def _act(self, fn, *args):
        self._check()
        fn(*args)
        self._check()          # a command that finished after STOP ends the script here

    def _warn(self, message: str):
        print(f"! {message}")                        # the terminal they launched from
        self._s.emit({"type": "warning", "message": message})

    def _cm(self, name: str, cm) -> int:
        cm = round(cm)
        fixed = _clamp(cm, MIN_MOVE_CM, MAX_MOVE_CM)
        if fixed != cm:
            # nudge, don't crash — a student's arithmetic producing 5 should not
            # end the mission (see plan §6, runtime clamping)
            self._warn(f"{name}({cm}) is outside {MIN_MOVE_CM}-{MAX_MOVE_CM} cm; using {fixed}")
        return fixed

    def _deg(self, name: str, deg) -> int:
        deg = round(deg)
        fixed = _clamp(deg, MIN_TURN_DEG, MAX_TURN_DEG)
        if fixed != deg:
            self._warn(f"{name}({deg}) is outside {MIN_TURN_DEG}-{MAX_TURN_DEG} deg; using {fixed}")
        return fixed

    # ------------------------------------------------------------------ flight

    def takeoff(self):
        """Take off and hover. Do this before any other movement."""
        self._act(self._d.takeoff)

    def land(self):
        """Land and stop the motors."""
        self._act(self._d.land)

    def forward(self, cm: float):
        """Fly forwards `cm` centimetres (20-500)."""
        self._act(self._d.move, "forward", self._cm("forward", cm))

    def back(self, cm: float):
        """Fly backwards `cm` centimetres (20-500)."""
        self._act(self._d.move, "back", self._cm("back", cm))

    def left(self, cm: float):
        """Slide left `cm` centimetres, still facing the same way (20-500)."""
        self._act(self._d.move, "left", self._cm("left", cm))

    def right(self, cm: float):
        """Slide right `cm` centimetres, still facing the same way (20-500)."""
        self._act(self._d.move, "right", self._cm("right", cm))

    def up(self, cm: float):
        """Climb `cm` centimetres (20-500)."""
        self._act(self._d.move, "up", self._cm("up", cm))

    def down(self, cm: float):
        """Descend `cm` centimetres (20-500)."""
        self._act(self._d.move, "down", self._cm("down", cm))

    def turn_right(self, deg: float):
        """Turn clockwise by `deg` degrees, staying in the same spot."""
        self._act(self._d.rotate, "cw", self._deg("turn_right", deg))

    def turn_left(self, deg: float):
        """Turn anticlockwise by `deg` degrees, staying in the same spot."""
        self._act(self._d.rotate, "ccw", self._deg("turn_left", deg))

    def flip(self, direction: str = "back"):
        """Do a flip: "forward", "back", "left" or "right". Needs >50% battery."""
        self._act(self._d.flip, direction)

    def wait(self, seconds: float):
        """Hover for `seconds`. Interrupted immediately by the emergency stop."""
        self._check()
        if self._s.stop.wait(timeout=max(0.0, seconds)):
            raise EmergencyStop("emergency stop")

    # ----------------------------------------------------------------- sensing

    def sees_target(self) -> bool:
        """True if a red victim marker is visible right now."""
        self._check()
        return self._s.get_detection().found

    def target(self) -> TargetView | None:
        """The victim the drone is locked onto, or None if it cannot see one.

        The lock survives a brief loss of sight and does not jump to whichever
        marker happens to look biggest this frame, so a value you read once
        stays about the same as you fly towards it.
        """
        self._check()
        return _view(self._s.get_detection().target)

    def targets(self) -> list[TargetView]:
        """Every victim marker in view, nearest first."""
        self._check()
        return [_view(t) for t in self._s.get_detection().targets]

    def distance_cm(self) -> float | None:
        """How far away the victim is, in centimetres — None if none is visible."""
        t = self.target()
        return t.distance_cm if t else None

    def bearing_deg(self) -> float | None:
        """Which way to turn to face the victim: + is right, - is left.

        None if no victim is visible.
        """
        t = self.target()
        return t.bearing_deg if t else None

    def elevation_deg(self) -> float | None:
        """How far above (+) or below (-) the camera the victim sits, in degrees."""
        t = self.target()
        return t.elevation_deg if t else None

    # ---------------------------------------------------------------- missions

    def approach_target(self, stop_distance_cm: float | None = None) -> bool:
        """Turn towards the victim, fly to it, and stop a safe distance short.

        Returns True once it is in position, False if the victim was lost or it
        ran out of steps. Same controller as the `approach marker` block: turn
        by the measured bearing error, then step forward by the measured range
        error, both clamped to what the drone will actually honour.
        """
        cfg = self._s.cfg
        stop_m = (cfg.approach_stop_distance_m if stop_distance_cm is None
                  else stop_distance_cm / 100.0)
        lost = 0
        for _ in range(cfg.approach_max_steps):
            self._check()
            det = self._s.get_detection()
            if not det.found:
                lost += 1
                if lost >= cfg.approach_lost_limit:
                    return False
                self.wait(0.3)
                continue
            lost = 0
            bearing, distance = det.bearing_deg, det.distance_m
            if abs(bearing) > cfg.approach_bearing_deadband_deg:
                deg = _clamp(round(abs(bearing)),
                             cfg.approach_min_turn_deg, cfg.approach_max_turn_deg)
                self._act(self._d.rotate, "cw" if bearing > 0 else "ccw", deg)
            elif distance > stop_m:
                remaining_cm = (distance - stop_m) * 100
                if remaining_cm < cfg.approach_min_step_cm:
                    return True                   # closer than one step — done
                self._act(self._d.move, "forward",
                          _clamp(round(remaining_cm),
                                 cfg.approach_min_step_cm, cfg.approach_max_step_cm))
            else:
                return True
            self.wait(self._s.settle_s)
        return False

    def mark_found(self):
        """Signal that you have found a victim (requirements §2.1) and count it."""
        self._act(self._d.flip, "back")
        self._s.found_count += 1
        self._s.emit({"type": "found_count", "count": self._s.found_count})

    @property
    def found_count(self) -> int:
        """How many victims you have marked so far this run."""
        return self._s.found_count

    # --------------------------------------------------------------- telemetry

    @property
    def battery(self) -> int:
        """Battery charge, 0-100."""
        self._check()
        return self._d.battery()

    @property
    def height(self) -> float:
        """Height above the floor in centimetres (0 when landed)."""
        self._check()
        reader = getattr(self._d, "height_cm", None)
        if callable(reader):
            return float(reader())
        # DroneAdapter has no height yet (plan phase 4 adds telemetry); the
        # simulator's pose is the one thing available today
        z = getattr(self._d, "z", None)
        return round(z * 100, 1) if z is not None else 0.0


class ScriptRun:
    """One student-script execution.

    Mirrors ``Interpreter``'s surface — ``request_stop()`` plus ``await run()``
    — so the server can hold either in ``app.state.interp`` and route stop and
    e-stop to it without caring which pathway is flying. That shared slot is
    also what makes a block run and a script run mutually exclusive.

    The script body runs on a **daemon** thread of our own rather than
    ``asyncio.to_thread``: the interpreter joins executor threads at exit, so a
    student's runaway loop in a pooled thread would wedge shutdown.
    """

    def __init__(self, drone: DroneAdapter,
                 get_detection: Callable[[], Detection],
                 emit: Callable[[dict], None],
                 cfg: VisionConfig = DEFAULT_CONFIG, *,
                 path: str | Path | None = None, name: str | None = None,
                 settle_s: float = 0.2, grace_s: float = 3.0):
        self._drone = drone
        self._raw_emit = emit
        self.path = Path(path) if path else None
        self.name = name or (self.path.name if self.path else "script")
        self.grace_s = grace_s
        self.session = Session(drone=drone, get_detection=get_detection,
                               emit=self._emit, cfg=cfg, settle_s=settle_s)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._fut: asyncio.Future | None = None
        self._watchdog = None

    def _emit(self, ev: dict):
        loop = self._loop
        if loop is None or threading.get_ident() == self._loop_thread:
            self._raw_emit(ev)
        else:
            loop.call_soon_threadsafe(self._raw_emit, ev)   # worker thread -> loop

    def request_stop(self):
        """Ask the script to stop. Called from the server's e-stop/stop handlers."""
        self.session.stop.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._arm_watchdog)

    def _arm_watchdog(self):
        if self._watchdog is None and self._fut is not None and not self._fut.done():
            self._watchdog = self._loop.call_later(self.grace_s, self._abandon)

    def _abandon(self):
        """The script ignored the stop flag — give up waiting for its thread.

        Only reachable if the student is looping without calling any drone
        method. The drone is already stopped by the server, and the script's
        ``Drone`` can never command it again, so the only thing left to do is
        free the run slot.
        """
        self._resolve("abandoned",
                      "script did not stop — a loop with no drone commands? "
                      "It can no longer control the drone.")

    def _resolve(self, reason: str, detail: str):
        if self._fut is not None and not self._fut.done():
            self._fut.set_result((reason, detail))

    async def run(self, target: Callable[[], None] | None = None) -> tuple[str, str]:
        """Run the script (or a callable, for tests) and report how it ended."""
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._fut = self._loop.create_future()
        self._emit({"type": "script", "state": "started", "name": self.name})
        threading.Thread(target=self._worker, args=(target,),
                         name="student-script", daemon=True).start()
        reason, detail = await self._fut
        if self._watchdog is not None:
            self._watchdog.cancel()
        if reason != "done":
            clear_session(self.session)          # no-op unless the thread is abandoned
            try:
                await asyncio.to_thread(self._drone.land)
            except Exception:
                pass
        self._emit({"type": "script", "state": reason, "detail": detail})
        self._emit({"type": "finished", "reason": reason, "detail": detail})
        return reason, detail

    def _worker(self, target):
        bind_session(self.session)
        reason, detail = "done", ""
        try:
            if self.session.stop.is_set():
                raise EmergencyStop("stopped before start")
            (target or self._load)()
        except EmergencyStop:
            reason = "stopped"
        except SystemExit:
            pass                                 # a student's sys.exit() is a clean finish
        except BaseException as exc:
            reason, detail = "error", f"{type(exc).__name__}: {exc}"
            traceback.print_exc()                # the student needs the line number
        finally:
            clear_session(self.session)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._resolve, reason, detail)

    def _load(self):
        if self.path is None:
            raise ValueError("ScriptRun needs a path or a callable")
        # the student's own file, run exactly as `python their_file.py` would.
        # This is not the forbidden thing: the ban is on compiling *blocks* into
        # Python — see docs/architecture/platform-options.md §2A.
        runpy.run_path(str(self.path), run_name="__main__")
