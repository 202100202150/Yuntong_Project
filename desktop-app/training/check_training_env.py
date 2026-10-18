"""Read-only readiness check for 云瞳 person-model training.

This script never installs packages, changes files, or starts training.
It checks the active Python interpreter, key package versions, CUDA visibility,
and the local COCO Person dataset layout.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = PROJECT_ROOT / "training" / "datasets" / "coco-person-2017" / "data.yaml"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
REQUIRED_PACKAGES = (
    "torch",
    "torchvision",
    "ultralytics",
    "onnx",
    "onnxslim",
    "onnxruntime",
)


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def count_files(directory: Path, suffixes: set[str] | None = None) -> int:
    if not directory.is_dir():
        return -1
    if suffixes is None:
        return sum(1 for item in directory.iterdir() if item.is_file())
    return sum(
        1
        for item in directory.iterdir()
        if item.is_file() and item.suffix.lower() in suffixes
    )


def load_summary(data_file: Path) -> dict:
    summary_file = data_file.parent / "download-summary.json"
    if not summary_file.is_file():
        return {}
    try:
        return json.loads(summary_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def inspect_dataset(data_file: Path) -> tuple[bool, list[str]]:
    messages: list[str] = []
    if not data_file.is_file():
        return False, [f"[失败] 找不到 data.yaml：{data_file}"]

    text = data_file.read_text(encoding="utf-8")
    if "0: person" not in text:
        messages.append("[失败] data.yaml 未声明唯一类别 0: person。")
        valid = False
    else:
        messages.append("[通过] 类别映射：0 = person")
        valid = True

    dataset_root = data_file.parent
    summary = load_summary(data_file)
    expected = {
        "train": summary.get("train", {}).get("images", 64115),
        "val": summary.get("val", {}).get("images", 2693),
    }

    for split in ("train", "val"):
        image_count = count_files(dataset_root / "images" / split, IMAGE_SUFFIXES)
        label_count = count_files(dataset_root / "labels" / split, {".txt"})
        target = expected[split]
        if image_count == target and label_count == target:
            messages.append(
                f"[通过] {split}: {image_count:,} 张图片 / {label_count:,} 个标签"
            )
        else:
            valid = False
            messages.append(
                f"[失败] {split}: 图片 {image_count:,}，标签 {label_count:,}，预期各 {target:,}"
            )

    test_images = dataset_root / "images" / "test"
    test_labels = dataset_root / "labels" / "test"
    if not test_images.exists() and not test_labels.exists() and "test:" not in text:
        messages.append("[提示] 当前官方子集只有 train/val，没有独立 test 集。")
    else:
        messages.append("[提示] 检测到 test 配置或目录，请自行确认其来源和划分方式。")

    return valid, messages


def inspect_nvidia_driver() -> str:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return "[警告] PATH 中找不到 nvidia-smi，无法检查 NVIDIA 驱动。"
    try:
        completed = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return f"[通过] NVIDIA 驱动：{completed.stdout.strip()}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"[警告] nvidia-smi 执行失败：{exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查云瞳 person 模型训练环境")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="YOLO data.yaml 路径")
    parser.add_argument(
        "--device",
        default="0",
        help="计划使用的训练设备；默认 0。只检查 CPU 时可传 cpu。",
    )
    args = parser.parse_args()

    print("=== 云瞳训练环境只读检查 ===")
    print(f"项目目录：{PROJECT_ROOT}")
    print(f"Python：{sys.version.split()[0]} ({sys.executable})")
    print(f"系统：{platform.platform()}")
    if sys.version_info[:3] == (3, 12, 13):
        print("[通过] Python 版本为推荐的 3.12.13。")
    elif sys.version_info[:2] == (3, 12):
        print("[提示] 当前为 Python 3.12，可用；教程基准版本是 3.12.13。")
    else:
        print("[警告] 推荐使用项目根目录 .venv-train 中的 Python 3.12.13。")

    print("\n--- Python 依赖 ---")
    missing: list[str] = []
    for package in REQUIRED_PACKAGES:
        version = package_version(package)
        if version is None:
            missing.append(package)
            print(f"[缺少] {package}")
        else:
            print(f"[已安装] {package} {version}")

    print("\n--- GPU / CUDA ---")
    print(inspect_nvidia_driver())
    cuda_available = False
    try:
        import torch

        cuda_available = torch.cuda.is_available()
        print(f"PyTorch CUDA 构建：{torch.version.cuda or '无（CPU 构建）'}")
        print(f"torch.cuda.is_available()：{cuda_available}")
        if cuda_available:
            for index in range(torch.cuda.device_count()):
                print(f"[通过] CUDA {index}: {torch.cuda.get_device_name(index)}")
    except Exception as exc:  # Import/runtime problems must be displayed, not hidden.
        print(f"[失败] 无法导入或检查 torch：{exc}")

    wants_cuda = str(args.device).lower() != "cpu"
    cuda_ok = cuda_available or not wants_cuda
    if wants_cuda and not cuda_available:
        print("[失败] 计划使用 CUDA device=0，但当前解释器无法使用 CUDA。")
    elif not wants_cuda:
        print("[提示] 已选择 CPU 检查；CPU 正式训练会非常慢。")

    print("\n--- COCO Person 数据集 ---")
    data_file = args.data.expanduser()
    if not data_file.is_absolute():
        data_file = (PROJECT_ROOT / data_file).resolve()
    dataset_ok, dataset_messages = inspect_dataset(data_file)
    print(f"data.yaml：{data_file}")
    for message in dataset_messages:
        print(message)

    ready = not missing and cuda_ok and dataset_ok
    print("\n--- 结论 ---")
    if ready:
        print("[就绪] 环境、CUDA 和数据集检查通过，可以先执行冒烟训练。")
        return 0

    print("[未就绪] 请按教程修复上面的缺失项；本脚本没有安装或修改任何内容。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
