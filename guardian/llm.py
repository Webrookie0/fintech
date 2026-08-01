"""Provider-agnostic LLM client.

One interface, many free tiers. Swap the provider in config or by setting one
env key — no code changes. `mock` needs no network at all (used for offline
demos and CI); the judge layer falls back to a deterministic heuristic then.
"""

from __future__ import annotations

import json
import os

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
}


class LLMClient:
    def __init__(self, provider: str = "auto", model: str = ""):
        self.provider = self._resolve(provider)
        self.model = model or DEFAULT_MODELS.get(self.provider, "")

    def _resolve(self, provider: str) -> str:
        if provider != "auto":
            return provider if provider in ("gemini", "openai", "groq", "mock") else "mock"
        if os.getenv("GEMINI_API_KEY"):
            return "gemini"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        if os.getenv("GROQ_API_KEY"):
            return "groq"
        return "mock"

    @property
    def available(self) -> bool:
        return self.provider != "mock"

    def describe(self) -> str:
        return f"{self.provider}/{self.model}"

    def complete(self, prompt: str, *, json_mode: bool = True) -> str:
        if self.provider == "mock":
            return ""
        if self.provider == "gemini":
            return self._gemini(prompt)
        return self._openai_compatible(prompt, json_mode=json_mode)

    def _gemini(self, prompt: str) -> str:
        from google import genai

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        resp = client.models.generate_content(model=self.model, contents=prompt)
        return (resp.text or "").strip()

    def _openai_compatible(self, prompt: str, *, json_mode: bool) -> str:
        from openai import OpenAI

        if self.provider == "groq":
            client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
        else:
            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            **kwargs,
        )
        return (resp.choices[0].message.content or "").strip()


def parse_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        start, end = text.index("{"), text.rindex("}")
        return json.loads(text[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        return None
