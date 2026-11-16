# Project review and circuit solutions

Reviewed the Python API and run ownership, external client/server camera path,
simulator motion and race scoring, published track files, example 05, and the
local solution folder against `CLAUDE.md` and the supplied competition PDF.

## Fixed findings

- **RC commands survived successful Python script completion.** `ScriptRun`
  only performed hover/land teardown on errors and stops. A script ending after
  `fly(fb=80)` left the setpoint active and its captured API object usable.
  All non-emergency outcomes now centre sticks and land, and the session is
  revoked when its worker finishes. Regression coverage includes normal return
  and `SystemExit`, plus the existing stop and emergency-stop tests.
- **Display annotations could contaminate external sensor frames.** At a
  native width of 640 pixels, display and sensor processing shared the same
  array. An adapter annotating in place modified the later raw JPEG. The
  display now receives its own copy; tests cover native/resized images and
  both copying and in-place adapters.
- **Simulated sensor images contained artificial gate numbers.** The renderer
  painted labels at opening centres, providing information not present in the
  physical scene and contaminating vision input. Labels have been removed from
  sensor rendering; the display minimap retains its numbering.
- **The runnable racing examples used obsolete assumptions.** Example 05
  ranged circular hoops, assumed a 0.9 m takeoff instead of the simulator's
  1.0 m, placed some staging points outside flight bounds, continued after
  failed alignment, and replaced its estimate with the intended exit despite
  RC lag, yaw corrections and command rounding. The old local solution also
  expected a fourth gate passage on Circuit 2 and omitted Circuit 3.
  The replacement controller loads the current published layouts, measures
  initial height, accounts for transmitted integer offsets, uses legal staging
  points and explicit crossings, and returns to the pad. Entry points release
  control on completion or failure. Relevant solution sources are now visible
  to Git rather than hidden by the blanket `solution/` ignore rule.
- **Gate completion did not validate the document's circuit shapes.** The
  earlier routes could finish the scorer while missing the figure-eight loops.
  Circuit 2's G1/G3 directions were also wrong, as was Circuit 3's G3 direction.
  In the PDF's orientation (+x down, +y right), Circuit 2 now passes G1/G2
  downward and G3 upward; Circuit 3 passes all three downward. Explicit upper
  and lower loops reproduce one and two path intersections respectively.
  Regression tests independently check the PDF gate headings and intersections
  of the actual simulator flight legs, in addition to scored gate passes.

## Mandatory vision and the physical gate

The contestant solution now implements mandatory visual localisation before the
route and before every gate crossing. It detects rectangular red/orange PVC
frames with white elbows and a centre mounting fitting, estimates metric pose
from calibrated image geometry, and applies bounded proportional/integral RC
corrections. Loss of a valid image/pose aborts instead of falling back to a blind
coordinate pass. A final short SDK displacement uses the last measured range
once the frame is about to leave the forward camera's field of view.

The external client's `frame(fresh=True)` waits for a new received JPEG, and the
Tello decoder expires old frames after a stall. These prevent stopped streams
from presenting indefinitely cached pixels as new control measurements. No
platform gate detector, ground-truth pose sensor or sensor overlay was added;
perception stays in the explicitly requested contestant solution.

## Validation and limits

The full Python suite passed 322 tests, and both JavaScript test files pass.
Geometry tests verify gate directions, one pass
per gate and zero/one/two route intersections independently of visual trims.

The final camera-guided scripts also completed through live local servers and
the installed external client: all three gates passed in each circuit, with
observed accuracy 0.987, 0.987 and 0.995 and times 31.82 s, 38.62 s and 43.06 s.
Timing and smoothness scores vary with scheduling.

Pixel-based flight tests render actual simulated camera views and JPEG-encode
them. They exercise starting offsets up to 15 cm, 5-degree heading errors,
persistent drift of (0.015, -0.025, 0.005) m/s and a 12 cm lateral/8 cm vertical
gust. All three circuits finish under the tested disturbances. Other tests cover
red hue wraparound, shaded elbows, a split lower tube, missing/cropped images,
client frame timeouts and decoder stalls. These are bounded simulator scenarios,
not a claim of arbitrary wind tolerance or hardware certification.

The supplied real-gate photo informed the tube-colour and mounting-joint
handling. Structural fixtures reproduce those features; the photo itself has
not been used for calibrated pose validation. Physical width/height/tube diameter
and measured camera intrinsics are configurable and still need measuring on the
real rig. The metric fit assumes a level camera and upright gate.

The rectangular simulator, scoring formula and document loop topology are
retained. Between landmarks and on the return to the pad, motion remains command
based. The final crossing has a short interval without a fully visible frame.
No continuous curved-flight bonus or physical flight-test result is claimed.
