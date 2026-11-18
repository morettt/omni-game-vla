@echo off
cd /d "%~dp0"
where pyw.exe >nul 2>&1
if not errorlevel 1 (
    start "" /b pyw.exe -3 "%~dp0python\real_pvz_recorder.py"
    exit /b
)
where pythonw.exe >nul 2>&1
if not errorlevel 1 (
    start "" /b pythonw.exe "%~dp0python\real_pvz_recorder.py"
    exit /b
)
echo Python 3 was not found on PATH.
pause
exit /b 1
