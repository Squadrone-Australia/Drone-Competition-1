import asyncio
from typing import Callable

from .drone.base import DroneAdapter
from .protocol import LIMITS, Block, Program
from .vision.config import VisionConfig, DEFAULT_CONFIG
from .vision.detector import Detection

MAX_EXPR_DEPTH = 32
MAX_LOOP_ITERS = 1000
# no target in view reads as "very far away", not 0: a student writing
# `repeat until (distance < 120)` must keep searching, not think it has arrived
NO_TARGET_DISTANCE_CM = 9999.0


class _Stopped(Exception):
    pass


class _MissionEnd(Exception):
    pass


class _LoopBreak(Exception):
    pass


class _LoopContinue(Exception):
    pass


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _truthy(v) -> bool:
    return v if isinstance(v, bool) else float(v) != 0


class Interpreter:
    def __init__(self, drone: DroneAdapter,
                 get_detection: Callable[[], Detection],
                 on_event: Callable[[dict], None],
                 cfg: VisionConfig = DEFAULT_CONFIG,
                 select_nearest_target: Callable[[], None] = lambda: None):
        self._drone = drone
        self._detect = get_detection
        self._emit = on_event
        self._cfg = cfg
        self._select_nearest_target = select_nearest_target
        # not cleared in run(): a stop requested before run() must still win
        self._stop = asyncio.Event()
        self.found_count = 0
        self.vars: dict[str, float | bool] = {}
        self._block_id = ""            # whose fault a warning is, for events

    def request_stop(self):
        self._stop.set()

    async def run(self, program: Program):
        self.vars.clear()
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

    # --- expressions ---

    def _warn(self, message: str, block_id: str | None = None):
        self._emit({"type": "warning", "blockId": block_id or self._block_id,
                    "message": message})

    def _sensor(self, s: str):
        det = self._detect()
        match s:
            case "target_visible":
                return det.found
            case "target_distance_cm":
                return det.distance_m * 100 if det.found else NO_TARGET_DISTANCE_CM
            case "target_bearing_deg":
                return det.bearing_deg if det.found else 0.0
            case "target_elevation_deg":
                return det.elevation_deg if det.found else 0.0
            case "target_count":
                return float(det.count) if det.found else 0.0
            case "target_position_left" | "target_position_center" | "target_position_right":
                return det.found and det.position == s.removeprefix("target_position_")
            case "found_count":
                return float(self.found_count)
            case "battery":
                return float(self._drone.battery())

    def _eval(self, node, depth=0):
        if depth > MAX_EXPR_DEPTH:
            raise ValueError(f"expression nested deeper than {MAX_EXPR_DEPTH} levels")
        match node.kind:
            case "number":
                return node.value
            case "sensor":
                return self._sensor(node.sensor)
            case "var":
                if node.name not in self.vars:
                    self._warn(f"variable '{node.name}' was never set — using 0")
                    return 0.0
                return self.vars[node.name]
            case "unop":
                v = self._eval(node.operand, depth + 1)
                if node.op == "not":
                    return not _truthy(v)
                return -float(v) if node.op == "neg" else abs(float(v))
            case "binop":
                return self._binop(node, depth)

    def _binop(self, node, depth):
        left = self._eval(node.left, depth + 1)
        if node.op == "and":                          # short-circuit, like the block reads
            return _truthy(left) and _truthy(self._eval(node.right, depth + 1))
        if node.op == "or":
            return _truthy(left) or _truthy(self._eval(node.right, depth + 1))
        a, b = float(left), float(self._eval(node.right, depth + 1))
        match node.op:
            case "+": return a + b
            case "-": return a - b
            case "*": return a * b
            case "/":
                if b == 0:
                    self._warn("division by zero — using 0")
                    return 0.0
                return a / b
            case "<": return a < b
            case ">": return a > b
            case "<=": return a <= b
            case ">=": return a >= b
            case "==": return a == b
            case "!=": return a != b

    def _cond(self, c) -> bool:
        return _truthy(self._eval(c))

    def _clamp_value(self, node, b: Block, field: str, cast=round):
        """Evaluate a value field and hold it to its range — warn, never raise.

        Ranges cannot be checked at parse time once a field can be computed, and a
        student whose arithmetic yields `move forward 3` deserves a nudge, not a
        mission that dies mid-flight.
        """
        lo, hi = LIMITS[field]
        v = cast(self._eval(node))
        clamped = cast(_clamp(v, lo, hi))
        if clamped != v:
            self._warn(f"{field} {v:g} is outside {lo}..{hi} — using {clamped:g}", b.id)
        return clamped

    async def _sleep(self, seconds: float):
        try:                                          # e-stop must cut a long wait short
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
        raise _Stopped()

    # --- execution ---

    async def _exec(self, b: Block):
        d = self._drone
        self._block_id = b.id
        match b.op:
            case "takeoff":
                await asyncio.to_thread(d.takeoff)
            case "land":
                await asyncio.to_thread(d.land)
            case "move":
                await asyncio.to_thread(d.move, b.dir, self._clamp_value(b.cm, b, "cm"))
            case "rotate":
                await asyncio.to_thread(d.rotate, b.dir, self._clamp_value(b.deg, b, "deg"))
            case "flip":
                await asyncio.to_thread(d.flip, b.dir)
            case "mark_found":
                await asyncio.to_thread(d.flip, "back")   # victory signal (requirements §2.1)
                self.found_count += 1
                self._emit({"type": "found_count", "count": self.found_count})
            case "end_mission":
                await asyncio.to_thread(d.land)
                raise _MissionEnd()
            case "set_var":
                self.vars[b.name] = self._eval(b.value)
            case "wait":
                await self._sleep(self._clamp_value(b.seconds, b, "seconds", cast=float))
            case "break":
                raise _LoopBreak()
            case "continue":
                raise _LoopContinue()
            case "repeat_n":
                for _ in range(self._clamp_value(b.n, b, "n")):
                    try:
                        await self._run_blocks(b.body)
                    except _LoopContinue:
                        continue
                    except _LoopBreak:
                        break
            case "repeat_until" | "while":
                # repeat_until stops when the condition goes true, while when it goes false
                stop_when = b.op == "repeat_until"
                for _ in range(MAX_LOOP_ITERS):           # hard safety bound
                    if self._stop.is_set():
                        raise _Stopped()
                    self._block_id = b.id                 # cond warnings belong to the loop
                    if self._cond(b.cond) == stop_when:
                        break
                    try:
                        await self._run_blocks(b.body)
                    except _LoopContinue:
                        continue
                    except _LoopBreak:
                        break
                else:
                    self._warn(f"loop gave up after {MAX_LOOP_ITERS} repeats", b.id)
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
        # Searching may have left the shared tracker locked to a marker that is
        # no longer the closest. Re-acquire once, then keep that lock while
        # moving so similarly sized markers cannot make the controller ping-pong.
        self._select_nearest_target()
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
