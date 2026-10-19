"""Small helpers for how things look in Discord: bars and colors.

Plain Python, no Discord imports. cogs/battle.py uses them for the battle display
and the combat log, cogs/character.py for the character sheet.
"""

SEGMENTS = 8

HP_FULL = "🟩"
HP_MID = "🟨"
HP_LOW = "🟥"
SANITY_FULL = "🟦"
SANITY_LOW_MORALE = "🟧"
EMPTY = "⬛"

SANITY_MIN = -45
SANITY_MAX = 45
LOW_MORALE_AT = -30


def bar(fraction: float, full: str, segments: int = SEGMENTS) -> str:
    """A row of `segments` squares, `full` for the filled part and a dark square for the rest.
    Anything above zero shows at least one filled square, and anything below one shows at least one empty square."""
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * segments)
    if fraction > 0 and filled == 0:
        filled = 1
    if fraction < 1 and filled == segments:
        filled = segments - 1
    return full * filled + EMPTY * (segments - filled)


def hp_bar(hp: int, max_hp: int, segments: int = SEGMENTS) -> str:
    """Green above half HP, yellow above a quarter, red below that."""
    if max_hp <= 0 or hp <= 0:
        return EMPTY * segments
    fraction = hp / max_hp
    full = HP_FULL if fraction > 0.5 else HP_MID if fraction > 0.25 else HP_LOW
    return bar(fraction, full, segments)


def sanity_bar(sanity: int, segments: int = SEGMENTS) -> str:
    """Sanity runs from -45 to 45, so an empty bar is Panic and a full bar is the top.
    Blue normally, orange once the unit is on Low Morale (-30 or lower)."""
    fraction = (sanity - SANITY_MIN) / (SANITY_MAX - SANITY_MIN)
    full = SANITY_LOW_MORALE if sanity <= LOW_MORALE_AT else SANITY_FULL
    return bar(fraction, full, segments)


def budget_bar(spent: int, total: int, segments: int = SEGMENTS) -> str:
    """How much of a point pool is used. Red when it is over the pool."""
    if total <= 0:
        return EMPTY * segments
    return bar(spent / total, HP_LOW if spent > total else SANITY_FULL, segments)
