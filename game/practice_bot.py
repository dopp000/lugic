"""A random practice opponent built with the same rules as a player. No Discord code.

`generate_bot` makes an ordinary (unsaved) Character with the Build Points, Skill Points and
Passive Points it is given, so a bot can match the level and points of the player it fights.
The stats are bought one step at a time at random, and every step is kept only if
`check_build` still accepts the build, so a bot never breaks the rules a player has to follow.
`spend` caps how many Build Points it may use, so by default a bot has spent only as many as the
player it fights; without it the bot spends every point of its pool. Skills are made to fit its
Power, its status ranks and its Skill Points, and passives to fit its Passive Points. A bot with no skills or passives (`skills=False`) is for a player who wants
to add those by hand.

The bot only gets attack skills. The practice AI (game/practice.py) does not play Guard, Evade
or Counter, so it never buys those archetypes or the Supermove slots.
"""

import copy
import random

from game.build_points import (
    HP_MAX, HP_STEP, LEVEL_THRESHOLDS, NONKEYWORD_CAP, OFFDEF_MAX, POWER_MAX, RANK_BUDGETS, RANK_MAX, RANK_MAX_STATUSES,
    RANK_POTENCY_CAPS, SPEED_HIGHEST, STARTING_BP, check_build, level_for, max_base_power, max_coin_power,
    passive_points_spent, skill_points_spent, skill_rank_problems,
)
from game.character import Character
from game.conditions import parse_trigger_text
from game.passives import MAX_PASSIVES, parse_passive
from game.resistances import DAMAGE_TYPES, STATUS_RESISTANCE_TYPES
from game.skills import Skill
from game.statuses import INFLICTABLE_STATUSES

BOT_NAME = "Bot"

# The lowest legal build: base stats and the narrowest legal Speed range (1 to 3).
_START_SPEED = (1, 3)


def bp_for_level(level: int) -> int:
    """The smallest Build Point pool that reaches this Level (1 to 5)."""
    level = max(1, min(int(level), LEVEL_THRESHOLDS[-1][0]))
    return dict(LEVEL_THRESHOLDS)[level]


def points_of(character) -> tuple[int, int, int]:
    """(BP, Skill Points, Passive Points) a character has in total."""
    return (
        int(getattr(character, "bp_total", STARTING_BP)),
        int(getattr(character, "sp_total", 0)),
        int(getattr(character, "pp_total", 0)),
    )


def spent_of(character) -> int:
    """The Build Points a character has spent, never more than its pool."""
    report = check_build(character)
    return max(0, min(report.spent, report.total))


def starting_spent() -> int:
    """What a brand new character has spent (a Trainee's build): the base stats and the narrowest Speed range."""
    new = Character(
        owner_id=0, name=BOT_NAME, speed=_START_SPEED[0], speed_min=_START_SPEED[0], speed_max=_START_SPEED[1],
        bp_total=STARTING_BP,
    )
    return spent_of(new)


# ---- stats -----------------------------------------------------------------

def _speed_range(c) -> tuple[int, int]:
    return c.speed_min, c.speed_max


def _hp(c, rng) -> bool:
    if c.max_hp >= HP_MAX:
        return False
    c.max_hp += HP_STEP
    c.hp = c.max_hp
    return True


def _power(c, rng) -> bool:
    if c.power >= POWER_MAX:
        return False
    c.power += 1
    return True


def _speed_up(c, rng) -> bool:
    lo, hi = _speed_range(c)
    if hi >= SPEED_HIGHEST:
        return False
    c.speed_min, c.speed_max = lo + 1, hi + 1
    return True


def _speed_wider(c, rng) -> bool:
    lo, hi = _speed_range(c)
    if hi >= SPEED_HIGHEST:
        return False
    c.speed_max = hi + 1
    return True


def _offense(c, rng) -> bool:
    if c.offense_level >= OFFDEF_MAX:
        return False
    c.offense_level += 1
    return True


def _defense(c, rng) -> bool:
    if c.defense_level >= OFFDEF_MAX:
        return False
    c.defense_level += 1
    return True


def _rank(c, rng) -> bool:
    owned = {k for k, v in c.status_ranks.items() if v}
    options = [s for s in INFLICTABLE_STATUSES if (s in owned and c.status_ranks[s] < RANK_MAX)
               or (s not in owned and len(owned) < RANK_MAX_STATUSES)]
    if not options:
        return False
    pick = rng.choice(options)
    c.status_ranks[pick] = c.status_ranks.get(pick, 0) + 1
    return True


