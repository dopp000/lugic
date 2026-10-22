"""Runs a whole build from the builder site through the bot's own rules.

The stats, ranks, defense types, tags and passives are checked by game/build_requests.py.
Skills have to go through the skill form (cogs/battle.py AddSkillModal), because that is
where the Power ceiling, status rank, defense type, Skill Point and Build Point rules for
skills live. Rather than copy those rules, this module fills the real form in and reads its
answer, so a skill that comes from a code is held to exactly what a skill typed in Discord is.
"""

import copy
import re
from types import SimpleNamespace

from cogs.battle import AddSkillModal
from game.build_points import check_build, passive_points_spent
from game.build_requests import build_steps, build_summary_text, try_change
from game.skills import Skill


class _Answer:
    """Stands in for the interaction the skill form answers. It keeps the first thing the form says."""

    def __init__(self):
        self.user = SimpleNamespace(id=0, bot=False)  # never an admin, so every limit applies
        self.reply = None
        self.view = None
        self.response = self

    async def send_message(self, content=None, *, view=None, **kwargs):
        if self.reply is None and self.view is None:
            self.reply, self.view = content, view


def player_words(reply: str) -> str:
    """The form's refusal without the sentences that send a player to an admin command."""
    lines = []
    for line in (reply or "").split("\n"):
        sentences = re.split(r"(?<=[.!?])\s+", line)
        kept = " ".join(x for x in sentences if "/charadmin" not in x).strip()
        if kept:
            lines.append(kept)
    return "\n".join(lines)


async def check_skill(character, raw: dict) -> tuple[Skill | None, str | None]:
    """Asks the skill form whether `character` could learn this skill. Returns (the skill, None) or (None, why not)."""
    if character.get_skill(raw["name"]) is not None:
        return None, f"{character.name} already has a skill called {raw['name']}."
    modal = AddSkillModal(character)
    modal.skill_name._value = raw["name"]
    modal.stats_input._value = raw["stats"]
    modal.attack_weight_input._value = raw.get("weight", "1")
    modal.status_input._value = raw.get("statuses", "none")
    modal.triggers_input._value = raw.get("triggers", "")
    answer = _Answer()
    await modal.on_submit(answer)
    if answer.view is not None:
        return answer.view.skill, None
    return None, player_words(answer.reply) or "The skill form refused it."


async def run_build(character, params: dict, defer_pp: bool = False) -> tuple[str, str | None, "object | None"]:
    """Applies a build to a copy of `character`. Returns (summary, error, the changed copy).

    Like game.build_requests.try_change, but also teaches the skills, and checks the finished
    character as a whole (a skill's own effects cost Build Points too).

    `defer_pp` is for a new character's import: it has no Passive Points yet (admins hand those
    out), so the passives are checked as if the points were there. The approval checks again for
    real, which is when an admin has to have given them.
    """
    if defer_pp and params.get("passives"):
        character = copy.deepcopy(character)
        asked = sum(int(p.get("cost", 0)) for p in params["passives"])
        character.pp_total = max(character.pp_total, passive_points_spent(character) + asked)
    if build_steps(params):
        _, error, trial = try_change(character, "build", params)
        if error:
            return "", error, None
    else:
        trial = copy.deepcopy(character)
    for raw in params.get("skills") or []:
        skill, why = await check_skill(trial, raw)
        if why:
            return "", f"Skill {raw['name']}:\n{why}", None
        trial.add_skill(skill)
    if not character.bp_exempt:
        before = set(check_build(character).all_problems())
        now = check_build(trial).all_problems()
        fresh = [p for p in now if p not in before]
        if fresh:
            lines = "\n".join(f"- {p}" for p in now[:8])
            return "", f"That would break the Build Point rules:\n{lines}", None
    return build_summary_text(params), None, trial
