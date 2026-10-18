"""Build Points (BP): the one budget that pays for a character's stats.

Pure rules, no Discord and no battle state, so everything here can be
tested on its own. Every number a GM might want to tune is a constant at
the top. The design comes from the original wiki guide: a single pool,
level as a label on the pool's size, and caps that stop one-stat builds.

What this module covers so far: HP, Power, Speed, Stagger tier removal,
and the Power ceiling on skills. Status ranks, status resistance dials,
defense archetypes, Supermove slots and Passive Points come later.
"""

from dataclasses import dataclass, field
import math

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
POWER_MAX = 14
POWER_BP_PER_POINT = 1  # 1 BP per point above the base: 8 BP reaches the cap of 14

# --- Speed -------------------------------------------------------------------
SPEED_LOWEST = 1
SPEED_HIGHEST = 8
SPEED_MIN_SPREAD = 2  # max - min must be at least this
# Total BP for a range whose AVERAGE is this value (an average between two
# whole numbers is priced in between, rounded up).
SPEED_AVG_COSTS = {1: 0, 2: 1, 3: 2, 4: 4, 5: 6, 6: 9, 7: 12, 8: 16}

# --- Stagger -----------------------------------------------------------------
STAGGER_REMOVE_COSTS = {2: 5, 3: 7}

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
    return max(0, power - POWER_BASE) * POWER_BP_PER_POINT


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
def max_base_power(power: int) -> int:
    """floor(2 + 0.4 x Power), in whole-number math."""
    return (20 + 4 * power) // 10


def max_coin_power(power: int) -> int:
    """floor(1 + 0.2 x Power), in whole-number math."""
    return (5 + power) // 5


def skill_power_problem(power: int, base_power: int, coin_power: int) -> str | None:
    """Plain-words reason a skill's numbers are too high for this Power, or None."""
    max_base, max_coin = max_base_power(power), max_coin_power(power)
    parts = []
    if base_power > max_base:
        parts.append(f"Base Power {base_power} is above the limit of {max_base}")
    if coin_power > max_coin:
        parts.append(f"Coin Power {coin_power} is above the limit of {max_coin}")
    if not parts:
        return None
    return " and ".join(parts) + f" for Power {power}."


# --- The check ---------------------------------------------------------------
@dataclass
class BuildReport:
    level: int
    total: int
    spent: int
    breakdown: dict[str, int]
    problems: list[str] = field(default_factory=list)        # stats and budget
    skill_problems: list[str] = field(default_factory=list)  # skills over the Power ceiling

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
    spent = sum(costs.values())
    report = BuildReport(level=level_for(total), total=total, spent=spent, breakdown=costs)
    p = report.problems

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
        issue = skill_power_problem(c.power, skill.base_power, skill.coin_power)
        if issue:
            report.skill_problems.append(f"Skill '{skill.name}': {issue}")
    return report


def build_summary(report: BuildReport) -> str:
    lines = [f"Level {report.level}, {report.spent}/{report.total} BP spent."]
    lines += [f"- {label}: {cost} BP" for label, cost in report.breakdown.items()]
    return "\n".join(lines)
