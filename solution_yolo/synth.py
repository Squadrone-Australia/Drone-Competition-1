"""Render labelled training frames from the simulator.

    python -m solution_yolo.synth --count 30000 --out solution_yolo/data/synth

Every frame comes with exact labels: the renderer's own camera projects the
nine ring keypoints, so there is nothing to annotate and no label noise. Each
sample also records each gate's true camera-frame offset and normal in
``meta/*.json``, which ``evaluate`` scores the detector's range against.

The variety is what makes a sim-trained model survive a real camera:

* every venue dressing the simulator has (floor, netting, hall, crowd,
  external light, camera artefacts, gate finish), drawn independently;
* the three official tracks, plus layouts with the gates moved, raised,
  lowered and turned, so the model learns a ring rather than three positions;
* views weighted to what flying needs: approach cones in front of a gate, the
  close, wall-limited standoffs where the ring overflows the frame, the back
  face, and random views anywhere (often with no gate at all, as negatives);
* a small camera roll and pitch, which the simulator's level camera lacks.
* the red PVC rig of the physical gates (posts through the opening, a
  T-bar inside the ring), painted over a third of the frames' nearest gate.

Floor reflections are drawn but never labelled, so the model learns that a
ring mirrored in varnish is not a gate.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import random
import sys
from dataclasses import replace
from functools import cache
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):  # python solution_yolo/synth.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comp2.sim import arena, track  # noqa: E402
from comp2.sim.imaging import ImagingModel  # noqa: E402
from comp2.sim.render import _frame_point, build_scenery, render  # noqa: E402
from comp2.sim.scene import NEAR, Camera  # noqa: E402
from comp2.vision.camera import SIM_INTRINSICS  # noqa: E402

from solution_yolo.keypoints import (  # noqa: E402
    CORNERS,
    FLIP_IDX,
    NUM_KPTS,
    OCCLUDED,
    VISIBLE,
    circle_points,
    format_label,
    object_points,
)

# Keep the camera this far inside the cage's netting and above the floor.
MARGIN_M = 0.15
MIN_Z_M, MAX_Z_M = 0.25, 2.8
# A gate smaller than this in the frame is not worth labelling: the model
# cannot see a corner on it and would only learn to guess.
MIN_BOX_PX = 10
# Less of the ring's box than this in shot, and the label is mostly a guess.
MIN_VISIBLE_FRACTION = 0.12
# Views where the band is closer to edge-on than this (|cos| of the angle
# between the view ray and the normal) are left unlabelled: no corners show.
MIN_FACING = 0.2
# How a view is chosen; see sample_view.
VIEW_MIX = (("approach", 0.55), ("close", 0.15), ("back", 0.08), ("random", 0.22))


@cache
def _track(name: str):
    return track.build(name)


def track_names() -> list[str]:
    return track.names()


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def random_appearance(rng: random.Random) -> arena.Appearance:
    """Any combination of the arena libraries, the clean room included now and
    then so the model also sees a ring with nothing around it."""
    if rng.random() < 0.04:
        return arena.CLEAN
    if rng.random() < 0.12:
        return replace(arena.CAGE, seed=rng.randrange(1 << 30))
    lib = arena.LIBRARIES
    return arena.compose(
        name="synth",
        floor=rng.choice(list(lib["floor"])),
        net=rng.choice(list(lib["net"])),
        hall=rng.choice(list(lib["hall"])),
        crowd=rng.choice(list(lib["crowd"])),
        light=rng.choice(list(lib["light"])),
        imaging=rng.choice(["ideal", "tello", "tello", "harsh"]),
        gate=rng.choice(list(lib["gate"])),
        light_intensity=round(rng.uniform(0.5, 1.5), 2),
        seed=rng.randrange(1 << 30),
    )


def random_world(rng: random.Random, tracks: list[str]):
    """An official track, or one with its gates moved, raised and turned."""
    base = _track(rng.choice(tracks))
    world = replace(base, gates=list(base.gates))
    if rng.random() < 0.5:
        return world
    gates = []
    for g in base.gates:
        r = g.radius_m
        x = min(max(g.x + rng.uniform(-0.6, 0.6), r + 0.2), world.width_m - r - 0.2)
        y = min(max(g.y + rng.uniform(-0.6, 0.6), r + 0.2), world.depth_m - r - 0.2)
        # The stand needs the ring at least a mast and a radius off the floor.
        z = rng.uniform(r + 0.3, min(2.4, world.height_m - r - 0.1))
        yaw = rng.uniform(0, 360) if rng.random() < 0.5 else g.yaw_deg
        gates.append(replace(g, x=x, y=y, z=z, yaw_deg=yaw))
    world.gates = gates
    return world


def _clamp_eye(world, x, y, z):
    return (
        min(max(x, MARGIN_M), world.width_m - MARGIN_M),
        min(max(y, MARGIN_M), world.depth_m - MARGIN_M),
        min(max(z, MIN_Z_M), MAX_Z_M),
    )


def sample_view(world, rng: random.Random, mode: str):
    """A camera pose (x, y, z, heading) for one of the VIEW_MIX modes."""
    if mode == "random" or not world.gates:
        x = rng.uniform(MARGIN_M, world.width_m - MARGIN_M)
        y = rng.uniform(MARGIN_M, world.depth_m - MARGIN_M)
        z = rng.uniform(0.4, 2.4)
        return x, y, z, rng.uniform(0, 360)
    g = rng.choice(world.gates)
    nx, ny = g.normal()
    lx, ly = math.cos(math.radians(g.yaw_deg)), -math.sin(math.radians(g.yaw_deg))
    if mode == "close":
        # The wall-limited standoffs at G1 and G3: under a metre, the ring
        # overflowing the frame, the camera drifted off the axis.
        dist = rng.uniform(0.3, 1.0)
        lateral = rng.gauss(0, 0.2)
        height = rng.gauss(0, 0.2)
        aim_jitter = 18.0
    else:
        # Approach cones: most samples between half a metre and three, where
        # the controller measures and aligns.
        dist = min(3.6, max(0.35, rng.lognormvariate(math.log(1.4), 0.5)))
        spread = math.tan(math.radians(rng.uniform(0, 40)))
        lateral = rng.choice((-1, 1)) * dist * spread
        height = rng.gauss(0, 0.3)
        aim_jitter = 14.0
    side = 1.0 if mode == "back" else -1.0  # approach side is against the normal
    x = g.x + side * nx * dist + lx * lateral
    y = g.y + side * ny * dist + ly * lateral
    x, y, z = _clamp_eye(world, x, y, g.z + height)
    heading = math.degrees(math.atan2(g.x - x, g.y - y))
    heading += rng.gauss(0, aim_jitter)
    return x, y, z, heading % 360


def _world_points(g, pts: np.ndarray, front: bool) -> np.ndarray:
    """Object-frame points (x right as seen, y down) on gate g, in the world."""
    sign = 1.0 if front else -1.0
    return np.array([_frame_point(g, sign * px, -py) for px, py, _ in pts])


def _inside(polygon: np.ndarray, point) -> bool:
    return (
        cv2.pointPolygonTest(
            polygon.astype(np.float32), tuple(map(float, point)), False
        )
        > 0
    )


def label_gates(world, cam: Camera) -> list[dict]:
    """Box, keypoints and ground truth for every gate worth labelling."""
    w, h = cam.w, cam.h
    model = object_points(world.gates[0].diameter_m, world.gates[0].opening_diameter_m)
    projected = []
    for index, g in enumerate(world.gates):
        nx, ny = g.normal()
        to_gate = np.array([g.x - cam.x, g.y - cam.y, g.z - cam.z])
        dist = float(np.linalg.norm(to_gate))
        if dist < 1e-6:
            continue
        # Seen from the approach side when the camera stands behind the plane
        # (against the normal); the keypoints are then labelled as seen.
        front = (cam.x - g.x) * nx + (cam.y - g.y) * ny < 0
        facing = abs(to_gate[0] * nx + to_gate[1] * ny) / dist
        outer = cam.to_camera(_world_points(g, circle_points(g.radius_m), front))
        inner = cam.to_camera(
            _world_points(g, circle_points(g.opening_radius_m), front)
        )
        if (outer[:, 2] < NEAR).any():
            continue  # flying through it: no ring to see
        kp_cam = cam.to_camera(_world_points(g, model, front))
        centre = kp_cam[-1]
        projected.append(
            {
                "index": index,
                "gate": g,
                "front": bool(front),
                "facing": float(facing),
                "depth": float(centre[2]),
                "outer": cam.project(outer),
                "inner": cam.project(inner),
                "kp_cam": kp_cam,
                "kp": cam.project(np.maximum(kp_cam, [-1e9, -1e9, NEAR])),
            }
        )
    out = []
    for p in projected:
        if p["facing"] < MIN_FACING:
            continue
        outer = p["outer"]
        x0, y0 = outer.min(axis=0)
        x1, y1 = outer.max(axis=0)
        full = max(1e-6, (x1 - x0) * (y1 - y0))
        cx0, cy0 = max(0.0, x0), max(0.0, y0)
        cx1, cy1 = min(float(w), x1), min(float(h), y1)
        if cx1 - cx0 < MIN_BOX_PX or cy1 - cy0 < MIN_BOX_PX:
            continue
        if (cx1 - cx0) * (cy1 - cy0) / full < MIN_VISIBLE_FRACTION:
            continue
        nearer = [o for o in projected if o is not p and o["depth"] < p["depth"]]
        kpts = np.zeros((NUM_KPTS, 3))
        for k, (u, v) in enumerate(p["kp"]):
            if p["kp_cam"][k, 2] < NEAR or not (0 <= u < w and 0 <= v < h):
                continue  # NOT_LABELLED
            hidden = any(
                _inside(o["outer"], (u, v)) and not _inside(o["inner"], (u, v))
                for o in nearer
            )
            kpts[k] = (u, v, OCCLUDED if hidden else VISIBLE)
        g = p["gate"]
        right, up, forward = (float(c) for c in p["kp_cam"][-1])
        normal_heading = g.yaw_deg if p["front"] else g.yaw_deg + 180
        out.append(
            {
                "box": (cx0, cy0, cx1, cy1),
                "keypoints": kpts,
                "gate_index": p["index"],
                "front": p["front"],
                "right_m": right,
                "up_m": up,
                "forward_m": forward,
                "normal_yaw_deg": _wrap_deg(normal_heading - cam.heading),
                "corners_in_shot": int((kpts[:CORNERS, 2] > 0).sum()),
                "clipped": bool(x0 < 0 or y0 < 0 or x1 > w or y1 > h),
            }
        )
    return out


def tilt(img: np.ndarray, labels: list[dict], roll_deg: float, pitch_px: float):
    """Roll the frame about its centre and shift it vertically, as a Tello
    banking or nosing down would, and move the labels with it.

    The simulator's camera is always level; a real one is not. Small angles
    only: the borders are reflected in, and the ground truth offsets are left
    in the level camera's frame (evaluate skips tilted samples for range)."""
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), roll_deg, 1.0)
    m[1, 2] += pitch_px
    out = cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REFLECT_101)
    moved = []
    for lab in labels:
        k = lab["keypoints"].copy()
        known = k[:, 2] > 0
        if known.any():
            k[known, :2] = k[known, :2] @ m[:, :2].T + m[:, 2]
            gone = known & ~(
                (k[:, 0] >= 0) & (k[:, 0] < w) & (k[:, 1] >= 0) & (k[:, 1] < h)
            )
            k[gone] = 0
        x0, y0, x1, y1 = lab["box"]
        box_pts = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) @ m[:, :2].T
        box_pts += m[:, 2]
        bx0, by0 = np.clip(box_pts.min(axis=0), 0, (w, h))
        bx1, by1 = np.clip(box_pts.max(axis=0), 0, (w, h))
        if bx1 - bx0 < MIN_BOX_PX or by1 - by0 < MIN_BOX_PX:
            continue
        moved.append({**lab, "keypoints": k, "box": (bx0, by0, bx1, by1)})
    return out, moved


