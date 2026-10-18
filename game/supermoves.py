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
    # "Deal +N% damage" lines multiply the whole thing (a scaled line counts at its cap).
    pct = sum((t.scale_max or t.effect_value) for t in skill.triggers if t.effect_type == "bonus_damage_pct")
    return round(expected * (1 + pct / 100))


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


# --- Build Point price of owning a Supermove ---------------------------------
# A character buys Supermove slots with Build Points (game/build_points.py).
# The Sanity cost above is what each USE costs in battle; this is what the
# slot costs to own. Every number here is a tuning knob.

MAX_SLOTS = 3          # Supermoves a character can own
MAX_SEVERITY = 200     # expected damage of one use; players cannot buy more than this

# (minimum expected damage, tier name, BP price). First row that fits wins.
BP_TIERS = [
    (130, "High", 22),
    (80, "Mid", 16),
    (0, "Low", 10),
]

# A drawback the admin attaches to a Supermove gives BP back. A slot can hold
# up to two drawbacks (Minor and/or Moderate), or one Severe on its own.
DRAWBACK_DISCOUNTS = {"minor": 2, "moderate": 4, "severe": 7}
MAX_DRAWBACKS_PER_SLOT = 2

# Tags only a Supermove may use: they hand out guaranteed recurring value,
# hinge on a contested outcome, or skip the opponent's counterplay.
SUPERMOVE_ONLY_TIMINGS = {
    "turn_start", "combat_start", "turn_end", "before_use",
    "clash_win", "clash_lose", "hit_after_clash_win", "hit_after_clash_lose",
    "on_unopposed_attack", "ally_turn_start", "ally_combat_start",
}
SUPERMOVE_ONLY_FLAGS = {"unclashable"}

# Extra BP for the most demanding timing a Supermove uses (only the highest
# one counts, they do not stack). Anything not listed costs nothing extra.
SURCHARGE_FLAGS = 1      # [Target Fixed], [Indiscriminate], [Unclashable]
SURCHARGE_PER_COIN = 2   # coin start, on hit, heads/tails hit, crit, attack end per coin
SURCHARGE_PER_SKILL = 3  # before use, before attack, attack end, on kill, on stagger ...
SURCHARGE_CLASH = 4      # clash start/win/lose, hit after clash, unopposed attack, ally reactions
SURCHARGE_TURN = 6       # turn start, turn end, combat start (fire every turn)

_PER_COIN = {
    "coin_start", "on_hit", "heads_hit", "tails_hit", "current_coin_attack_end",
    "heads_attack_end", "tails_attack_end", "on_crit", "on_crit_heads_hit", "on_crit_tails_hit",
}
_PER_SKILL = {
    "before_use", "before_attack", "attack_end", "on_kill", "on_crit_kill",
    "on_stagger", "tremor_burst", "on_evade", "before_getting_hit",
}
_CLASH = {
    "clash_start", "clash_win", "clash_lose", "hit_after_clash_win",
    "hit_after_clash_lose", "on_unopposed_attack",
    "ally_on_hit", "ally_on_crit", "ally_on_kill", "ally_is_hit", "ally_on_stagger",
}
_TURN = {"turn_start", "turn_end", "combat_start", "ally_turn_start", "ally_combat_start"}
_SURCHARGE_FLAG_TAGS = {"target_fixed", "indiscriminate", "unclashable"}


@dataclass
class SupermoveBP:
    tier: str
    base: int
    surcharge: int
    discount: int
    total: int


def bp_tier(severity: int) -> tuple[str, int]:
    for minimum, name, price in BP_TIERS:
        if severity >= minimum:
            return name, price
    return BP_TIERS[-1][1], BP_TIERS[-1][2]


def timing_surcharge(skill) -> int:
    """BP added for the most demanding timing or flag this skill uses."""
    best = 0
    if set(skill.tags) & _SURCHARGE_FLAG_TAGS:
        best = SURCHARGE_FLAGS
    for t in skill.triggers:
        if t.timing in _TURN:
            best = max(best, SURCHARGE_TURN)
        elif t.timing in _CLASH:
            best = max(best, SURCHARGE_CLASH)
        elif t.timing in _PER_SKILL:
            best = max(best, SURCHARGE_PER_SKILL)
        elif t.timing in _PER_COIN:
            best = max(best, SURCHARGE_PER_COIN)
    return best


def drawback_discount(tiers) -> int:
    return sum(DRAWBACK_DISCOUNTS.get(t, 0) for t in tiers)


def supermove_bp(skill, tiers=()) -> SupermoveBP:
    """What owning this Supermove costs in Build Points, after any drawbacks."""
    tier, base = bp_tier(skill_severity(skill))
    surcharge = timing_surcharge(skill)
    discount = drawback_discount(tiers)
    return SupermoveBP(tier, base, surcharge, discount, max(0, base + surcharge - discount))


def supermove_only_tags(skill) -> list[str]:
    """Names (as written in brackets) of Supermove-only tags this skill uses."""
    from game.conditions import ALL_TIMING_LOOKUP
    names = {internal: text for text, internal in reversed(list(ALL_TIMING_LOOKUP.items()))}
    found = []
    for t in skill.triggers:
        if t.timing in SUPERMOVE_ONLY_TIMINGS:
            label = "[" + names.get(t.timing, t.timing).title() + "]"
            if label not in found:
                found.append(label)
    for flag in sorted(set(skill.tags) & SUPERMOVE_ONLY_FLAGS):
        label = "[" + flag.title() + "]"
        if label not in found:
            found.append(label)
    return found


def describe_bp(skill, tiers=()) -> str:
    """One plain line: how the BP price was worked out."""
    p = supermove_bp(skill, tiers)
    text = f"{p.tier} Supermove {p.base} BP"
    if p.surcharge:
        text += f" + {p.surcharge} for its timing"
    if p.discount:
        text += f" - {p.discount} for its drawback"
    return text + f" = {p.total} BP"
