@echo off
rem NBA Quant resident services launcher (login autostart).
rem Both guardians use CREATE_BREAKAWAY_FROM_JOB so they are not killed
rem when the Startup wscript/cmd job object closes.
cd /d "D:\nba-quant-20261103"
set PY=D:\nba-quant-20261103\.venv\Scripts\python.exe
set LD=D:\nba-quant-20261103\scripts\launch_detached.py
start "" /b "%PY%" "%LD%" "%PY%" -m scripts.serve_watchdog
timeout /t 3 /nobreak >nul
start "" /b "%PY%" "%LD%" "%PY%" -m scripts.watch_guard
exit /b 0