def _stagger(c, rng) -> bool:
    tiers = list(c.stagger_tiers_enabled or [True, True, True])
    if tiers[1]:
        tiers[1] = False
    elif tiers[2]:
        tiers[2] = False
    else:
        return False
    c.stagger_tiers_enabled = tiers
    return True


def _resist(c, rng) -> bool:
    key = rng.choice(STATUS_RESISTANCE_TYPES)
    now = round(float(c.resistances.get(key, 1.0)), 2)
    nxt = {1.0: 0.75, 0.75: 0.5}.get(now)
    if nxt is None:
        return False
    c.resistances[key] = nxt
    return True


# (name, move, how often it is picked before the bot's own taste is applied)
_MOVES = (
    ("hp", _hp, 1.0), ("power", _power, 1.0), ("speed", _speed_up, 0.8), ("wider", _speed_wider, 0.3),
    ("offense", _offense, 0.8), ("defense", _defense, 0.8), ("rank", _rank, 0.8),
    ("stagger", _stagger, 0.4), ("resist", _resist, 0.5),
)


def _spend_points(c, rng, reserve: int = 0, limit: int | None = None):
    """Buys random upgrades until nothing else fits the budget, keeping `reserve` BP unspent and never spending more than `limit` in total (None: no limit). Each bot has its own taste, so some lean on Power, some on HP, and so on, but the build caps keep any of them from becoming one-sided."""
    taste = {name: weight * rng.uniform(0.3, 2.0) for name, _, weight in _MOVES}
    while True:
        # Weighted order without replacement: a higher taste tends to come first.
        order = sorted(_MOVES, key=lambda m: rng.random() ** (1.0 / taste[m[0]]), reverse=True)
        for _, move, _ in order:
            trial = _copy(c)
            if not move(trial, rng):
                continue
            report = check_build(trial)
            if report.ok and report.spent <= report.total - reserve and (limit is None or report.spent <= limit):
                c = trial
                break
        else:
            return c


def _copy(c):
    return copy.deepcopy(c)


# ---- skills ----------------------------------------------------------------

SKILL_NAMES = {
    "slash": ["Rending Cut", "Crescent Sweep", "Reaper's Edge", "Quick Slice", "Cleaving Arc", "Razor Dance", "Split Second", "Wide Swing"],
    "blunt": ["Heavy Smash", "Iron Fist", "Crushing Blow", "Shoulder Charge", "Hammer Down", "Rattling Strike", "Pounding Rush", "Stone Knuckle"],
    "pierce": ["Thrust", "Needle Point", "Piercing Lunge", "Skewer", "Spear Rush", "Hairline Stab", "Drilling Jab", "Long Reach"],
}

# One-line skill effects, all of them kinds a player may write on a normal skill. The first
# line on a skill is free and each extra line costs a Skill Point (game/build_points.py).
SKILL_LINES = [
    "[On Use] Coin Power +1",
    "[On Use] Deal +10% damage",
    "[On Use] Gain 5 Shield",
    "[Before Attack] If the target has any Burn, Deal +3 damage",
    "[On Use] If this unit's Speed is faster than the target's, Coin Power +1",
    "[On Use] If this unit's HP is 50% or less, Deal +15% damage",
    "[Clash Start] Clash Power +1",
    "[Attack End] Gain 2 Sanity",
    "[Attack End] Heal 4 HP",
    "[Turn Start] Gain 4 Shield",
    "[On Use] If the target's HP is 30% or less, Deal +20% damage",
    "[Before Use] If the target has 3+ negative effects, Coin Power +1",
]

MAX_SKILL_LINES = 3

# Say so when more than this many Build Points could not be spent.
UNSPENT_NOTE_AT = 2


def _skill_name(damage_type: str, used: set[str], rng) -> str:
    options = [n for n in SKILL_NAMES[damage_type] if n.lower() not in used]
    if options:
        name = rng.choice(options)
    else:
        base = rng.choice(SKILL_NAMES[damage_type])
        n = 2
        while f"{base} {n}".lower() in used:
            n += 1
        name = f"{base} {n}"
    used.add(name.lower())
    return name


