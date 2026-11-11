"""Score a trained model where it matters: does it find the gate, and is the
range right?

    python -m solution_yolo.evaluate --model best.pt --data solution_yolo/data/gates
    python -m solution_yolo.evaluate --model best.pt --clips clips.csv

* ``--data``: synthetic test frames with ground truth (``meta/test_sim``).
  For each frame with a measurable gate, meaning one in the 0.3-3.4 m window,
  within 38 degrees of square-on and with no second gate as near, it reports
  whether ``YoloGateDetector.detect`` measured it, and the range, lateral,
  height and yaw error, binned by distance. ``--compare-colour`` scores the
  shipped colour detector on the same frames. ``--map`` also runs
  Ultralytics' own box and keypoint mAP on the test split.
* ``--clips``: real videos with tape-measured distances, as a CSV with
  columns ``file,distance_m`` and optionally ``lateral_m,height_m,yaw_deg``.
  It reports the detection rate and the median measured range against the tape.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.vision import CameraCalibration, GateDetector  # noqa: E402

BINS = ((0.3, 1.0), (1.0, 2.0), (2.0, 3.4))


def target_gate(meta: dict):
    """The gate a detector should report, or None when there is no single
    measurable one."""
    gates = [
        g
        for g in meta["gates"]
        if 0.3 < g["forward_m"] < 3.4
        and abs(g["normal_yaw_deg"]) < 38
        and g["corners_in_shot"] >= 4
    ]
    if not gates:
        return None
    gates.sort(key=lambda g: g["forward_m"])
    nearest = gates[0]
    others = [g for g in meta["gates"] if g is not nearest]
    if any(g["forward_m"] < nearest["forward_m"] * 1.5 for g in others):
        return None
    return nearest


def score_synthetic(detector, root: Path, limit: int | None) -> dict:
    meta_dir = root / "meta" / "test_sim"
    image_dir = root / "images" / "test_sim"
    rows = []
    false_alarms = empties = 0
    metas = sorted(meta_dir.glob("*.json"))[:limit]
    for path in metas:
        meta = json.loads(path.read_text())
        if meta["tilt"]["roll_deg"] or meta["tilt"]["pitch_deg"]:
            continue  # the ground truth is in the level camera's frame
        frame = cv2.imread(str(image_dir / f"{path.stem}.jpg"))
        if frame is None:
            continue
        gt = target_gate(meta)
        sighting = detector.detect(frame)
        if not meta["gates"]:
            empties += 1
            false_alarms += sighting is not None
        if gt is None:
            continue
        hit = None
        if sighting is not None:
            cx, cy = np.asarray(sighting.outline).mean(axis=0)
            x0, y0, x1, y1 = gt["box"]
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                hit = sighting
        rows.append((gt, hit))
    out = {"frames": len(rows), "background_frames": empties}
    out["background_false_alarms"] = false_alarms
    found = [(g, s) for g, s in rows if s is not None]
    out["detection_rate"] = len(found) / len(rows) if rows else None
    for lo, hi in BINS:
        sel = [(g, s) for g, s in rows if lo <= g["forward_m"] < hi]
        got = [(g, s) for g, s in sel if s is not None]
        entry = {"n": len(sel), "detected": len(got)}
        if got:
            rel = [abs(s.forward_m - g["forward_m"]) / g["forward_m"] for g, s in got]
            lat = [abs(s.right_m - g["right_m"]) for g, s in got]
            up = [abs(s.up_m - g["up_m"]) for g, s in got]
            yaw = [abs(s.normal_yaw_deg - g["normal_yaw_deg"]) for g, s in got]
            entry |= {
                "range_err_median_pct": 100 * float(np.median(rel)),
                "range_err_p90_pct": 100 * float(np.percentile(rel, 90)),
                "lateral_err_median_cm": 100 * float(np.median(lat)),
                "height_err_median_cm": 100 * float(np.median(up)),
                "yaw_err_median_deg": float(np.median(yaw)),
            }
        out[f"{lo}-{hi} m"] = entry
    partial = [(g, s) for g, s in rows if g.get("clipped")]
    if partial:
        out["partial_ring_detection_rate"] = sum(
            s is not None for _, s in partial
        ) / len(partial)
    return out


def score_clips(detector, csv_path: Path) -> list[dict]:
    results = []
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f):
            path = (csv_path.parent / row["file"]).resolve()
            truth = float(row["distance_m"])
            capture = cv2.VideoCapture(str(path))
            ranges, frames = [], 0
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frames += 1
                s = detector.detect(frame)
                if s is not None:
                    ranges.append(s.forward_m)
            capture.release()
            entry = {"file": row["file"], "distance_m": truth, "frames": frames}
            entry["detection_rate"] = len(ranges) / frames if frames else 0.0
            if ranges:
                median = float(np.median(ranges))
                entry |= {
                    "median_range_m": median,
                    "range_err_pct": 100 * (median - truth) / truth,
                    "range_spread_m": float(np.percentile(ranges, 90))
                    - float(np.percentile(ranges, 10)),
                }
            results.append(entry)
    return results


# Words a recording's notes may use for how far away the gate was, nearest
# first: the ranges measured for them should come out in the same order.
DISTANCE_WORDS = ("closest", "close", "mid", "far", "furthest")


def _distance_word(notes: str) -> str | None:
    words = set(re.findall(r"[a-z]+", notes.lower()))
    for word in reversed(DISTANCE_WORDS):  # "furthest" before "far"
        if word in words:
            return word
    return None


def tape_distance_m(info: dict) -> float | None:
    """A recording's tape-measured distance from its sidecar: ``distance_m`` in
    metres, or ``dist`` in centimetres (as the recorder's notes give it)."""
    for key, scale in (("distance_m", 1.0), ("dist", 0.01)):
        value = info.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value) * scale
    return None


