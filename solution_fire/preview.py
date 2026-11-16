"""Draw labels back onto their images, to check a dataset by eye.

    python -m solution_fire.preview solution_fire/data/real --out preview --status pending
    python -m solution_fire.preview solution_fire/data/synth --out preview --grid

Draws the box and the five keypoints (0 centre, 1 top, 2 right, 3 bottom,
4 left). ``--grid`` packs them into contact sheets of 16 instead of one PNG
each. Adapted from ``solution_yolo/preview.py``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution_fire.geometry import OCCLUDED, read_labels  # noqa: E402

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")
KPT_COLOURS = ((0, 255, 0), (255, 255, 0), (255, 0, 255), (0, 255, 255), (255, 128, 0))


def draw(img: np.ndarray, discs, note: str | None = None) -> np.ndarray:
    out = img.copy()
    for _, box, kpts in discs:
        x0, y0, x1, y1 = (int(round(c)) for c in box)
        cv2.rectangle(out, (x0, y0), (x1, y1), (255, 160, 0), 2)
        for k, (x, y, v) in enumerate(kpts):
            if v <= 0:
                continue
            p = (int(round(x)), int(round(y)))
            colour = (0, 220, 255) if v == OCCLUDED else KPT_COLOURS[k]
            cv2.circle(out, p, 4, colour, -1)
            cv2.putText(out, str(k), (p[0] + 5, p[1] - 5), 0, 0.5, colour, 1, cv2.LINE_AA)
    if note:
        cv2.rectangle(out, (0, 0), (out.shape[1], 24), (0, 0, 0), -1)
        cv2.putText(out, note, (6, 17), 0, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", type=Path, help="folder with images/ and labels/")
    parser.add_argument("--out", type=Path, default=Path("preview"))
    parser.add_argument("--count", type=int, default=48)
    parser.add_argument("--status", help="only frames with this review.json status")
    parser.add_argument("--random", action="store_true", help="a random sample, not the first")
    parser.add_argument("--grid", action="store_true", help="contact sheets of 16")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    images = sorted(p for p in (args.root / "images").rglob("*") if p.suffix in IMAGE_SUFFIXES)
    manifest = args.root / "review.json"
    statuses = json.loads(manifest.read_text())["frames"] if manifest.exists() else {}
    if args.status:
        images = [p for p in images if statuses.get(p.stem, {}).get("status") == args.status]
    if args.random:
        random.Random(0).shuffle(images)
    images = images[: args.count]
    tiles = []
    for path in images:
        img = cv2.imread(str(path))
        h, w = img.shape[:2]
        rel = path.relative_to(args.root / "images").with_suffix(".txt")
        discs = read_labels(args.root / "labels" / rel, w, h)
        entry = statuses.get(path.stem, {})
        note = path.stem
        if entry:
            note += f" [{entry.get('status')}]" + (f" iou {entry['iou']}" if "iou" in entry else "")
        meta = (args.root / "meta" / rel).with_suffix(".json")
        if meta.exists():
            m = json.loads(meta.read_text())
            if "distance_m" in m:
                note += f" {m['distance_m']:.2f} m"
        drawn = draw(img, discs, note)
        if args.grid:
            tiles.append(cv2.resize(drawn, (480, 360)))
        else:
            cv2.imwrite(str(args.out / f"{path.stem}.png"), drawn)
    if args.grid:
        blank = np.zeros((360, 480, 3), np.uint8)
        for n in range(0, len(tiles), 16):
            sheet = tiles[n : n + 16] + [blank] * (16 - len(tiles[n : n + 16]))
            rows = [np.hstack(sheet[r * 4 : r * 4 + 4]) for r in range(4)]
            cv2.imwrite(str(args.out / f"sheet_{n // 16:03d}.jpg"), np.vstack(rows))
    print(f"Wrote {len(images)} previews to {args.out}")


if __name__ == "__main__":
    main()
