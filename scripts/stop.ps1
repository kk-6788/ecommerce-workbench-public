. (Join-Path $PSScriptRoot 'instance.ps1')
$failed = $false
foreach ($kind in @('worker','web')) {
    try {
        $process = Get-RegisteredInstance $kind
        if ($process) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
            Wait-Process -Id $process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
            if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) { throw '进程仍在运行，保留登记供核对' }
        }
        $record = Join-Path $InstanceData ($kind+'.instance.json')
        if (Test-Path -LiteralPath $record) { Remove-Item -LiteralPath $record -Force }
    } catch { $failed=$true; Write-Host ('[错误] '+$kind+'：'+$_.Exception.Message) }
}
if ($failed) { exit 1 }
Write-Host '[OK] 本实例已停止，数据保留。'
