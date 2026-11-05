# NBA Quant 常驻服务启动器
# 关键点：用 WMI (Win32_Process.Create) 创建守护进程，父进程是 WMI 服务，
# 因此不会被 IDE/终端的 Job Object (KILL_ON_JOB_CLOSE) 连带杀掉。
# 普通 Start-Process / schtasks / cmd start 都做不到这一点。
$ErrorActionPreference = "Stop"

$root = "D:\nba-quant-20261103"
$py = Join-Path $root ".venv\Scripts\python.exe"

function Start-Detached([string]$module) {
    $cmd = '"' + $py + '" -m ' + $module
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
        -Arguments @{ CommandLine = $cmd; CurrentDirectory = $root }
    if ($r.ReturnValue -ne 0) {
        Write-Output "WMI create failed for $module (rv=$($r.ReturnValue))"
    } else {
        Write-Output "$module -> pid $($r.ProcessId)"
    }
}

Start-Detached "scripts.serve_watchdog"
Start-Sleep -Seconds 2
Start-Detached "scripts.watch_guard"
