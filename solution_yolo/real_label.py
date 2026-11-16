"""Turn real footage into frames with draft labels, ready for review.

    python -m solution_yolo.real_label trial1 gate_clip.mp4 --out solution_yolo/data/real

Takes videos and folders of frames (searched recursively), keeps every Nth
frame that differs enough from the last one kept, and drafts labels for each:

* with ``--model best.pt``, the trained network's rings (the loop that makes
  each round of labelling cheaper than the last);
* otherwise the shipped colour detector (``--vision-config`` and
  ``--calibration`` as for the flight commands). It finds one gate at a time
  and only when it can measure it, so frames it misses are queued for hand
  labelling rather than counted as empty.

Nothing is trusted until ``python -m solution_yolo.review`` has seen it. The
folder's ``review.json`` records each frame's source, session and status:
``auto`` (drafted), ``pending`` (needs labels), and after review ``accepted``,
``empty`` (no gate in shot) or ``rejected``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.vision import CameraCalibration, GateDetector, VisionConfig  # noqa: E402

from solution_yolo.keypoints import (  # noqa: E402
    CENTRE,
    CORNERS,
    NUM_KPTS,
    VISIBLE,
    box_from_keypoints,
    centre_from_corners,
    write_labels,
)

VIDEO_SUFFIXES = {".mp4", ".avi", ".mkv", ".mov", ".h264", ".m4v"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
MANIFEST = "review.json"


def load_manifest(root: Path) -> dict:
    path = root / MANIFEST
    if path.exists():
        return json.loads(path.read_text())
    return {"frames": {}}


def save_manifest(root: Path, manifest: dict) -> None:
    tmp = root / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1))
    tmp.replace(root / MANIFEST)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "frames"


def clip_notes(video: Path) -> str | None:
    """The ``notes`` of a recording's sidecar JSON (``clip.mp4`` + ``clip.json``),
    as the capture tool writes them, if there is one."""
    sidecar = video.with_suffix(".json")
    if not sidecar.exists():
        return None
    try:
        notes = json.loads(sidecar.read_text()).get("notes")
    except (OSError, ValueError, AttributeError):
        return None
    return str(notes).strip() if notes else None


def _read_video(video: Path, every: int):
    capture = cv2.VideoCapture(str(video))
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % every == 0:
            yield f"{video}#{index}", frame
        index += 1
    capture.release()


def iter_frames(sources: list[Path], every: int):
    """(session, source description, frame, notes) from videos and image folders.

    A session is one video, or one folder of stills: frames from the same one
    are near-copies, so the dataset build keeps a session on one side of the
    train/validation split. A folder's videos are each their own session.
    """
    for source in sources:
        if source.is_dir():
            files = sorted(p for p in source.rglob("*") if p.is_file())
            for video in (p for p in files if p.suffix.lower() in VIDEO_SUFFIXES):
                notes = clip_notes(video)
                for where, frame in _read_video(video, every):
                    yield _slug(video.stem), where, frame, notes
            stills = [p for p in files if p.suffix.lower() in IMAGE_SUFFIXES]
            for i, path in enumerate(stills):
                if i % every == 0:
                    frame = cv2.imread(str(path))
                    if frame is not None:
                        session = _slug(str(path.parent.relative_to(source.parent)))
                        yield session, str(path), frame, None
        elif source.suffix.lower() in VIDEO_SUFFIXES:
            notes = clip_notes(source)
            for where, frame in _read_video(source, every):
                yield _slug(source.stem), where, frame, notes
        elif source.suffix.lower() in IMAGE_SUFFIXES:
            frame = cv2.imread(str(source))
            if frame is not None:
                yield _slug(source.parent.name), str(source), frame, None
        else:
            print(f"Skipping {source}: not a video, image or folder")


def _thumb(frame: np.ndarray) -> np.ndarray:
    small = cv2.resize(frame, (64, 48), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)


def draft_colour(detector: GateDetector, frame: np.ndarray) -> list:
    """The colour detector's gate as a (box, keypoints) label, if it found one."""
    sighting = detector.detect(frame)
    if sighting is None:
        return []
    h, w = frame.shape[:2]
    corners = np.asarray(sighting.corners, float)
    inside = (
        (corners[:, 0] >= 0)
        & (corners[:, 0] < w)
        & (corners[:, 1] >= 0)
        & (corners[:, 1] < h)
    )
    kpts = np.zeros((NUM_KPTS, 3))
    kpts[:CORNERS, :2] = corners
    kpts[:CORNERS, 2] = np.where(inside, VISIBLE, 0)
    # The pose puts every corner somewhere, in shot or not: the centre comes
    # from all eight, then is kept only if it is in the frame.
    centre = centre_from_corners(corners)
    if centre is not None and 0 <= centre[0] < w and 0 <= centre[1] < h:
        kpts[CENTRE] = (*centre, VISIBLE)
    kpts[kpts[:, 2] == 0] = 0
    box = box_from_keypoints(
        np.column_stack((corners, np.full(CORNERS, VISIBLE))).tolist() + [[0, 0, 0]],
        w,
        h,
        detector.diameter_m,
        detector.opening_m,
    )
    return [] if box is None else [(box, kpts)]


