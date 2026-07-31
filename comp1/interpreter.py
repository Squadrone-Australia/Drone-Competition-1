import asyncio
from typing import Callable

from .drone.base import DroneAdapter
from .protocol import Block, Program
from .vision.config import VisionConfig, DEFAULT_CONFIG
from .vision.detector import Detection


class _Stopped(Exception):
    pass


class _MissionEnd(Exception):
    pass


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Interpreter:
    def __init__(self, drone: DroneAdapter,
                 get_detection: Callable[[], Detection],
                 on_event: Callable[[dict], None],
                 cfg: VisionConfig = DEFAULT_CONFIG):
        self._drone = drone
        self._detect = get_detection
        self._emit = on_event
        self._cfg = cfg
        # not cleared in run(): a stop requested before run() must still win
        self._stop = asyncio.Event()
        self.found_count = 0

    def request_stop(self):
        self._stop.set()

    async def run(self, program: Program):
        reason, detail = "done", ""
        try:
            await self._run_blocks(program.blocks)
        except _Stopped:
            reason = "stopped"
        except _MissionEnd:
            pass
        except Exception as exc:
            reason, detail = "error", str(exc)
        if reason != "done":
            try:
                await asyncio.to_thread(self._drone.land)
            except Exception:
                pass
        self._emit({"type": "finished", "reason": reason, "detail": detail})

    async def _run_blocks(self, blocks: list[Block]):
        for b in blocks:
            if self._stop.is_set():
                raise _Stopped()
            self._emit({"type": "highlight", "blockId": b.id})
            await self._exec(b)

    def _cond(self, c) -> bool:
        if c.sensor == "marker_visible":
            return self._detect().found
        if c.sensor.startswith("marker_position_"):
            det = self._detect()
            return det.found and det.position == c.sensor.removeprefix("marker_position_")
        return self.found_count >= c.value            # found_count_gte

    async def _exec(self, b: Block):
        d = self._drone
        match b.op:
            case "takeoff":
                await asyncio.to_thread(d.takeoff)
            case "land":
                await asyncio.to_thread(d.land)
            case "move":
                await asyncio.to_thread(d.move, b.dir, b.cm)
            case "rotate":
                await asyncio.to_thread(d.rotate, b.dir, b.deg)
            case "flip":
                await asyncio.to_thread(d.flip, b.dir)
            case "mark_found":
                await asyncio.to_thread(d.flip, "back")   # victory signal (requirements §2.1)
                self.found_count += 1
                self._emit({"type": "found_count", "count": self.found_count})
            case "end_mission":
                await asyncio.to_thread(d.land)
                raise _MissionEnd()
            case "repeat_n":
                for _ in range(b.n):
                    await self._run_blocks(b.body)
            case "repeat_until":
                for _ in range(1000):                     # hard safety bound
                    if self._stop.is_set():
                        raise _Stopped()
                    if self._cond(b.cond):
                        break
                    await self._run_blocks(b.body)
            case "if":
                await self._run_blocks(b.body if self._cond(b.cond) else b.else_body)
            case "approach_marker":
                await self._approach()

    async def _approach(self):
        """Turn toward the target, then close on it, using metric bearing and range.

        Proportional rather than fixed-step: the correction is sized from the
        measured error, then clamped to what the Tello will actually honour —
        it ignores rotations below ~10° and refuses translations below 20 cm.
        """
        cfg = self._cfg
        lost = 0
        for _ in range(cfg.approach_max_steps):
            if self._stop.is_set():
                raise _Stopped()
            det = self._detect()
            if not det.found:
                lost += 1
                if lost >= cfg.approach_lost_limit:
                    return
                await asyncio.sleep(0.3)
                continue
            lost = 0
            bearing, distance = det.bearing_deg, det.distance_m
            if abs(bearing) > cfg.approach_bearing_deadband_deg:
                deg = _clamp(round(abs(bearing)),
                             cfg.approach_min_turn_deg, cfg.approach_max_turn_deg)
                await asyncio.to_thread(self._drone.rotate,
                                        "cw" if bearing > 0 else "ccw", deg)
            elif distance > cfg.approach_stop_distance_m:
                remaining_cm = (distance - cfg.approach_stop_distance_m) * 100
                if remaining_cm < cfg.approach_min_step_cm:
                    return                                # closer than one step — done
                cm = _clamp(round(remaining_cm),
                            cfg.approach_min_step_cm, cfg.approach_max_step_cm)
                await asyncio.to_thread(self._drone.move, "forward", cm)
            else:
                return                                    # close enough (requirements §3.2)
            await asyncio.sleep(0.2)                      # let video catch up
