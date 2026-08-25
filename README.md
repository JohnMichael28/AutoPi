# AutoPi — Intelligent In-Car Diagnostic Guardian

> A personal project built to explore embedded systems, real-time vehicle data,
> and applied ML — from OBD-II protocol handling to on-device anomaly detection.

A Raspberry Pi-powered, touchscreen car diagnostic device that reads live OBD-II
data, explains it in plain English, and is learning to detect problems before
they happen. Built around a Fallout-style terminal "guardian" that reacts to your
car's health in real time.

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
  clear codes, each with an AI plain-English explanation.
- **Fuel system analysis** — live short/long-term fuel trims + O2 voltage with a
  health verdict and AI diagnosis of the actual numbers.
- **Virtual dyno** — estimates horsepower from an acceleration pull (clearly
  labeled ESTIMATED — it's an OBD acceleration method, not a real dyno).
- **Tuning monitor** — live knock/timing watch with debounced alarms.
- **Offline voice assistant** — push-to-talk questions answered by a local AI,
  using your car's live data. Fully offline speech recognition (Vosk).
- **ML anomaly detection** (in data-collection phase — see below).

## The ML system

The long-term goal is a model that learns *your specific car's* normal behavior
and flags anomalies (fuel system drifting, knock developing, O2 sensor aging,
coolant creeping, abnormal load) before they become failures.

**Approach:** unsupervised anomaly detection with an Isolation Forest, chosen
after reviewing the literature on vehicle/sensor anomaly detection. Isolation
Forest is lightweight (runs on a Pi), needs only normal data (no labeled
failures), and handles the multivariate correlations that single-threshold
alarms miss. Real-engine-data research (e.g. the EngineAD dataset) found simple
classical methods competitive with or superior to deep learning for this task.

**Three-phase pipeline:**
1. **Collect** (current phase) — the device silently logs clean, engine-running
   snapshots to CSV during normal driving, building a "normal" dataset.
2. **Train** — offline on a laptop (`train_model.py`): scale features, fit the
   Isolation Forest, compute per-feature baselines for interpretability, save
   the model.
3. **Deploy** — the trained model runs on the Pi for real-time inference, and
   the guardian reacts when readings deviate from learned normal — naming *which*
   sensor is off, not just "something's wrong."

This mirrors how real ML systems work: heavy training offline, light inference
on-device.

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
  render loop. Blocking work never touches the UI thread. All OBD access is
  serialized through a lock, since python-OBD is not thread-safe.
- **Tiered polling** — fast-changing PIDs read every cycle, slow ones rotated
  in, so no single read cycle overloads the connection.
- **Tiered AI fallback** — local Ollama when reachable, graceful offline
  behavior otherwise. The device never pretends to have data it doesn't.
- **Console/KMSDRM display** — runs as a full-screen pygame app on the Pi
  console (boot-to-console + autologin), drawing directly via KMSDRM with no
  desktop environment.

## Engineering notes: the connection-stability investigation

Early development ran on a Raspberry Pi Zero 2W and hit persistent OBD
connection drops under real driving. The root cause was isolated methodically:

- The adapter, cable, and hub were verified fully working on a laptop (connected
  and streamed data reliably).
- The application code connected cleanly in isolated single-threaded tests.
- Kernel logs on the Zero 2W showed continuous USB errors
  (`ftdi_sio ttyUSB0: failed to get modem status: -71`) — a hardware-layer
  failure of the Zero 2W's single shared USB controller, below the application.

**Resolution:** migrating to a Raspberry Pi 4 (which has dedicated USB
controllers) eliminated the USB errors entirely — the same code, adapter, and
30-read polling test that failed on the Zero 2W ran flawlessly with a clean
kernel log. A second, software-level cause was also fixed: python-OBD is not
thread-safe, so simultaneous access from the background snapshot thread and
main-thread diagnostic reads was serialized behind a lock. Together these
produced a stable live connection.

## Honest limitations

- **Boost gauge → MAF airflow:** The test vehicles do not expose manifold/intake
  pressure (OBD-II PID 0x0B) over the standard protocol. Since boost is derived
  as manifold pressure minus barometric pressure, true boost (PSI) cannot be
  calculated on these cars with a standard adapter. The gauge instead displays
  MAF (mass air flow, g/s) — a supported, measured value that is the best
  available indicator of turbo activity, since airflow rises sharply when the
  turbo spools. Real measured data, not a fabricated or estimated number.

- **Virtual dyno is an estimate**, not a calibrated dyno reading.

- **ML anomaly detection is in its data-collection phase.** The device logs
  clean engine-running snapshots during driving to build a "normal" dataset.
  The Isolation Forest model activates only after training on enough real,
  varied driving data — anomaly detection is not yet live.

## Setup

1. Copy `config.example.json` to `config.json` and set your values (OBD port,
   Ollama IP if using the AI, etc.).
2. Install system packages on the Pi (pygame via apt, not pip — the pip build
   bundles a broken SDL): `sudo apt install python3-pygame python3-full libportaudio2`
3. Create a venv with system packages and install requirements:
   `python3 -m venv venv --system-site-packages && source venv/bin/activate && pip install -r requirements.txt`
4. Download the Vosk speech model into the project directory.
5. Run on the Pi console (boot-to-console + autologin) so KMSDRM can own the
   display. Auto-launch on login via `~/.bash_profile` (tty1 only).
6. To train the ML model (laptop only): `pip install -r requirements-train.txt`

## License

Copyright (c) 2026 JohnMichael Betancourt. All Rights Reserved.
This project is viewable for portfolio and evaluation purposes only.
No use, copying, or distribution is permitted without written permission.
See the LICENSE file.