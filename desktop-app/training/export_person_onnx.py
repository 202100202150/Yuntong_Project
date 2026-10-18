"""Export a trained person-only YOLO checkpoint to ONNX and record SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = PROJECT_ROOT / "training" / "runs" / "person-yolo26n-v1" / "weights" / "best.pt"
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "person-v1.onnx"


def resolve_project_path(value: Path) -> Path:
    value = value.expanduser()
    return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将 person best.pt 导出为 ONNX")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS, help="输入 best.pt")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="目标 ONNX 文件")
    parser.add_argument("--imgsz", type=int, default=640, help="导出输入尺寸，默认 640")
    parser.add_argument("--opset", type=int, default=17, help="ONNX opset，默认 17")
    parser.add_argument(
        "--no-simplify",
        action="store_true",
        help="关闭 ONNX simplify；默认启用",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    weights = resolve_project_path(args.weights)
    output = resolve_project_path(args.output)
    if not weights.is_file():
        raise SystemExit(f"错误：找不到训练权重：{weights}")

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "错误：未安装 ultralytics。请先按 TRAINING_GUIDE.md 安装导出依赖。"
        ) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(weights))
    exported = model.export(
        format="onnx",
        imgsz=args.imgsz,
        opset=args.opset,
        simplify=not args.no_simplify,
        dynamic=False,
        device="cpu",
    )

    exported_path = Path(str(exported)).resolve()
    if not exported_path.is_file():
        raise SystemExit(f"错误：Ultralytics 未返回可用的 ONNX 文件：{exported_path}")
    if exported_path != output:
        shutil.copy2(exported_path, output)

    digest = sha256_file(output)
    hash_file = output.with_suffix(".sha256.txt")
    hash_file.write_text(f"{digest}  {output.name}\n", encoding="utf-8")

    print("=== ONNX 导出完成 ===")
    print(f"模型：{output}")
    print(f"SHA-256：{digest}")
    print(f"哈希文件：{hash_file}")
    print("提示：脚本没有假定或硬编码模型输出张量形状；接入时必须按实际导出模型检查。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
