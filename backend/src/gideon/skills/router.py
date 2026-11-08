"""Intent router: transcript -> (skill, intent, slots) -> response.

Matching is ordered regular expressions per language, not a model: it is deterministic, testable with golden
cases, explainable in a log line, and costs microseconds on the critical path. The transcribed language is tried
first, then the other supported language, because Whisper often mislabels two-word clips; the language whose
pattern matched decides the answer language.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from gideon.skills.base import (
    LANGUAGES,
    IntentMatch,
    Skill,
    SkillContext,
    SkillResponse,
    Utterance,
    say,
)

log = logging.getLogger(__name__)

# Whisper often keeps the tail of the wake phrase ("Jarvis, what time is it?").
_WAKE_PREFIX = re.compile(
    r"^(?:(?:hey|hi|ok|okay|hay|hej)\s+)?(?:jarvis|gideon|cervis|carvis)\s*", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class RouteResult:
    skill: str
    intent: str
    response: SkillResponse
    match: IntentMatch | None


class SkillRouter:
    def __init__(self, skills: Sequence[Skill], fallback: Skill | None = None) -> None:
        names = [s.name for s in skills]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate skill names: {names}")
        self.skills = list(skills)
        self.fallback = fallback

    @property
    def skill_names(self) -> list[str]:
        names = [s.name for s in self.skills]
        if self.fallback is not None:
            names.append(self.fallback.name)
        return names

    def vocabulary(self, language: str, max_chars: int = 400) -> str:
        """Example phrases of every installed intent in one language, used as the STT decoder prompt.

        Whisper reads at most ~224 prompt tokens; the cap keeps the prompt well inside that.
        """
        phrases = [p for s in self.skills for i in s.intents for p in i.examples.get(language, ())]
        out = ""
        for phrase in phrases:
            if len(out) + len(phrase) + 1 > max_chars:
                break
            out = f"{out} {phrase}".strip()
        return out

    def match(self, text: str, language: str) -> IntentMatch | None:
        stripped = _WAKE_PREFIX.sub("", text.strip())
        order = [language] + [lang for lang in LANGUAGES if lang != language]
        for lang in order:
            if lang not in LANGUAGES:
                continue
            utterance = Utterance.of(stripped, lang)
            if not utterance.folded:
                continue
            for skill in self.skills:
                for intent in skill.intents:
                    for pattern in intent.compiled.get(lang, ()):
                        m = pattern.search(utterance.folded)
                        if m is None:
                            continue
                        slots = {
                            key: utterance.display[m.start(key) : m.end(key)].strip()
                            for key, value in m.groupdict().items()
                            if value is not None
                        }
                        return IntentMatch(skill.name, intent.name, lang, slots, utterance)
        return None

    async def route(self, text: str, language: str, ctx: SkillContext) -> RouteResult:
        lang = language if language in LANGUAGES else "en"
        match = self.match(text, lang)
        if match is not None:
            skill = next(s for s in self.skills if s.name == match.skill)
            try:
                response = await skill.handle(match, ctx)
            except Exception:
                log.exception("skill failed", extra={"fields": {"skill": skill.name, "intent": match.intent}})
                response = say(
                    match.language, en="Sorry, that skill failed.", tr="Üzgünüm, bu beceri hata verdi."
                )
            return RouteResult(match.skill, match.intent, response, match)
        if self.fallback is not None:
            utterance = Utterance.of(text, lang)
            fallback_match = IntentMatch(self.fallback.name, "fallback", lang, {"text": text}, utterance)
            response = await self.fallback.handle(fallback_match, ctx)
            return RouteResult(self.fallback.name, "fallback", response, fallback_match)
        return RouteResult(
            "none",
            "unknown",
            say(lang, en="Sorry, I didn't catch that.", tr="Üzgünüm, anlayamadım."),
            None,
        )
