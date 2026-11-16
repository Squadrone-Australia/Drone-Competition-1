"""Turn Tello footage into labelled training frames, with no hand labelling.

    # positives: the disc on its marker board
    python -m solution_fire.autolabel solution_yolo/data/raw/redcircle \
        --out solution_fire/data/real --calibration tello_camera.json
    # negatives: footage with no red disc in it at all
    python -m solution_fire.autolabel solution_yolo/data/raw/notredcircle \
        solution_yolo/data/raw/empetystand --out solution_fire/data/real --empty
    # competition boards, which carry no marker: see markerless_draft
    python -m solution_fire.autolabel solution_yolo/data/raw/redcircle_positive \
        --out solution_fire/data/real --markerless --model solution_fire/models/fire_pose_s.pt

How a positive frame is labelled
--------------------------------
1. The ArUco marker under the disc gives the board's pose (``MarkerTruth``).
2. Per clip, the disc's true offset from the marker and its radius are measured
   from the footage itself: the red blob's outline is cast onto the marker's
   plane, which gives it in metres. The ruler values (``geometry``) are the
   fallback, used when the estimate disagrees with them by more than 15%.
3. The disc's centre and four rim points are projected through the pose. That
   is the label: exact wherever the marker is, even with the disc cut off by
   the frame edge.
4. The red blob nearest that projection is fitted with an ellipse and compared
   with the projected one. They agree (IoU >= ``--iou-min``) and the frame is
   ``auto``; otherwise, or with no marker in shot, it is ``pending`` for
   ``solution_fire.review``, drafted from the blob (or ``--model``) if there
   is one.

Every saved frame has its marker painted out (``marker.paint_out``): the
competition boards will not have one. ``meta/<stem>.json`` keeps the marker's
ground truth (range, offsets, yaw) for ``evaluate``.

Sessions
--------
Neighbouring frames are near-copies, so ``build_dataset`` keeps a session on
one side of the train/validation line. With only a few clips, one session per
clip would mean training on one of them, so each clip is cut into
``--chunk-s`` second sessions (``<clip>_s0``, ``_s1``, ...) and frames within
``--guard-s`` of a cut are dropped, so no held-out chunk has a twin in train.

The manifest (``review.json``) format is ``solution_yolo.real_label``'s:
status ``auto`` / ``pending`` / ``empty``, then ``accepted`` / ``rejected``
after review.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution_fire.geometry import (  # noqa: E402
    BOTTOM,
    CENTRE,
    DISC_DIAMETER_M,
    DISC_OFFSET_M,
    LEFT,
    NUM_KPTS,
    RIGHT,
    TOP,
    VISIBLE,
    CameraCalibration,
    box_from_keypoints,
    project_disc,
    write_labels,
)
from solution_fire.marker import (  # noqa: E402
    MarkerTruth,
    disc_truth,
    paint_out,
    predicted_corners,
)

VIDEO_SUFFIXES = {".mp4", ".avi", ".mkv", ".mov", ".h264", ".m4v"}
MANIFEST = "review.json"


def load_manifest(root: Path) -> dict:
    path = root / MANIFEST
    if path.exists():
        return json.loads(path.read_text())
    return {"frames": {}, "clips": {}}


def save_manifest(root: Path, manifest: dict) -> None:
    tmp = root / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1))
    tmp.replace(root / MANIFEST)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "clip"


def find_videos(sources: list[Path]) -> list[Path]:
    out = []
    for source in sources:
        if source.is_dir():
            out += sorted(p for p in source.rglob("*") if p.suffix.lower() in VIDEO_SUFFIXES)
        elif source.suffix.lower() in VIDEO_SUFFIXES:
            out.append(source)
        else:
            print(f"Skipping {source}: not a video or folder")
    return out


def read_video(video: Path, every: int):
    """(frame index, frame) for every ``every``-th frame."""
    capture = cv2.VideoCapture(str(video))
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % every == 0:
            yield index, frame
        index += 1
    capture.release()


def video_fps(video: Path) -> float:
    sidecar = video.with_suffix(".json")
    if sidecar.exists():
        try:
            fps = float(json.loads(sidecar.read_text()).get("fps") or 0)
            if fps > 0:
                return fps
        except (OSError, ValueError, AttributeError):
            pass
    capture = cv2.VideoCapture(str(video))
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    capture.release()
    return fps


def chunk_of(index: int, fps: float, chunk_s: float, guard_s: float) -> int | None:
    """The chunk a frame belongs to, or None within ``guard_s`` of a cut."""
    t = index / fps
    chunk = int(t // chunk_s)
    into = t - chunk * chunk_s
    if chunk > 0 and into < guard_s:
        return None
    if chunk_s - into < guard_s:
        return None
    return chunk


def _thumb(frame: np.ndarray) -> np.ndarray:
    small = cv2.resize(frame, (64, 48), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)


# --- the red blob -----------------------------------------------------------------------


def red_mask(frame: np.ndarray) -> np.ndarray:
    """Red-to-orange print under indoor light. Red wraps the hue axis, hence
    two bands; the Tello renders this print anywhere from hue 170 to 15."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 90, 60), (15, 255, 255)) | cv2.inRange(
        hsv, (165, 90, 60), (180, 255, 255)
    )
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def blobs(frame: np.ndarray, min_area: float = 150.0) -> list[np.ndarray]:
    contours, _ = cv2.findContours(red_mask(frame), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return [c for c in contours if cv2.contourArea(c) >= min_area and len(c) >= 5]


def roundness(contour: np.ndarray) -> tuple[float, float]:
    """(circularity of the hull, solidity): a filled disc is near (1, 1); the
    hollow ring is round but has almost no solidity."""
    hull = cv2.convexHull(contour)
    hull_area = cv2.contourArea(hull)
    perimeter = cv2.arcLength(hull, True)
    if hull_area <= 0 or perimeter <= 0:
        return 0.0, 0.0
    return 4 * math.pi * hull_area / perimeter**2, cv2.contourArea(contour) / hull_area


def disc_like(frame: np.ndarray, min_area: float = 400.0):
    """The roundest solid red blob, if any looks like a disc: the colour
    drafter's guess, and the false-alarm count on negative footage."""
    best, best_score = None, 0.0
    for c in blobs(frame, min_area):
        circ, solid = roundness(c)
        if circ > 0.85 and solid > 0.9 and circ * solid > best_score:
            best, best_score = c, circ * solid
    return best


def touches_border(contour: np.ndarray, width: int, height: int, margin: int = 2) -> bool:
    x, y, w, h = cv2.boundingRect(contour)
    return x <= margin or y <= margin or x + w >= width - margin or y + h >= height - margin


def nearest_blob(frame: np.ndarray, centre_px, radius_px: float):
    """The red blob whose centroid is nearest the projected disc centre, within
    half the projected radius; the stand and clutter sit further away."""
    best, best_d = None, 0.5 * radius_px + 4
    for c in blobs(frame):
        m = cv2.moments(c)
        if m["m00"] <= 0:
            continue
        d = math.hypot(m["m10"] / m["m00"] - centre_px[0], m["m01"] / m["m00"] - centre_px[1])
        if d < best_d:
            best, best_d = c, d
    return best


def polygon_iou(a: np.ndarray, b: np.ndarray, width: int, height: int) -> float:
    """IoU of two filled outlines, inside the frame."""
    pts = np.vstack((a.reshape(-1, 2), b.reshape(-1, 2)))
    x0 = int(max(0, np.floor(pts[:, 0].min())))
    y0 = int(max(0, np.floor(pts[:, 1].min())))
    x1 = int(min(width, np.ceil(pts[:, 0].max()) + 1))
    y1 = int(min(height, np.ceil(pts[:, 1].max()) + 1))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    ma = np.zeros((y1 - y0, x1 - x0), np.uint8)
    mb = np.zeros_like(ma)
    off = np.array([x0, y0])
    cv2.fillPoly(ma, [np.round(a.reshape(-1, 2) - off).astype(np.int32)], 1)
    cv2.fillPoly(mb, [np.round(b.reshape(-1, 2) - off).astype(np.int32)], 1)
    union = np.count_nonzero(ma | mb)
    return np.count_nonzero(ma & mb) / union if union else 0.0


def ellipse_label(contour: np.ndarray, width: int, height: int):
    """A draft (box, keypoints) from a blob's fitted ellipse: centre, and the
    ellipse's top, right, bottom and left extremes as the rim points."""
    (cx, cy), (a, b), angle = cv2.fitEllipse(contour)
    t = np.linspace(0, 2 * math.pi, 180, endpoint=False)
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    xs = cx + a / 2 * np.cos(t) * ca - b / 2 * np.sin(t) * sa
    ys = cy + a / 2 * np.cos(t) * sa + b / 2 * np.sin(t) * ca
    pts = np.column_stack((xs, ys))
    k = np.zeros((NUM_KPTS, 3))
    picks = {
        CENTRE: (cx, cy),
        TOP: pts[np.argmin(ys)],
        RIGHT: pts[np.argmax(xs)],
        BOTTOM: pts[np.argmax(ys)],
        LEFT: pts[np.argmin(xs)],
    }
    for i, (x, y) in picks.items():
        if 0 <= x < width and 0 <= y < height:
            k[i] = (x, y, VISIBLE)
    box = box_from_keypoints(k, width, height)
    return None if box is None else (box, k)


def twin_discs(frame: np.ndarray, discs: list) -> list:
    """Other red discs the same size and shape as the labelled one: in this
    footage, its reflection in a mirrored wardrobe door. A reflection looks
    exactly like a red disc, so leaving it unlabelled would teach the network
    to ignore one; it is drafted from its ellipse and the frame is sent to
    review instead."""
    if not discs:
        return []
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = discs[0][0]
    area = max((x1 - x0) * (y1 - y0), 1.0)
    out = []
    for c in blobs(frame, 60):
        circ, solid = roundness(c)
        bx, by, bw, bh = cv2.boundingRect(c)
        if circ < 0.88 or solid < 0.92 or min(bw, bh) < 10 or not 0.6 < bw / bh < 1.6:
            continue
        if not 0.15 < bw * bh / area < 4:
            continue
        cx, cy = bx + bw / 2, by + bh / 2
        if any(b[0] - 5 <= cx <= b[2] + 5 and b[1] - 5 <= cy <= b[3] + 5 for b, _ in discs):
            continue
        draft = ellipse_label(c, w, h)
        if draft is not None:
            out.append(draft)
    return out


# --- footage without a marker -----------------------------------------------------------
#
# The drone-cage footage (2026-11-11) is the competition's own boards: no ArUco
# marker, held by hand or hung in the red-and-white stand. With no pose to
# project, a label has to come from the image, so two independent guesses are
# made and only their agreement is trusted without a look.


def chroma_mask(frame: np.ndarray, ratio: float = 1.8, min_red: int = 14,
                local: bool = False) -> np.ndarray:
    """Red by channel *ratio*, not by HSV brightness.

    Backlit against the cage's windows, the disc comes out at V 28-67 with
    G and B near zero (BGR 0, 4, 28): far below ``red_mask``'s V 60 floor, yet
    unmistakably red, because red still dwarfs the other two channels. The
    black print it must be told from is (0, 30, 35): dark too, but G is close
    to R. ``min_red`` keeps sensor noise in crushed blacks (0, 1, 9) out.

    ``local`` is for a crop around one disc: the cage's varnished wood is as
    red *by ratio* as a backlit disc (0, 15, 70), so there the chroma
    ``R - max(G, B)`` must also clear an Otsu split of the crop. The disc is
    the strongest red in its own neighbourhood whatever the light: about
    twice the wood's chroma in daylight, well above the shaded sheet's when
    backlit.

    The cage netting draws dark lines across the disc, so the mask is closed
    to bridge them back into one blob."""
    b, g, r = (c.astype(np.int16) for c in cv2.split(frame))
    top = np.maximum(g, b)
    keep = (r >= min_red) & (r * 10 >= int(ratio * 10) * top + 20)
    if local:
        chroma = np.clip(r - top, 0, 255).astype(np.uint8)
        split, _ = cv2.threshold(chroma, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        keep &= chroma > max(split, 10)
    mask = keep.astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def chroma_discs(frame: np.ndarray, min_area: float = 120.0, circ_min: float = 0.75,
                 solid_min: float = 0.8, roi=None, open_px: int = 0):
    """Round, solid blobs of ``chroma_mask``, roundest first: (contour, circ, solid).

    A hand over the rim takes a bite out of the blob, so roundness is judged
    on the hull and the gates are looser than ``disc_like``'s; agreement with
    the network is what makes a frame ``auto``.

    ``roi`` (x0, y0, x1, y1) looks only there. Over the whole frame, the
    cage's varnished beams pass the ratio test too (wood is a dull red) and
    the closing welds them to the disc; just around a detection, the disc's
    neighbours are the white sheet, and it comes out clean.

    ``open_px`` (with ``roi``) opens the mask with a disc that wide: the
    stand's red pole runs close past the sheet's edge and the closing can
    weld it to the disc by a thin bridge. Both are far narrower than the
    disc, so an opening a fraction of its size cuts them off."""
    ox = oy = 0
    if roi is not None:
        h, w = frame.shape[:2]
        ox, oy = max(0, int(roi[0])), max(0, int(roi[1]))
        frame = frame[oy:min(h, int(math.ceil(roi[3]))), ox:min(w, int(math.ceil(roi[2])))]
        if frame.size == 0:
            return []
    mask = chroma_mask(frame, local=roi is not None)
    if open_px >= 3:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_px, open_px))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE,
                                   offset=(ox, oy))
    out = []
    for c in contours:
        if cv2.contourArea(c) < min_area or len(c) < 5:
            continue
        circ, solid = roundness(c)
        _, _, bw, bh = cv2.boundingRect(c)
        if circ >= circ_min and solid >= solid_min and 0.35 < bw / bh < 2.8:
            out.append((c, circ, solid))
    out.sort(key=lambda t: -t[1] * t[2])
    return out


