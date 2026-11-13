"""The judge: checks every answer before the driver sees it.

Two layers, cheapest and strongest first:

  1. CODE checks (always on, can't be talked out of anything):
     - every number and trouble code in the answer must appear in the evidence
     - no claims that something was done to the car
     - no prompt-injection text or safety-bypass advice
     - length cap
  2. LLM judge (optional, separate prompt, sees only DATA): catches things
     code can't, like an answer that dodges the question or overstates a
     diagnosis. It can only VETO. It never approves an action - actions are
     gated by tools.ToolRegistry.authorize() and the driver.

The LLM judge is a second model that can also be fooled, so it never replaces
layer 1. On the Pi it's off by default (it doubles the wait); it runs when a
bigger tier (the laptop) is reachable.
"""
import json
import re

from autopi.guardrails import fence, parse_json_reply

NUMBER_RX = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")   # "85-105" = 85 and 105
CODE_RX = re.compile(r"\b[PCBU][0-9A-F]{4}\b")
ACTION_RX = re.compile(
    r"\b(i|i've|i have|we|we've|autopi)\s+(just\s+)?(cleared|reset|erased|bled|"
    r"flashed|adjusted|disabled|turned off|deleted|reprogrammed|fixed)\b"
    r"|\b(codes?|light|fault)s?\s+(has|have)\s+been\s+(cleared|reset|erased)\b",
    re.IGNORECASE)


class Verdict:
    def __init__(self, ok, reasons, security_flag=None, layer="code"):
        self.ok = ok
        self.reasons = reasons
        self.security_flag = security_flag
        self.layer = layer

    def __repr__(self):
        return ("Verdict(ok=" + str(self.ok) + ", layer=" + self.layer
                + ", reasons=" + str(self.reasons) + ")")


class Judge:
    SMALL_INTS_OK = set(range(0, 9))     # cylinder numbers, counts, "2-3 things"

    def __init__(self, scanner, prompts=None, llm=None, max_chars=600,
                 llm_tiers=None, llm_enabled=True):
        self._scanner = scanner
        self._prompts = prompts
        self._llm = llm
        self._max_chars = max_chars
        self._llm_tiers = llm_tiers          # e.g. ["laptop"]; None = any
        self._llm_enabled = llm_enabled

    # ---------- layer 1: code ----------
    @staticmethod
    def _numbers(text):
        out = []
        for m in NUMBER_RX.findall(text or ""):
            try:
                out.append(float(m))
            except ValueError:
                pass
        return out

    def _grounded(self, value, pool):
        if value.is_integer() and int(value) in self.SMALL_INTS_OK:
            return True
        for p in pool:
            if abs(p - value) <= max(0.051, abs(p) * 0.01):   # rounding slack
                return True
        return False

    def check_code(self, answer, evidence_text, question=""):
        reasons = []
        if not answer or not answer.strip():
            return Verdict(False, ["empty answer"])
        if len(answer) > self._max_chars:
            reasons.append("answer longer than " + str(self._max_chars) + " chars")
        pool = self._numbers(evidence_text) + self._numbers(question)
        for n in self._numbers(CODE_RX.sub(" ", answer)):
            if not self._grounded(n, pool):
                reasons.append("number " + str(n) + " is not in the evidence")
        known_codes = set(CODE_RX.findall((evidence_text or "") + " " + (question or "")))
        for c in CODE_RX.findall(answer):
            if c not in known_codes:
                reasons.append("code " + c + " is not in the evidence")
        if ACTION_RX.search(answer):
            reasons.append("claims an action was performed on the car")
        hits = self._scanner.scan(answer)
        flag = None
        if hits:
            reasons.append("answer contains instruction-like text")
            flag = "answer contained: " + ", ".join(hits)
        return Verdict(len(reasons) == 0, reasons, flag, "code")

    # ---------- layer 2: LLM ----------
    def check_llm(self, question, evidence_text, answer):
        if not self._llm_enabled or self._llm is None or self._prompts is None:
            return None
        messages = [
            {"role": "system", "content": self._prompts.system("judge")},
            {"role": "user", "content":
                fence("QUESTION", question) + "\n\n"
                + fence("EVIDENCE", evidence_text) + "\n\n"
                + fence("ANSWER", answer)},
        ]
        try:
            text, tier = self._llm.chat(messages, max_tokens=160,
                                        temperature=0.0, tiers=self._llm_tiers)
        except Exception:
            return None                       # judge unavailable: layer 1 stands
        obj, err = parse_json_reply(text)
        if obj is None or obj.get("verdict") not in ("pass", "fail"):
            return None                       # unreadable judge: layer 1 stands
        reasons = obj.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        return Verdict(obj["verdict"] == "pass", [str(r)[:120] for r in reasons],
                       obj.get("security_flag"), "llm:" + tier)

    def review(self, question, evidence_text, answer):
        v = self.check_code(answer, evidence_text, question)
        if not v.ok:
            return v
        lv = self.check_llm(question, evidence_text, answer)
        return lv if lv is not None else v
