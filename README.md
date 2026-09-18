# AutoPi — Intelligent In-Car Diagnostic Guardian

> A personal project built to explore embedded systems, real-time vehicle data,
> and applied ML — from OBD-II protocol handling to on-device anomaly detection
> and a fully offline local language model.

A Raspberry Pi-powered, touchscreen car diagnostic device that reads live OBD-II
data, explains it in plain English, and detects problems before they happen.
Built around a Fallout-style terminal "guardian" that reacts to your car's
health in real time. Runs entirely offline in the vehicle — no cloud, no phone.

Tested on a 2024 Cadillac XT5 350T (2.0T) and a 2023 Subaru Outback Wilderness
(FA24 turbo).

## What it does

- **Guardian face dashboard** — a pixel face that changes mood/color based on
  real vehicle state (coolant, codes, fuel trims, drive mode).
- **Four drive modes** — Highway, Track, Adventure, Camp — each showing the
  gauges that matter for that context, with sourced warning thresholds.
- **Live zoned gauges** — RPM, MAF, air/fuel, and load as green/yellow/red
  bars (tachometer-style), plus a coolant trend line. Design backed by
  automotive gauge research.
- **Full diagnostics** — read/pending/permanent/readiness codes, freeze frame,
  clear codes (with a two-tap confirm gate), each with a plain-English AI
  explanation.
- **Fuel system analysis** — live short/long-term fuel trims + O2 voltage with a
  health verdict and AI diagnosis of the actual numbers.
- **Warnings tab** — rule-based warnings AND ML anomalies together, timestamped,
  each one tappable for an on-demand AI explanation with concrete next actions.
