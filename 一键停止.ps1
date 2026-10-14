# 电商作业平台 · 一键停止（双击本文件）
# 委托给 scripts/stop.ps1（R28 健壮版），保证单点逻辑
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\stop.ps1")
Read-Host "按回车退出"
