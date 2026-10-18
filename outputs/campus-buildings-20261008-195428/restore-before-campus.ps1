$ErrorActionPreference = 'Stop'
$campusRecord = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'change-record.json') -Raw | ConvertFrom-Json
$sceneDestination = Join-Path $campusRecord.ProjectRoot 'desktop-app\components\spatial-scene.tsx'
$campusModule = Join-Path $campusRecord.ProjectRoot 'desktop-app\lib\campus-buildings.ts'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'spatial-scene.before.tsx') -Destination $sceneDestination -Force
if (Test-Path -LiteralPath $campusModule) {
    Remove-Item -LiteralPath $campusModule -Force
}
Write-Host 'Restored the scene before adding campus buildings. Restart the application to load it.'
