# Drone operations reference

This is the complete contestant-facing Python reference for Squadrone. It
covers both ways of controlling the selected simulator or Tello:

- **From your IDE (recommended):** install `client/`, start Squadrone, then
  import `Drone` from `squadrone`.
- **Bundled script:** run a file with `python -m comp2 --script your_file.py`
  and import `Drone` from `comp2.api`.

Both APIs send commands to the drone selected in the Squadrone cockpit. They
have the same flight operations and use centimetres, degrees, seconds, and
stick percentages as described below.

## Set up and connect

Install the IDE client into the same Python environment that runs your code:

```powershell
python -m pip install .\client
```

Start Squadrone, leave its browser cockpit open, and run:

```python
from squadrone import Drone

with Drone() as drone:
    drone.takeoff()
    drone.forward(100)
    drone.land()
```

`Drone()` connects to `ws://127.0.0.1:8765/ws` by default. To use a different
server, pass its WebSocket URL or set `SQUADRONE_URL`:

```python
drone = Drone("ws://192.168.1.20:8765/ws", timeout=30)
```

The `timeout` argument is the maximum number of seconds the client waits for a
connection or command response. Calls are synchronous: the next Python line
runs only after the operation finishes. Only one IDE client can control the
drone at a time.

The context manager is recommended because leaving the `with` block closes
the connection. A disconnect or close centres the RC sticks before Squadrone
lands the drone. Without a context manager, call `drone.close()` when done;
`close()` takes no arguments and returns `None`.

For a bundled script, use:

```python
from comp2.api import Drone

drone = Drone()
```

When launched with `--script`, `Drone()` automatically uses the simulator or
real Tello selected by the Squadrone process. `Drone(sim=True)` creates a
standalone simulator for testing, but has no cockpit or Emergency Stop button.
Its optional `track="track_id"` argument selects a published track.

## Coordinates and units

All movement is relative to the drone's body and its current heading. The
contestant API intentionally provides no live arena-absolute position.

| Name | Positive direction | Negative direction | Unit |
| --- | --- | --- | --- |
| `cm` | Named command direction | Not accepted | centimetres |
| `deg` | Named turn direction | Not accepted | degrees |
| `lr` | right | left | stick percentage |
| `fb` | forward | back | stick percentage |
| `ud` | up | down | stick percentage |
| `yaw` | clockwise/right | anticlockwise/left | stick percentage |
| `x_cm` | forward | back | centimetres |
| `y_cm` | left | right | centimetres |
| `z_cm` | up | down | centimetres |

Notice that positive `lr` means **right**, while positive `y_cm` in `go()` and
`curve()` means **left**. These follow the Tello command conventions.

Numeric command arguments are rounded to the nearest integer. Out-of-range
values are clamped and produce a warning instead of ending a flight.

## Takeoff and landing

### `takeoff()`

Takes off and settles into a hover. Call it before any movement operation.
The race clock starts at takeoff.

```python
drone.takeoff()
```

### `land()`

Centres the RC sticks, descends, and stops the motors. In a scored run, landing
finishes the race only after all gates have been passed and the drone is over
the finish pad; otherwise the run is a DNF.

```python
drone.land()
```

`takeoff()` and `land()` take no arguments and return `None`.

## Discrete movement

Discrete moves are easy to tune but stop between commands, which generally
produces a less smooth racing line.

| Operation | Meaning | Arguments |
| --- | --- | --- |
| `forward(cm)` | Move straight forward | `cm`: 20–500 |
| `back(cm)` | Move straight backward | `cm`: 20–500 |
| `left(cm)` | Strafe left without changing heading | `cm`: 20–500 |
| `right(cm)` | Strafe right without changing heading | `cm`: 20–500 |
| `up(cm)` | Climb | `cm`: 20–500 |
| `down(cm)` | Descend | `cm`: 20–500 |
| `turn_right(deg)` | Rotate clockwise in place | `deg`: 1–360 |
| `turn_left(deg)` | Rotate anticlockwise in place | `deg`: 1–360 |

