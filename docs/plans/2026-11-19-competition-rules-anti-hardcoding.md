# Competition rules: closing the hard-coded-route exploit

Date: 2026-11-19 · Status: proposal for the organisers · Source rules: *ICONIP 2026 Drone Search and
Find Competition — Details* (snapshot of iconip2026.org/dsf-comp/details, 2026-09-03)

## Summary

Under the published rules a team can score the maximum 6/6 **without using the camera at all**: fly
a fixed sequence of moves to the three published fire coordinates, flip at each, and land. Vision
adds nothing to that score, costs flight time (the tie-breaker), and adds the risk of a false
positive. The rational strategy is to skip vision, which defeats the purpose of the event.

The event is aimed at high-school students, who will not train models or write vision code. The
marker detector is supplied by the competition platform and is the same for every team; its
training data already includes the decoys, so a team that does use it would not approach a decoy
either. The skill the event can actually test is therefore **using what the camera reports to make
decisions in the program**: an `if` on a sensor, a search that brings each stand into view, and an
approach that ends close enough to count. Today none of that is needed, because the answer to
"which stands are fires?" is printed in the rules.

The root cause is that the *identity* of each marker is published together with its position. The
fix keeps the physical layout the organisers want, but makes the decision of which stand to flip at
one that only the camera can make:

1. **Shuffle the printed faces across the seven fixed stands before every attempt**, after the
   team's code is locked. Positions stay public; which three are fires does not.
2. **Score the sum of the best two of three attempts**, so a blind team cannot win a lottery by
   guessing, while one crashed attempt is still forgiven.
3. **Credit each flip to the single nearest stand, if it is within 0.8 m**, so a flip between two
   stands cannot claim whichever one turns out to be the fire.
4. **Break ties on fewer false positives first**, then flight time.

Fixes 1–3 are what close the exploit. None of them asks students for more vision skill than the
platform already provides, and none of them forces a longer or riskier flight than today's rules:
every stand can be checked from above the take-off pad (§4, F1a). In the simulator, a program
built only from the supplied blocks scores 6/6 on all 35 possible arrangements, and the hard-coded
route scores nothing. Proposed replacement rule text is in §5.

## 1. The exploit, reproduced

The fixed layout is:

| Marker | Class | x (m) | y (m) |
|---|---|---|---|
| Fire 1 | red circle | 0.75 | 3.25 |
| Fire 2 | red circle | 5.25 | 3.25 |
| Fire 3 | red circle | 3.00 | 0.50 |
| Decoy 1 | green triangle | 0.75 | 1.50 |
| Decoy 2 | black square | 5.25 | 1.50 |
| Decoy 3 | green circle | 1.75 | 0.50 |
| Decoy 4 | black triangle | 4.25 | 0.50 |

Take-off is at (3.0, 3.0). This program uses no sensing of any kind:

```
takeoff
left 100      → (2.0, 3.0), 1.27 m from Fire 1   flip
right 200     → (4.0, 3.0), 1.27 m from Fire 2   flip
left 100
back 150      → (3.0, 1.5), 1.00 m from Fire 3   flip
forward 150   → back over the pad
land
```

Run through the simulator's own competition arena and scorer (`comp1/sim`, scenery `corridor`):

| Condition | Result |
|---|---|
| Perfect odometry | 3/3 fires, back at pad, **success** |
| 10 % random error on every move, 200 seeds | 173/200 runs perfect (86.5 %) |
| …the same, best of three attempts | ≈ 99.7 % chance of a perfect 6/6 |

A real Tello holds position indoors on its downward vision sensor well enough for a route this
short; the rules do not define the "required detection position", so in practice a referee will
accept a flip anywhere reasonably near the marker, and this route qualifies.

## 2. Why vision earns nothing

| # | Cause | Effect |
|---|---|---|
| R1 | Marker identities are fixed **and** published with their coordinates. | The classification is answered before the drone takes off. A route can visit the three fires and never go near a decoy. |
| R2 | The only penalty is for flipping at a decoy, and decoys sit at known positions. | Nobody is ever penalised: the blind route never goes near a decoy, and a team using the supplied detector does not flip at one either, because its training set includes the decoys. The decoys are decoration, so detection never changes a score. |
| R3 | Tie-break is shortest flight time. | Scanning and approaching under vision takes longer than flying straight there. Between two 6/6 runs, the blind one wins. |
| R4 | The rules describe colour and shape recognition as the teams' task. | For this audience the detector is the platform's, identical for every team. Rules that score detection quality would score the platform, not the students. What students can be tested on is acting on the detector's output. |
| R5 | "Required detection position" is undefined. | Generous judging makes a dead-reckoned route easy to land, and lets a flip placed between two stands be argued either way. |
| R6 | Best of three attempts. | Harmless while identities are fixed. Once they are randomised (F1) it would let a blind team re-roll a guess three times — see F2. |

