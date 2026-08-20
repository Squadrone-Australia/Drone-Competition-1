import asyncio
import base64
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__, settings as settings_store, update as updater
from .api import ScriptRun
from .drone.base import DroneAdapter
from .interpreter import Interpreter
from .paths import is_frozen
from .protocol import Program
from .sim.mission import MissionScorer
from .vision.calibration import (
    CalibrationError,
    auto_suggest_hsv,
    check_coverage,
    config_with_hsv,
    draw_calibration_preview,
    hsv_values,
    suggest_hsv,
)
from .vision.config import DEFAULT_CONFIG, VisionConfig
from .vision.detector import Detection, TargetTracker, draw_overlay
from .vision.obstacles import is_in_the_way

FRONTEND_DIR = Path(__file__).parent / "frontend"
FRAME_INTERVAL = 0.1  # ~10 fps
# Pose is a handful of floats, so it can run far faster than video. It has to:
# at 10 Hz the third-person view stutters, and no amount of browser-side
# smoothing hides a 100 ms step in heading during a fast yaw.
POSE_INTERVAL = 1 / 30
SCRIPT_CLIENT_WAIT = 5.0  # how long a --script run holds off for the browser
#: How often the watchdog looks at the hardware link, and how long it waits
#: between reconnection attempts. A reboot of the aircraft takes several
#: seconds; retrying faster than this only fills the console.
LINK_CHECK_INTERVAL = 2.0
#: How often the aircraft is asked for its charge. A Tello answers `get_battery`
#: over the same command socket every flight command uses, so this is kept slow:
#: the reading moves by one point every few minutes, and each poll is a datagram
#: that has to be paired with its reply.
BATTERY_INTERVAL = 5.0
#: How long the startup update check is allowed to hold anything up: nothing at
#: all. It runs on a thread and reports itself whenever it finishes, which at a
#: venue with no route to the internet is "never, quietly".
UPDATE_CHECK_DELAY = 2.0
#: How often the idle watch looks at whether anybody is still watching.
IDLE_CHECK_INTERVAL = 1.0
#: How long every browser window must stay shut before the program closes
#: itself. The packaged build has no window of its own, so a tab closed and
#: never reopened would otherwise leave an invisible process running until the
#: laptop is rebooted — still holding the aircraft's video port. Generous enough
#: that a page refresh (which reconnects in well under a second) and a laptop
#: waking from sleep are never mistaken for "the student has finished".
DEFAULT_IDLE_TIMEOUT = 30.0


def _new_tello() -> DroneAdapter:
    # Keep this lazy: a simulator launch must not touch the hardware pathway.
    # The adapter is constructed only after a deliberate click in the browser.
    from .drone.tello import TelloDrone

    return TelloDrone()


def _new_simulator() -> DroneAdapter:
    from .sim.drone import SimDrone

    return SimDrone()


