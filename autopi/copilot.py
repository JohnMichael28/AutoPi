"""AutoPi Copilot: a bounded, guarded ReAct agent (CS 5390 Weeks 6-8).

    READY -> DECIDE -> TOOL -> DECIDE -> ... -> REVIEW -> DONE
                +-> STOPPED (budget, bad output, judge veto, offline)

- DECIDE: the model proposes ONE JSON action: a tool request or an answer.
- TOOL:   the registry validates + authorizes + runs it. The result goes back
          to the model inside a <<DATA>> fence, after an injection scan.
- REVIEW: the Judge checks the answer against the tool evidence. One rewrite
          is allowed; after that, AutoPi shows the raw evidence instead.

Budgets (Topic 6B) are enforced by code, in this order: deadline, model
turns, tool calls. The model never decides when to stop.

If no model tier answers, the copilot falls back to keyword routing (Topic
7B): code picks the tools, runs them, and shows the evidence. The driver
always gets real data, never an invented answer.
"""
import json
import time

from autopi.guardrails import fence, parse_json_reply
from autopi.llm_client import LLMUnavailable
from autopi.tools import ToolError


class CopilotResult:
    def __init__(self, status, answer, evidence, trace, incidents, tier):
        self.status = status          # "done" | "stopped" | "offline"
        self.answer = answer
        self.evidence = evidence      # list of "tool: result" strings
        self.trace = trace            # list of (seconds, state, detail)
        self.incidents = incidents    # security incidents raised this run
        self.tier = tier

    def lines(self):
        out = [self.answer]
        if self.status != "done" and self.evidence:
            out.append("")
            out.append("Evidence:")
            for e in self.evidence:
                out.append("  " + e[:110])
        if self.incidents:
            out.append("")
            out.append("SECURITY: " + str(len(self.incidents))
                       + " blocked attempt(s) - see WARNINGS.")
        return out