The organisers' own text states the intent the rules fail to enforce: *"The search route itself may
use programmed movements and rotations. Computer vision is used to decide whether the object being
observed is a valid fire target."* A programmed route is fine. A programmed **decision** is the
problem, and the rules currently allow it.

## 3. Constraints on a fix

- **Keep the physical setup reproducible.** The organisers want a consistent layout teams can
  rehearse. Stands, positions, heights and take-off point should not change.
- **Keep it judgeable by a referee with a tape measure.** No motion capture, no log inspection.
- **Keep it winnable by beginners.** Block-coding students using the supplied detector must still
  be able to score well. Heavy penalties for misses would punish them more than blind teams.
- **Do not make the flight more dangerous.** In a 6 × 4 m enclosure, touching a wall or a stand
  almost always ends the attempt: the Tello usually goes down, and even when it does not, recovery
  costs time it cannot make back. A fix must not force teams onto longer routes past more
  obstacles, and the scoring must not make a single crash fatal to a team's result.
- **Do not ban programmed routes.** The rules explicitly allow them, and "you may not hard-code" is
  unenforceable: a hard-coded route that lets vision decide is exactly the intended solution.
- **Do not raise the vision bar.** Students use the supplied detector and blocks. A fix must make
  *using* the sensor necessary, not make the sensor harder to build.

## 4. Proposed fixes

### F1 — Shuffle marker faces before every attempt (closes R1, R2)

Keep the seven stands at the seven published positions, all facing the same way as today. Before
each attempt, after the team's code is locked, a referee shuffles which printed face goes on which
stand: three red circles and four decoys across seven stands, 35 equally likely arrangements.

A blind strategy now has to guess. Every blind flip is a fire with probability 3/7 and a decoy with
probability 4/7, worth 2·3/7 − 2·4/7 = **−2/7 points on average**. The best blind strategy is to not
flip at all. A team that wants points has to look.

Practicalities:
- Faces attach to the stands with clips or Velcro so a swap takes under a minute.
- Draw the arrangement with dice or from a pre-printed sealed list, in front of the team, *after*
  the code lock (F6). Redraw for every attempt and every team, so watching another team's run
  reveals nothing.

Mirroring or rotating the layout instead does **not** work: the three fires are symmetric about
x = 3.0, so a mirrored layout puts the fires in the same places.

### F1a — Check: achievable for high-school students, and no riskier to fly?

Shuffling means a team must check all seven stands rather than fly straight to three. That would
be a real cost if it meant a long route past every stand. It does not have to:

- All seven stands are **2.2–2.8 m from the take-off pad**, and the supplied detector reads a marker
  out to about **4.1 m**. Hovering over the pad at marker height and turning to each stand's
  published bearing, the detector identifies every stand without the drone moving (checked in the
  simulator: each faced stand reads within 2° of centre, its neighbours ~28° off to the side).
- The drone then only has to fly out to the fires and back, a trip the same length as today's
  hard-coded route. Flying never goes near a decoy.

Both strategies were flown in the simulator against all 35 arrangements, using only what the block
interface offers (`turn`, `move`, `if sees target`, `approach target`, `flip`). Flips were credited
by F3. A collision ends the attempt; flips before it count, flips after it do not. Odometry error is
random per move, plus ±2° per turn.

| Program | Odometry error | Mean score | Perfect 6/6 | Collisions | False positives |
|---|---|---|---|---|---|
| Check from the pad, fly out only to fires | none | 4.69 | 18/35 | 0/35 | 0 |
| Check from the pad, fly out only to fires | 5 % | 4.06 | 12/35 | 1/35 | 0 |
| Check from the pad, fly out only to fires | 10 % | 4.29 | 15/35 | 1/35 | 0 |
| Fixed route to a viewpoint ~1 m from every stand | none | **6.00** | **35/35** | 0/35 | 0 |
| Fixed route to a viewpoint ~1 m from every stand | 5 % | 4.74 | 19/35 | 7/35 | 0 |
| Fixed route to a viewpoint ~1 m from every stand | 10 % | 4.23 | 14/35 | 9/35 | 0 |
| Hard-coded route to the published fires | none / 5 % / 10 % | 0.00 / 0.00 / −0.23 | 0/35 | 0/35 | 0 / 0 / 4 |

