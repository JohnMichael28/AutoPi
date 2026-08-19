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
- **Live zoned gauges** — RPM, boost, air/fuel, and load as green/yellow/red
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
- **ML anomaly detection** (in learning phase — see below).

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

- Raspberry Pi Zero 2W
- 5" Elecrow HDMI capacitive touchscreen (800×480)
- OBDLink EX USB OBD-II adapter
- USB audio adapter + mic (voice)
- Powered USB hub, 12V→5V buck converter, fuse tap

## Architecture

- **Model / View / Adapter** separation. The car is accessed only through a
  `Vehicle` class; the UI reads cached snapshots and never blocks on OBD I/O.
- **Background threading** — OBD reads and the ML logger run off the 60fps
  render loop. Blocking work never touches the UI thread.
- **Tiered polling** — fast-changing PIDs read every cycle, slow ones rotated
  in, so no single read cycle overloads the connection.
- **Tiered AI fallback** — local Ollama when reachable, graceful offline
  behavior otherwise. The device never pretends to have data it doesn't.

## Honest limitations

- **Highway USB stability:** under sustained high-speed driving (electrical
  noise, heat, vibration), the USB OBD adapter can drop its connection. The
  software detects this, shows `--`, and attempts to recover (including
  re-scanning for a re-enumerated adapter), but on this hardware a physical
  drop can't always be prevented. A more robust hub / secured wiring — or an
  ESP32 dedicated as the OBD reader feeding the Pi — is the planned fix.
- **Virtual dyno is an estimate**, not a calibrated dyno reading.
- **The ML model is in its learning phase** — collecting data now; anomaly
  detection goes live after training on enough real driving.
- **Some PIDs aren't supported on every car** and honestly show `--`.

## Setup

1. Copy `config.example.json` to `config.json` and set your values (OBD port,
   Ollama IP if using the AI, etc.).
2. Install dependencies: `pip install -r requirements.txt`
   On the Pi, install pygame via apt instead of pip:
   `sudo apt install python3-pygame`
   (the pip version bundles a broken SDL on this hardware).
3. To train the ML model (laptop only): `pip install -r requirements-train.txt`
4. Run `python3 main_ui.py`, or install the systemd service to boot on startup.

## License

## License

Copyright (c) 2026 JohnMichael Betancourt. All Rights Reserved.
This project is viewable for portfolio and evaluation purposes only.
No use, copying, or distribution is permitted without written permission.
See the LICENSE file.