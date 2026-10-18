# 云瞳 AIRSPACE / UAVGuard AI 开发移交说明

更新时间：2026-09-04

## 1. 项目目标与当前边界

本工程用于校园低空空域的无人机、固定翼无人机、鸟类和未知空中目标监测。当前技术路线为：两台固定摄像机生成运动候选，完成双目关联与三角测量，再进行三维轨迹滤波和类别投票；一台云台变焦相机作为后续光学确认通道。

现有硬件背景：两台海康威视 DS-2CD3T86WDA-PW 6 mm PoE 摄像机和一台交换机，默认双机基线约 40 m。当前工程的真实能力目标仍是 100–300 m 分级探测与定位。前端中的 1 km 空域范围只是界面演示范围，不代表已经实现 1 km 无人机/鸟类/固定翼分类。

当前版本是可运行 MVP 和工程骨架，不是已经通过现场验收的成品。仓库中没有真实 RTSP 密码、现场有效标定文件、训练数据或已验收的 ONNX 权重。

## 2. 已完成内容

### Python 后端

- 双路 RTSP、视频文件和合成信号源接入。
- 三帧差分、MOG2、天空掩膜、微振动补偿、连通域候选和二维轨迹确认。
- 时间门限、极线距离、类别兼容性、DLT 三角测量、非线性重投影优化和交会角过滤。
- 恒加速度三维卡尔曼轨迹、5/8 确认、类别投票、失联状态和误差字段。
- ROI 分类器接口，可加载 ONNX Runtime；未提供伪造模型权重。
- FastAPI REST、WebSocket、MJPEG、事件录像、SQLite 事件库、Webhook 和基础监控页。
- 内参、外参、闪灯时间偏移和轨迹评估工具。
- Docker、Compose、Ubuntu service 示例以及相机、标定、训练、验收文档。

### React 前端

- “云瞳 AIRSPACE”校园低空监控工作台。
- ENU 三维投影、2D/3D 切换、旋转、缩放、聚焦、轨迹和误差展示。
- 多旋翼、固定翼无人机、鸟类、未知目标四类 10 Hz 模拟数据。
- 双固定相机、单云台确认通道、目标详情、事件中心、健康状态和录像链接。
- 前端模拟、后端模拟、真实网关三种状态明确区分；真实模式不会静默回退到模拟数据。
- 海雾蓝主题：`#3368A0`、`#66A3BF`、`#C8DFDB`、`#F2EFE7`。
- 已加入静态 `darkreader-lock`，避免浏览器深色插件改写品牌颜色。

## 3. 尚未完成或不能宣称完成

- 尚未接入学校现场两路真实 RTSP 长时间运行。
- 尚未完成真实机位内外参、ENU 原点、控制点和闪灯时间同步标定。
- 尚未采集并按完整飞行批次隔离校园训练/验证/测试数据。
- 尚未训练和验收 `multirotor`、`fixed_wing_uav`、`bird`、`unknown` 分类模型。
- 尚未实现云台转动、变焦、自动指向和厂商 SDK/ISAPI 控制；当前“聚焦”只改变前端视角。
- 尚未把真实校园三维模型或正射地图测绘配准到 ENU 坐标。
- 尚未完成 8 小时负样本、RTK 真值、误报率、延迟和距离分档验收。
- 尚未验证 1 km 目标分类能力。中心云台远距确认属于下一阶段，需要按目标像素数、焦距、大气条件和夜间补光重新做指标设计。
- 当前云端页面仅用于演示，不能直接访问校园内网 RTSP 或后端服务。

## 4. 目录与关键入口

```text
src/uavguard/                 Python 实时处理与服务
  app.py                      FastAPI 路由、WebSocket、MJPEG
  pipeline.py                 双路处理线程和总体运行时
  motion.py                   运动候选与二维跟踪
  geometry.py                 极线关联、三角测量、重投影优化
  tracking3d.py               三维卡尔曼跟踪与状态确认
  classifier.py               ONNX ROI 分类接口
  calibration.py             标定文件读取与门禁
  recorder.py / storage.py    录像分段与事件存储

frontend/                     独立 React/Vinext 监控前端
  components/monitoring-workspace.tsx  工作台、视频、事件、配置
  components/airspace-view.tsx         三维空域投影与交互
  lib/airspace.ts                      数据契约、校验、投影、模拟轨迹
  lib/use-airspace.ts                  网关连接、重连、轨迹过期
  app/globals.css / palette.css        布局与视觉主题

config/                       演示与生产配置模板
calibration/                  标定格式和演示标定
tools/                        标定、同步、轨迹评测命令行工具
docs/                         现场部署、相机、训练、验收手册
tests/                        Python 后端测试
frontend/tests/               前端数据和几何测试
```

## 5. 本地运行

### 后端演示

要求 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\uavguard --config config/demo.yaml
```

访问 `http://127.0.0.1:8080`，接口文档为 `http://127.0.0.1:8080/docs`。

### 独立前端演示

要求 Node.js 22.13 或更高版本。

```powershell
cd frontend
npm ci
npm run dev
```

默认使用浏览器端模拟数据。对接后端时，在“接入配置”中选择实时网关；开发环境由 Vite 将 `/api` 和 `/ws` 代理到 `UAVGUARD_BACKEND`，默认 `http://127.0.0.1:8080`。

## 6. 前后端接口约定

