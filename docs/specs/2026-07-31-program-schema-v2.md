# Program Schema v2 — Expressions, Variables, and Runtime Ranges

**Status:** Implemented 2026-07-31 (Phase 2) · **Plan:** [2026-07-31-target-sensing-and-expressions.md](../plans/2026-07-31-target-sensing-and-expressions.md) · **Depends on:** [2026-07-31-target-sensing-design.md](2026-07-31-target-sensing-design.md)

## Purpose

Turn the block language from a fixed command list into something a student can *compute with*. v1 had
no way to say "turn by however far off the target is" or "keep a counter": the only condition was a
five-member `Condition` enum, and every distance was a number typed into a field.

v2 introduces a recursive **value node**, wires the Phase-1 metric sensors (`distance`, `bearing`,
`elevation`) into it, and adds `set_var`, `while`, and `wait`. `comp1/protocol.py` is the wire
contract; `comp1/frontend/blocks.js` and `Interpreter._eval` are its two implementations.

## Envelope

```json
{"version": 2, "blocks": [ Block, ... ]}
```

`version` accepts `1` or `2`. A v1 document is upgraded during validation and reports `version == 2`
afterwards.

## Value nodes

A discriminated union on `kind`, recursive through `left`/`right`/`operand`:

```json
{"kind": "number", "value": 30}
{"kind": "sensor", "sensor": "target_distance_cm"}
{"kind": "var",    "name": "steps"}
{"kind": "binop",  "op": "+",   "left": Value, "right": Value}
{"kind": "unop",   "op": "not", "operand": Value}
```

- `binop.op` ∈ `+ - * / < > <= >= == != and or`
- `unop.op` ∈ `not neg abs`

**A bare JSON number is accepted anywhere a `Value` is** and normalises to a `NumberLit`. This is
what keeps `{"op":"move","cm":50}` — every saved program and every test fixture — valid, and keeps
hand-written JSON readable. It is a `BeforeValidator` wrapped *outside* the tagged union, so it
applies at every depth, including inside a `binop`.

`and`/`or` short-circuit. Comparisons return booleans; arithmetic coerces through `float`, so
`target_visible + 1` is 2 rather than an error — the language has no type errors by design.

### Sensors

| sensor | type | when no target visible |
|---|---|---|
| `target_visible` | bool | `false` |
| `target_distance_cm` | number | **9999** |
| `target_bearing_deg` | number | `0` |
| `target_elevation_deg` | number | `0` |
| `target_count` | number | `0` |
| `target_position_left` | bool | `false` |
| `target_position_center` | bool | `false` |
| `target_position_right` | bool | `false` |
| `found_count` | number | n/a |
| `battery` | number | n/a |

All read from the same polled `Detection` the rest of the system uses
([CLAUDE.md](../../CLAUDE.md) — "detection and control are decoupled by polling"), so a sensor
inside an expression sees exactly what the video overlay and the telemetry panel see.

#### Why 9999 and not 0

The naive search a student writes first is

```
repeat until (target_distance_cm < 120):  turn right 15
```

With `0` for "nothing in view", that loop exits on its very first test and the drone behaves as
though it had already arrived — the failure is silent and looks like success. A large sentinel makes
the naive program correct: "nothing in view" reads as "very far away", the loop keeps turning, and
the value falls into range only when a real target appears.

The honest alternative is a `null`/NaN that poisons every comparison, which is defensible in a
language with error handling and wrong in one aimed at 13-year-olds. The cost is that `9999` can
leak into arithmetic (`distance / 2` is a large number, not an error), so the block tooltip must say
"9999 when nothing is seen".

## Blocks

`Block.op` ∈ `takeoff land move rotate flip approach_marker mark_found end_mission repeat_n
repeat_until while if set_var wait`

Fields taking a `Value` (or a bare number):

| op | field | range |
|---|---|---|
| `move` | `cm` | 20..500 |
| `rotate` | `deg` | 1..360 |
| `repeat_n` | `n` | 0..50 (0 = skip body) |
| `wait` | `seconds` | 0..10 |

New ops:

```json
{"id":"x", "op":"set_var", "name":"steps", "value": Value}
{"id":"x", "op":"wait", "seconds": Value}
{"id":"x", "op":"while", "cond": Value, "body":[Block,...]}
```

`if`, `repeat_until`, and `while` take `cond` as a **`Value`**, replacing the v1 `Condition` object.
Truthiness: booleans are themselves, numbers are true when `!= 0`.

`repeat_until` loops until the condition is true; `while` loops while it is true. Both keep the hard
1000-iteration bound and now emit a warning when they hit it, rather than ending indistinguishably
from a normal exit.