def draft_model(
    detector, frame: np.ndarray, keypoint_min: float, max_gates: int | None = None
) -> list:
    """Every ring the network reports, keypoints kept where it is confident."""
    h, w = frame.shape[:2]
    out = []
    for ring in detector.rings(frame)[:max_gates]:
        k = np.asarray(ring.keypoints, float).copy()
        good = (
            (k[:, 2] >= keypoint_min)
            & (k[:, 0] >= 0)
            & (k[:, 0] < w)
            & (k[:, 1] >= 0)
            & (k[:, 1] < h)
        )
        k[:, 2] = np.where(good, VISIBLE, 0)
        k[~good] = 0
        out.append((ring.box, k))
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sources", type=Path, nargs="+", help="videos or folders")
    parser.add_argument("--out", type=Path, default=Path("solution_yolo/data/real"))
    parser.add_argument("--every", type=int, default=5, help="keep every Nth frame")
    parser.add_argument(
        "--min-change",
        type=float,
        default=4.0,
        help="mean grey-level change from the last kept frame, else skipped",
    )
    parser.add_argument("--model", type=Path, help="draft with trained YOLO weights")
    parser.add_argument("--keypoint-min", type=float, default=0.5)
    parser.add_argument(
        "--max-gates",
        type=int,
        help="keep only the N highest-scoring rings per frame (e.g. 1 when only "
        "one gate was filmed, so clutter the model mistakes for a ring is "
        "left out of the draft)",
    )
    parser.add_argument(
        "--empty",
        action="store_true",
        help="the footage has no gate in it: keep its frames as reviewed "
        "negatives, and count the ones the drafter would have mistaken for one",
    )
    parser.add_argument("--calibration", type=Path, help="CameraCalibration JSON")
    parser.add_argument("--vision-config", type=Path, help="colour detector profile")
    args = parser.parse_args(argv)

    calibration = (
        CameraCalibration(**json.loads(args.calibration.read_text()))
        if args.calibration
        else CameraCalibration()
    )
    if args.model:
        from solution_yolo.detector import YoloGateDetector

        yolo = YoloGateDetector(args.model, calibration=calibration, score_min=0.3)

        def draft(frame):
            return draft_model(yolo, frame, args.keypoint_min, args.max_gates)
    else:
        vision = VisionConfig.load(args.vision_config) if args.vision_config else None
        colour = GateDetector(calibration=calibration, config=vision)

        def draft(frame):
            return draft_colour(colour, frame)

    (args.out / "images").mkdir(parents=True, exist_ok=True)
    (args.out / "labels").mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.out)
    frames = manifest["frames"]
    known_sources = {entry["source"] for entry in frames.values()}
    counters: dict[str, int] = {}
    last: dict[str, np.ndarray] = {}
    kept = drafted = false_alarms = 0
    for session, source, frame, notes in iter_frames(args.sources, max(1, args.every)):
        if source in known_sources:
            continue
        thumb = _thumb(frame)
        if (
            session in last
            and float(np.mean(np.abs(thumb - last[session]))) < args.min_change
        ):
            continue
        last[session] = thumb
        n = counters.get(session, 0)
        while f"{session}_{n:05d}" in frames:
            n += 1
        counters[session] = n + 1
        stem = f"{session}_{n:05d}"
        h, w = frame.shape[:2]
        cv2.imwrite(
            str(args.out / "images" / f"{stem}.jpg"),
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, 95],
        )
        gates = draft(frame)
        if args.empty:
            false_alarms += bool(gates)
            gates = []
        elif gates:
            write_labels(args.out / "labels" / f"{stem}.txt", gates, w, h)
        frames[stem] = {
            "source": source,
            "session": session,
            "size": [w, h],
            "status": "empty" if args.empty else "auto" if gates else "pending",
        }
        if notes:
            frames[stem]["notes"] = notes
        kept += 1
        drafted += bool(gates)
        if kept % 100 == 0:
            save_manifest(args.out, manifest)
            print(f"{kept} frames kept, {drafted} drafted")
    save_manifest(args.out, manifest)
    if args.empty:
        print(
            f"Kept {kept} new negative frames in {args.out}; the drafter saw a "
            f"gate in {false_alarms} of them."
        )
        return
    print(
        f"Kept {kept} new frames in {args.out} ({drafted} drafted, "
        f"{kept - drafted} need labels). Next: python -m solution_yolo.review "
        f"{args.out}"
    )


if __name__ == "__main__":
    main()