def _put_status(skill: Skill, status: str, rank: int, rng) -> bool:
    """Puts `status` on some coins of `skill`, inside what `rank` allows. False if nothing fit."""
    free = [i for i in range(skill.coins) if not skill.coin_statuses[i]]
    if not free:
        return False
    rng.shuffle(free)
    chosen = sorted(free[: rng.randint(1, len(free))])
    budget = RANK_BUDGETS[status][rank] if status in RANK_BUDGETS else None
    used = 0
    placed = False
    for i in chosen:
        if status in RANK_POTENCY_CAPS:
            potency, count = rng.randint(1, RANK_POTENCY_CAPS[status][rank]), rng.randint(1, 2)
        else:
            options = [(p, n) for p in (1, 2, 3) for n in (1, 2, 3) if used + p * n <= budget]
            if not options:
                continue
            potency, count = rng.choice(options)
            used += potency * count
        skill.coin_statuses[i] = status
        skill.coin_status_potencies[i] = potency
        skill.coin_status_counts[i] = count
        placed = True
    return placed


def _clear_statuses(skill: Skill):
    skill.coin_statuses = [None] * skill.coins
    skill.coin_status_potencies = [0] * skill.coins
    skill.coin_status_counts = [0] * skill.coins


def _parse(lines: list[str]):
    triggers, flags = parse_trigger_text("\n".join(lines))
    return triggers, flags


def _give_skills(c, rng, count: int | None = None):
    level = level_for(c.bp_total)
    count = count or (3 if level <= 2 else 4)
    types = list(DAMAGE_TYPES)
    rng.shuffle(types)
    used_names: set[str] = set()
    skills = []
    for i in range(count):
        dtype = types[i % len(types)]
        coins = rng.choice((1, 2, 2, 3, 3, 4))
        max_base, max_coin = max_base_power(c.power, coins), max_coin_power(c.power, coins)     # fewer numbers for more coins, like a player's
        skills.append(Skill(
            _skill_name(dtype, used_names, rng),
            base_power=rng.randint(max(1, max_base - 2), max_base),
            coin_power=rng.randint(1, max_coin),
            coins=coins,
            damage_type=dtype,
        ))
    # Every status rank the bot paid for goes on at least one skill, otherwise it is wasted.
    for status, rank in sorted(c.status_ranks.items()):
        if not rank or status not in INFLICTABLE_STATUSES:
            continue
        for skill in rng.sample(skills, len(skills)):
            if _put_status(skill, status, rank, rng):
                break
    ranks = {k: v for k, v in c.status_ranks.items() if v}
    for skill in skills:
        if skill_rank_problems(ranks, skill):
            _clear_statuses(skill)      # never expected, but a bot must not break the rules
    # Trigger lines: one free line per skill, extras paid from the Skill Points.
    sp_left = c.sp_total
    for skill in skills:
        lines: list[str] = []
        if rng.random() < 0.6:
            lines.append(rng.choice(SKILL_LINES))
        while lines and len(lines) < MAX_SKILL_LINES and sp_left > 0 and rng.random() < 0.5:
            extra = rng.choice([l for l in SKILL_LINES if l not in lines])
            lines.append(extra)
            sp_left -= 1
        if lines:
            skill.triggers, flags = _parse(lines)
            skill.tags = set(flags)
    for skill in skills:
        c.add_skill(skill)


# ---- passives --------------------------------------------------------------

# Passives are priced in Passive Points by an admin for a real character. These prices are the bot's own.
PASSIVE_POOL = [
    {"name": "Thick Skin", "kind": "passive", "lines": ["[Combat Start] Gain 10 Shield"], "cost": 1},
    {"name": "Steady Hands", "kind": "passive", "lines": ["[Turn Start] Gain 3 Sanity"], "cost": 1},
    {"name": "Brace", "kind": "passive", "lines": ["[Self On Stagger] Gain 10 Shield"], "cost": 1},
    {"name": "Second Wind", "kind": "passive", "lines": ["[Turn Start] If this unit's HP is 50% or less, Heal 5 HP"], "cost": 2},
    {"name": "Killer Instinct", "kind": "passive", "lines": ["[On Use] If the target's HP is 30% or less, Deal +20% damage"], "cost": 2},
    {"name": "Hardened", "kind": "passive", "lines": ["[Self Is Hit] Gain 2 Shield"], "cost": 2},
    {"name": "Keen Edge", "kind": "passive", "lines": ["[Before Use] Coin Power +1"], "cost": 3},
    {"name": "Warding Habit", "kind": "support", "lines": ["[Turn Start] Gain 1 Protection"], "cost": 3},
]


def _give_passives(c, rng):
    left = c.pp_total
    for entry in rng.sample(PASSIVE_POOL, len(PASSIVE_POOL)):
        if len(c.passives) >= MAX_PASSIVES:
            break
        if entry["cost"] <= left and not parse_passive(entry["lines"])[1]:
            c.passives.append({**entry, "lines": list(entry["lines"]), "note": "Practice bot"})
            left -= entry["cost"]


