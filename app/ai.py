"""AI helpers over any OpenAI-compatible chat API (OpenRouter, Ollama, llama.cpp…).

Providers are tried in AI_ORDER. A provider that fails or rate-limits is skipped for a
few minutes. If none work, callers fall back to the rule-based parser.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from .config import Settings
from .tasks import AREAS

log = logging.getLogger("ticklight.ai")

OPENROUTER = "https://openrouter.ai/api/v1"
COOLDOWN = 600          # seconds to skip a failing model
MODEL_LIST_TTL = 6 * 3600
MAX_TASKS = 25


class AIUnavailable(Exception):
    pass


@dataclass
class Target:
    label: str
    url: str
    model: str
    key: str = ""


TRIAGE_PROMPT = """You turn a brain dump from someone with ADHD into clean tasks.

Today is {today} ({weekday}), time {time}.

Rules:
- One task per distinct thing to do. Merge duplicates. Ignore filler and feelings,
  but keep anything actionable hidden in them.
- "title": short, starts with a verb, under 70 characters, no dates in it.
- "when": the date/time/repeat in this exact style, or "" if none was said:
  "today", "tomorrow 3pm", "friday", "next monday 9:30", "dec 5", "in 2 weeks",
  "every day", "every weekday at 9", "every tue and fri at 7pm", "monthly on the 1st",
  "every last sunday", "every year on march 3", "30 days after done".
  Never invent a date the person didn't imply.
- "priority": "high" only if they said it's urgent/important, "low" if they said it doesn't matter, else "normal".
- "area": one of {areas}.
- "energy": "low" (mindless), "medium", or "high" (hard focus, phone calls, anything dreaded).
- "minutes": honest estimate including setup. People with ADHD underestimate; round up.
- "steps": [] if the task is one obvious action. Otherwise 2-5 concrete physical steps;
  the first must take under 2 minutes and need no decisions (e.g. "Open the bank app").
- "someday": true for vague wishes and ideas ("learn piano one day"), else false.
- "shared": true only if that item was tagged #home, #shared or #household; else false.
  Never put the tag in the title.

Reply with JSON only, no prose:
{{"tasks": [{{"title": "", "when": "", "priority": "normal", "area": "home", "energy": "low", "minutes": 10, "steps": [], "someday": false, "shared": false}}]}}"""

BREAKDOWN_PROMPT = """Break a task into tiny steps for someone with ADHD who is stuck.

Rules:
- 3 to 6 steps, each a concrete physical action starting with a verb, under 60 characters.
- Step 1 must take under 2 minutes and need zero decisions (open, find, put, write one line).
- No vague steps like "plan" or "research"; say exactly what to do.
- Don't repeat steps that are already done.

Reply with JSON only: {"steps": ["", ""]}"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)  # reasoning models
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    return json.loads(m.group())


