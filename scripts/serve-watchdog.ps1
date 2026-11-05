# NBA Quant serve 看门狗：serve 进程死了就拉起来
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\serve-watchdog.ps1 start
#   powershell -ExecutionPolicy Bypass -File scripts\serve-watchdog.ps1 stop
#   powershell -ExecutionPolicy Bypass -File scripts\serve-watchdog.ps1 status
param(
    [ValidateSet("start", "stop", "status", "run")]
    [string]$Action = "status"
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root "data\serve-watchdog.log"
$intervalSec = 60

function Get-WatchdogProc {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
        Where-Object { $_.CommandLine -match "serve-watchdog\.ps1\s+run" }
}

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding UTF8
}

switch ($Action) {
    "start" {
        $procs = Get-WatchdogProc
        if ($procs) { Write-Output ("already running: " + (($procs | ForEach-Object { $_.ProcessId }) -join ", ")); return }
        # 独立进程 + 文件重定向，避免随父 shell 退出被连带杀
        $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                   "-File", (Join-Path $PSScriptRoot "serve-watchdog.ps1"), "run")
        Start-Process -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory $root `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $root "data\serve-watchdog.out") `
            -RedirectStandardError (Join-Path $root "data\serve-watchdog.err")
        Start-Sleep -Seconds 3
        $procs = Get-WatchdogProc
        if ($procs) { Write-Output ("started: " + (($procs | ForEach-Object { $_.ProcessId }) -join ", ")) }
        else { Write-Output "start failed" }
    }
    "stop" {
        $procs = Get-WatchdogProc
        if (-not $procs) { Write-Output "not running"; return }
        $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-Output ("stopped: " + (($procs | ForEach-Object { $_.ProcessId }) -join ", "))
    }
    "status" {
        $procs = Get-WatchdogProc
        $serve = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
            Where-Object { $_.CommandLine -match "app\.cli\s+serve" }
        $listening = [bool](Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
        Write-Output ("watchdog: " + ($(if ($procs) { "running" } else { "not running" })) +
                      " · serve: " + ($(if ($serve) { "running" } else { "not running" })) +
                      " · listening: " + $listening)
    }
    "run" {
        Log "watchdog started"
        while ($true) {
            try {
                $serve = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
                    Where-Object { $_.CommandLine -match "app\.cli\s+serve" }
                $listening = [bool](Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
                if (-not $serve -or -not $listening) {
                    Log "serve down (proc=$([bool]$serve) listening=$listening) -> restarting"
                    & (Join-Path $PSScriptRoot "serve-ctl.ps1") start | Out-Null
                    Log "restart issued"
                }
            } catch {
                Log "watchdog error: $($_.Exception.Message)"
            }
            Start-Sleep -Seconds $intervalSec
        }
    }
}