- `GET /api/v1/config`
- `GET /api/v1/health`
- `GET /api/v1/tracks/active?confirmed_only=true`
- `GET /api/v1/events?limit=100`
- `GET /api/v1/cameras/{camera_id}/frame.jpg`
- `GET /api/v1/cameras/{camera_id}/mjpeg`
- `GET /api/v1/recordings/{path}`
- `WS /ws/tracks`

核心轨迹消息：

```json
{
  "trackId": "UAV-017",
  "timestampUtc": "ISO-8601",
  "state": "candidate|confirmed|lost",
  "class": "multirotor|fixed_wing_uav|bird|unknown",
  "classConfidence": 0.0,
  "positionEnuM": [0.0, 0.0, 0.0],
  "velocityEnuMps": [0.0, 0.0, 0.0],
  "positionStdM": [0.0, 0.0, 0.0],
  "reprojectionErrorPx": 0.0,
  "cameraIds": ["cam01", "cam02"]
}
```

坐标为局部 ENU 米制坐标：x 向东、y 向北、z 向上。不得把 `candidate`、`unknown` 或前端模拟数据当作确认无人机。

## 7. 真实设备接入顺序

1. 阅读 `docs/CAMERA_SETTINGS.md`，完成两机码流、快门、VLAN、账号和 NTP 设置。
2. 按 `docs/FIELD_DEPLOYMENT.md` 固定机位并完成内参、控制点外参、ENU 与时间偏移标定。
3. 复制 `config/production.example.yaml` 为 `config/production.yaml`。
4. 复制根目录 `.env.example` 为 `.env`，只在本机或密钥系统中填写 RTSP 和服务口令。
5. 生成 `calibration/site-cam01.yaml` 和 `site-cam02.yaml`；只有现场复核后才能设置 `valid: true`。
6. 先在无分类模型状态下验证取流、检测、关联、定位和轨迹，再接入 ONNX 模型。
7. 按 `docs/ACCEPTANCE_AND_OPERATIONS.md` 做受控飞行与长时间负样本验收。

## 8. 建议的下一阶段优先级

### P0：真实几何链路

- 两路 4K/25 fps 稳定取流、时间戳记录、断流重连和 8 小时稳定性。
- 内外参、40 m 左右基线、控制点、ENU 与同步残差现场验收。
- 用合规 RTK 无人机轨迹验证 100–300 m 三维误差。

### P1：分类与误报控制

- 采集校园无人机、固定翼、鸟、昆虫、云、树叶和远处飞机负样本。
- 按完整飞行批次切分数据，训练并导出 ONNX ROI 模型。
- 联调连续帧/双机投票，输出不足时保持 `unknown`。

### P1：生产前端网关

- 在校园内网用同源 HTTPS 网关代理网页、REST、WebSocket 和 MJPEG。
- 接入真实相机健康、事件录像和模型版本；增加断连、空数据、权限错误提示。
- 决定云端演示站点保持私有还是公开。当前站点仍为仅所有者访问，未携带所有者会话时可能显示“未找到站点”。

### P2：中心云台确认

- 确定云台型号和可见光长焦像元指标，建立固定相机轨迹到 PTZ 方位/俯仰/变焦的坐标变换。
- 接入海康 ISAPI/SDK 控制和回读，设置转动限位、人工接管与失败降级。
- 单独设计 1 km 白天/夜间识别验收；激光补光只能改善受照目标可见度，不能绕过焦距、像元、大气和安全约束。

## 9. 云端演示状态

- 演示地址：`https://yuntong-campus-airspace.wyx3443812800.chatgpt.site`
- 站点当前处于启用状态，最近版本已成功发布。
- 访问策略为仅所有者，未授权会话可能看到“未找到站点”；不要把该提示误判为源码或部署丢失。
- `frontend/.openai/hosting.json` 只包含 Sites 项目标识，不包含访问令牌。接手者没有所有者权限时不能直接发布到原站点，应由原所有者授权或创建新站点。

## 10. 验证基线

2026-09-04 打包前验证：

- Python：`10 passed`。存在一条 Starlette TestClient/httpx 弃用警告，不影响当前测试，但依赖升级时应处理。
- 前端：`9 passed`，TypeScript 类型检查通过，lint 通过，生产构建通过。

建议每次 AI 修改后至少执行：

```powershell
.venv\Scripts\python -m pytest
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
```

## 11. 给后续 AI 的约束

- 先阅读本文件、根目录 `README.md`、`frontend/README.md` 和相关 `docs/`，再修改代码。
- 不要把真实 RTSP 地址、密码、Cookie、令牌或学校内网信息写入源码、日志、截图或提交。
- 保持现有 TrackEvent 字段和枚举兼容；接口变更必须同时修改 Python、前端解析器和测试。
- 不要让真实模式在连接失败时自动显示模拟轨迹，这会造成运行人员误判。
- 几何质量不足、分类置信度不足或目标像素不足时输出 `unknown`，不要通过降低门限制造“识别成功”。
- 不实现自动干扰、截控或反制；告警与云台指向必须保留人工确认和审计。
- 修改前记录基线，修改后运行最小相关测试和全量验收命令。

## 12. 本移交包包含与排除内容

包含：Python/前端源码、测试、示例配置、演示标定、部署脚本、文档、前端图标和分享图。

排除：`.git` 历史、虚拟环境、`node_modules`、构建缓存、`dist`、临时打包目录、真实 `.env`、数据库、录像、训练数据和模型权重。接手者解压后应新建 Git 仓库或放入单位现有代码仓库。
