import json
import threading
import time

import requests
from autopi.discovery import OllamaFinder


class AIClient:
    """The AI 'brain' for AutoPi, with a local-first tiered fallback so the
    device always has an answer - even off-grid in the car.

    TIER 1 - LOCAL SLM (llama-server on this Pi, OpenAI /v1 API): tried FIRST,
             always available, needs no network. A small quantized model
             (Qwen3-0.6B) kept on a tight leash by the system prompt below.
    TIER 2 - HOME OLLAMA (the bigger model on the home box, Ollama /api API):
             only used if the local server is down. Smarter, but only reachable
             on home WiFi.
    TIER 3 - the caller's own offline handling (e.g. OfflineExplainer) if both
             fail; ask() returns an 'AI unavailable' string it can fall back on.

    The model is weak, so RELIABILITY COMES FROM THE HARNESS, not the model:
    a fixed car-diagnostic system prompt, live data supplied by the caller in
    the prompt, thinking disabled, a short token cap, and a short timeout. This
    is what pushes a 0.6B toward useful, honest answers."""

    # The leash. Every local call gets this system prompt. It fixes the role,
    # forbids catastrophizing, demands brevity, and asks for concrete actions.
    SYSTEM_PROMPT = (
        "You are AutoPi, an in-car diagnostic assistant. You are given real "
        "readings from THIS car. Explain plainly what they mean for the driver "
        "right now, then give 2-3 concrete things they can do about it. "
        "Rules: Be concise - 3 sentences max plus a short action list. Use plain "
        "words, no jargon. Explain what the reading INDICATES; do NOT assert a "
        "part is broken or needs replacement unless the data clearly proves it - "
        "prefer 'this can be caused by...' over 'your X is bad'. If the readings "
        "look normal, say so plainly and reassure. Never invent causes unrelated "
        "to the data (for example, fuel level does not affect battery voltage)."
    )

    # Guardrails: the system prompt is now built from autopi/prompts
    # (explainer.md + the shared security.md), and every answer passes the
    # Judge's code checks before any screen shows it. The string above is
    # only a fallback if the prompt files are missing.
    _guard = None

    @classmethod
    def _guards(cls):
        if cls._guard is None:
            from autopi.guardrails import PromptLibrary, InjectionScanner, SecurityLog
            from autopi.judge import Judge
            scanner = InjectionScanner()
            try:
                lib = PromptLibrary("autopi/prompts")
                system = lib.system("explainer")
                # The on-Pi 0.6B model reads prompts at only a few dozen
                # tokens/s, so it gets the SAME rules in compact form.
                system_local = lib.system("explainer", security="security_short")
            except Exception:
                system = cls.SYSTEM_PROMPT
                system_local = cls.SYSTEM_PROMPT
            cls._guard = {"system": system, "system_local": system_local,
                          "scanner": scanner,
                          "judge": Judge(scanner, llm_enabled=False, max_chars=900),
                          "log": SecurityLog("logs/security.jsonl")}
        return cls._guard

    def _screen(self, prompt, text):
        # Code-only review of a free-text answer. Numbers aren't forced to be
        # grounded here (explanations quote normal ranges), but action claims,
        # injected instructions and runaway length are blocked, and a model's
        # own "SECURITY:" report is logged for the driver.
        g = self._guards()
        if text.strip().upper().startswith("SECURITY:"):
            g["log"].report("explainer", "model reported suspicious input",
                            text.strip()[:200])
        verdict = g["judge"].check_code(text, prompt + " " + text, "")
        hard = [r for r in verdict.reasons if not r.startswith("number ")]
        if hard:
            g["log"].report("explainer", "answer blocked", "; ".join(hard))
            return "AI unavailable: answer blocked by AutoPi safety check"
        return text

    def __init__(self, config, model="llama3.2",
                 local_url="http://127.0.0.1:8080", local_model="qwen3-0.6b"):
        # config: a Config object (we ask it for the ollama_ip)
        self.__config = config
        self.__ip = config.ollama_ip          # home Ollama (tier 2)
        self.__model = model                  # home Ollama model name
        self.__finder = OllamaFinder()
        self.__local_url = local_url.rstrip("/")   # local llama-server (tier 1)
        self.__local_model = local_model
        # Timing + retry settings: config.json "ai" (defaults below).
        try:
            with open("config.json") as f:
                acfg = json.load(f).get("ai", {})
        except Exception:
            acfg = {}
        # A local answer is short; the Pi does ~7 tok/s, so ~90 tokens is
        # ~13 s plus reading the prompt. 25 s was too tight once the security
        # rules were added - answers were cut off right before they finished.
        self.__local_timeout = acfg.get("local_timeout_s", 45)
        self.__local_max_tokens = acfg.get("local_max_tokens", 90)
        self.__home_timeout = acfg.get("home_timeout_s", 30)
        # Connect timeout: an unreachable laptop fails in 2 s, not 30 s.
        self.__connect_timeout = acfg.get("connect_timeout_s", 2)
        # After the home box fails (e.g. you're driving), skip it for a while
        # instead of waiting on it - and re-scanning the network - every ask.
        self.__home_skip_s = acfg.get("home_skip_s", 120)
        self.__home_down_until = 0.0
        self.__last_scan = 0.0
        self.__scan_every_s = acfg.get("rescan_every_s", 300)
        # Same question within cache_ttl_s = instant answer, no model call.
        self.__cache_ttl = acfg.get("cache_ttl_s", 600)
        self.__cache = {}
        # The Pi model answers ONE question at a time. Serialize calls so
        # screens queue in order instead of all timing out together, and let
        # a duplicate question wait for the first one's answer.
        self.__model_lock = threading.Lock()
        self.__inflight = {}
        self.__inflight_lock = threading.Lock()
        self.retry_attempts = acfg.get("retry_attempts", 3)
        self.retry_wait_s = acfg.get("retry_wait_s", 4)
        self.retry_slow_s = acfg.get("retry_slow_s", 20)

    # ---- TIER 1: local llama-server (OpenAI /v1/chat/completions) ----------
    def __ask_local(self, prompt):
        # "/no_think" disables Qwen3's reasoning monologue (huge speedup + no
        # rambling). System prompt leashes the model; max_tokens keeps it short.
        url = self.__local_url + "/v1/chat/completions"
        data = {
            "model": self.__local_model,
            "messages": [
                {"role": "system", "content": self._guards()["system_local"]},
                {"role": "user", "content": prompt + " /no_think"},
            ],
            # Reuse the already-processed system prompt between calls
            # (llama-server prompt cache) instead of re-reading it every time.
            "cache_prompt": True,
            "max_tokens": self.__local_max_tokens,
            "temperature": 0.3,     # low - we want steady, factual answers
            "stream": False,
        }
        response = requests.post(url, json=data,
                                 timeout=(self.__connect_timeout, self.__local_timeout))
        result = response.json()
        text = result["choices"][0]["message"]["content"]
        # Qwen3 sometimes still emits empty <think></think> tags even with
        # /no_think; strip any stray thinking block so the driver never sees it.
        if "<think>" in text:
            text = text.split("</think>")[-1]
        return text.strip()

    # ---- TIER 2: home Ollama (/api/generate) ------------------------------
    def __url(self):
        return "http://" + self.__ip + ":11434/api/generate"

    def __rediscover(self):
        found = self.__finder.find()
        if found is not None:
            self.__ip = found
            print("Auto-discovered Ollama at", found)
            return True
        return False

    def __ask_home(self, prompt):
        now = time.monotonic()
        if now < self.__home_down_until:
            raise RuntimeError("home AI not reachable (skipping for now)")
        data = {"model": self.__model,
                "prompt": self._guards()["system"] + "\n\n" + prompt,
                "stream": False}
        timeout = (self.__connect_timeout, self.__home_timeout)
        try:
            response = requests.post(self.__url(), json=data, timeout=timeout)
            return response.json()["response"]
        except Exception:
            # Re-scan the subnet at most every few minutes, not every ask.
            if now - self.__last_scan >= self.__scan_every_s:
                self.__last_scan = now
                if self.__rediscover():
                    try:
                        response = requests.post(self.__url(), json=data, timeout=timeout)
                        return response.json()["response"]
                    except Exception:
                        pass
            self.__home_down_until = time.monotonic() + self.__home_skip_s
            raise

    # ---- Public API (unchanged signature - callers don't change) ----------
    def ask(self, prompt):
        # Send any prompt to the AI, return the text response. LOCAL FIRST so
        # the device answers instantly, offline, without waiting on the network.
        # Falls back to home Ollama only if the local server is down.
        # Cached answers return instantly; identical questions share one call.
        now = time.monotonic()
        hit = self.__cache.get(prompt)
        if hit is not None and now - hit[1] <= self.__cache_ttl:
            return hit[0]
        with self.__inflight_lock:
            waiter = self.__inflight.get(prompt)
            if waiter is None:
                waiter = {"event": threading.Event(), "answer": None}
                self.__inflight[prompt] = waiter
                owner = True
            else:
                owner = False
        if not owner:
            waiter["event"].wait(self.__local_timeout + self.__home_timeout + 10)
            return waiter["answer"] or "AI unavailable: timed out waiting"
        try:
            with self.__model_lock:
                answer = self.__ask_tiers(prompt)
            if not answer.startswith("AI unavailable"):
                self.__cache[prompt] = (answer, time.monotonic())
            waiter["answer"] = answer
            return answer
        finally:
            waiter["event"].set()
            with self.__inflight_lock:
                self.__inflight.pop(prompt, None)

    def __ask_tiers(self, prompt):
        try:
            return self._screen(prompt, self.__ask_local(prompt))
        except Exception:
            pass
        try:
            return self._screen(prompt, self.__ask_home(prompt))
        except Exception as e:
            return "AI unavailable: " + str(e)[:120]

    def ask_with_retry(self, prompt, on_status=None, still_wanted=None):
        """ask(), retried automatically so the driver never has to leave the
        screen and come back. on_status(text) shows progress on the screen.
        still_wanted() -> False stops retrying once you leave that screen;
        while you stay, it keeps trying (fast at first, then every
        retry_slow_s) until the AI answers."""
        quick = max(1, int(self.retry_attempts))
        attempt = 0
        answer = "AI unavailable"
        while True:
            attempt += 1
            if still_wanted is not None and not still_wanted():
                return answer
            if on_status is not None:
                on_status("AI thinking..." if attempt == 1 else
                          "AI retrying (try " + str(attempt) + ")...")
            answer = self.ask(prompt)
            if not answer.startswith("AI unavailable"):
                return answer
            if still_wanted is None and attempt >= quick:
                return answer
            wait = self.retry_wait_s if attempt < quick else self.retry_slow_s
            for left in range(int(wait), 0, -1):
                if still_wanted is not None and not still_wanted():
                    return answer
                if on_status is not None:
                    on_status("AI busy or unreachable - trying again in "
                              + str(left) + " s")
                time.sleep(1)

    def explain_code(self, code):
        prompt = ("Explain car trouble code " + code + " briefly and simply, "
                  "and what the driver should do about it.")
        return self.ask(prompt)

    def summarize(self, facts):
        prompt = ("Write a short, plain-English vehicle health summary. "
                  "Facts: " + facts + " Keep it under 80 words.")
        return self.ask(prompt)

    def is_reachable(self):
        # Fast check used at boot. TRUE if EITHER brain answers. Checks the
        # local server first (it's the primary now), then the home box.
        try:
            r = requests.get(self.__local_url + "/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        try:
            r = requests.get("http://" + self.__ip + ":11434/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def home_reachable(self):
        """(True/False, ip) - is the laptop Ollama answering right now?"""
        try:
            r = requests.get("http://" + self.__ip + ":11434/api/tags",
                             timeout=self.__connect_timeout)
            return r.status_code == 200, self.__ip
        except Exception:
            return False, self.__ip

    def local_reachable(self):
        # Specifically: is the on-Pi SLM up? (For boot status / diagnostics.)
        try:
            r = requests.get(self.__local_url + "/health", timeout=2)
            return r.status_code == 200
        except Exception:
            return False