What this shows:

- The supplied detector never caused a false positive. Every lost point is navigation.
- The route past every stand collides in 20–25 % of attempts once odometry drifts. Checking from the
  pad collides in about 3 %, about the same as today's hard-coded route. F1 does not force the
  dangerous strategy; choosing the safer one is part of the challenge.
- The pad strategy's misses come from one fixable mistake in the student program: `approach target`
  turns the drone to centre the marker, and the program then assumed its heading had not changed.
  Correcting for that is exactly the kind of sensor-driven reasoning the event should reward.
- The hard-coded route now scores nothing on average, and a little below zero once drift carries a
  flip near a decoy.

### F2 — Sum of the best two of three attempts (closes R6)

With F1 in place but best-of-three kept, a blind team can buy three lottery tickets. Summing all
three attempts closes that, but since a collision usually ends an attempt, it would make one crash
very costly for a beginner team. The sum of the **best two** attempts does both jobs.

Simulated over 30 000 three-attempt sessions: fire +2, false positive −2, miss 0, faces redrawn
every attempt, and a 25 % chance per attempt of a crash part way through (the remaining stands are
lost). "Sensor-driven, p" is a team whose program gets each stand right with probability p. Since
the detector is the platform's, p measures the team's program (its route, its approach), not the
quality of its vision.

| Strategy | Best 1 of 3: mean | Best 1 of 3: P(max) | **Best 2 of 3: mean** | **Best 2 of 3: P(max)** | Sum of 3: mean | Sum of 3: P(max) |
|---|---|---|---|---|---|---|
| Blind, flip at 1 stand | 1.3 | 0 % | 0.9 | 0 % | −0.7 | 0 % |
| Blind, flip at 3 stands | 1.4 | **7.0 %** | 0.6 | 0.2 % | −2.2 | 0 % |
| Sensor-driven, p = 0.8 | 4.6 | 43 % | 7.5 | 7.9 % | 8.4 | 0.5 % |
| Sensor-driven, p = 0.9 | 5.5 | 78 % | 9.8 | 34 % | 12.1 | 5.9 % |

("P(max)": chance of the maximum possible result: 6, 12 or 18.)

- **Best of three** gives a blind three-flip guesser a 7 % chance of a perfect 6, and a blind run
  is the fastest run, so under a time tie-break it beats an honest 6/6.
- **Best two of three** cuts that chance to 0.2 % and leaves a blind team averaging under 1 point,
  against 7.5–10 for a team that uses the sensor, while one crashed attempt is dropped outright.
- **Sum of three** is the strictest against guessing, but every crash counts against the team.
  Choose it only if the organisers expect crashes to be rare.

Penalties tested and **not** recommended: a −1 penalty per missed fire makes a beginner team's
expected score negative, and a −3 false-positive penalty adds little once attempts are combined.

### F3 — Credit each flip to the single nearest stand (closes R5)

- When a flip starts, the referee finds the **nearest stand** to the drone. If it is within
  **0.8 m** horizontally, the flip counts for that stand and no other; otherwise it scores nothing.
- A flip credited to a decoy is a false positive (−2).
- Each fire counts once; repeated flips at the same fire score nothing more (as now).

Floor marking: tape a 0.8 m circle round each stand's foot. Where two circles overlap (the closest
stands are 1.25 m apart: Fire 3's position to its two neighbours on the y = 0.5 row), tape the line
half way between the two stands; each side belongs to its own stand.

Why nearest-stand and why 0.8 m:
- Because a flip can only ever count for one stand, flipping half way between a fire and a decoy is
  still a blind guess, not a way to claim whichever turns out to be the fire.
- A 0.6 m zone, the largest with no overlaps, was tested first and is too tight. The supplied
  `approach target` block aims to stop 0.5 m from the marker, but approaching a side stand at an
  angle it stopped at 0.64–0.70 m, outside the zone. The rule should not take away points the
  platform's own approach earns honestly.

### F4 — Say who owns detection (closes R4)

State in the rules that the platform supplies the marker detector, and that what teams build is
the search and the decisions taken on what it reports. Python teams may substitute their own
vision, but gain no rule advantage from it. This matches what students actually do, and stops
"colour and shape recognition" reading as a task they are expected to engineer.

### F5 — Tie-break: fewer false positives, then flight time (tempers R3)

