云瞳 AIRSPACE · Windows 便携版

使用方式
1. 完整解压整个文件夹，然后运行 YuntongWorkbench.exe。不要单独移动 EXE。
2. 首次使用请创建管理员账户。安装包没有预设账号或密码。
3. 设备、模型、事件、权限与 Web 版共用同一套页面和服务实现。
4. 关闭应用窗口后，应用会停止自己启动的本地摄像机服务。

数据目录
应用数据存放于 %APPDATA%\YuntongAIRSPACE\data。
运行路径配置文件为 %APPDATA%\YuntongAIRSPACE\runtime-config.json。
升级前备份该数据目录。不要把测试数据库复制到运行目录。

摄像机与 GPU
发布包包含共享 Vue 前端、Node 后端及生产依赖、Python 摄像机源码和仓库中的 drone.onnx。
目标机器仍须具备匹配的 NVIDIA 驱动、CUDA、TensorRT、Python 依赖。
在运行路径配置文件中设置 YUNTONG_CAMERA_PYTHON，指向实际 GPU Python 解释器。
也可以通过环境变量配置；环境变量优先。配置值必须为绝对路径。
YUNTONG_DRONE_MODEL 可覆盖随包模型；YUNTONG_CAMERA_PROJECT_DIR 可指向外置摄像机项目。
YUNTONG_MODEL_PYTHON 用于 ONNX 文件验证。
YUNTONG_CALIBRATION_PYTHON 用于标定板采集和参数计算，应包含完整 OpenCV 标定模块。
YUNTONG_FFMPEG 和 YUNTONG_FFPROBE 用于录像时长探测及片段导出。
无可用设备或 GPU 环境时，管理界面可启动，视频与推理显示未连接。

部署边界
本包不携带开发机器的摄像机密码、历史录像、数据库、CUDA/TensorRT 安装和 GPU 虚拟环境。
实际视频流、检测吞吐、双目标定误差需要在目标硬件和真实场地联调验证。
商业分发前需配置 Windows 代码签名和目标环境的安装部署方案。
