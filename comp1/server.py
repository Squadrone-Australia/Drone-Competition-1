import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .api import ScriptRun
from .drone.base import DroneAdapter
from .interpreter import Interpreter
from .protocol import Program
from .sim.mission import MissionScorer
from .vision.config import VisionConfig, DEFAULT_CONFIG
from .vision.detector import Detection, TargetTracker, draw_overlay

FRONTEND_DIR = Path(__file__).parent / "frontend"
FRAME_INTERVAL = 0.1  # ~10 fps
# Pose is a handful of floats, so it can run far faster than video. It has to:
# at 10 Hz the third-person view stutters, and no amount of browser-side
# smoothing hides a 100 ms step in heading during a fast yaw.
POSE_INTERVAL = 1 / 30
SCRIPT_CLIENT_WAIT = 5.0  # how long a --script run holds off for the browser


def _new_tello() -> DroneAdapter:
    # Keep this lazy: a simulator launch must not touch the hardware pathway.
    # The adapter is constructed only after a deliberate click in the browser.
    from .drone.tello import TelloDrone
    return TelloDrone()


def create_app(drone: DroneAdapter, cfg: VisionConfig = DEFAULT_CONFIG, *,
               script: str | Path | None = None, script_delay: float = 1.5,
               tello_factory: Callable[[], DroneAdapter] = _new_tello) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        drone.connect()
        app.state.drone = drone
        app.state.drone_switching = False
        app.state.latest_detection = Detection(found=False)
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
        app.state.script_task = (asyncio.create_task(_script_loop(app))
                                 if script else None)
        yield
        app.state.video_task.cancel()
        app.state.pose_task.cancel()
        if app.state.script_task:
            # cancelling the task only drops the await — tell the script itself
            if isinstance(app.state.interp, ScriptRun):
                app.state.interp.request_stop()
            app.state.script_task.cancel()

    app = FastAPI(lifespan=lifespan)

    async def _video_loop(app: FastAPI):
        last_telemetry = None
        while True:
            active_drone = app.state.drone
            frame = await asyncio.to_thread(active_drone.get_frame)
            if frame is not None:
                # detection runs on the raw sensor frame; every overlay below is
                # applied to a copy, so nothing we draw can be detected
                det = app.state.tracker.update(frame)
                app.state.latest_detection = det
                small = cv2.resize(frame, (640, 480)) if frame.shape[1] != 640 else frame
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
        last = None
        while True:
            pose = app.state.drone.pose()
            if pose is not None and pose != last and app.state.clients:
                last = pose
                await _broadcast_json(app, {"type": "pose", **pose})
                # arrival and landing are pose facts, so this is where success is
                # noticed — on change only, like telemetry
                await _publish_mission(app, pose)
            await asyncio.sleep(POSE_INTERVAL)

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
        await _broadcast_json(app, {"type": "sceneries",
                                    "sceneries": active_drone.scenery_catalog(),
                                    "current": _current_scenery(app)})

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
        await _broadcast_json(app, {"type": "reset", "repositioned": active_drone.can_reset})
        await _publish_mission(app)

    async def _switch_to_tello(app: FastAPI):
        """Connect hardware first, then atomically make it the active adapter.

        Keeping the simulator active until ``connect`` succeeds means a missing
        Tello or incorrect Wi-Fi never leaves the application without a usable
        drone. Missions are refused while the connection attempt is in flight.
        """
        app.state.drone_switching = True
        await _broadcast_json(app, {"type": "drone_mode",
                                    "mode": app.state.drone.mode,
                                    "switching": True})
        try:
            candidate = tello_factory()
            await asyncio.to_thread(candidate.connect)
        except Exception as exc:
            app.state.drone_switching = False
            await _broadcast_json(app, {"type": "drone_mode",
                                        "mode": app.state.drone.mode,
                                        "switching": False})
            await _broadcast_json(app, {"type": "error",
                                        "message": f"could not connect to Tello: {exc}"})
            return

        app.state.drone = candidate
        app.state.drone_switching = False
        app.state.tracker = TargetTracker(cfg)
        app.state.latest_detection = Detection(found=False)
        app.state.scorer = _new_scorer(app)
        app.state.mission = None
        await _broadcast_json(app, {"type": "scene", "scene": candidate.scene()})
        await _broadcast_json(app, {"type": "sceneries",
                                    "sceneries": candidate.scenery_catalog(),
                                    "current": _current_scenery(app)})
        await _broadcast_json(app, {"type": "drone_mode",
                                    "mode": candidate.mode,
                                    "switching": False})

    def _select_nearest_target():
        """Make the closest currently visible marker the new tracker lock."""
        app.state.latest_detection = app.state.tracker.reacquire_nearest(
            app.state.latest_detection)

    def _score(ev: dict):
        """Credit a ``mark found`` at the instant it happens.

        ``mark found`` is a bare counter in the interpreter and in comp1.api —
        neither can know whether the drone was actually beside a victim, and the
        simulator can. This has to be synchronous: broadcasting is a task, and a
        whole program can run to completion before a task gets a turn, by which
        point the drone is nowhere near the victim it was signalling.
        """
        if ev.get("type") != "found_count" or app.state.scorer is None:
            return None
        return ("credited" if app.state.scorer.signal(app.state.drone.pose())
                else "no victim nearby")

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
        run = ScriptRun(app.state.drone, lambda: app.state.latest_detection, _emit, cfg=cfg,
                        select_nearest_target=_select_nearest_target,
                        path=script)
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
        await ws.send_text(json.dumps({"type": "sceneries",
                                       "sceneries": active_drone.scenery_catalog(),
                                       "current": _current_scenery(app)}))
        if app.state.scorer is not None:
            await ws.send_text(json.dumps({"type": "mission", "signal": None,
                                           **app.state.scorer.state(active_drone.pose())}))
        await ws.send_text(json.dumps({"type": "drone_mode",
                                       "mode": active_drone.mode,
                                       "switching": app.state.drone_switching}))

        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg["type"] == "run":
                    if app.state.drone_switching:
                        await _broadcast_json(app, {"type": "error",
                                                    "message": "wait for the drone connection"})
                        continue
                    if app.state.interp is not None:
                        await _broadcast_json(app, {"type": "error", "message": "already running"})
                        continue
                    try:
                        program = Program.model_validate(msg["program"])
                    except ValidationError as e:
                        await _broadcast_json(app, {"type": "error",
                                                    "message": f"invalid program: {e}"})
                        continue
                    # This is the canonical program the interpreter will run,
                    # after schema validation and v1-to-v2 compatibility lifts.
                    await _broadcast_json(app, {"type": "debug_program",
                                                "program": program.model_dump(
                                                    mode="json", exclude_none=True)})
                    # every attempt starts from the same place, so a change in the
                    # program is the only thing that changed
                    await _reset(app)
                    interp = Interpreter(app.state.drone,
                                         lambda: app.state.latest_detection, emit, cfg=cfg,
                                         select_nearest_target=_select_nearest_target)
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
                        await _broadcast_json(app, {"type": "error",
                                                    "message": "wait for the drone connection"})
                    elif app.state.interp is not None:
                        await _broadcast_json(app, {"type": "error",
                                                    "message": "stop the mission before resetting"})
                    else:
                        await _reset(app)
                elif msg["type"] in ("scenery", "layout"):
                    # Editing the arena mid-flight would move the ground out from
                    # under a running mission, so it waits — same rule as reset.
                    if app.state.drone_switching:
                        await _broadcast_json(app, {
                            "type": "error", "message": "wait for the drone connection"})
                    elif app.state.drone.scenery_catalog() is None:
                        await _broadcast_json(app, {
                            "type": "error",
                            "message": "this drone has no arena to edit"})
                    elif app.state.interp is not None:
                        await _broadcast_json(app, {
                            "type": "error",
                            "message": "stop the mission before changing the arena"})
                    elif msg["type"] == "scenery":
                        await _rebuild_arena(app, name=msg.get("name"),
                                             randomise=bool(msg.get("randomise")))
                    else:
                        await _rebuild_arena(app, victims=msg.get("victims") or [])
                elif msg["type"] == "estop":
                    if app.state.interp:
                        app.state.interp.request_stop()
                    await asyncio.to_thread(app.state.drone.emergency)
                    await _broadcast_json(app, {"type": "estopped"})
                elif msg["type"] == "switch_drone":
                    if msg.get("mode") != "tello":
                        await _broadcast_json(app, {"type": "error",
                                                    "message": "unsupported drone mode"})
                    elif app.state.drone.mode == "tello":
                        await _broadcast_json(app, {"type": "drone_mode", "mode": "tello",
                                                    "switching": False})
                    elif app.state.interp is not None:
                        await _broadcast_json(app, {"type": "error",
                                                    "message": "stop the mission before switching drones"})
                    elif app.state.drone_switching:
                        await _broadcast_json(app, {"type": "error",
                                                    "message": "a drone connection is already in progress"})
                    else:
                        await _switch_to_tello(app)
        except WebSocketDisconnect:
            pass
        finally:
            app.state.clients.discard(ws)

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app
