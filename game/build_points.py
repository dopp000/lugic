"""Build Points (BP): the one budget that pays for a character's stats.

Pure rules, no Discord and no battle state, so everything here can be
tested on its own. Every number a GM might want to tune is a constant at
the top. The design comes from the original wiki guide: a single pool,
level as a label on the pool's size, and caps that stop one-stat builds.

What this module covers so far: HP, Power, Speed, Stagger tier removal,
the Power ceiling on skills, Supermove slots (priced in
game/supermoves.py), resistances, Offense/Defense Level, status ranks,
defense archetypes, non-keyword effects, Skill Points and Passive Points.
"""

from dataclasses import dataclass, field
import math

from game.resistances import DAMAGE_TYPES, STATUS_RESISTANCE_TYPES
from game.supermoves import (
    MAX_SEVERITY as SUPERMOVE_MAX_SEVERITY,
    MAX_SLOTS as SUPERMOVE_MAX_SLOTS,
    describe_bp,
    skill_severity,
    supermove_bp,
)

# --- The pool and levels ---------------------------------------------------
STARTING_BP = 30
# (level, total BP needed). Level is only a label on the running total.
LEVEL_THRESHOLDS = [(1, 30), (2, 45), (3, 63), (4, 84), (5, 108)]

# --- HP ----------------------------------------------------------------------
HP_BASE = 100
HP_MAX = 160
HP_STEP = 10
# Total BP spent at +0, +1, ... +6 steps of HP_STEP.
HP_STEP_COSTS = [0, 1, 2, 4, 4, 6, 8]

# --- Power -------------------------------------------------------------------
POWER_BASE = 6
POWER_MAX = 12
# Total BP spent at Power 6, 7, ... 12 (the old reference tables). Like HP, the
# price climbs unevenly on purpose.
POWER_STEP_COSTS = [0, 1, 2, 4, 4, 6, 8]

# --- Speed -------------------------------------------------------------------
SPEED_LOWEST = 1
SPEED_HIGHEST = 8
SPEED_MIN_SPREAD = 2  # max - min must be at least this
# Total BP for a range whose AVERAGE is this value (an average between two
# whole numbers is priced in between, rounded up).
SPEED_AVG_COSTS = {1: 0, 2: 1, 3: 2, 4: 4, 5: 6, 6: 9, 7: 12, 8: 16}

# --- Stagger -----------------------------------------------------------------
STAGGER_REMOVE_COSTS = {2: 5, 3: 7}

# --- Resistances -------------------------------------------------------------
# Status resistance dials (Burn, Bleed, Tremor, Rupture, Sinking). 1.0x is free.
# Resisting costs BP, being vulnerable gives BP back (negative price).
STATUS_RESIST_COSTS = {0.5: 3, 0.75: 1, 1.0: 0, 1.25: -1, 1.5: -3, 1.75: -6, 2.0: -10}
# Most BP a character can bank from vulnerabilities, all dials together.
RESIST_REFUND_CAP = 18
# Slash / Blunt / Pierce are not priced: they are either all Normal (not chosen
# yet) or one of each 0.5x, 1.0x and 1.5x in any order.
PHYSICAL_SETS = ([1.0, 1.0, 1.0], [0.5, 1.0, 1.5])

# --- Offense / Defense Level -------------------------------------------------
# Each step is 4% more damage dealt (Offense) or 4% less damage taken (Defense).
OFFDEF_MAX = 5
OFFDEF_STEP_COSTS = [0, 1, 2, 4, 4, 6]
OFFDEF_PERCENT_PER_STEP = 4
LEVEL_OVERRIDE_MAX = 10


def offdef_cost(steps: int) -> int:
    return OFFDEF_STEP_COSTS[max(0, min(int(steps), OFFDEF_MAX))]


def character_level(c) -> int:
    """The Level a character fights at: an admin override if set, else from its BP pool."""
    override = getattr(c, "level_override", None)
    if override:
        return max(1, min(int(override), LEVEL_OVERRIDE_MAX))
    return level_for(c.bp_total)


