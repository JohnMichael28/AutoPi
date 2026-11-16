"""One client for every model tier (CS 5390 Week 2 slm_probe pattern).

llama-server, Ollama and most local runtimes speak the same OpenAI-compatible
route: POST <base_url>/v1/chat/completions. So the laptop (Ollama), the phone
and the on-Pi llama-server all share ONE code path, using only the standard
library (no requests dependency on the hot path).

Tiers come from config.json "copilot.tiers", tried in order. A tier that
failed is skipped for `retry_after_s` so a dead laptop doesn't add a timeout
to every question. If SLM_API_KEY is set it's sent as a bearer token and never
printed (Week 2 rule)."""
import json
import os
import time
import urllib.request
import urllib.error

from autopi.model_gate import LOCAL_MODEL_LOCK


class LLMUnavailable(Exception):
    pass


class LLMClient:
    def __init__(self, tiers, retry_after_s=60.0):
        # tiers: [{"name", "base_url", "model", "timeout_s"}, ...]
        self._tiers = list(tiers)
        self._retry_after = retry_after_s
        self._down_until = {}        # tier name -> time it may be retried
        self.last_tier = None

    def _endpoint(self, tier):
        return tier["base_url"].rstrip("/") + "/v1/chat/completions"

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("SLM_API_KEY", "")
        if key:
            headers["Authorization"] = "Bearer " + key
        return headers

    def _post(self, tier, messages, max_tokens, temperature):
        # Qwen3 "thinks" out loud before answering unless told /no_think -
        # hundreds of hidden tokens on a Pi = timeouts. And cache_prompt lets
        # llama-server reuse the already-read system prompt between calls.
        if tier.get("no_think", True) and messages and messages[-1]["role"] == "user":
            messages = messages[:-1] + [{"role": "user",
                                         "content": messages[-1]["content"] + " /no_think"}]
        payload = {"model": tier["model"], "messages": messages,
                   "max_tokens": max_tokens, "temperature": temperature,
                   "stream": False,
                   "cache_prompt": tier.get("cache_prompt", True)}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._endpoint(tier), data=data,
                                     headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=tier.get("timeout_s", 30)) as r:
            body = json.loads(r.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"] or ""
        if "</think>" in text:               # Qwen3 thinking leftovers
            text = text.split("</think>")[-1]
        return text.strip()

    def chat(self, messages, max_tokens=256, temperature=0.0, tiers=None):
        """Try each tier in order; return (text, tier_name). Raises
        LLMUnavailable if every tier fails. `tiers` optionally limits which
        tier names may be used (e.g. the judge only on the laptop)."""
        now = time.time()
        errors = []
        for tier in self._tiers:
            name = tier["name"]
            if tiers is not None and name not in tiers:
                continue
            if self._down_until.get(name, 0) > now:
                continue
            # Wait our turn for the Pi model (a screen may be using it).
            if not LOCAL_MODEL_LOCK.acquire(timeout=tier.get("timeout_s", 30)):
                errors.append(name + ": busy (AutoPi AI is answering another screen)")
                continue
            try:
                text = self._post(tier, messages, max_tokens, temperature)
                self.last_tier = name
                return text, name
            except (urllib.error.URLError, OSError, ValueError, KeyError,
                    IndexError) as err:
                self._down_until[name] = time.time() + self._retry_after
                errors.append(name + ": " + str(err)[:80])
            finally:
                LOCAL_MODEL_LOCK.release()
        raise LLMUnavailable("; ".join(errors) or "no tier available")

    def tier_names(self):
        return [t["name"] for t in self._tiers]
