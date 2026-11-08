from __future__ import annotations

import asyncio
import platform
from pathlib import Path

import pytest

from gideon.skills.base import SkillContext, Utterance, lower, normalize, parse_number
from gideon.skills.builtin import (
    AllowedApp,
    ClockSkill,
    HelpSkill,
    NotesSkill,
    NoteStore,
    OpenAppSkill,
    SystemInfoSkill,
    TimerSkill,
    load_allow_list,
)
from gideon.skills.llm import LlmFallbackSkill
from gideon.skills.router import SkillRouter
from tests.constants import FIXED_NOW


@pytest.fixture
def full_router(tmp_path: Path) -> SkillRouter:
    return SkillRouter(
        [ClockSkill(), TimerSkill(), NotesSkill(NoteStore(tmp_path / "n.jsonl")), SystemInfoSkill(), HelpSkill(),
         OpenAppSkill(dry_run=True)]
    )  # fmt: skip


# Golden cases: phrasings as faster-whisper actually returns them (casing, punctuation, wake-word residue,
# missing diacritics), and language labels that are wrong for short clips.
GOLDEN = [
    ("What time is it?", "en", "clock", "time.now", {}),
    ("Jarvis, what time is it?", "en", "clock", "time.now", {}),
    ("Hey Jarvis what's the time", "en", "clock", "time.now", {}),
    ("What's the date today?", "en", "clock", "date.today", {}),
    ("what day is it", "en", "clock", "date.today", {}),
    ("Saat kaç?", "tr", "clock", "time.now", {}),
    ("saat kac", "tr", "clock", "time.now", {}),
    ("SAAT KAÇ", "tr", "clock", "time.now", {}),
    ("Saat kaç?", "en", "clock", "time.now", {}),  # mislabelled: the Turkish pattern still wins
    ("Bugün ayın kaçı?", "tr", "clock", "date.today", {}),
    ("Set a timer for five minutes.", "en", "timer", "timer.set", {"amount": "five", "unit": "minutes"}),
    ("Start a timer for 90 seconds", "en", "timer", "timer.set", {"amount": "90", "unit": "seconds"}),
    ("set timer for twenty five minutes", "en", "timer", "timer.set", {"amount": "twenty five"}),
    ("Beş dakikalık zamanlayıcı kur.", "tr", "timer", "timer.set", {"amount": "beş", "unit": "dakika"}),
    ("Zamanlayıcı kur on saniye", "tr", "timer", "timer.set", {"amount": "on", "unit": "saniye"}),
    ("Cancel my timers", "en", "timer", "timer.cancel", {}),
    ("Zamanlayıcıyı iptal et", "tr", "timer", "timer.cancel", {}),
    ("Take a note: buy milk.", "en", "notes", "note.add", {"text": "buy milk"}),
    ("remember that the meeting is at 3", "en", "notes", "note.add", {"text": "the meeting is at 3"}),
    ("Not al: süt al", "tr", "notes", "note.add", {"text": "süt al"}),
    ("Yarın Ankara'ya git diye not al", "tr", "notes", "note.add", {"text": "yarın ankara'ya git"}),
    ("Read my notes", "en", "notes", "note.list", {}),
    ("Notlarımı oku.", "tr", "notes", "note.list", {}),
    ("System status", "en", "system", "system.info", {}),
    ("Sistem durumu nedir?", "tr", "system", "system.info", {}),
    ("What can you do?", "en", "help", "help", {}),
    ("Neler yapabilirsin?", "tr", "help", "help", {}),
    ("Open the calculator.", "en", "apps", "app.open", {"app": "calculator"}),
    ("Hesap makinesini aç", "tr", "apps", "app.open", {"app": "hesap makinesini"}),
]


@pytest.mark.parametrize(("text", "lang", "skill", "intent", "slots"), GOLDEN)
def test_golden_intents(
    full_router: SkillRouter, text: str, lang: str, skill: str, intent: str, slots: dict[str, str]
) -> None:
    match = full_router.match(text, lang)
    assert match is not None, text
    assert (match.skill, match.intent) == (skill, intent)
    for key, value in slots.items():
        assert match.slots[key] == value


