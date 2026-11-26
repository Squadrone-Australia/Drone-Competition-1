# Tutorial: the optional camera lesson (2026-11-20)

Status: implemented in `comp1/frontend/tutorial.js`. It extends the
[first-run tutorial](2026-11-18-first-run-tutorial.md), whose rules all still hold.

Removed on 2026-11-21 and **restored on 2026-11-26**: students still needed to be taught how
to write a search loop, and the goal-only challenge left on the final card did not teach it.
It came back as it was, with its fixture (`tests/fixtures/tutorial_vision_lesson.json`) and
tests. Two things from the removal stayed: the whole tutorial flies in the square arena, and
the console challenge after the first lesson gives a goal and where to look, not the five
blocks (it now also points to this lesson).

## Why

The first-run tutorial taught a fixed route: take off, fly out, turn round, fly back, land. It
said that "your program only knows what's in this picture", but it never had a student use
the camera. The final card listed a "find a target" challenge as plain text: a list of block
names, with no checks and no "Do it for me", and nothing flown.

That gap matters for the competition. The
[rules proposal](2026-11-19-competition-rules-anti-hardcoding.md) closes the hard-coded-route
exploit so that the skill the event tests is using what the camera reports to make decisions.
A student whose only guided program is a fixed route has not been taught that skill.

The challenge as text also hid the two ideas a vision program needs that a stack of moves
does not:

- **A test goes into a slot.** "target seen?" is a different shape from a command block. It
  plugs into the gap at the end of "keep doing until". A student who has only stacked blocks
  drops it beside the loop instead.
- **Blocks go inside a loop, not under it.** A turn snapped below the loop looks connected,
  but it runs once, after the loop ends. The loop is left empty, so it never looks round at
  all. The first-run plan already listed "nesting inside a loop has no feedback" under
  *Not done*.

## What it is

An optional second chapter, **Camera lesson**, with 9 counted steps. A student gets to it
from two places:

- **The first lesson's final card.** "Camera lesson ›" is the primary button there; Finish
  still ends the tutorial. The text challenge that used to be on this card became this
  lesson.
- **The welcome card.** A "Camera lesson ›" link sits beside "Skip". It is for a returning
  student who reopens the tutorial and doesn't want to redo the basics.

The program it builds is the old challenge, with each new idea given a checked step of its
own:

```
take off
keep doing until ⟨target seen?⟩
    turn ↻ clockwise 30°
approach target and stop
signal target found 🎉 by flipping
land
```

| Step | What the student does | What the check catches |
| --- | --- | --- |
| Intro | Spotlight on the camera and its readings | (none) |
| Put aside | Only if they have blocks of their own | (none) |
| Take off | Same as the first lesson | Not snapped on |
| Loop | Snap "keep doing until" under take off | Not snapped on, wrong block |
| Test | Drag "target seen?" into the loop's slot | Left beside the slot, wrong test in the slot |
| Turn | Put the turn **inside** the loop, change 90 to 30 | Snapped below the loop, wrong number |
| Approach | Snap "approach target and stop" **below** the loop | Dropped inside the loop |
| Signal, land | As before | As before |
| Run | Press Run and watch the camera | Crash, or a signal that didn't count |

Every build step has **Do it for me**. It repairs the student's attempt rather than replacing
it. For example, it moves their own turn block from below the loop into it, and it uses
their loose "target seen?" instead of making a second one.

A step can check a block in stages: its shell first (`SHELL`), then its test (`COND`), then
the blocks inside it (`FULL`). That is why "keep doing until" gets three steps without the
first one complaining about an empty slot.

### Success means a find that counted

The signal block runs whether or not the approach reached a target. So the run step does not
praise a flight that simply finished. It waits for the server's scorer to mark the signal
`credited` on the `mission` message. If the drone signalled where nothing could be credited,
the card says the find didn't count and asks the student to press Reset and Run again. The
usual cause is a second Run without Reset, when the target in front of the drone was already
counted.

The credit is broadcast from a task of its own, so it can arrive just after `finished`. The
step waits 600 ms for a late credit before it decides.

### Workspace rules

The lesson needs an empty program, as the first lesson does:

- **The first lesson's program** is cleared without asking. It is the tutorial's own program,
  and the student has just learned to rebuild it.
- **The student's own blocks** are put aside on a card that asks first. They are offered back
  on the lesson's last card, and they come back if the student closes the lesson. This uses
  the same stash as the first lesson.
