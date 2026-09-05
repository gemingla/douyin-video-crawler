# 视频爬虫 Release 构建脚本
# 用法: powershell -ExecutionPolicy Bypass -File build_release.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "=== 1/3 PyInstaller 打包 GUI (onefile, windowed) ==="
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name VideoCrawler `
  --collect-all customtkinter `
  --collect-all playwright `
  --exclude-module tkinter.test `
  --exclude-module pytest `
  main.py

if (-not (Test-Path "dist\VideoCrawler.exe")) {
    Write-Host "打包失败：未找到 dist\VideoCrawler.exe"
    exit 1
}
Write-Host ("dist\VideoCrawler.exe 大小: {0:N0} MB" -f ((Get-Item dist\VideoCrawler.exe).Length/1MB))

Write-Host "=== 2/3 浏览器内核归档 (chromium, 供无 Python 用户解压使用) ==="
$browserRoot = "$env:LOCALAPPDATA\ms-playwright"
$browserZip = "dist\chromium-win64.zip"
if (Test-Path $browserZip) { Remove-Item $browserZip }
Compress-Archive -Path "$browserRoot\chromium-1223", "$browserRoot\ffmpeg-1011" `
                 -DestinationPath $browserZip -CompressionLevel Optimal
Write-Host ("chromium-win64.zip 大小: {0:N0} MB" -f ((Get-Item $browserZip).Length/1MB))

Write-Host "=== 3/3 组装发布 zip ==="
$releaseZip = "dist\VideoCrawler-win64.zip"
if (Test-Path $releaseZip) { Remove-Item $releaseZip }
Compress-Archive -Path "dist\VideoCrawler.exe" `
                 -DestinationPath $releaseZip -CompressionLevel Optimal
Write-Host ("VideoCrawler-win64.zip 大小: {0:N0} MB" -f ((Get-Item $releaseZip).Length/1MB))
Write-Host "=== 完成: dist\VideoCrawler-win64.zip + dist\chromium-win64.zip ==="