Examples:

```python
drone.forward(150)  # 1.5 m forward
drone.left(40)  # 0.4 m left, with no turn
drone.up(75)  # climb 0.75 m
drone.turn_right(90)  # face 90 degrees clockwise from the old heading
```

Each call blocks until the move finishes and returns `None`. A distance below
20 cm becomes 20 cm rather than a tiny move; a distance above 500 cm becomes
500 cm. Turns are similarly clamped to 1–360 degrees.

### `flip(direction="back")`

Performs a flip. `direction` must be one of `"forward"`, `"back"`, `"left"`,
or `"right"`; the default is `"back"`.

```python
drone.flip()  # back flip
drone.flip("right")
```

The real Tello may reject a flip when its battery is low (typically below
about 50%), there is too little clearance, or its own safety checks fail. A
flip is not necessary to complete a race.

## Racing and continuous movement

### `fly(lr=0, fb=0, ud=0, yaw=0, *, seconds=...)`

Applies all four virtual sticks together. This is the main operation for a
continuous racing line.

- `lr`: lateral input, from -100 (left) to +100 (right).
- `fb`: longitudinal input, from -100 (back) to +100 (forward).
- `ud`: vertical input, from -100 (down) to +100 (up).
- `yaw`: rotation input, from -100 (anticlockwise) to +100 (clockwise).
- `seconds`: how long to hold the inputs before automatically hovering. The
  IDE client accepts 0–10 seconds per call.

```python
drone.fly(fb=70, seconds=1.0)  # forward
drone.fly(fb=65, ud=15, yaw=-20, seconds=0.3)  # climb and arc left
drone.fly(lr=-25, seconds=0.2)  # slide left
```

Stick values are percentages, not guaranteed physical speeds. In the
simulator, 100 corresponds approximately to 1 m/s on translation channels and
100 degrees/s on yaw. Real-flight speed varies with the Tello, battery, and
conditions.

The IDE client requires `seconds`; omitting it raises `ValueError` before a
command is sent, and Squadrone rejects a value outside 0–10 seconds. This
prevents a debugger pause from leaving a non-zero stick command active. In a
bundled `comp2.api` script only, `seconds` may be omitted, in which case the
setpoint remains active until the next `fly()` or `hover()`. Prefer bounded
calls in all code.

### `hover()`

Centres all four sticks (`lr=fb=ud=yaw=0`) so the drone stops translating or
turning and holds position.

```python
drone.hover()
```

Use it before landing after a continuous sequence, or whenever your controller
has no confident movement command.

### `go(x_cm=0, y_cm=0, z_cm=0, speed=50)`

Flies one straight diagonal line to a body-relative offset, without executing
the axes as separate stop-and-start moves.

- `x_cm`: forward (+) or back (-), from -500 to 500 cm.
- `y_cm`: left (+) or right (-), from -500 to 500 cm.
- `z_cm`: up (+) or down (-), from -500 to 500 cm.
- `speed`: travel speed from 10 to 100 cm/s; default 50.

Every axis must be either zero or have a magnitude of at least 20 cm. A
non-zero value between -20 and 20 is changed to zero.

```python
drone.go(x_cm=100, z_cm=50, speed=60)  # 1 m forward and 0.5 m up
drone.go(80, -40, 0, speed=35)  # forward and right
```

The offset is measured from the position and heading at the start of the call.
The drone's heading does not change.

### `curve(x1, y1, z1, x2, y2, z2, speed=50)`

Flies a smooth curve through body-relative point 1 and finishes at
body-relative point 2. It is useful for carrying speed through an off-axis
gate.

- `x1`, `y1`, `z1`: coordinates of the intermediate control point in cm.
- `x2`, `y2`, `z2`: coordinates of the ending point in cm.
- `speed`: travel speed from 10 to 100 cm/s; default 50.

