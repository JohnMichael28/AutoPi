"""Builds the whole guarded AI stack from config.json in one place (DRY), so
every caller gets the same prompts, security rules, tools, judge and log.

    stack = AIStack.from_config(snapshot_fn, vehicle=vehicle, dyno_fn=...)
    result = stack.copilot.ask("why is my check engine light on?")
    stack.security_log.recent()   # -> shown on the WARNINGS screen
"""
import json

from autopi.guardrails import PromptLibrary, InjectionScanner, SecurityLog
from autopi.llm_client import LLMClient
from autopi.judge import Judge
from autopi.tools import build_default_registry
from autopi.copilot import Copilot
from autopi.offline_ai import OfflineExplainer

DEFAULTS = {
    "prompts_dir": "autopi/prompts",
    "security_log": "logs/security.jsonl",
    # LOCAL ONLY: the model runs on the Pi itself (llama-server). No laptop,
    # no network - AutoPi must work fully anywhere the car goes.
    "tiers": [
        {"name": "pi", "base_url": "http://127.0.0.1:8080",
         "model": "qwen3-0.6b", "timeout_s": 45},
    ],
    "retry_after_s": 5,
    "profiles": {"driving": {"allow": ["read"]},
                 "service": {"allow": ["read", "actuate"]}},
    "max_turns": 5,
    "max_tool_calls": 3,
    "deadline_s": 75,
    "max_answer_chars": 600,
    "route_first": True,
    # The code judge always runs. A second model as judge would double the
    # wait on the Pi, so the LLM judge is off (it was laptop-only).
    "judge": {"llm_enabled": False, "llm_tiers": ["pi"]},
}


class AIStack:
    def __init__(self, cfg, snapshot_fn, vehicle=None, dyno_fn=None):
        self.cfg = cfg
        self.prompts = PromptLibrary(cfg["prompts_dir"])
        self.scanner = InjectionScanner()
        self.security_log = SecurityLog(cfg["security_log"])
        self.llm = LLMClient(cfg["tiers"], retry_after_s=cfg["retry_after_s"])
        offline = OfflineExplainer()

        def dtc_fn():
            if vehicle is None:
                return []
            return vehicle.get_dtcs()

        self.registry = build_default_registry(
            cfg["profiles"], snapshot_fn, dtc_fn=dtc_fn,
            explain_fn=offline.explain, dyno_fn=dyno_fn)
        jcfg = cfg.get("judge", {})
        self.judge = Judge(self.scanner, prompts=self.prompts, llm=self.llm,
                           max_chars=cfg["max_answer_chars"],
                           llm_tiers=jcfg.get("llm_tiers"),
                           llm_enabled=jcfg.get("llm_enabled", True))
        self.copilot = Copilot(self.llm, self.registry, self.prompts, self.judge,
                               self.scanner, self.security_log,
                               max_turns=cfg["max_turns"],
                               max_tool_calls=cfg["max_tool_calls"],
                               deadline_s=cfg["deadline_s"],
                               route_first=cfg.get("route_first", True))

    @classmethod
    def from_config(cls, snapshot_fn, vehicle=None, dyno_fn=None,
                    path="config.json"):
        cfg = dict(DEFAULTS)
        try:
            with open(path) as f:
                user = json.load(f).get("copilot", {})
            cfg.update(user)
        except Exception:
            pass
        return cls(cfg, snapshot_fn, vehicle=vehicle, dyno_fn=dyno_fn)
