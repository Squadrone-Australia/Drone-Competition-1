"""The learned marker finder: the ``solution_fire`` YOLO pose model, run by onnxruntime.

The colour detector (``detector.find_targets``) and this one are two answers to
the same question: "where are the red circles in this frame?". Both return the
same :class:`~comp1.vision.detector.Target` list, so the tracker, the
interpreter, the approach controller and the telemetry never know which one is
running. ``VisionConfig.detector`` picks between them.

Three rules shape this module.

**No torch, no ultralytics.** The model is trained in ``solution_fire/`` with
both, but the shipped program must not carry two gigabytes of either. The
weights are exported to ONNX once (see ``models/README.md``) and run here with
onnxruntime and a few lines of numpy. ``solution_fire`` is never imported.

**One camera model.** The network only finds the disc and its five keypoints.
Every metric answer -- bearing, elevation, range -- comes from
``cfg.intrinsics``, exactly as it does for the colour detector, so switching
detector never changes what "1.5 m away" means and the simulator, which shares
those intrinsics, stays consistent with the hardware.

**Unavailable is a state, not an error.** A checkout without onnxruntime, or an
install that has lost the model file, must still start and fly on colour.
:func:`available` answers that question without raising, and nothing imports
this module unless the AI detector has actually been chosen.
"""

from __future__ import annotations

import math
import os
import threading
from pathlib import Path

import cv2
import numpy as np

from .config import DEFAULT_CONFIG, VisionConfig
from .detector import Target

#: Shipped next to this file. ``Path(__file__)`` resolves inside the frozen
#: build's ``_internal`` tree too, because comp1.spec copies the folder there.
MODEL_PATH = Path(__file__).resolve().parent / "models" / "fire_pose.onnx"

# Keypoint layout, fixed by the training labels (solution_fire/geometry.py).
# Left and right are as the camera sees them.
CENTRE, TOP, RIGHT, BOTTOM, LEFT = range(5)
NUM_KPTS = 5

#: A keypoint is trusted for geometry only above this confidence. The same
#: threshold solution_fire's FireDetector uses, so range here matches the
#: numbers in its README.
KEYPOINT_MIN = 0.5
#: Ultralytics' own default; two discs closer than this overlap are one disc.
NMS_IOU = 0.7
#: The letterbox fill colour Ultralytics trains with.
PAD_VALUE = 114


def available(model_path: Path | None = None) -> tuple[bool, str | None]:
    """Can the AI detector run here? ``(True, None)`` or ``(False, why)``.

    Never raises: this is asked at startup and on every browser connect, and a
    broken optional dependency must cost the AI option, not the program.
    """
    path = Path(model_path or MODEL_PATH)
    if not path.is_file():
        return False, "the model file is missing from this install"
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False, "onnxruntime is not installed"
    return True, None


def letterbox(frame_bgr: np.ndarray, size: int) -> tuple[np.ndarray, float, float, float]:
    """Resize keeping aspect, pad to ``size`` square. Returns (image, scale, pad_x, pad_y).

    The same centred grey padding Ultralytics applies at training time; any
    other resize (a plain stretch, top-left padding) shifts every box and
    keypoint by a little and silently biases range.
    """
    h, w = frame_bgr.shape[:2]
    scale = min(size / h, size / w)
    new_w, new_h = round(w * scale), round(h * scale)
    pad_x, pad_y = (size - new_w) / 2, (size - new_h) / 2
    resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    top, left = round(pad_y - 0.1), round(pad_x - 0.1)
    out = cv2.copyMakeBorder(
        resized,
        top,
        size - new_h - top,
        left,
        size - new_w - left,
        cv2.BORDER_CONSTANT,
        value=(PAD_VALUE, PAD_VALUE, PAD_VALUE),
    )
    return out, scale, float(left), float(top)


def decode(
    raw: np.ndarray, score_min: float, scale: float, pad_x: float, pad_y: float
) -> list[tuple[float, np.ndarray, np.ndarray]]:
    """Raw head output -> [(score, box xyxy, keypoints (5, 3))] in frame pixels.

    ``raw`` is the model's ``(1, 4 + 1 + 15, anchors)`` tensor: box centre and
    size, the one class score, then x, y, confidence for each keypoint, all in
    letterboxed pixels. The export is not end-to-end, so overlapping anchors on
    the same disc are suppressed here.
    """
    rows = np.asarray(raw, np.float32).reshape(raw.shape[-2], raw.shape[-1]).T
    rows = rows[rows[:, 4] >= score_min]
    if not len(rows):
        return []
    cx, cy, bw, bh = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
    boxes_xywh = np.stack([cx - bw / 2, cy - bh / 2, bw, bh], axis=1)
    keep = cv2.dnn.NMSBoxes(
        boxes_xywh.tolist(), rows[:, 4].tolist(), score_min, NMS_IOU
    )
    keep = np.asarray(keep, int).reshape(-1)
    out = []
    for i in keep:
        x, y, w, h = boxes_xywh[i]
        box = np.array(
            [(x - pad_x) / scale, (y - pad_y) / scale,
             (x + w - pad_x) / scale, (y + h - pad_y) / scale],
            np.float64,
        )
        k = rows[i, 5 : 5 + NUM_KPTS * 3].reshape(NUM_KPTS, 3).astype(np.float64)
        k[:, 0] = (k[:, 0] - pad_x) / scale
        k[:, 1] = (k[:, 1] - pad_y) / scale
        out.append((float(rows[i, 4]), box, k))
    out.sort(key=lambda d: -d[0])
    return out


