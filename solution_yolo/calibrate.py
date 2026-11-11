"""Measure the Tello camera from ChArUco footage.

    python -m solution_yolo.calibrate solution_yolo/data/raw/calib_25_20mm_DICT_5x5_100 \
        --squares 7 7 --square-mm 25 --marker-mm 20 --dict DICT_5X5_100 \
        --out tello_camera.json

Range from PnP scales directly with the focal length, so this is the number
that makes metres right. Takes videos and folders of stills, finds the board in
every Nth frame, keeps the sharpest views spread over the image (by where the
board sits and how big it is), and runs OpenCV's calibration twice: once on all
of them, then again without views that fit far worse than the rest.

``--out`` is a ``CameraCalibration`` JSON for every ``--calibration`` flag in
``solution/`` and ``solution_yolo/``. The fit's diagnostics (reprojection error,
views used, field of view) go beside it as ``<out>.report.json``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution_yolo.marker import aruco_dictionary  # noqa: E402
from solution_yolo.real_label import IMAGE_SUFFIXES, VIDEO_SUFFIXES  # noqa: E402

GRID = (4, 3)  # where the board's centre falls in the image
SIZES = (0.15, 0.35)  # board area / image area: small, medium, large


def frames(sources: list[Path], every: int):
    """(name, frame) from videos and image folders, every Nth."""
    for source in sources:
        files = (
            sorted(p for p in source.rglob("*") if p.is_file())
            if source.is_dir()
            else [source]
        )
        stills = [p for p in files if p.suffix.lower() in IMAGE_SUFFIXES]
        for i, path in enumerate(stills):
            if i % every == 0 and (img := cv2.imread(str(path))) is not None:
                yield path.name, img
        for video in (p for p in files if p.suffix.lower() in VIDEO_SUFFIXES):
            capture = cv2.VideoCapture(str(video))
            index = 0
            while True:
                ok, img = capture.read()
                if not ok:
                    break
                if index % every == 0:
                    yield f"{video.name}#{index}", img
                index += 1
            capture.release()


def sharpness(grey: np.ndarray, corners: np.ndarray) -> float:
    """Laplacian variance inside the board's bounding box: blur ruins corners."""
    x0, y0 = np.floor(corners.min(axis=0)).astype(int)
    x1, y1 = np.ceil(corners.max(axis=0)).astype(int)
    patch = grey[max(0, y0) : y1 + 1, max(0, x0) : x1 + 1]
    return float(cv2.Laplacian(patch, cv2.CV_64F).var()) if patch.size else 0.0


def select_views(views: list[dict], width: int, height: int, per_bin: int) -> list:
    """The sharpest few views in each (position, size) bin, so the fit sees
    the board everywhere in the image, near and far, not 200 copies of one
    pose."""
    bins: dict[tuple, list] = {}
    for v in views:
        cx, cy = v["image"].mean(axis=0)
        gx = min(GRID[0] - 1, int(cx / width * GRID[0]))
        gy = min(GRID[1] - 1, int(cy / height * GRID[1]))
        hull = cv2.convexHull(v["image"].astype(np.float32))
        area = cv2.contourArea(hull) / (width * height)
        size = sum(area > s for s in SIZES)
        bins.setdefault((gx, gy, size), []).append(v)
    chosen = []
    for members in bins.values():
        members.sort(key=lambda v: v["sharpness"], reverse=True)
        # Within a bin, skip near-duplicates of a view already taken.
        taken: list = []
        for v in members:
            if len(taken) >= per_bin:
                break
            if all(
                np.abs(v["image"].mean(0) - t["image"].mean(0)).max() > 15
                for t in taken
            ):
                taken.append(v)
        chosen += taken
    return chosen


def calibrate(views: list[dict], size: tuple[int, int], flags: int):
    obj = [v["object"].astype(np.float32) for v in views]
    img = [v["image"].astype(np.float32) for v in views]
    rms, matrix, dist, rvecs, tvecs = cv2.calibrateCamera(
        obj, img, size, None, None, flags=flags
    )
    errors, tilts = [], []
    for o, i, r, t in zip(obj, img, rvecs, tvecs, strict=True):
        p, _ = cv2.projectPoints(o, r, t, matrix, dist)
        errors.append(
            float(
                np.sqrt(
                    np.mean(np.sum((p.reshape(-1, 2) - i.reshape(-1, 2)) ** 2, axis=1))
                )
            )
        )
        normal = cv2.Rodrigues(r)[0][:, 2]
        tilts.append(math.degrees(math.acos(min(1.0, abs(float(normal[2]))))))
    return rms, matrix, dist.ravel(), np.array(errors), np.array(tilts)


