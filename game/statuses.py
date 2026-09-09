from dataclasses import dataclass

# The 5 target-facing statuses that can be inflicted on an opponent and resisted. See docs/ENGINEERING_NOTES.md#statuses-comment-3.
INFLICTABLE_STATUSES = ["burn", "bleed", "tremor", "rupture", "sinking"]


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

    return StatusInstance(name=name, potency=new_potency, count=new_count)