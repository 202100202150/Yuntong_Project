$ErrorActionPreference = 'Stop'
$recenterRecord = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'change-record.json') -Raw | ConvertFrom-Json
$campusDestination = Join-Path $recenterRecord.ProjectRoot 'desktop-app\lib\campus-buildings.ts'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'campus-buildings.before.ts') -Destination $campusDestination -Force
Write-Host 'Restored the previous campus building names, positions and sizes. Restart the application to load it.'
