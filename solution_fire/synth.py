"""Synthetic training frames: printed sheets pasted into real Tello footage.

    python -m solution_fire.synth --real solution_fire/data/real --out solution_fire/data/synth \
        --count 8000 --exclude-sessions "redcircle_*_s2" "redcircle_*_s4" ...

The real footage has one red disc, on one board, in one flat. This adds what
it lacks, with exact labels:

* the competition's **decoys**, which were not filmed: a green circle, a green
  triangle, a black square and a black triangle, filled or outlined;
* **hard negatives** that a colour mask confuses with a fire: red squares,
  red triangles, red ring outlines (the hollow ring *was* filmed; these vary
  its size and line width) and blank sheets;
* **red discs** in sizes, shades and poses the footage never reached: 0.45 to
  5 m away, up to 60 degrees off-axis.

Each sheet is a flat print on a plane in front of the Tello's calibrated
camera, sometimes with stand poles beside it, and is warped into a real
frame by that plane's homography. A disc's five keypoints go through the same
homography, so its labels are exact. Decoys are pasted unlabelled: the model
should stay silent on them.

Backgrounds are reviewed frames from ``autolabel``: negatives, and positives
whose real disc keeps its label (pasted sheets never cover it). Sessions held
out for validation and test are skipped with ``--exclude-sessions``, so no
held-out frame leaks into training as a background. ``meta/<stem>.json``
lists what was pasted, and ``evaluate`` reads it to count decoy false alarms.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import multiprocessing
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution_fire.autolabel import load_manifest  # noqa: E402
from solution_fire.geometry import (  # noqa: E402
    CameraCalibration,
    label_from_image_points,
    read_labels,
    write_labels,
)

POSITIVE = "red_disc"
# (kind, weight) for each pasted sheet. Positive weight is set per image.
NEGATIVES = (
    ("green_circle", 3.0),
    ("green_triangle", 3.0),
    ("black_square", 3.0),
    ("black_triangle", 3.0),
    # Filmed in the cage (2026-11-11): the old model called it a fire in 73%
    # of frames. Backlit, a red disc is nearly as dark, so this is the decoy
    # that teaches "dark is not red".
    ("black_circle", 3.0),
    ("green_ring", 1.0),
    ("red_ring", 3.0),
    ("red_square", 2.0),
    ("red_triangle", 2.0),
    ("blank", 1.0),
)
#: Share of sheets lit from behind (see ``composite``), and of sheets seen
#: through the cage's net. Both are the drone cage of 2026-11-11.
BACKLIT_P = 0.3
NET_P = 0.3
SHEETS_M = ((0.21, 0.297), (0.297, 0.21), (0.297, 0.42), (0.30, 0.30), (0.25, 0.25), (0.42, 0.42))


# --- the print --------------------------------------------------------------------------


def hsv_colour(rng, hue, sat, val) -> tuple:
    """A BGR colour from OpenCV HSV ranges (hue 0-180), sampled uniformly."""
    h = rng.uniform(*hue) % 180
    pixel = np.uint8([[[h, rng.uniform(*sat) * 255, rng.uniform(*val) * 255]]])
    return tuple(int(c) for c in cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0, 0])


def ink(kind: str, rng) -> tuple:
    if kind.startswith("red"):
        # Printer red as the Tello renders it: crimson through the orange-red
        # the filmed print comes out as (hue 168..12, wrapping), never yellow.
        return hsv_colour(rng, (168, 192), (0.6, 1.0), (0.45, 0.95))
    if kind.startswith("green"):
        return hsv_colour(rng, (40, 85), (0.35, 1.0), (0.3, 0.85))
    if kind.startswith("black"):
        v = rng.uniform(0.04, 0.22)
        if rng.random() < 0.5:
            return hsv_colour(rng, (0, 180), (0, 0.3), (v, v))
        # The Tello renders black print warm: (0, 30, 35) in the cage, H 26,
        # S 255: green is 0.7-1x its red (H 20-32), which is what still tells it
        # from a backlit red disc (0, 4, 28) at the same brightness.
        return hsv_colour(rng, (20, 32), (0.4, 1.0), (v, v))
    raise ValueError(kind)


def draw_shape(canvas, kind: str, centre, size_px: float, colour, rng) -> None:
    """Draw one print shape, centred, ``size_px`` across."""
    cx, cy = centre
    r = size_px / 2
    shape = kind.split("_", 1)[1] if "_" in kind else kind
    filled = not shape.endswith("ring") and rng.random() > (0.0 if kind == POSITIVE else 0.25)
    thick = -1 if filled else max(2, int(round(r * rng.uniform(0.06, 0.22))))
    aa = cv2.LINE_AA
    if shape in ("disc", "circle", "ring"):
        cv2.circle(canvas, (int(round(cx * 16)), int(round(cy * 16))),
                   int(round((r if thick < 0 else r - thick / 2) * 16)), colour, thick, aa, 4)
    elif shape == "square":
        s = r * rng.uniform(0.8, 1.0)
        pts = np.array([[cx - s, cy - s], [cx + s, cy - s], [cx + s, cy + s], [cx - s, cy + s]])
        _poly(canvas, pts, colour, thick)
    elif shape == "triangle":
        a = np.radians([-90, 30, 150]) + (math.pi if rng.random() < 0.15 else 0)
        pts = np.column_stack((cx + r * np.cos(a), cy + r * np.sin(a) + r * 0.15))
        _poly(canvas, pts, colour, thick)


def _poly(canvas, pts, colour, thick) -> None:
    p = np.round(pts * 16).astype(np.int32).reshape(-1, 1, 2)
    if thick < 0:
        cv2.fillPoly(canvas, [p], colour, cv2.LINE_AA, 4)
    else:
        cv2.polylines(canvas, [p], True, colour, thick, cv2.LINE_AA, 4)


def render_board(kind: str, ppm: float, rng):
    """The sheet, and maybe its stand, as a BGR + alpha canvas.

    Returns (bgr, alpha, to_board, disc) where ``to_board`` maps canvas pixels
    to board metres (x right, y up, origin at the sheet's centre) and ``disc``
    is (radius_m) for the positive, else None. The shape sits at the sheet's
    centre so the disc centre is the board origin."""
    sw, sh = SHEETS_M[rng.integers(len(SHEETS_M))]
    sw *= rng.uniform(0.9, 1.1)
    sh *= rng.uniform(0.9, 1.1)
    poles = rng.random() < 0.45
    pole_w, pole_len = 0.022, rng.uniform(0.4, 1.4)
    margin_x = 0.06 if poles else 0.0
    left, right = -sw / 2 - margin_x, sw / 2 + margin_x
    top, bottom = sh / 2 + (0.05 if poles else 0.0), -sh / 2 - (pole_len if poles else 0.0)
    wpx = max(8, int(math.ceil((right - left) * ppm)))
    hpx = max(8, int(math.ceil((top - bottom) * ppm)))
    bgr = np.zeros((hpx, wpx, 3), np.uint8)
    alpha = np.zeros((hpx, wpx), np.uint8)

    def px(x, y):
        return ((x - left) * ppm, (top - y) * ppm)

    if poles:
        colour = hsv_colour(rng, (172, 186), (0.6, 1), (0.5, 0.9)) if rng.random() < 0.7 else (
            hsv_colour(rng, (0, 180), (0, 0.15), (0.6, 0.95)))
        for x in (left + pole_w, right - pole_w):
            a, b = px(x, top - 0.01), px(x, bottom)
            for img, c in ((bgr, colour), (alpha, 255)):
                cv2.line(img, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), c,
                         max(2, int(pole_w * ppm)))
        a, b = px(left + pole_w, top - 0.02), px(right - pole_w, top - 0.02)
        for img, c in ((bgr, colour), (alpha, 255)):
            cv2.line(img, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), c,
                     max(2, int(pole_w * ppm)))
    paper = hsv_colour(rng, (0, 180), (0.0, 0.08), (0.8, 1.0))
    x0, y0 = px(-sw / 2, sh / 2)
    x1, y1 = px(sw / 2, -sh / 2)
    cv2.rectangle(bgr, (int(x0), int(y0)), (int(x1), int(y1)), paper, -1)
    cv2.rectangle(alpha, (int(x0), int(y0)), (int(x1), int(y1)), 255, -1)
    disc = None
    if kind != "blank":
        size_m = min(sw, sh) * rng.uniform(0.55, 0.88)
        cx, cy = px(0.0, 0.0)
        draw_shape(bgr, kind, (cx, cy), size_m * ppm, ink(kind, rng), rng)
        if kind == POSITIVE:
            disc = size_m / 2
    to_board = np.array([[1 / ppm, 0, left], [0, -1 / ppm, top], [0, 0, 1]], np.float64)
    return bgr, alpha, to_board, disc


# --- the camera -------------------------------------------------------------------------


def rotation(yaw_deg, pitch_deg, roll_deg) -> np.ndarray:
    """Board -> camera rotation for a board facing the camera, then turned.
    Board: x right, y up, z towards the viewer. Camera: x right, y down, z
    forward."""
    y, p, r = np.radians([yaw_deg, pitch_deg, roll_deg])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    facing = np.diag([1.0, -1.0, -1.0])
    return ry @ rx @ rz @ facing


def board_homography(matrix, rot, t) -> np.ndarray:
    """Board metres (x, y, 1) -> image pixels."""
    return matrix @ np.column_stack((rot[:, 0], rot[:, 1], t))


def sample_pose(rng, matrix, w, h, dist_range):
    d = math.exp(rng.uniform(math.log(dist_range[0]), math.log(dist_range[1])))
    u = rng.uniform(-0.08 * w, 1.08 * w)
    v = rng.uniform(0.05 * h, 0.95 * h)
    ray = np.linalg.solve(matrix, np.array([u, v, 1.0]))
    t = d * ray / np.linalg.norm(ray)
    rot = rotation(rng.uniform(-60, 60), rng.uniform(-12, 12), rng.uniform(-6, 6))
    return d, rot, t


# --- one image --------------------------------------------------------------------------


def _overlaps(a, b, pad=4.0) -> bool:
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


def paste(frame, kind, rng, matrix, occupied, dist_range):
    """Paste one sheet; (label or None, meta) or None if no clear spot found."""
    h, w = frame.shape[:2]
    for _ in range(12):
        d, rot, t = sample_pose(rng, matrix, w, h, dist_range)
        ppm = float(np.clip(matrix[0, 0] / d * 1.3, 200, 2400))
        bgr, alpha, to_board, disc = render_board(kind, ppm, rng)
        hom = board_homography(matrix, rot, t) @ to_board
        ch, cw = alpha.shape
        corners = cv2.perspectiveTransform(
            np.array([[[0, 0]], [[cw, 0]], [[cw, ch]], [[0, ch]]], np.float64), hom
        ).reshape(-1, 2)
        if not np.isfinite(corners).all():
            continue
        extent = (*corners.min(axis=0), *corners.max(axis=0))
        if extent[2] < 0 or extent[0] > w or extent[3] < 0 or extent[1] > h:
            continue
        if any(_overlaps(extent, o) for o in occupied):
            continue
        label = None
        if disc is not None:
            board_hom = board_homography(matrix, rot, t)
            pts = np.array([[0, 0], [0, disc], [disc, 0], [0, -disc], [-disc, 0]], np.float64)
            a = np.linspace(0, 2 * math.pi, 64, endpoint=False)
            rim = np.column_stack((disc * np.cos(a), disc * np.sin(a)))
            kp = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), board_hom).reshape(-1, 2)
            rim_px = cv2.perspectiveTransform(rim.reshape(-1, 1, 2), board_hom).reshape(-1, 2)
            label = label_from_image_points(kp, rim_px, w, h, min_inside=0.4)
            if label is None:
                continue
        warped = cv2.warpPerspective(bgr, hom, (w, h), flags=cv2.INTER_LINEAR)
        mask = cv2.warpPerspective(alpha, hom, (w, h), flags=cv2.INTER_LINEAR)
        composite(frame, warped, mask.astype(np.float32) / 255, rng)
        occupied.append(extent)
        yaw = math.degrees(math.atan2(-rot[0, 2], -rot[2, 2]))
        return label, {"kind": kind, "distance_m": round(d, 3), "yaw_deg": round(yaw, 1),
                       "extent": [round(float(v), 1) for v in extent]}
    return None


def composite(frame, sheet, alpha, rng) -> None:
    """Light the print like the room it lands in, then blend it in."""
    region = alpha > 0.5
    if not region.any():
        return
    sheet = sheet.astype(np.float32)
    ys, xs = np.nonzero(region)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    pad = 40
    around = frame[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad].reshape(-1, 3)
    ambient = around.mean(axis=0).astype(np.float32) + 1
    tint = ambient / ambient.mean()
    if rng.random() < BACKLIT_P:
        # Backlit: the sheet hangs in front of a bright window and the
        # Tello's auto-exposure meters for the window, so the print comes
        # out a quarter of its brightness whatever the room around it. Every
        # kind gets this, decoys too, or "dark" would come to mean "red".
        gain = rng.uniform(0.12, 0.45)
    else:
        gain = rng.uniform(0.55, 1.1) * (0.6 + 0.4 * ambient.mean() / 160)
    sheet *= gain * (1 + rng.uniform(0.2, 0.6) * (tint - 1))
    # Light falls off across the sheet a little.
    gy, gx = np.mgrid[0:frame.shape[0], 0:frame.shape[1]].astype(np.float32)
    g = rng.normal(0, 0.15, 2)
    span = max(x1 - x0, y1 - y0, 1)
    sheet *= (1 + g[0] * (gx - xs.mean()) / span + g[1] * (gy - ys.mean()) / span)[..., None]
    sigma = rng.uniform(0.3, 1.3)
    sheet = cv2.GaussianBlur(sheet, (0, 0), sigma)
    a = cv2.GaussianBlur(alpha, (0, 0), max(0.5, sigma))[..., None]
    out = frame.astype(np.float32) * (1 - a) + sheet * a
    if rng.random() < NET_P:
        out = netting(out, a[..., 0], rng)
    frame[:] = np.clip(out, 0, 255).astype(np.uint8)


def netting(image, where, rng):
    """The drone cage's net between the camera and the sheet: a dark,
    slightly rotated square mesh drawn over the print (the background frame
    already has its own net, or none). In the cage footage it crosses every
    disc, breaking its outline into cells."""
    h, w = image.shape[:2]
    pitch = rng.uniform(12, 40)
    width = int(rng.integers(1, 4))
    angle = rng.uniform(-0.35, 0.35)
    mesh = np.zeros((h, w), np.uint8)
    reach = int(math.hypot(w, h))
    c, s = math.cos(angle), math.sin(angle)
    ox, oy = rng.uniform(0, pitch, 2)
    for k in np.arange(-reach, reach, pitch):
        for (dx, dy), (px, py) in (((c, s), (-s, c)), ((-s, c), (c, s))):
            x0, y0 = w / 2 + (k + ox) * px - reach * dx, h / 2 + (k + oy) * py - reach * dy
            x1, y1 = w / 2 + (k + ox) * px + reach * dx, h / 2 + (k + oy) * py + reach * dy
            cv2.line(mesh, (int(x0), int(y0)), (int(x1), int(y1)), 255, width, cv2.LINE_AA)
    # The net is out of focus and the Tello moves: soft strands, not ink.
    mesh = cv2.GaussianBlur(mesh, (0, 0), rng.uniform(0.6, 1.6))
    strength = rng.uniform(0.4, 0.85)
    m = (mesh.astype(np.float32) / 255) * strength * where
    shade = np.float32(rng.uniform(10, 45))
    return image * (1 - m[..., None]) + shade * m[..., None]


def camera_effects(frame, rng) -> np.ndarray:
    if rng.random() < 0.25:
        k = int(rng.integers(3, 12))
        kernel = np.zeros((k, k), np.float32)
        angle = rng.uniform(0, math.pi)
        c = (k - 1) / 2
        dx, dy = math.cos(angle) * c, math.sin(angle) * c
        cv2.line(kernel, (int(round(c - dx)), int(round(c - dy))),
                 (int(round(c + dx)), int(round(c + dy))), 1.0, 1)
        frame = cv2.filter2D(frame, -1, kernel / max(kernel.sum(), 1e-6))
    if rng.random() < 0.6:
        frame = np.clip(frame + rng.normal(0, rng.uniform(1, 5), frame.shape), 0, 255).astype(
            np.uint8)
    quality = int(rng.integers(40, 96))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else frame


# --- the job ----------------------------------------------------------------------------


def make_one(job) -> dict:
    i, seed, bg_path, bg_label, out, calib, positive_p, dist_range, decoy_only = job
    rng = np.random.default_rng(seed)
    frame = cv2.imread(bg_path)
    h, w = frame.shape[:2]
    matrix = calib.matrix(w, h)
    labels, occupied = [], []
    for _, box, k in read_labels(bg_label, w, h) if bg_label else []:
        labels.append((box, k))
        occupied.append(box)
    kinds_w = np.array([wt for _, wt in NEGATIVES])
    n = int(rng.choice([1, 2, 3], p=[0.5, 0.35, 0.15]))
    pasted = []
    want_positive = not decoy_only and rng.random() < positive_p
    for j in range(n):
        if want_positive and j == 0:
            kind = POSITIVE
        else:
            kind = NEGATIVES[rng.choice(len(NEGATIVES), p=kinds_w / kinds_w.sum())][0]
        result = paste(frame, kind, rng, matrix, occupied, dist_range)
        if result is None:
            continue
        label, meta = result
        if label is not None:
            labels.append(label)
        pasted.append(meta)
    frame = camera_effects(frame, rng)
    stem = f"synth_{i:06d}"
    cv2.imwrite(str(out / "images" / f"{stem}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    write_labels(out / "labels" / f"{stem}.txt", labels, w, h)
    meta = {"background": bg_path, "pasted": pasted, "decoy_only": decoy_only,
            "real_discs": sum(1 for _ in read_labels(bg_label, w, h)) if bg_label else 0}
    (out / "meta" / f"{stem}.json").write_text(json.dumps(meta))
    return {"positives": sum(m["kind"] == POSITIVE for m in pasted), "pasted": len(pasted)}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--real", type=Path, required=True,
                        help="autolabel folder whose reviewed frames are the backgrounds")
    parser.add_argument("--out", type=Path, default=Path("solution_fire/data/synth"))
    parser.add_argument("--count", type=int, default=8000)
    parser.add_argument("--exclude-sessions", nargs="+", default=[], metavar="PATTERN",
                        help="sessions held out for val/test: never used as backgrounds")
    parser.add_argument("--positive-p", type=float, default=0.4,
                        help="share of images that get a pasted red disc")
    parser.add_argument("--decoy-only-p", type=float, default=0.1,
                        help="share of images on a negative background with only decoys "
                        "(evaluate counts false alarms on these)")
    parser.add_argument("--min-dist", type=float, default=0.45)
    parser.add_argument("--max-dist", type=float, default=5.0)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--workers", type=int,
                        default=max(1, (multiprocessing.cpu_count() or 2) - 1))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    calib = CameraCalibration.load(args.calibration) if args.calibration else CameraCalibration()
    frames = load_manifest(args.real)["frames"]
    negatives, positives = [], []
    for stem, e in sorted(frames.items()):
        if any(fnmatch.fnmatch(e["session"], p) for p in args.exclude_sessions):
            continue
        image = str(args.real / "images" / f"{stem}.jpg")
        if e["status"] == "empty":
            negatives.append((image, None))
        elif e["status"] == "accepted":
            positives.append((image, str(args.real / "labels" / f"{stem}.txt")))
    if not negatives:
        raise SystemExit("No reviewed negative frames to use as backgrounds")
    print(f"Backgrounds: {len(negatives)} negative, {len(positives)} positive frames")
    for sub in ("images", "labels", "meta"):
        (args.out / sub).mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    jobs = []
    for i in range(args.count):
        decoy_only = rng.random() < args.decoy_only_p
        pool = negatives if decoy_only or not positives or rng.random() < 0.7 else positives
        bg, label = pool[rng.integers(len(pool))]
        jobs.append((i, int(rng.integers(2**31)), bg, label, args.out, calib, args.positive_p,
                     (args.min_dist, args.max_dist), decoy_only))
    done = {"positives": 0, "pasted": 0}
    with multiprocessing.Pool(args.workers) as pool:
        for n, r in enumerate(pool.imap_unordered(make_one, jobs, chunksize=16), 1):
            for k in done:
                done[k] += r[k]
            if n % 1000 == 0:
                print(f"{n}/{args.count} images")
    print(f"Wrote {args.count} images to {args.out}: {done['pasted']} sheets pasted, "
          f"{done['positives']} of them red discs")


if __name__ == "__main__":
    main()
