"""Train the YOLO26 pose model on a dataset from ``build_dataset``.

    python -m solution_fire.train --data solution_fire/data/fire/data.yaml \
        --model yolo26s-pose.pt --name fire_s
    # fine-tune after another round of footage, at a lower rate
    python -m solution_fire.train --data solution_fire/data/fire2/data.yaml \
        --model solution_fire/runs/fire_s/weights/best.pt --lr0 0.002 --epochs 60

The pose weights shipped with Ultralytics have a 17-keypoint head (people);
starting from them keeps the backbone and re-initialises the head for this
dataset's five keypoints.

Augmentation is chosen for this target. **Hue jitter is tiny**: red against
green is exactly the decision the model is for, and a wide hue swing would
turn a green decoy into a red training example. Saturation and brightness
jitter is wide, because venue lighting and the Tello's auto-exposure both
swing. Left-right flips swap the left and right keypoints through
``flip_idx``. Up-down flips are off (a Tello is level and the stand is under
the print), and rotation is small for the same reason.

Adapted from ``solution_yolo/train.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, required=True, help="data.yaml")
    parser.add_argument("--model", default="yolo26s-pose.pt",
                        help="starting weights: yolo26{n,s,m}-pose.pt or a best.pt")
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=float, default=-1,
                        help="-1 sizes it to the GPU's memory")
    parser.add_argument("--device", default="0", help="0 for the first GPU, or cpu")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--project", type=Path, default=Path("solution_fire/runs"))
    parser.add_argument("--name", default="fire")
    parser.add_argument("--lr0", type=float, help="initial learning rate")
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--fraction", type=float, default=1.0, help="share of train")
    parser.add_argument("--cache", choices=("ram", "disk", "none"), default="none")
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
        hsv_h=0.005,
        hsv_s=0.5,
        hsv_v=0.45,
        degrees=5.0,
        translate=0.15,
        scale=0.5,
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
