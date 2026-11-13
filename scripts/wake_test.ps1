# RTC wake test: log a timestamp, then self-unregister the test task.
# ASCII-safe log content; path may contain non-ASCII (F:\量化\...) which is fine in PS strings.

$log = 'F:\量化\nba-quant-20261103\data\wake_test.log'
try {
  "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) woke from shutdown + task ran" |
    Out-File -FilePath $log -Append -Encoding utf8
} catch {}

Unregister-ScheduledTask -TaskName 'RTCWakeTest' -Confirm:$false -ErrorAction SilentlyContinue
