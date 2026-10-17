#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
    python3 launch.py "$@"
else
    echo 'Install Python 3.12 or newer: https://www.python.org/downloads/'
fi
read -r -p 'Press Enter to close.'
