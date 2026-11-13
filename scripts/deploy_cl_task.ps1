# NBA Quant CL Session - Windows Scheduled Task deployment
# Usage (PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_cl_task.ps1
# Remove:
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_cl_task.ps1 -Remove

param(
    [switch]$Remove
)

$TaskName = "NBA-CL-Session"
$ProjectRoot = "F:\nba-quant"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "scripts\cl_session.py"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Task '$TaskName' removed."
    exit
}

$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m scripts.cl_session --interval 180" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At "06:00"
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 16) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "NBA game-day CL capture session"

Write-Output "Task '$TaskName' registered."
Write-Output "  Trigger: daily 06:00"
Write-Output "  Script: $Script"
Write-Output "  WorkingDir: $ProjectRoot"