The axes use the same signs and normalization as `go()`: x forward, y left,
z up; each value is limited to ±500 cm and values with magnitude below 20 cm
become zero.

```python
drone.curve(
    80,
    40,
    0,  # bend through a point forward and left
    160,
    0,
    20,  # finish forward and slightly higher
    speed=60,
)
```

The simulator models this as a quadratic curve. Real Tello firmware can reject
geometrically impossible curves, so test the exact points at low speed and
with generous clearance before a full run. `curve()` is Python-only and has no
block-program equivalent.

### `wait(seconds)`

Waits while the drone holds its current state. The argument is a duration in
seconds; negative values act like zero.

```python
drone.wait(0.5)
```

The wait is interruptible by Stop or Emergency Stop. In the IDE client it is a
remote call, so the chosen duration should be shorter than the client's command
`timeout`.

## Camera and sensors

### `frame(timeout=None, *, fresh=False)`

Returns the latest raw forward-camera image as a NumPy array in **BGR** channel
order, ready for OpenCV, or `None` if no frame is available.

```python
import cv2

frame = drone.frame(timeout=2.0)
if frame is not None:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height_px, width_px = frame.shape[:2]
```

For the IDE client, `timeout` is how many seconds to wait for the first frame;
when omitted it uses the connection's default timeout. Later calls normally
return a copy of the most recently received frame immediately. The bundled
API has `frame()` with no arguments and returns the current frame immediately.

For external visual control loops, use `drone.frame(fresh=True, timeout=0.8)`.
This waits for a JPEG received after the call begins and returns `None` if it
times out or the connection closes. Reinstall `client/` to use this option.

The image contains no gate detections or sensor overlays. Squadrone may draw a
minimap on its own display copy, but that drawing is never included in the
frame returned to contestant code.

### `show_detections(detections, *, size=None)`

Draws your own detections on the video in the Squadrone window, so the operator
can see what your program thinks it is looking at. This is display only: the
outlines go on the cockpit's copy of the stream, never on the frames `frame()`
returns, so they can never feed back into your detector. Nothing here detects
anything for you.

Each detection is a sequence of `(x, y)` points in the pixel coordinates of the
frame you analysed — a box is four corners — or a `(points, label)` pair to
caption it:

```python
frame = drone.frame(fresh=True, timeout=0.8)
found = my_detector(frame)  # your own OpenCV
if found is None:
    drone.show_detections([])  # nothing in view: clear the last outline
else:
    x, y, w, h = found.box
    box = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    drone.show_detections([(box, f"gate {found.distance_m:.2f} m")])
```

Pass `()` or `[]` when you see nothing; an outline you stop updating clears
itself after about a second. `size` is `(height, width)` of the frame those
pixels came from, and defaults to the current camera frame's size — pass it
explicitly if you detected on a resized image. Up to 12 outlines are drawn, of
at most 16 points each; extra ones are dropped rather than failing the call,
but a malformed point raises. The drawing lands on whichever frame the cockpit
shows next, so at speed the box trails the gate slightly: it is a view of what
your program believes, not a measurement. Reinstall `client/` to use this
method from an IDE program.

### `battery`

A read-only property containing battery charge as an integer percentage from
0 to 100. It is a property, not a function.

```python
if drone.battery < 30:
    drone.land()
```

Use `drone.battery`, not `drone.battery()`.

### `height`

A read-only property giving a height estimate in centimetres as a float.

```python
print(f"Height: {drone.height:.1f} cm")
```

The simulator reports its modelled height above the floor. The real-Tello
adapter reports the SDK's `h` telemetry; it does not substitute downward ToF
range or mission-pad coordinates. Each hardware read waits up to 0.75 seconds
for a new SDK state packet. Missing, invalid or stalled telemetry raises an
error rather than returning a misleading zero or a cached height. This is a
height estimate, not a guarantee of centimetre accuracy or an arena position.
After takeoff, wait for valid, settled readings instead of assuming a fixed
takeoff height. The circuit solutions do this before their first climb, then
correct their estimate against the known gate height using the camera.

