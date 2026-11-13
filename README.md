# 云瞳视觉监测项目

## 产品版启动

Web 与 Electron 现在共用 Vue 3 浅色产品界面和 Node.js / SQLite 业务服务，包含运行总览、实时监控、事件复核、录像回放、设备管理及系统设置。

安装 Node.js 22.13 或以上版本后，在仓库根目录运行其中一个入口：

```powershell
.\启动Web产品.ps1
.\启动桌面产品.ps1
```

Web 默认访问 `http://127.0.0.1:8000`；桌面入口会打开 Electron。首次启动创建管理员账号，没有预设密码。设备或 GPU 环境未就绪时仍可使用管理界面，视频和推理状态反映实际连接情况。

完整启动、构建与验证命令见 [产品版启动与验收](产品版启动与验收.txt)、[后端说明](backend/README.md)和[桌面端说明](desktop-app/README.md)。以下保留项目结构、开发服务器和底层硬件部署说明。

双摄像机无人机检测工作台提供 Web 和 Electron 两种入口。项目目录也包含独立运行的 AIRSPACE / UAVGuard。

| 软件 | 用途 | 运行入口 | 默认访问方式 |
| --- | --- | --- | --- |
| **云瞳双摄像机无人机检测工作台（v2 Web 版）** | 双路 RTSP 预览、TensorRT 无人机检测、双目测距与方向估计、2D/3D 空间显示、带框录像与抓拍。 | `frontend` + `backend` | 前端 `http://localhost:5173` |
| **云瞳双摄像机无人机检测工作台（Electron 桌面版）** | 在独立窗口运行，复用 Python 摄像机服务进行检测与定位。 | `desktop-app` | Electron 桌面窗口 |
| **云瞳 AIRSPACE / UAVGuard** | 双目空中小目标检测、三角定位、三维轨迹和事件监控。 | `云瞳AIRSPACE_UAVGuard_AI开发移交_2026-09-04\yuntong-airspace-uavguard` | 后端 `http://127.0.0.1:8080`；独立前端 `http://localhost:3000` |

工作台用于无人机检测与近似空间定位。米制定位依赖两台相机的有效标定和双路匹配。

## 更新日志

- **2026-11-13** Web 与 Electron 统一产品入口，接入账号权限、事件复核、媒体检索与导出等持久化业务；抓拍、录像与监测操作以实际服务结果确认。保留校园原透视图，并提供对应的俯视底图。
- **2026-11-10** 接入 `drone.onnx` 和 TensorRT 推理。使用双路检测框中心做三角测量，输出 XYZ、距离、方位角和仰角。加入目标编号、跳点过滤、短时位置保持和界面插值。
- **2026-11-08** 调整后端启动顺序，成功监听端口后再启动摄像机服务，端口冲突时提示关闭旧后端。
- **2026-11-06** 支持成对导入外参，自动计算基线、两光心的左右关系及相对中点的位置。

## v2.0 架构升级说明

相比于 v1.0 的纯 Electron 桌面端，v2.0 版本进行了彻底的架构解耦与前端重写：

1. **前后端分离**：引入了 Node.js (`backend`) 作为中间件，将前端的 UI 渲染与 Python 的重度 AI 推理解耦。
2. **Vue 3 重构**：前端从 React 迁移至 Vue 3 + TypeScript + Pinia，Web 和 Electron 使用相同的浅色产品界面与交互。
3. **空间视图**：校园透视和对应俯视底图共用图像缩放与平移视口；真实目标使用独立的米制相对定位场景。相对定位中的 Canvas 建筑参考层采用近似轮廓，校园底图和参考层均未完成测量配准。
4. **全局状态打通**：采用 Pinia 管理全局相机状态（包括流状态、FPS、检测框、检测帧率上限），实现“设备管理”、“模型配置”与“工作台”的数据同步。
5. **一键启动**：新增了本地一键启动脚本，自动拉起前后端并清理端口。

## 目录

