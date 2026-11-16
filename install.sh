#!/bin/bash
# AutoPi one-command setup / rebuild.
#
# Fresh SD card (Raspberry Pi OS Lite, user john288, WiFi set in the imager):
#     git clone https://github.com/JohnMichael28/AutoPi.git ~/autopi-project
#     cd ~/autopi-project && bash install.sh
#
# Safe to run again on a working Pi: every step checks first and skips what
# is already done. It never overwrites an existing llama-server service,
# config.json, trained model, or your .bash_profile autostart.
#
#     bash install.sh --dry-run   show what it would do, change nothing
#
# Settings (model download, folders) live in the "install" block of
# config.json / config.example.json.

PROJECT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ME="$(id -un)"
DRY=0
[ "$1" = "--dry-run" ] && DRY=1

mkdir -p "$PROJECT/logs"
LOG="$PROJECT/logs/install.log"
OK=(); WARN=(); FAIL=()

say()  { echo "[autopi] $*" | tee -a "$LOG"; }
run()  {                       # run a command (or just print it in --dry-run)
    if [ "$DRY" = "1" ]; then echo "  would run: $*"; return 0; fi
    "$@" >>"$LOG" 2>&1
}
ok()   { OK+=("$1");   say "OK    $1"; }
warn() { WARN+=("$1"); say "WARN  $1"; }
fail() { FAIL+=("$1"); say "FAIL  $1"; }

# Settings: config.json if present, else config.example.json, else default.
CONFIG="$PROJECT/config.json"
[ -f "$CONFIG" ] || CONFIG="$PROJECT/config.example.json"
setting() {
    python3 - "$CONFIG" "$1" "$2" <<'PY' 2>/dev/null || echo "$2"
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("install", {}).get(sys.argv[2], sys.argv[3]))
except Exception:
    print(sys.argv[3])
PY
}
# shellcheck disable=SC2088  # the "~" is expanded on purpose by home_path
home_path() { local p="$1"; echo "${p/#\~/$HOME}"; }   # "~/x" -> /home/you/x
# shellcheck disable=SC2088
LLAMA_DIR="$(home_path "$(setting llama_dir "~/llama.cpp")")"
LLAMA_REPO="$(setting llama_repo 'https://github.com/ggml-org/llama.cpp')"
# shellcheck disable=SC2088
MODEL_DIR="$(home_path "$(setting model_dir "~/models")")"
MODEL_FILE="$(setting model_file 'Qwen_Qwen3-0.6B-Q4_K_M.gguf')"
MODEL_URL="$(setting model_url 'https://huggingface.co/bartowski/Qwen_Qwen3-0.6B-GGUF/resolve/main/Qwen_Qwen3-0.6B-Q4_K_M.gguf')"
LLAMA_ARGS="$(setting llama_args '-t 3 -c 2048 --host 127.0.0.1 --port 8080')"
LLAMA_CPUS="$(setting llama_cpus '1,2,3')"
SMART="$(setting smart_model 'True')"
SMART_FILE="$(setting smart_model_file 'Qwen_Qwen3-1.7B-Q4_K_M.gguf')"
SMART_URL="$(setting smart_model_url 'https://huggingface.co/bartowski/Qwen_Qwen3-1.7B-GGUF/resolve/main/Qwen_Qwen3-1.7B-Q4_K_M.gguf')"
SMART_ARGS="$(setting smart_llama_args '-t 3 -c 2048 --host 127.0.0.1 --port 8081')"
SMART_MIN_RAM="$(setting smart_min_ram_mb '3500')"
VOSK_DIR="$(setting vosk_dir 'vosk-model-small-en-us-0.15')"
VOSK_URL="$(setting vosk_url 'https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip')"

say "=== install $(date '+%Y-%m-%d %H:%M:%S')  project=$PROJECT user=$ME dry=$DRY ==="
if [ "$(id -u)" = "0" ]; then
    echo "Run this as your normal user (john288), not with sudo. It asks for sudo itself."
    exit 1
fi
[ "$DRY" = "1" ] || sudo -v || { echo "sudo is needed"; exit 1; }

# ---- 1. system packages ----------------------------------------------------
say "1/12 system packages"
# python3-pygame = Debian's pygame, built against the system SDL that has the
# KMSDRM (no-desktop) display driver - the pip wheel may not have it.
REQUIRED="git python3-full python3-venv python3-dev python3-pygame build-essential cmake curl unzip"
OPTIONAL="libportaudio2 libopenblas0 network-manager libcurl4-openssl-dev libsdl2-2.0-0 libsdl2-ttf-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0"
run sudo apt-get update
if run sudo apt-get install -y $REQUIRED; then ok "required packages"; else fail "required packages (see logs/install.log)"; fi
for pkg in $OPTIONAL; do            # one at a time: a renamed package can't sink the rest
    run sudo apt-get install -y "$pkg" || warn "optional package $pkg not installed"