def mirror(img: np.ndarray, labels: list[dict]):
    """Horizontal mirror with the keypoint identities swapped, as YOLO's own
    fliplr does; kept here so the saved set is already balanced."""
    w = img.shape[1]
    out = []
    for lab in labels:
        k = lab["keypoints"][list(FLIP_IDX)].copy()
        known = k[:, 2] > 0
        k[known, 0] = w - k[known, 0]
        x0, y0, x1, y1 = lab["box"]
        out.append(
            {
                **lab,
                "keypoints": k,
                "box": (w - x1, y0, w - x0, y1),
                "right_m": -lab["right_m"],
                "normal_yaw_deg": -lab["normal_yaw_deg"],
            }
        )
    return np.ascontiguousarray(img[:, ::-1]), out


def draw_red_rig(img: np.ndarray, cam: Camera, g, rng: random.Random) -> None:
    """Paint a red PVC rig through the ring, like the one in the real frames.

    The physical rig differs from the simulator's grey stand: two red posts
    run from the floor up through the opening to the top segment, and a
    crossbar with a T down to the bottom segment sits inside the ring. In a
    colour mask that merges with the fabric, so the model has to see it in
    training. Drawn over the frame (in front of the bottom segment, as on the
    rig), and only on the nearest gate, so nothing nearer should cover it.
    """
    r_in = g.opening_radius_m
    post_u = rng.uniform(0.12, 0.19)
    tube = rng.uniform(0.02, 0.032)
    red = (rng.randint(15, 60), rng.randint(15, 55), rng.randint(150, 230))
    white = tuple([rng.randint(195, 240)] * 3)
    bar_v = rng.uniform(-0.12, 0.0)
    top_v = math.sqrt(max(0.0, r_in**2 - post_u**2))
    bar_colour = white if rng.random() < 0.6 else red
    segments = [
        ((-post_u, -g.z), (-post_u, top_v), red),
        ((post_u, -g.z), (post_u, top_v), red),
        ((-post_u, bar_v), (post_u, bar_v), bar_colour),
        ((0.0, bar_v), (0.0, -r_in), white),
    ]
    for (u0, v0), (u1, v1), colour in segments:
        ends = cam.to_camera([_frame_point(g, u0, v0), _frame_point(g, u1, v1)])
        if (ends[:, 2] < NEAR).any():
            continue
        (x0, y0), (x1, y1) = cam.project(ends)
        if not all(map(math.isfinite, (x0, y0, x1, y1))):
            continue
        width = max(1, round(cam.focal * tube / float(ends[:, 2].mean())))
        shade = tuple(int(c * 0.7) for c in colour)
        p0, p1 = (round(x0), round(y0)), (round(x1), round(y1))
        cv2.line(img, p0, p1, shade, width, cv2.LINE_AA)
        if width >= 3:  # a lit stripe down a round tube
            cv2.line(img, p0, p1, colour, max(1, width // 2), cv2.LINE_AA)


# Share of frames whose nearest gate gets the red PVC rig.
RIG_CHANCE = 0.35


def make_sample(seed: int, sizes, tracks):
    """One rendered, labelled frame: (image, labels, meta)."""
    rng = random.Random(seed)
    world = random_world(rng, tracks)
    app = random_appearance(rng)
    world.appearance = app
    world.scenery = build_scenery(app, world, rng.randrange(1 << 30))
    mode = rng.choices([m for m, _ in VIEW_MIX], [p for _, p in VIEW_MIX])[0]
    w, h = rng.choice(sizes)
    x, y, z, heading = sample_view(world, rng, mode)
    img = render(world, x, y, z, heading, w, h)
    cam = Camera.at(SIM_INTRINSICS, x, y, z, heading, w, h)
    labels = label_gates(world, cam)
    # A separate stream, so adding the rig left every other draw unchanged.
    rig_rng = random.Random(seed ^ 0x5EED5)
    rig = None
    if labels and rig_rng.random() < RIG_CHANCE:
        nearest = min(labels, key=lambda lab: lab["forward_m"])
        draw_red_rig(img, cam, world.gates[nearest["gate_index"]], rig_rng)
        rig = nearest["gate_index"]
    imaging = ImagingModel(app.imaging, rng.randrange(1 << 30))
    imaging.advance(rng.uniform(0, 60))
    motion = (rng.uniform(-6, 6), rng.uniform(-3, 3)) if rng.random() < 0.5 else (0, 0)
    img = imaging.apply(img, motion_px=motion)
    roll = pitch = 0.0
    if rng.random() < 0.6:
        roll = rng.uniform(-4, 4)
        pitch = rng.uniform(-3, 3)
        focal = SIM_INTRINSICS.focal_px(w)
        img, labels = tilt(img, labels, roll, focal * math.tan(math.radians(pitch)))
    mirrored = rng.random() < 0.5
    if mirrored:
        img, labels = mirror(img, labels)
    meta = {
        "seed": seed,
        "mode": mode,
        "size": [w, h],
        "pose": {"x": x, "y": y, "z": z, "heading": heading},
        "track": world.name,
        "arena": app.name,
        "tilt": {"roll_deg": roll, "pitch_deg": pitch},
        "mirrored": mirrored,
        "red_rig_gate": rig,
        "gates": [
            {k: v for k, v in lab.items() if k not in ("keypoints",)}
            | {"box": [float(c) for c in lab["box"]]}
            for lab in labels
        ],
    }
    return img, labels, meta


def _write(index: int, seed: int, out: Path, sizes, tracks, quality: int) -> int:
    img, labels, meta = make_sample(seed, sizes, tracks)
    h, w = img.shape[:2]
    stem = f"synth_{index:06d}"
    cv2.imwrite(
        str(out / "images" / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    (out / "labels" / f"{stem}.txt").write_text(
        "".join(
            format_label(lab["box"], lab["keypoints"], w, h) + "\n" for lab in labels
        )
    )
    (out / "meta" / f"{stem}.json").write_text(json.dumps(meta))
    return len(labels)


def _work(job):
    return _write(*job)


def parse_sizes(text: str) -> list[tuple[int, int]]:
    sizes = []
    for part in text.split(","):
        w, h = part.lower().split("x")
        if int(w) > 800 or int(h) > 600:
            # The simulator's sensor-noise bank is 800x600.
            raise SystemExit(f"--sizes {part}: frames above 800x600 are not supported")
        sizes.append((int(w), int(h)))
    return sizes


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--count", type=int, default=30000)
    parser.add_argument("--out", type=Path, default=Path("solution_yolo/data/synth"))
    parser.add_argument("--seed", type=int, default=0, help="first sample's seed")
    parser.add_argument("--start", type=int, default=0, help="first file index")
    parser.add_argument(
        "--sizes",
        default="640x480",
        help="comma-separated WxH (4:3, at most 800x600), one picked per frame; "
        "YOLO resizes to --imgsz anyway, so the Tello's 960x720 is not needed",
    )
    parser.add_argument(
        "--tracks", default="all", help="comma-separated track ids, or all"
    )
    parser.add_argument(
        "--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1)
    )
    parser.add_argument("--quality", type=int, default=92, help="JPEG quality")
    args = parser.parse_args(argv)
    tracks = track_names() if args.tracks == "all" else args.tracks.split(",")
    sizes = parse_sizes(args.sizes)
    for sub in ("images", "labels", "meta"):
        (args.out / sub).mkdir(parents=True, exist_ok=True)
    jobs = [
        (
            args.start + i,
            args.seed + args.start + i,
            args.out,
            sizes,
            tracks,
            args.quality,
        )
        for i in range(args.count)
    ]
    labelled = empty = 0
    if args.workers <= 1:
        results = map(_work, jobs)
    else:
        pool = multiprocessing.Pool(args.workers)
        results = pool.imap_unordered(_work, jobs, chunksize=16)
    for done, n in enumerate(results, 1):
        labelled += n
        empty += n == 0
        if done % 500 == 0 or done == len(jobs):
            print(f"{done}/{len(jobs)} frames, {labelled} gates, {empty} empty")
    if args.workers > 1:
        pool.close()
        pool.join()
    print(f"Wrote {len(jobs)} frames to {args.out}")


if __name__ == "__main__":
    main()
