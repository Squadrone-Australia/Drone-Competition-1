"""Merge synthetic and reviewed real frames into one YOLO pose dataset.

    python -m solution_yolo.build_dataset --synth solution_yolo/data/synth \
        --real solution_yolo/data/real --out solution_yolo/data/gates

Writes ``images/<split>/`` and ``labels/<split>/`` (hard links where the disk
allows, else copies) plus ``data.yaml`` for training and ``data_real.yaml``,
whose validation and test sets are real frames only: the number that says
whether the model will fly.

* Synthetic frames split at random: ``val_sim`` and ``test_sim`` (with their
  ground-truth ``meta`` for range scoring) and the rest to ``train``.
* Real frames split **by session** (one video, or one folder of stills), so
  near-identical neighbours never sit either side of the line, and only once
  reviewed (``accepted`` or ``empty``; ``--include-auto`` also takes drafts).
  Real training frames are repeated ``--real-repeat`` times, so a few hundred
  of them still count against tens of thousands of renders.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import random
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution_yolo.keypoints import CLASS_NAMES, FLIP_IDX, KPT_SHAPE  # noqa: E402
from solution_yolo.real_label import load_manifest  # noqa: E402

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")


def _place(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _add(out: Path, split: str, image: Path, label: Path | None, name: str) -> None:
    _place(image, out / "images" / split / f"{name}{image.suffix}")
    dst = out / "labels" / split / f"{name}.txt"
    if label is not None and label.exists():
        _place(label, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("")  # a background image


def session_split(session: str, val: float, test: float) -> str:
    """A stable split for a session, from its name alone."""
    u = int(hashlib.sha1(session.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test_real" if u < test else "val_real" if u < test + val else "train"


def _yaml(out: Path, train: list[str], val: list[str], test: list[str]) -> str:
    def seq(items):
        return "[" + ", ".join(items) + "]"

    lines = [
        f"path: {out.resolve().as_posix()}",
        f"train: {seq(train)}",
        f"val: {seq(val)}",
    ]
    if test:
        lines.append(f"test: {seq(test)}")
    lines += [
        f"kpt_shape: [{KPT_SHAPE[0]}, {KPT_SHAPE[1]}]",
        f"flip_idx: {list(FLIP_IDX)}",
        "names:",
        *(f"  {k}: {v}" for k, v in CLASS_NAMES.items()),
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--synth", type=Path, action="append", default=[])
    parser.add_argument("--real", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, default=Path("solution_yolo/data/gates"))
    parser.add_argument("--sim-val", type=float, default=0.06)
    parser.add_argument("--sim-test", type=float, default=0.04)
    parser.add_argument("--real-val", type=float, default=0.2)
    parser.add_argument("--real-test", type=float, default=0.15)
    parser.add_argument("--real-repeat", type=int, default=5)
    parser.add_argument(
        "--val-sessions",
        nargs="+",
        default=[],
        metavar="PATTERN",
        help="real sessions (glob on the session name) held out for validation; "
        "naming them keeps lighting and clip types balanced, which a hash of "
        "the name does not",
    )
    parser.add_argument("--test-sessions", nargs="+", default=[], metavar="PATTERN")
    parser.add_argument(
        "--include-auto",
        action="store_true",
        help="also use unreviewed drafts (quick experiments only)",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--force", action="store_true", help="replace an existing --out folder"
    )
    args = parser.parse_args(argv)
    if not args.synth and not args.real:
        raise SystemExit("Give at least one --synth or --real folder")
    if args.out.exists() and any(args.out.iterdir()):
        if not args.force:
            raise SystemExit(f"{args.out} is not empty; pass --force to rebuild it")
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    counts: dict[str, int] = {}

    for root in args.synth:
        images = sorted(
            p for p in (root / "images").iterdir() if p.suffix in IMAGE_SUFFIXES
        )
        for image in images:
            u = rng.random()
            split = (
                "test_sim"
                if u < args.sim_test
                else "val_sim"
                if u < args.sim_test + args.sim_val
                else "train"
            )
            name = f"{root.name}_{image.stem}"
            _add(args.out, split, image, root / "labels" / f"{image.stem}.txt", name)
            meta = root / "meta" / f"{image.stem}.json"
            if split != "train" and meta.exists():
                _place(meta, args.out / "meta" / split / f"{name}.json")
            counts[split] = counts.get(split, 0) + 1

    usable = {"accepted", "empty"} | ({"auto"} if args.include_auto else set())
    for root in args.real:
        frames = load_manifest(root)["frames"]
        sessions = sorted({e["session"] for e in frames.values()})
        named = args.val_sessions or args.test_sessions
        splits = {}
        for s in sessions:
            if any(fnmatch.fnmatch(s, pat) for pat in args.test_sessions):
                splits[s] = "test_real"
            elif any(fnmatch.fnmatch(s, pat) for pat in args.val_sessions):
                splits[s] = "val_real"
            else:
                splits[s] = (
                    "train"
                    if named
                    else session_split(s, args.real_val, args.real_test)
                )
        # Two or more sessions: make sure validation gets one.
        if not named and len(sessions) > 1 and "val_real" not in splits.values():
            splits[sessions[-1]] = "val_real"
        held = sorted(s for s in sessions if splits[s] != "train")
        if held:
            print(f"{root}: held out {', '.join(f'{s} ({splits[s]})' for s in held)}")
        for stem, entry in sorted(frames.items()):
            if entry["status"] not in usable:
                continue
            split = splits[entry["session"]]
            image = root / "images" / f"{stem}.jpg"
            label = root / "labels" / f"{stem}.txt"
            if entry["status"] == "empty":
                label = None
            repeats = args.real_repeat if split == "train" else 1
            for r in range(repeats):
                suffix = f"_r{r}" if r else ""
                _add(args.out, split, image, label, f"real_{stem}{suffix}")
            counts[split] = counts.get(split, 0) + repeats

    present = [s for s in counts if counts[s]]
    if "train" not in present:
        raise SystemExit("No training images: check the sources and their status")
    val = [f"images/{s}" for s in ("val_sim", "val_real") if s in present]
    test = [f"images/{s}" for s in ("test_sim", "test_real") if s in present]
    if not val:
        raise SystemExit("No validation images: add more frames or sessions")
    (args.out / "data.yaml").write_text(_yaml(args.out, ["images/train"], val, test))
    if "val_real" in present:
        real_test = ["images/test_real"] if "test_real" in present else []
        (args.out / "data_real.yaml").write_text(
            _yaml(args.out, ["images/train"], ["images/val_real"], real_test)
        )
    (args.out / "summary.json").write_text(json.dumps(counts, indent=1))
    print(json.dumps(counts, indent=1))
    print(f"Wrote {args.out / 'data.yaml'}")


if __name__ == "__main__":
    main()