# --- Status ranks ------------------------------------------------------------
# A character can own up to 2 of these statuses at Rank 1 to 5. A normal skill
# may only use a status the character owns, within what its rank allows.
RANKED_STATUSES = ["burn", "bleed", "tremor", "rupture", "sinking", "poise", "charge"]
RANK_MAX = 5
RANK_MAX_STATUSES = 2
RANK_COSTS = [0, 1, 2, 4, 7, 11]
# Bleed / Tremor / Poise / Charge: Potency x Count added up over a whole skill must
# stay at or under the budget for the rank (index 0 = not owned).
RANK_BUDGETS = {s: [0, 4, 6, 8, 10, 12] for s in ("bleed", "tremor", "poise", "charge")}
# Burn / Sinking / Rupture: the most Potency one application may carry.
RANK_POTENCY_CAPS = {
    "burn": [0, 1, 2, 3, 4, 4],
    "sinking": [0, 1, 2, 3, 4, 6],
    "rupture": [0, 1, 1, 2, 2, 3],
}


def status_grants(skill) -> list[tuple[str, int, int]]:
    """Every (status, potency, count) a skill hands out, from its coins and its trigger lines."""
    grants = []
    for name, pot, cnt in zip(skill.coin_statuses, skill.coin_status_potencies, skill.coin_status_counts):
        if name:
            grants.append(("_".join(str(name).lower().split()), pot, cnt))
    for t in skill.triggers:
        if t.effect_type in ("inflict_status", "gain_status") and t.status_name:
            grants.append((t.status_name, t.effect_value, t.status_count))
    return [g for g in grants if g[0] in RANKED_STATUSES]


def skill_rank_problems(ranks: dict, skill) -> list[str]:
    """What stops `skill` from fitting the status ranks in `ranks` (empty list if it fits)."""
    problems = []
    used: dict[str, int] = {}
    for status, pot, cnt in status_grants(skill):
        rank = int(ranks.get(status, 0) or 0)
        label = status.capitalize()
        if rank <= 0:
            msg = f"{label} needs a {label} rank, and this character has none."
            if msg not in problems:
                problems.append(msg)
        elif status in RANK_POTENCY_CAPS:
            cap = RANK_POTENCY_CAPS[status][min(rank, RANK_MAX)]
            if pot > cap:
                problems.append(f"{label} Potency {pot} is over the Rank {rank} limit of {cap}.")
        else:
            used[status] = used.get(status, 0) + pot * cnt
    for status, total in used.items():
        rank = min(int(ranks.get(status, 0)), RANK_MAX)
        budget = RANK_BUDGETS[status][rank]
        if total > budget:
            problems.append(
                f"{status.capitalize()} Potency x Count adds up to {total}, over the Rank {rank} budget of {budget}."
            )
    return problems


# --- Non-keyword effects (Haste, Fragile, Bind, Protection ...) --------------
# A normal skill may hand these out, paid from the BP pool: each grant costs
# ceil(size / 2) BP for its one turn, and the whole kit may spend at most 3 BP.
# Size is the Potency for Bind and Power Down, otherwise the Count. Percent
# effects (Fragile, Damage Up/Down, Protection) count at most 10, Paralyze at most 4.
NONKEYWORD_CAP = 3
NONKEYWORD_SIZE_CAPS = {"paralyze": 4}
NONKEYWORD_PERCENT_CAP = 10


def nonkeyword_grants(skill) -> list[tuple[str, int]]:
    """(status, size) for every non-keyword effect a skill hands out, coins and trigger lines both."""
    from game.statuses import ONE_TURN_STATUSES, POTENCY_TURN_STATUSES
    raw = []
    for name, pot, cnt in zip(skill.coin_statuses, skill.coin_status_potencies, skill.coin_status_counts):
        if name:
            raw.append(("_".join(str(name).lower().split()), pot, cnt))
    for t in skill.triggers:
        if t.effect_type in ("inflict_status", "gain_status") and t.status_name:
            raw.append((t.status_name, t.effect_value, t.status_count))
    out = []
    for name, pot, cnt in raw:
        if name not in ONE_TURN_STATUSES:
            continue
        size = pot if name in POTENCY_TURN_STATUSES else max(pot, cnt)
        out.append((name, max(0, size)))
    return out


def nonkeyword_cost(status: str, size: int) -> int:
    from game.statuses import PERCENT_COUNT_STATUSES
    cap = NONKEYWORD_SIZE_CAPS.get(status, NONKEYWORD_PERCENT_CAP if status in PERCENT_COUNT_STATUSES else None)
    if cap is not None:
        size = min(size, cap)
    return math.ceil(size / 2)


def nonkeyword_total(c) -> tuple[int, list[str]]:
    """BP the whole kit spends on non-keyword effects (Supermoves are priced separately), plus a line per grant."""
    total = 0
    lines = []
    for s in c.skills.values():
        if "supermove" in s.tags:
            continue
        for status, size in nonkeyword_grants(s):
            cost = nonkeyword_cost(status, size)
            total += cost
            lines.append(f"{s.name}: {status.replace('_', ' ').title()} {size} = {cost} BP")
    return total, lines


