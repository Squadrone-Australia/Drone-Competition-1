"""Train the YOLO26 pose model on a dataset from ``build_dataset``.

    # stage 1: synthetic frames (plus any reviewed real ones)
    python -m solution_yolo.train --data solution_yolo/data/gates/data.yaml
    # stage 2: fine-tune on a set with more real frames, at a lower rate
    python -m solution_yolo.train --data solution_yolo/data/gates_real/data.yaml \
        --model solution_yolo/runs/stage1/weights/best.pt --lr0 0.002 --epochs 60

Augmentation is chosen for this ring. Hue jitter is kept small because the
fabric's red-orange against the checker is the cue. Saturation and brightness
jitter is wide, because venue lighting and the Tello's auto-exposure swing
both. Left-right flips swap keypoint identities through ``flip_idx``.
Up-down flips are off, because the print and the stand make the ring's top
and bottom different. Rotation is small, because a Tello is near level.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, required=True, help="data.yaml")
    parser.add_argument(
        "--model",
        default="yolo26s-pose.pt",
        help="starting weights: yolo26{n,s,m}-pose.pt (downloaded) or a best.pt",
    )
    parser.add_argument("--epochs", type=int, default=150)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--batch", type=float, default=-1, help="-1 sizes it to the GPU's memory"
    )
    parser.add_argument("--device", default="0", help="0 for the first GPU, or cpu")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--project", type=Path, default=Path("solution_yolo/runs"))
    parser.add_argument("--name", default="gates")
    parser.add_argument("--lr0", type=float, help="initial learning rate")
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--fraction", type=float, default=1.0, help="share of train")
    parser.add_argument(
        "--cache", choices=("ram", "disk", "none"), default="none", help="image cache"
    )
    parser.add_argument("--resume", action="store_true", help="resume --model last.pt")
    args = parser.parse_args(argv)

    from ultralytics import YOLO

    model = YOLO(args.model)
    if args.resume:
        model.train(resume=True)
        return
    batch = int(args.batch) if args.batch >= 1 or args.batch == -1 else args.batch
    options = dict(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=batch,
        device=args.device,
        workers=args.workers,
        project=str(args.project.resolve()),
        name=args.name,
        patience=args.patience,
        fraction=args.fraction,
        cache=False if args.cache == "none" else args.cache,
        # Augmentation for this target (see the module docstring).
        hsv_h=0.008,
        hsv_s=0.5,
        hsv_v=0.45,
        degrees=4.0,
        translate=0.15,
        scale=0.45,
        shear=0.0,
        perspective=0.0002,
        fliplr=0.5,
        flipud=0.0,
        mosaic=1.0,
        close_mosaic=10,
        mixup=0.0,
        exist_ok=False,
        plots=True,
    )
    if args.lr0 is not None:
        options["lr0"] = args.lr0
    results = model.train(**options)
    save_dir = Path(getattr(results, "save_dir", "") or model.trainer.save_dir)
    print(f"Best weights: {save_dir / 'weights' / 'best.pt'}")


if __name__ == "__main__":
    main()