- **If blocks are already put aside**, whatever is on screen is the tutorial's, so it is
  cleared. Putting it aside again would overwrite the student's real work.

Back never leads out of the lesson, and its progress bar fills by itself rather than ending
the first chapter at half full.

### Revisited 2026-11-21: does the lesson hand students the answer?

Mostly not. It teaches the search, approach and signal moves, and it stops at one target.
The skills the competition actually tests are all left to the student: a search that brings
every stand into view, re-facing after `approach target` has turned the drone, the `if` and
distance blocks, and getting home safely.

The console challenge that was logged after the first lesson was the exception. It listed
all five blocks in order, so it was a recipe with none of the reasons the lesson gives. It
now gives the goal and which drawers to look in, and (since the 2026-11-26 restore) names
this lesson as the step-by-step route.

Letting the student pick the turn angle (anything from 10° to 60°) was tried and dropped. The
angle teaches nothing in the lesson: every angle up to 90° found a target in both arenas
(square arena, 20 layouts; the competition arena's layout is fixed), and only 120° failed,
in the competition arena, by circling until the loop gave up. The lesson keeps a fixed 30°.
Students find out what their numbers do when they program the competition arena themselves.

### Flown in the square arena

The whole tutorial now switches to the square arena as it opens and switches back when it
ends (see the first-run plan's rules). For this lesson, that means students no longer meet
the corridor's "lost sight" warning described below. The lesson is still flown in every
arena by the tests.

## Measured before it was chosen

The program was flown in the simulator, scored the way the server scores it, after commit
`e6f9e6e` made signs and their posts solid:

| Detector | Arena | Corridor |
| --- | --- | --- |
| Colour (HSV) | 40/40 found, 0 crashes | 40/40 found, 0 crashes |
| AI model | 20/20 found, 0 crashes | 20/20 found, 0 crashes |

`tests/test_tutorial_lesson.py` re-flies a few layouts of each arena with each detector as a
regression guard. It doesn't repeat the full measurement, because a corridor flight takes
about 3 s of wall-clock time.

It was also clicked through in headless Chrome against the real server. The flight was
credited, and the screenshots showed no card covering the program it describes.

## Known issue: approaching a high sign in the corridor

In the corridor, every approach ends with **"lost sight of the marker — approach stopped"**,
even though the find counts.

Corridor signs hang at 2.0 m and the drone hovers at 1.0 m. The approach sees the sign at
about 2.3 m and takes a full 100 cm step. From 1.3 m away the sign is 38° above the camera,
which is out of the top of the frame. The approach then times out after 2.5 s, still inside
the 1.5 m credit radius.

This was already true of "approach target and stop", and of the old text challenge; the
tutorial did not introduce it. The fix belongs in the approach controller, in both
`interpreter.py` and `api.py`. Two options:

- limit each forward step so the target stays inside the vertical field of view;
- treat "lost it off the top, straight after a forward step" as arriving.

The test holds the arena to zero warnings and allows only this one warning in the corridor,
so it will tighten once the controller is fixed.

## Found in a real browser

The server sends `running: false` *after* `finished`. The jsdom tests sent them the other way
round. The late render wiped the ✓ line under a successful flight, in this lesson and in the
first one. `runStatus()` now repeats the praise while the flight is done, and a test now
sends the messages in the server's order.

## Tests

The lesson is held in three places that change together, exactly like the first one:

- `tests/fixtures/tutorial_vision_lesson.json`: the program.
- `tests/js/tutorial.test.js`: the tutorial builds exactly that program with no warnings. The
  file also checks each staged step and hint against the real vendored Blockly, Do it for me
  for the nesting mistakes, and both entrances clicked through in jsdom. That includes the
  put-aside, the uncredited signal and the late credit.
- `tests/test_tutorial_lesson.py`: flies it in every arena with both detectors and requires a
  credited find, no crash, a landing, and (outside the corridor) no warnings.

## Not done

- **The decision blocks.** "distance to target", "direction to target", "target is
  left/centre/right" and `if` are still not taught. The last card points students to the
  block descriptions. A step that builds an `if` on a sensor was considered and left out to
  keep the lesson to about 5 minutes.
- **Finding all three** stays a question on the last card and in the console, for the reason
  the first-run plan gives: there is no honest one-line recipe for it.
- **The corridor approach warning** (see above).
