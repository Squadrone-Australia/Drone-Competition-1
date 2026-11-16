"""Score trained weights on the held-out splits, the way the competition will.

    python -m solution_fire.evaluate --model solution_fire/runs/fire_s/weights/best.pt \
        --data solution_fire/data/fire --out solution_fire/runs/fire_s/eval.json

A correct fire is +2 and a flip at a decoy is -2, so this reports both sides
at a sweep of score thresholds, per split:

* **recall**: real held-out frames with a disc, and the share where it is found
  (IoU >= 0.5 with the label);
* **false alarms**: detections that match no disc, and the share of
  negative frames (no disc at all) with one; per session, the longest run of
  consecutive false-alarm frames, since a mission that waits for N frames in
  a row before flipping only flips on a run that long;
* on synthetic test frames, false alarms **per pasted decoy kind** (green
  circle, black triangle, red ring, ...), from the frame's ``meta``;
* **range** error against the marker's ground truth (real frames), and which
  measurement it came from;
* ``--speed``: milliseconds per frame on the CPU, which is what a laptop at
  the venue has.

``--map`` adds Ultralytics' own box and pose mAP on ``data_real.yaml``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution_fire.geometry import CameraCalibration, read_labels  # noqa: E402

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")
THRESHOLDS = (0.25, 0.35, 0.5, 0.6, 0.7, 0.8, 0.9)


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match(preds, truths, iou_min=0.5):
    """Greedy by score: (matched pairs [(pred, truth index)], false alarms, misses)."""
    used, pairs, false = set(), [], []
    for p in sorted(preds, key=lambda s: -s.score):
        best, best_iou = None, iou_min
        for j, t in enumerate(truths):
            if j not in used and (v := iou(p.box, t)) >= best_iou:
                best, best_iou = j, v
        if best is None:
            false.append(p)
        else:
            used.add(best)
            pairs.append((p, best))
    return pairs, false, len(truths) - len(used)


def _frame_index(stem: str) -> int:
    m = re.search(r"_(\d+)(?:_r\d+)?$", stem)
    return int(m.group(1)) if m else 0


def overlaps_extent(box, extent) -> bool:
    return not (box[2] < extent[0] or extent[2] < box[0] or box[3] < extent[1] or
                extent[3] < box[1])


def evaluate_split(detector, root: Path, split: str, thresholds) -> dict:
    images = sorted(p for p in (root / "images" / split).iterdir() if p.suffix in IMAGE_SUFFIXES)
    rows = []
    for path in images:
        frame = cv2.imread(str(path))
        h, w = frame.shape[:2]
        truths = [b for _, b, _ in read_labels(root / "labels" / split / f"{path.stem}.txt", w, h)]
        meta_path = root / "meta" / split / f"{path.stem}.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        rows.append((path.stem, truths, detector.detect(frame), meta))
    out = {"images": len(rows), "with_disc": sum(bool(t) for _, t, _, _ in rows),
           "negative": sum(not t for _, t, _, _ in rows), "thresholds": {}}
    for thr in thresholds:
        tp = fp = fn = neg_alarm = found = 0
        by_kind: dict[str, int] = {}
        decoy_only_alarm = 0
        runs: dict[str, list] = {}
        range_err, range_how = [], {}
        for stem, truths, preds, meta in rows:
            kept = [p for p in preds if p.score >= thr]
            pairs, false, missed = match(kept, truths)
            tp += len(pairs)
            fp += len(false)
            fn += missed
            if truths and pairs:
                found += 1
            if not truths:
                neg_alarm += bool(false)
            if meta.get("decoy_only"):
                decoy_only_alarm += bool(false)
            for p in false:
                hit = [m["kind"] for m in meta.get("pasted", []) if overlaps_extent(p.box,
                                                                                    m["extent"])]
                key = hit[0] if hit else "background"
                by_kind[key] = by_kind.get(key, 0) + 1
            if "session" in meta and not truths:
                runs.setdefault(meta["session"], []).append((_frame_index(stem), bool(false)))
            if "distance_m" in meta and len(truths) == 1 and pairs:
                p = pairs[0][0]
                if p.range_m is not None:
                    range_err.append(abs(p.range_m - meta["distance_m"]) / meta["distance_m"])
                    range_how[p.range_from] = range_how.get(p.range_from, 0) + 1
        longest = {}
        for session, seq in runs.items():
            best = cur = 0
            for _, alarm in sorted(seq):
                cur = cur + 1 if alarm else 0
                best = max(best, cur)
            longest[session] = best
        with_disc = out["with_disc"]
        res = {
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "frames_found": f"{found}/{with_disc}",
            "false_alarms": fp,
            "negative_frames_alarmed": f"{neg_alarm}/{out['negative']}",
        }
        if by_kind:
            res["false_alarms_by_kind"] = dict(sorted(by_kind.items(), key=lambda kv: -kv[1]))
        if any(m.get("decoy_only") for *_, m in rows):
            res["decoy_only_frames_alarmed"] = (
                f"{decoy_only_alarm}/{sum(bool(m.get('decoy_only')) for *_, m in rows)}")
        if longest:
            res["longest_alarm_run_per_negative_session"] = longest
        if range_err:
            e = np.array(range_err)
            res["range_rel_error"] = {"median": round(float(np.median(e)), 4),
                                      "p90": round(float(np.percentile(e, 90)), 4),
                                      "n": len(e), "from": range_how}
        out["thresholds"][str(thr)] = res
    return out


def cpu_speed(weights, root: Path, n: int = 40) -> float:
    from solution_fire.detector import FireDetector

    det = FireDetector(weights, device="cpu", score_min=0.5)
    images = sorted((root / "images").rglob("*.jpg"))[:n]
    frames = [cv2.imread(str(p)) for p in images]
    det.detect(frames[0])
    t = time.perf_counter()
    for f in frames:
        det.detect(f)
    return (time.perf_counter() - t) / len(frames) * 1000


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True, help="build_dataset output folder")
    parser.add_argument("--splits", nargs="+", default=["val_real", "test_real", "test_sim"])
    parser.add_argument("--thresholds", nargs="+", type=float, default=list(THRESHOLDS))
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--device", default=None)
    parser.add_argument("--map", action="store_true", help="Ultralytics mAP on data_real.yaml")
    parser.add_argument("--speed", action="store_true", help="CPU milliseconds per frame")
    parser.add_argument("--out", type=Path, help="write the report as JSON here")
    args = parser.parse_args(argv)

    from solution_fire.detector import FireDetector

    calib = CameraCalibration.load(args.calibration) if args.calibration else CameraCalibration()
    detector = FireDetector(args.model, calibration=calib, score_min=min(args.thresholds),
                            device=args.device)
    report = {"model": str(args.model), "splits": {}}
    for split in args.splits:
        if not (args.data / "images" / split).exists():
            print(f"(no {split} split)")
            continue
        report["splits"][split] = evaluate_split(detector, args.data, split, args.thresholds)
    if args.map and (args.data / "data_real.yaml").exists():
        from ultralytics import YOLO

        model = YOLO(str(args.model))
        for split in ("val", "test"):
            m = model.val(data=str(args.data / "data_real.yaml"), split=split, verbose=False,
                          plots=False, device=args.device)
            report.setdefault("map_real", {})[split] = {
                "box_map50": round(float(m.box.map50), 4),
                "box_map50_95": round(float(m.box.map), 4),
                "pose_map50": round(float(m.pose.map50), 4),
                "pose_map50_95": round(float(m.pose.map), 4),
            }
    if args.speed:
        report["cpu_ms_per_frame"] = round(cpu_speed(args.model, args.data), 1)
    text = json.dumps(report, indent=1)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)


if __name__ == "__main__":
    main()
