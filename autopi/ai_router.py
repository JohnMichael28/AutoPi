from autopi.offline_ai import OfflineExplainer


class AIRouter:
    """The tiered-inference router (the AI/ML architecture showpiece).
    Routes a code-explanation request down the ladder:
      TIER 1: the on-Pi model (AIClient -> local llama-server), no network
      TIER 2: offline dictionary + queue - always works, even if the model
              service is down

    This is 'graceful degradation': the device ALWAYS gives an answer,
    using the best tier currently available."""

    def __init__(self, ai_client, offline=None):
        self._ai = ai_client          # Tier 1 (on-Pi model)
        self._offline = offline or OfflineExplainer()

    def explain_code(self, code):
        # Try Tier 1 (the good AI). If it fails, fall back to Tier 3.
        result = self._ai.explain_code(code)
        # AIClient returns an "AI unavailable..." string on failure
        if result and not result.startswith("AI unavailable"):
            return {"text": result, "tier": "AI (full)", "queued": False}

        # Tier 1 failed - use offline dictionary + queue for later
        offline_text = self._offline.explain(code)
        self._offline.queue_for_ai(code)
        return {"text": offline_text, "tier": "offline", "queued": True}

    def process_queue(self):
        # When reconnected, explain everything that was queued offline.
        results = []
        for code in self._offline.get_queue():
            r = self._ai.explain_code(code)
            if r and not r.startswith("AI unavailable"):
                results.append((code, r))
        if results:
            self._offline.clear_queue()
        return results