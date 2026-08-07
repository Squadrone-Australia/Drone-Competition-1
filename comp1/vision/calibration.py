"""Guided HSV calibration for the browser tuning panel."""

from dataclasses import replace
from math import atan2, pi
from collections.abc import Mapping

import cv2
import numpy as np

from .config import VisionConfig
from .detector import color_mask, detect_red_circle, draw_overlay

HSV_KEYS = ("lower1", "upper1", "lower2", "upper2")


class CalibrationError(ValueError):
    """An operator-facing calibration validation error."""


def hsv_values(cfg: VisionConfig) -> dict[str, list[int]]:
    """The editable colour portion of a vision config, ready for JSON."""
    return {key: [int(value) for value in getattr(cfg, key)] for key in HSV_KEYS}


def config_with_hsv(cfg: VisionConfig, values: Mapping) -> VisionConfig:
    """Return ``cfg`` with validated OpenCV HSV bounds."""
    if not isinstance(values, Mapping):
        raise CalibrationError("vision config must be an object")
    parsed: dict[str, tuple[int, int, int]] = {}
    for key in HSV_KEYS:
        raw = values.get(key)
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise CalibrationError(f"{key} must contain three numbers")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               for value in raw):
            raise CalibrationError(f"{key} must contain three numbers")
        triplet = tuple(int(round(value)) for value in raw)
        if not 0 <= triplet[0] <= 180:
            raise CalibrationError(f"{key} hue must be between 0 and 180")
        if any(not 0 <= value <= 255 for value in triplet[1:]):
            raise CalibrationError(f"{key} saturation/value must be between 0 and 255")
        parsed[key] = triplet
    for suffix in ("1", "2"):
        lower, upper = parsed[f"lower{suffix}"], parsed[f"upper{suffix}"]
        if any(lo > hi for lo, hi in zip(lower, upper)):
            raise CalibrationError(f"lower{suffix} cannot exceed upper{suffix}")
    return replace(cfg, **parsed)


def _circular_hue_mean(hues: np.ndarray, saturation: np.ndarray) -> float:
    """Mean on OpenCV's circular 0..179 hue axis, weighted by saturation."""
    angles = hues.astype(np.float64) * (2 * pi / 180)
    weights = saturation.astype(np.float64) / 255
    x = float(np.sum(np.cos(angles) * weights))
    y = float(np.sum(np.sin(angles) * weights))
    return (atan2(y, x) * 180 / (2 * pi)) % 180


def suggest_hsv(frame_bgr: np.ndarray, roi: list[float] | tuple[float, ...]) -> dict[str, list[int]]:
    """Suggest robust HSV bands from a normalised marker selection.

    Hue percentiles are calculated after unwrapping around the circular mean,
    so a red sample spanning 179 -> 0 becomes two compact ranges instead of
    almost the entire hue axis.  Low-saturation and very dark pixels are
    ignored because their hue is unstable and usually comes from glare,
    shadows, or selection background.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        raise CalibrationError("no camera frame is available")
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        raise CalibrationError("select a rectangular marker region")
    try:
        x0, y0, x1, y1 = (float(value) for value in roi)
    except (TypeError, ValueError) as exc:
        raise CalibrationError("marker region coordinates must be numbers") from exc
    if not all(np.isfinite([x0, y0, x1, y1])):
        raise CalibrationError("marker region coordinates must be finite")
    x0, x1 = sorted((min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)))
    y0, y1 = sorted((min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)))
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        raise CalibrationError("select a larger region inside the marker")

    height, width = frame_bgr.shape[:2]
    left, right = int(x0 * width), max(int(np.ceil(x1 * width)), 1)
    top, bottom = int(y0 * height), max(int(np.ceil(y1 * height)), 1)
    crop = frame_bgr[top:bottom, left:right]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    usable = hsv[(hsv[:, 1] >= 40) & (hsv[:, 2] >= 30)]
    if usable.shape[0] < max(25, hsv.shape[0] // 20):
        raise CalibrationError(
            "the selection has too little colour; avoid white glare and dark shadow")

    hues, saturation, value = usable[:, 0], usable[:, 1], usable[:, 2]
    mean = _circular_hue_mean(hues, saturation)
    offsets = ((hues.astype(np.float64) - mean + 90) % 180) - 90
    # Ignore pixels far from the dominant colour before finding the band. This
    # tolerates a loose selection containing some floor or wall background.
    clustered = np.abs(offsets) <= 30
    if int(np.count_nonzero(clustered)) < 25:
        raise CalibrationError("the selection does not contain one clear colour")
    offsets = offsets[clustered]
    saturation = saturation[clustered]
    value = value[clustered]

    low = int(np.floor(mean + np.percentile(offsets, 2) - 2))
    high = int(np.ceil(mean + np.percentile(offsets, 98) + 2))
    sat_min = max(0, int(np.percentile(saturation, 5)) - 20)
    value_min = max(0, int(np.percentile(value, 5)) - 20)

    if low < 0:
        bands = ((0, min(high, 180)), (max(0, low + 180), 180))
    elif high > 180:
        bands = ((0, min(180, high - 180)), (max(0, low), 180))
    else:
        bands = ((max(0, low), min(180, high)),) * 2
    return {
        "lower1": [bands[0][0], sat_min, value_min],
        "upper1": [bands[0][1], 255, 255],
        "lower2": [bands[1][0], sat_min, value_min],
        "upper2": [bands[1][1], 255, 255],
    }


def draw_calibration_preview(frame_bgr: np.ndarray, cfg: VisionConfig) -> np.ndarray:
    """Dim rejected pixels and tint accepted pixels for visual verification."""
    mask = color_mask(frame_bgr, cfg)
    preview = (frame_bgr.astype(np.float32) * 0.25).astype(np.uint8)
    accepted = frame_bgr.copy()
    tint = np.full_like(accepted, (0, 210, 255))
    accepted = cv2.addWeighted(accepted, 0.45, tint, 0.55, 0)
    preview[mask > 0] = accepted[mask > 0]
    return draw_overlay(preview, detect_red_circle(frame_bgr, cfg))
