import asyncio
import math
from collections.abc import Callable

from .drone.base import DroneAdapter
from .drone.config import (
    SIGNAL_FLIP_DIR,
    SIGNAL_SPIN_DEG,
    choose_signal,
)
from .protocol import LIMITS, Block, Program
from .vision.config import DEFAULT_CONFIG, VisionConfig
from .vision.detector import Detection

MAX_EXPR_DEPTH = 32
MAX_LOOP_ITERS = 1000
# nothing in view reads as "very far away", not 0: a student writing
# `repeat until (distance < 120)` must keep searching, not think it has arrived.
NO_TARGET_DISTANCE_CM = 9999.0
#: Shown when a plan runs to its end with the drone still up and is landed for
#: the student. The second half is the fix, in the block's own words.
AUTO_LAND_WARNING = (
    "the plan ended with the drone still in the air, so it was landed for you"
    " — finish with a “land” block to choose where it comes down"
)


class _Stopped(Exception):
    pass


class _MissionEnd(Exception):
    pass


class DroneTimeout(Exception):
    """An aircraft command that never came back inside its budget."""


class _LoopBreak(Exception):
    pass


class _LoopContinue(Exception):
    pass


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _truthy(v) -> bool:
    return v if isinstance(v, bool) else float(v) != 0


def _tidy(v: float) -> float:
    """Round off float noise from trig, so sin 30 is 0.5 and not 0.49999999999999994.

    A student testing `sin 30 = 0.5` or `cos 90 = 0` must get true; ten decimal places
    is far finer than anything a drone can fly and far coarser than the noise.
    """
    return round(v, 10) + 0.0  # + 0.0 turns -0.0 into 0.0


