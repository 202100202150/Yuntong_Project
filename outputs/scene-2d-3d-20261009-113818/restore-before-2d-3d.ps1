$ErrorActionPreference = 'Stop'
$modeRecord = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'change-record.json') -Raw | ConvertFrom-Json
$modeFiles = @('desktop-app\components\spatial-scene.tsx', 'desktop-app\components\spatial-scene.css', 'desktop-app\lib\campus-buildings.ts')
foreach ($modeFile in $modeFiles) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $modeFile) -Destination (Join-Path $modeRecord.ProjectRoot $modeFile) -Force
}
Write-Host 'Restored the scene before adding 2D/3D switching. Restart the application to load it.'
