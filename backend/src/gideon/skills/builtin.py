"""Built-in skills. Patterns are written against folded, lowercased text (see :func:`gideon.skills.base.fold`)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gideon.skills.base import IntentMatch, IntentSpec, SkillContext, SkillResponse, fold, parse_number, say

log = logging.getLogger(__name__)

_EN_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_TR_DAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
_EN_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December")  # fmt: skip
_TR_MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
              "Kasım", "Aralık")  # fmt: skip


class ClockSkill:
    name = "clock"
    intents = (
        IntentSpec(
            "time.now",
            {
                "en": [r"\bwhat(?:'s| is)? the time\b", r"\bwhat time is it\b", r"\btell me the time\b"],
                "tr": [r"\bsaat kac\b", r"\bsaati (?:soyle|soyler misin)\b", r"\bsaat ne\b"],
            },
            {"en": ["What time is it?"], "tr": ["Saat kaç?"]},
        ),
        IntentSpec(
            "date.today",
            {
                "en": [r"\bwhat(?:'s| is)? (?:the |today's )?date\b", r"\bwhat day is (?:it|today)\b"],
                "tr": [
                    r"\bbugun (?:ayin kaci|gunlerden ne|ne gunu|hangi gun)\b",
                    r"\b(?:bugunun )?tarih(?:i)? (?:ne|nedir|soyle)\b",
                ],
            },
            {"en": ["What's the date today?"], "tr": ["Bugün ayın kaçı?"]},
        ),
    )

    async def handle(self, match: IntentMatch, ctx: SkillContext) -> SkillResponse:
        now = ctx.now()
        if match.intent == "time.now":
            return say(match.language, en=f"It's {now:%H:%M}.", tr=f"Saat {now:%H:%M}.")
        en = f"Today is {_EN_DAYS[now.weekday()]}, {_EN_MONTHS[now.month - 1]} {now.day}, {now.year}."
        tr = f"Bugün {now.day} {_TR_MONTHS[now.month - 1]} {now.year}, {_TR_DAYS[now.weekday()]}."
        return say(match.language, en=en, tr=tr)


_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "minute": 60, "minutes": 60, "hour": 3600, "hours": 3600,
    "saniye": 1, "dakika": 60, "saat": 3600,
}  # fmt: skip


class TimerSkill:
    """Countdown timers that announce themselves through ``ctx.notify`` (spoken if the assistant is idle)."""

    name = "timer"
    max_seconds = 24 * 3600
    max_active = 10
    intents = (
        IntentSpec(
            "timer.cancel",
            {
                "en": [r"\b(?:cancel|stop|clear) (?:the |my |all )?timers?\b"],
                "tr": [r"\b(?:zamanlayici|sayac)(?:yi|lari|i)? (?:iptal et|durdur|sil)\b"],
            },
            {"en": ["Cancel the timer."], "tr": ["Zamanlayıcıyı iptal et."]},
        ),
        IntentSpec(
            "timer.set",
            {
                "en": [
                    r"\b(?:set|start)(?: a| the)? timer (?:for )?(?P<amount>[a-z0-9 -]+?) "
                    r"(?P<unit>seconds?|minutes?|hours?)\b",
                    r"\b(?P<amount>[a-z0-9 -]+?) (?P<unit>seconds?|minutes?|hours?) timer\b",
                ],
                "tr": [
                    r"\b(?P<amount>[a-z0-9 ]+?) (?P<unit>saniye|dakika|saat)(?: ?lik)? "
                    r"(?:bir )?(?:zamanlayici|sayac|alarm)\b",
                    r"\b(?:zamanlayici|sayac) kur (?P<amount>[a-z0-9 ]+?) (?P<unit>saniye|dakika|saat)\b",
                ],
            },
            {"en": ["Set a timer for ten minutes."], "tr": ["On dakikalık zamanlayıcı kur."]},
        ),
    )

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def active(self) -> int:
        return len(self._tasks)

    async def handle(self, match: IntentMatch, ctx: SkillContext) -> SkillResponse:
        lang = match.language
        if match.intent == "timer.cancel":
            count = len(self._tasks)
            for task in list(self._tasks):
                task.cancel()
            self._tasks.clear()
            return say(lang, en=f"Cancelled {count} timer(s).", tr=f"{count} zamanlayıcı iptal edildi.")

        # "a timer for five minutes" -> amount may carry a leading article in English.
        amount_text = match.slots.get("amount", "")
        words = amount_text.split()
        amount = parse_number(amount_text, lang)
        if amount is None and len(words) > 1:
            amount = parse_number(words[-1], lang)
        unit = fold(match.slots.get("unit", ""))
        if amount is None or amount <= 0 or unit not in _UNIT_SECONDS:
            return say(lang, en="How long should the timer be?", tr="Zamanlayıcı ne kadar olsun?")
        seconds = amount * _UNIT_SECONDS[unit]
        if seconds > self.max_seconds:
            return say(
                lang, en="Timers can be at most 24 hours.", tr="Zamanlayıcı en fazla 24 saat olabilir."
            )
        if len(self._tasks) >= self.max_active:
            return say(lang, en="Too many timers are running.", tr="Çok fazla zamanlayıcı çalışıyor.")

        label = match.slots["amount"] + " " + match.slots["unit"]
        task = asyncio.create_task(self._ring(seconds, label, lang, ctx), name=f"timer-{seconds}s")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return say(lang, en=f"Timer set for {label}.", tr=f"{label} için zamanlayıcı kuruldu.")

    async def _ring(self, seconds: int, label: str, lang: str, ctx: SkillContext) -> None:
        await asyncio.sleep(seconds)
        text = say(lang, en=f"Your {label} timer is done.", tr=f"{label} zamanlayıcısı doldu.").text
        await ctx.notify(text, lang)


class SystemInfoSkill:
    name = "system"
    intents = (
        IntentSpec(
            "system.info",
            {
                "en": [
                    r"\bsystem (?:info|information|status)\b",
                    r"\bhow is (?:my |the )?(?:computer|system)\b",
                ],
                # "durumu", "durumun" (Whisper splits "durumu nedir" as "durumun edir"), "bilgisi", ...
                "tr": [r"\bsistem (?:durum|bilgi)\w*", r"\bbilgisayar(?:in)? (?:durumu|nasil)\b"],
            },
            {"en": ["System status."], "tr": ["Sistem durumu nedir?"]},
        ),
    )

    def __init__(self) -> None:
        self._started = datetime.now(UTC)

    async def handle(self, match: IntentMatch, ctx: SkillContext) -> SkillResponse:
        cpus = os.cpu_count() or 1
        system = f"{platform.system()} {platform.release()}".strip()
        load = ""
        if hasattr(os, "getloadavg"):
            one, _, _ = os.getloadavg()
            load = f"{one / cpus * 100:.0f}"
        up_min = int((datetime.now(UTC) - self._started).total_seconds() // 60)
        py = f"{sys.version_info.major}.{sys.version_info.minor}"
        en = f"Running on {system} with {cpus} logical CPUs"
        tr = f"{system} üzerinde, {cpus} mantıksal işlemciyle çalışıyorum"
        if load:
            en += f", load {load} percent"
            tr += f", yük yüzde {load}"
        en += f". Python {py}, up {up_min} minutes."
        tr += f". Python {py}, {up_min} dakikadır açığım."
        return say(match.language, en=en, tr=tr)


@dataclass(frozen=True)
class AllowedApp:
    key: str
    aliases: tuple[str, ...]
    argv: Mapping[str, Sequence[str]]  # platform.system().lower() -> argv


DEFAULT_APPS: tuple[AllowedApp, ...] = (
    AllowedApp(
        "calculator",
        ("calculator", "calc", "hesap makinesi", "hesap makinesini"),
        {"windows": ["calc.exe"], "darwin": ["open", "-a", "Calculator"], "linux": ["gnome-calculator"]},
    ),
    AllowedApp(
        "notepad",
        ("notepad", "text editor", "not defteri", "not defterini"),
        {"windows": ["notepad.exe"], "darwin": ["open", "-a", "TextEdit"], "linux": ["gedit"]},
    ),
    AllowedApp(
        "browser",
        ("browser", "web browser", "tarayici", "tarayiciyi"),
        {"windows": ["explorer.exe", "https://example.com"], "darwin": ["open", "https://example.com"],
         "linux": ["xdg-open", "https://example.com"]},
    ),
)  # fmt: skip


def load_allow_list(path: Path | None) -> tuple[AllowedApp, ...]:
    """Allow-list from JSON: ``{"key": {"aliases": [...], "argv": {"windows": [...], ...}}}``."""
    if path is None:
        return DEFAULT_APPS
    data = json.loads(path.read_text(encoding="utf-8"))
    apps = []
    for key, entry in data.items():
        argv = {os_name: [str(a) for a in args] for os_name, args in entry["argv"].items()}
        aliases = tuple(fold(a.lower()) for a in entry.get("aliases", [key]))
        apps.append(AllowedApp(key, aliases, argv))
    return tuple(apps)


class OpenAppSkill:
    """Opens applications from a fixed allow-list only.

    The transcript only ever selects an allow-list *key*; it never becomes part of a command line, and processes
    start with ``create_subprocess_exec`` (no shell). A misheard or malicious phrase can at worst open the
    calculator. ``dry_run`` (the default in Docker) reports what would run without starting anything.
    """

    name = "apps"
    intents = (
        IntentSpec(
            "app.open",
            {
                "en": [r"\b(?:open|launch|start) (?:the |my )?(?P<app>[a-z0-9 ]+?)(?: app| application)?$"],
                "tr": [
                    r"^(?P<app>[a-z0-9 ]+?) (?:uygulamasini |programini )?(?:ac|baslat)$",
                    r"\b(?:ac|baslat) (?P<app>[a-z0-9 ]+)$",
                ],
            },
            {"en": ["Open the calculator."], "tr": ["Hesap makinesini aç."]},
        ),
    )

    def __init__(self, apps: Sequence[AllowedApp] = DEFAULT_APPS, *, dry_run: bool = False) -> None:
        self.apps = tuple(apps)
        self.dry_run = dry_run
        self.launched: list[list[str]] = []

    def resolve(self, spoken: str) -> AllowedApp | None:
        wanted = fold(spoken.lower()).strip()
        for app in self.apps:
            if wanted == app.key or wanted in app.aliases:
                return app
        return None

    async def handle(self, match: IntentMatch, ctx: SkillContext) -> SkillResponse:
        lang = match.language
        spoken = match.slots.get("app", "")
        app = self.resolve(spoken)
        if app is None:
            log.info("app not allowed", extra={"fields": {"requested": spoken[:60]}})
            return say(lang, en=f"{spoken} is not on my allow-list.", tr=f"{spoken} izin listemde yok.")
        argv = app.argv.get(platform.system().lower())
        if not argv:
            return say(
                lang, en=f"I can't open {app.key} on this system.", tr=f"Bu sistemde {app.key} açamam."
            )
        self.launched.append(list(argv))
        if self.dry_run:
            log.info("app launch (dry run)", extra={"fields": {"app": app.key, "argv": list(argv)}})
            return say(lang, en=f"Would open {app.key} (dry run).", tr=f"{app.key} açılırdı (deneme modu).")
        try:
            await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            log.warning("app launch failed", extra={"fields": {"app": app.key}})
            return say(lang, en=f"I couldn't open {app.key}.", tr=f"{app.key} açılamadı.")
        return say(lang, en=f"Opening {app.key}.", tr=f"{app.key} açılıyor.")


class NoteStore:
    """Notes as JSONL in the user's data directory: append-only, human-readable, trivially backed up."""

    max_chars = 500

    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, text: str, at: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": at.isoformat(timespec="seconds"), "text": text[: self.max_chars]}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def latest(self, count: int) -> list[str]:
        if not self.path.exists():
            return []
        lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return [json.loads(ln)["text"] for ln in lines[-count:]]


