$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$InstanceData = Join-Path $ProjectRoot 'runtime'
$InstancePython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
function Get-RegisteredInstance([string]$Kind) {
    $recordPath = Join-Path $InstanceData ($Kind + '.instance.json')
    if (-not (Test-Path -LiteralPath $recordPath)) { return $null }
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    if ($record.root -ne $ProjectRoot -or $record.data -ne $InstanceData) { throw '实例登记路径不匹配' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.processId)"
    if (-not $process) { return $null }
    if (($process.CreationDate.ToUniversalTime().Ticks -ne ([datetime]$record.started).ToUniversalTime().Ticks) -or $process.ExecutablePath -ne $record.executable -or $process.CommandLine -ne $record.command) { throw 'PID 已被其他进程使用，拒绝操作；请核对实例登记' }
    return $process
}
function Start-RegisteredInstance([string]$Kind, [string]$Module, [int]$Port=8510) {
    $logs = Join-Path $InstanceData 'logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $arguments = '-m ' + $Module + ' --data-dir "' + $InstanceData + '"'
    if ($Module -eq 'app.server') { $arguments += ' --port ' + $Port }
    $started = Start-Process -FilePath $InstancePython -ArgumentList $arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs ($Kind+'.out.log')) -RedirectStandardError (Join-Path $logs ($Kind+'.err.log')) -PassThru
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($started.Id)"
    # Windows venv python.exe can be a launcher; register the actual child interpreter.
    for ($probe=0; $probe -lt 20; $probe++) {
        $child = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($started.Id)" | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like ('* -m '+$Module+' --data-dir *') }
        if ($child) { $process=$child; break }
        Start-Sleep -Milliseconds 50
        if (-not (Get-Process -Id $started.Id -ErrorAction SilentlyContinue)) { break }
    }
    if (-not $process) { throw ($Kind+' 启动后退出，请检查日志') }
    @{processId=$process.ProcessId;root=$ProjectRoot;data=$InstanceData;executable=$process.ExecutablePath;command=$process.CommandLine;started=$process.CreationDate.ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstanceData ($Kind+'.instance.json')) -Encoding UTF8
}
