#!/bin/bash

cd "$(dirname "$0")"

echo 'Running "bb-yt-downloader.py"...'

if [ ! -d ".venv" ]; then
    echo ".venv not found. Creating virtual environment..."

    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo "Failed to create virtual environment."
        exit 1
    fi

    .venv/bin/pip install -r requirements.txt
fi

.venv/bin/python "bb-yt-downloader.py"

echo
echo "Application exited."
