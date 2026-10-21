"""Rules for /practice, a solo fight against a Dummy that the bot controls. No Discord code.

The Dummy is an ordinary Fighter with no owner, so nobody can declare for it. At the start
of every round `plan_moves` picks what it will do and `apply_plan` declares those moves, so
by the time the player picks theirs the battle already has everything it needs to run.
A player who is shown the plan can try a Clash on purpose: a Clash happens when the player's
slot aims at the Dummy slot that is aiming back at it.
"""

import random
from dataclasses import dataclass

from game.battle import Battle, Fighter
from game.npc_tiers import NPC_TIERS
from game.skills import Skill

DUMMY_NAME = "Dummy"
TRAINEE_NAME = "Trainee"

# The tiers a player can pick. They are the same presets /battle addnpc uses.
PRACTICE_TIERS = ("fodder", "elite")
DEFAULT_TIER = "fodder"

TIER_NOTES = {
    "fodder": "Easy: 60 HP, one attack a round",
    "elite": "Tougher: 150 HP, three attacks a round, hits harder",
}

# How many of its skill slots the Dummy uses each round. The easy one attacks once, so a new
# player has a single move to read and answer.
MOVES_PER_ROUND = {"fodder": 1, "elite": 3}


def dummy_skills() -> list[Skill]:
    """One skill per damage type, so the Dummy shows how resistances work too."""
    return [
        Skill("Wild Swing", base_power=3, coin_power=1, coins=2, damage_type="slash"),
        Skill("Heavy Smash", base_power=5, coin_power=1, coins=1, damage_type="blunt"),
        Skill("Quick Stab", base_power=3, coin_power=2, coins=2, damage_type="pierce"),
    ]


def starter_skill() -> Skill:
    """Lent to a fighter that has no skill, so anyone can try a practice fight."""
    return Skill("Practice Strike", base_power=4, coin_power=2, coins=2, damage_type="blunt")


def build_dummy(tier_key: str = DEFAULT_TIER, name: str = DUMMY_NAME) -> Fighter:
    preset = NPC_TIERS.get(tier_key) or NPC_TIERS[DEFAULT_TIER]
    dummy = Fighter(
        name=name, side="B", hp=preset.hp, max_hp=preset.hp, power=preset.power,
        skill_slots=preset.skill_slots, speed_min=preset.speed_min, speed_max=preset.speed_max,
        level=preset.level, owner_id=None,
    )
    dummy.stagger_tiers_enabled = list(preset.stagger_tiers_enabled)
    dummy.stagger_thresholds = list(preset.stagger_thresholds)
    for skill in dummy_skills():
        dummy.add_skill(skill)
    return dummy


def build_trainee(owner_id: int) -> Fighter:
    """A ready-made fighter for someone who has not made a character yet."""
    trainee = Fighter(name=TRAINEE_NAME, side="A", hp=100, max_hp=100, owner_id=owner_id)
    trainee.add_skill(starter_skill())
    return trainee


@dataclass(frozen=True)
class Move:
    slot: int
    skill: Skill
    target: Fighter
    target_slot: int


def _foes(battle: Battle, dummy: Fighter) -> list[Fighter]:
    return [f for f in battle.fighters if f.side != dummy.side and f.is_alive() and not f.eliminated]


def plan_moves(battle: Battle, dummy: Fighter, rng=random, max_moves: int | None = None) -> list[Move]:
    """Picks a skill, a target and a target slot for each of the opponent's slots (the first `max_moves` of them).

    Different slots use different skills and aim at different enemy slots when there is
    room, so a round shows more than one thing at once. A skill it cannot pay for (a
    Supermove without the Sanity) is left out. An empty list means it has nothing to do.
    """
    foes = _foes(battle, dummy)
    skills = list(dummy.skills.values())
    if not foes or not skills or not dummy.can_act():
        return []
    taken: dict[str, set[int]] = {}
    picked: set[str] = set()
    plan = []
    slots = [s for s in range(1, dummy.skill_slots + 1) if not dummy.slot_disabled(s)]
    for slot in slots[:max_moves]:
        options = [sk for sk in skills if dummy.supermove_check(slot, sk)[1]]
        if not options:
            continue
        skill = rng.choice([sk for sk in options if sk.name not in picked] or options)
        picked.add(skill.name)
        target = rng.choice(foes)
        used = taken.setdefault(target.name, set())
        open_slots = [s for s in range(1, target.skill_slots + 1) if s not in used]
        target_slot = rng.choice(open_slots or list(range(1, target.skill_slots + 1)))
        used.add(target_slot)
        plan.append(Move(slot, skill, target, target_slot))
    return plan


def apply_plan(dummy: Fighter, plan: list[Move]) -> int:
    """Declares the planned moves. Returns how many were accepted."""
    return sum(1 for m in plan if dummy.declare_in_slot(m.slot, m.skill, m.target, m.target_slot))


def needs_plan(dummy: Fighter) -> bool:
    return dummy.can_act() and not dummy.has_declared()