def _ellipse_outline(box, n: int = 72) -> np.ndarray:
    x0, y0, x1, y1 = box
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack(((x0 + x1) / 2 + (x1 - x0) / 2 * np.cos(t),
                            (y0 + y1) / 2 + (y1 - y0) / 2 * np.sin(t)))


def _hull5(contour: np.ndarray) -> np.ndarray:
    """The blob's hull for an ellipse fit, or the contour itself when the hull
    is too few points for one (a blob with straight sides)."""
    hull = cv2.convexHull(contour)
    return hull if len(hull) >= 5 else contour


def _blob_outline(contour: np.ndarray, n: int = 72) -> np.ndarray:
    (cx, cy), (a, b), angle = cv2.fitEllipse(_hull5(contour))
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return np.column_stack((cx + a / 2 * np.cos(t) * ca - b / 2 * np.sin(t) * sa,
                            cy + a / 2 * np.cos(t) * sa + b / 2 * np.sin(t) * ca))


def markerless_draft(frame: np.ndarray, model, iou_min: float, score_auto: float = 0.5):
    """(discs, status, info) for a frame of footage with no marker.

    The network's best detection is matched to the chroma blob whose fitted
    ellipse overlaps it most. The label is that ellipse when the blob is whole
    (its rim is pixel-exact, where the network's keypoints are a regression),
    else the network's own. ``auto`` needs a confident detection *and* a blob
    that agrees with it; everything else is ``pending``, drafted from whichever
    guess exists, for a human to settle -- including frames where neither
    found anything, which may yet hold a disc too dark for both."""
    h, w = frame.shape[:2]
    dets = model.detect(frame) if model is not None else []
    info: dict = {}
    if dets:
        d = dets[0]
        x0, y0, x1, y1 = d.box
        gx, gy = 0.125 * (x1 - x0), 0.125 * (y1 - y0)
        roi = (x0 - gx, y0 - gy, x1 + gx, y1 + gy)
        blobs_ = chroma_discs(frame, roi=roi) or chroma_discs(
            frame, roi=roi, open_px=int(0.12 * min(x1 - x0, y1 - y0)))
        target = _ellipse_outline(d.box)
        best, iou = None, 0.0
        for c, _, _ in blobs_:
            v = polygon_iou(_blob_outline(c), target, w, h)
            if v > iou:
                best, iou = c, v
        info.update(score=round(d.score, 3), iou=round(iou, 3))
        draft = None
        if best is not None and iou >= 0.6 and not touches_border(best, w, h):
            draft = ellipse_label(_hull5(best), w, h)
        if draft is None:
            draft = (d.box, d.keypoints_labelled())
        discs = [draft]
        # A second confident disc (two boards in shot) is drafted too, but
        # always looked at.
        extra = [(s.box, s.keypoints_labelled()) for s in dets[1:] if s.score >= score_auto]
        status = "auto" if d.score >= score_auto and iou >= iou_min and not extra else "pending"
        return discs + extra, status, info
    for c, circ, solid in chroma_discs(frame):
        if circ > 0.88 and solid > 0.92 and not touches_border(c, w, h):
            draft = ellipse_label(_hull5(c), w, h)
            if draft is not None:
                return [draft], "pending", info
    return [], "pending", info


