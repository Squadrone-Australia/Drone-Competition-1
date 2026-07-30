import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .drone.base import DroneAdapter
from .interpreter import Interpreter
from .protocol import Program
from .vision.config import VisionConfig, DEFAULT_CONFIG
from .vision.detector import Detection, detect_red_circle, draw_overlay

FRONTEND_DIR = Path(__file__).parent / "frontend"
FRAME_INTERVAL = 0.1  # ~10 fps


def create_app(drone: DroneAdapter, cfg: VisionConfig = DEFAULT_CONFIG) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        drone.connect()
        app.state.latest_detection = Detection(found=False)
        app.state.clients = set()
        app.state.interp = None
        app.state.video_task = asyncio.create_task(_video_loop(app))
        yield
        app.state.video_task.cancel()

    app = FastAPI(lifespan=lifespan)

    async def _video_loop(app: FastAPI):
        while True:
            frame = await asyncio.to_thread(drone.get_frame)
            if frame is not None:
                det = detect_red_circle(frame, cfg)
                app.state.latest_detection = det
                small = cv2.resize(frame, (640, 480)) if frame.shape[1] != 640 else frame
                ok, jpeg = cv2.imencode(".jpg", draw_overlay(small, det),
                                        [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    await _broadcast_bytes(app, jpeg.tobytes())
            await asyncio.sleep(FRAME_INTERVAL)

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

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        app.state.clients.add(ws)
        loop = asyncio.get_running_loop()

        def emit(ev):
            loop.create_task(_broadcast_json(app, ev))

        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg["type"] == "run":
                    if app.state.interp is not None:
                        await _broadcast_json(app, {"type": "error", "message": "already running"})
                        continue
                    try:
                        program = Program.model_validate(msg["program"])
                    except ValidationError as e:
                        await _broadcast_json(app, {"type": "error",
                                                    "message": f"invalid program: {e}"})
                        continue
                    interp = Interpreter(drone, lambda: app.state.latest_detection, emit, cfg=cfg)
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
                elif msg["type"] == "estop":
                    if app.state.interp:
                        app.state.interp.request_stop()
                    await asyncio.to_thread(drone.emergency)
                    await _broadcast_json(app, {"type": "estopped"})
        except WebSocketDisconnect:
            pass
        finally:
            app.state.clients.discard(ws)

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app
