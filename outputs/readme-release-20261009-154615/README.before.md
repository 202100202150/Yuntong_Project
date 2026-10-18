# 云瞳视觉监测项目

本仓库包含两套可以独立运行的视觉监测软件。请先根据用途选择入口：

| 软件 | 用途 | 运行入口 | 默认访问方式 |
| --- | --- | --- | --- |
| 云瞳双摄像机人物检测工作台 | 两路 RTSP 实时预览、TensorRT 人体检测、跟踪、带框录像与抓拍 | `desktop-app` | Electron 桌面窗口 |
| 云瞳 AIRSPACE / UAVGuard | 双目空中小目标检测、三角定位、三维轨迹和事件监控 | `云瞳AIRSPACE_UAVGuard_AI开发移交_2026-09-04\yuntong-airspace-uavguard` | 后端 `http://127.0.0.1:8080`；独立前端通常为 `http://localhost:3000` |

两套软件互不替代，也不共用 Python 虚拟环境。只想查看摄像机中的人物，请运行第一套；想体验校园低空三维监控演示，请运行第二套。

> 能力边界：人物检测工作台只检测完整人体 `person`，不做人脸、身份或性别识别。UAVGuard 当前是可运行 MVP 和工程骨架，不能宣称已经完成现场无人机识别验收。两套软件都不包含干扰、截控或自动反制功能。

## 目录

