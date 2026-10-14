DAMAGE_TYPES = ["slash", "blunt", "pierce"]

# Same 5 statuses as INFLICTABLE_STATUSES in game/statuses.py, listed here
# too so this module doesn't need to import statuses.py just for this list.
STATUS_RESISTANCE_TYPES = ["burn", "bleed", "tremor", "rupture", "sinking"]

ALL_RESISTANCE_TYPES = DAMAGE_TYPES + STATUS_RESISTANCE_TYPES

# 1.0 is Normal (no change). Below 1.0 is resistant, above 1.0 is weak.
# See docs/ENGINEERING_NOTES.md#resistances-apply-resistance for why this
# replaced the old percent-based asymmetric formula.
DEFAULT_RESISTANCES = {key: 1.0 for key in ALL_RESISTANCE_TYPES}

# Every resistance value has to land on this step -- 0.75, 1.0, 1.25, 1.5,
# and so on. Keeps the whole cast on one consistent granularity instead of
# admins picking arbitrary numbers.
RESISTANCE_STEP = 0.25


def apply_resistance(value: int, multiplier: float) -> int:
    """Scales a value by a flat decimal damage multiplier: 1.0x is Normal
    (no change), lower is resistant, higher is weak -- a straight
    multiply, no asymmetric adjustment. See
    docs/ENGINEERING_NOTES.md#resistances-apply-resistance for why this
    replaced the old percent-based formula, and the result is still
    floored at 0 since resistance should never turn damage into healing.
    """
    return max(0, round(value * multiplier))


def parse_resistance_multiplier(raw: str) -> tuple[float | None, str | None]:
    """Parses one resistance value typed by a player/admin as a decimal
    damage multiplier (e.g. '1.25', '0.75'), stepped in fixed
    RESISTANCE_STEP increments. Returns (value, error) -- error is None
    on success, and value should be ignored when it isn't. Shared by
    /character resistance, /character edit, and /battle setstatus so all
    three give identical validation and error wording.
    """
    try:
        value = float(raw)
    except ValueError:
        return None, f"'{raw}' isn't a number."

    if value < 0:
        return None, f"'{raw}' can't be negative -- 0 means immune, not healing."

    steps = value / RESISTANCE_STEP
    if abs(steps - round(steps)) > 1e-6:
        return None, (
            f"'{raw}' has to land on a {RESISTANCE_STEP} step, e.g. 0.75, 1.0, 1.25, 1.5."
        )

    return round(value, 2), None


def migrate_percent_to_multiplier(resistance_pct: float) -> float:
    """One-time conversion for a character saved before this decimal-
    multiplier system existed, where resistance was a percent int under
    the old asymmetric formula (a weakness scaled 1:1, an actual
    resistance was only half as effective per point -- see
    docs/ENGINEERING_NOTES.md#resistances-apply-resistance for that old
    formula's full rationale). Converts to whichever RESISTANCE_STEP
    multiplier comes closest to producing the same damage behavior, so an
    existing character doesn't silently change out from under its owner
    just because this format switched. Some precision is unavoidably lost
    for values that don't land near a 0.25 step (e.g. old 20% resistance
    rounds up to Normal) -- that's the intended tradeoff of moving to a
    fixed step size, not a bug.
    """
    if resistance_pct <= 0:
        old_multiplier = 1 - (resistance_pct / 100)
    else:
        old_multiplier = 1 - (resistance_pct / 200)
    stepped = round(old_multiplier / RESISTANCE_STEP) * RESISTANCE_STEP
    return max(0.0, round(stepped, 2))