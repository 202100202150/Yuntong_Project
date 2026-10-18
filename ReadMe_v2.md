# 云瞳视觉监测项目 (v2.0)

本仓库包含两套可以独立运行的视觉监测软件。请先根据用途选择入口：

| 软件 | 用途 | 运行入口 | 默认访问方式 |
| --- | --- | --- | --- |
| **云瞳双摄像机人物检测工作台 (v2 Web版)** | 前后端分离架构，支持两路 RTSP 实时预览、TensorRT 人体检测、基于 Canvas 的 2D/3D 校园沙盘空间定位、带框录像与抓拍。 | `frontend` + `backend` | 前端 `http://localhost:5173` |
| **云瞳双摄像机人物检测工作台 (v1 桌面版)** | Electron 桌面应用，功能同上，但在本地作为独立窗口运行。 | `desktop-app` | Electron 桌面窗口 |
| **云瞳 AIRSPACE / UAVGuard** | 双目空中小目标检测、三角定位、三维轨迹和事件监控。 | `云瞳AIRSPACE_UAVGuard_AI开发移交_2026-09-04\yuntong-airspace-uavguard` | 后端 `http://127.0.0.1:8080`；独立前端 `http://localhost:3000` |

> **能力边界**：人物检测工作台只检测完整人体 `person`，不做人脸、身份或性别识别。UAVGuard 当前是可运行 MVP 和工程骨架，不能宣称已经完成现场无人机识别验收。两套软件都不包含干扰、截控或自动反制功能。

## v2.0 架构升级说明

相比于 v1.0 的纯 Electron 桌面端，v2.0 版本进行了彻底的架构解耦与前端重写：

1. **前后端分离**：引入了 Node.js (`backend`) 作为中间件，将前端的 UI 渲染与 Python 的重度 AI 推理解耦。
2. **Vue 3 重构**：前端从 React 迁移至 Vue 3 + TypeScript + Pinia，提供更丰富的交互动效和深浅色主题切换。
3. **3D 校园沙盘重写**：移除了庞大的 Three.js 依赖，使用原生 Canvas 2D 引擎手写正交投影算法，1:1 还原了深圳技术大学 6 栋校园建筑的 3D 轮廓与 2D 俯视平面图，并支持平滑的平移、旋转、缩放。
4. **全局状态打通**：采用 Pinia 管理全局相机状态（包括流状态、FPS、检测框、检测帧率上限），实现“设备管理”、“模型配置”与“工作台”的数据同步。
5. **一键启动**：新增了本地一键启动脚本，自动拉起前后端并清理端口。

## 目录

