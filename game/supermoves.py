"""
Supermoves (the E.G.O of this setting): a normal skill carrying the
[Supermove] tag. The bot works out its Sanity price from how hard it hits,
so nobody can write a huge skill for free, and every reuse in the same
battle costs more. All the tuning numbers live at the top of this file.
See docs/ENGINEERING_NOTES.md#supermoves.
"""

import math
from dataclasses import dataclass

# Upfront cost, paid when the skill is declared: expected damage / this.
COST_DIVISOR = 6
MIN_COST = 4
MAX_COST = 30

# Aftermath: (minimum expected damage, Sanity lost per turn end, turns).
# The first row that fits wins, below the last row there is no aftermath.
AFTERMATH_TIERS = [
    (130, 10, 3),
    (80, 8, 3),
    (40, 5, 2),
]

# Each earlier use of the same Supermove in this battle adds this fraction
# of the base cost and the base drain on top. 0.5 = +50% per reuse.
REUSE_STEP = 0.5


@dataclass
class SupermoveProfile:
    severity: int      # expected total damage of one use
    cost: int          # Sanity paid on declare (and needed to declare)
    drain: int         # Sanity lost at each turn end afterwards, 0 = none
    drain_turns: int   # how many turn ends it keeps draining


def skill_severity(skill) -> int:
    """Expected total damage of one use, counting flat Deal/Coin Power bonuses as if they always fire."""
    n = skill.coins
    expected = sum(skill.base_power + skill.coin_power * i / 2 for i in range(1, n + 1))
    for t in skill.triggers:
        first = t.coin_index or 1
        left = max(0, n - first + 1)
        if t.effect_type == "bonus_power":
            expected += t.effect_value * left
        elif t.effect_type == "bonus_coin_power":
            expected += t.effect_value * 0.5 * left * (left + 1) / 2
    return round(expected)


def supermove_profile(skill, uses: int = 0) -> SupermoveProfile:
    """Price of using `skill` once more, after it has already been used `uses` times this battle."""
    severity = skill_severity(skill)
    base_cost = min(MAX_COST, max(MIN_COST, round(severity / COST_DIVISOR)))
    base_drain, turns = 0, 0
    for minimum, per_turn, length in AFTERMATH_TIERS:
        if severity >= minimum:
            base_drain, turns = per_turn, length
            break
    mult = 1 + REUSE_STEP * max(0, uses)
    return SupermoveProfile(
        severity=severity,
        cost=min(MAX_COST * 2, math.ceil(base_cost * mult)),
        drain=math.ceil(base_drain * mult) if base_drain else 0,
        drain_turns=turns,
    )


def describe_profile(profile: SupermoveProfile) -> str:
    """One plain line for previews and replies."""
    text = f"costs {profile.cost} Sanity (you need at least that much to use it)"
    if profile.drain:
        text += f", then you lose {profile.drain} Sanity at each turn end for {profile.drain_turns} turns"
    return text