- [更新日志](#更新日志)
- [最快运行方式](#最快运行方式)
- [项目结构](#项目结构)
- [一、双摄像机无人机检测工作台](#一双摄像机无人机检测工作台)
- [二、AIRSPACE / UAVGuard](#二airspace--uavguard)
- [环境与依赖排错](#环境与依赖排错)
- [数据安全与合规](#数据安全与合规)
- [许可证](#许可证)
- [三、首次部署与运行（Windows + Python 虚拟环境）](#三首次部署与运行windows--python-虚拟环境)

## 最快运行方式

### 运行 v2 Web 端（推荐使用一键启动）

**前置条件**：确保你已经配置好了底层的 Python GPU 环境（详见下文“系统要求”）。

**一键启动（Windows）**：
在项目根目录 `Yuntong_Project-master` 下，直接双击运行 **`一键启动.bat`**。

脚本先清理 8000 端口占用并启动 Node.js 后端，固定等待 3 秒后启动 Vue 前端。Node.js 成功监听端口后启动 Python 摄像机服务。

**手动启动（Windows）**

打开两个终端，从项目根目录分别启动后端和前端。

1. **启动 Node.js 后端**
   ```powershell
   cd backend
   npm install
   npm run dev
   ```

2. **启动 Vue 3 前端**
   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

启动成功后，打开浏览器访问 <http://localhost:5173>。首次使用创建管理员账号，之后使用自己的账号登录。

### 运行 Electron 桌面版
如果本机已经完成 GPU 环境配置，并且 `camera-rtsp-low-latency\.env` 中已经填写两台摄像机，执行：
```powershell
Set-Location .\desktop-app
npm run desktop:run
```

## 项目结构

```text
Yuntong_Project-master/
├── backend/                       # Node.js 中间件后端服务
│   ├── src/
│   │   ├── normalize/             # 状态数据清洗与格式化
│   │   ├── routes/                # API 路由 (状态、推流、检测、录像、设备等)
│   │   ├── sidecar/               # Python 子进程生命周期管理 (启动/停止)
│   │   ├── ws/                    # WebSocket 帧接口
│   │   └── index.mjs              # Node.js 服务启动入口
│   ├── .env                       # 后端环境变量配置
│   └── package.json
├── frontend/                      # Vue 3 前端界面 (v2 新增)
│   ├── src/
│   │   ├── api/                   # 前后端通信 API 封装
│   │   ├── assets/                # 静态资源 (图片、图标)
│   │   ├── components/            # 公共组件 (CameraPanel 等)
│   │   ├── layouts/               # 布局组件 (MainLayout 侧边栏/顶栏)
│   │   ├── lib/                   # 建筑模型与界面辅助函数
│   │   ├── router/                # Vue Router 路由配置
│   │   ├── stores/                # Pinia 全局状态管理
│   │   ├── styles/                # 全局 CSS 样式
│   │   ├── types/                 # TypeScript 接口定义
│   │   └── views/                 # 页面视图 (首页、工作台、设备管理等)
│   └── package.json
├── desktop-app/                   # Electron 桌面应用与 GPU 环境
│   ├── models/drone.onnx          # 无人机检测模型
│   ├── desktop-dist/              # 桌面前端构建产物
│   └── work/gpu-runtime/.venv/    # Python GPU 虚拟环境
├── camera-rtsp-low-latency/       # Python AI Sidecar 核心拉流与推理端
│   ├── camera_bridge.py           # 提供 HTTP 服务，管理相机生命周期
│   ├── camera_low_latency.py      # 低延迟拉流与解码
│   ├── tensorrt_drone_detector.py # TensorRT 无人机检测
│   ├── spatial_localization.py   # 双目三角测量
│   ├── drone_tracking.py         # 三维目标跟踪与平滑
│   └── .env                       # 相机 IP、账号密码及拉流参数配置
├── 云瞳AIRSPACE_UAVGuard...\      # 独立的低空三维监控系统
└── 一键启动.bat                   # Windows 本地一键启动脚本
```

## 一、双摄像机无人机检测工作台

### 主要功能

- 同时连接两台 RTSP 摄像机，Web 前端通过 HTTP 获取成对的 JPEG 预览帧。
- 使用 NVIDIA GPU 和 TensorRT 运行 `drone.onnx`，在预览中显示无人机检测框。
- 在 3D/2D 地图中显示目标编号、XYZ、距离、方位角和仰角。地图支持旋转、缩放和平移。
- 支持两路带框录像和手动抓拍。

### 运行链路
```text
两路 RTSP 摄像机
  → Python / PyAV 解码 → 双路配帧 → TensorRT 无人机检测
  → Python 三角测量与三维跟踪
    ├─ Node.js HTTP API → Vue 3 网页前端
    └─ Electron 主进程与 IPC → Electron 桌面前端
```

### 系统要求 (GPU 环境)
运行环境要求基本与 v1 一致，这是跑通底层的核心：
- Windows 10/11 x64 / Node.js `22.13.0` 以上
- CPython `3.12.13` x64
- NVIDIA 显卡驱动 / CUDA Toolkit `12.9` / TensorRT `11.3.0.99`
- **GPU 计算能力需为 8.9（SM 8.9，例如 RTX 4070）**；当前 GPU 脚本不接受其他算力

以下“首次部署与运行”章节是本项目 GPU 环境的完整操作顺序。

### 界面操作
| 按钮 | 作用 |
| --- | --- |
| `开始检测 / 暂停检测` | 控制 Python 端是否启动 AI 推理线程（停用后只推流，画面继续） |
| `开始录制` | 同时开始两路带框录像 |
| `截图保存` | 触发两侧快照 |
| `3D / 2D` | 切换左上角空间视图的投影模式 |
| `全屏显示` | 进入浏览器全屏或原生全屏 |

### 设计细节说明
1. **配帧与定位**。程序用同一台电脑的单调时钟记录两路帧到达时间，并根据码流积压估算帧时刻。实时画面和录像的时间水印不参与配帧。两路估计帧差最多为 40 ms。定位取两路完整检测框的中心，使用内外参三角测量。
2. **三维跟踪**。同一目标沿用编号。后端平滑 XYZ 并过滤突变，前端对显示坐标做插值。短时漏检时，地图以灰色保留最近一次有效位置，约 0.45 秒后清除。单路检测可显示预览框，更新三维位置需要有效的双路配对。
3. **运行参数**。默认预览分辨率为 768×432，预览帧率上限为 30 FPS。每路检测的配置上限为 12.5 FPS，实际帧率取决于配帧和推理耗时。

### 米制定位与相机标定

由外参解算的米制坐标以两台相机的光心中点为原点，X 向左、Y 向上、Z 向前。地图显示无人机的完整三维坐标，距离为到原点的直线距离，方向为相对相机坐标系的方位角和仰角。

在「设备管理」中按 C1、C2 视频通道编号分别导入内参和外参。两份外参须使用同一个固定的 AprilGrid 坐标系，平移单位为米。成对导入后，页面自动填入基线、两台相机的位置、左右关系和原点距离。核对后点击「保存定位配置」，参数将写入 `camera-rtsp-low-latency/spatial-config.json`，供空间定位和地图共同使用。设备管理也支持导入完整定位 JSON。

## 二、AIRSPACE / UAVGuard

*(此部分文档与原版保持一致，主要包括：)*
- 双路 RTSP、离线视频、合成信号源接入
- 极线匹配与三角定位三维轨迹
- React 独立前端展示空域状态
- 适用于真实固定机位的相机标定与部署流程

## 环境与依赖排错

### 端口被占用
如果启动 Node.js 后端时提示 8000 端口已被占用，先查找监听进程，确认是此前启动的本项目后端后再关闭。
```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

### 前端报错 `ECONNREFUSED`
说明 Node.js 后端服务没启动或正在静默重启。请检查 `backend` 终端里是否有红字报错。

### 找不到 ONNX 模型
确认 `desktop-app/models/drone.onnx` 存在。Web 版若设置了 `YUNTONG_DRONE_MODEL`，请核对它指向的文件。首次检测会在后台准备 TensorRT 引擎，后续按模型和运行环境复用缓存。

## 数据安全与合规
- 摄像机密码只保存在本机 `camera-rtsp-low-latency\.env` 中。
- 摄像机通过局域网 RTSP 接入，Python 摄像机服务监听本机回环地址。
- 实时预览在内存中处理，点击「开始录制」或「截图保存」后写入对应文件。
- 不建议将开发服务器直接暴露在公网。

## 许可证
UAVGuard 子项目为 Apache-2.0。其余代码在发布前应补充适合的开源许可证声明。公开可见不等于允许复制或商业使用，需核实所有第三方依赖。

## 三、首次部署与运行（Windows + Python 虚拟环境）

本节只覆盖本仓库的 backend、frontend、desktop-app 和
camera-rtsp-low-latency。根目录的一键启动.bat 只启动已经准备好的 Node.js
服务，不会创建 Python 虚拟环境、安装依赖、准备 GPU 或修正摄像机配置。
以下命令均以 PowerShell 为例，并假设当前目录为：

    E:\Yuntong_Project_v2\Yuntong_Project-master

Python 推理侧车固定使用项目内的虚拟环境：

    desktop-app\work\gpu-runtime\.venv\

不要把 TensorRT、CUDA Python、PyAV 或 CUDA 版 OpenCV 安装到系统 Python，
也不要用系统 Python 直接启动 camera_bridge.py。Node.js 依赖使用各目录自己的
node_modules。

### 3.1 首次部署前检查

GPU 脚本的版本检查是严格的：

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11 x64 |
| Node.js | >=22.13.0 |
| Python | CPython 3.12.13、64 位 |
| CUDA Toolkit | 12.9，必须有 nvcc.exe |
| NVIDIA GPU | 计算能力必须为 8.9（SM 8.9，例如 RTX 4070） |
| C++ 构建工具 | Visual Studio 2022 Build Tools 的 Desktop development with C++ |
| CMake | >=3.28.0；优先使用 Visual Studio 自带的原生 CMake |
| Git | 可访问 GitHub |
| 磁盘空间 | OpenCV CUDA 编译盘至少 20 GB |

如果 CUDA 12.9 不在默认目录，先在当前 PowerShell 会话设置环境变量：

    $env:CUDA_PATH_V12_9 = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9'

然后在项目根目录执行：

    node --version
    npm --version
    git --version
    cmake --version
    $cudaRoot = if ($env:CUDA_PATH_V12_9) { $env:CUDA_PATH_V12_9 } else { 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9' }
    & "$cudaRoot\bin\nvcc.exe" --version
    nvidia-smi --query-gpu=name,compute_cap,driver_version --format=csv

最后一条必须显示 compute_cap 为 8.9。

### 3.2 创建 GPU Python 虚拟环境

    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    Set-Location 'E:\Yuntong_Project_v2\Yuntong_Project-master'

若已有 CPython 3.12.13 x64，显式传入其解释器：

    .\desktop-app\scripts\gpu\setup-gpu-env.ps1 `
      -BasePython 'C:\Path\To\Python312\python.exe'

若没有，可安装 uv，再让脚本自动取得固定版本：

    .\desktop-app\scripts\gpu\setup-gpu-env.ps1

脚本会创建 desktop-app\work\gpu-runtime\.venv，安装 requirements-gpu.txt、
TensorRT 11.3.0.99 和 CUDA Python 12.9.7，并执行 pip check 与 TensorRT 验证。
完成后确认虚拟环境：

    & '.\desktop-app\work\gpu-runtime\.venv\Scripts\python.exe' `
      -c "import sys, platform; print(sys.executable); print(platform.python_version()); print(64 if sys.maxsize > 2**32 else 32)"

手动运行 Python 命令时可以激活环境；Node 后端启动时会直接调用该绝对路径：

    & '.\desktop-app\work\gpu-runtime\.venv\Scripts\Activate.ps1'
    python --version
    deactivate

### 3.3 编译并接入 CUDA 版 OpenCV

requirements-gpu.txt 故意不安装 opencv-python。build-opencv-cuda.ps1 会从固定提交
构建 OpenCV 5.0.0，并把 cv2 链接到上述虚拟环境，因此不要在该环境安装
opencv-python。

编译脚本还要求 CUDA 12.9 开发文件目录中直接存在：

    <CudaRuntimeRoot>\include\cuda_runtime.h
    <CudaRuntimeRoot>\lib\x64\cudart.lib
    <CudaRuntimeRoot>\lib\x64\cuda.lib
    <CudaRuntimeRoot>\lib\x64\cudadevrt.lib

构建参数还会使用同目录中的 cudart_static.lib；准备开发目录或 overlay 时应保留
整个 lib\x64 内容，不要只复制上面列出的三个文件。

先检查完整 Toolkit：

    $cudaRoot = if ($env:CUDA_PATH_V12_9) { $env:CUDA_PATH_V12_9 } else { 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9' }
    @("$cudaRoot\include\cuda_runtime.h","$cudaRoot\lib\x64\cudart.lib","$cudaRoot\lib\x64\cuda.lib","$cudaRoot\lib\x64\cudadevrt.lib") | ForEach-Object { '{0} : {1}' -f $_, (Test-Path -LiteralPath $_) }

另外，CudaRoot 还必须包含 CUBLAS，因为构建启用了 WITH_CUBLAS：

    @("$cudaRoot\include\cublas_v2.h","$cudaRoot\lib\x64\cublas.lib") | ForEach-Object { '{0} : {1}' -f $_, (Test-Path -LiteralPath $_) }

上面列出的所有结果都是 True 时可直接构建：

    .\desktop-app\scripts\gpu\build-opencv-cuda.ps1 `
      -CudaRoot $cudaRoot -CudaRuntimeRoot $cudaRoot -Jobs 8

如果任一文件不存在，不能把当前 Toolkit 根目录直接传给脚本。当前机器的精简
Toolkit 虽然有 nvcc、CUBLAS 和 NPP，却没有完整的 CUDA runtime SDK；而
`nvidia-cuda-runtime-cu12` wheel 也不会单独提供 OpenCV 5.0 编译所需的全部
`crt`、`nv/target`、Thrust/CUB 头文件。因此直接将 `$runtime` 传给
`-CudaRuntimeRoot` 会先后出现 `crt/host_config.h`、`npp.h`、`nv/target` 或
`thrust/tuple.h` 缺失。

推荐把这些开发头文件和 runtime 库组合到项目内的 overlay。先在 GPU 虚拟环境
中安装与本机 CUDA 12.9 匹配的开发文件：

    $venv = (Resolve-Path '.\desktop-app\work\gpu-runtime\.venv\Scripts\python.exe').Path
    & $venv -m pip install --disable-pip-version-check --no-deps --extra-index-url https://pypi.nvidia.com `
      'nvidia-cuda-runtime-cu12==12.9.79' `
      'nvidia-cuda-nvcc-cu12==12.9.86' `
      'nvidia-cuda-cccl-cu12==12.9.27'

然后将 runtime wheel、Toolkit、nvcc wheel 和 CCCL wheel 的头文件合并，库和 DLL
仍从 runtime wheel 复制：

    $site = (& $venv -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
    $runtime = Join-Path $site 'nvidia\cuda_runtime'
    $nvccInclude = Join-Path $site 'nvidia\cuda_nvcc\include'
    $ccclInclude = Join-Path $site 'nvidia\cuda_cccl\include'
    $toolkitInclude = Join-Path $cudaRoot 'include'
    $overlay = Join-Path (Resolve-Path '.\desktop-app\work\opencv-cuda') 'cuda-runtime-overlay'
    New-Item -ItemType Directory -Force "$overlay\include","$overlay\lib\x64","$overlay\bin" | Out-Null
    Copy-Item "$runtime\include\*" "$overlay\include" -Recurse -Force
    Copy-Item "$toolkitInclude\*" "$overlay\include" -Recurse -Force
    Copy-Item "$nvccInclude\*" "$overlay\include" -Recurse -Force
    Copy-Item "$ccclInclude\*" "$overlay\include" -Recurse -Force
    Copy-Item "$runtime\lib\x64\*" "$overlay\lib\x64" -Recurse -Force
    Copy-Item "$runtime\bin\*" "$overlay\bin" -Recurse -Force
    @(
      "$overlay\include\cuda_runtime.h",
      "$overlay\include\crt\host_config.h",
      "$overlay\include\nv\target",
      "$overlay\include\thrust\tuple.h",
      "$overlay\include\npp.h",
      "$overlay\include\cublas_v2.h",
      "$overlay\lib\x64\cudart.lib",
      "$overlay\lib\x64\cuda.lib",
      "$overlay\lib\x64\cudadevrt.lib",
      "$overlay\lib\x64\cudart_static.lib",
      "$overlay\bin\cudart64_12.dll"
    ) | ForEach-Object { '{0} : {1}' -f $_, (Test-Path -LiteralPath $_) }
    $runtimeBin = "$overlay\bin"

这些检查必须全部为 True。随后始终使用 overlay 构建；CudaRoot 仍指向包含
nvcc、CUBLAS 和 NPP 的 CUDA Toolkit：

    $env:CUDA_PATH = $cudaRoot
    $env:CUDA_PATH_V12_9 = $cudaRoot
    $env:PATH = "$cudaRoot\bin;$overlay\bin;$env:PATH"
    .\desktop-app\scripts\gpu\build-opencv-cuda.ps1 `
      -CudaRoot $cudaRoot -CudaRuntimeRoot $overlay -Jobs 8

这一步会下载固定版本的 OpenCV/opencv_contrib 并在 desktop-app\work\opencv-cuda
下编译。OpenCV 编译完成后，建议把 runtime wheel 的 DLL 复制到 OpenCV 安装目录中。
项目生成的 pth 会通过 os.add_dll_directory 加载该目录；仅设置 PATH 在部分
Windows Python 版本上仍可能无法满足 cv2 的首次 DLL 加载：

    if (-not (Test-Path -LiteralPath variable:runtimeBin)) {
      if (Test-Path "$cudaRoot\bin\cudart64_12.dll") { $runtimeBin = "$cudaRoot\bin" }
      elseif (Test-Path -LiteralPath variable:runtime) { $runtimeBin = Join-Path $runtime 'bin' }
    }
    $opencvBin = (Get-ChildItem '.\desktop-app\work\opencv-cuda\install' -Recurse -Filter 'opencv_core*.dll' | Select-Object -First 1).DirectoryName
    if ((Test-Path -LiteralPath variable:runtimeBin) -and (Test-Path "$runtimeBin\cudart64_12.dll")) { Copy-Item "$runtimeBin\cudart64_12.dll" $opencvBin -Force }
    & $venv -c "import cv2; print(cv2.__version__)"

如果使用 overlay 而不是 wheel，先把 wheel 的 bin 文件复制到受控目录，并将
$runtimeBin 指向该目录。上面的 import cv2 必须成功；失败时不要继续启动 Node。
成功后验证完整栈：

    & '.\desktop-app\work\gpu-runtime\.venv\Scripts\python.exe' `
      '.\desktop-app\scripts\gpu\verify_gpu_stack.py' `
      --opencv-install-root '.\desktop-app\work\opencv-cuda\install'

必须看到 GPU STACK VERIFICATION PASSED。没有 CPU 推理备用路径。

### 3.4 准备模型与摄像机配置

Web 版和源码运行的桌面版默认读取 `desktop-app/models/drone.onnx`。在项目根目录确认文件存在。

    Test-Path -LiteralPath '.\desktop-app\models\drone.onnx' -PathType Leaf

当前检测器要求 ONNX 输入为 `1×3×1280×1280`，输出为 `1×300×6`。Web 版如需使用其他兼容模型，在 `backend/.env` 中设置 `YUNTONG_DRONE_MODEL`。相对路径以 `desktop-app` 为基准。

首次配置相机时，若本地没有 `camera-rtsp-low-latency/.env`，复制模板并填写真实值。

    Copy-Item '.\camera-rtsp-low-latency\.env.example' '.\camera-rtsp-low-latency\.env'
    notepad '.\camera-rtsp-low-latency\.env'

至少替换 CAMERA1_IP、CAMERA2_IP、CAMERA_USERNAME、CAMERA_PASSWORD，并确认
CAMERA_RTSP_PORT=554、CAMERA_CHANNEL=1、CAMERA_STREAM=main、
CAMERA_TRANSPORT=tcp。两台账号不同时使用 CAMERA1_USERNAME/CAMERA1_PASSWORD
和 CAMERA2_USERNAME/CAMERA2_PASSWORD。设备路径不同则设置 CAMERA1_RTSP_PATH
和 CAMERA2_RTSP_PATH。不要保留 192.0.2.10/192.0.2.11 或示例密码；密码缺失
会使 sidecar 退出。.env 已被 Git 忽略，不要提交真实凭据。

### 3.5 安装 Node.js 依赖

    Set-Location '.\backend'; npm ci
    Set-Location '..\frontend'; npm ci
    Set-Location '..\desktop-app'; npm ci
    Set-Location '..'

可先构建前端：

    Set-Location '.\frontend'; npm run build; Set-Location '..'

### 3.6 首次运行 v2 Web 版

首次运行建议手动开两个终端，以便看到 sidecar 报错。

终端 A：

    Set-Location 'E:\Yuntong_Project_v2\Yuntong_Project-master\backend'
    npm run dev

后端默认监听 8000，并自动使用 desktop-app\work\gpu-runtime\.venv\Scripts\python.exe
启动 Python sidecar。终端 B：

    Set-Location 'E:\Yuntong_Project_v2\Yuntong_Project-master\frontend'
    npm run dev

打开 <http://localhost:5173>，使用 `test / 904` 登录。确认两路预览正常后点击「开始检测」。首次检测可能在后台构建 TensorRT 引擎，缓存写入 `desktop-app/work/tensorrt`。模型就绪后，再检查检测框和三维定位。

    Invoke-RestMethod 'http://localhost:8000/api/status' | ConvertTo-Json -Depth 8

/api/status 在 sidecar 失败时也可能返回 HTTP 200，必须查看返回 JSON 的
serviceState、error、cameras 和 engineState。

完成一次手动验证后再使用一键启动：

    Set-Location 'E:\Yuntong_Project_v2\Yuntong_Project-master'
    .\一键启动.bat

该脚本会清理 8000 端口、启动后端、等待 3 秒后启动前端；这 3 秒只是固定等待，
不是 GPU 或 TensorRT 已就绪的判断，仍以 /api/status 的 serviceState 和 engineState
为准。它不执行 npm ci、GPU setup、模型配置或 .env 配置，且必须从项目根目录运行。

### 3.7 首次运行 Electron 桌面版（可选）

桌面版不需要启动 backend 或 frontend，但仍需完成 GPU 虚拟环境、OpenCV 验证、
摄像机 .env 和模型准备。先构建并启动：

    Set-Location 'E:\Yuntong_Project_v2\Yuntong_Project-master\desktop-app'
    npm run desktop:run

`desktop:run` 会构建桌面界面并启动 Electron。桌面主进程使用项目 GPU 虚拟环境及 `desktop-app/models/drone.onnx`。Web 和 Electron 各自启动摄像机服务，请选择一种入口运行。需要安装包时，在运行验证通过后执行：

    npm run desktop:dist

安装包中的无人机模型位于 `resources/models/drone.onnx`。

### 3.8 停止与故障排查

- 手动启动的 Node、Vite、Electron 窗口分别按 Ctrl+C；Node 退出时会请求 sidecar 关闭。
- EADDRINUSE 表示 8000 已占用。先确认 PID 再停止项目进程：

      Get-NetTCPConnection -LocalPort 8000 -State Listen
      Stop-Process -Id <PID>

- ECONNREFUSED 先检查 backend 终端和 http://localhost:8000/api/status。
- “Python 环境/摄像机桥接/drone ONNX 不存在”时检查 GPU venv、模型路径和
  camera-rtsp-low-latency\.env，不要改用系统 Python。
- TensorRT 或 cv2.cuda 失败时确认 Python 3.12.13 x64、CUDA 12.9、SM 8.9，以及
  GPU venv 中没有 opencv-python wheel。
- 401 通常是账号密码错误；超时检查 IP、RTSP 端口、VLAN 和 transport；连接拒绝
  检查设备 RTSP 是否启用；无效流数据检查 CAMERA*_RTSP_PATH、通道和编码。
- 一键脚本找不到目录时先 Set-Location 到 Yuntong_Project-master。

运行前应同时满足：GPU verifier 通过、模型存在、两路摄像机 .env 已填写、三个
Node 项目已 npm ci，且后端控制台没有 sidecar 启动错误。Python sidecar 只绑定
127.0.0.1；Node 后端未显式限定监听地址，需通过防火墙或运行环境限制访问，不要
把开发服务器或含密码的 .env 暴露到公网。
