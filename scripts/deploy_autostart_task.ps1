# NBA Quant autostart - Windows Scheduled Task (at boot / at logon, no interactive session needed)
# Ensures the two supervisors (serve_watchdog / watch_guard) come back after a reboot,
# even when nobody logs in.
# Usage (PowerShell, admin recommended):
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_autostart_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_autostart_task.ps1 -Interactive
# Remove:
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_autostart_task.ps1 -Remove
#
# Default LogonType is S4U: runs at boot without storing a password and without
# an interactive logon. Outbound HTTP works; SMB shares would not (we don't use any).
#
# ASCII-only on purpose: Windows PowerShell 5.1 mis-parses non-ASCII scripts
# saved without a BOM.

param(
    [switch]$Remove,
    [switch]$Interactive
)

$TaskName = "NBA-AutoStart"
$ProjectRoot = "F:\nba-quant"
$PythonW = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Task '$TaskName' removed."
    exit
}

if (-not (Test-Path $PythonW)) { throw "pythonw.exe not found: $PythonW" }

$Action = New-ScheduledTaskAction -Execute $PythonW -Argument "-m scripts.boot_start" -WorkingDirectory $ProjectRoot

# At boot wait 2 min (disk/network settle); also catch the logon case.
$BootTrigger = New-ScheduledTaskTrigger -AtStartup
$BootTrigger.Delay = "PT2M"

$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$LogonTrigger.Delay = "PT45S"

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

if ($Interactive) {
    $LogonType = "Interactive"
} else {
    $LogonType = "S4U"
}
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType $LogonType -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action -Trigger @($BootTrigger, $LogonTrigger) -Settings $Settings -Principal $Principal `
    -Description "Start serve/watch supervisors at boot (S4U) - no login needed" -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
Write-Output "Task '$TaskName' registered."
Write-Output "  Triggers: at startup (+2min), at logon (+45s)"
Write-Output "  LogonType: $($task.Principal.LogonType)"
Write-Output "  Runner: $PythonW -m scripts.boot_start"
Write-Output "  WorkingDir: $ProjectRoot"
Write-Output "  Log: $ProjectRoot\data\boot_start.log"
