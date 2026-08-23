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

- **Hardware limitation — Raspberry Pi Zero 2W USB (known blocker):** This
  project currently runs on a Raspberry Pi Zero 2W, whose single shared USB
  controller cannot reliably sustain the USB-serial (FTDI) connection to the
  OBD-II adapter under real driving conditions. The connection establishes,
  then drops with continuous kernel-level USB errors
  (`ftdi_sio ttyUSB0: failed to get modem status: -71`), which no software
  change can fix — the failure is at the USB hardware layer, below the
  application. This was isolated methodically: the adapter, cable, and powered
  hub were all verified fully working when connected to a laptop (they connect
  and stream data reliably), and the application code connects cleanly in
  isolated tests when no other process holds the port. The bottleneck is
  specifically the Zero 2W's USB hardware. **Recommended fix: run this on a
  Raspberry Pi 4**, which has dedicated USB controllers — the application code
  runs unchanged. An ESP32-based CAN reader is an alternative but requires
  separate firmware and may not work on vehicles using CAN-FD.

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
  varied driving data — anomaly detection is not yet live. (Data collection is
  also gated by the connection stability issue above.)

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

Copyright (c) 2026 JohnMichael Betancourt. All Rights Reserved.
This project is viewable for portfolio and evaluation purposes only.
No use, copying, or distribution is permitted without written permission.
See the LICENSE file.