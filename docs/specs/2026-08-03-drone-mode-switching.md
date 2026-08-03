# Runtime Drone Switching

**Date:** 2026-08-03 · **Status:** Implemented

## Purpose

An operator can move between hardware-free rehearsal and a real DJI Tello without restarting the
application. The header always names the active adapter and presents one action:

| Active mode | Button label | Requested mode |
|---|---|---|
| Simulator | **Use real Tello** | `tello` |
| Real Tello | **Use Simulator** | `sim` |

The button is disabled before the server reports a mode, while a switch is in progress, and while a
block program or Python mission is running.

## WebSocket contract

The browser requests a mode with:

```json
{"type": "switch_drone", "mode": "sim"}
```

or:

```json
{"type": "switch_drone", "mode": "tello"}
```

The server accepts only `sim` and `tello`. A transition first broadcasts the current mode with
`"switching": true`. On success it broadcasts the new `scene`, scenery catalog, and final mode with
`"switching": false`. This ordering hides simulator-only arena controls while hardware is active
and restores them before the UI announces that the simulator is ready.

Switch requests are refused when a mission is active or another switch is already in progress.
Requesting the already-active mode is idempotent and simply returns its current mode state.

## Adapter lifecycle and failure behavior

For a Tello transition, the candidate adapter must connect before it becomes active. If construction
or connection fails, the existing adapter remains active and the browser receives both its unchanged
mode and a `could not connect to Tello` error.

When the app starts in simulator mode, the server retains that exact `SimDrone` while Tello is active.
Returning to the simulator therefore preserves the configured seed, movement noise, selected scenery,
and edited victim layout. A launch that starts directly in Tello mode creates a default simulator
lazily on the first request for `sim`.

The pose broadcaster invalidates its last-value cache whenever the adapter identity changes. This
ensures the restored simulator sends its current pose even when that pose matches the last value sent
before switching to Tello.

## Physical-drone safety boundary

Switching adapters selects where future programs are sent. It deliberately does not land, emergency
stop, or otherwise move the Tello. Before selecting **Use Simulator**, the UI asks the operator to
confirm that the physical aircraft is safely landed. Mission-active switching is also rejected so a
program cannot continue against a different adapter halfway through execution.

## Verification

Server regression coverage exercises simulator → Tello → the same simulator, successful Tello
execution, and failed Tello connection fallback. Frontend asset coverage verifies that both labels and
both protocol modes remain present.
