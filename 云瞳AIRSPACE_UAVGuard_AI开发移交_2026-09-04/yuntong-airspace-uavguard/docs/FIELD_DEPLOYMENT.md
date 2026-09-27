# 现场部署与标定

## 安装几何

- 默认基线40米，允许30–50米；两机光心坐标测量误差不超过0.2米；
- 两机共同对准约180米处，100–300米范围内交会角不低于5°；
- 支架必须刚性固定并防雷接地，避开空调、旗杆和会周期振动的薄墙；
- 画面下缘可以保留少量固定建筑特征用于振动校正，但必须用天空掩膜排除
  宿舍窗户、道路和人员区域；
- 避开太阳直射方向及大面积玻璃反光。

## 内参

使用刚性大尺寸棋盘，每台相机在最终分辨率、快门、焦点下采集至少30张，
覆盖画面中央、四角、不同距离和倾角。示例：

```bash
python tools/calibrate_intrinsics.py --images "captures/cam01/*.jpg" \
  --camera-id cam01 --cols 9 --rows 6 --square-size-m 0.10 \
  --output calibration/cam01-intrinsics.yaml
```

内参RMS必须≤0.8 px。调整镜头或更换相机后重新执行。

## ENU外参与控制点

1. 选择一个永久测量标记作为ENU原点；记录其WGS84坐标供外部转换使用。
2. 使用全站仪或RTK测量至少12个两机共同可见控制点，覆盖不同距离、高度和
   画面位置；不要全部放在同一平面或画面下缘。
3. 至少保留3个控制点作为`holdout`，不得参与解算。
4. 分别为两台相机填写CSV并运行：

```bash
python tools/calibrate_extrinsics.py \
  --intrinsics calibration/cam01-intrinsics.yaml \
  --points control-points-cam01.csv --output calibration/site-cam01.yaml
```

工具只有在内参≤0.8 px且留出点外参误差≤2 px时才写入`valid: true`。

## 上线门禁

- 两份现场标定均为`valid: true`；
- 校验文件中的`camera_id`和配置一致；
- 共同视场静态点的极线距离≤2.5 px；
- 闪灯同步P95≤20 ms；
- 8小时双路取流可用率≥99%；
- 完成学校审批、飞行测试许可、提示标识和数据保存制度确认。

