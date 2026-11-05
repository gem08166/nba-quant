# NBA Quant watch 常驻控制脚本（无需管理员权限）
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\watch-ctl.ps1 start
#   powershell -ExecutionPolicy Bypass -File scripts\watch-ctl.ps1 stop
#   powershell -ExecutionPolicy Bypass -File scripts\watch-ctl.ps1 status
#   powershell -ExecutionPolicy Bypass -File scripts\watch-ctl.ps1 restart
param(
    [ValidateSet("start", "stop", "status", "restart")]
    [string]$Action = "status"
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\pythonw.exe"
$log = Join-Path $root "data\watch.log"

function Get-WatchProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match "app\.cli\s+watch" }
}

function Show-Ids($procs) {
    ($procs | ForEach-Object { $_.ProcessId }) -join ", "
}

switch ($Action) {
    "start" {
        $procs = Get-WatchProcesses
        if ($procs) { Write-Output ("already running: " + (Show-Ids $procs)); return }
        if (-not (Test-Path $py)) { throw "pythonw not found: $py" }
        Start-Process -FilePath $py -ArgumentList "-m", "app.cli", "watch" `
            -WorkingDirectory $root -WindowStyle Hidden
        Start-Sleep -Seconds 3
        $procs = Get-WatchProcesses
        if ($procs) { Write-Output ("started: " + (Show-Ids $procs)) }
        else { Write-Output "start failed - check data\watch.log" }
    }
    "stop" {
        $procs = Get-WatchProcesses
        if (-not $procs) { Write-Output "not running"; return }
        $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Write-Output ("stopped: " + (Show-Ids $procs))
    }
    "restart" {
        & $PSCommandPath -Action stop
        Start-Sleep -Seconds 1
        & $PSCommandPath -Action start
    }
    "status" {
        $procs = Get-WatchProcesses
        if ($procs) { Write-Output ("running: " + (Show-Ids $procs)) }
        else { Write-Output "not running" }
        if (Test-Path $log) {
            Write-Output "--- last log lines ---"
            Get-Content $log -Tail 10
        }
    }
}
