@echo off
setlocal
echo Removing SONORA Background Helper from Windows Startup...
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_PATH=%STARTUP_DIR%\SonoraHelper.lnk"

if exist "%SHORTCUT_PATH%" (
    del "%SHORTCUT_PATH%"
    echo [SUCCESS] SONORA Helper removed from Windows Startup.
) else (
    echo SONORA Helper was not found in Windows Startup.
)
pause
