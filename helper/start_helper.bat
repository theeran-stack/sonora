@echo off
setlocal
cd /d "%~dp0.."
echo Starting SONORA Windows Background Helper...

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "helper\sonora_helper.py"
    echo SONORA Helper started in background (.venv pythonw).
) else if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" "helper\sonora_helper.py"
    echo SONORA Helper started in background (.venv python).
) else (
    start "" pythonw "helper\sonora_helper.py"
    echo SONORA Helper started in background (system pythonw).
)
