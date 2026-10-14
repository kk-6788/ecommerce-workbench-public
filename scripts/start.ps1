param([int]$Port=8510)
. (Join-Path $PSScriptRoot 'instance.ps1')
try {
    if (-not (Test-Path -LiteralPath $InstancePython)) { throw '缺少 .venv，请先按 README 安装运行环境' }
    $web = Get-RegisteredInstance 'web'
    if (-not $web) {
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw '8510 端口已占用。旧版运行实例请先关闭后再启动，不能误认其他实例。' }
        Start-RegisteredInstance 'web' 'app.server' $Port
    }
    if (-not (Get-RegisteredInstance 'worker')) { Start-RegisteredInstance 'worker' 'app.worker' }
    $healthy = $false
    for ($attempt=0; $attempt -lt 10; $attempt++) {
        try { $health=Invoke-RestMethod ("http://127.0.0.1:"+$Port+"/health") -TimeoutSec 2; if ($health.status -eq 'ok') { $healthy=$true; break } } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $healthy -or -not (Get-RegisteredInstance 'web') -or -not (Get-RegisteredInstance 'worker')) { throw '启动检查失败，请查看 runtime\logs' }
    Write-Host ("[OK] 平台已运行：http://127.0.0.1:"+$Port+'；首次使用可在登录页创建账号')
} catch { Write-Host ('[错误] '+$_.Exception.Message); exit 1 }
