#!/bin/bash

cd "$(dirname "$0")"

echo "[1/3] Check the virtual environment..."

if [ ! -d ".venv" ]; then
    echo ".venv not found. Creating virtual environment..."

    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo "Failed to create virtual environment."
        exit 1
    fi

    .venv/bin/pip install -r requirements.txt
fi

.venv/bin/pip show pyinstaller >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo
    echo "PyInstaller not found. Installing..."
    .venv/bin/pip install pyinstaller
fi

echo "[2/3] Building with PyInstaller..."

.venv/bin/pyinstaller --noconfirm --onefile --windowed --icon=icon/bb-yt-downloader.png --name="BB YT Downloader" --add-data "icon:icon" --add-data "ffmpeg/linux:ffmpeg/linux" --add-data "deno/linux:deno/linux" --add-data "bb-yt-downloader.ui:." bb-yt-downloader.py
if [ $? -ne 0 ]; then
    echo "PyInstaller failed."
    exit 1
fi

echo "[3/3] Cleaning up build files..."

sleep 3

for i in {1..5}; do

    rm -rf "build"

    if [ ! -d "build" ]; then
        break
    fi

    sleep 1

done

if [ -f "BB YT Downloader.spec" ]; then
    rm -f "BB YT Downloader.spec"
fi

echo
echo "Build completed."
