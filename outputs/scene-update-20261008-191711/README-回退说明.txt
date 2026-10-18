本次移植记录
项目：E:\Yuntong_Project-master\Yuntong_Project-master
来源：E:\Yuntong_Project-master(version2).zip
修改前 Git 提交：f117630c993c60c15e3a0edcb757c67778bbcb7f；修改前工作区无源码改动。

唯一修改的应用源码：desktop-app\components\spatial-scene.tsx。
采用新版：完整正负三平面网格、Y 轴刻度、正交投影、俯仰范围、左右键拖动平移、重置平移与操作提示。
保留旧版：triangulate 计算、groupSpatialTracks、目标圆点/颜色/发光/半径/连线/坐标标签、匹配诊断。
未移植新版的立体柱体目标与身高计算。摄像机/Python/GPU/模型/配置/检测/录像代码未修改。

original 目录为修改前原文件的逐字节备份。
original-project-source.zip 为修改前 Git 已跟踪源码完整快照，不含未跟踪的摄像机凭据、运行环境、录像、缓存。
change-record.json 记录本次修改文件的修改前后 SHA-256。

回退：关闭桌面应用，执行本目录 restore-old-scene.ps1；然后重新运行项目的 npm run desktop:run。
回退脚本只恢复本次修改的场景组件，不覆盖其他项目文件。
