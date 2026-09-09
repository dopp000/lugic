DAMAGE_TYPES = ["slash", "blunt", "pierce"]

# Same 5 statuses as INFLICTABLE_STATUSES in game/statuses.py, listed here
# too so this module doesn't need to import statuses.py just for this list.
STATUS_RESISTANCE_TYPES = ["burn", "bleed", "tremor", "rupture", "sinking"]

ALL_RESISTANCE_TYPES = DAMAGE_TYPES + STATUS_RESISTANCE_TYPES

DEFAULT_RESISTANCES = {key: 0 for key in ALL_RESISTANCE_TYPES}


def apply_resistance(value: int, resistance_pct: int) -> int:
    """Reduces a value by a resistance percentage, using Limbus Company's own asymmetric formula instead of a flat linear reduction: a WEAKNESS... See docs/ENGINEERING_NOTES.md#resistances-apply-resistance for the full rationale."""
    if resistance_pct <= 0:
        multiplier = 1 - (resistance_pct / 100)
    else:
        multiplier = 1 - (resistance_pct / 200)
    return max(0, round(value * multiplier))