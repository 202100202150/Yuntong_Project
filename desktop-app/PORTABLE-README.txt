云瞳 AIRSPACE · Windows 便携版

使用方式
1. 完整解压整个文件夹，然后运行 YuntongWorkbench.exe。不要单独移动 EXE。
2. 首次使用请创建管理员账户。安装包没有预设账号或密码。
3. 实时监控与 Web 版共用同一套页面和服务实现。左侧三维、右侧视频墙同时显示，设备在工具栏中按需打开。
4. 可将三维或视频区域弹出到独立窗口，每种区域最多一个。关闭主窗口时会关闭分离窗口并停止本应用启动的本地摄像机服务。

数据目录
应用数据存放于 %APPDATA%\YuntongAIRSPACE\data。
运行路径配置文件为 %APPDATA%\YuntongAIRSPACE\runtime-config.json。
升级前备份该数据目录。不要把测试数据库复制到运行目录。

摄像机与 GPU
发布包包含共享 Vue 前端、本地三维模型、Node 后端及生产依赖、Python 摄像机源码和仓库中的 drone.onnx。
目标机器仍须具备匹配的 NVIDIA 驱动、CUDA、TensorRT、Python 依赖。
在运行路径配置文件中设置 YUNTONG_CAMERA_PYTHON，指向实际 GPU Python 解释器。
也可以通过环境变量配置；环境变量优先。配置值必须为绝对路径。
YUNTONG_DRONE_MODEL 可覆盖随包模型；YUNTONG_CAMERA_PROJECT_DIR 可指向外置摄像机项目。
YUNTONG_MODEL_PYTHON 用于 ONNX 文件验证。
YUNTONG_CALIBRATION_PYTHON 用于标定板采集和参数计算，应包含完整 OpenCV 标定模块。
YUNTONG_FFMPEG 和 YUNTONG_FFPROBE 用于录像时长探测及片段导出。
无可用设备或 GPU 环境时，管理界面可启动，视频与推理显示未连接。

多路视频与三维场景
新增视频通道使用 OpenCV Python 采集真实 RTSP 画面，沿用 YUNTONG_CAMERA_PYTHON 或系统 Python；该解释器需要安装 OpenCV。
三维场景、模型和解码器随包提供，无需访问作者网站加载。相机点位和无人机航迹使用真实定位数据；未完成模型与现场坐标配准时，不会自动伪造建筑上的相机位置。
单独打开三维或视频窗口共享当前登录，关闭主窗口会同时关闭分离窗口。
Web 版使用浏览器全屏；桌面标题栏的最小化、最大化/还原和关闭对应真实系统窗口操作。

部署边界
本包不携带开发机器的摄像机密码、历史录像、数据库、CUDA/TensorRT 安装和 GPU 虚拟环境。
实际视频流、检测吞吐、双目标定误差需要在目标硬件和真实场地联调验证。
商业分发前需配置 Windows 代码签名和目标环境的安装部署方案。
