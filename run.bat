@echo off

cd /d "%~dp0"

echo Running "bb-yt-downloader.py"...

if not exist ".venv" (
    echo .venv not found. Creating virtual environment...

    py -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
    .venv\Scripts\pip.exe install -r requirements.txt
)

.venv\Scripts\python.exe "bb-yt-downloader.py"

echo.
echo Application exited.
pause
