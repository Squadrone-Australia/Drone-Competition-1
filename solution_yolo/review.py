"""Check, correct and hand-place labels on real frames.

    python -m solution_yolo.review solution_yolo/data/real

Opens each frame ``real_label`` drafted (or could not), with its rings drawn:
box in blue, keypoints numbered 0-8 (green), the selected ring brighter.

Keys
  a / Enter   accept the labels as shown, next frame
  e           no gate in this frame (a background image), next
  x           reject the frame (left out of the dataset), next
  Right / d   next frame, unchanged      Left / s   previous frame
  g           new ring: click its 9 keypoints in order (0-8, see README);
              Space skips a keypoint that is out of shot or hidden
  1-9         re-place keypoint 0-8 of the selected ring with the next click
  h           hide the keypoint nearest the mouse (out of shot)
  b           drag a box for the selected ring (only needed when fewer than
              four corners are visible; otherwise the box follows the corners)
  Tab         select the next ring        Delete / c   delete the selected ring
  q / Esc     save and quit
Mouse: drag a keypoint to move it.

Keypoint order, as the camera sees the ring (left/right in the image, even
from behind): 0 top outer-left, 1 top outer-right, 2 top inner-right,
3 top inner-left, 4 bottom inner-left, 5 bottom inner-right, 6 bottom
outer-right, 7 bottom outer-left, 8 centre. Corners are where the orange
segment's straight end meets the checker, on the band's outer or inner edge.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution_yolo.keypoints import (  # noqa: E402
    KPT_NAMES,
    NUM_KPTS,
    VISIBLE,
    box_from_keypoints,
    read_labels,
    write_labels,
)
from solution_yolo.real_label import load_manifest, save_manifest  # noqa: E402

KEY_LEFT = {2424832, 65361, ord("s")}
KEY_RIGHT = {2555904, 65363, ord("d")}
KEY_DELETE = {3014656, 65535, ord("c")}
GRAB_PX = 14


class Ring:
    def __init__(self, box=None, kpts=None, manual_box=False):
        self.box = box
        self.kpts = np.zeros((NUM_KPTS, 3)) if kpts is None else np.array(kpts, float)
        self.manual_box = manual_box

    def refresh_box(self, w, h):
        if self.manual_box:
            return
        box = box_from_keypoints(self.kpts, w, h)
        if box is not None:
            self.box = box


class Reviewer:
    def __init__(self, root: Path, stems: list[str], manifest: dict, scale: float):
        self.root, self.stems, self.manifest = root, stems, manifest
        self.scale = scale
        self.index = 0
        self.mouse = (0, 0)
        self.drag = None  # (ring, keypoint) being moved
        self.place = None  # [ring, next keypoint] while placing a new ring
        self.replace_kp = None  # keypoint index to set on next click
        self.box_start = None
        self.box_mode = False
        self.load()

    # --- frame state ----------------------------------------------------------

    @property
    def stem(self) -> str:
        return self.stems[self.index]

    def load(self):
        self.image = cv2.imread(str(self.root / "images" / f"{self.stem}.jpg"))
        self.h, self.w = self.image.shape[:2]
        self.rings = [
            Ring(box, k)
            for _, box, k in read_labels(
                self.root / "labels" / f"{self.stem}.txt", self.w, self.h
            )
        ]
        self.selected = 0
        self.place = self.replace_kp = self.drag = None
        self.box_mode = False

    def save(self, status: str):
        rings = [] if status == "empty" else self.rings
        rings = [r for r in rings if r.box is not None]
        write_labels(
            self.root / "labels" / f"{self.stem}.txt",
            [(r.box, r.kpts) for r in rings],
            self.w,
            self.h,
        )
        if status == "accepted" and not rings:
            status = "empty"
        self.manifest["frames"][self.stem]["status"] = status
        save_manifest(self.root, self.manifest)

    def move(self, step: int) -> bool:
        target = self.index + step
        if not 0 <= target < len(self.stems):
            return False
        self.index = target
        self.load()
        return True

    # --- interaction ------------------------------------------------------------

    def to_image(self, x, y):
        return x / self.scale, y / self.scale

    def nearest(self, x, y):
        best, best_d = None, GRAB_PX / self.scale
        for ring in self.rings:
            for k, (kx, ky, v) in enumerate(ring.kpts):
                if v > 0:
                    d = np.hypot(kx - x, ky - y)
                    if d < best_d:
                        best, best_d = (ring, k), d
        return best

    def on_mouse(self, event, x, y, flags, param):
        x, y = self.to_image(x, y)
        self.mouse = (x, y)
        if self.box_mode and self.rings:
            if event == cv2.EVENT_LBUTTONDOWN:
                self.box_start = (x, y)
            elif event == cv2.EVENT_LBUTTONUP and self.box_start:
                (x0, y0), ring = self.box_start, self.rings[self.selected]
                ring.box = (min(x0, x), min(y0, y), max(x0, x), max(y0, y))
                ring.manual_box = True
                self.box_mode, self.box_start = False, None
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            if self.place is not None:
                ring, k = self.place
                ring.kpts[k] = (x, y, VISIBLE)
                self.advance_place()
            elif self.replace_kp is not None and self.rings:
                ring = self.rings[self.selected]
                ring.kpts[self.replace_kp] = (x, y, VISIBLE)
                ring.refresh_box(self.w, self.h)
                self.replace_kp = None
            else:
                self.drag = self.nearest(x, y)
        elif event == cv2.EVENT_MOUSEMOVE and self.drag:
            ring, k = self.drag
            ring.kpts[k, :2] = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and self.drag:
            self.drag[0].refresh_box(self.w, self.h)
            self.drag = None

    def advance_place(self):
        ring, k = self.place
        if k + 1 < NUM_KPTS:
            self.place = [ring, k + 1]
            return
        self.place = None
        ring.refresh_box(self.w, self.h)
        if ring.box is None:
            self.box_mode = True  # too few corners: the box must be drawn

    def key(self, key: int) -> bool:
        """Handle a key; False to quit."""
        if key in (ord("q"), 27):
            return False
        if key in (ord("a"), 13):
            self.save("accepted")
            self.move(1)
        elif key == ord("e"):
            self.save("empty")
            self.move(1)
        elif key == ord("x"):
            self.save("rejected")
            self.move(1)
        elif key in KEY_RIGHT:
            self.move(1)
        elif key in KEY_LEFT:
            self.move(-1)
        elif key == ord("g"):
            self.rings.append(Ring())
            self.selected = len(self.rings) - 1
            self.place = [self.rings[-1], 0]
        elif key == ord(" ") and self.place is not None:
            self.advance_place()
        elif ord("1") <= key <= ord("9") and self.rings:
            self.replace_kp = key - ord("1")
        elif key == ord("h"):
            hit = self.nearest(*self.mouse)
            if hit:
                hit[0].kpts[hit[1]] = 0
                hit[0].refresh_box(self.w, self.h)
        elif key == ord("b") and self.rings:
            self.box_mode = True
        elif key == 9 and self.rings:
            self.selected = (self.selected + 1) % len(self.rings)
        elif key in KEY_DELETE and self.rings:
            del self.rings[self.selected]
            self.selected = 0
        return True

    # --- drawing ------------------------------------------------------------------

    def render(self) -> np.ndarray:
        out = self.image.copy()
        for i, ring in enumerate(self.rings):
            bright = i == self.selected
            if ring.box is not None:
                x0, y0, x1, y1 = (int(round(c)) for c in ring.box)
                cv2.rectangle(
                    out, (x0, y0), (x1, y1), (255, 160, 0), 2 if bright else 1
                )
            for k, (x, y, v) in enumerate(ring.kpts):
                if v <= 0:
                    continue
                colour = (0, 255, 0) if bright else (0, 150, 0)
                p = (int(round(x)), int(round(y)))
                cv2.circle(out, p, 4 if bright else 3, colour, -1)
                cv2.putText(out, str(k), (p[0] + 5, p[1] - 5), 0, 0.45, colour, 1)
        if self.scale != 1:
            out = cv2.resize(out, None, fx=self.scale, fy=self.scale)
        status = self.manifest["frames"][self.stem]["status"]
        if self.place is not None:
            hint = f"click keypoint {self.place[1]}: {KPT_NAMES[self.place[1]]} (Space skips)"
        elif self.replace_kp is not None:
            hint = f"click to re-place keypoint {self.replace_kp}"
        elif self.box_mode:
            hint = "drag the ring's box"
        else:
            hint = "a accept  e empty  x reject  g new ring  arrows move  q quit"
        text = f"{self.index + 1}/{len(self.stems)} {self.stem} [{status}]  {hint}"
        cv2.rectangle(out, (0, 0), (out.shape[1], 22), (0, 0, 0), -1)
        cv2.putText(out, text, (6, 16), 0, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", type=Path, help="folder written by real_label")
    parser.add_argument(
        "--status",
        default="auto,pending",
        help="which frames to show: comma-separated statuses, or all",
    )
    parser.add_argument("--scale", type=float, default=1.0, help="display zoom")
    args = parser.parse_args(argv)
    manifest = load_manifest(args.root)
    wanted = None if args.status == "all" else set(args.status.split(","))
    stems = [
        stem
        for stem, entry in sorted(manifest["frames"].items())
        if wanted is None or entry["status"] in wanted
    ]
    if not stems:
        raise SystemExit(f"No frames with status {args.status} in {args.root}")
    reviewer = Reviewer(args.root, stems, manifest, args.scale)
    window = "ring labels"
    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(window, reviewer.on_mouse)
    while True:
        cv2.imshow(window, reviewer.render())
        key = cv2.waitKeyEx(30)
        if key != -1 and not reviewer.key(key):
            break
        if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
            break
    save_manifest(args.root, manifest)
    cv2.destroyAllWindows()
    done = sum(
        e["status"] in ("accepted", "empty", "rejected")
        for e in manifest["frames"].values()
    )
    print(f"{done}/{len(manifest['frames'])} frames reviewed")


if __name__ == "__main__":
    main()
