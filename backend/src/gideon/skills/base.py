"""Skill contracts and the bilingual (Turkish/English) text normalisation the intent matcher relies on."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

LANGUAGES = ("en", "tr")

# Turkish diacritics folded to ASCII, one character to one character so match spans stay valid on the unfolded
# text. Whisper is inconsistent about diacritics on short clips ("saat kac" vs "saat kaç"); patterns are written
# against the folded form so both match.
_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")
_PUNCT = re.compile(r"[^\w\s']+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def lower(text: str, language: str) -> str:
    """Language-aware lowercase: Turkish maps I -> ı and İ -> i (Python's default maps I -> i)."""
    text = text.replace("I", "ı").replace("İ", "i") if language == "tr" else text.replace("İ", "i")
    return text.lower()


def normalize(text: str, language: str) -> str:
    """Lowercase, punctuation to spaces, collapse whitespace. Apostrophes survive ("what's", "Ankara'da")."""
    return _SPACES.sub(" ", _PUNCT.sub(" ", lower(text, language))).strip()


def fold(text: str) -> str:
    return text.translate(_FOLD)


@dataclass(frozen=True, slots=True)
class Utterance:
    """A command in one candidate language: ``display`` keeps diacritics, ``folded`` is what patterns see."""

    raw: str
    language: str
    display: str
    folded: str

    @classmethod
    def of(cls, raw: str, language: str) -> Utterance:
        display = normalize(raw, language)
        return cls(raw=raw, language=language, display=display, folded=fold(display))


@dataclass(frozen=True)
class IntentSpec:
    name: str
    patterns: Mapping[str, Sequence[str]]
    # Natural example phrasings per language. They document the intent and prime the STT decoder toward the
    # command vocabulary (see SkillRouter.vocabulary and FasterWhisperStt).
    examples: Mapping[str, Sequence[str]] = field(default_factory=dict)
    compiled: dict[str, list[re.Pattern[str]]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        compiled = {lang: [re.compile(p) for p in pats] for lang, pats in self.patterns.items()}
        object.__setattr__(self, "compiled", compiled)


@dataclass(frozen=True, slots=True)
class IntentMatch:
    skill: str
    intent: str
    language: str
    slots: Mapping[str, str]
    utterance: Utterance


@dataclass(frozen=True, slots=True)
class SkillResponse:
    text: str
    language: str


Notify = Callable[[str, str], Awaitable[None]]


async def _no_notify(_text: str, _language: str) -> None:
    return None


@dataclass
class SkillContext:
    """What a skill may touch. Wall-clock time is injected so skills are testable at a fixed instant."""

    now: Callable[[], datetime] = datetime.now
    monotonic: Callable[[], float] = time.monotonic
    notify: Notify = _no_notify


@runtime_checkable
class Skill(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def intents(self) -> Sequence[IntentSpec]: ...

    async def handle(self, match: IntentMatch, ctx: SkillContext) -> SkillResponse: ...


def say(language: str, *, en: str, tr: str) -> SkillResponse:
    return SkillResponse(text=tr if language == "tr" else en, language=language)


_EN_NUMBERS = {
    "zero": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90,
}  # fmt: skip
_TR_NUMBERS = {
    "sifir": 0, "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5, "alti": 6, "yedi": 7, "sekiz": 8,
    "dokuz": 9, "on": 10, "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50, "altmis": 60, "doksan": 90,
}  # fmt: skip


def parse_number(text: str, language: str) -> int | None:
    """Digits or number words up to the tens ("twenty five", "yirmi beş", "45"); None if not a number."""
    words = fold(text.strip().replace("-", " ")).split()
    if not words:
        return None
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])
    table = _TR_NUMBERS if language == "tr" else _EN_NUMBERS
    total = 0
    for word in words:
        if word not in table:
            return None
        total += table[word]
    return total
