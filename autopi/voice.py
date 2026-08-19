"""Offline push-to-talk voice input. Records a fixed buffer from the USB mic
and transcribes locally with Vosk - no network needed (Rule 7: offline-safe).
Records+transcribes on a background thread (Rule 5) so the UI never blocks.
Device, model, rate, and duration come from config.json (Rule 2). No
f-strings (Rule 1).

Uses a fixed-buffer record (sounddevice.rec -> wait -> process) rather than a
streaming queue: the streaming approach raced the audio callback and dropped
words. Recording a clean buffer first, then feeding Vosk, is accurate."""
import json
import threading

import sounddevice
import vosk


class VoiceInput:
    def __init__(self, model_path, device_name=None, sample_rate=44100,
                 record_seconds=5):
        self._sample_rate = sample_rate
        self._record_seconds = record_seconds
        self._model = vosk.Model(model_path)
        self._device = self._find_device(device_name)
        self._listening = False
        self._result_text = None
        self._status = "idle"     # idle | listening | thinking | done | error

    @staticmethod
    def _find_device(device_name):
        # Find the input device index by name substring (robust to the USB
        # card number shifting between boots). None = system default.
        if not device_name:
            return None
        try:
            for index, dev in enumerate(sounddevice.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    if device_name.lower() in dev.get("name", "").lower():
                        return index
        except Exception:
            pass
        return None

    def start_listening(self):
        # Kick off record+transcribe on a background thread. Non-blocking.
        if self._listening:
            return
        self._listening = True
        self._result_text = None
        self._status = "listening"
        thread = threading.Thread(target=self._record_and_transcribe,
                                  daemon=True)
        thread.start()

    def _record_and_transcribe(self):
        # OFF the UI thread. Clean fixed-buffer record, then feed Vosk once.
        try:
            frames = int(self._record_seconds * self._sample_rate)
            audio = sounddevice.rec(frames, samplerate=self._sample_rate,
                                    channels=1, dtype="int16",
                                    device=self._device)
            sounddevice.wait()
            self._status = "thinking"
            rec = vosk.KaldiRecognizer(self._model, self._sample_rate)
            rec.AcceptWaveform(audio.tobytes())
            final = json.loads(rec.FinalResult())
            self._result_text = final.get("text", "").strip()
            self._status = "done"
        except Exception as err:
            self._result_text = ""
            self._status = "error: " + str(err)
        finally:
            self._listening = False

    def poll_result(self):
        # UI calls each frame. Returns text once ready, else None. O(1).
        if self._status == "done" and self._result_text is not None:
            text = self._result_text
            self._result_text = None
            self._status = "idle"
            return text
        return None

    def status(self):
        return self._status