# ---- putting it together ---------------------------------------------------

def generate_bot(
    bp_total: int, sp_total: int = 0, pp_total: int = 0, rng=None, name: str = BOT_NAME,
    owner_id: int = 0, skills: bool = True, passives: bool = True, spend: int | None = None,
) -> Character:
    """An unsaved Character that follows the Build Point rules for the pools it is given. `spend` is the most Build Points it may use (None: all of them)."""
    rng = rng or random
    c = Character(
        owner_id=owner_id, name=name, speed=_START_SPEED[0], speed_min=_START_SPEED[0], speed_max=_START_SPEED[1],
        bp_total=max(int(bp_total), 1), sp_total=max(int(sp_total), 0), pp_total=max(int(pp_total), 0),
    )
    # A bot whose skills the player will add by hand keeps a few BP back, because a skill with
    # a Haste or Fragile style effect is paid for in Build Points (NONKEYWORD_CAP).
    c = _spend_points(c, rng, reserve=0 if skills else NONKEYWORD_CAP, limit=spend)
    if skills:
        _give_skills(c, rng)
    if passives:
        _give_passives(c, rng)
    return c


def describe_stats(c) -> str:
    lo, hi = c.speed_min, c.speed_max
    bits = [f"HP {c.max_hp}", f"Power {c.power}", f"Speed {lo} to {hi}"]
    if c.offense_level:
        bits.append(f"Offense Level {c.offense_level}")
    if c.defense_level:
        bits.append(f"Defense Level {c.defense_level}")
    ranks = [f"{k.capitalize()} {v}" for k, v in sorted(c.status_ranks.items()) if v]
    if ranks:
        bits.append("Ranks: " + ", ".join(ranks))
    if c.stagger_tiers_enabled and not all(c.stagger_tiers_enabled):
        gone = [str(i + 1) for i, on in enumerate(c.stagger_tiers_enabled) if not on]
        bits.append("No Stagger Tier " + " or ".join(gone))
    resist = [f"{k.capitalize()} {v}x" for k, v in sorted(c.resistances.items())
              if k in STATUS_RESISTANCE_TYPES and round(float(v), 2) != 1.0]
    if resist:
        bits.append("Resists: " + ", ".join(resist))
    return ", ".join(bits)


def describe_skill(skill: Skill) -> str:
    coins = f"{skill.coins} coin" + ("" if skill.coins == 1 else "s")
    extras = []
    names = sorted({n.capitalize() for n in skill.coin_statuses if n})
    if names:
        extras.append(" and ".join(names))
    if skill.triggers:
        extras.append(f"{len(skill.triggers)} trigger line" + ("" if len(skill.triggers) == 1 else "s"))
    tail = f", {', '.join(extras)}" if extras else ""
    return f"{skill.name} ({skill.damage_type.capitalize()}, Base {skill.base_power}, Coin +{skill.coin_power}, {coins}{tail})"


def bot_text(c, spend: int | None = None) -> str:
    """The private summary after a bot is made: level, points, stats, skills and passives. `spend` is the limit the bot was made with, when it was matched to the player's own spending."""
    report = check_build(c)
    sp_used = skill_points_spent(c)
    pp_used = passive_points_spent(c)
    lines = [
        f"Level {report.level}: {report.spent}/{report.total} Build Points, "
        f"{sp_used}/{c.sp_total} Skill Points, {pp_used}/{c.pp_total} Passive Points used.",
        describe_stats(c) + ".",
    ]
    left = report.total - report.spent
    if spend is not None:
        lines.append(
            f"It has spent about as many Build Points as you have ({spend} for you), so the fight is even. "
            "Use the level option to fight a bot with every point of a Level spent."
        )
    elif left > UNSPENT_NOTE_AT:
        if c.skills:
            lines.append(
                f"{left} Build Points are unspent. What is left needs Supermoves or Guard, Evade and Counter skills, "
                "which the practice bot does not use."
            )
        else:
            lines.append(
                f"{left} Build Points are unspent. Effects on the skills you add can cost some of them. "
                "The rest needs Supermoves or Guard, Evade and Counter skills, which the practice bot does not use."
            )
    if c.skills:
        lines.append("Skills: " + "; ".join(describe_skill(s) for s in c.skills.values()) + ".")
    else:
        lines.append("Skills: none yet.")
    if c.passives:
        lines.append("Passives: " + "; ".join(f"{p['name']} ({p['cost']} PP)" for p in c.passives) + ".")
    elif c.pp_total:
        lines.append("Passives: none yet.")
    return "\n".join(lines)
