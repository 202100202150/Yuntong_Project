$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\Yuntong_Project-master\Yuntong_Project-master'
$originalFile = Join-Path $PSScriptRoot 'original\desktop-app\components\spatial-scene.tsx'
$targetFile = Join-Path $projectRoot 'desktop-app\components\spatial-scene.tsx'
Copy-Item -LiteralPath $originalFile -Destination $targetFile -Force
Write-Output '已恢复本次修改前的 spatial-scene.tsx。重新运行 npm run desktop:run 即可构建并使用旧版场景。'
