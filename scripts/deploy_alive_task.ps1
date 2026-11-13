# NBA Quant alive monitor - Windows Scheduled Task (every 5 minutes)
# Runs without an interactive logon (S4U). pythonw = no console window.
# Usage (PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_alive_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_alive_task.ps1 -Interactive
# Remove:
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_alive_task.ps1 -Remove

param(
    [switch]$Remove,
    [switch]$Interactive
)

$TaskName = "NBA-Alive-Monitor"
$ProjectRoot = "F:\nba-quant"
$PythonW = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Script = Join-Path $ProjectRoot "scripts\alive_monitor.py"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Task '$TaskName' removed."
    exit
}

if (-not (Test-Path $PythonW)) { throw "pythonw.exe not found: $PythonW" }

$Action = New-ScheduledTaskAction -Execute $PythonW -Argument "-m scripts.alive_monitor --heartbeat" -WorkingDirectory $ProjectRoot

# Start soon, then repeat every 5 minutes for ~10 years (indefinite).
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

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
    -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
    -Description "Local liveness check (serve/watch/cl_session/guards) with ntfy push alerts" -Force | Out-Null

Write-Output "Task '$TaskName' registered."
Write-Output "  Repeat: every 5 minutes"
Write-Output "  LogonType: $((Get-ScheduledTask -TaskName $TaskName).Principal.LogonType)"
Write-Output "  Runner: $PythonW -m scripts.alive_monitor --heartbeat"
Write-Output "  WorkingDir: $ProjectRoot"
Write-Output "  Log: $ProjectRoot\data\alive_monitor.log"
