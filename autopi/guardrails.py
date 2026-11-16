"""Guardrails for every model AutoPi talks to.

The big idea (CS 5390 Topics 5B/6A/6B): the model only PROPOSES. Code decides.
Prompts ask the model to behave, and these classes make sure it doesn't
matter if it doesn't:

  PromptLibrary   - builds every system prompt as ROLE + SECURITY + contract,
                    from the .md files in autopi/prompts (one source, DRY).
  fence()         - wraps untrusted text in <<DATA ...>> blocks so the model
                    can tell data from instructions, and neutralises any fake
                    fence markers an attacker hides inside the data.
  InjectionScanner- flags text that tries to instruct the model (prompt
                    injection) BEFORE it reaches the model.
  SecurityLog     - append-only incident log the driver can read on the
                    WARNINGS screen ("report back to me").
  parse_json_reply- strict parser for the one-JSON-object reply contract.
"""
import json
import os
import re
import time

FENCE_OPEN = "<<DATA "
FENCE_CLOSE = "<<END DATA>>"


class PromptLibrary:
    """Loads prompt files once. Every system prompt = role file + the shared
    SECURITY block, so no model can ever be started without the rules."""

    def __init__(self, prompts_dir):
        self._dir = prompts_dir
        self._cache = {}

    def _read(self, name):
        if name not in self._cache:
            path = os.path.join(self._dir, name + ".md")
            with open(path, encoding="utf-8") as f:
                self._cache[name] = f.read().strip()
        return self._cache[name]

    def system(self, role, security="security", **values):
        # security="security_short" gives tiny on-Pi models the same rules in
        # ~90 words (the full block makes a 0.6B model slow to read prompts).
        text = self._read(role)
        for key in values:
            text = text.replace("{" + key + "}", str(values[key]))
        return text + "\n\n" + self._read(security)


def tidy_answer(text):
    """The small on-device model sometimes parrots its own prompt: "<<DATA"
    fence markers, or a "SECURITY: I cannot claim..." sentence copied from
    the rules. Strip those so the driver only reads the explanation."""
    for marker in ("<<END DATA>>", "<<DATA", "<<", ">>"):
        text = text.replace(marker, "")
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    keep = [p for p in parts if "SECURITY" not in p.upper()
            and "AS AN AI" not in p.upper()]
    return " ".join(keep).strip() or text.strip()


def fence(label, text, max_chars=4000):
    """Wrap untrusted text as DATA. Any fence markers inside the text are
    defanged so data can't 'close' its own block and start giving orders.
    Long text is cut so one tool result can't flood the context window."""
    body = str(text)
    body = body.replace("<<", "< <").replace(">>", "> >")
    if len(body) > max_chars:
        body = body[:max_chars] + " ...[truncated]"
    return FENCE_OPEN + label + ">>\n" + body + "\n" + FENCE_CLOSE


class InjectionScanner:
    """Pattern check for text that tries to instruct a model. Cheap, runs on
    every tool result and every model output. It can't catch everything -
    that's why code (not the model) enforces permissions - but it catches the
    common attacks and gives us something concrete to report."""

    PATTERNS = [
        r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
        r"disregard (all |any |the )?(previous|prior|above|your) ",
        r"forget (all |your |the )?(previous |prior )?(instructions|rules)",
        r"you are now ",
        r"new (system )?instructions?:",
        r"(reveal|print|show|repeat) (your |the )?(system )?(prompt|instructions|rules)",
        r"\bsystem prompt\b",
        r"(act|pretend|roleplay) as ",
        r"developer mode|jailbreak|do anything now",
        r"<\|?(im_start|im_end|system|assistant)\|?>",
        r"^\s*(system|assistant)\s*:",
        r"\"tool\"\s*:\s*\"",
        r"(call|run|use|invoke) the [a-z_]+ tool",
        r"(clear|erase|delete) (all )?(the )?(trouble )?codes",
        r"(disable|bypass|defeat|delete) (the )?(abs|airbag|immobili[sz]er|emissions|egr|dpf|catalytic|cat|traction)",
        r"api[_ ]?key|password|bearer ",
    ]

    def __init__(self):
        self._compiled = [re.compile(p, re.IGNORECASE | re.MULTILINE)
                          for p in self.PATTERNS]

    def scan(self, text):
        """Returns a list of matched snippets (empty list = looks clean)."""
        hits = []
        if not text:
            return hits
        for rx in self._compiled:
            m = rx.search(str(text))
            if m:
                hits.append(m.group(0)[:60])
        return hits


class SecurityLog:
    """Append-only JSON-lines incident log. Each write is flushed and fsynced
    so a power cut while cranking can't lose the record (Topic 6B)."""

    def __init__(self, path, keep_in_memory=50):
        self._path = path
        self._recent = []
        self._keep = keep_in_memory
        folder = os.path.dirname(path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)

    def report(self, source, kind, detail):
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "source": source, "kind": kind, "detail": str(detail)[:300]}
        self._recent.append(entry)
        self._recent = self._recent[-self._keep:]
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            pass        # logging must never take the UI down
        print("SECURITY [" + source + "] " + kind + ": " + entry["detail"])
        return entry

    def recent(self):
        return list(self._recent)


def parse_json_reply(text):
    """Strict-but-forgiving parser for the one-JSON-object contract. Strips a
    stray <think> block or ``` fence (small models add them), then requires
    exactly one JSON object. Returns (dict, None) or (None, error string)."""
    if text is None:
        return None, "empty reply"
    s = str(text)
    if "</think>" in s:
        s = s.split("</think>")[-1]
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return None, "no JSON object in reply"
    try:
        obj = json.loads(s[start:end + 1])
    except ValueError as err:
        return None, "invalid JSON: " + str(err)
    if not isinstance(obj, dict):
        return None, "reply must be a JSON object"
    return obj, None
