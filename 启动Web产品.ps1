$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    foreach ($packageDirectory in @('frontend', 'backend')) {
        if (-not (Test-Path -LiteralPath (Join-Path $taskRoot "$packageDirectory/node_modules"))) {
            & npm.cmd --prefix $packageDirectory ci
            if ($LASTEXITCODE -ne 0) { throw "$packageDirectory 依赖安装失败" }
        }
    }
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    Write-Host '启动后可在浏览器打开终端显示的本地地址。按 Ctrl+C 关闭服务。'
    & npm.cmd --prefix backend start
    if ($LASTEXITCODE -ne 0) { throw '服务已退出，请查看上面的错误信息' }
} finally {
    Pop-Location
}