done

# ---- 2. Pi settings: console autologin + device permissions ------------------
say "2/12 Pi settings"
if command -v raspi-config >/dev/null; then
    if run sudo raspi-config nonint do_boot_behaviour B2; then ok "boot to console with autologin"; else warn "console autologin not set"; fi
else
    warn "raspi-config not found - set console autologin by hand"
fi
if run sudo usermod -aG video,render,input,dialout "$ME"; then ok "groups: video render input dialout"; else warn "could not add groups"; fi

# ---- 3. Python environment ---------------------------------------------------
say "3/12 Python venv + packages"
cd "$PROJECT" || exit 1
# --system-site-packages: the venv uses the system pygame (KMSDRM display).
[ -d venv ] || run python3 -m venv venv --system-site-packages
run venv/bin/pip install --upgrade pip
if run venv/bin/pip install -r requirements.txt; then ok "python packages"; else fail "python packages (see logs/install.log)"; fi

# ---- 4. config + folders -----------------------------------------------------
say "4/12 config + folders"
if [ -f config.json ]; then
    ok "config.json kept"
else
    run cp config.example.json config.json && warn "config.json created from example - copy yours from the laptop"
fi
run mkdir -p data data/archive logs reports models
[ -f run_guardian.sh ] && run chmod +x run_guardian.sh
ok "folders ready"

# ---- 5. voice model (offline speech-to-text) ---------------------------------
say "5/12 voice model"
if [ -d "$VOSK_DIR" ]; then
    ok "voice model present"
elif run curl -fL -o /tmp/vosk.zip "$VOSK_URL" && run unzip -q -o /tmp/vosk.zip -d "$PROJECT"; then
    run rm -f /tmp/vosk.zip
    ok "voice model downloaded"
else
    warn "voice model download failed - voice input stays off"
fi

# ---- 6. llama.cpp (the on-Pi AI engine) --------------------------------------
say "6/12 llama.cpp (first build takes 20-40 minutes on a Pi 4)"
if [ -x "$LLAMA_DIR/build/bin/llama-server" ]; then
    ok "llama-server already built"
else
    [ -d "$LLAMA_DIR" ] || run git clone --depth 1 "$LLAMA_REPO" "$LLAMA_DIR"
    if run cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_CURL=OFF \
       && run cmake --build "$LLAMA_DIR/build" --config Release -j 3 --target llama-server; then
        ok "llama-server built"
    else
        fail "llama.cpp build failed (see logs/install.log)"
    fi
fi

# ---- 7. AI model file --------------------------------------------------------
say "7/12 AI model"
run mkdir -p "$MODEL_DIR"
if [ -s "$MODEL_DIR/$MODEL_FILE" ]; then
    ok "AI model present"
elif run curl -fL -o "$MODEL_DIR/$MODEL_FILE.part" "$MODEL_URL" \
     && run mv "$MODEL_DIR/$MODEL_FILE.part" "$MODEL_DIR/$MODEL_FILE"; then
    ok "AI model downloaded"
else
    run rm -f "$MODEL_DIR/$MODEL_FILE.part"
    fail "AI model download failed - check WiFi and model_url"
fi

# ---- 8. llama-server service (starts the AI at every boot) -------------------
say "8/12 llama-server service"
UNIT=/etc/systemd/system/llama-server.service
if [ -f "$UNIT" ]; then
    ok "llama-server service kept (already set up)"
else
    TMP="$(mktemp)"
    cat >"$TMP" <<EOF
[Unit]
Description=llama.cpp server for AutoPi (local SLM)
After=network.target

[Service]
User=$ME
ExecStart=/usr/bin/taskset -c $LLAMA_CPUS $LLAMA_DIR/build/bin/llama-server -m $MODEL_DIR/$MODEL_FILE $LLAMA_ARGS
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    if run sudo install -m 644 "$TMP" "$UNIT" && run sudo systemctl daemon-reload \
       && run sudo systemctl enable --now llama-server; then
        ok "llama-server service installed (pinned to CPUs $LLAMA_CPUS)"
    else
        fail "llama-server service"
    fi
    rm -f "$TMP"
fi

# ---- 8b. SMART model for TALK TO AUTOPI (Qwen3-1.7B, its own server) ----------
# Screens keep the fast 0.6B model on 8080. Conversation uses this bigger,
# smarter one on 8081 (~2-3x slower). If it's missing, TALK falls back to 0.6B.
say "8b/12 smart conversation model"
RAM_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)"
SUNIT=/etc/systemd/system/llama-server-smart.service
if [ "$SMART" != "True" ] && [ "$SMART" != "true" ]; then
    ok "smart model off in config"
elif [ "${RAM_MB:-0}" -lt "$SMART_MIN_RAM" ]; then
    warn "smart model skipped: ${RAM_MB} MB RAM (needs ${SMART_MIN_RAM}) - TALK uses the fast model"