class Interpreter:
    def __init__(
        self,
        drone: DroneAdapter,
        get_detection: Callable[[], Detection],
        on_event: Callable[[dict], None],
        cfg: VisionConfig = DEFAULT_CONFIG,
        select_nearest_target: Callable[[], None] = lambda: None,
    ):
        self._drone = drone
        self._detect = get_detection
        self._emit = on_event
        self._cfg = cfg
        self._select_nearest_target = select_nearest_target
        # not cleared in run(): a stop requested before run() must still win
        self._stop = asyncio.Event()
        self.found_count = 0
        self.vars: dict[str, float | bool] = {}
        self._block_id = ""  # whose fault a warning is, for events
        self._block_op = ""
        # Set when the stop came from the EMERGENCY STOP button: the aircraft is
        # being cut, so this interpreter must not also try to land it.
        self._emergency = False

    def request_stop(self, emergency: bool = False):
        """Ask the mission to stop at the next block boundary.

        ``emergency`` suppresses the automatic ``land`` in :meth:`run`. An
        emergency stop cuts the motors itself, and a ``land`` racing that from
        this side is at best redundant and at worst a flight command sent to an
        aircraft that is already falling.
        """
        if emergency:
            self._emergency = True
        self._stop.set()

    async def run(self, program: Program):
        self.vars.clear()
        reason, detail = "done", ""
        try:
            await self._run_blocks(program.blocks)
            # The flag is otherwise read only *before* a block, so a Stop that
            # arrives during the last one -- often the whole program is a
            # single `take off` -- would find nothing left to check it and be
            # reported, and treated, as a clean finish.
            if self._stop.is_set():
                raise _Stopped()
        except _Stopped:
            reason = "stopped"
        except _MissionEnd:
            pass
        except Exception as exc:
            reason, detail = "error", str(exc)
        # A program that ends with the drone still up hands a hovering aircraft
        # to nobody: Stop has no mission left to stop, and the next Run's
        # `take off` meets a drone already in the air. So "done" lands too --
        # but only when airborne, since most programs end with their own land.
        # Stopped and errored runs land unconditionally, as they always have:
        # there the belief is least trustworthy and a spare land is harmless.
        must_land = reason != "done" or self._drone.flying
        if reason == "done" and must_land and not self._emergency:
            # Said out loud, because a drone that comes down with no `land`
            # block in the plan looks like the program doing something on its
            # own -- and the lesson is that a plan should finish on the ground.
            self._warn(AUTO_LAND_WARNING)
        if must_land and not self._emergency:
            try:
                await self._call_drone("land", block_op="automatic_land")
            except Exception:
                pass
        self._emit({"type": "finished", "reason": reason, "detail": detail})

    async def _run_blocks(self, blocks: list[Block]):
        for b in blocks:
            # Hand the event loop a turn before every block. Blocks that command
            # the aircraft await a thread and yield anyway, but a loop whose body
            # is pure arithmetic never suspends — and then video, telemetry, and
            # the websocket message carrying Stop or EMERGENCY STOP all sit in a
            # queue that cannot be drained until the program ends. `sleep(0)` is
            # the cheapest possible yield and costs nothing a student can see.
            await asyncio.sleep(0)
            if self._stop.is_set():
                raise _Stopped()
            self._block_id, self._block_op = b.id, b.op
            self._emit({"type": "highlight", "blockId": b.id})
            self._emit(
                {"type": "execution", "kind": "block", "blockId": b.id, "op": b.op}
            )
            await self._exec(b)

    # --- expressions ---

    def _warn(self, message: str, block_id: str | None = None):
        self._emit(
            {
                "type": "warning",
                "blockId": block_id or self._block_id,
                "message": message,
            }
        )

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
            case (
                "target_position_left"
                | "target_position_center"
                | "target_position_right"
            ):
                return det.found and det.position == s.removeprefix("target_position_")
            case "found_count":
                return float(self.found_count)
            case "battery":
                result = float(self._drone.battery())
                self._emit(
                    {
                        "type": "execution",
                        "kind": "call",
                        "blockId": self._block_id,
                        "op": self._block_op,
                        "adapter": type(self._drone).__name__,
                        "method": "battery",
                        "args": [],
                        "result": result,
                    }
                )
                return result

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
                return self._unary(node.op, float(v))
            case "binop":
                return self._binop(node, depth)

    def _unary(self, op: str, x: float) -> float:
        """One-number maths. Total by design: no input may raise mid-flight.

        Whatever has no answer becomes 0 with a warning, the same rule division by
        zero already follows; an input just outside a domain (asin of 1.0000001, from
        float noise) is clamped onto it instead.
        """
        match op:
            case "neg":
                return -x
            case "abs":
                return abs(x)
        if not math.isfinite(x):
            # only reachable through arithmetic that overflowed already; math.floor(inf)
            # and math.sin(inf) both raise, and a warning beats a crash
            self._warn(f"{op} of {x:g} has no answer — using 0")
            return 0.0
        match op:
            case "sqrt":
                if x < 0:
                    self._warn(f"square root of a negative number ({x:g}) — using 0")
                    return 0.0
                return math.sqrt(x)
            case "round":
                # halves go up, as taught in school and as JS Math.round does;
                # Python's round(2.5) == 2 would read as a bug to a student
                return float(math.floor(x + 0.5))
            case "floor":
                return float(math.floor(x))
            case "ceil":
                return float(math.ceil(x))
            case "tan" if (x - 90) % 180 == 0:
                self._warn(f"tan of {x:g}° has no answer — using 0")
                return 0.0
            case "sin" | "cos" | "tan":
                fn = {"sin": math.sin, "cos": math.cos, "tan": math.tan}[op]
                return _tidy(fn(math.radians(x)))
            case "asin" | "acos":
                if not -1 <= x <= 1:
                    clamped = _clamp(x, -1.0, 1.0)
                    self._warn(f"{op} needs a number from -1 to 1 — using {clamped:g}")
                    x = clamped
                fn = math.asin if op == "asin" else math.acos
                return _tidy(math.degrees(fn(x)))
            case "atan":
                return _tidy(math.degrees(math.atan(x)))
        return 0.0  # unreachable: protocol.UN_OPS is closed

    def _binop(self, node, depth):
        left = self._eval(node.left, depth + 1)
        if node.op == "and":  # short-circuit, like the block reads
            return _truthy(left) and _truthy(self._eval(node.right, depth + 1))
        if node.op == "or":
            return _truthy(left) or _truthy(self._eval(node.right, depth + 1))
        a, b = float(left), float(self._eval(node.right, depth + 1))
        match node.op:
            case "+":
                return a + b
            case "-":
                return a - b
            case "*":
                return a * b
            case "/":
                if b == 0:
                    self._warn("division by zero — using 0")
                    return 0.0
                return a / b
            case "%":
                if b == 0:
                    self._warn("remainder after dividing by zero — using 0")
                    return 0.0
                return a % b
            case "**":
                try:
                    # math.pow raises where ** would quietly go complex ((-8) ** (1/3))
                    return math.pow(a, b)
                except OverflowError:
                    self._warn(f"{a:g} to the power {b:g} is too big — using 0")
                except ValueError:
                    self._warn(f"{a:g} to the power {b:g} has no answer — using 0")
                return 0.0
            case "min":
                return min(a, b)
            case "max":
                return max(a, b)
            case "<":
                return a < b
            case ">":
                return a > b
            case "<=":
                return a <= b
            case ">=":
                return a >= b
            case "==":
                return a == b
            case "!=":
                return a != b

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
        try:  # e-stop must cut a long wait short
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return
        raise _Stopped()

    # --- execution ---

    async def _call_drone(self, method: str, *args, block_op: str | None = None):
        """Trace and invoke the exact adapter method behind a block action."""
        self._emit(
            {
                "type": "execution",
                "kind": "call",
                "blockId": self._block_id,
                "op": block_op or self._block_op,
                "adapter": type(self._drone).__name__,
                "method": method,
                "args": list(args),
            }
        )
        # Bounded on purpose. `to_thread` cannot be cancelled, so a command the
        # aircraft never answers would otherwise park this coroutine for good:
        # the mission would never finish, `app.state.interp` would never clear,
        # and because the stop flag is only read between blocks, neither Stop
        # nor EMERGENCY STOP could end it. Giving up abandons the worker thread
        # — which is the lesser evil against a mission that cannot be stopped.
        timeout = getattr(self._drone, "command_timeout_s", None)
        call = asyncio.to_thread(getattr(self._drone, method), *args)
        try:
            return await asyncio.wait_for(call, timeout=timeout)
        except TimeoutError:
            raise DroneTimeout(
                f"the drone did not answer '{method}' within {timeout:g}s"
            ) from None

    async def _exec(self, b: Block):
        self._block_id, self._block_op = b.id, b.op
        match b.op:
            case "takeoff":
                # Warn, don't raise: the drone is already where this block
                # would put it. Sent anyway, a real Tello refuses it and the
                # whole mission dies on its first block.
                if self._drone.flying:
                    self._warn("the drone is already flying — skipping take off")
                else:
                    await self._call_drone("takeoff")
            case "land":
                await self._call_drone("land")
            case "move":
                await self._call_drone("move", b.dir, self._clamp_value(b.cm, b, "cm"))
            case "rotate":
                await self._call_drone(
                    "rotate", b.dir, self._clamp_value(b.deg, b, "deg")
                )
            case "flip":
                await self._call_drone("flip", b.dir)
            case "mark_found":
                await self._signal_found(b.signal or "flip")
                self.found_count += 1
                self._emit({"type": "found_count", "count": self.found_count})
            case "end_mission":
                await self._call_drone("land")
                raise _MissionEnd()
            case "set_var":
                self.vars[b.name] = self._eval(b.value)
            case "wait":
                await self._sleep(
                    self._clamp_value(b.seconds, b, "seconds", cast=float)
                )
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
                for _ in range(MAX_LOOP_ITERS):  # hard safety bound
                    # also here: a loop with an empty body never reaches the
                    # yield in _run_blocks, and spinning 1000 times on the
                    # condition alone would starve the loop just as effectively
                    await asyncio.sleep(0)
                    if self._stop.is_set():
                        raise _Stopped()
                    self._block_id, self._block_op = b.id, b.op
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

    async def _signal_found(self, kind: str):
        """Perform the find signal required by §2.1, downgrading a doomed flip.

        A controller built out of ``flip`` and ``rotate``, like :meth:`_approach`,
        so no adapter gains a primitive for it. The battery is only read when a
        flip was actually asked for — on a real Tello that is an SDK round trip,
        and the spin never needs it. A reading that fails counts as unknown,
        which :func:`choose_signal` treats as too risky to flip on.
        """
        battery = None
        if kind == "flip":
            try:
                battery = int(await self._call_drone("battery"))
            except Exception:
                battery = None  # unknown, not fatal — choose_signal spins
        flight = getattr(self._drone, "flight", None)
        kind, warning = choose_signal(kind, battery, flight)
        if warning:
            self._warn(warning)
        if kind == "flip":
            await self._call_drone("flip", SIGNAL_FLIP_DIR)
        else:
            await self._call_drone("rotate", "cw", SIGNAL_SPIN_DEG)

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
        blind_until = None
        for _ in range(cfg.approach_max_steps):
            if self._stop.is_set():
                raise _Stopped()
            det = self._detect()
            if not det.found:
                # A cluttered arena drops frames in bursts — glare, motion blur
                # after a rotate, something passing in front. Hover and keep
                # looking for the whole budget rather than treating the first
                # few empty frames as a vanished target.
                now = asyncio.get_running_loop().time()
                if blind_until is None:
                    blind_until = now + cfg.approach_lost_timeout_s
                elif now >= blind_until:
                    self._warn("lost sight of the marker — approach stopped")
                    return
                await asyncio.sleep(0.3)
                continue
            blind_until = None
            bearing, distance = det.bearing_deg, det.distance_m
            if abs(bearing) > cfg.approach_bearing_deadband_deg:
                deg = _clamp(
                    round(abs(bearing)),
                    cfg.approach_min_turn_deg,
                    cfg.approach_max_turn_deg,
                )
                await self._call_drone("rotate", "cw" if bearing > 0 else "ccw", deg)
            elif distance > cfg.approach_stop_distance_m:
                remaining_cm = (distance - cfg.approach_stop_distance_m) * 100
                if remaining_cm < cfg.approach_min_step_cm:
                    return  # closer than one step — done
                cm = _clamp(
                    round(remaining_cm),
                    cfg.approach_min_step_cm,
                    cfg.approach_max_step_cm,
                )
                await self._call_drone("move", "forward", cm)
            else:
                return  # close enough (requirements §3.2)
            await asyncio.sleep(0.2)  # let video catch up