- [最快运行方式](#最快运行方式)
- [项目结构](#项目结构)
- [一、双摄像机人物检测工作台 (v2 前后端分离版)](#一双摄像机人物检测工作台v2-前后端分离版)
- [二、AIRSPACE / UAVGuard](#二airspace--uavguard)
- [环境与依赖排错](#环境与依赖排错)
- [数据安全与合规](#数据安全与合规)
- [许可证](#许可证)

## 最快运行方式

### 运行 v2 Web 端（推荐使用一键启动）

**前置条件**：确保你已经配置好了底层的 Python GPU 环境（详见下文“系统要求”）。

**一键启动（Windows）**：
在项目根目录 `Yuntong_Project-master` 下，直接双击运行 **`一键启动.bat`**。

脚本会自动执行以下操作：
1. 清理被占用的 8000 端口；
2. 在后台拉起 `backend` 的 Node.js 服务，并自动启动 Python AI 推理侧车；
3. 等待 3 秒让 GPU 模型完成加载；
4. 在另一个窗口拉起 `frontend` 的 Vue 3 服务。

**手动启动（跨平台备用）**：
如果你在 macOS / Linux 下，或者想手动查看报错信息，可以打开两个终端分别执行：

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

启动成功后，打开浏览器访问 <http://localhost:5173>。使用测试账号 `test`，密码 `904` 登录后即可进入工作台。

### 运行 v1 Electron 桌面版
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
│   │   ├── ws/                    # WebSocket 视频流实时推送
│   │   └── index.mjs              # Node.js 服务启动入口
│   ├── .env                       # 后端环境变量配置
│   └── package.json
├── frontend/                      # Vue 3 前端界面 (v2 新增)
│   ├── src/
│   │   ├── api/                   # 前后端通信 API 封装
│   │   ├── assets/                # 静态资源 (图片、图标)
│   │   ├── components/            # 公共组件 (CameraPanel 等)
│   │   ├── layouts/               # 布局组件 (MainLayout 侧边栏/顶栏)
│   │   ├── lib/                   # 核心算法库 (建筑坐标、目标匹配算法)
│   │   ├── router/                # Vue Router 路由配置
│   │   ├── stores/                # Pinia 全局状态管理
│   │   ├── styles/                # 全局 CSS 样式
│   │   ├── types/                 # TypeScript 接口定义
│   │   └── views/                 # 页面视图 (首页、工作台、设备管理等)
│   └── package.json
├── desktop-app/                   # v1 Electron 桌面应用底座
│   ├── desktop-dist/              
│   └── work/gpu-runtime/.venv/    # Python GPU 虚拟环境
├── camera-rtsp-low-latency/       # Python AI Sidecar 核心拉流与推理端
│   ├── camera_bridge.py           # 提供 HTTP 服务，管理相机生命周期
│   ├── camera_low_latency.py      # 低延迟拉流、解码与目标追踪底层库
│   ├── tensorrt_person_detector.py# TensorRT 硬件加速推理引擎
│   └── .env                       # 相机 IP、账号密码及拉流参数配置
├── 云瞳AIRSPACE_UAVGuard...\      # 独立的低空三维监控系统
└── 一键启动.bat                   # Windows 本地一键启动脚本
```

## 一、双摄像机人物检测工作台 (v2 前后端分离版)

### 主要功能
- 同时连接两台支持 RTSP 的网络摄像机；
- 低延迟实时双路画面预览（前端通过 WebSocket 获取 JPEG）；
- 使用 NVIDIA GPU 和 TensorRT 检测 `person`；
- 每路独立进行 IoU 目标跟踪，生成稳定对象 ID；
- **3D/2D 视图无缝切换**：基于 Canvas 投影算法，支持左键旋转、滚轮缩放、平移；2D 模式为严格俯视图。
- **校园建筑模型渲染**：还原 6 栋校园建筑的体块与塔楼/裙楼结构。
- 同时录制两路带检测框的视频，并自动截取带框抓拍；
- 全面的隐私保护：密码明文不进 Git，未点击录制不落盘。

### 运行链路
```text
两路 RTSP 摄像机
  → Python / PyAV 解码 → TensorRT person 检测
  → 内存预览（JPEG 流）
  → Node.js (Express + WebSocket) 
  → Vue 3 前端界面 (Canvas 渲染 3D 与 2D)
```

### 系统要求 (GPU 环境)
运行环境要求基本与 v1 一致，这是跑通底层的核心：
- Windows 10/11 x64 / Node.js `22.13.0` 以上
- CPython `3.12.13` x64
- NVIDIA 显卡驱动 / CUDA Toolkit `12.9` / TensorRT `11.3.0.99`
- **GPU 算力需在 8.6 及以上**（如 RTX 3050/3060/4070 等）

*(详细 GPU 部署步骤见原 `desktop-app` 中的 `setup-gpu-env.ps1` 与 OpenCV-CUDA 构建文档)*

### 界面操作
| 按钮 | 作用 |
| --- | --- |
| `开始检测 / 暂停检测` | 控制 Python 端是否启动 AI 推理线程（停用后只推流，画面继续） |
| `开始录制` | 同时开始两路带框录像 |
| `截图保存` | 触发两侧快照 |
| `3D / 2D` | 切换左上角空间视图的投影模式 |
| `全屏显示` | 进入浏览器全屏或原生全屏 |

### 设计细节说明
1. **双摄匹配算法**：采用了基于外观相似度和几何位置代价的匈牙利匹配算法。无法匹配的单一目标将在 3D 空间中以灰色显示。
2. **相机拉流参数**：默认使用 768x432 的预览分辨率与 30FPS 上限。AI 检测帧率被限制在 12.5 FPS，防止 GPU 过热或内存溢出。
3. **设备与模型配置**：可以通过路由菜单进入相应页面，查看当前 RTSP 地址与 TensorRT 运行状态。

## 二、AIRSPACE / UAVGuard

*(此部分文档与原版保持一致，主要包括：)*
- 双路 RTSP、离线视频、合成信号源接入
- 极线匹配与三角定位三维轨迹
- React 独立前端展示空域状态
- 适用于真实固定机位的相机标定与部署流程

## 环境与依赖排错

### 端口被占用
如果启动 Node.js 后端时提示 `EADDRINUSE: address already in use :::8000`，执行：
```powershell
npx kill-port 8000
```

### 前端报错 `ECONNREFUSED`
说明 Node.js 后端服务没启动或正在静默重启。请检查 `backend` 终端里是否有红字报错。

### 找不到 ONNX 模型
确认 `desktop-app/desktop-dist/models/person-v1.onnx` 或你在 `.env` 配置的路径下文件存在。如果你修改了模型文件，必须手动删除 `desktop-app/work/tensorrt` 缓存，让它重新编译。

## 数据安全与合规
- 摄像机密码只保存在本机 `camera-rtsp-low-latency\.env` 中。
- 视频流只在局域网内局域网回环地址 (127.0.0.1) 中流转。
- 实时预览默认不录盘，直到点击 `Record` 或 `Screenshot`。
- 系统只检测完整人体，不做人脸识别或身份关联。
- 不建议将开发服务器直接暴露在公网。

## 许可证
UAVGuard 子项目为 Apache-2.0。其余代码在发布前应补充适合的开源许可证声明。公开可见不等于允许复制或商业使用，需核实所有第三方依赖。