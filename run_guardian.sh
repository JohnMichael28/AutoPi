#!/bin/bash
# AutoPi guardian launcher - sets the environment the UI needs on the
# Pi console (KMSDRM display, touch, no audio spam) and starts the app.
# Works from wherever the project folder is (install.sh points at it).
cd "$(dirname "$(readlink -f "$0")")" || exit 1
source venv/bin/activate

export SDL_VIDEODRIVER=kmsdrm
XDG_RUNTIME_DIR=/run/user/$(id -u)
export XDG_RUNTIME_DIR
export SDL_AUDIODRIVER=dummy
unset DISPLAY

python3 main_ui.py
