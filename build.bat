@echo off

cd /d "%~dp0"

echo [1/3] Check the virtual environment...

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

.venv\Scripts\pip.exe show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo.
    echo PyInstaller not found. Installing...
    .venv\Scripts\pip.exe install pyinstaller
)

echo.
echo [2/3] Building with PyInstaller...

.venv\Scripts\pyinstaller.exe --noconfirm --onefile --windowed --icon=icon/bb-yt-downloader.ico --name="BB YT Downloader" --add-data "icon;icon" --add-data "ffmpeg/windows;ffmpeg/windows" --add-data "deno/windows;deno/windows" --add-data "bb-yt-downloader.ui;." bb-yt-downloader.py
if errorlevel 1 (
    echo PyInstaller failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Cleaning up build files...

timeout /t 3 /nobreak >nul

for /L %%i in (1,1,5) do (

    rmdir /S /Q "build" >nul 2>&1

    if not exist "build" goto cleanup_done

    timeout /t 1 /nobreak >nul

)

:cleanup_done

if exist "BB YT Downloader.spec" (
    del /Q "BB YT Downloader.spec"
)

echo.
echo Build completed.
pause