- **Virtual dyno** — estimates horsepower from an acceleration pull (clearly
  labeled ESTIMATED — it's an OBD acceleration method, not a real dyno).
- **Tuning monitor** — live knock/timing watch with debounced alarms.
- **Offline voice assistant** — push-to-talk questions answered by the on-device
  language model, using your car's live data. Fully offline speech recognition
  (Vosk) + fully offline answers (local SLM).
- **ML anomaly detection** — a trained, context-aware Isolation Forest runs
  on-device and flags readings that deviate from your car's learned normal.

## The AI system — a tiered, offline-first brain

The device never depends on the cloud. Its intelligence is layered so it always
has an answer, and the reliability comes from the harness around the models, not
from any single model being large.

**Tier 1 — Local SLM (primary).** A quantized Qwen3-0.6B language model runs
directly on the Pi via `llama.cpp` (`llama-server`, OpenAI-compatible API). It
answers push-to-talk questions and explains warnings/anomalies with **zero
network** — anywhere, instantly. Because a 0.6B model is weak, it's kept on a
tight leash: a fixed car-diagnostic system prompt, the driver's live readings
supplied in every prompt, reasoning-mode disabled for speed, a short token cap,
and a low temperature for steady answers. It runs as a `systemd` service pinned
to 3 of the Pi's 4 cores (`taskset`) so it can never starve the real-time UI,
and auto-starts on boot.

**Tier 2 — Home Ollama (optional).** When the device is on home WiFi it can fall
back to a larger model on a home server for richer answers. Off-grid, this tier
is simply skipped — the local SLM already answered.

**Tier 3 — Offline dictionary.** Built-in plain-English descriptions of common
OBD-II codes as a final fallback.

## The ML anomaly system — contextual detection

The goal is a model that learns *your specific car's* normal behavior and flags
anomalies (fuel drift, developing knock, O2 aging, coolant creep, abnormal load)
before they become failures.

**Approach:** unsupervised anomaly detection with an Isolation Forest, chosen
after reviewing the vehicle/sensor anomaly-detection literature — lightweight
enough for a Pi, needs only normal data (no labeled failures), and catches the
multivariate correlations single-threshold alarms miss.

**Three-phase pipeline:**
1. **Collect** — the device silently logs clean, engine-running snapshots to CSV
   during normal driving, building a "normal" dataset.
2. **Train** (offline, laptop) — a SQL feature-engineering stage forward-fills
   tiered PIDs and derives rolling-window features (moving averages + deltas) so
   the model sees trends, not just instantaneous values; then it trains the
   Isolation Forest with per-feature baselines for interpretability.
3. **Deploy** — the trained model runs on the Pi for real-time inference and
   names *which* sensor is off, not just "something's wrong."

**Contextual detection (the key refinement).** A single global baseline produced
too many false positives, because engine "normal" is context-dependent: a 65°C
coolant is normal cold-starting but abnormal warm; high load is normal climbing
but not at idle. So the model is **regime-aware** — it segments driving into
operating regimes (cold-start / high-load / normal), learns a separate baseline
per regime, and judges each live reading against the normal *for its current
state* (formally, P(reading | regime) rather than P(reading)). Regimes were
chosen from the real logged data, kept deliberately coarse to avoid the
over-segmentation failure mode noted in the literature. This cut the
false-positive rate from ~8-10% to ~5.7% while still catching injected faults,
and specifically fixed cold-start false alarms.

Detection combines the forest (unusual multivariate *combinations*) with a
per-feature z-score guard (any single sensor far outside its regime's normal),
and a persistence state machine so momentary blips read "watch" and only
sustained anomalies reach "elevated." The ML is surfaced as an **advisory**
alongside the rule-based warning system, which remains the authoritative
real-time safety layer.

## Hardware

- Raspberry Pi 4 Model B (4GB)
- 5" Elecrow HDMI capacitive touchscreen (800×480)
- OBDLink EX USB OBD-II adapter
- USB audio adapter + mic (voice)
- 12V→5V buck converter, fuse tap (in-vehicle power)

## Architecture

- **Model / View / Adapter** separation. The car is accessed only through a
  `Vehicle` class; the UI reads cached snapshots and never blocks on OBD I/O.
- **Background threading** — OBD reads and the ML logger run off the 60fps
  render loop. All OBD access is serialized through a lock, since python-OBD is
  not thread-safe.
- **Tiered polling** — fast-changing PIDs read every cycle, slow ones rotated
  in, so no single read cycle overloads the connection.
- **Tiered, offline-first AI** — local SLM primary, home Ollama optional,
  offline dictionary as a floor. The device never pretends to have data it
  doesn't.
- **Resource isolation** — the SLM is pinned to 3 cores so inference can't
  starve the UI/OBD loop on the 4th.
- **Console/KMSDRM display** — full-screen pygame app on the Pi console
  (boot-to-console + autologin), drawing directly via KMSDRM, no desktop.

## Engineering notes: the connection-stability investigation

Early development ran on a Raspberry Pi Zero 2W and hit persistent OBD
connection drops under real driving. The root cause was isolated methodically:
the adapter/cable/hub were verified working on a laptop; the app connected
cleanly in isolated single-threaded tests; but kernel logs on the Zero 2W showed
continuous USB errors (`ftdi_sio ttyUSB0: failed to get modem status: -71`) — a
hardware-layer failure of the Zero 2W's single shared USB controller.

**Resolution:** migrating to a Raspberry Pi 4 (dedicated USB controllers)
eliminated the USB errors entirely — the same code and 30-read polling test that
failed on the Zero 2W ran flawlessly with a clean kernel log. A second,
software-level cause was also fixed: python-OBD is not thread-safe, so
background and main-thread reads were serialized behind a lock. Together these
produced a stable live connection.

## Honest limitations

- **Boost gauge → MAF airflow:** The test vehicles don't expose manifold/intake
  pressure (PID 0x0B), so true boost (PSI) can't be derived with a standard
  adapter. The gauge instead shows MAF (mass air flow, g/s) — a supported,
  measured value that's the best available indicator of turbo activity. Real
  measured data, not a fabricated number.
- **Virtual dyno is an estimate**, not a calibrated dyno reading.
- **The local SLM is small (0.6B).** It's fast and fully offline, but it will
  occasionally be wrong or oversimplify — the system prompt reduces this and
  bans catastrophizing, but doesn't eliminate it. That's why the rule-based
  WarningAdvisor, not the SLM, is the authoritative safety layer.
- **ML anomaly detection is an advisory.** Even regime-aware, it flags some
  legitimate readings as unusual (~5.7%). It complements, and never overrides,
  the rule-based warnings.

## Setup

1. Copy `config.example.json` to `config.json` and set your values (OBD port,
   optional Ollama IP, etc.).
2. Install system packages on the Pi: `sudo apt install python3-pygame
   python3-full libportaudio2 cmake git`
3. Create a venv with system packages and install requirements:
   `python3 -m venv venv --system-site-packages && source venv/bin/activate && pip install -r requirements.txt`
4. Download the Vosk speech model into the project directory.
5. **Local SLM:** build `llama.cpp` (`cmake -B build -DGGML_NATIVE=ON && cmake
   --build build --config Release -j4`), download a Qwen3-0.6B Q4_K_M GGUF into
   `~/models/`, and install the provided `llama-server` systemd service (pinned
   with `taskset -c 1,2,3`, `-t 3`) so it auto-starts on boot.
6. Run on the Pi console (boot-to-console + autologin) so KMSDRM can own the
   display. Auto-launch on login via `~/.bash_profile` (tty1 only).
7. To (re)train the ML model on a laptop: `pip install -r requirements-train.txt`,
   then `python autopi/train_model.py <driving_log.csv>`.

## License

Copyright (c) 2026 JohnMichael Betancourt. All Rights Reserved.
This project is viewable for portfolio and evaluation purposes only.
No use, copying, or distribution is permitted without written permission.
See the LICENSE file.