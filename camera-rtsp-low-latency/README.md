# Camera RTSP Sidecar

这是云瞳单仓库中的 Python 摄像机 Sidecar，负责双路 RTSP 取流、TensorRT `person` 检测、目标跟踪、带框录像和带框抓拍。

完整配置、GPU 环境、运行和测试步骤请阅读仓库根目录的 [`README.md`](../README.md)。真实摄像机配置只能写入本目录的 `.env`；不要把 `.env` 提交到 Git。

新 6×6 AprilGrid 标定板已适配：tag36h11、标签黑外边长 55 mm、间隙 16.5 mm、2 bit 黑边。使用 `aprilgrid-6x6.json` 和 `spatial_calibration.py --board-type aprilgrid` 对应子命令。完整采集、标定和导入命令见 [AprilGrid使用说明](../outputs/aprilgrid-20261021/AprilGrid使用说明.md)。

