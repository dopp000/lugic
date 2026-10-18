from dataclasses import dataclass

# The 5 target-facing statuses that can be inflicted on an opponent and resisted. See docs/ENGINEERING_NOTES.md#statuses-comment-3.
INFLICTABLE_STATUSES = ["burn", "bleed", "tremor", "rupture", "sinking"]

# One-turn debuffs: they land immediately, last the rest of the round,
# and are wiped at turn end. No resistances apply to them. Two flavours:
# Potency-based (strength is Potency, Count is just a presence marker
# forced to 1) and Count-based (strength is Count, Potency is just 1).
# See docs/ENGINEERING_NOTES.md#statuses-one-turn-debuffs.
POTENCY_TURN_STATUSES = ["bind", "power_down"]
COUNT_TURN_STATUSES = [
    "fragile", "slash_fragility", "pierce_fragility", "blunt_fragility",
    "damage_down", "paralyze",
]
ONE_TURN_STATUSES = POTENCY_TURN_STATUSES + COUNT_TURN_STATUSES

# Count-based statuses whose Count tops out at 10 (10% each, max 100%).
PERCENT_COUNT_CAP = 10
PERCENT_COUNT_STATUSES = [
    "fragile", "slash_fragility", "pierce_fragility", "blunt_fragility", "damage_down",
]

# Longer-lived debuffs added alongside: Poison (Count, fixed damage at
# turn end then halves) and Immobilized (skips acting, see
# Fighter.immobilized_blocked).
OTHER_DEBUFF_STATUSES = ["poison", "immobilized"]

# Everything /battle setstatus can place on a fighter.
SETTABLE_STATUSES = INFLICTABLE_STATUSES + ONE_TURN_STATUSES + OTHER_DEBUFF_STATUSES

PERCENT_PER_COUNT = 0.10


def _count_of(statuses: dict, key: str) -> int:
    inst = statuses.get(key)
    if inst is None or inst.count <= 0:
        return 0
    return min(PERCENT_COUNT_CAP, inst.count)


def fragility_bonus(statuses: dict, damage_type: str) -> float:
    """Extra damage fraction a target takes from Fragile plus the matching type Fragility (0.0 if neither is active). 10% per Count, each capped at +100%."""
    return (_count_of(statuses, "fragile") + _count_of(statuses, f"{damage_type}_fragility")) * PERCENT_PER_COUNT


def damage_down_penalty(statuses: dict) -> float:
    """Fraction of damage an attacker with Damage Down loses (10% per Count, max 100%)."""
    return _count_of(statuses, "damage_down") * PERCENT_PER_COUNT


# Maps a status's magnitude (potency * count) to a Hint tier (1-3, see
# Trigger.hint_tier in game/conditions.py). Shared by two callers that
# both need the exact same scale: cogs/battle.py's _status_hint_tier
# (for a fighter's currently ACTIVE status) and parse_trigger_text in
# game/conditions.py (to auto-set a newly written Trigger's own tier
# from its inflict_status effect, instead of requiring a hand-typed
# "(Hint N)"). Lives here, not in either caller, since neither module
# can import from the other without a cycle.
STATUS_HINT_THRESHOLDS = [(15, 3), (6, 2), (1, 1)]


@dataclass
class StatusInstance:
    """One status effect currently active on a fighter. See docs/ENGINEERING_NOTES.md#statuses-statusinstance for the full rationale."""
    name: str
    potency: int
    count: int


def decay_after_trigger(current: StatusInstance) -> StatusInstance:
    """Called when a status's trigger condition fires (e.g. See docs/ENGINEERING_NOTES.md#statuses-decay-after-trigger for the full rationale."""
    new_count = current.count - 1
    if new_count <= 0:
        return StatusInstance(name=current.name, potency=0, count=0)
    return StatusInstance(name=current.name, potency=current.potency, count=new_count)


def magnitude_to_hint_tier(magnitude: int, status_name: str | None = None) -> int:
    """Maps a potency*count magnitude to a Hint tier via STATUS_HINT_THRESHOLDS above (0 if below even the lowest threshold). Rupture gets a flat +1 on top of its magnitude tier, capped at 3, and is floored at 2 even from a 0 magnitude -- its automatic on-hit trigger makes any active Rupture a live threat regardless of size. See docs/ENGINEERING_NOTES.md#statuses-magnitude-to-hint-tier for the full rationale."""
    tier = 0
    for threshold, t in STATUS_HINT_THRESHOLDS:
        if magnitude >= threshold:
            tier = t
            break
    if status_name == "rupture":
        tier = min(3, tier + 1) if tier > 0 else 2
    return tier


def apply_status(current: StatusInstance | None, name: str, added_potency: int, added_count: int) -> StatusInstance:
    """Layers a new application on top of whatever is currently there. See docs/ENGINEERING_NOTES.md#statuses-apply-status for the full rationale.

    Potency is capped at 99 for every status, matching canon Limbus's
    own cap. Charge additionally caps its own Count at 20 (its own
    distinct resource ceiling, separate from the general potency cap).
    """
    base_potency = current.potency if current else 0
    base_count = current.count if current else 0

    new_count = base_count + added_count
    new_potency = base_potency + added_potency
    if new_count > 0 and new_potency <= 0:
        new_potency = 1

    new_potency = min(new_potency, 99)
    if name == "charge":
        new_count = min(new_count, 20)
    if name in POTENCY_TURN_STATUSES:
        new_count = 1 if new_count > 0 else 0
    if name in PERCENT_COUNT_STATUSES:
        new_count = min(new_count, PERCENT_COUNT_CAP)

    return StatusInstance(name=name, potency=new_potency, count=new_count)