class Copilot:
    # Keyword routing for the no-model fallback (and a sanity baseline).
    ROUTES = [
        (("code", "check engine", "cel", "light", "trouble"), "get_trouble_codes", {}),
        (("dyno", "horsepower", "hp", "power", "pull"), "last_dyno_pull", {}),
        (("temp", "hot", "overheat", "coolant"), "get_live_data",
         {"keys": ["coolant_temp", "intake_temp"]}),
        (("battery", "volt", "charging", "alternator"), "get_live_data",
         {"keys": ["voltage"]}),
        (("fuel", "trim", "lean", "rich", "mileage", "mpg"), "get_live_data",
         {"keys": ["stft_b1", "ltft_b1", "o2_b1s2", "fuel"]}),
    ]
    DEFAULT_ROUTE = ("get_live_data", {"keys": ["rpm", "speed", "coolant_temp",
                                                 "engine_load"]})

    def __init__(self, llm, registry, prompts, judge, scanner, security_log,
                 max_turns=5, max_tool_calls=3, deadline_s=45.0,
                 max_rewrites=1, tool_result_chars=1500, route_first=True):
        self._llm = llm
        self._tools = registry
        self._prompts = prompts
        self._judge = judge
        self._scan = scanner
        self._log = security_log
        self._max_turns = max_turns
        self._max_tool_calls = max_tool_calls
        self._deadline_s = deadline_s
        self._max_rewrites = max_rewrites
        self._tool_chars = tool_result_chars
        # route_first: code runs the obvious tool for the question BEFORE the
        # first model turn, so the small on-Pi model usually answers in ONE
        # turn instead of spending a slow turn just asking for data.
        self._route_first = route_first

    # ------------------------------------------------------------------
    def ask(self, question, profile="driving"):
        t0 = time.time()
        trace = []
        incidents = []
        evidence = []
        tier = None

        def step(state, detail):
            trace.append((round(time.time() - t0, 2), state, str(detail)[:160]))

        def incident(source, kind, detail):
            incidents.append(self._log.report(source, kind, detail))

        step("READY", question)
        hits = self._scan.scan(question)
        if hits:
            # The driver is trusted, but a radio or passenger isn't. Log it;
            # the model still can't do anything the profile doesn't allow.
            incident("driver_input", "instruction-like text in question", hits)

        catalog = self._tools.catalog(profile)
        system = self._prompts.system("copilot", max_tool_calls=self._max_tool_calls)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content":
                "DRIVER: " + question.replace("<<", "< <") + "\n\n"
                + fence("TOOLS", json.dumps(catalog))},
        ]

        turns = 0
        tool_calls = 0
        rewrites = 0
        if self._route_first:
            name, args = self._route(question)
            try:
                result = self._tools.run(name, args, profile)
                rhits = self._scan.scan(result)
                if rhits:
                    incident("tool:" + name, "instruction-like text in tool result", rhits)
                else:
                    tool_calls += 1
                    evidence.append(name + ": " + result[:400])
                    step("TOOL", "pre-routed " + name + " -> " + result)
                    messages.append({"role": "user", "content":
                        fence("TOOL RESULT " + name, result, self._tool_chars)
                        + "\nAutoPi: this data was fetched for you. Answer now "
                        "if it is enough; call another tool only if needed."})
            except Exception as err:
                step("TOOL", "pre-route " + name + " failed: " + str(err)[:60])
        while True:
            # ---- budgets, checked by code, in order ----
            if time.time() - t0 >= self._deadline_s:
                step("STOPPED", "deadline")
                return self._fallback("deadline", question, evidence, trace,
                                      incidents, tier, profile)
            if turns >= self._max_turns:
                step("STOPPED", "max_turns")
                return self._fallback("max_turns", question, evidence, trace,
                                      incidents, tier, profile)

            # ---- DECIDE ----
            try:
                text, tier = self._llm.chat(messages, max_tokens=256, temperature=0.0)
            except LLMUnavailable as err:
                step("OFFLINE", err)
                return self._fallback("offline", question, evidence, trace,
                                      incidents, tier, profile)
            turns += 1
            step("DECIDE", text)
            obj, err = parse_json_reply(text)
            if obj is None:
                messages.append({"role": "assistant", "content": text[:500]})
                messages.append({"role": "user", "content":
                    "AutoPi: your reply was rejected (" + err + "). Reply with "
                    "exactly one JSON object as described."})
                continue
            messages.append({"role": "assistant", "content": json.dumps(obj)})

            flag = obj.get("security_flag")
            if flag:
                incident("model", "model reported suspicious input", flag)

            # ---- TOOL ----
            if "tool" in obj:
                name = obj.get("tool")
                args = obj.get("arguments") or {}
                if tool_calls >= self._max_tool_calls:
                    messages.append({"role": "user", "content":
                        "AutoPi: tool budget used up. Answer now with the "
                        "evidence you have."})
                    step("TOOL", "refused: budget")
                    continue
                tool_calls += 1
                try:
                    result = self._tools.run(str(name), args, profile)
                except ToolError as terr:
                    step("TOOL", str(name) + " error: " + str(terr))
                    messages.append({"role": "user", "content":
                        fence("TOOL ERROR " + str(name), str(terr))})
                    continue
                except Exception as terr:
                    step("TOOL", str(name) + " crashed: " + str(terr))
                    messages.append({"role": "user", "content":
                        fence("TOOL ERROR " + str(name), "tool failed")})
                    continue
                rhits = self._scan.scan(result)
                if rhits:
                    incident("tool:" + str(name), "instruction-like text in tool result", rhits)
                    result = json.dumps({"quarantined": True, "reason":
                        "AutoPi removed this result because it contained "
                        "instruction-like text."})
                else:
                    evidence.append(str(name) + ": " + result[:400])
                step("TOOL", str(name) + " -> " + result)
                messages.append({"role": "user", "content":
                    fence("TOOL RESULT " + str(name), result, self._tool_chars)})
                continue

            # ---- REVIEW ----
            if "answer" in obj:
                answer = str(obj.get("answer", "")).strip()
                ev_text = "\n".join(evidence)
                verdict = self._judge.review(question, ev_text, answer)
                step("REVIEW", verdict)
                if verdict.security_flag:
                    incident("judge", "judge flagged answer", verdict.security_flag)
                if verdict.ok:
                    step("DONE", answer)
                    return CopilotResult("done", answer, evidence, trace,
                                         incidents, tier)
                if rewrites < self._max_rewrites:
                    rewrites += 1
                    messages.append({"role": "user", "content":
                        "AutoPi REVIEW rejected your answer: "
                        + "; ".join(verdict.reasons)
                        + ". Rewrite it using only facts from the TOOL RESULT "
                        "blocks, or call a tool first."})
                    continue
                step("STOPPED", "judge veto")
                return self._fallback("unverified", question, evidence, trace,
                                      incidents, tier, profile)

            messages.append({"role": "user", "content":
                "AutoPi: reply must contain either \"tool\" or \"answer\"."})

    # ------------------------------------------------------------------
    def _route(self, question):
        q = question.lower()
        for words, tool, args in self.ROUTES:
            for w in words:
                if w in q:
                    return tool, args
        return self.DEFAULT_ROUTE

    def _fallback(self, reason, question, evidence, trace, incidents, tier, profile):
        """No verified model answer: show REAL data, chosen by code."""
        if not evidence:
            tool, args = self._route(question)
            try:
                result = self._tools.run(tool, args, profile)
                evidence.append(tool + ": " + result[:400])
            except Exception as err:
                evidence.append(tool + ": unavailable (" + str(err)[:60] + ")")
        headline = {
            "offline": "AI is offline. Here is the live data for your question:",
            "deadline": "AI ran out of time. Here is the data it gathered:",
            "max_turns": "AI couldn't settle on an answer. Here is the data:",
            "unverified": "AI's answer didn't match the data, so it was blocked. Here is the data:",
        }.get(reason, "Here is the data:")
        status = "offline" if reason == "offline" else "stopped"
        return CopilotResult(status, headline, evidence, trace, incidents, tier)
