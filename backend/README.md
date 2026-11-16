# 云瞳产品后端

Node.js 22.13+。Web 和 Electron 使用同一服务、SQLite 数据与 HTTP 会话。

```powershell
npm ci
npm start
npm test
```

默认仅监听 http://127.0.0.1:8000，并服务 ../frontend/dist。首次在本机打开页面创建管理员；无固定账号密码。摄像机服务未就绪时业务管理仍可使用，监测状态显示离线。

## 运行配置

| 环境变量 | 用途 |
|---|---|
| PORT / HOST | HTTP 端口与监听地址，默认 8000 / 127.0.0.1 |
| YUNTONG_DATA_DIR | SQLite、录像、模型、标定任务的持久目录，默认 backend/data |
| YUNTONG_STATIC_DIR | Web 构建目录 |
| YUNTONG_ROOT | 桌面运行根目录 |
| YUNTONG_CAMERA_PROJECT_DIR | Python 摄像机项目目录 |
| YUNTONG_CAMERA_PYTHON | 已配置 PyAV、CUDA、TensorRT 的 Python |
| YUNTONG_DRONE_MODEL | 初始 ONNX 模型 |
| YUNTONG_CALIBRATION_PYTHON | 完整 OpenCV / NumPy 标定环境，默认回落摄像机 Python 或 python |
| YUNTONG_MODEL_PYTHON | 带 onnx 的模型验证环境 |
| YUNTONG_FFMPEG / YUNTONG_FFPROBE | 录像剪辑与时长探测程序，默认从 PATH 查找 |

首个管理员只能从直接访问本机的请求创建。生产部署应由运维先在本机初始化，再配置 HTTPS 反向代理；默认不开放公网。相机密码仅用于服务连接，不随设备 API 返回。应限制持久目录的操作系统访问权限并纳入备份。

## API

- /api/auth/status、setup、login、logout：初始化与 HttpOnly / SameSite 会话。
- /api/overview、status：真实运行总览与相机状态。
- /api/events：由空间目标生成、去重与持久保存的事件。/:id/review 提交一次人工复核；/:id/export 导出证据索引与轨迹。
- /api/media：检索真实本地录像与图片。/:id/content 支持 Range；/:id/download 下载原文件；POST /:id/export 接收起止秒数并用 FFmpeg 导出 MP4。
- /api/devices：接入配置，PATCH /:id 保存并重启摄像机服务；POST /:id/test 测试 TCP 连接。
- /api/models/config：读取和保存推理阈值、帧率、位置保持时间。/models/upload 接收原始 ONNX；/:id/validate 使用 onnx checker；/:id/activate 切换验证过的版本。
- /api/spatial/config：不依赖 GPU 的配置读取与 CPU 校验保存；/derive 推导两机外参。在线应用失败时响应 applied:false，并保留配置供后续启动加载。
- /api/calibration/capture、solve、job、apply：调用原有 AprilGrid 采集与标定程序。solve 必须提供实际测量的 baselineMeters，质量检查失败不会覆盖运行配置。
- /api/users、audit、settings：账号角色、审计日志与站点设置。

事件生成时尝试保存实际同步双帧截图；没有录制对应时段时不会虚构视频证据。浏览器录像兼容性取决于源文件编码；FFmpeg 缺失时仍可下载原片，但不能剪辑。

## 验证边界

自动测试覆盖会话、角色、首管理员约束、事件去重/复核、文件路径约束、Range 回放和配置校验。真实 RTSP 摄像机、TensorRT 推理性能及物理标定精度需在部署环境完成验收。重投影误差不等同于独立测量的空间定位精度。