def describe_plan(plan: list[Move], dummy: Fighter, show: bool = True) -> str:
    """The channel message for a new round: what the Dummy picked, or only that it picked."""
    if not plan and not dummy.skills:
        return (
            f"**Practice.** {dummy.name} has no skills yet, so the round can't run. "
            f"Add one with `/practice addskill`, pick your own move, then run `/battle combat`."
        )
    if not plan:
        return f"**Practice.** {dummy.name} can't act this round, so you only need to pick your own move."
    if not show:
        return (
            f"**Practice.** {dummy.name} has picked its moves and is keeping them to itself. "
            f"Pick yours, then run `/battle combat`."
        )
    lines = [f"- Slot {m.slot}: **{m.skill.name}** at {m.target.name}'s Slot {m.target_slot}" for m in plan]
    first = plan[0]
    which = "it" if len(plan) == 1 else "the first one"
    return (
        f"**Practice.** {dummy.name} is going to do this:\n" + "\n".join(lines) + "\n"
        f"To Clash a move, aim back at it from the slot it is aiming at. To Clash {which}, use "
        f"{first.target.name}'s Slot {first.target_slot} on {dummy.name}'s Slot {first.slot}. "
        f"A move you don't answer hits unopposed. Pick yours with `/battle declare`, then run `/battle combat`."
    )


def outcome(battle: Battle) -> str | None:
    """'won' when Side B is down, 'lost' when Side A is down, 'draw' when both are, else None (still going)."""
    def standing(side: str) -> bool:
        return any(f.is_alive() and not f.eliminated for f in battle.side(side))

    a, b = standing("A"), standing("B")
    if a and b:
        return None
    if a:
        return "won"
    if b:
        return "lost"
    return "draw"


def end_text(result: str, rounds: int, foe: str = DUMMY_NAME) -> str:
    fought = f"{rounds} round" + ("" if rounds == 1 else "s")
    if result == "won":
        head = f"**You win!** {foe} went down after {fought}."
    elif result == "lost":
        head = f"**You were knocked out** after {fought}. It's only practice."
    else:
        head = f"**Both sides went down** after {fought}."
    return (
        f"{head} `/battle log` shows any round again. "
        f"Run `/practice start` or `/practice bot` to go again, or `/practice stop` to clear the channel."
    )


def start_text(player: Fighter, dummy: Fighter, lent_skill: str | None, show_plan: bool, trainee: bool = False) -> str:
    """The private how-it-works message after /practice start."""
    skill = next(iter(player.skills.values())).name if player.skills else "your skill"
    if trainee:
        who = (
            f"You don't have a character yet, so you fight as **{player.name}** with a starter skill, "
            f"**{lent_skill}**. Make your own with `/character create`.\n"
        )
    elif lent_skill:
        who = f"{player.name} has no skills yet, so I lent it **{lent_skill}** for this fight (it isn't saved).\n"
    else:
        who = ""
    seen = "It will tell you what it plans to do." if show_plan else "It keeps its plan secret."
    return (
        f"**Practice fight started.** {player.name} (Side A) against {dummy.name} (Side B, {dummy.max_hp} HP). "
        f"{dummy.name} is run by the bot and picks its moves at the start of every round. {seen}\n"
        f"{who}"
        f"**Each round:**\n"
        f"1. Pick a move: `/battle declare fighter:{player.name} slot:1 skill_name:{skill} "
        f"target:{dummy.name} target_slot:1`, then press **Confirm** within a minute.\n"
        f"2. Run `/battle combat` to roll it.\n"
        f"3. Read what happened. `/battle log` shows it again.\n"
        f"Done early? `/practice stop`."
    )


def bot_start_text(player: Fighter, bot: Fighter, summary: str, lent_skill: str | None, show_plan: bool, trainee: bool, manual: bool) -> str:
    """The private how-it-works message after /practice bot. `summary` is practice_bot.bot_text."""
    skill = next(iter(player.skills.values())).name if player.skills else "your skill"
    if trainee:
        who = (
            f"You don't have a character yet, so you fight as **{player.name}** (Level 1) with a starter skill, "
            f"**{lent_skill}**. Make your own with `/character create`.\n"
        )
    elif lent_skill:
        who = f"{player.name} has no skills yet, so I lent it **{lent_skill}** for this fight (it isn't saved).\n"
    else:
        who = ""
    if manual:
        setup = (
            f"{bot.name} has its stats but no skills or passives. Give it some with `/practice addskill` and "
            f"`/practice addpassive` (the same rules as a character, within its points). The round won't run until it has a skill.\n"
        )
    else:
        setup = "See all of it any time with `/practice sheet`.\n"
    seen = "It will tell you what it plans to do." if show_plan else "It keeps its plan secret."
    return (
        f"**Practice fight started.** {player.name} (Side A) against {bot.name} (Side B), a random opponent made with the same rules as a character. "
        f"{bot.name} is run by the bot and picks its moves at the start of every round. {seen}\n"
        f"{who}**{bot.name}:**\n{summary}\n{setup}"
        f"**Each round:** `/battle declare fighter:{player.name} slot:1 skill_name:{skill} "
        f"target:{bot.name} target_slot:1` and press **Confirm** within a minute, then `/battle combat`. "
        f"`/battle log` shows a round again. Done early? `/practice stop`."
    )
