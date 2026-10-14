# 电商作业平台 · 重建桌面/项目快捷方式（R27：移动目录后重建）
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\create-shortcuts.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Wsh = New-Object -ComObject WScript.Shell
$files = @(
    @{ Lnk = Join-Path $Root "启动电商作业平台.lnk"; Target = Join-Path $Root "一键启动.ps1" },
    @{ Lnk = Join-Path $Root "停止电商作业平台.lnk"; Target = Join-Path $Root "一键停止.ps1" }
)
foreach ($f in $files) {
    $s = $Wsh.CreateShortcut($f.Lnk)
    $s.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$($f.Target)`""
    $s.WorkingDirectory = $Root
    $s.IconLocation = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe,0"
    $s.Description = "电商作业平台"
    $s.Save()
    Write-Host "[OK] 已生成快捷方式: $($f.Lnk)" -ForegroundColor Green
}