## Range checking: parse time *and* run time

The v1 constraint `cm: int = Field(ge=20, le=500)` cannot survive a computed field — the value does
not exist until the mission is flying. The rule is therefore split, and `protocol.LIMITS` is the
single table both halves read:

- **A literal is still rejected at parse time.** `{"op":"move","cm":5}` is a mistake the student can
  see and fix before takeoff, and rejecting it keeps the editor's feedback immediate.
  (`test_move_requires_valid_distance` is unchanged from v1.)
- **A computed value is clamped at run time**, with
  `{"type":"warning","blockId":<id>,"message":...}` broadcast to the workspace.

Clamping does **not** raise. A student whose arithmetic yields `move forward 3` should get a visible
nudge and a mission that continues, not a drone that aborts mid-flight because of an off-by-one.
The same reasoning covers **division by zero**, which yields `0` and warns, and **reading an unset
variable**, which yields `0` and warns.

The hard error is reserved for the one case that cannot be given a sensible value: **expression
depth beyond 32 nodes**, which is a malformed or malicious document rather than a student mistake,
and ends the run as a normal mission error (the drone lands).

## Variables

One flat `dict` on the `Interpreter`, cleared at the start of every `run()`. No scoping, no
declarations, no types — a name holds a number or a boolean. Loop bodies share the enclosing frame,
which is what makes the counter idiom (`set i = 0` / `while i < 3` / `set i = i + 1`) work as it
reads.

## v1 compatibility

The v1 `Condition` maps onto value nodes during a `model_validator(mode="before")` walk of the block
tree. A `cond` dict with no `"kind"` key is unambiguously a v1 condition, so the upgrade is applied
by shape rather than by version number and a stray v1 condition in a v2 document also loads.

| v1 condition | v2 value |
|---|---|
| `{"sensor":"marker_visible"}` | `{"kind":"sensor","sensor":"target_visible"}` |
| `{"sensor":"marker_position_left"}` | `{"kind":"sensor","sensor":"target_position_left"}` |
| `{"sensor":"marker_position_center"}` | `{"kind":"sensor","sensor":"target_position_center"}` |
| `{"sensor":"marker_position_right"}` | `{"kind":"sensor","sensor":"target_position_right"}` |
| `{"sensor":"found_count_gte","value":N}` | `{"kind":"binop","op":">=","left":{"kind":"sensor","sensor":"found_count"},"right":{"kind":"number","value":N}}` |

The `marker_` → `target_` rename is confined to this table; the interpreter speaks only v2.
`Condition` survives in `protocol.py` for exactly this purpose, so a malformed v1 program still gets
a precise validation error instead of an opaque union mismatch.

## Interpreter

| Piece | Role |
|---|---|
| `_eval(node, depth=0)` | recursive evaluation, returns `float` or `bool` |
| `_cond(c)` | `_truthy(_eval(c))` — the only entry point loops and `if` use |
| `_sensor(name)` | the table above, off one `self._detect()` call |
| `_clamp_value(node, b, field)` | evaluate → clamp to `LIMITS[field]` → warn on change |
| `_warn(msg, block_id)` | `{"type":"warning","blockId":...,"message":...}` |
| `_sleep(seconds)` | `wait`, cut short by the e-stop rather than blocking on it |

`_block_id` tracks whose warning it is. Loops re-assert it before each condition test, so a warning
raised while re-evaluating `while (i / 0)` is attributed to the loop and not to the last block of
its body.

`wait` is implemented as `asyncio.wait_for(self._stop.wait(), timeout=seconds)`: a plain
`asyncio.sleep(10)` would make the EMERGENCY STOP button unresponsive for up to ten seconds.

## Testing

`tests/test_expressions.py` (50 tests) covers every node kind, every binary operator, nested
arithmetic, all ten sensors in both the target-visible and no-target states, variables including the
unset read, truthiness of numbers and booleans, the depth cap either side of the limit,
division by zero, runtime clamping of `move`/`rotate`/`repeat_n`/`wait` in both directions with the
warning event asserted, `while` vs `repeat_until` semantics, the 1000-iteration bound, and the full
v1 upgrade table.

`test_v1_program_runs_unchanged_under_the_v2_parser` (in `tests/test_interpreter.py`) is the
regression that matters: a v1 document asserted event-for-event, drone-call-for-drone-call, with
zero warnings.

## Out of scope

`height_cm`, `distance_to_home_cm`, and `bearing_to_home_deg` are named in the plan but not in the
v2 sensor vocabulary — they need pose estimation (Phase 4). Nothing here persists a variable across
runs, and there is still no notion of a function or a user-defined block.