@pytest.mark.parametrize("text", ["", "   ", "the weather in Mardin", "bugün hava nasıl", "jarvis"])
def test_unmatched_utterances(full_router: SkillRouter, text: str) -> None:
    assert full_router.match(text, "en") is None


def test_answer_language_follows_the_matched_pattern_not_the_stt_label(full_router: SkillRouter) -> None:
    match = full_router.match("Saat kaç?", "en")
    assert match is not None and match.language == "tr"


def test_turkish_lowercasing_and_normalisation() -> None:
    assert lower("IŞIK", "tr") == "ışık"
    assert lower("İSTANBUL", "tr") == "istanbul"
    assert lower("I am", "en") == "i am"
    assert normalize("  Saat   kaç?!  ", "tr") == "saat kaç"
    utterance = Utterance.of("Notlarımı OKU", "tr")
    assert utterance.display == "notlarımı oku"
    assert utterance.folded == "notlarimi oku"
    assert len(utterance.display) == len(utterance.folded)  # spans stay aligned


@pytest.mark.parametrize(
    ("text", "lang", "value"),
    [("5", "en", 5), ("five", "en", 5), ("twenty five", "en", 25), ("twenty-five", "en", 25), ("a", "en", 1),
     ("beş", "tr", 5), ("yirmi beş", "tr", 25), ("kırk", "tr", 40), ("on", "tr", 10), ("many", "en", None),
     ("", "tr", None)],
)  # fmt: skip
def test_parse_number(text: str, lang: str, value: int | None) -> None:
    assert parse_number(text, lang) == value


async def route(router: SkillRouter, text: str, lang: str, ctx: SkillContext | None = None) -> str:
    result = await router.route(text, lang, ctx or SkillContext(now=lambda: FIXED_NOW))
    return result.response.text


async def test_clock_answers_in_both_languages(full_router: SkillRouter) -> None:
    assert await route(full_router, "what time is it", "en") == "It's 14:05."
    assert await route(full_router, "saat kaç", "tr") == "Saat 14:05."
    assert await route(full_router, "what's the date", "en") == "Today is Sunday, November 8, 2026."
    assert await route(full_router, "bugün ayın kaçı", "tr") == "Bugün 8 Kasım 2026, Pazar."


async def test_unknown_without_llm_says_so_in_the_right_language(full_router: SkillRouter) -> None:
    assert await route(full_router, "the weather in Mardin", "en") == "Sorry, I didn't catch that."
    assert await route(full_router, "bugün hava nasıl", "tr") == "Üzgünüm, anlayamadım."
    assert (await full_router.route("xyz", "de", SkillContext())).response.language == "en"


class FakeLlm:
    name = "fake-llm"

    def __init__(self, answer: str = "Mardin is sunny.", fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, prompt: str, language: str) -> str:
        self.prompts.append((prompt, language))
        if self.fail:
            raise TimeoutError
        return self.answer


async def test_llm_fallback_only_sees_unmatched_utterances(tmp_path: Path) -> None:
    llm = FakeLlm()
    router = SkillRouter([ClockSkill()], fallback=LlmFallbackSkill(llm))
    assert await route(router, "what time is it", "en") == "It's 14:05."
    assert llm.prompts == []
    result = await router.route("weather in Mardin?", "en", SkillContext())
    assert (result.skill, result.response.text) == ("llm", "Mardin is sunny.")
    assert llm.prompts == [("weather in Mardin?", "en")]
    assert router.skill_names == ["clock", "llm"]


async def test_llm_failure_degrades_to_a_spoken_apology() -> None:
    router = SkillRouter([], fallback=LlmFallbackSkill(FakeLlm(fail=True)))
    assert await route(router, "hava nasıl", "tr") == "Dil modeline ulaşamıyorum."


async def test_timer_rings_through_notify_and_can_be_cancelled() -> None:
    timer = TimerSkill()
    router = SkillRouter([timer])
    rung: list[tuple[str, str]] = []

    async def notify(text: str, lang: str) -> None:
        rung.append((text, lang))

    ctx = SkillContext(notify=notify)
    timer.max_seconds = 10**6
    assert await route(router, "set a timer for 1 second", "en", ctx) == "Timer set for 1 second."
    await asyncio.sleep(1.1)
    assert rung == [("Your 1 second timer is done.", "en")]

    assert (
        await route(router, "beş dakikalık zamanlayıcı", "tr", ctx) == "beş dakika için zamanlayıcı kuruldu."
    )
    assert timer.active == 1
    assert await route(router, "zamanlayıcıyı iptal et", "tr", ctx) == "1 zamanlayıcı iptal edildi."
    await asyncio.sleep(0)
    assert timer.active == 0


