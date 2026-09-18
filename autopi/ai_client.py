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

    def __init__(self, config, model="llama3.2",
                 local_url="http://127.0.0.1:8080", local_model="qwen3-0.6b"):
        # config: a Config object (we ask it for the ollama_ip)
        self.__config = config
        self.__ip = config.ollama_ip          # home Ollama (tier 2)
        self.__model = model                  # home Ollama model name
        self.__finder = OllamaFinder()
        self.__local_url = local_url.rstrip("/")   # local llama-server (tier 1)
        self.__local_model = local_model
        # A local answer is short; the Pi does ~7 tok/s, so ~80 tokens is ~11s
        # worst case. Keep the timeout generous enough for that but not forever.
        self.__local_timeout = 25
        self.__local_max_tokens = 90

    # ---- TIER 1: local llama-server (OpenAI /v1/chat/completions) ----------
    def __ask_local(self, prompt):
        # "/no_think" disables Qwen3's reasoning monologue (huge speedup + no
        # rambling). System prompt leashes the model; max_tokens keeps it short.
        url = self.__local_url + "/v1/chat/completions"
        data = {
            "model": self.__local_model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt + " /no_think"},
            ],
            "max_tokens": self.__local_max_tokens,
            "temperature": 0.3,     # low - we want steady, factual answers
            "stream": False,
        }
        response = requests.post(url, json=data, timeout=self.__local_timeout)
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
        data = {"model": self.__model,
                "prompt": self.SYSTEM_PROMPT + "\n\n" + prompt,
                "stream": False}
        try:
            response = requests.post(self.__url(), json=data, timeout=30)
            return response.json()["response"]
        except Exception:
            if self.__rediscover():
                response = requests.post(self.__url(), json=data, timeout=30)
                return response.json()["response"]
            raise

    # ---- Public API (unchanged signature - callers don't change) ----------
    def ask(self, prompt):
        # Send any prompt to the AI, return the text response. LOCAL FIRST so
        # the device answers instantly, offline, without waiting on the network.
        # Falls back to home Ollama only if the local server is down.
        try:
            return self.__ask_local(prompt)
        except Exception:
            pass
        try:
            return self.__ask_home(prompt)
        except Exception as e:
            return "AI unavailable: " + str(e)

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

    def local_reachable(self):
        # Specifically: is the on-Pi SLM up? (For boot status / diagnostics.)
        try:
            r = requests.get(self.__local_url + "/health", timeout=2)
            return r.status_code == 200
        except Exception:
            return False