# --- per-clip board estimate ------------------------------------------------------------


def estimate_board(video: Path, truth: MarkerTruth, every: int = 6):
    """The disc's centre (marker coordinates) and radius, metres, measured from
    the footage: medians over frames where the marker and a whole red disc are
    both in shot. Returns (offset, radius, frames used)."""
    radius0 = DISC_DIAMETER_M / 2
    offsets, radii = [], []
    for _, frame in read_video(video, every):
        pose = truth.pose(frame)
        if pose is None:
            continue
        h, w = frame.shape[:2]
        projected = project_disc(pose.rvec, pose.tvec, truth.calibration, w, h, radius0,
                                 DISC_OFFSET_M, min_inside=0.99)
        if projected is None:
            continue
        _, kpts, rim = projected
        radius_px = float(np.ptp(rim[:, 1])) / 2
        blob = nearest_blob(frame, kpts[CENTRE, :2], radius_px)
        if blob is None or touches_border(blob, w, h):
            continue
        circ, solid = roundness(blob)
        if circ < 0.8 or solid < 0.9:
            continue
        plane = truth.ray_onto_plane(frame, pose, blob.reshape(-1, 2).astype(np.float64))
        plane = plane[np.isfinite(plane).all(axis=1)]
        if len(plane) < 20:
            continue
        centre = plane.mean(axis=0)
        offsets.append(centre)
        radii.append(float(np.median(np.linalg.norm(plane - centre, axis=1))))
    if not offsets:
        return None, None, 0
    return np.median(np.array(offsets), axis=0), float(np.median(radii)), len(offsets)