async def test_timer_limits() -> None:
    timer = TimerSkill()
    router = SkillRouter([timer])
    assert await route(router, "set a timer for 25 hours", "en") == "Timers can be at most 24 hours."
    timer.max_active = 1
    await route(router, "set a timer for 5 minutes", "en")
    assert await route(router, "set a timer for 6 minutes", "en") == "Too many timers are running."
    await route(router, "cancel timers", "en")


async def test_notes_are_appended_and_read_back(tmp_path: Path) -> None:
    store = NoteStore(tmp_path / "notes.jsonl")
    router = SkillRouter([NotesSkill(store)])
    assert await route(router, "read my notes", "en") == "You have no notes."
    assert await route(router, "take a note buy milk", "en") == "Noted: buy milk."
    assert await route(router, "not al: süt al", "tr") == "Not aldım: süt al."
    assert await route(router, "notlarımı oku", "tr") == "Son notların: buy milk; süt al."
    store.add("x" * 2_000, FIXED_NOW)
    assert len(store.latest(1)[0]) == NoteStore.max_chars


async def test_open_app_uses_the_allow_list_argv_and_never_the_transcript() -> None:
    apps = OpenAppSkill(dry_run=True)
    router = SkillRouter([apps])
    text = await route(router, "open calculator", "en")
    assert text == "Would open calculator (dry run)."
    expected = {
        "windows": ["calc.exe"],
        "darwin": ["open", "-a", "Calculator"],
        "linux": ["gnome-calculator"],
    }
    assert apps.launched == [expected[platform.system().lower()]]

    for hostile in ["open calc; rm -rf ~", "open powershell", "open ../../bin/sh", "start cmd.exe /c del"]:
        reply = await route(router, hostile, "en")
        assert "allow-list" in reply, hostile
    assert len(apps.launched) == 1


async def test_open_app_on_a_platform_without_an_entry() -> None:
    apps = OpenAppSkill([AllowedApp("only-mac", ("only mac",), {"plan9": ["acme"]})], dry_run=True)
    assert await route(SkillRouter([apps]), "open only mac", "en") == "I can't open only-mac on this system."


def test_allow_list_file(tmp_path: Path) -> None:
    path = tmp_path / "apps.json"
    path.write_text(
        '{"music": {"aliases": ["Müzik", "music"], "argv": {"linux": ["rhythmbox"]}}}', encoding="utf-8"
    )
    (app,) = load_allow_list(path)
    assert app.key == "music" and app.aliases == ("muzik", "music") and app.argv == {"linux": ["rhythmbox"]}
    assert load_allow_list(None)[0].key == "calculator"


async def test_system_info_reports_cpu_count(full_router: SkillRouter) -> None:
    import os

    text = await route(full_router, "system status", "en")
    assert f"{os.cpu_count()} logical CPUs" in text
    assert "mantıksal işlemci" in await route(full_router, "sistem durumu", "tr")


async def test_a_failing_skill_is_contained() -> None:
    class Broken:
        name = "broken"
        intents = ClockSkill.intents

        async def handle(self, match: object, ctx: object) -> object:
            raise RuntimeError("boom")

    router = SkillRouter([Broken()])  # type: ignore[list-item]
    assert await route(router, "what time is it", "en") == "Sorry, that skill failed."


def test_duplicate_skill_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        SkillRouter([ClockSkill(), ClockSkill()])


def test_vocabulary_prompt_is_built_from_skill_examples(full_router: SkillRouter) -> None:
    tr = full_router.vocabulary("tr")
    assert tr.startswith("Saat kaç? Bugün ayın kaçı?")
    assert "Notlarımı oku." in tr
    assert "What time is it?" in full_router.vocabulary("en")
    assert len(full_router.vocabulary("en", max_chars=40)) <= 40
    assert full_router.vocabulary("de") == ""