def _radius_px(box, k: np.ndarray, w: int, h: int) -> float:
    """Apparent disc radius in pixels, from the most trustworthy measurement.

    The top-bottom span first: turning the board about its vertical axis -- the
    usual way a drone sees it off-axis -- does not shrink it. Then left-right
    (an underestimate of size, so an overestimate of range, when the board is
    turned). Then the box height, unless the box is cut by the frame edge.
    """
    ok = (k[:, 2] >= KEYPOINT_MIN) & (k[:, 0] >= 0) & (k[:, 0] < w) & (k[:, 1] >= 0) & (
        k[:, 1] < h
    )
    if ok[TOP] and ok[BOTTOM]:
        span = math.dist(k[TOP, :2], k[BOTTOM, :2])
        if span > 1:
            return span / 2
    if ok[LEFT] and ok[RIGHT]:
        span = math.dist(k[LEFT, :2], k[RIGHT, :2])
        if span > 1:
            return span / 2
    x0, y0, x1, y1 = box
    # A box clipped by the frame is short, which would read as far away. Its
    # width is the better guess then, if that edge is clear.
    if y0 > 2 and y1 < h - 2:
        return max((y1 - y0) / 2, 0.5)
    return max((x1 - x0) / 2, (y1 - y0) / 2, 0.5)


def to_target(score: float, box, k: np.ndarray, w: int, h: int, cfg: VisionConfig) -> Target:
    """One network detection, expressed in the colour detector's units."""
    if k[CENTRE, 2] >= KEYPOINT_MIN:
        px, py = float(k[CENTRE, 0]), float(k[CENTRE, 1])
    else:
        px, py = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    # Plain floats: these end up in JSON telemetry, which numpy scalars break.
    cx, cy = float(px) / w, float(py) / h
    radius_norm = float(_radius_px(box, k, w, h)) / w
    cam = cfg.intrinsics
    bw, bh = max(box[2] - box[0], 0.0), max(box[3] - box[1], 0.0)
    if abs(cx - 0.5) < cfg.center_band / 2:
        pos = "center"
    else:
        pos = "left" if cx < 0.5 else "right"
    return Target(
        cx=cx,
        cy=cy,
        radius_norm=radius_norm,
        area_ratio=float(math.pi * float(bw) * float(bh) / 4 / (w * h)),
        # Not measured: the network decides "is this a disc", so the colour
        # detector's shape gates have nothing to say here.
        circularity=1.0,
        solidity=1.0,
        bearing_deg=cam.bearing_deg(cx),
        elevation_deg=cam.elevation_deg(cy, h / w),
        distance_m=cam.distance_m(radius_norm, cfg.marker_radius_m),
        position=pos,
        score=float(score),
    )


class YoloFinder:
    """The ONNX model, loaded on first use and safe to call from worker threads.

    ``session`` is injectable so the tests can drive the decoding with a fake
    and need neither onnxruntime nor the model file.
    """

    def __init__(self, model_path: Path | None = None, session=None):
        self._path = Path(model_path or MODEL_PATH)
        self._session = session
        self._lock = threading.Lock()

    def _get_session(self):
        if self._session is None:
            with self._lock:
                if self._session is None:
                    import onnxruntime as ort

                    opts = ort.SessionOptions()
                    # Half the cores: the video loop, the simulator's renderer
                    # and the browser all need the rest, and a 640 px nano
                    # model gains little past four threads anyway.
                    opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
                    self._session = ort.InferenceSession(
                        str(self._path), opts, providers=["CPUExecutionProvider"]
                    )
        return self._session

    def _input(self) -> tuple[str, int]:
        inp = self._get_session().get_inputs()[0]
        size = inp.shape[-1]
        return inp.name, int(size) if isinstance(size, int) else 640

    def detect(self, frame_bgr: np.ndarray, score_min: float):
        """Raw detections in frame pixels, best first. See :func:`decode`."""
        name, size = self._input()
        image, scale, pad_x, pad_y = letterbox(frame_bgr, size)
        blob = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.ascontiguousarray(blob.transpose(2, 0, 1)[None])
        raw = self._get_session().run(None, {name: blob})[0]
        return decode(raw, score_min, scale, pad_x, pad_y)

    def find_targets(self, frame_bgr: np.ndarray, cfg: VisionConfig = DEFAULT_CONFIG) -> list:
        """Every red circle above ``cfg.yolo_score_min``, nearest first."""
        h, w = frame_bgr.shape[:2]
        out = [
            to_target(score, box, k, w, h, cfg)
            for score, box, k in self.detect(frame_bgr, cfg.yolo_score_min)
        ]
        out.sort(key=lambda t: t.distance_m)
        return out


_shared: YoloFinder | None = None
_shared_lock = threading.Lock()


def shared() -> YoloFinder:
    """The process-wide finder. Loading the model costs a few hundred ms, and a
    new tracker is built on every reset and drone switch -- so it is loaded
    once, the first time the AI detector is used."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = YoloFinder()
        return _shared


def find_targets(frame_bgr: np.ndarray, cfg: VisionConfig = DEFAULT_CONFIG) -> list:
    """Same signature as ``detector.find_targets``, so the two are interchangeable."""
    return shared().find_targets(frame_bgr, cfg)