With identities shuffled, every competitive team is using the sensor, so flight time is a fair
tie-breaker again, and since every team has the same detector it now rewards a well-designed search
route. Putting false positives ahead of it rewards the more careful of two equal-score teams before
the faster one. Order: score → fewer false positives → shorter total flight time, both counted over
the two attempts that were scored.

### F6 — Code lock (makes F1 enforceable)

The program to be flown is loaded onto the competition laptop and locked before the faces are
shuffled. Between the shuffle and take-off the team may not touch the laptop. Any input of marker
positions or identities to the program after the draw is manual correction and voids the attempt
(this extends the existing "no manual piloting or manual correction" rule).

### Considered and rejected

| Idea | Why not |
|---|---|
| Ban hard-coded routes | Contradicts the organisers' own text and cannot be judged. Under F1 a hard-coded route is fine. |
| Mirror or rotate the layout per attempt | The three fires are symmetric about x = 3.0; mirroring leaves them where they were. |
| Randomise the drone's heading at take-off | The Tello has no compass; this punishes every team's navigation without testing classification. |
| Random stand positions | Breaks the reproducible setup the organisers chose, and every team must re-plan its route. F1 gets the same effect with no physical change. |
| Require video evidence of detection | Large judging load, and a recording proves nothing about what decided the flip. |
| Vary the number of fires (2–4) | Valid as an extra-difficulty option; not needed to close the exploit, and it makes "return after all fires are found" harder to state. |
| Add a red non-circle decoy to "test shape recognition" | Tests the platform's detector, not the students: every team has the same one. If it ever misfired on the red decoy, every team would lose 2 points to noise they cannot fix. |
| Let a drone carry on after touching a wall | In this enclosure a touch almost always brings the Tello down, and recovery from one that stays up costs more time than it saves. Best two of three already forgives a crashed attempt. |
| 0.6 m non-overlapping detection zones | The supplied approach block stops outside them on side stands (F3). |

## 5. Proposed rule text

Drop-in additions and replacements for the published sections.

**Target and Decoys Positions** — add under the table:

> The seven stand positions above are fixed. Which stands carry the three fire markers and which
> carry the four decoys is drawn at random by the referee before every attempt, after the team's
> program has been locked. Teams therefore know where the markers are, but not which markers are
> fires.

**Programming and Computer Vision** — replace the vision bullets:

> - The competition interface provides the marker detector. Teams design the search and decide,
>   in their program, how to act on what the detector reports. Teams using Python may use their
>   own vision instead.

**Competition Rules** — add:

> - Before each attempt the team loads its program and hands over the laptop. The referee then
>   draws and mounts the marker arrangement. From this point until landing, the team may not
>   interact with the laptop or the drone. Entering marker identities or positions after the draw
>   is a manual correction.
> - A flip is credited to the stand nearest the drone when the flip begins, provided that stand is
>   within 0.8 m horizontally. A flip credited to a decoy is a misidentification. A flip with no
>   stand within 0.8 m scores nothing. The zones are marked on the floor.
> - If the drone touches a wall, a stand or the floor during flight, the attempt ends. Fires
>   credited before that moment count.

**Assessment Framework** — replace the first paragraph:

> Each team flies three official attempts, with a new marker arrangement drawn for each. The
> team's score is the **sum of its two best attempts**. Each correctly identified fire is worth
> +2 points and each misidentification −2 points, so the maximum is 12 points. Ties are broken
> first by fewer misidentifications, then by shorter total flight time, both over the two attempts
> that were scored.

## 6. Consequences for Drone Coder

Not part of this proposal's rule change, but the simulator is where students rehearse, and today it
rehearses the exploitable version. When the organisers settle the rules:

- `comp1/sim/scenery.py` `_corridor` deliberately ignores `seed` and always places the published
  identities. It should keep the seven positions and shuffle faces per seed (and offer a re-draw on
  reset), so practice in the simulator matches F1. A fixed "published layout" option can stay for
  the tutorial (`tests/test_tutorial_lesson.py` flies the lesson in every arena).
- `comp1/sim/mission.py` `MissionScorer.signal` credits the nearest fire within 1.5 m and ignores
  decoys entirely, so the simulator never shows a −2. It should score F3: nearest stand within
  0.8 m, a false positive when that stand is a decoy, and nothing after a collision.
- Show attempt number and the best-two-of-three total in the mission panel (F2).
- An example in `examples/` (and a tutorial step) for the check-from-the-pad strategy, including
  re-facing the next bearing after `approach target` has turned the drone. That is where the
  simulated students lost their points.
