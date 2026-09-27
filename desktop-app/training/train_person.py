"""Train a one-class YOLO person detector for 云瞳.

Training is opt-in: this file only starts work when the user runs it directly.
It deliberately refuses to fall back to CPU when a CUDA device was requested.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = PROJECT_ROOT / "training" / "datasets" / "coco-person-2017" / "data.yaml"
DEFAULT_PROJECT = PROJECT_ROOT / "training" / "runs"


def resolve_project_path(value: Path) -> Path:
    value = value.expanduser()
    return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()


def require_cuda(device: str) -> None:
    if device.strip().lower() == "cpu":
        print("警告：正在显式使用 CPU。COCO 规模的正式训练会非常慢。", file=sys.stderr)
        return

    try:
        import torch
    except ImportError as exc:
        raise SystemExit(
            "错误：未安装 PyTorch。请先按照 TRAINING_GUIDE.md 安装 CUDA 版 PyTorch。"
        ) from exc

    if not torch.cuda.is_available():
        raise SystemExit(
            "错误：参数要求使用 CUDA device=0，但当前 Python 环境的 "
            "torch.cuda.is_available() 为 False。程序不会静默退回 CPU。\n"
            "请运行 python training/check_training_env.py 检查环境，或在确认接受极慢速度后显式传 --device cpu。"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="训练云瞳 person 单类别 YOLO 检测模型")
    parser.add_argument("--model", default="yolo26n.pt", help="初始权重或模型名称")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="YOLO data.yaml")
    parser.add_argument("--epochs", type=int, default=50, help="训练轮数，默认 50")
    parser.add_argument("--batch", type=int, default=16, help="批大小，默认 16")
    parser.add_argument("--workers", type=int, default=4, help="数据加载进程数，默认 4")
    parser.add_argument("--device", default="0", help="默认 0；显式 CPU 请传 cpu")
    parser.add_argument("--imgsz", type=int, default=640, help="输入尺寸，默认 640")
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT, help="训练输出根目录")
    parser.add_argument("--name", default="person-yolo26n-v1", help="本次运行名称")
    parser.add_argument("--patience", type=int, default=20, help="早停等待轮数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="冒烟模式：epochs=1、fraction=0.01、workers=0、batch=8",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        help="从指定 last.pt 断点续训；断点中的训练参数优先",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    require_cuda(args.device)

    data_file = resolve_project_path(args.data)
    if not data_file.is_file():
        raise SystemExit(f"错误：找不到数据集配置：{data_file}")
    project_dir = resolve_project_path(args.project)

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "错误：未安装 ultralytics。请先按 TRAINING_GUIDE.md 安装训练依赖。"
        ) from exc

    if args.resume:
        resume_file = resolve_project_path(args.resume)
        if not resume_file.is_file():
            raise SystemExit(f"错误：找不到断点权重：{resume_file}")
        print(f"从断点继续：{resume_file}")
        model = YOLO(str(resume_file))
        model.train(resume=True, device=args.device)
        return 0

    epochs = 1 if args.smoke else args.epochs
    fraction = 0.01 if args.smoke else 1.0
    workers = 0 if args.smoke else args.workers
    batch = 8 if args.smoke else args.batch
    run_name = f"{args.name}-smoke" if args.smoke and not args.name.endswith("-smoke") else args.name

    print("=== 云瞳 person 模型训练 ===")
    print(f"模式：{'冒烟训练' if args.smoke else '正式训练'}")
    print(f"模型：{args.model}")
    print(f"数据：{data_file}")
    print(f"epochs={epochs}, batch={batch}, workers={workers}, fraction={fraction}")
    print(f"device={args.device}, imgsz={args.imgsz}")
    print(f"输出：{project_dir / run_name}")

    model = YOLO(args.model)
    model.train(
        data=str(data_file),
        epochs=epochs,
        batch=batch,
        workers=workers,
        device=args.device,
        imgsz=args.imgsz,
        fraction=fraction,
        project=str(project_dir),
        name=run_name,
        patience=args.patience,
        seed=args.seed,
        deterministic=True,
        plots=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
