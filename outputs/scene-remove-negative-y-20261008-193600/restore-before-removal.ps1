$ErrorActionPreference = 'Stop'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'spatial-scene.before.tsx') -Destination 'E:\Yuntong_Project-master\Yuntong_Project-master\desktop-app\components\spatial-scene.tsx' -Force
Write-Output '已恢复删除 Y 轴负方向网格之前的场景。重新运行 npm run desktop:run 生效。'