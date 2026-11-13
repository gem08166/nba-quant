# NBA Quant autostart - Windows Scheduled Task (at logon)
# Ensures the two supervisors (serve_watchdog / watch_guard) come back after a reboot.
# Usage (PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_autostart_task.ps1
# Remove:
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_autostart_task.ps1 -Remove
#
# ASCII-only on purpose: Windows PowerShell 5.1 mis-parses non-ASCII scripts
# saved without a BOM.

param(
    [switch]$Remove
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

# Wait 45s after logon so network/disk settle, then start the supervisors.
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Trigger.Delay = "PT45S"

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
    -Description "Start serve/watch supervisors at logon (idempotent)" -Force | Out-Null

Write-Output "Task '$TaskName' registered."
Write-Output "  Trigger: at logon (+45s)"
Write-Output "  Runner: $PythonW -m scripts.boot_start"
Write-Output "  WorkingDir: $ProjectRoot"
Write-Output "  Log: $ProjectRoot\data\boot_start.log"