- [最快运行方式](#最快运行方式)
- [项目结构](#项目结构)
- [一、双摄像机人物检测工作台](#一双摄像机人物检测工作台)
- [二、AIRSPACE / UAVGuard（新增）](#二airspace--uavguard新增)
- [测试与开发命令](#测试与开发命令)
- [数据安全与合规](#数据安全与合规)
- [许可证](#许可证)

## 最快运行方式

### 运行人物检测桌面软件

如果本机已经完成 GPU 环境配置，并且 `camera-rtsp-low-latency\.env` 中已经填写两台摄像机，执行：

```powershell
Set-Location -LiteralPath 'E:\Yuntong_Project-master\Yuntong_Project-master\desktop-app'
npm run desktop:run
```

程序会先构建 React 页面，再打开 Electron 窗口，并自动启动 Python 摄像机 Sidecar。进入界面后点击 `Start` 才会开始人物检测；软件启动本身不会自动录像或抓拍。

如果启动失败，先运行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\gpu\verify-gpu-stack.ps1
```

如果验证器提示环境缺失，请按后文[首次部署](#首次部署)完成安装。

> 从压缩包解压、移动项目目录或更换 Windows 用户后，不要直接复用别人生成的 `.venv`。先执行 `.\work\gpu-runtime\.venv\Scripts\python.exe --version`；如果错误信息仍指向旧电脑路径，请只删除 `desktop-app\work\gpu-runtime\.venv`，再运行 `setup-gpu-env.ps1` 重新创建。TensorRT engine 同样应在本机重新生成。

### 运行 AIRSPACE / UAVGuard 演示

只启动 Python 后端和内置基础监控页：

```powershell
$UavRoot = 'E:\Yuntong_Project-master\Yuntong_Project-master\云瞳AIRSPACE_UAVGuard_AI开发移交_2026-09-04\yuntong-airspace-uavguard'
Set-Location -LiteralPath $UavRoot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\uavguard.exe --config config/demo.yaml
```

打开 <http://127.0.0.1:8080>，接口文档位于 <http://127.0.0.1:8080/docs>。

演示模式使用合成双摄像机和模拟飞行轨迹，不需要连接真实摄像机，也不代表真实识别准确率。

如果还要运行新的 AIRSPACE React 前端，请保持后端终端不动，另开一个 PowerShell：

```powershell
$UavRoot = 'E:\Yuntong_Project-master\Yuntong_Project-master\云瞳AIRSPACE_UAVGuard_AI开发移交_2026-09-04\yuntong-airspace-uavguard'
Set-Location -LiteralPath (Join-Path $UavRoot 'frontend')
npm ci
npm run dev
```

打开终端显示的地址，通常是 <http://localhost:3000>。前端默认使用浏览器内的模拟数据；要连接已启动的 Python 演示后端，请在页面“接入配置”中选择“实时网关”，基础地址留空。

## 项目结构

```text
Yuntong_Project-master\
├─ README.md
├─ desktop-app\                         Electron + React + TypeScript 桌面应用
│  ├─ components\
│  ├─ desktop\
│  │  └─ public\
│  │     ├─ models\person-v1.onnx       TensorRT 使用的 person ONNX 模型
│  │     └─ ort\                        浏览器侧 ONNX Runtime WASM 资源
│  ├─ lib\
│  ├─ scripts\gpu\                     GPU 环境与 OpenCV CUDA 构建脚本
│  ├─ tests\
│  └─ package.json
├─ camera-rtsp-low-latency\             人物检测工作台的 Python Sidecar
│  ├─ camera_bridge.py
│  ├─ camera_low_latency.py
│  ├─ camera_inspect.py
│  ├─ tensorrt_person_detector.py
│  ├─ requirements-gpu.txt
│  └─ .env.example
└─ 云瞳AIRSPACE_UAVGuard_AI开发移交_2026-09-04\
   └─ yuntong-airspace-uavguard\        独立的低空三维监控系统
      ├─ src\uavguard\
      ├─ frontend\
      ├─ config\
      ├─ calibration\
      ├─ tools\
      ├─ tests\
      └─ docs\
```

## 一、双摄像机人物检测工作台

### 主要功能

- 同时连接两台支持 RTSP 的网络摄像机；
- 双路低延迟实时预览；
- 使用 NVIDIA GPU 和 TensorRT 检测 `person`；
- 每路独立进行 IoU 目标跟踪并生成稳定对象 ID；
- 同时启动、暂停或停止两路检测；
- 同时录制两路带检测框的视频；
- 同时保存两路带检测框的全分辨率抓拍；
- 录像优先使用 NVIDIA NVENC，不可用时自动降级到 `libx264`；
- 摄像机凭据只保存在本机 `.env`；
- 未点击 `Record` 或 `Screenshot` 时不保存摄像机媒体。

运行链路：

```text
两路 RTSP 摄像机
  → Python / PyAV 解码
  → TensorRT person 检测
  → 每路独立目标跟踪
  → 内存预览、带框录像或带框抓拍
  → 仅监听 127.0.0.1 的本地 Sidecar
  → Electron 主进程
  → React 桌面界面
```

`desktop-app` 默认从同一仓库的兄弟目录寻找 `camera-rtsp-low-latency`，因此完整仓库可以放在任意有写入权限的目录，不依赖开发者原来的盘符。

### 系统要求

当前自动化脚本按以下环境编写并验证：

- Windows 10/11 x64；
- PowerShell 5.1 或 PowerShell 7；
- Node.js `22.13.0` 或更高版本及 npm；
- CPython `3.12.13` x64，或由 `uv` 自动安装；
- NVIDIA 显卡驱动；
- CUDA Toolkit `12.9`；
- NVIDIA GPU Compute Capability `8.9`，当前脚本对应 RTX 4070 / Ada；
- TensorRT `11.3.0.99`；
- Visual Studio 2022 Build Tools，包含“使用 C++ 的桌面开发”、MSVC v143、Windows SDK 和 CMake tools；
- CMake `3.28` 或更高版本；
- 两台可从运行电脑访问并已启用 RTSP 的网络摄像机；
- 首次构建 OpenCV CUDA 建议预留至少 20 GB 磁盘空间。

#### GPU 兼容性

当前脚本固定编译 CUDA 架构 `SM 8.9`。如果显卡的 Compute Capability 不是 8.9，验证器会拒绝继续，不能直接假设兼容。适配其他架构时，需要同步修改并重新验证：

```text
desktop-app\scripts\gpu\common.ps1
desktop-app\scripts\gpu\build-opencv-cuda.ps1
desktop-app\scripts\gpu\verify_gpu_stack.py
```

TensorRT engine 与 ONNX 模型、TensorRT 版本、GPU 架构和精度配置绑定。不要从其他电脑复制 engine；目标电脑会在第一次启动检测时自行构建并缓存。

### 首次部署

以下命令从仓库根目录开始：

```powershell
Set-Location -LiteralPath 'E:\Yuntong_Project-master\Yuntong_Project-master'
```

#### 1. 配置两台摄像机

```powershell
Copy-Item .\camera-rtsp-low-latency\.env.example `
          .\camera-rtsp-low-latency\.env
notepad .\camera-rtsp-low-latency\.env
```

至少修改：

```dotenv
CAMERA1_IP=<第一台摄像机IP>
CAMERA2_IP=<第二台摄像机IP>

CAMERA_RTSP_PORT=554
CAMERA_USERNAME=<摄像机用户名>
CAMERA_PASSWORD=<摄像机密码>

CAMERA_CHANNEL=1
CAMERA_STREAM=main
CAMERA_TRANSPORT=tcp

CAMERA1_OUTPUT_DIR=outputs\camera1
CAMERA2_OUTPUT_DIR=outputs\camera2
CAMERA_SNAPSHOT_FORMAT=png
CAMERA_RECORD_ON_START=false
```

两台摄像机使用不同账号时，可以增加：

```dotenv
CAMERA1_USERNAME=<第一台用户名>
CAMERA1_PASSWORD=<第一台密码>
CAMERA2_USERNAME=<第二台用户名>
CAMERA2_PASSWORD=<第二台密码>
```

相对输出目录默认解析为：

```text
camera-rtsp-low-latency\outputs\camera1
camera-rtsp-low-latency\outputs\camera2
```

也可以使用其他有写入权限的绝对路径。先测试 RTSP 端口：

```powershell
Test-NetConnection <第一台摄像机IP> -Port 554
Test-NetConnection <第二台摄像机IP> -Port 554
```

`.env` 已被 Git 忽略。不要把真实密码写入 `.env.example`、README、Issue、截图或日志。

#### 2. 安装桌面依赖

```powershell
Set-Location .\desktop-app
node --version
npm ci
```

Node.js 版本低于 `22.13.0` 时，先升级并重新打开 PowerShell。

#### 3. 安装 CUDA 与 C++ 构建工具

安装 CUDA Toolkit 12.9 后，重新打开 PowerShell并检查：

```powershell
& 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9\bin\nvcc.exe' --version
nvidia-smi --query-gpu=name,compute_cap,driver_version --format=csv,noheader
cmake --version
```

只有 NVIDIA 驱动并不够，`nvcc.exe` 必须存在。CMake 版本应不低于 3.28。

#### 4. 创建项目专用 GPU Python 环境

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

方式 A：使用 `uv` 自动准备 Python 3.12.13：

```powershell
winget install --id astral-sh.uv -e
```

重新打开 PowerShell，回到 `desktop-app` 后执行：

```powershell
.\scripts\gpu\setup-gpu-env.ps1
```

如果项目来自压缩包，并且携带的旧 `.venv` 指向另一台电脑，请先在 `desktop-app` 中删除且只删除该虚拟环境，再执行上面的脚本：

```powershell
Remove-Item -LiteralPath .\work\gpu-runtime\.venv -Recurse -Force
.\scripts\gpu\setup-gpu-env.ps1
```

方式 B：使用已有的 CPython 3.12.13 x64：

```powershell
.\scripts\gpu\setup-gpu-env.ps1 `
  -BasePython 'D:\Python312\python.exe'
```

虚拟环境会创建在：

```text
desktop-app\work\gpu-runtime\.venv
```

脚本会根据 `camera-rtsp-low-latency\requirements-gpu.txt` 安装固定版本的 PyAV、NumPy、TensorRT bindings/runtime、CUDA Python、requests 和 python-dotenv，并单独安装 TensorRT 的 `tensorrt-cu12` Python 入口包（它提供 `import tensorrt`，且需要允许 pip 构建这个小型 source package）。不要把全局 Python 或训练环境直接作为正式运行环境，也不要在这个 GPU 环境中手工安装 PyPI 的 `opencv-python`、`opencv-python-headless` 或 `opencv-contrib-python`。

#### 5. 构建 OpenCV CUDA

```powershell
.\scripts\gpu\build-opencv-cuda.ps1 `
  -CudaRuntimeRoot 'E:\Codex\yuntongsoftware\software\work\opencv-cuda\cuda-runtime-overlay' `
  -Jobs 8
```

命令中的 CUDA runtime overlay 是这台电脑旧软件已有的 CUDA 12.9 开发文件缓存；脚本只读取它，新 OpenCV 源码、编译产物和 Python venv 仍放在本项目的 `desktop-app\work` 内。若在没有旧缓存的电脑上部署，需先将脚本要求的 `nvidia-cuda-runtime-cu12==12.9.79` 开发文件准备到 `desktop-app\work\opencv-cuda\cuda-runtime-overlay`，或把 `-CudaRuntimeRoot` 指向该版本已准备好的目录。脚本随后会获取并校验 OpenCV 5.0.0 与 opencv_contrib 5.0.0，使用 Visual Studio 2022 为 CUDA 12.9 / SM 8.9 构建所需模块并接入项目虚拟环境。首次执行需要下载源码并本地编译；中断后可重新运行，继续使用 `desktop-app\work` 下的受控缓存。

#### 6. 验证 GPU 运行栈

```powershell
.\scripts\gpu\verify-gpu-stack.ps1
```

验证器会实际检查 Python 版本和位数、GPU 与 Compute Capability、CUDA Toolkit、TensorRT、CUDA Python、OpenCV CUDA 构建信息，以及 GPU upload、CUDA resize 和 download 冒烟测试。只有完整通过才表示运行环境已经准备好。

#### 7. 检查部署模型

```powershell
Test-Path .\desktop\public\models\person-v1.onnx
```

预期输出 `True`。检测器要求固定输入 `1 × 3 × 640 × 640`、单输入、单输出，并支持当前代码约定的 YOLO `person` 输出布局。

替换 ONNX 后，只删除生成的 TensorRT 缓存，不要删除模型：

```powershell
Remove-Item -Recurse -Force .\work\tensorrt -ErrorAction SilentlyContinue
```

#### 8. 启动软件

```powershell
npm run desktop:run
```

启动时会构建 React 前端、打开 Electron、定位 Python Sidecar，并使用 `desktop-app\work\gpu-runtime\.venv` 启动 `camera_bridge.py`。首次点击 `Start` 时，TensorRT 可能需要生成 engine，等待时间会明显长于后续启动。

### 界面操作

| 按钮 | 作用 |
| --- | --- |
| `Start` | 同时启动两路人物检测和跟踪 |
| `Pause` | 暂停检测，实时预览继续 |
| `Stop` | 停止检测并清除跟踪结果 |
| `Record` | 同时开始两路带检测框录像；再次点击停止并完成文件封装 |
| `Screenshot` | 同时保存两路带检测框抓拍 |
| `Fullscreen` | 进入或退出全屏 |

建议先点击 `Start`，确认两路画面出现 `person` 框，再使用 `Record` 或 `Screenshot`。直接关闭软件前，应先停止录像，让文件完成封装。

### 自定义目录

正常的单仓库结构不需要设置绝对路径。如果 Sidecar、Python 环境或 TensorRT 缓存位于其他位置，可在启动前覆盖：

```powershell
$env:YUNTONG_CAMERA_PROJECT_DIR = 'D:\Projects\camera-rtsp-low-latency'
$env:YUNTONG_CAMERA_PYTHON = 'D:\PythonEnvs\yuntong\Scripts\python.exe'
$env:YUNTONG_TENSORRT_CACHE = 'D:\Caches\yuntong-tensorrt'
npm run desktop:run
```

这些变量只影响当前 PowerShell 会话。

### 独立检查摄像机

从仓库根目录运行只读检查：

```powershell
.\desktop-app\work\gpu-runtime\.venv\Scripts\python.exe `
  .\camera-rtsp-low-latency\camera_inspect.py `
  --ip <摄像机IP>
```

独立启动 Python 双摄像机预览：

```powershell
Set-Location .\camera-rtsp-low-latency
..\desktop-app\work\gpu-runtime\.venv\Scripts\python.exe camera_low_latency.py
```

独立的 `camera_low_latency.py` 使用原始压缩包 remux 录像；桌面应用的 `camera_bridge.py` 会重新编码，并把检测框烧录到视频中。

### 默认性能参数

- 桌面应用每路预览尺寸：`768 × 432`；
- 预览刷新上限：30 FPS；
- 每路检测目标频率：约 12.5 Hz；
- 每路只保留最新待处理帧，旧帧会被丢弃；
- 两路共用一个 TensorRT 推理线程；
- 每路使用独立目标跟踪器。

实际帧率还受摄像机源 FPS、网络、RTSP 解码、JPEG 编码、Electron 绘制和录像编码负载影响。TensorRT 提高的是检测吞吐，不能把 25 FPS 的摄像机源流变成 30 个不同的真实帧。

### 人物检测工作台常见问题

#### 找不到摄像机桥接程序

确认 `desktop-app` 与 `camera-rtsp-low-latency` 是同级目录。如果 Sidecar 在其他位置，设置 `YUNTONG_CAMERA_PROJECT_DIR`。

#### 找不到 CUDA / TensorRT Python 环境

在 `desktop-app` 中检查：

```powershell
Test-Path .\work\gpu-runtime\.venv\Scripts\python.exe
```

返回 `False` 时重新执行：

```powershell
.\scripts\gpu\setup-gpu-env.ps1
.\scripts\gpu\build-opencv-cuda.ps1 -Jobs 8
```

#### `import cv2` 成功，但 GPU 验证失败

可能误装了 CPU OpenCV wheel：

```powershell
.\work\gpu-runtime\.venv\Scripts\python.exe -m pip list |
  Select-String opencv
```

GPU 虚拟环境不应安装 `opencv-python` 或 `opencv-contrib-python` wheel；正确的 `cv2` 来自 `desktop-app\work\opencv-cuda\install`。

#### 两路画面黑屏或反复重连

检查摄像机在线状态、RTSP 开关、IP、端口、账号、密码、Windows 防火墙、VLAN、`CAMERA_STREAM`，以及主码流编码格式是否受当前 FFmpeg / PyAV 支持。

#### 录像或抓拍没有生成文件

检查 `.env` 中的 `CAMERA1_OUTPUT_DIR` 与 `CAMERA2_OUTPUT_DIR`，确认当前用户有写权限且磁盘空间充足。带框抓拍和录像应在点击 `Start` 并出现新鲜检测结果后执行。

#### pip 出现 SSL EOF 或证书错误

不要使用 `--trusted-host` 关闭证书验证。先检查 Windows 时间、代理、HTTPS 检查软件、组织根证书、防火墙以及 PyPI / NVIDIA 包索引连通性，再重新执行安装脚本。

### 人物模型说明

`person-v1.onnx` 仅用于人体目标检测：类别只有 `person`，不进行人脸识别、身份判断或性别区分，且可能漏检或误检，不应作为高风险自动决策的唯一依据。

当前仓库模型文件记录的 SHA-256 为：

```text
3AF1A57313BB5377E480859239AC1994EFDAE16761C983095E097E0AAA9221D5
```

公开或再分发模型前，维护者必须确认基础权重、训练代码和数据集的许可证要求，并补充准确的模型来源、训练或导出方式、许可证与校验值。

## 二、AIRSPACE / UAVGuard（新增）

UAVGuard 面向两台固定网络摄像机的校园低空监测。它从双路 RTSP、离线视频或合成视频中提取小型运动目标，建立二维轨迹，通过极线约束和稀疏三角测量恢复局部 ENU 三维坐标，再使用恒加速度卡尔曼滤波形成确认轨迹。

### 已实现能力

- 双路 RTSP、视频文件和合成信号源接入；
- 三帧差分、MOG2、天空掩膜、微振动补偿和二维轨迹确认；
- 时间门限、极线匹配、DLT 三角测量、非线性重投影优化和误差协方差；
- 三维轨迹确认、类别投票、告警 Webhook 和 SQLite 事件库；
- FastAPI REST、WebSocket、双画面 MJPEG 和基础 ENU 监控页；
- 独立 React 前端，提供 2D / 3D 空域、模拟轨迹、视频通道、事件中心和网关接入；
- 内参、外参、闪灯同步与 RTK 轨迹评估工具；
- Docker、Compose、Ubuntu service 示例和现场部署文档。

### 当前能力边界

- 当前是可运行 MVP，不是已经通过现场验收的成品；
- 仓库不包含真实 RTSP 密码、现场有效标定、校园训练数据或已验收的 ONNX 权重；
- 尚未完成真实机位内外参、ENU 原点、控制点和闪灯时间同步标定；
- 尚未完成校园数据集训练、8 小时负样本、RTK 真值、误报率和距离分档验收；
- 当前前端的 1 km 空域是显示范围，不代表实现了 1 km 识别；
- 前端“聚焦”只改变视角，不会控制真实云台；
- 几何或分类质量不足时必须保持 `candidate` 或 `unknown`，不能当作确认无人机。

以现有 6 mm 镜头为背景，项目文档采用分级目标，而不是承诺全范围分类：

- 100–150 m：0.5 m 多旋翼的分类与三维定位目标区；
- 150–250 m：小目标探测、跟踪和定位目标区；
- 250–300 m：1 m 以上固定翼目标扩展区。

这些仍需现场采集与验收验证。

### 后端演示

要求 Python 3.11 或更高版本：

```powershell
$UavRoot = 'E:\Yuntong_Project-master\Yuntong_Project-master\云瞳AIRSPACE_UAVGuard_AI开发移交_2026-09-04\yuntong-airspace-uavguard'
Set-Location -LiteralPath $UavRoot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\uavguard.exe --config config/demo.yaml
```

- 基础监控页：<http://127.0.0.1:8080>
- OpenAPI 文档：<http://127.0.0.1:8080/docs>
- 健康检查：<http://127.0.0.1:8080/api/v1/health>

Ubuntu 对应命令：

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e '.[dev]'
.venv/bin/uavguard --config config/demo.yaml
```

### 独立 React 前端

要求 Node.js 22.13 或更高版本：

```powershell
Set-Location -LiteralPath (Join-Path $UavRoot 'frontend')
npm ci
npm run dev
```

默认前端完全离线模拟，无需后端或相机。对接 Python 后端时，在页面中选择“实时网关”；开发代理默认将 `/api` 和 `/ws` 转发到 `http://127.0.0.1:8080`。需要更改后端时，在启动前设置：

```powershell
$env:UAVGUARD_BACKEND = 'http://127.0.0.1:8080'
npm run dev
```

真实模式连接失败时不会静默回退到模拟轨迹，避免运行人员误判。

### 主要 API

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/config` | 相机 ID 与后端演示状态 |
| `GET /api/v1/health` | 相机、帧率、同步和模型健康状态 |
| `GET /api/v1/tracks/active?confirmed_only=true` | 活动轨迹 |
| `GET /api/v1/events?limit=100` | 历史事件 |
| `GET /api/v1/cameras/{camera_id}/frame.jpg` | 单帧图像 |
| `GET /api/v1/cameras/{camera_id}/mjpeg` | MJPEG 视频 |
| `GET /api/v1/recordings/{path}` | 事件录像 |
| `WS /ws/tracks` | 实时 TrackEvent |

轨迹采用局部 ENU 米制坐标：x 向东、y 向北、z 向上。每条轨迹还会输出位置标准差与重投影误差。

### 真实摄像机上线顺序

1. 阅读 `docs\CAMERA_SETTINGS.md`，完成两机码流、安全、账号、VLAN 与 NTP 设置。
2. 按 `docs\FIELD_DEPLOYMENT.md` 固定机位，完成内参、控制点外参、ENU 和时间偏移标定。
3. 复制 `config\production.example.yaml` 为 `config\production.yaml`。
4. 复制 `.env.example` 为 `.env`，只在本机或密钥管理系统中填写完整且正确 URL 编码的 RTSP 地址与服务口令。
5. 生成 `calibration\site-cam01.yaml` 和 `calibration\site-cam02.yaml`；只有现场复核后才能设置 `valid: true`。
6. 没有分类模型时，先保持 `classifier.model_path` 为空，验证取流、检测、关联、定位和轨迹。
7. 按 `docs\MODEL_TRAINING.md` 训练、验证并部署 ONNX 分类模型。
8. 按 `docs\ACCEPTANCE_AND_OPERATIONS.md` 完成受控飞行和长时间负样本验收。

生产环境应使用同源 HTTPS 网关统一代理网页、REST、WebSocket 和 MJPEG；不要把开发服务器直接暴露到公网。

## 测试与开发命令

### 人物检测桌面应用

```powershell
Set-Location -LiteralPath 'E:\Yuntong_Project-master\Yuntong_Project-master\desktop-app'
npm test
npm run typecheck
npm run lint -- --deny-warnings
npm run build
```

Python Sidecar 单元测试：

```powershell
Set-Location -LiteralPath 'E:\Yuntong_Project-master\Yuntong_Project-master'
.\desktop-app\work\gpu-runtime\.venv\Scripts\python.exe `
  -m unittest discover `
  -s .\camera-rtsp-low-latency `
  -v
```

这些单元测试不会连接真实摄像机，也不应向正式录像目录写入媒体。

开发命令：

```powershell
Set-Location .\desktop-app
npm run dev
npm run desktop:run
```

生成 Windows NSIS 安装包：

```powershell
npm run desktop:dist
```

当前安装包不自动捆绑完整 CUDA、TensorRT、OpenCV CUDA 和 Sidecar 环境；标准运行方式仍是按本文从源码准备 GPU 环境。

### UAVGuard 后端与前端

```powershell
Set-Location -LiteralPath $UavRoot
.\.venv\Scripts\python.exe -m pytest

Set-Location .\frontend
npm test
npm run typecheck
npm run lint
npm run build
```

测试通过不等于真实现场验收完成。

## 数据安全与合规

- 人物工作台的摄像机密码只应保存在 `camera-rtsp-low-latency\.env`；UAVGuard 的凭据只应保存在其 `.env` 或密钥管理系统中。
- 人物检测 Sidecar 只绑定 `127.0.0.1`，Electron 每次启动都会生成短期 Bearer token。
- 实时预览、人物检测与跟踪默认只在内存中处理，只有点击 `Record` 或 `Screenshot` 才写盘。
- 保存目录来自本地 `.env`，前端不能提交任意文件路径。
- 项目不会自动把摄像机画面上传到远程服务。
- 生产相机建议置于独立 VLAN，禁止公网直接访问。
- 不要在公开 Issue、日志或截图中暴露人员、地点、摄像机地址、Cookie、令牌或密码。
- 部署视频监控、人体检测或校园低空监测前，应遵守当地关于隐私、监控、数据保存、提示标识、飞行活动和备案的要求。
- 检测、分类与轨迹结果存在误报和漏报，不应作为高风险自动决策的唯一依据。

## 许可证

UAVGuard 子项目的 `pyproject.toml` 声明为 Apache-2.0，并带有其自身 `NOTICE`。仓库其余公开副本尚未由所有者统一补充根级许可证。GitHub 或压缩包公开可见不等于自动授予复制、修改或商业使用权；正式发布前应核对全部源码、模型、训练数据和第三方依赖的授权，并添加适用的根级 `LICENSE` 与第三方声明。
