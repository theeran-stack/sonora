@echo off
setlocal
cd /d "%~dp0"
echo =========================================================
echo Installing SONORA Background Helper into Windows Startup
echo =========================================================

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET_BAT=%~dp0start_helper.bat"
set "SHORTCUT_PATH=%STARTUP_DIR%\SonoraHelper.lnk"

echo Creating shortcut in: %STARTUP_DIR%
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%TARGET_BAT%'; $s.WorkingDirectory = '%~dp0..'; $s.WindowStyle = 7; $s.Description = 'SONORA Music Platform Background Helper'; $s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo.
    echo [SUCCESS] SONORA Helper will now launch automatically when Windows starts!
    echo Shortcut created: %SHORTCUT_PATH%
) else (
    echo.
    echo [ERROR] Could not create startup shortcut automatically.
)
pause
