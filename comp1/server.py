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

from .api import ScriptRun
from .drone.base import DroneAdapter
from .interpreter import Interpreter
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
) -> FastAPI:
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
        app.state.clients = set()
        # ground truth for the mission panel; None whenever the adapter has no
        # arena to score against (mock, real Tello)
        app.state.scorer = _new_scorer(app)
        app.state.mission = None
        # one slot for whatever is flying — Interpreter or ScriptRun. Both expose
        # request_stop(), so stop/e-stop routing below is pathway-agnostic, and a
        # block program cannot start while a script is running.
        app.state.interp = None
        app.state.video_task = asyncio.create_task(_video_loop(app))
        app.state.pose_task = asyncio.create_task(_pose_loop(app))
        app.state.link_task = asyncio.create_task(_link_loop(app))
        app.state.script_task = (
            asyncio.create_task(_script_loop(app)) if script else None
        )
        yield
        app.state.video_task.cancel()
        app.state.pose_task.cancel()
        app.state.link_task.cancel()
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
        neither can know whether the drone was actually beside a fire, and the
        simulator can. This has to be synchronous: broadcasting is a task, and a
        whole program can run to completion before a task gets a turn, by which
        point the drone is nowhere near the fire it was signalling.
        """
        if ev.get("type") != "found_count" or app.state.scorer is None:
            return None
        return (
            "credited"
            if app.state.scorer.signal(app.state.drone.pose())
            else "no fire nearby"
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
        await ws.send_text(json.dumps(_vision_message("vision_config", cfg)))

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
                                await _broadcast_json(
                                    app, _vision_message("vision_config", cfg)
                                )
                            else:
                                _apply_hsv(initial_hsv)
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
        except WebSocketDisconnect:
            pass
        finally:
            app.state.clients.discard(ws)

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app
