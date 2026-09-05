@echo off
cd /d "%~dp0"
title 视频爬虫 - 图形界面

echo ============================================
echo    视频爬虫 图形界面启动器
echo ============================================
echo.

rem 检查 Python
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python 命令!
    echo 请安装 Python 3.10 或更高版本：
    echo   https://www.python.org/downloads/
    echo 安装时务必勾选 Add Python to PATH
    echo.
    pause
    exit /b 1
)
echo [1/3] Python 已找到

rem 检查核心依赖
python -c "import yt_dlp, playwright, requests" >nul 2>nul
if errorlevel 1 (
    echo [2/3] 缺少依赖，正在安装（请稍候）...
    pip install -i https://pypi.tuna.tsinghua.edu.cn/simple yt-dlp Pillow playwright requests
    echo.
) else (
    echo [2/3] 核心依赖已就绪
)

rem 检查浏览器内核(抖音提取必需)
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()" >nul 2>nul
if errorlevel 1 (
    echo [提示] 浏览器内核缺失，正在安装 Chromium ~180MB...
    python -m playwright install chromium
    echo.
) else (
    echo [3/3] 浏览器内核已就绪
)

echo.
echo 正在启动图形界面窗口...
echo.
python main.py

echo.
echo [程序已退出]
pause