else
    if [ -s "$MODEL_DIR/$SMART_FILE" ]; then
        ok "smart model present"
    elif run curl -fL -o "$MODEL_DIR/$SMART_FILE.part" "$SMART_URL" \
         && run mv "$MODEL_DIR/$SMART_FILE.part" "$MODEL_DIR/$SMART_FILE"; then
        ok "smart model downloaded (~1.1 GB)"
    else
        run rm -f "$MODEL_DIR/$SMART_FILE.part"
        warn "smart model download failed - TALK uses the fast model"
    fi
    if [ -f "$SUNIT" ]; then
        ok "smart model service kept (already set up)"
    elif [ -s "$MODEL_DIR/$SMART_FILE" ] || [ "$DRY" = "1" ]; then
        TMP="$(mktemp)"
        cat >"$TMP" <<EOF
[Unit]
Description=llama.cpp SMART server for TALK TO AUTOPI (Qwen3-1.7B)
After=network.target llama-server.service

[Service]
User=$ME
ExecStart=/usr/bin/taskset -c $LLAMA_CPUS $LLAMA_DIR/build/bin/llama-server -m $MODEL_DIR/$SMART_FILE $SMART_ARGS
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        if run sudo install -m 644 "$TMP" "$SUNIT" && run sudo systemctl daemon-reload \
           && run sudo systemctl enable --now llama-server-smart; then
            ok "smart model service installed (port 8081)"
        else
            warn "smart model service not installed - TALK uses the fast model"
        fi
        rm -f "$TMP"
    fi
fi

# ---- 9. permission for the report-sharing WiFi (nmcli) -----------------------
say "9/12 report-sharing WiFi permission"
RULE=/etc/sudoers.d/autopi-nmcli
if [ -f "$RULE" ]; then
    ok "nmcli permission kept"
else
    TMP="$(mktemp)"
    echo "$ME ALL=(root) NOPASSWD: /usr/bin/nmcli" >"$TMP"
    # visudo -c checks the rule BEFORE it is installed, so a typo can't break sudo.
    if run sudo visudo -cf "$TMP" && run sudo install -m 440 "$TMP" "$RULE"; then
        ok "nmcli permission added"
    else
        fail "nmcli permission (AUTOPI WIFI sharing may say Not authorized)"
    fi
    rm -f "$TMP"
fi

# ---- 10. SD-card care: system logs in RAM ------------------------------------
say "10/12 SD-card care"
JCONF=/etc/systemd/journald.conf.d/autopi.conf
if [ -f "$JCONF" ]; then
    ok "system logs already kept in RAM"
else
    TMP="$(mktemp)"
    printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=30M\n' >"$TMP"
    if run sudo mkdir -p /etc/systemd/journald.conf.d \
       && run sudo install -m 644 "$TMP" "$JCONF" \
       && run sudo systemctl restart systemd-journald; then
        ok "system logs kept in RAM (fewer SD writes; AutoPi keeps its own logs)"
    else
        warn "could not move system logs to RAM"
    fi
    rm -f "$TMP"
fi

# ---- 11. start AutoPi on the screen at boot ----------------------------------
say "11/12 autostart"
PROFILE="$HOME/.bash_profile"
if [ -f "$PROFILE" ] && grep -q -e "main_ui.py" -e "run_guardian.sh" "$PROFILE"; then
    ok "autostart kept (.bash_profile already launches AutoPi)"
elif [ "$DRY" = "1" ]; then
    echo "  would add the tty1 autostart block to $PROFILE"
else
    cat >>"$PROFILE" <<EOF

# Auto-launch AutoPi guardian on console login (tty1 only, where KMSDRM works;
# SSH logins are left alone). Added by install.sh.
if [ "\$(tty)" = "/dev/tty1" ]; then
    $PROJECT/run_guardian.sh
fi
EOF
    ok "autostart added to .bash_profile"
fi

# ---- summary -----------------------------------------------------------------
echo
say "=== done: ${#OK[@]} ok, ${#WARN[@]} warnings, ${#FAIL[@]} failed ==="
for w in "${WARN[@]}"; do echo "  WARN  $w"; done
for f in "${FAIL[@]}"; do echo "  FAIL  $f"; done
echo
echo "Not in git (on purpose) - run these ON YOUR LAPTOP, in the project folder:"
echo "  scp config.json $ME@$(hostname).local:$PROJECT/"
echo "  scp models/anomaly_model.joblib $ME@$(hostname).local:$PROJECT/models/"
echo "Without the model the ML monitor stays off until you retrain."
echo "Module list: run DIAGNOSTICS > FIND MODULES once in the car."
echo
echo "Then: sudo reboot   (AutoPi starts on the screen by itself)"
echo "Full log: $LOG"
[ "${#FAIL[@]}" = "0" ]
