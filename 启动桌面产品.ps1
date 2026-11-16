$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    foreach ($packageDirectory in @('frontend', 'backend', 'desktop-app')) {
        if (-not (Test-Path -LiteralPath (Join-Path $taskRoot "$packageDirectory/node_modules"))) {
            & npm.cmd --prefix $packageDirectory ci
            if ($LASTEXITCODE -ne 0) { throw "$packageDirectory 依赖安装失败" }
        }
    }
    & npm.cmd --prefix desktop-app run desktop:run
    if ($LASTEXITCODE -ne 0) { throw '桌面端已退出，请查看上面的错误信息' }
} finally {
    Pop-Location
}
