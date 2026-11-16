# 云瞳 AIRSPACE 桌面端

桌面端与 Web 共用 `frontend` 的 Vue 页面和 `backend` 的产品接口。主入口是实时监控工作台：左侧三维场景、右侧视频墙同时显示。Electron 负责原生窗口控制、受控分离窗口、本地服务启动、下载对话框和退出时的资源回收；默认不再加载旧 React 界面。

## 安装与启动

在仓库根目录使用 Node.js 22.13 或更新版本：

```powershell
npm --prefix frontend ci
npm --prefix backend ci
npm --prefix desktop-app ci
npm --prefix desktop-app run desktop:run
```

`desktop:run` 先构建共用 Vue 页面，再打开 Electron。窗口内页面和 API 都来自仅监听 `127.0.0.1` 的随机端口。首次使用需要创建管理员，没有硬编码通用密码。

账号、事件和设置保存在 `%APPDATA%/YuntongAIRSPACE/data`。Web 和桌面版默认拥有独立数据库；不要同时运行两个实例占用同一组摄像机。

自动化验收或独立运行配置可通过绝对路径环境变量 `YUNTONG_PROFILE_DIR` 使用单独的登录、缓存和数据库目录；未设置时仍使用上述默认目录。验收不得指向用户现有数据目录。

GPU 与摄像机运行环境独立配置。可在启动前设置以下环境变量：

```powershell
$env:YUNTONG_CAMERA_PYTHON = 'D:\Yuntong\gpu-runtime\Scripts\python.exe'
$env:YUNTONG_CAMERA_PROJECT_DIR = 'D:\Yuntong\camera-rtsp-low-latency'
$env:YUNTONG_DRONE_MODEL = 'D:\Yuntong\models\drone.onnx'
npm --prefix desktop-app run desktop:run
```

也可以在 `%APPDATA%/YuntongAIRSPACE/runtime-config.json` 保存对应路径。路径必须为绝对路径；环境变量优先于文件。可配置项为 `YUNTONG_CAMERA_PYTHON`、`YUNTONG_CAMERA_PROJECT_DIR`、`YUNTONG_DRONE_MODEL`、`YUNTONG_CALIBRATION_PYTHON`、`YUNTONG_MODEL_PYTHON`、`YUNTONG_FFMPEG`、`YUNTONG_FFPROBE`。发布包不复制开发机器的 `.env`。

缺少 GPU 环境、模型或设备时仍可启动管理界面；视频和推理显示实际未连接状态。

## 构建 Windows 安装包

```powershell
npm --prefix desktop-app run desktop:pack
npm --prefix desktop-app run desktop:dist
```

`desktop:pack` 生成目录版，`desktop:dist` 生成 NSIS 安装包。构建会重新编译共享 Vue 前端，完整复制前端产物（包括本地三维模型及其加载器资源）、后端源码与生产依赖，以及 Python 摄像机源码。存在 `desktop-app/models/drone.onnx` 时会一起复制；不存在时需在部署机器配置模型路径。

不会打包本机摄像机密码、数据库、录像、GPU 虚拟环境和 CUDA/TensorRT 安装。GPU 驱动、Python 依赖与硬件仍须在目标机器部署。正式分发还需配置 Windows 签名证书。

## 保留旧桌面入口

```powershell
npm --prefix desktop-app run desktop:legacy
```

该入口保留旧 React 工作台，用于迁移期间核对原有摄像机能力。日常运行使用 `desktop:run`。

## 安全与生命周期

渲染器开启 sandbox、contextIsolation 和 webSecurity，关闭 Node integration，不暴露任意 IPC。页面只访问当前本地服务，拒绝外部导航、外部弹出窗口和无关权限请求；只允许受控本地窗口请求用户操作触发的全屏。唯一允许的分离窗口是当前服务的 `/workbench?pane=scene` 和 `/workbench?pane=video`，每种最多一个，继承相同安全选项并共享登录会话。关闭主窗口会同时关闭分离窗口，并等待后端关闭摄像机子进程后退出应用；关闭单个分离窗口不会退出主窗口。

无边框标题栏通过沙箱 preload 暴露 `window.yuntongDesktop` 的有限窗口操作：`minimize()`、`maximize()`（最大化/还原切换）、`close()`、`windowState()`、`onWindowState(listener)`。IPC 会验证来源为本地页面的主 frame，且只操作发起调用的窗口。Web 页面不依赖这些原生接口。标题栏拖动区域由共享前端设置，按钮从拖动区域排除。

窗口移除了 Electron 通用菜单，避免按 Alt 出现与产品无关的默认入口。导出使用系统保存对话框，保存中断会显示明确错误，用户主动取消不会被当作失败。页面进程异常退出或已打开页面加载失败时，会显示“重新加载 / 关闭应用”对话框；两项操作分别重新打开当前本地服务和正常关闭后端退出。

`npm run test:product-actions` 会在独立临时 AppData 中执行生产桌面入口，验证原生窗口控制与状态事件、三维/视频分离窗口及去重/共享会话、实际文件下载、保存失败反馈、渲染进程崩溃后重载与关闭、本地服务退出和权限拦截。测试由代码提供保存路径和恢复对话框答复；原生窗口状态、文件写入、Electron DownloadItem、渲染进程崩溃和后端端口关闭均真实执行。摄像机 Python 指向不存在的测试路径，不连接现场设备。
