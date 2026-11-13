# NBA Quant CL Session - Windows Scheduled Task deployment
# Runs at 06:00 daily without an interactive logon (S4U). pythonw = no console window.
# Usage (PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_cl_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_cl_task.ps1 -Interactive
# Remove:
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_cl_task.ps1 -Remove

param(
    [switch]$Remove,
    [switch]$Interactive
)

$TaskName = "NBA-CL-Session"
$ProjectRoot = "F:\nba-quant"
$Python = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Script = Join-Path $ProjectRoot "scripts\cl_session.py"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Task '$TaskName' removed."
    exit
}

$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m scripts.cl_session --interval 180" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At "06:00"
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 16) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
# Sleep-wake at 06:00 (closed lid / deep sleep may still prevent it)
$Settings.WakeToRun = $true

if ($Interactive) {
    $LogonType = "Interactive"
} else {
    $LogonType = "S4U"
}
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType $LogonType -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "NBA game-day CL capture session (pythonw, no console)" -Force | Out-Null

Write-Output "Task '$TaskName' registered."
Write-Output "  Trigger: daily 06:00 (WakeToRun)"
Write-Output "  LogonType: $((Get-ScheduledTask -TaskName $TaskName).Principal.LogonType)"
Write-Output "  Runner: $Python -m scripts.cl_session --interval 180"
Write-Output "  WorkingDir: $ProjectRoot"
Write-Output "  Log: $ProjectRoot\data\cl_logs\cl_session_YYYYMMDD.log"
