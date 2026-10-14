# 电商作业平台 · 备份脚本
# 用法: powershell -NoProfile -File scripts\backup.ps1 [-Output work\backup-check]
param([string]$Output = "work\backup-check")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$DataDir = Join-Path $Root "runtime"
& $VenvPy -m app.cli backup --data-dir $DataDir --output (Join-Path $Root $Output)
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[OK] 备份完成: $(Join-Path $Root $Output)"
