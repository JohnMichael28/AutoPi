#!/bin/bash
# AutoPi guardian launcher - sets the environment the UI needs on the
# Pi console (KMSDRM display, touch, no audio spam) and starts the app.
cd /home/john288/autopi-project
source venv/bin/activate

export SDL_VIDEODRIVER=kmsdrm
export XDG_RUNTIME_DIR=/run/user/1000
export SDL_AUDIODRIVER=dummy
unset DISPLAY

python3 main_ui.py