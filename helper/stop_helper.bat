@echo off
setlocal
cd /d "%~dp0"
echo Stopping SONORA Windows Background Helper...

if exist "sonora_helper.pid" (
    set /p HELPER_PID=<"sonora_helper.pid"
    echo Terminating Helper Process PID: %HELPER_PID%
    taskkill /PID %HELPER_PID% /F 2>nul
    del "sonora_helper.pid" 2>nul
    echo SONORA Helper stopped successfully.
) else (
    echo Helper PID file not found. Ensuring all helper instances are stopped...
    powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*sonora_helper.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    echo Done.
)