There is deliberately no `pose()`, gate location sensor, or detector API.
Published track coordinates are available in the track files, but live
arena-absolute position is not available on a real Tello and is not exposed in
the simulator contestant API either.

### Mission pads (optional)

Mission pads provide measurements relative to a visible physical landmark.
They do not expose arena-absolute pose or detect racing gates. Update both
the server and IDE client before using these methods.

| Method | Behaviour |
| --- | --- |
| `enable_mission_pads()` | Enables downward detection; unsupported hardware/setup raises an error. Call before takeoff. |
| `mission_pad()` | Waits for one new state packet; returns `{"id": 1, "x_cm": ..., "y_cm": ..., "z_cm": ...}` in the detected pad's coordinate frame, or `None` when unseen. Invalid/stalled telemetry raises. |
| `center_on_mission_pad(pad_id, height_cm=80, speed=20)` | Requires a fresh detection of that exact ID, then moves to `(0, 0, height_cm)` relative to the pad, preserving heading. Does not land. |
| `disable_mission_pads()` | Disables detection after the flight. |

IDs must be integers 1–8; an invalid ID is rejected rather than replaced with
another pad. Centre height is clamped to 30–120 cm and speed to 10–100 cm/s with
the usual warnings. Measurements use the SDK pad axes, not body axes; centring
does not require the drone's nose to align with the pad's rocket. A successful
movement acknowledgement is not a verified landing alignment: read fresh pad
measurements again before landing.

Enable, disable and centre commands also have validated block equivalents.
The structured `mission_pad()` observation is Python-only, like camera frames:
its validity/identity and coordinates must be consumed as one snapshot rather
than separate scalar sensor reads from potentially different packets.

The simulator models pad 1 at its takeoff location with limited visibility;
the mock adapter deliberately reports unsupported. See the
[circuit solution](../solution/README.md#optional-mission-pad-startup-and-landing)
for the complete startup/return workflow, bounded retries and recovery behaviour.

## Errors, stopping, and cleanup

The IDE package exports these exceptions:

- `DroneError`: Squadrone rejected a command or the drone operation failed.
- `ConnectionError`: Squadrone could not be reached, disconnected, or timed
  out. It is a subclass of `DroneError`.
- `EmergencyStop`: the browser operator stopped the control session. It is a
  `BaseException`, not an `Exception`, so a broad `except Exception` cannot
  accidentally swallow a safety stop.

Use the context manager for cleanup, and do not catch and ignore an emergency
stop:

```python
from squadrone import Drone, DroneError

try:
    with Drone() as drone:
        drone.takeoff()
        drone.fly(fb=40, seconds=0.5)
        drone.hover()
        drone.land()
except DroneError as exc:
    print(f"Flight command failed: {exc}")
```

`EmergencyStop` bypasses the `except DroneError` block. The context manager
still closes the connection, allowing Squadrone's safety teardown to centre
the sticks and land.

The cockpit Stop/Emergency Stop path always centres RC sticks before landing
or cutting motors. Closing or losing an IDE connection also centres the sticks
before landing. Normal code should still finish with `hover()` and `land()` so
its intent is explicit.

## Complete IDE example

```python
from squadrone import Drone

with Drone(timeout=30) as drone:
    print(f"Battery: {drone.battery}%")
    drone.takeoff()

    # Bounded control updates make a safe closed-loop controller.
    for _ in range(20):
        frame = drone.frame(timeout=1)
        if frame is None:
            drone.hover()
            continue

        # Replace these values with outputs from your own detector/controller.
        steer = 0
        climb = 0
        drone.fly(fb=35, ud=climb, yaw=steer, seconds=0.1)

    drone.hover()
    drone.land()
```

See `examples/01_first_lap.py` for discrete commands,
`examples/02_smooth_lap.py` for continuous control, and
`examples/03_vision_gate.py` for a camera-guided controller skeleton.