# --- Defense archetypes ------------------------------------------------------
# A character can own up to 2. A normal Guard / Evade / Counter style skill needs
# the matching archetype. Some need a minimum Speed (the LOWEST of the range) or
# the removed Stagger Tier 2.
DEFENSE_ARCHETYPES = {
    "guard": 3,
    "evade": 4,
    "counter": 5,
    "clashable_guard": 6,
    "clashable_counter": 7,
}
DEFENSE_MAX = 2
DEFENSE_MIN_SPEED = 4
DEFENSE_NEEDS_SPEED = {"evade", "counter", "clashable_counter"}
DEFENSE_NEEDS_NO_TIER2 = {"clashable_guard", "clashable_counter"}
# Most specific first: a skill carrying more than one tag counts as the first match.
_DEFENSE_ORDER = ["clashable_counter", "clashable_guard", "counter", "evade", "guard"]


def defense_label(key: str) -> str:
    return key.replace("_", " ").title()


def skill_archetype(skill) -> str | None:
    for key in _DEFENSE_ORDER:
        if key in skill.tags:
            return key
    return None


def skill_defense_problem(owned, skill) -> str | None:
    key = skill_archetype(skill)
    if key is None or key in (owned or []):
        return None
    return f"{defense_label(key)} is a defense archetype ({DEFENSE_ARCHETYPES[key]} BP) and this character does not own it."


# --- Passive Points ------------------------------------------------------------
# A slow second currency. Admins grant it only for GM-approved moments (at most 2 per
# event). It pays for identity passives (the admin sets the price) and for new unique
# statuses (1 PP, +1 if it keeps Potency, +1 if it gains Potency from Count).
PP_MAX_GRANT = 2
PP_COST_MAX = 6


def status_pp_cost(keep_potency: bool, potency_every: int) -> int:
    return 1 + int(bool(keep_potency)) + int(potency_every > 0)


def passive_points_spent(c) -> int:
    passives = sum(int(p.get("cost", 0)) for p in (getattr(c, "passives", None) or []))
    statuses = sum(int(v) for k, v in (getattr(c, "status_pp", None) or {}).items()
                   if k in (getattr(c, "custom_statuses", None) or {}))
    return passives + statuses


# --- Skill Points ------------------------------------------------------------
# The first trigger line on a skill is free. Every extra line costs this many SP.
# Supermoves are paid for in BP instead, so they cost no SP.
SP_PER_EXTRA_LINE = 1


def skill_sp_cost(skill) -> int:
    if "supermove" in skill.tags:
        return 0
    return max(0, len(skill.triggers) - 1) * SP_PER_EXTRA_LINE


def skill_points_spent(c) -> int:
    return sum(skill_sp_cost(s) for s in c.skills.values())


# --- Caps that stop one-sided builds ---------------------------------------
SINGLE_STAT_SHARE = 0.40   # no one stat above this share of the pool
PAIR_SHARE = 0.60          # Power+Speed and Power+HP together, at most this share


def level_for(total_bp: int) -> int:
    level = 1
    for lvl, needed in LEVEL_THRESHOLDS:
        if total_bp >= needed:
            level = lvl
    return level


def next_level_at(total_bp: int) -> int | None:
    for lvl, needed in LEVEL_THRESHOLDS:
        if total_bp < needed:
            return needed
    return None


# --- Costs -------------------------------------------------------------------
def hp_cost(hp: int) -> int:
    steps = max(0, math.ceil((hp - HP_BASE) / HP_STEP))
    return HP_STEP_COSTS[min(steps, len(HP_STEP_COSTS) - 1)]


def power_cost(power: int) -> int:
    steps = max(0, power - POWER_BASE)
    return POWER_STEP_COSTS[min(steps, len(POWER_STEP_COSTS) - 1)]


def speed_cost(low: int, high: int) -> int:
    avg = (low + high) / 2
    avg = min(max(avg, SPEED_LOWEST), SPEED_HIGHEST)
    floor = int(avg)
    if floor >= SPEED_HIGHEST:
        return SPEED_AVG_COSTS[SPEED_HIGHEST]
    frac = avg - floor
    base = SPEED_AVG_COSTS[floor]
    nxt = SPEED_AVG_COSTS[floor + 1]
    return math.ceil(base + frac * (nxt - base))