class NotesSkill:
    name = "notes"
    intents = (
        IntentSpec(
            "note.list",
            {
                "en": [r"\b(?:read|list|what are) (?:me )?(?:my )?notes\b"],
                "tr": [r"\bnotlarimi (?:oku|soyle|listele)\b", r"\bnotlarim (?:neler|ne)\b"],
            },
            {"en": ["Read my notes."], "tr": ["Notlarımı oku."]},
        ),
        IntentSpec(
            "note.add",
            {
                "en": [
                    r"\b(?:take|make|add|write) (?:a )?note(?: that)?:? (?P<text>.+)$",
                    r"\b(?:note|remember) that (?P<text>.+)$",
                ],
                "tr": [r"\bnot (?:al|et|yaz):? (?P<text>.+)$", r"^(?P<text>.+) diye not (?:al|et)$"],
            },
            {"en": ["Take a note: call the bank."], "tr": ["Not al: ekmek al."]},
        ),
    )

    def __init__(self, store: NoteStore) -> None:
        self.store = store

    async def handle(self, match: IntentMatch, ctx: SkillContext) -> SkillResponse:
        lang = match.language
        if match.intent == "note.add":
            text = match.slots.get("text", "").strip()
            if not text:
                return say(lang, en="What should I note?", tr="Neyi not alayım?")
            self.store.add(text, ctx.now())
            return say(lang, en=f"Noted: {text}.", tr=f"Not aldım: {text}.")
        notes = self.store.latest(3)
        if not notes:
            return say(lang, en="You have no notes.", tr="Hiç notun yok.")
        joined = "; ".join(notes)
        return say(lang, en=f"Your latest notes: {joined}.", tr=f"Son notların: {joined}.")


class HelpSkill:
    name = "help"
    intents = (
        IntentSpec(
            "help",
            {
                "en": [r"\bwhat can you do\b", r"^help(?: me)?$"],
                "tr": [r"\bneler yapabilirsin\b", r"\bne yapabilirsin\b", r"^yardim$"],
            },
            {"en": ["What can you do?"], "tr": ["Neler yapabilirsin?"]},
        ),
    )

    async def handle(self, match: IntentMatch, ctx: SkillContext) -> SkillResponse:
        return say(
            match.language,
            en="I can tell the time and date, set timers, take notes, report system status and open allowed apps.",
            tr="Saati ve tarihi söyleyebilir, zamanlayıcı kurabilir, not alabilir, sistem durumunu söyleyebilir "
            "ve izinli uygulamaları açabilirim.",
        )