def choose_board(offset, radius, used: int, tolerance: float = 0.15):
    """The footage's estimate when it agrees with the ruler, else the ruler."""
    measured_r = DISC_DIAMETER_M / 2
    measured_o = np.array(DISC_OFFSET_M)
    if offset is None or used < 10:
        return measured_o, measured_r, "ruler (too few frames to estimate)"
    if abs(radius - measured_r) / measured_r > tolerance or np.linalg.norm(
        offset - measured_o
    ) > tolerance * np.linalg.norm(measured_o):
        return measured_o, measured_r, "ruler (footage estimate disagrees)"
    return np.asarray(offset, float), float(radius), "footage"


# --- main --------------------------------------------------------------------------------


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sources", type=Path, nargs="+", help="videos or folders of videos")
    parser.add_argument("--out", type=Path, default=Path("solution_fire/data/real"))
    parser.add_argument("--every", type=int, default=3, help="keep every Nth frame")
    parser.add_argument("--min-change", type=float, default=3.0,
                        help="mean grey-level change from the last kept frame, else skipped")
    parser.add_argument("--chunk-s", type=float, default=8.0, help="seconds per session")
    parser.add_argument("--guard-s", type=float, default=0.5,
                        help="frames this close to a session cut are dropped")
    parser.add_argument("--iou-min", type=float, default=0.8)
    parser.add_argument("--empty", action="store_true",
                        help="the footage has no red disc: keep its frames as reviewed negatives")
    parser.add_argument("--calibration", type=Path, help="CameraCalibration JSON")
    parser.add_argument("--model", type=Path,
                        help="draft frames the marker cannot label with trained weights")
    parser.add_argument("--keep-marker", action="store_true",
                        help="do not paint the ArUco marker out (debugging only)")
    parser.add_argument("--markerless", action="store_true",
                        help="the boards carry no marker (the competition prints): draft "
                        "from --model and the red-chroma blob, paint nothing out")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    calibration = CameraCalibration.load(args.calibration) if args.calibration else (
        CameraCalibration())
    truth = MarkerTruth(calibration=calibration)
    model = None
    if args.model:
        from solution_fire.detector import FireDetector

        # Markerless drafting wants the network's weak guesses too: a 0.15
        # detection that a chroma blob agrees with is still a useful draft.
        model = FireDetector(args.model, calibration=calibration,
                             score_min=0.15 if args.markerless else 0.3)
    if args.markerless and args.empty:
        parser.error("--markerless is for footage with discs; --empty already needs no marker")
    rng = np.random.default_rng(args.seed)

    for sub in ("images", "labels", "meta"):
        (args.out / sub).mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.out)
    manifest.setdefault("clips", {})
    frames = manifest["frames"]
    known = {e["source"] for e in frames.values()}
    totals = {"kept": 0, "auto": 0, "pending": 0, "empty": 0, "rejected": 0, "alarms": 0}

    for video in find_videos(args.sources):
        clip = _slug(video.stem)
        fps = video_fps(video)
        offset, radius = np.array(DISC_OFFSET_M), DISC_DIAMETER_M / 2
        if args.markerless:
            manifest["clips"][clip] = {"source": "markerless"}
        elif not args.empty:
            est_o, est_r, used = estimate_board(video, truth)
            offset, radius, why = choose_board(est_o, est_r, used)
            manifest["clips"][clip] = {
                "offset_m": [float(v) for v in offset],
                "radius_m": radius,
                "source": why,
                "estimate_offset_m": None if est_o is None else [float(v) for v in est_o],
                "estimate_radius_m": est_r,
                "estimate_frames": used,
            }
            est = (f"offset ({est_o[0]:+.3f}, {est_o[1]:+.3f}) m, diameter {2 * est_r:.3f} m "
                   f"from {used} frames" if est_o is not None else "no estimate")
            print(f"{clip}: {est}; using {why}")
        last = None
        counts = {"auto": 0, "pending": 0, "empty": 0, "rejected": 0, "alarms": 0}
        for index, frame in read_video(video, max(1, args.every)):
            source = f"{video.as_posix()}#{index}"
            if source in known:
                continue
            chunk = chunk_of(index, fps, args.chunk_s, args.guard_s)
            if chunk is None:
                continue
            thumb = _thumb(frame)
            if last is not None and float(np.mean(np.abs(thumb - last))) < args.min_change:
                continue
            last = thumb
            session = f"{clip}_s{chunk}"
            stem = f"{clip}_{index:05d}"
            h, w = frame.shape[:2]
            entry = {"source": source, "session": session, "clip": clip, "size": [w, h]}
            discs = []
            if args.empty:
                counts["alarms"] += disc_like(frame) is not None
                status = "empty"
            elif args.markerless:
                discs, status, info = markerless_draft(frame, model, args.iou_min)
                entry.update(info)
            else:
                pose = truth.pose(frame)
                status = "pending"
                if pose is not None:
                    projected = project_disc(pose.rvec, pose.tvec, calibration, w, h, radius,
                                             offset)
                    meta = disc_truth(pose, offset)
                    meta["marker_side_px"] = pose.side_px
                    (args.out / "meta" / f"{stem}.json").write_text(json.dumps(meta, indent=1))
                    if projected is None:
                        # The marker is in shot but the disc is not: a true
                        # negative, labelled by geometry. A sliver of disc at
                        # the edge is neither a sighting nor a negative, so
                        # that frame is left out.
                        sliver = project_disc(pose.rvec, pose.tvec, calibration, w, h, radius,
                                              offset, min_inside=0.0)
                        status = "rejected" if sliver is not None else "empty"
                    else:
                        box, kpts, rim = projected
                        radius_px = max(float(np.ptp(rim[:, 1])), float(np.ptp(rim[:, 0]))) / 2
                        blob = nearest_blob(frame, kpts[CENTRE, :2], radius_px)
                        iou = 0.0 if blob is None else polygon_iou(rim, blob, w, h)
                        entry["iou"] = round(iou, 3)
                        discs = [(box, kpts)]
                        status = "auto" if iou >= args.iou_min else "pending"
                if pose is None:
                    if model is not None:
                        discs = [(s.box, s.keypoints_labelled()) for s in model.detect(frame)][:1]
                    else:
                        blob = disc_like(frame)
                        draft = None if blob is None else ellipse_label(blob, w, h)
                        discs = [] if draft is None else [draft]
            twins = [] if args.empty or args.markerless else twin_discs(frame, discs)
            if twins:
                discs = discs + twins
                entry["twins"] = len(twins)
                if status == "auto":
                    status = "pending"
            if args.markerless:
                entry["painted"] = "none"
            elif not args.keep_marker:
                found = truth.detect_all(frame)
                for corners in found:
                    paint_out(frame, corners, rng=rng)
                # ArUco lost the marker (blur, frame edge, or mirrored in a
                # reflection), but it is still under its disc: paint where the
                # board geometry puts it.
                for i, (_, kpts) in enumerate(discs):
                    if found and i == 0:
                        continue
                    quad = predicted_corners(kpts, radius, offset)
                    if quad is not None:
                        paint_out(frame, quad, grow=0.3, rng=rng)
                entry["painted"] = "marker" if found else "predicted" if discs else "none"
            cv2.imwrite(str(args.out / "images" / f"{stem}.jpg"), frame,
                        [cv2.IMWRITE_JPEG_QUALITY, 95])
            write_labels(args.out / "labels" / f"{stem}.txt", discs, w, h)
            entry["status"] = status
            frames[stem] = entry
            counts[status] = counts.get(status, 0) + 1
            if sum(counts[k] for k in ("auto", "pending", "empty")) % 100 == 0:
                save_manifest(args.out, manifest)
        save_manifest(args.out, manifest)
        kept = counts["auto"] + counts["pending"] + counts["empty"]
        line = f"{clip}: kept {kept} frames"
        if args.empty:
            line += f" as negatives; the colour drafter would have flagged {counts['alarms']}"
        else:
            line += (f": {counts['auto']} auto, {counts['pending']} pending, "
                     f"{counts['empty']} disc out of shot, {counts['rejected']} disc cut off")
        print(line)
        for k in totals:
            totals[k] += counts.get(k, 0)
        totals["kept"] += kept
    save_manifest(args.out, manifest)
    print(f"Done: {totals}. Next: python -m solution_fire.review {args.out}")


if __name__ == "__main__":
    main()