def score_sessions(detector, folder: Path, every: int = 2) -> dict:
    """Every video in a folder, with its sidecar notes: how often the gate is
    measured and at what range. A sidecar with a tape-measured distance
    (``tape_distance_m``) also scores the range error; otherwise clips noted
    closest .. furthest must at least measure in that order."""
    clips = []
    for video in sorted(folder.rglob("*")):
        if video.suffix.lower() not in {".mp4", ".avi", ".mkv", ".mov"}:
            continue
        notes, truth = "", None
        sidecar = video.with_suffix(".json")
        if sidecar.exists():
            info = json.loads(sidecar.read_text())
            notes = str(info.get("notes") or "")
            truth = tape_distance_m(info)
        capture = cv2.VideoCapture(str(video))
        ranges, frames, index = [], 0, 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % every == 0:
                frames += 1
                s = detector.detect(frame)
                if s is not None:
                    ranges.append(s.forward_m)
            index += 1
        capture.release()
        entry = {"clip": video.stem, "notes": notes, "frames": frames}
        entry["measured_rate"] = round(len(ranges) / frames, 3) if frames else 0.0
        if truth is not None:
            entry["tape_m"] = truth
        if ranges:
            entry["median_range_m"] = round(float(np.median(ranges)), 3)
            entry["range_p10_p90_m"] = [
                round(float(np.percentile(ranges, q)), 3) for q in (10, 90)
            ]
            if truth is not None:
                entry["range_err_pct"] = round(
                    100 * (entry["median_range_m"] - truth) / truth, 1
                )
        clips.append(entry)
    # The ordering check, within clips that share every note but the distance.
    groups: dict[str, list] = {}
    for c in clips:
        word = _distance_word(c["notes"])
        if word and "median_range_m" in c:
            rest = re.sub(word, "", c["notes"].lower())
            key = " ".join(re.findall(r"[a-z]+", rest))
            groups.setdefault(key, []).append((DISTANCE_WORDS.index(word), c))
    ordering = {}
    for key, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m[0])
        medians = [c["median_range_m"] for _, c in members]
        ordering[key] = {
            "order": [DISTANCE_WORDS[i] for i, _ in members],
            "median_range_m": medians,
            "monotonic": all(a < b for a, b in zip(medians, medians[1:], strict=False)),
        }
    return {"clips": clips, "distance_ordering": ordering}


def _videos(folder: Path) -> list[Path]:
    return [
        p
        for p in sorted(folder.rglob("*"))
        if p.suffix.lower() in {".mp4", ".avi", ".mkv", ".mov"}
    ]


def _read_every(video: Path, every: int):
    capture = cv2.VideoCapture(str(video))
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % every == 0:
            yield frame
        index += 1
    capture.release()


def _errors(pairs) -> dict:
    """Summary of (truth, sighting) pairs."""
    rel = np.array([(s.forward_m - t.forward_m) / t.forward_m for t, s in pairs])
    lat = np.array([abs(s.right_m - t.right_m) for t, s in pairs])
    up = np.array([abs(s.up_m - t.up_m) for t, s in pairs])
    yaw = np.array([abs(s.normal_yaw_deg - t.normal_yaw_deg) for t, s in pairs])
    return {
        "range_err_median_pct": round(100 * float(np.median(np.abs(rel))), 1),
        "range_err_p90_pct": round(100 * float(np.percentile(np.abs(rel), 90)), 1),
        "range_bias_pct": round(100 * float(np.median(rel)), 1),
        "lateral_err_median_cm": round(100 * float(np.median(lat)), 1),
        "height_err_median_cm": round(100 * float(np.median(up)), 1),
        "yaw_err_median_deg": round(float(np.median(yaw)), 1),
    }