def _str(v, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def clean_triage(data: dict) -> list[dict]:
    out = []
    for t in (data.get("tasks") or [])[:MAX_TASKS]:
        if not isinstance(t, dict):
            continue
        title = _str(t.get("title"), 120)
        if not title:
            continue
        area = str(t.get("area") or "").lower()
        energy = str(t.get("energy") or "").lower()
        try:
            minutes = max(1, min(600, int(float(t.get("minutes") or 0)))) or None
        except (TypeError, ValueError):
            minutes = None
        out.append({
            "title": title,
            "when": _str(t.get("when"), 80),
            "priority": {"high": 2, "low": 0}.get(str(t.get("priority")).lower(), 1),
            "area": area if area in AREAS else None,
            "energy": {"low": 1, "medium": 2, "med": 2, "high": 3}.get(energy),
            "minutes": minutes,
            "steps": [_str(s, 100) for s in (t.get("steps") or [])[:6] if _str(s, 100)],
            "someday": bool(t.get("someday")),
            "shared": t.get("shared") is True,
        })
    return out


class AI:
    def __init__(self, s: Settings):
        self.s = s
        self._cooldown: dict[str, float] = {}
        self._free_models: list[str] = []
        self._free_fetched = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.s.openrouter_key or (self.s.local_ai_url and self.s.local_ai_model))

    async def _free_list(self, client: httpx.AsyncClient) -> list[str]:
        if self.s.openrouter_free_models:
            return self.s.openrouter_free_models
        if self._free_models and time.time() - self._free_fetched < MODEL_LIST_TTL:
            return self._free_models
        try:
            r = await client.get(f"{OPENROUTER}/models", timeout=15)
            r.raise_for_status()
            models = [m for m in r.json().get("data", [])
                      if m.get("id", "").endswith(":free")
                      and "text" in (m.get("architecture", {}).get("output_modalities") or ["text"])]
            # Bigger context usually means a bigger, newer model
            models.sort(key=lambda m: -(m.get("context_length") or 0))
            self._free_models = [m["id"] for m in models[:4]]
            self._free_fetched = time.time()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("couldn't list OpenRouter free models: %s", e)
        return self._free_models

    async def _targets(self, client: httpx.AsyncClient) -> list[Target]:
        out = []
        for kind in self.s.ai_order:
            if kind == "free" and self.s.openrouter_key:
                out += [Target(f"openrouter:{m}", OPENROUTER, m, self.s.openrouter_key)
                        for m in await self._free_list(client)]
            elif kind == "local" and self.s.local_ai_url and self.s.local_ai_model:
                out.append(Target(f"local:{self.s.local_ai_model}", self.s.local_ai_url,
                                  self.s.local_ai_model))
            elif kind == "paid" and self.s.openrouter_key and self.s.openrouter_paid_model:
                out.append(Target(f"openrouter:{self.s.openrouter_paid_model}", OPENROUTER,
                                  self.s.openrouter_paid_model, self.s.openrouter_key))
        now = time.time()
        return [t for t in out if self._cooldown.get(t.label, 0) < now]

    async def chat_json(self, system: str, user: str, timeout: float = 45) -> tuple[dict, str]:
        """Send one prompt, return (parsed JSON, provider label)."""
        if not self.configured:
            raise AIUnavailable("no AI provider configured")
        async with httpx.AsyncClient() as client:
            targets = await self._targets(client)
            for t in targets:
                headers = {"Authorization": f"Bearer {t.key}"} if t.key else {}
                if t.url == OPENROUTER:
                    headers["X-Title"] = "Ticklight"
                try:
                    r = await client.post(f"{t.url}/chat/completions", headers=headers, timeout=timeout,
                                          json={"model": t.model, "temperature": 0.2,
                                                "messages": [{"role": "system", "content": system},
                                                             {"role": "user", "content": user}]})
                    r.raise_for_status()
                    content = r.json()["choices"][0]["message"]["content"]
                    return _extract_json(content), t.label
                except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
                    log.warning("AI %s failed: %s", t.label, e)
                    self._cooldown[t.label] = time.time() + COOLDOWN
        raise AIUnavailable("all AI providers failed")

    async def triage(self, text: str, now: datetime) -> tuple[list[dict], str]:
        system = TRIAGE_PROMPT.format(today=now.date().isoformat(), weekday=now.strftime("%A"),
                                      time=now.strftime("%H:%M"), areas=", ".join(AREAS))
        data, label = await self.chat_json(system, text[:6000])
        tasks = clean_triage(data)
        if not tasks:
            raise AIUnavailable("AI returned no tasks")
        return tasks, label

    async def breakdown(self, title: str, notes: str = "", done: list[str] | None = None) -> list[str]:
        user = f"Task: {title}"
        if notes:
            user += f"\nNotes: {notes[:1000]}"
        if done:
            user += "\nAlready done: " + "; ".join(done)
        data, _ = await self.chat_json(BREAKDOWN_PROMPT, user)
        steps = [_str(s, 100) for s in (data.get("steps") or [])[:8] if _str(s, 100)]
        if not steps:
            raise AIUnavailable("AI returned no steps")
        return steps
