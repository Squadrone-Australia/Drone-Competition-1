"""Export trained weights for faster or dependency-light inference.

    python -m solution_yolo.export solution_yolo/runs/gates/weights/best.pt --format onnx

``onnx`` runs anywhere onnxruntime does (CPU laptops included); ``engine`` is
TensorRT, the fastest on an NVIDIA GPU but tied to that machine's GPU and
TensorRT version. Both load through ``--yolo-weights`` like a ``.pt``.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("weights", type=Path)
    parser.add_argument("--format", default="onnx", choices=("onnx", "engine"))
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--half", action="store_true", help="FP16 (GPU formats)")
    parser.add_argument("--device", default=None)
    args = parser.parse_args(argv)

    from ultralytics import YOLO

    path = YOLO(str(args.weights)).export(
        format=args.format, imgsz=args.imgsz, half=args.half, device=args.device
    )
    print(f"Exported {path}")


if __name__ == "__main__":
    main()
