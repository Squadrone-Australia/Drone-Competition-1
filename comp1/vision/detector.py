from dataclasses import dataclass

import cv2
import numpy as np

from .config import VisionConfig, DEFAULT_CONFIG


@dataclass
class Detection:
    found: bool
    cx: float = 0.5
    area_ratio: float = 0.0
    position: str = "none"


def detect_red_circle(frame_bgr: np.ndarray, cfg: VisionConfig = DEFAULT_CONFIG) -> Detection:
    h, w = frame_bgr.shape[:2]
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(cfg.lower1), np.array(cfg.upper1)) | \
           cv2.inRange(hsv, np.array(cfg.lower2), np.array(cfg.upper2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        area = cv2.contourArea(c)
        if area / (w * h) < cfg.min_area_ratio:
            continue
        perim = cv2.arcLength(c, True)
        if perim == 0:
            continue
        circularity = 4 * np.pi * area / (perim * perim)
        if circularity < cfg.circularity_min:
            continue
        if best is None or area > cv2.contourArea(best):
            best = c
    if best is None:
        return Detection(found=False)
    m = cv2.moments(best)
    cx = m["m10"] / m["m00"] / w
    area_ratio = cv2.contourArea(best) / (w * h)
    if abs(cx - 0.5) < cfg.center_band / 2:
        pos = "center"
    else:
        pos = "left" if cx < 0.5 else "right"
    return Detection(found=True, cx=cx, area_ratio=area_ratio, position=pos)


def draw_overlay(frame_bgr: np.ndarray, det: Detection) -> np.ndarray:
    out = frame_bgr.copy()
    label = f"VICTIM {det.position} ({det.area_ratio:.1%})" if det.found else "searching..."
    color = (0, 255, 0) if det.found else (0, 165, 255)
    cv2.putText(out, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    if det.found:
        x = int(det.cx * out.shape[1])
        cv2.line(out, (x, 0), (x, out.shape[0]), color, 2)
    return out
