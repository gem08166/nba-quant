# CL Session — 比赛日收盘线捕捉计划任务部署
# 用法（管理员 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_cl_task.ps1
# 或卸载：
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_cl_task.ps1 -Remove

param(
    [switch]$Remove
)

$TaskName = "NBA-CL-Session"
$ProjectRoot = "F:\量化\nba-quant-20261103"
$Python = "$ProjectRoot\.venv\Scripts\python.exe"
$Script = "$ProjectRoot\scripts\cl_session.py"
$LogDir = "$ProjectRoot\data\cl_logs"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Task '$TaskName' removed."
    exit
}

# 确保日志目录存在
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# 计划任务：每天 06:00（北京时间）触发，脚本自行管理等待/退出
$Action = New-ScheduledTaskAction -Execute $Python `
    -Argument "-m scripts.cl_session --interval 180" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At "06:00"

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 16) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
    -Description "比赛日 CL 捕捉：首场前 2h 启动，末场后 30min 休眠"

Write-Output "Task '$TaskName' registered."
Write-Output "  Trigger: daily 06:00"
Write-Output "  Script: $Script"
Write-Output "  WorkingDir: $ProjectRoot"
Write-Output "  Log: $LogDir\cl_session_YYYYMMDD.log"
