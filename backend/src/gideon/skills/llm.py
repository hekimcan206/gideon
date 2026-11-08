"""Optional LLM fallback behind a provider interface. Off by default.

The deterministic skills answer in microseconds and never send audio or text anywhere. An LLM is only consulted for
utterances no skill matched, only when explicitly enabled, and the default provider is a local Ollama server, so
nothing leaves the machine unless the user points it elsewhere.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Protocol, runtime_checkable

from gideon.skills.base import IntentMatch, SkillContext, SkillResponse, say


@runtime_checkable
class LlmProvider(Protocol):
    name: str

    async def complete(self, prompt: str, language: str) -> str: ...


class OllamaProvider:
    name = "ollama"

    def __init__(
        self, base_url: str = "http://127.0.0.1:11434", model: str = "llama3.2:3b", timeout_s: float = 8.0
    ):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Ollama URL must be http(s)")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    async def complete(self, prompt: str, language: str) -> str:
        system = (
            "You are GIDEON, a desktop voice assistant. Answer in one or two short spoken sentences, "
            + ("in Turkish." if language == "tr" else "in English.")
        )
        body = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": {"num_predict": 80},
            }
        ).encode()
        request = urllib.request.Request(  # noqa: S310 - scheme validated in __init__
            f"{self.base_url}/api/generate", data=body, headers={"Content-Type": "application/json"}
        )

        def call() -> str:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:  # noqa: S310
                return str(json.loads(resp.read())["response"]).strip()

        return await asyncio.to_thread(call)


class LlmFallbackSkill:
    name = "llm"
    intents = ()

    def __init__(self, provider: LlmProvider, max_prompt_chars: int = 400) -> None:
        self.provider = provider
        self.max_prompt_chars = max_prompt_chars

    async def handle(self, match: IntentMatch, ctx: SkillContext) -> SkillResponse:
        prompt = match.slots.get("text", match.utterance.raw)[: self.max_prompt_chars]
        try:
            answer = await self.provider.complete(prompt, match.language)
        except Exception:
            return say(
                match.language, en="My language model is not reachable.", tr="Dil modeline ulaşamıyorum."
            )
        return SkillResponse(text=answer[:600] or "...", language=match.language)
