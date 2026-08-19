import requests
from autopi.discovery import OllamaFinder


class AIClient:
    """Encapsulates the local Ollama AI. The URL and model are private;
    callers just ask questions. Reads the IP from a Config object
    (dependency injection) so the IP lives in one place."""

    def __init__(self, config, model="llama3.2"):
        # config: a Config object (we ask it for the ollama_ip)
        self.__config = config
        self.__ip = config.ollama_ip
        self.__model = model
        self.__finder = OllamaFinder()
    
    def __url(self):
        return "http://" + self.__ip + ":11434/api/generate"

    def __rediscover(self):
        # Config IP failed - hunt for Ollama on the network
        found = self.__finder.find()
        if found is not None:
            self.__ip = found
            print("Auto-discovered Ollama at", found)
            return True
        return False
    
    def ask(self, prompt):
        # Send any prompt to the AI, return the text response.
        # Try/except so an unreachable brain never crashes the app.
        data = {
            "model": self.__model,
            "prompt": prompt,
            "stream": False,
        }
        try:
            response = requests.post(self.__url(), json=data, timeout=30)
            result = response.json()
            return result["response"]
        except Exception:
            # First failure - try to rediscover the IP, then retry once
            if self.__rediscover():
                try:
                    response = requests.post(self.__url(), json=data, timeout=30)
                    return response.json()["response"]
                except Exception as e:
                    return "AI unavailable: " + str(e)
            return "AI unavailable: could not find Ollama on the network"

    def explain_code(self, code):
        # Convenience method: explain a trouble code in plain English
        prompt = "Explain car trouble code " + code + " briefly and simply."
        return self.ask(prompt)

    def summarize(self, facts):
        # Convenience method: write a health summary from a facts string
        prompt = ("Write a short, plain-English vehicle health summary for a "
                  "mechanic. Facts: " + facts + " Keep it under 100 words.")
        return self.ask(prompt)
    
    def is_reachable(self):
        # Fast check: can we actually reach Ollama right now? Short timeout so
        # it never hangs boot. Returns True only if the AI genuinely responds.
        try:
            r = requests.get("http://" + self.__ip + ":11434/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False