def stagger_cost(tiers_enabled: list[bool] | None) -> int:
    if not tiers_enabled:
        return 0
    cost = 0
    if len(tiers_enabled) > 1 and not tiers_enabled[1]:
        cost += STAGGER_REMOVE_COSTS[2]
    if len(tiers_enabled) > 2 and not tiers_enabled[2]:
        cost += STAGGER_REMOVE_COSTS[3]
    return cost


# --- Power ceiling on skills -------------------------------------------------
# A skill's Base Power and Coin Power are capped by the character's Power, and by how many coins the skill has: each coin after the first
# lowers both limits by COIN_CAP_STEP (never below MIN_COIN_CAP). Coin Power carries on to the coins after it, so without this a 4-coin
# skill did about five times what a 1-coin skill did for the same Power, and more coins was simply better.
COIN_CAP_STEP = 1
MIN_COIN_CAP = 1


def max_base_power(power: int, coins: int = 1) -> int:
    """floor(2 + 0.4 x Power) for a 1-coin skill, less COIN_CAP_STEP for each extra coin (never below MIN_COIN_CAP). Whole-number math."""
    return max(MIN_COIN_CAP, (20 + 4 * power) // 10 - COIN_CAP_STEP * (max(1, coins) - 1))


def max_coin_power(power: int, coins: int = 1) -> int:
    """floor(1 + 0.2 x Power) for a 1-coin skill, less COIN_CAP_STEP for each extra coin (never below MIN_COIN_CAP). Whole-number math."""
    return max(MIN_COIN_CAP, (5 + power) // 5 - COIN_CAP_STEP * (max(1, coins) - 1))


def skill_power_problem(power: int, base_power: int, coin_power: int, coins: int = 1) -> str | None:
    """Plain-words reason a skill's numbers are too high for this Power (and this many coins), or None.

    With `coins` left at 1 this is the plain ceiling a character's Power gives, which is what check_build holds saved skills to."""
    max_base, max_coin = max_base_power(power, coins), max_coin_power(power, coins)
    parts = []
    if base_power > max_base:
        parts.append(f"Base Power {base_power} is above the limit of {max_base}")
    if coin_power > max_coin:
        parts.append(f"Coin Power {coin_power} is above the limit of {max_coin}")
    if not parts:
        return None
    message = " and ".join(parts) + f" for Power {power}"
    if coins > 1:
        message += f" with {coins} coins (each coin after the first lowers both limits by {COIN_CAP_STEP})"
    return message + "."


def skill_coin_excess(power: int, base_power: int, coin_power: int, coins: int) -> int:
    """How far a skill is over the limits its coin count gives (0 when it fits): Base Power over plus Coin Power over."""
    return (max(0, base_power - max_base_power(power, coins))
            + max(0, coin_power - max_coin_power(power, coins)))


# --- The check ---------------------------------------------------------------
@dataclass
class BuildReport:
    level: int
    total: int
    spent: int
    breakdown: dict[str, int]
    problems: list[str] = field(default_factory=list)        # stats and budget
    skill_problems: list[str] = field(default_factory=list)  # skills over the Power ceiling
    supermove_lines: list[str] = field(default_factory=list)  # how each Supermove was priced
    effect_lines: list[str] = field(default_factory=list)     # each non-keyword effect and its price

    @property
    def ok(self) -> bool:
        return not self.problems and not self.skill_problems

    def all_problems(self) -> list[str]:
        return self.problems + self.skill_problems


def _speed_range(c) -> tuple[int, int]:
    if getattr(c, "speed_min", None) is not None:
        lo = c.speed_min
        hi = c.speed_max if c.speed_max is not None else c.speed_min
    else:
        lo = hi = c.speed
    return (lo, hi) if lo <= hi else (hi, lo)


def check_build(c) -> BuildReport:
    """Checks a Character against the rules. `c` needs hp/max_hp, power, speed, speed_min, speed_max, stagger_tiers_enabled, skills and bp_total."""
    total = c.bp_total
    hp = c.max_hp
    lo, hi = _speed_range(c)

    costs = {
        "HP": hp_cost(hp),
        "Power": power_cost(c.power),
        "Speed": speed_cost(lo, hi),
        "Stagger removal": stagger_cost(c.stagger_tiers_enabled),
    }
    # Resistances: status dials are priced, with a cap on how much BP
    # vulnerabilities can give back. The physical three are a fixed set.
    res = getattr(c, "resistances", None) or {}
    res_problems: list[str] = []
    res_paid = res_refunds = 0
    for key in STATUS_RESISTANCE_TYPES:
        value = round(float(res.get(key, 1.0)), 2)
        price = STATUS_RESIST_COSTS.get(value)
        if price is None:
            res_problems.append(f"{key.capitalize()} resistance {value}x is outside the allowed 0.5x to 2.0x.")
        elif price > 0:
            res_paid += price
        else:
            res_refunds += -price
    if res_refunds > RESIST_REFUND_CAP:
        res_problems.append(
            f"Vulnerabilities give back {res_refunds} BP, but at most {RESIST_REFUND_CAP} BP can be banked."
        )
    off = getattr(c, "offense_level", 0)
    dfn = getattr(c, "defense_level", 0)
    offdef_problems: list[str] = []
    for label, steps in (("Offense Level", off), ("Defense Level", dfn)):
        if not 0 <= steps <= OFFDEF_MAX:
            offdef_problems.append(f"{label} {steps} is outside the allowed 0 to {OFFDEF_MAX}.")
        if steps:
            costs[label] = offdef_cost(steps)
    ranks = getattr(c, "status_ranks", None) or {}
    rank_problems: list[str] = []
    owned = {k: int(v) for k, v in ranks.items() if v}
    for key, value in owned.items():
        if key not in RANKED_STATUSES:
            rank_problems.append(f"'{key}' is not a status that can have a rank.")
        elif not 1 <= value <= RANK_MAX:
            rank_problems.append(f"{key.capitalize()} rank {value} is outside the allowed 1 to {RANK_MAX}.")
    if len(owned) > RANK_MAX_STATUSES:
        rank_problems.append(f"{len(owned)} ranked statuses, but a character can own at most {RANK_MAX_STATUSES}.")
    if owned:
        costs["Status ranks"] = sum(RANK_COSTS[v] for v in owned.values() if 1 <= v <= RANK_MAX)
    owned_def = list(getattr(c, "defense_archetypes", None) or [])
    def_problems: list[str] = []
    for key in owned_def:
        if key not in DEFENSE_ARCHETYPES:
            def_problems.append(f"'{key}' is not a defense archetype.")
            continue
        if key in DEFENSE_NEEDS_SPEED and lo < DEFENSE_MIN_SPEED:
            def_problems.append(
                f"{defense_label(key)} needs a lowest Speed of {DEFENSE_MIN_SPEED} or more (it is {lo})."
            )
        if key in DEFENSE_NEEDS_NO_TIER2:
            tiers = getattr(c, "stagger_tiers_enabled", None)
            if not (tiers and len(tiers) > 1 and not tiers[1]):
                def_problems.append(f"{defense_label(key)} needs Stagger Tier 2 to be removed.")
    if len(set(owned_def)) > DEFENSE_MAX:
        def_problems.append(f"{len(set(owned_def))} defense archetypes, but a character can own at most {DEFENSE_MAX}.")
    if owned_def:
        costs["Defense archetypes"] = sum(DEFENSE_ARCHETYPES.get(k, 0) for k in set(owned_def))
    pp_used = passive_points_spent(c)
    pp_problems: list[str] = []
    if pp_used > getattr(c, "pp_total", 0):
        pp_problems.append(
            f"Passives and unique statuses use {pp_used} Passive Points but only {getattr(c, 'pp_total', 0)} are available."
        )
    nk_total, nk_lines = nonkeyword_total(c)
    nk_problems: list[str] = []
    if nk_total:
        costs["Non-keyword effects"] = nk_total
    if nk_total > NONKEYWORD_CAP:
        nk_problems.append(
            f"Non-keyword effects (Haste, Fragile, Bind and so on) cost {nk_total} BP, but a kit can spend at most {NONKEYWORD_CAP} on them."
        )
    if res_paid or res_refunds:
        costs["Resistances"] = res_paid - min(res_refunds, RESIST_REFUND_CAP)
    physical = sorted(round(float(res.get(k, 1.0)), 2) for k in DAMAGE_TYPES)
    if physical not in PHYSICAL_SETS:
        res_problems.append(
            "Slash, Blunt and Pierce must be either all 1.0x, or one each of 0.5x, 1.0x and 1.5x."
        )

    # Supermove slots (rules in game/supermoves.py). A Supermove is priced by
    # its damage tier, its timing and any drawback an admin attached to it.
    supers = [s for s in c.skills.values() if "supermove" in s.tags]
    drawbacks = getattr(c, "supermove_drawbacks", None) or {}
    super_problems: list[str] = []
    super_lines: list[str] = []
    if supers:
        super_total = 0
        any_severe = False
        for s in supers:
            tiers = list((drawbacks.get(s.name.lower()) or {}).get("tiers", []))
            any_severe = any_severe or "severe" in tiers
            price = supermove_bp(s, tiers)
            super_total += price.total
            super_lines.append(f"{s.name}: {describe_bp(s, tiers)}")
            severity = skill_severity(s)
            if severity > SUPERMOVE_MAX_SEVERITY:
                super_problems.append(
                    f"Supermove '{s.name}' hits for about {severity}, the limit is {SUPERMOVE_MAX_SEVERITY}."
                )
        costs["Supermoves"] = super_total
        if len(supers) > SUPERMOVE_MAX_SLOTS:
            super_problems.append(
                f"{len(supers)} Supermoves, but a character can have at most {SUPERMOVE_MAX_SLOTS}."
            )
        if any_severe and len(supers) > 1:
            super_problems.append("A Severe drawback uses up all the Supermove slots, so it must be the only Supermove.")

    spent = sum(costs.values())
    report = BuildReport(level=level_for(total), total=total, spent=spent, breakdown=costs)
    report.supermove_lines = super_lines
    report.effect_lines = nk_lines
    p = report.problems
    p.extend(super_problems)
    p.extend(res_problems)
    p.extend(offdef_problems)
    p.extend(rank_problems)
    p.extend(def_problems)
    p.extend(nk_problems)
    p.extend(pp_problems)

    if hp < HP_BASE:
        p.append(f"HP {hp} is below the minimum of {HP_BASE}.")
    if hp > HP_MAX:
        p.append(f"HP {hp} is above the maximum of {HP_MAX}.")
    if c.power < POWER_BASE:
        p.append(f"Power {c.power} is below the minimum of {POWER_BASE}.")
    if c.power > POWER_MAX:
        p.append(f"Power {c.power} is above the maximum of {POWER_MAX}.")
    if lo < SPEED_LOWEST or hi > SPEED_HIGHEST:
        p.append(f"Speed {lo}-{hi} is outside the allowed {SPEED_LOWEST}-{SPEED_HIGHEST}.")
    elif hi - lo < SPEED_MIN_SPREAD:
        p.append(f"Speed {lo}-{hi} is too narrow: the range must span at least {SPEED_MIN_SPREAD}.")

    single_cap = int(total * SINGLE_STAT_SHARE)
    pair_cap = int(total * PAIR_SHARE)
    speed_out_of_range = lo < SPEED_LOWEST or hi > SPEED_HIGHEST
    for label in ("HP", "Power", "Speed"):
        if label == "Speed" and speed_out_of_range:
            continue  # already reported above; the price is meaningless out of range
        if costs[label] > single_cap:
            p.append(
                f"{label} uses {costs[label]} BP, but one stat can use at most {single_cap} "
                f"(40% of {total})."
            )
    if costs["Power"] + costs["Speed"] > pair_cap:
        p.append(
            f"Power and Speed together use {costs['Power'] + costs['Speed']} BP, "
            f"the limit is {pair_cap} (60% of {total})."
        )
    if costs["Power"] + costs["HP"] > pair_cap:
        p.append(
            f"Power and HP together use {costs['Power'] + costs['HP']} BP, "
            f"the limit is {pair_cap} (60% of {total})."
        )
    if spent > total:
        p.append(f"This build costs {spent} BP but only {total} BP are available.")

    for skill in c.skills.values():
        if "supermove" in skill.tags:
            continue  # priced in BP instead, see game/supermoves.py
        # Skills saved before the per-coin limit are grandfathered: this holds every saved skill to the plain ceiling its Power gives
        # (1 coin), and the per-coin limit is checked when a skill is added or edited (see AddSkillModal and skill_coin_excess).
        issue = skill_power_problem(c.power, skill.base_power, skill.coin_power)
        if issue:
            report.skill_problems.append(f"Skill '{skill.name}': {issue}")
    return report


def build_summary(report: BuildReport) -> str:
    lines = [f"Level {report.level}, {report.spent}/{report.total} BP spent."]
    lines += [f"- {label}: {cost} BP" for label, cost in report.breakdown.items()]
    lines += [f"  - {text}" for text in report.supermove_lines]
    lines += [f"  - {text}" for text in report.effect_lines]
    return "\n".join(lines)
