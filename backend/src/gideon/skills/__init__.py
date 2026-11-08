"""Pluggable skills. A skill is any object with ``name``, ``intents`` and ``async handle(match, ctx)``."""

from gideon.skills.base import IntentMatch, IntentSpec, Skill, SkillContext, SkillResponse
from gideon.skills.router import RouteResult, SkillRouter

__all__ = [
    "IntentMatch",
    "IntentSpec",
    "RouteResult",
    "Skill",
    "SkillContext",
    "SkillResponse",
    "SkillRouter",
]
