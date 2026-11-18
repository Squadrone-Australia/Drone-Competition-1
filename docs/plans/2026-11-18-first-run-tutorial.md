# First-run tutorial (2026-11-18)

Status: implemented in `comp1/frontend/tutorial.js`.

## What it is for

A student opening Drone Coder for the first time sees eight panels, eight block drawers and
a red button. Usually no teacher is beside them. The tutorial explains each area next to the
thing itself, and then has the student build and fly one program. The aim is that their first
flight succeeds and they understand why it did.

It was designed around the questions a high-school student actually has, roughly in this
order:

| Question | Where it is answered |
| --- | --- |
| What is this? Will I break something? | Welcome card: "you're flying the simulator… nothing real can crash" |
| Where do the blocks come from? What do the colours mean? | Toolbox step; block names in the text are drawn in their real block colour |
| Why did my program do nothing? | Start-block step ("a block left floating is ignored"), plus a live hint in every build step |
| What does this block do? | Block description step |
| Where is my drone? What can it see? | 3D view, camera and sensor-reading steps |
| Why can't I just tell it to go to the red dot? | Camera step: no map, no GPS, it can only use what is in the picture |
| Something went wrong. Where do I look? | Console step |
| What is the big red button? | Its own step: it cuts the motors and the drone falls. Use Stop instead |
| Is my work saved? | Final card: saved automatically on this computer |
| What do I do next? | Final card: a "find a target" challenge listing the blocks to use |

## Shape

1. **Welcome** (modal). It says what the mission is (find the red targets and signal each
   one), and it can be skipped in one click.
2. **Quick tour**, 7 steps. Each one dims the page and puts a spotlight on one region,
   with a short card beside it. Every card has **Jump to building ›**.
3. **Build and fly**, 8 steps. Each step is checked against the real workspace rather than
   just described. The lesson program is
   `take off → fly forward 50 → turn clockwise 180° → fly forward 50 → land`
   ("fly out and come home"). The student opens a drawer, drags blocks, edits a number and
   presses the real Run button. Every step has **Do it for me**, so a student who can't get
   a trackpad drag to work still gets to see the drone fly.
   The Run step stays on the result until the student presses Next, with **Live execution**
   listing every command the drone ran.
4. **Python view**: the program they just flew, shown as code.
5. **Final card**: a recap and the next challenge. The challenge is also written to the
   console, so it is still there after the card closes.

It opens by itself once, and the **Tutorial** button in the top bar reopens it.

## Rules

- **It only flies the simulator.** It will not start against a real Tello: the button shows
  a notice instead. If the drone is switched while it is open, it closes itself.
- **It never throws away a student's blocks.** If the workspace already has blocks, they
  are put aside, both in memory and in `localStorage` (`comp1.tutorial.stash.v1`), and
  offered back at the end. If the tutorial is skipped, closed, or the drone is switched,
  they come back automatically. A tab closed mid-lesson gives them back on the next load.
- **It opens by itself only when that is clearly wanted.** That means the simulator, an
  empty workspace, and storage that can remember the tutorial was seen
  (`comp1.tutorial.v1` = `done` | `skipped`). Without storage it would reopen on every
  launch at a venue.
- **Nothing in it may throw.** This is the same rule as `buffer.js`.
- **Hints wait; praise doesn't.** A wrong number gets its hint only after the student stops
  typing. A correct one is accepted while the number editor is still open.

## Why this lesson program

The obvious first program, "fly forward 100 cm", was measured against both arenas first. It
flew into the wall of the competition arena on **every** attempt, because the start pad there
is a metre from the wall. The out-and-back program cannot hit anything, by construction:
nothing solid is ever placed within `PAD_CLEARANCE_M` (1.2 m) of the start pad, and 50 cm is
well inside that. It also lands back on the pad, so "come home" is literally what happens.

The challenge on the final card (turn until a target is seen, approach, signal, land) found a
target in 40 out of 40 random layouts of each arena, with no crashes.

## Tests

The lesson is a contract between the frontend and the simulator, held in three places that
change together:

- `tests/fixtures/tutorial_lesson.json`: the program.
- `tests/js/tutorial.test.js`: checks the tutorial still builds exactly that program. It
  also covers the checks and hints against the real vendored Blockly, the storage edge
  cases, and a full click-through of the tutorial in jsdom over the real `index.html`.
- `tests/test_tutorial_lesson.py`: flies the program through the real interpreter in every
  arena, with 25 layouts each, and requires no crash and a landing back on the pad.

## Found by trying it in a browser

The jsdom tests didn't catch these two:

- Blockly gives its toolbox `z-index: 70`, so the drawers never dimmed, and a card next to
  them slid underneath. The tutorial now sits at 80/81. That is still below Blockly's
  dropdowns (1000) and number editor (99999), which the build steps need on top.
  `#closed-overlay` moved to 200 so that it still always wins.
- The 400 ms poll restarted the 700 ms "wait until they stop" timer every time it ran, so a
  dropped-but-not-snapped block never got its hint. There is now a regression test for this.

## Usability tests

The tutorial was tried twice by a Sonnet subagent playing a high-school student. It could
only use screenshots, clicks, drags and typing, on a fresh browser origin, so each run was a
true first launch. Each round's findings were fixed before the next round.

**Round 1** ("Maya", 15, impatient, skims):

- The step counter jumped from "2 of 8" to "4 of 9", because the put-aside step was
  re-decided live.
- The disabled Next button swallowed clicks without saying why.
- Run with a loose or empty program reported a bare "mission done".
- Every open drawer covered the start block. The fresh start block moved from (30, 30)
  to (340, 30).
- A found target was only counted in the header, never said in the console.
- The 12-card tour felt like "click, click, click", so it was cut to 7.
- The first flight's result was covered by the next card.
- The dimmed background was too dark to read the blocks.

**Round 2** ("Leo", 14, careful, trackpad). All the round-1 fixes were confirmed. New
findings:

- On a cold cache the tutorial missed `drone_mode`, which the server sends once on
  connect, and then refused to open beside a green "connected". This was latent for
  every late-loading panel. `app.js` now replays the last of each state sent on connect.
- The answer to an early Next was wiped by the next poll.
- The Run card covered the program.
- The challenge vanished on Finish.
- The `# block` ids in the Python view read as garbage.
- The obstacle message ("the attempt does not count") was ambiguous.
- "About 3 minutes" was optimistic. Both students took 10 minutes or more.

## Not done

- **The text is English only.** It matches the rest of the UI. All of it is in the `STEPS`
  table in `tutorial.js`, so translating means editing one place.
- **The flight is very fast.** The simulator flies the lesson in about a second. The Run
  step now waits on the result, and Live execution replays it in words, but the flight
  itself is easy to miss.
- **Nesting blocks inside a loop has no feedback** once the tutorial is over. A stack that
  sits inside "repeat" without snapping looks connected.
- **The Next button moves between tour cards** as the card height and position change.
