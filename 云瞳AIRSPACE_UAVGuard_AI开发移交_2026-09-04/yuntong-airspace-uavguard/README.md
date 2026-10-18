# UAVGuard 校园双摄无人机识别与三维定位

这是一个面向两台固定海康网络摄像机的可运行MVP。系统从双路RTSP或
离线/合成视频中提取小型运动目标，建立二维轨迹，通过极线约束和稀疏
三角测量恢复校园ENU三维坐标，再用恒加速度卡尔曼滤波形成确认轨迹。

当前代码已经包含：

- 双路视频接入、断流状态和演示信号源；
- 三帧差分、MOG2、天空掩膜、微振动补偿和3/5二维确认；
- 时间门限、极线匹配、DLT三角测量、非线性重投影优化和误差协方差；
- 5/8三维轨迹确认、类别投票、告警Webhook和SQLite事件库；
- REST、WebSocket、双画面MJPEG和ENU俯视监控页面；
- 内参、外参、闪灯同步和RTK轨迹评测工具；
- Docker部署、现场配置、训练和验收说明。

## 能力边界

现有6 mm镜头不能在整个100–300米范围内保证小型无人机分类。系统按以下
分级输出，像素或置信度不足时必须返回`unknown`：

- 100–150米：0.5米多旋翼的分类与三维定位区；
- 150–250米：小目标探测、跟踪和定位区；
- 250–300米：1米以上固定翼目标扩展区。

代码不包含干扰、截控或自动反制。`models/`中不会附带伪造权重；现场模型
必须在完成合规飞行采集、按飞行批次隔离数据集并通过验收后部署。

## 本地演示

新增独立的[云瞳 AIRSPACE 三维监控前端](frontend/README.md)，包含模拟轨迹、双路视频布局、云台确认面板、事件中心与现有 API 的接入入口。原有后端与基础页面保持不变。

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\uavguard --config config/demo.yaml
```

Ubuntu：

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e '.[dev]'
.venv/bin/uavguard --config config/demo.yaml
```

打开 <http://127.0.0.1:8080>。演示模式生成两台相距40米的合成摄像机和一条
三维飞行轨迹，用来验证安装、API和几何链路，不代表真实识别准确率。

运行测试：

```bash
pytest
```

## 真实摄像头上线顺序

1. 按[摄像头设置](docs/CAMERA_SETTINGS.md)完成码流、安全和时间配置。
2. 按[现场部署与标定](docs/FIELD_DEPLOYMENT.md)完成安装、控制点测量及标定。
3. 复制`config/production.example.yaml`为`config/production.yaml`。
4. 复制`.env.example`为`.env`，写入完整且URL编码的RTSP地址。
5. 将通过审核的文件保存为`calibration/site-cam01.yaml`和
   `calibration/site-cam02.yaml`；未标记`valid: true`时服务会拒绝启动。
6. 没有模型时先保留空的`classifier.model_path`，完成几何验证；随后按
   [模型训练](docs/MODEL_TRAINING.md)部署ONNX权重。
7. 按[验收与运维](docs/ACCEPTANCE_AND_OPERATIONS.md)完成8小时负样本和受控飞行测试。

## API

- `GET /api/v1/health`
- `GET /api/v1/tracks/active?confirmed_only=true`
- `GET /api/v1/events?limit=100`
- `GET /api/v1/cameras/{camera_id}/frame.jpg`
- `GET /api/v1/cameras/{camera_id}/mjpeg`
- `WS /ws/tracks`
- 交互式接口文档：`/docs`

轨迹坐标为局部ENU米制坐标，x向东、y向北、z向上。每条轨迹同时输出位置
标准差和重投影误差，调用方不得把`candidate`或`unknown`当作确认无人机。

## 项目结构

```text
src/uavguard/       实时检测、几何、跟踪、服务和Web界面
config/             演示与生产配置模板
calibration/        标定格式、合成标定与无效现场模板
tools/              标定、闪灯同步、轨迹评测工具
tests/              几何、检测、跟踪、配置和API测试
docs/               相机、现场、训练、验收与运维手册
```

## 安全与隐私

真实密码只通过`.env`或密钥管理系统注入。生产相机必须处于独立VLAN，禁止
公网访问；生产配置强制使用HTTP Basic口令，并把访问时间、来源、路径和状态
写入审计表。建议在正式环境前置HTTPS反向代理和学校统一身份认证。天空掩膜
应排除宿舍窗户、道路和人员区域。录像保存、提示标识和公安备案由学校保卫及
法务部门按实际系统性质确认。
