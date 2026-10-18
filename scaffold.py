# scaffold.py
# 云瞳 · 前后端分离目录脚手架（Node.js + Vue 3）
# 只创建目录和空文件，不写入任何内容，不安装依赖

from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---------------- 后端 ----------------
BACKEND_DIRS = [
    "backend",
    "backend/src",
    "backend/src/sidecar",
    "backend/src/routes",
    "backend/src/ws",
    "backend/src/normalize",
]
BACKEND_FILES = [
    "backend/package.json",
    "backend/.env",
    "backend/src/index.mjs",
    "backend/src/sidecar/client.mjs",
    "backend/src/sidecar/manager.mjs",
    "backend/src/routes/status.mjs",
    "backend/src/routes/frame.mjs",
    "backend/src/routes/detection.mjs",
    "backend/src/routes/recording.mjs",
    "backend/src/routes/snapshots.mjs",
    "backend/src/ws/frame-stream.mjs",
    "backend/src/normalize/status.mjs",
]

# ---------------- 前端 ----------------
FRONTEND_DIRS = [
    "frontend",
    "frontend/public",
    "frontend/src",
    "frontend/src/api",
    "frontend/src/stores",
    "frontend/src/router",
    "frontend/src/views",
    "frontend/src/components",
    "frontend/src/styles",
    "frontend/src/types",
    "frontend/src/utils",
]
FRONTEND_FILES = [
    "frontend/package.json",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/index.html",
    "frontend/.env.development",
    "frontend/.env.production",
    "frontend/src/main.ts",
    "frontend/src/App.vue",
    "frontend/src/vite-env.d.ts",
    "frontend/src/api/http.ts",
    "frontend/src/api/camera-bridge.ts",
    "frontend/src/stores/camera.ts",
    "frontend/src/router/index.ts",
    "frontend/src/views/DualCameraView.vue",
    "frontend/src/views/AnnotationView.vue",
    "frontend/src/components/SpatialScene.vue",
    "frontend/src/components/CameraPanel.vue",
    "frontend/src/components/ToastStack.vue",
    "frontend/src/styles/desktop.css",
    "frontend/src/styles/dual-camera-workspace.css",
    "frontend/src/styles/annotation-workspace.css",
    "frontend/src/styles/spatial-scene.css",
    "frontend/src/types/camera-types.ts",
    "frontend/src/utils/format.ts",
]


def ensure_dir(relative_path: str) -> None:
    target = ROOT / relative_path
    target.mkdir(parents=True, exist_ok=True)


def ensure_file(relative_path: str) -> None:
    target = ROOT / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.touch()


def main() -> None:
    print("==> 创建后端目录...")
    for d in BACKEND_DIRS:
        ensure_dir(d)

    print("==> 创建后端空文件...")
    for f in BACKEND_FILES:
        ensure_file(f)

    print("==> 创建前端目录...")
    for d in FRONTEND_DIRS:
        ensure_dir(d)

    print("==> 创建前端空文件...")
    for f in FRONTEND_FILES:
        ensure_file(f)

    print("\n✅ 完成。目录结构如下：\n")
    for root, dirs, files in __import__("os").walk(ROOT):
        # 只显示 backend/ 和 frontend/ 两块
        rel = Path(root).relative_to(ROOT)
        if rel.parts and rel.parts[0] not in ("backend", "frontend"):
            continue
        level = len(rel.parts)
        indent = "  " * level
        if rel.parts:
            print(f"{indent}{rel.name}/")
        for name in sorted(files):
            print(f"{indent}  {name}")


if __name__ == "__main__":
    main()