def score_marker(detector, folder: Path, truth, offset, every: int = 2) -> dict:
    """Every frame where the marker is seen is a ground-truth frame: how often
    the detector measured the ring there, and how far off it was."""
    clips, pooled = [], []
    for video in _videos(folder):
        seen, pairs = 0, []
        for frame in _read_every(video, every):
            pose = truth.pose(frame)
            if pose is None:
                continue
            seen += 1
            gt = truth.ring(pose, offset)
            s = detector.detect(frame)
            if s is not None:
                pairs.append((gt, s))
        entry = {"clip": video.stem, "marker_frames": seen}
        if seen:
            entry["measured_rate"] = round(len(pairs) / seen, 3)
        if pairs:
            entry |= _errors(pairs)
        clips.append(entry)
        pooled += pairs
    bins = {}
    for lo, hi in BINS:
        sel = [(t, s) for t, s in pooled if lo <= t.forward_m < hi]
        if sel:
            bins[f"{lo}-{hi} m"] = {"n": len(sel)} | _errors(sel)
    out = {"ring_offset_m": [round(float(v), 3) for v in offset], "clips": clips}
    out["by_true_range"] = bins
    if pooled:
        out["all"] = {"n": len(pooled)} | _errors(pooled)
    return out


def _print(title: str, report) -> None:
    print(f"\n== {title}")
    print(json.dumps(report, indent=1))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", type=Path, required=True, help="YOLO weights")
    parser.add_argument("--data", type=Path, help="dataset folder from build_dataset")
    parser.add_argument("--clips", type=Path, help="CSV of tape-measured clips")
    parser.add_argument(
        "--sessions",
        type=Path,
        help="folder of recordings with sidecar JSON notes (no tape measure)",
    )
    parser.add_argument(
        "--aruco",
        type=Path,
        help="folder of recordings with an ArUco marker in the ring's plane",
    )
    parser.add_argument("--marker-mm", type=float, help="the marker's printed side")
    parser.add_argument("--marker-dict", default="DICT_5X5_100")
    parser.add_argument("--marker-id", type=int, default=0)
    parser.add_argument(
        "--ring-offset",
        type=float,
        nargs=2,
        metavar=("X", "Y"),
        help="ring centre from the marker centre in metres (x right, y up); "
        "estimated from the footage when left out",
    )
    parser.add_argument("--calibration", type=Path, help="CameraCalibration JSON")
    parser.add_argument("--compare-colour", action="store_true")
    parser.add_argument("--map", action="store_true", help="also run Ultralytics val")
    parser.add_argument("--limit", type=int, help="score at most this many frames")
    parser.add_argument("--device", default=None)
    parser.add_argument("--report", type=Path, help="write the results as JSON")
    args = parser.parse_args(argv)
    if not (args.data or args.clips or args.sessions or args.aruco):
        raise SystemExit("Give --data, --clips, --sessions and/or --aruco")
    if args.aruco and not args.marker_mm:
        raise SystemExit("--aruco needs --marker-mm, the marker's measured side")

    from solution_yolo.detector import YoloGateDetector

    calibration = (
        CameraCalibration(**json.loads(args.calibration.read_text()))
        if args.calibration
        else CameraCalibration()
    )
    detectors = {
        "yolo": YoloGateDetector(
            args.model, calibration=calibration, device=args.device
        )
    }
    if args.compare_colour:
        detectors["colour"] = GateDetector(calibration=calibration)
    report: dict = {}
    if args.data:
        for name, det in detectors.items():
            report[f"synthetic/{name}"] = score_synthetic(det, args.data, args.limit)
            _print(f"synthetic test, {name} detector", report[f"synthetic/{name}"])
        if args.map:
            from ultralytics import YOLO

            metrics = YOLO(str(args.model)).val(
                data=str(args.data / "data.yaml"), split="test", device=args.device
            )
            report["map"] = {
                "box_map50": float(metrics.box.map50),
                "box_map50_95": float(metrics.box.map),
                "pose_map50": float(metrics.pose.map50),
                "pose_map50_95": float(metrics.pose.map),
            }
            _print("Ultralytics mAP (test split)", report["map"])
    if args.sessions:
        for name, det in detectors.items():
            report[f"sessions/{name}"] = score_sessions(det, args.sessions)
            _print(f"recorded sessions, {name} detector", report[f"sessions/{name}"])
    if args.aruco:
        from solution_yolo.marker import MarkerTruth, estimate_offset

        truth = MarkerTruth(
            args.marker_mm / 1000, args.marker_dict, args.marker_id, calibration
        )
        if args.ring_offset:
            offset = np.array(args.ring_offset)
        else:
            frames = (f for v in _videos(args.aruco) for f in _read_every(v, 5))
            offset, used = estimate_offset(truth, detectors["yolo"], frames)
            if offset is None:
                raise SystemExit(
                    "Could not place the ring from the marker; pass --ring-offset"
                )
            print(
                f"Ring centre from the marker centre, from {used} frames: "
                f"{100 * offset[0]:+.1f} cm right, {100 * offset[1]:+.1f} cm up "
                "(check with a ruler; --ring-offset to override)"
            )
        for name, det in detectors.items():
            report[f"aruco/{name}"] = score_marker(det, args.aruco, truth, offset)
            _print(f"marker ground truth, {name} detector", report[f"aruco/{name}"])
    if args.clips:
        for name, det in detectors.items():
            report[f"clips/{name}"] = score_clips(det, args.clips)
            _print(f"tape-measured clips, {name} detector", report[f"clips/{name}"])
    if args.report:
        args.report.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
