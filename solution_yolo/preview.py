"""Draw labels back onto their images, to check a dataset by eye.

    python -m solution_yolo.preview solution_yolo/data/synth --out preview --count 40

Writes one PNG per image with the box, the nine keypoints (green visible,
yellow occluded, numbered) and, where ``meta`` exists, the true range.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution_yolo.keypoints import OCCLUDED, read_labels  # noqa: E402

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")


def draw(img: np.ndarray, gates, notes=None) -> np.ndarray:
    out = img.copy()
    for i, (_, box, kpts) in enumerate(gates):
        x0, y0, x1, y1 = (int(round(c)) for c in box)
        cv2.rectangle(out, (x0, y0), (x1, y1), (255, 160, 0), 1)
        for k, (x, y, v) in enumerate(kpts):
            if v <= 0:
                continue
            colour = (0, 220, 255) if v == OCCLUDED else (0, 255, 0)
            p = (int(round(x)), int(round(y)))
            cv2.circle(out, p, 3, colour, -1)
            cv2.putText(
                out, str(k), (p[0] + 4, p[1] - 4), 0, 0.35, colour, 1, cv2.LINE_AA
            )
        if notes and i < len(notes):
            cv2.putText(
                out, notes[i], (x0, max(12, y0 - 4)), 0, 0.45, (255, 255, 255), 1
            )
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", type=Path, help="folder with images/ and labels/")
    parser.add_argument("--out", type=Path, default=Path("preview"))
    parser.add_argument("--count", type=int, default=40)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    images = sorted(
        p for p in (args.root / "images").rglob("*") if p.suffix in IMAGE_SUFFIXES
    )[: args.count]
    for path in images:
        img = cv2.imread(str(path))
        h, w = img.shape[:2]
        rel = path.relative_to(args.root / "images").with_suffix(".txt")
        gates = read_labels(args.root / "labels" / rel, w, h)
        notes = None
        meta = (args.root / "meta" / rel).with_suffix(".json")
        if meta.exists():
            m = json.loads(meta.read_text())
            notes = [
                f"{g['forward_m']:.2f}m yaw {g['normal_yaw_deg']:.0f}"
                + ("" if g["front"] else " back")
                for g in m["gates"]
            ]
        cv2.imwrite(str(args.out / f"{path.stem}.png"), draw(img, gates, notes))
    print(f"Wrote {len(images)} previews to {args.out}")


if __name__ == "__main__":
    main()