def create_app(
    drone: DroneAdapter,
    cfg: VisionConfig = DEFAULT_CONFIG,
    *,
    script: str | Path | None = None,
    script_delay: float = 1.5,
    tello_factory: Callable[[], DroneAdapter] = _new_tello,
    simulator_factory: Callable[[], DroneAdapter] = _new_simulator,
    settings_path: Path | None = None,
    update_check: Callable[[], "updater.Release | None"] | None = None,
    shutdown: Callable[[], None] | None = None,
    idle_timeout: float | None = None,
) -> FastAPI:
    """Build the application.

    ``settings_path`` is opt-in on purpose: with no path the server never writes
    to the user's profile, which is what keeps the test suite (and a developer
    run) from leaving preferences behind. ``comp1.__main__`` passes the real
    one. ``update_check`` is likewise injected rather than imported, so no test
    can reach the network.

    ``shutdown`` is how the program stops itself. The packaged build is windowed
    — there is no console to close and no tray icon — so the browser page *is*
    the window, and it needs both a way to say "close the program" and a way for
    the program to notice that every window has gone. Without it there is no
    quit at all short of Task Manager. ``comp1.__main__`` passes a hook that
    ends the uvicorn server; tests leave it unset, and the browser then hides
    the Quit button rather than offering one that does nothing.

    ``idle_timeout`` arms the same shutdown after every browser window has been
    closed for that long. Left unset (tests, ``--no-browser``, a developer run
    that owns its own terminal) the program simply keeps running.
    """
    # Each app gets an independent runtime config. In particular, a calibration
    # session must never mutate DEFAULT_CONFIG or leak into a later test/server.
    cfg = replace(cfg)
    initial_hsv = hsv_values(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        startup_error = None
        try:
            drone.connect()
        except Exception as exc:
            # `--drone tello` launched before the Wi-Fi is joined must not kill
            # the server. The watchdog keeps trying, so joining the network is
            # all the operator has to do — no relaunch, and the browser is up
            # to say what is wrong in the meantime.
            drone.link_ok = False
            startup_error = f"could not connect to the drone: {exc}"
        app.state.drone = drone
        # Keep the configured simulator (seed, scenery, noise, and any teacher
        # edits) available while a real Tello is active so the UI can switch
        # back to the same arena rather than silently creating a different one.
        app.state.simulator = drone if drone.mode == "sim" else None
        app.state.drone_switching = False
        # Last link state broadcast to the browser. Tracked separately from
        # ``drone.link_ok`` so the watchdog reports transitions, not every tick.
        app.state.link_ok = startup_error is None
        app.state.link_message = startup_error
        app.state.latest_detection = Detection(found=False)
        app.state.latest_frame = None
        app.state.tracker = TargetTracker(cfg)
        # Last charge read from the active drone; None means "not known yet",
        # which is what a client sees before the first poll and after a switch.
        app.state.battery = None
        app.state.clients = set()
        # "Has a browser ever connected?" — the idle watch must not close the
        # program during the second or two between the server coming up and the
        # browser it launched arriving.
        app.state.seen_client = False
        # Latched once the program is on its way out, so the idle watch stops
        # counting and a second quit cannot race the first.
        app.state.quitting = False
        # ground truth for the mission panel; None whenever the adapter has no
        # arena to score against (mock, real Tello)
        app.state.scorer = _new_scorer(app)
        app.state.mission = None
        # one slot for whatever is flying — Interpreter or ScriptRun. Both expose
        # request_stop(), so stop/e-stop routing below is pathway-agnostic, and a
        # block program cannot start while a script is running.
        app.state.interp = None
        # The newest release, once the check has come back with one. None means
        # "no newer version, or we could not tell" — the browser cannot and
        # should not distinguish the two.
        app.state.update = None
        app.state.update_task = (
            asyncio.create_task(_update_loop(app)) if update_check else None
        )
        app.state.idle_task = (
            asyncio.create_task(_idle_loop(app))
            if idle_timeout and shutdown
            else None
        )
        app.state.video_task = asyncio.create_task(_video_loop(app))
        app.state.pose_task = asyncio.create_task(_pose_loop(app))
        app.state.link_task = asyncio.create_task(_link_loop(app))
        app.state.battery_task = asyncio.create_task(_battery_loop(app))
        app.state.script_task = (
            asyncio.create_task(_script_loop(app)) if script else None
        )
        yield
        if app.state.update_task:
            app.state.update_task.cancel()
        if app.state.idle_task:
            app.state.idle_task.cancel()
        app.state.video_task.cancel()
        app.state.pose_task.cancel()
        app.state.link_task.cancel()
        app.state.battery_task.cancel()
        if app.state.script_task:
            # cancelling the task only drops the await — tell the script itself
            if isinstance(app.state.interp, ScriptRun):
                app.state.interp.request_stop()
            app.state.script_task.cancel()
        # Hand back the video port and the aircraft object. Without this a
        # relaunch of the app inside the same process (the test suite, a
        # reloader) finds the stream still held by the previous run.
        await _close_drone(app.state.drone)

    app = FastAPI(lifespan=lifespan)

    async def _video_loop(app: FastAPI):
        last_telemetry = None
        while True:
            active_drone = app.state.drone
            try:
                frame = await asyncio.to_thread(active_drone.get_frame)
            except Exception:
                # A camera that throws must not take the video loop down with
                # it: this task never restarts, so the dead loop would outlive
                # the fault and the picture would stay black even after the
                # drone came back. The watchdog below does the recovering.
                active_drone.link_ok = False
                frame = None
            if frame is not None:
                # detection runs on the raw sensor frame; every overlay below is
                # applied to a copy, so nothing we draw can be detected
                app.state.latest_frame = frame.copy()
                det = app.state.tracker.update(frame)
                app.state.latest_detection = det
                small = (
                    cv2.resize(frame, (640, 480)) if frame.shape[1] != 640 else frame
                )
                display = active_drone.annotate(draw_overlay(small, det))
                ok, jpeg = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    await _broadcast_bytes(app, jpeg.tobytes())
                telemetry = _telemetry(det)
                if telemetry != last_telemetry:
                    last_telemetry = telemetry
                    await _broadcast_json(app, telemetry)
            await asyncio.sleep(FRAME_INTERVAL)

    async def _pose_loop(app: FastAPI):
        """Feed the browser's third-person view.

        Display data only — see ``DroneAdapter.pose``. Adapters that have no
        arena-absolute pose (mock, real Tello) return None and this loop idles.
        """
        last_drone = None
        last = None
        while True:
            active_drone = app.state.drone
            if active_drone is not last_drone:
                last_drone = active_drone
                last = None
            pose = active_drone.pose()
            if pose is not None and pose != last and app.state.clients:
                last = pose
                await _broadcast_json(app, {"type": "pose", **pose})
                # arrival and landing are pose facts, so this is where success is
                # noticed — on change only, like telemetry
                await _publish_mission(app, pose)
            await asyncio.sleep(POSE_INTERVAL)

    async def _link_loop(app: FastAPI):
        """Watch the hardware link and put it back up by itself.

        A Tello that is rebooted mid-session comes back as a brand-new SDK
        session: the old one answers nothing and its video stream never resumes.
        Nothing in the protocol announces that, so it is noticed the only way it
        can be — the adapter stops getting frames and answers — and repaired the
        only way it can be, by connecting again from scratch. Before this loop
        existed the only cure was restarting the whole program, which on
        competition day costs a team its slot.
        """
        while True:
            await asyncio.sleep(LINK_CHECK_INTERVAL)
            active_drone = app.state.drone
            if app.state.drone_switching:
                continue
            if active_drone.link_ok:
                if not app.state.link_ok:
                    await _publish_link(app, True, "drone reconnected")
                continue
            if app.state.link_ok:
                await _publish_link(app, False, "lost contact with the drone")
            # Never reconnect underneath a flying mission: the interpreter is
            # issuing commands on this same adapter from a worker thread. The
            # mission fails on the first unanswered command anyway, and the
            # retry below picks it up as soon as it has stopped.
            if app.state.interp is not None:
                continue
            try:
                await asyncio.to_thread(active_drone.reconnect)
            except Exception:
                continue  # still gone — try again next tick, quietly
            if app.state.drone is active_drone and active_drone.link_ok:
                app.state.latest_frame = None
                app.state.tracker = TargetTracker(cfg)
                app.state.latest_detection = Detection(found=False)
                await _publish_link(app, True, "drone reconnected")

    async def _battery_loop(app: FastAPI):
        """Keep the app bar's charge reading current.

        Never polls while a mission is running. On a real Tello the interpreter
        is issuing commands on this same adapter from a worker thread, and
        djitellopy pairs replies with commands positionally — an extra
        ``get_battery`` slipped in from here would offset every reply after it
        and each command would read the answer to the one before. The reading
        simply holds its last value for the length of a flight, which is what a
        charge that moves by a point every few minutes can afford to do.
        """
        while True:
            active_drone = app.state.drone
            if app.state.interp is None and not app.state.drone_switching:
                try:
                    level = await asyncio.to_thread(active_drone.battery)
                except Exception:
                    # A command that raises means the aircraft is not answering;
                    # the link watchdog does the repairing, this just stops
                    # reporting a charge nobody can vouch for.
                    active_drone.link_ok = False
                    level = None
                if app.state.drone is active_drone and level != app.state.battery:
                    app.state.battery = level
                    await _broadcast_json(app, _battery_message(app))
            await asyncio.sleep(BATTERY_INTERVAL)

    async def _update_loop(app: FastAPI):
        """Ask GitHub once, well after the app is usable, and stay quiet.

        Deliberately a single shot rather than a poll: the check exists so a
        classroom laptop drifts back into date between sessions, not so it can
        interrupt one. Runs on a thread because ``urllib`` blocks, and the event
        loop is carrying video and pose while this waits on a socket that, at a
        venue, is going nowhere.
        """
        await asyncio.sleep(UPDATE_CHECK_DELAY)
        try:
            release = await asyncio.to_thread(update_check)
        except Exception:
            return  # check() swallows its own failures; this covers the rest
        if release is None:
            return
        app.state.update = release
        await _broadcast_json(app, _update_message(app))

    async def _idle_loop(app: FastAPI):
        """Close the program once every browser window has gone.

        The packaged build has no window of its own, so "the student closed the
        tab" is the only signal that they are finished — and before this loop
        existed it was a signal nothing acted on: the process stayed alive,
        invisible, holding the aircraft's video port, until the laptop was
        rebooted or somebody found it in Task Manager.

        Deliberately a countdown rather than an immediate exit. A page refresh,
        a laptop waking up, and a student dragging the tab into another window
        all disconnect briefly, and none of them means "quit".
        """
        idle = 0.0
        # Never coarser than the timeout itself, so a short one (a test, a
        # deliberately impatient setting) is honoured rather than rounded up.
        interval = min(IDLE_CHECK_INTERVAL, idle_timeout)
        while True:
            await asyncio.sleep(interval)
            if app.state.quitting:
                return
            busy = (
                app.state.clients
                or app.state.interp is not None
                or app.state.drone_switching
                # Never before the first browser arrives: the server comes up a
                # second or so ahead of the window it launched.
                or not app.state.seen_client
            )
            if busy:
                idle = 0.0
                continue
            idle += interval
            if idle >= idle_timeout:
                await _quit(app)
                return

    async def _quit(app: FastAPI):
        """Stop the program. The drone is released by the lifespan shutdown."""
        if app.state.quitting:
            return
        app.state.quitting = True
        # Told before the socket dies, so any other tab can say what happened
        # instead of sitting on "disconnected, retrying" forever.
        await _broadcast_json(app, {"type": "quitting"})
        shutdown()

    async def _install_update(app: FastAPI, release):
        """Download, verify, hand over to the installer, and step aside.

        The download is a few hundred megabytes of OpenCV and NumPy, so it runs
        on a thread with the progress said out loud — a browser that sits mute
        for two minutes after a click reads as broken.
        """
        await _broadcast_json(
            app,
            {
                "type": "update_progress",
                "state": "downloading",
                "version": release.version,
            },
        )
        try:
            installer = await asyncio.to_thread(updater.download, release)
        except Exception as exc:
            # The deliberate half, unlike the check: the operator asked for
            # this and is owed the reason it did not happen.
            await _broadcast_json(
                app,
                {"type": "update_progress", "state": "failed", "message": str(exc)},
            )
            return
        await _broadcast_json(
            app,
            {
                "type": "update_progress",
                "state": "installing",
                "version": release.version,
            },
        )
        # Let go of the aircraft and its video port *before* handing over: the
        # installer terminates this process (see INSTALLER_ARGS), so anything
        # left until afterwards never happens. close() never flies the drone.
        await _close_drone(app.state.drone)
        try:
            updater.launch_installer(installer)
        except Exception as exc:
            await _broadcast_json(
                app,
                {"type": "update_progress", "state": "failed", "message": str(exc)},
            )

    def _update_message(app: FastAPI) -> dict:
        release = app.state.update
        return {
            "type": "update_available",
            "current": __version__,
            **(release.to_json() if release else {"version": None, "notes": ""}),
        }

    def _settings_message() -> dict:
        return {
            "type": "settings",
            "version": __version__,
            # The browser hides anything that only makes sense for an installed
            # copy — there is no installer to run against a source checkout.
            "installed": is_frozen(),
            "persisted": settings_path is not None,
            # Without a shutdown hook the browser must not offer a Quit button:
            # a run from a terminal is quit with Ctrl+C, and a button that did
            # nothing would be worse than no button.
            "can_quit": shutdown is not None,
            "settings": settings_store.load(settings_path).to_json(),
        }

    def _remember(changes: dict) -> None:
        """Persist preferences, if this server was given somewhere to put them.

        Takes a plain dict rather than keyword arguments because the browser's
        half of this arrives as untrusted JSON — unknown fields are dropped by
        ``settings._clean``, and none of them can collide with a parameter name.
        """
        if settings_path is not None and isinstance(changes, dict):
            settings_store.update(settings_path, **changes)

    def _battery_message(app: FastAPI) -> dict:
        return {"type": "battery", "percent": app.state.battery}

    async def _publish_link(app: FastAPI, ok: bool, message: str | None):
        app.state.link_ok = ok
        app.state.link_message = message
        await _broadcast_json(
            app,
            {
                "type": "drone_link",
                "ok": ok,
                "mode": app.state.drone.mode,
                "message": message,
            },
        )

    async def _close_drone(candidate: DroneAdapter):
        """Release an adapter that is no longer the active drone.

        Dropping the reference is not enough for a Tello — its video decoder
        keeps the UDP port for the life of the process, and the *next*
        connection then cannot open a stream. That is why switching to the
        simulator and back used to require restarting the program.
        """
        try:
            await asyncio.to_thread(candidate.close)
        except Exception:
            pass  # housekeeping must never block a drone switch

    def _new_scorer(app: FastAPI):
        scene = app.state.drone.scene()
        return MissionScorer(scene) if scene else None

    def _current_scenery(app: FastAPI):
        scene = app.state.drone.scene()
        return scene.get("name") if scene else None

    async def _rebuild_arena(app: FastAPI, **kwargs):
        """Apply an arena edit, then put everything watching back to the start."""
        active_drone = app.state.drone
        await asyncio.to_thread(lambda: active_drone.load_scenery(**kwargs))
        await _reset(app)
        await _broadcast_json(app, {"type": "scene", "scene": active_drone.scene()})
        await _broadcast_json(
            app,
            {
                "type": "sceneries",
                "sceneries": active_drone.scenery_catalog(),
                "current": _current_scenery(app),
            },
        )

    async def _publish_mission(app: FastAPI, pose=None, signal=None):
        scorer = app.state.scorer
        if scorer is None:
            return
        state = scorer.state(pose if pose is not None else app.state.drone.pose())
        if state == app.state.mission and signal is None:
            return
        app.state.mission = state
        await _broadcast_json(app, {"type": "mission", "signal": signal, **state})

    def _telemetry(det: Detection) -> dict:
        return {
            "type": "telemetry",
            "visible": det.found,
            "count": det.count,
            "distance_cm": round(det.distance_m * 100) if det.found else None,
            "bearing_deg": round(det.bearing_deg) if det.found else None,
            "elevation_deg": round(det.elevation_deg) if det.found else None,
            # Obstacles are reported whether or not a target is in view: the
            # moment they matter most is while the drone is still searching.
            "obstacle_count": det.obstacle_count,
            "obstacle_distance_cm": (
                round(det.obstacle.distance_m * 100) if det.obstacle else None
            ),
            "obstacle_bearing_deg": (
                round(det.obstacle.bearing_deg) if det.obstacle else None
            ),
            "obstacle_ahead": is_in_the_way(det.obstacle, cfg),
        }

    def _vision_message(message_type: str, active_cfg: VisionConfig) -> dict:
        return {"type": message_type, "config": hsv_values(active_cfg)}

    def _vision_preview(active_cfg: VisionConfig) -> str:
        frame = app.state.latest_frame
        if frame is None:
            raise CalibrationError("no camera frame is available yet")
        small = cv2.resize(frame, (640, 480)) if frame.shape[1] != 640 else frame
        preview = draw_calibration_preview(small, active_cfg)
        ok, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            raise CalibrationError("could not create the mask preview")
        return base64.b64encode(jpeg.tobytes()).decode("ascii")

    def _apply_hsv(values: dict) -> None:
        updated = config_with_hsv(cfg, values)
        for key in ("lower1", "upper1", "lower2", "upper2"):
            setattr(cfg, key, getattr(updated, key))
        app.state.tracker = TargetTracker(cfg)
        app.state.latest_detection = Detection(found=False)

    async def _broadcast_bytes(app, data: bytes):
        for ws in list(app.state.clients):
            try:
                await ws.send_bytes(data)
            except Exception:
                app.state.clients.discard(ws)

    async def _broadcast_json(app, data: dict):
        for ws in list(app.state.clients):
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                app.state.clients.discard(ws)

    async def _reset(app: FastAPI):
        """Start state, for the drone and for everything watching it.

        The tracker goes too: a marker lock held over from the previous attempt
        would have the new run reacting to something the drone is no longer
        looking at.
        """
        active_drone = app.state.drone
        await asyncio.to_thread(active_drone.reset)
        app.state.tracker = TargetTracker(cfg)
        app.state.latest_detection = Detection(found=False)
        app.state.scorer = _new_scorer(app)
        app.state.mission = None
        # `repositioned` is the difference between "the drone is on its pad" and
        # "the counters are zero but the aircraft has not moved". The browser must
        # not claim the former on hardware, where reset() is a no-op.
        await _broadcast_json(
            app, {"type": "reset", "repositioned": active_drone.can_reset}
        )
        await _publish_mission(app)

    async def _begin_switch(app: FastAPI):
        app.state.drone_switching = True
        await _broadcast_json(
            app, {"type": "drone_mode", "mode": app.state.drone.mode, "switching": True}
        )

    async def _abandon_switch(app: FastAPI, message: str):
        app.state.drone_switching = False
        await _broadcast_json(
            app,
            {"type": "drone_mode", "mode": app.state.drone.mode, "switching": False},
        )
        await _broadcast_json(app, {"type": "error", "message": message})

    async def _activate(app: FastAPI, candidate: DroneAdapter):
        """Make a connected adapter the active drone and re-sync every panel."""
        previous = app.state.drone
        app.state.drone = candidate
        app.state.drone_switching = False
        app.state.latest_frame = None
        app.state.tracker = TargetTracker(cfg)
        app.state.latest_detection = Detection(found=False)
        app.state.scorer = _new_scorer(app)
        app.state.mission = None
        # The previous drone's charge says nothing about this one: the simulator
        # is always full, an aircraft rarely is. Blank it until the next poll.
        app.state.battery = None
        # The simulator is kept, not closed — it holds the seed, the scenery and
        # any teacher edits, so switching back returns to the same arena.
        if previous is not candidate and previous is not app.state.simulator:
            await _close_drone(previous)
        await _publish_link(app, candidate.link_ok, None)
        await _broadcast_json(app, {"type": "scene", "scene": candidate.scene()})
        await _broadcast_json(
            app,
            {
                "type": "sceneries",
                "sceneries": candidate.scenery_catalog(),
                "current": _current_scenery(app),
            },
        )
        await _broadcast_json(
            app, {"type": "drone_mode", "mode": candidate.mode, "switching": False}
        )
        await _broadcast_json(app, _battery_message(app))

    async def _switch_to_tello(app: FastAPI):
        """Connect hardware first, then atomically make it the active adapter.

        Keeping the simulator active until ``connect`` succeeds means a missing
        Tello or incorrect Wi-Fi never leaves the application without a usable
        drone. Missions are refused while the connection attempt is in flight.

        Also the reconnect path: a Tello that has been rebooted needs a whole new
        adapter — the old one's SDK session, response buffer and video stream all
        died with it — so the previous one is closed rather than reused.
        """
        await _begin_switch(app)
        previous_tello = app.state.drone if app.state.drone.mode == "tello" else None
        if previous_tello is not None:
            # Free the video port *before* the new stream tries to open it.
            await _close_drone(previous_tello)
        candidate = None
        try:
            candidate = tello_factory()
            await asyncio.to_thread(candidate.connect)
        except Exception as exc:
            if candidate is not None:
                await _close_drone(candidate)  # half-open sockets are still sockets
            await _abandon_switch(app, f"could not connect to Tello: {exc}")
            return
        await _activate(app, candidate)

    async def _switch_to_simulator(app: FastAPI):
        """Restore the configured simulator, creating one for Tello-first launches."""
        await _begin_switch(app)
        try:
            candidate = app.state.simulator or simulator_factory()
            if app.state.simulator is None:
                await asyncio.to_thread(candidate.connect)
                app.state.simulator = candidate
        except Exception as exc:
            await _abandon_switch(app, f"could not start simulator: {exc}")
            return
        await _activate(app, candidate)

    def _select_nearest_target():
        """Make the closest currently visible marker the new tracker lock."""
        app.state.latest_detection = app.state.tracker.reacquire_nearest(
            app.state.latest_detection
        )

    def _score(ev: dict):
        """Credit a ``mark found`` at the instant it happens.

        ``mark found`` is a bare counter in the interpreter and in comp1.api —
        neither can know whether the drone was actually beside a target, and the
        simulator can. This has to be synchronous: broadcasting is a task, and a
        whole program can run to completion before a task gets a turn, by which
        point the drone is nowhere near the target it was signalling.
        """
        if ev.get("type") != "found_count" or app.state.scorer is None:
            return None
        return (
            "credited"
            if app.state.scorer.signal(app.state.drone.pose())
            else "no target nearby"
        )

    async def _on_event(ev: dict, signal=None):
        await _broadcast_json(app, ev)
        if signal is not None:
            await _publish_mission(app, signal=signal)

    def _emit(ev: dict):
        """Broadcast an event. Must be called on the event loop thread —
        ``ScriptRun`` marshals its worker thread's events through it."""
        asyncio.ensure_future(_on_event(ev, _score(ev)))

    async def _script_loop(app: FastAPI):
        # don't fly before the student can see it — wait for the browser, but not
        # forever, so `--no-browser --script` still runs
        await asyncio.sleep(script_delay)
        waited = 0.0
        while not app.state.clients and waited < SCRIPT_CLIENT_WAIT:
            await asyncio.sleep(0.05)
            waited += 0.05
        # A click during the short startup delay may still be connecting the
        # Tello. Never capture the simulator halfway through that handoff and
        # then run it invisibly behind the hardware camera feed.
        while app.state.drone_switching:
            await asyncio.sleep(0.05)
        run = ScriptRun(
            app.state.drone,
            lambda: app.state.latest_detection,
            _emit,
            cfg=cfg,
            select_nearest_target=_select_nearest_target,
            path=script,
        )
        app.state.interp = run
        try:
            await run.run()
        finally:
            if app.state.interp is run:
                app.state.interp = None

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        app.state.clients.add(ws)
        app.state.seen_client = True
        loop = asyncio.get_running_loop()

        def emit(ev):
            loop.create_task(_on_event(ev, _score(ev)))

        # The arena is sent per client on connect and re-broadcast to everyone
        # whenever the picker or the plan editor changes it — it is static for
        # the length of a run, not for the length of the session.
        active_drone = app.state.drone
        await ws.send_text(json.dumps({"type": "scene", "scene": active_drone.scene()}))
        await ws.send_text(
            json.dumps(
                {
                    "type": "sceneries",
                    "sceneries": active_drone.scenery_catalog(),
                    "current": _current_scenery(app),
                }
            )
        )
        if app.state.scorer is not None:
            await ws.send_text(
                json.dumps(
                    {
                        "type": "mission",
                        "signal": None,
                        **app.state.scorer.state(active_drone.pose()),
                    }
                )
            )
        await ws.send_text(
            json.dumps(
                {
                    "type": "drone_mode",
                    "mode": active_drone.mode,
                    "switching": app.state.drone_switching,
                }
            )
        )
        await ws.send_text(
            json.dumps(
                {
                    "type": "drone_link",
                    "ok": app.state.link_ok,
                    "mode": active_drone.mode,
                    "message": app.state.link_message,
                }
            )
        )
        await ws.send_text(json.dumps(_battery_message(app)))
        await ws.send_text(json.dumps(_vision_message("vision_config", cfg)))
        await ws.send_text(json.dumps(_settings_message()))
        # The check may well have finished before this client connected — a
        # browser opened late, or reloaded — so replay the result rather than
        # letting the banner depend on the timing of a page refresh.
        if app.state.update is not None:
            await ws.send_text(json.dumps(_update_message(app)))

        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg["type"] == "run":
                    if app.state.drone_switching:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "wait for the drone connection",
                            },
                        )
                        continue
                    if app.state.interp is not None:
                        await _broadcast_json(
                            app, {"type": "error", "message": "already running"}
                        )
                        continue
                    if not app.state.drone.link_ok:
                        # Flying at an aircraft that is not answering ends as a
                        # mission that dies on its first command; say why now.
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "the drone is not connected — "
                                "reconnecting, try again in a moment",
                            },
                        )
                        continue
                    try:
                        program = Program.model_validate(msg["program"])
                    except ValidationError as e:
                        await _broadcast_json(
                            app, {"type": "error", "message": f"invalid program: {e}"}
                        )
                        continue
                    # This is the canonical program the interpreter will run,
                    # after schema validation and v1-to-v2 compatibility lifts.
                    await _broadcast_json(
                        app,
                        {
                            "type": "debug_program",
                            "program": program.model_dump(
                                mode="json", exclude_none=True
                            ),
                        },
                    )
                    # every attempt starts from the same place, so a change in the
                    # program is the only thing that changed
                    await _reset(app)
                    interp = Interpreter(
                        app.state.drone,
                        lambda: app.state.latest_detection,
                        emit,
                        cfg=cfg,
                        select_nearest_target=_select_nearest_target,
                    )
                    app.state.interp = interp

                    async def _run(interp=interp, program=program):
                        try:
                            await interp.run(program)
                        finally:
                            app.state.interp = None

                    asyncio.create_task(_run())
                elif msg["type"] == "stop":
                    if app.state.interp:
                        app.state.interp.request_stop()
                elif msg["type"] == "reset":
                    if app.state.drone_switching:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "wait for the drone connection",
                            },
                        )
                    elif app.state.interp is not None:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "stop the mission before resetting",
                            },
                        )
                    else:
                        await _reset(app)
                elif msg["type"] in ("scenery", "layout"):
                    # Editing the arena mid-flight would move the ground out from
                    # under a running mission, so it waits — same rule as reset.
                    if app.state.drone_switching:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "wait for the drone connection",
                            },
                        )
                    elif app.state.drone.scenery_catalog() is None:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "this drone has no arena to edit",
                            },
                        )
                    elif app.state.interp is not None:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "stop the mission before changing the arena",
                            },
                        )
                    elif msg["type"] == "scenery":
                        await _rebuild_arena(
                            app,
                            name=msg.get("name"),
                            randomise=bool(msg.get("randomise")),
                        )
                    else:
                        await _rebuild_arena(app, fires=msg.get("fires") or [])
                elif msg["type"] in (
                    "vision_sample",
                    "vision_auto",
                    "vision_preview",
                    "vision_apply",
                    "vision_reset",
                ):
                    if app.state.drone_switching:
                        await ws.send_text(
                            json.dumps(
                                {
                                    "type": "vision_error",
                                    "message": "wait for the drone connection",
                                }
                            )
                        )
                    elif app.state.interp is not None:
                        await ws.send_text(
                            json.dumps(
                                {
                                    "type": "vision_error",
                                    "message": "stop the mission before calibrating vision",
                                }
                            )
                        )
                    else:
                        try:
                            if msg["type"] in ("vision_sample", "vision_auto"):
                                raw = app.state.latest_frame
                                if msg["type"] == "vision_auto":
                                    values, roi = auto_suggest_hsv(raw, cfg)
                                else:
                                    roi = msg.get("roi")
                                    values = suggest_hsv(raw, roi)
                                candidate = config_with_hsv(cfg, values)
                                # gate proposals only: an operator dragging a
                                # slider wide on purpose should get the preview
                                # they asked for, not an error
                                check_coverage(raw, candidate)
                                response = _vision_message(
                                    "vision_suggestion", candidate
                                )
                                response["preview_jpeg"] = _vision_preview(candidate)
                                response["roi"] = [float(v) for v in roi]
                                await ws.send_text(json.dumps(response))
                            elif msg["type"] == "vision_preview":
                                candidate = config_with_hsv(
                                    cfg, msg.get("config") or {}
                                )
                                response = _vision_message("vision_preview", candidate)
                                response["preview_jpeg"] = _vision_preview(candidate)
                                await ws.send_text(json.dumps(response))
                            elif msg["type"] == "vision_apply":
                                _apply_hsv(msg.get("config") or {})
                                # A venue re-tune (§3.1) has to outlive the
                                # session that made it: an operator who
                                # calibrated the gym at 9am should not have to
                                # do it again after lunch because someone
                                # closed the program.
                                _remember({"hsv": hsv_values(cfg)})
                                await _broadcast_json(
                                    app, _vision_message("vision_config", cfg)
                                )
                            else:
                                _apply_hsv(initial_hsv)
                                _remember({"hsv": {}})
                                await _broadcast_json(
                                    app, _vision_message("vision_config", cfg)
                                )
                        except CalibrationError as exc:
                            await ws.send_text(
                                json.dumps(
                                    {"type": "vision_error", "message": str(exc)}
                                )
                            )
                elif msg["type"] == "estop":
                    if app.state.interp:
                        app.state.interp.request_stop()
                    try:
                        await asyncio.to_thread(app.state.drone.emergency)
                    except Exception as exc:
                        # The stop button must survive an unreachable aircraft:
                        # the program stop above already happened, and killing
                        # this socket would take the button away entirely.
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": f"emergency stop could not reach the drone: {exc}",
                            },
                        )
                    await _broadcast_json(app, {"type": "estopped"})
                elif msg["type"] == "reconnect_drone":
                    # The manual twin of the watchdog: an operator who has just
                    # power-cycled the aircraft should not have to wait for the
                    # timeout, and a link that looks fine but is not (stale SDK
                    # session after a reboot) has no other cure.
                    if app.state.drone_switching:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "a drone connection is already in progress",
                            },
                        )
                    elif app.state.interp is not None:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "stop the mission before reconnecting",
                            },
                        )
                    elif app.state.drone.mode != "tello":
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "only the real Tello can be reconnected",
                            },
                        )
                    else:
                        await _switch_to_tello(app)
                elif msg["type"] == "switch_drone":
                    requested_mode = msg.get("mode")
                    if requested_mode not in ("sim", "tello"):
                        await _broadcast_json(
                            app, {"type": "error", "message": "unsupported drone mode"}
                        )
                    elif app.state.drone.mode == requested_mode:
                        await _broadcast_json(
                            app,
                            {
                                "type": "drone_mode",
                                "mode": requested_mode,
                                "switching": False,
                            },
                        )
                    elif app.state.interp is not None:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "stop the mission before switching drones",
                            },
                        )
                    elif app.state.drone_switching:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "a drone connection is already in progress",
                            },
                        )
                    elif requested_mode == "tello":
                        await _switch_to_tello(app)
                    else:
                        await _switch_to_simulator(app)
                elif msg["type"] == "quit":
                    if shutdown is None:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "this copy is run from a terminal — "
                                "press Ctrl+C there to close it",
                            },
                        )
                    elif app.state.interp is not None:
                        # Same family as the calibration and update guards, and
                        # the same reason as the update one: closing the program
                        # underneath a flying drone leaves it in the air with
                        # nothing controlling it.
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "stop the mission before closing",
                            },
                        )
                    else:
                        await _quit(app)
                elif msg["type"] == "save_settings":
                    # Preferences only — what the program should default to next
                    # launch. Nothing here takes effect on the running drone;
                    # switching drone or scenery has its own message, and
                    # conflating the two would let a settings panel silently
                    # reconnect hardware mid-session.
                    _remember(msg.get("settings") or {})
                    await _broadcast_json(app, _settings_message())
                elif msg["type"] == "install_update":
                    release = app.state.update
                    if release is None:
                        await _broadcast_json(
                            app,
                            {"type": "error", "message": "no update is available"},
                        )
                    elif app.state.interp is not None:
                        # Same guard as calibration and the drone switch, for a
                        # much better reason: the installer's first act is to
                        # close this process, and doing that to a drone in the
                        # air would leave it hovering with nothing flying it.
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "stop the mission before updating",
                            },
                        )
                    elif app.state.drone_switching:
                        await _broadcast_json(
                            app,
                            {
                                "type": "error",
                                "message": "wait for the drone connection",
                            },
                        )
                    else:
                        await _install_update(app, release)
        except WebSocketDisconnect:
            pass
        finally:
            app.state.clients.discard(ws)

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app
