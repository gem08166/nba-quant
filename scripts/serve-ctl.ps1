# NBA Quant serve 常驻控制脚本（无需管理员权限）
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\serve-ctl.ps1 start
#   powershell -ExecutionPolicy Bypass -File scripts\serve-ctl.ps1 stop
#   powershell -ExecutionPolicy Bypass -File scripts\serve-ctl.ps1 status
#   powershell -ExecutionPolicy Bypass -File scripts\serve-ctl.ps1 restart
param(
    [ValidateSet("start", "stop", "status", "restart")]
    [string]$Action = "status"
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\pythonw.exe"
$port = 8000

function Get-ServeProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match "app\.cli\s+serve" }
}

function Show-Ids($procs) {
    ($procs | ForEach-Object { $_.ProcessId }) -join ", "
}

function Test-Listening {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

switch ($Action) {
    "start" {
        $procs = Get-ServeProcesses
        if ($procs) { Write-Output ("already running: " + (Show-Ids $procs)); return }
        if (-not (Test-Path $py)) { throw "pythonw not found: $py" }
        # pythonw 无控制台：不给 uvicorn 输出句柄会在启动写 stdout 时直接崩，
        # 必须重定向到文件（文件句柄不随父进程退出而失效）
        $outLog = Join-Path $root "data\serve.out"
        $errLog = Join-Path $root "data\serve.err"
        Start-Process -FilePath $py -ArgumentList "-m", "app.cli", "serve", "--host", "0.0.0.0", "--port", "$port" `
            -WorkingDirectory $root -WindowStyle Hidden `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        # pythonw 无控制台，等端口起来才算成功
        $ok = $false
        foreach ($i in 1..10) {
            Start-Sleep -Seconds 1
            if (Test-Listening) { $ok = $true; break }
        }
        $procs = Get-ServeProcesses
        if ($ok -and $procs) { Write-Output ("started: " + (Show-Ids $procs)) }
        else { Write-Output "start failed - run 'python -m app.cli serve' in foreground to see the error" }
    }
    "stop" {
        $procs = Get-ServeProcesses
        if (-not $procs) { Write-Output "not running"; return }
        $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-Output ("stopped: " + (Show-Ids $procs))
    }
    "status" {
        $procs = Get-ServeProcesses
        if ($procs) {
            Write-Output ("running: " + (Show-Ids $procs) + " listening=" + (Test-Listening))
        } else { Write-Output "not running" }
    }
    "restart" {
        & $PSScriptRoot\serve-ctl.ps1 stop
        Start-Sleep -Seconds 1
        & $PSScriptRoot\serve-ctl.ps1 start
    }
}
