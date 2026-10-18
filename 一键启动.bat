@echo off
chcp 65001 >nul
title 云瞳系统 - 一键启动

echo ===================================================
echo               云瞳智能监控系统 启动器
echo ===================================================
echo.

echo [1/3] 正在安全清理 8000 端口...
:: 👇 优化了清理逻辑，忽略掉 PID 为 0 的系统进程
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    if not "%%a"=="0" (
        taskkill /F /PID %%a >nul 2>&1
    )
)
echo 清理完毕。
echo.

echo [2/3] 正在启动后端 (Node.js + Python GPU 推理)...
cd backend
start "云瞳后端服务" cmd /c "npm run dev"
cd ..
echo 后端服务已在新窗口启动。
echo.

echo 等待 3 秒，让后端和 GPU 模型完成初始化...
timeout /t 3 /nobreak >nul
echo.

echo [3/3] 正在启动前端界面...
:: 👇 注意：如果你的前端文件夹不叫 frontend，请将下面的 frontend 改成真实的文件夹名
if exist "frontend" (
    cd frontend
    start "云瞳前端界面" cmd /c "npm run dev"
    cd ..
) else (
    echo [错误] 找不到 frontend 文件夹，请确认项目路径。
)

echo.
echo 所有服务已启动！请查看弹出的前端窗口，或者手动访问 http://localhost:5173
echo.
pause