def focal_spread(
    views: list[dict], size, flags: int, rng, rounds: int = 15
) -> list[float]:
    """fx refitted on random halves of the views: how far to trust it. A board
    filmed square-on cannot tell focal length from distance, and this spread is
    where that shows."""
    if len(views) < 20:
        return []
    out = []
    for _ in range(rounds):
        pick = rng.choice(len(views), len(views) // 2, replace=False)
        out.append(float(calibrate([views[i] for i in pick], size, flags)[1][0, 0]))
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sources", type=Path, nargs="+", help="videos or folders")
    parser.add_argument(
        "--squares", type=int, nargs=2, default=(7, 7), metavar=("X", "Y")
    )
    parser.add_argument("--square-mm", type=float, required=True)
    parser.add_argument("--marker-mm", type=float, required=True)
    parser.add_argument("--dict", default="DICT_5X5_100")
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="boards from OpenCV < 4.6 with an even row count",
    )
    parser.add_argument("--every", type=int, default=3, help="look at every Nth frame")
    parser.add_argument("--min-corners", type=int, default=12)
    parser.add_argument("--per-bin", type=int, default=8)
    parser.add_argument(
        "--k3",
        action="store_true",
        help="also fit the third radial term; only with views right into the image corners",
    )
    parser.add_argument("--out", type=Path, default=Path("tello_camera.json"))
    args = parser.parse_args(argv)

    board = cv2.aruco.CharucoBoard(
        tuple(args.squares),
        args.square_mm / 1000,
        args.marker_mm / 1000,
        aruco_dictionary(args.dict),
    )
    board.setLegacyPattern(args.legacy)
    detector = cv2.aruco.CharucoDetector(board)

    views, size, looked = [], None, 0
    for name, frame in frames(args.sources, max(1, args.every)):
        looked += 1
        h, w = frame.shape[:2]
        if size is None:
            size = (w, h)
        elif size != (w, h):
            raise SystemExit(
                f"{name} is {w}x{h}, not {size[0]}x{size[1]}: calibrate one resolution at a time"
            )
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _, _ = detector.detectBoard(grey)
        if ids is None or len(ids) < args.min_corners:
            continue
        obj, img = board.matchImagePoints(corners, ids)
        if obj is None or len(obj) < args.min_corners:
            continue
        img = img.reshape(-1, 2)
        if board.checkCharucoCornersCollinear(ids):
            continue
        views.append(
            {
                "name": name,
                "object": obj.reshape(-1, 3),
                "image": img,
                "sharpness": sharpness(grey, img),
            }
        )
    if size is None:
        raise SystemExit("No frames found")
    print(f"Board found in {len(views)} of {looked} frames looked at")
    if len(views) < 10:
        raise SystemExit("Too few views of the board to calibrate (need 10 or more)")
    # The blurriest third of all detections never make the cut.
    floor = float(np.percentile([v["sharpness"] for v in views], 33))
    sharp = [v for v in views if v["sharpness"] >= floor]
    chosen = select_views(sharp, *size, args.per_bin)
    print(f"Calibrating on {len(chosen)} views spread over the image")

    flags = 0 if args.k3 else cv2.CALIB_FIX_K3
    rms, matrix, dist, errors, tilts = calibrate(chosen, size, flags)
    keep = errors <= max(1.0, 3 * float(np.median(errors)))
    if not keep.all():
        dropped = [v["name"] for v, k in zip(chosen, keep, strict=True) if not k]
        print(f"Refitting without {len(dropped)} outlier views: {', '.join(dropped)}")
        chosen = [v for v, k in zip(chosen, keep, strict=True) if k]
        rms, matrix, dist, errors, tilts = calibrate(chosen, size, flags)

    spread = focal_spread(chosen, size, flags, np.random.default_rng(0))
    w, h = size
    fx, fy, cx, cy = matrix[0, 0], matrix[1, 1], matrix[0, 2], matrix[1, 2]
    calibration = {
        "focal_per_width": float(fx / w),
        "fy_per_width": float(fy / w),
        "cx_fraction": float(cx / w),
        "cy_fraction": float(cy / h),
        "distortion": [float(d) for d in dist[:5]],
    }
    report = {
        "image_size": [w, h],
        "views": len(chosen),
        "rms_reprojection_px": float(rms),
        "per_view_error_px": {
            "median": float(np.median(errors)),
            "max": float(errors.max()),
        },
        "fx_px": float(fx),
        "fy_px": float(fy),
        "cx_px": float(cx),
        "cy_px": float(cy),
        "hfov_deg": math.degrees(2 * math.atan(w / 2 / fx)),
        "vfov_deg": math.degrees(2 * math.atan(h / 2 / fy)),
        "dfov_deg": math.degrees(2 * math.atan(math.hypot(w / 2 / fx, h / 2 / fy))),
        "fx_px_resampled_10_90": [float(v) for v in np.percentile(spread, [10, 90])]
        if spread
        else None,
        "board_tilt_deg": {
            "median": float(np.median(tilts)),
            "max": float(tilts.max()),
            "share_over_30": float((tilts > 30).mean()),
        },
        "coverage_bins": len(
            {tuple((v["image"].mean(0) / (w, h) * GRID).astype(int)) for v in chosen}
        ),
        "board": {
            "squares": list(args.squares),
            "square_mm": args.square_mm,
            "marker_mm": args.marker_mm,
            "dict": args.dict,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(calibration, indent=1))
    report_path = args.out.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    print(f"Wrote {args.out} (use it as --calibration) and {report_path}")
    if rms > 1.0:
        print(
            "Warning: RMS reprojection above 1 px. Check --squares, the measured sizes and the board's flatness."
        )
    if report["board_tilt_deg"]["share_over_30"] < 0.2:
        print(
            "Warning: few views tilt the board more than 30 degrees, so the focal "
            "length is weakly measured (see fx_px_resampled_10_90). Film the board "
            "turned 30-50 degrees left, right, up and down."
        )
    if report["coverage_bins"] < GRID[0] * GRID[1] - 2:
        print(
            "Warning: the board missed parts of the image; distortion near the edges is extrapolated."
        )


if __name__ == "__main__":
    main()
