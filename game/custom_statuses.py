"""Player-made statuses ("custom resources" like Pulsation). See docs/ENGINEERING_NOTES.md#custom-statuses."""
from dataclasses import dataclass

MAX_CUSTOM_STATUSES_PER_CHARACTER = 10
MAX_NAME_LENGTH = 24

KINDS = ("buff", "debuff")  # buff: gained by its owner. debuff: inflicted on enemies.
LIFETIMES = ("persistent", "turn")  # turn: wiped at every turn end, like Bind.

# Names a custom status may not take, so it can never shadow a built-in.
RESERVED_NAMES = {
    "burn", "bleed", "tremor", "rupture", "sinking", "poise", "charge",
    "bind", "fragile", "slash_fragility", "pierce_fragility", "blunt_fragility",
    "power_down", "damage_down", "paralyze", "poison", "immobilized",
    "shield", "stagger", "guard", "evasion", "evade", "counter", "sanity",
    "speed", "hp", "power", "tremor_burst", "panic", "low_morale", "none",
}


def slugify(name: str) -> str:
    """'Crimson Pulse' -> 'crimson_pulse'. The same form trigger lines are normalized to."""
    return "_".join(name.strip().lower().split())


def validate_name(name: str) -> str | None:
    """Returns an error message, or None if the name is usable."""
    key = slugify(name)
    if not key:
        return "The status needs a name."
    if len(name.strip()) > MAX_NAME_LENGTH:
        return f"Keep the name to {MAX_NAME_LENGTH} characters or fewer."
    if not all(ch.isalpha() or ch == " " for ch in name.strip()):
        return "Use letters and spaces only in a status name."
    if key in RESERVED_NAMES:
        return f"'{name.strip()}' is already a built-in status name. Pick another."
    return None


@dataclass
class CustomStatus:
    name: str  # display name, e.g. "Pulsation"
    key: str  # slugified, what triggers and fighters use
    kind: str = "buff"
    lifetime: str = "persistent"
    emoji_id: int | None = None
    animated: bool = False
    description: str = ""
    # Stays on the fighter at 0 Count if it has Potency (a permanent buff).
    keep_potency: bool = False
    # Every this many Count spent in total (per battle) adds +1 Potency.
    # 0 turns it off.
    potency_every: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name, "key": self.key, "kind": self.kind,
            "lifetime": self.lifetime, "emoji_id": self.emoji_id,
            "animated": self.animated, "description": self.description,
            "keep_potency": self.keep_potency, "potency_every": self.potency_every,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CustomStatus":
        return cls(
            name=data["name"], key=data.get("key") or slugify(data["name"]),
            kind=data.get("kind", "buff"), lifetime=data.get("lifetime", "persistent"),
            emoji_id=data.get("emoji_id"), animated=bool(data.get("animated", False)),
            description=data.get("description", ""),
            keep_potency=bool(data.get("keep_potency", False)),
            potency_every=int(data.get("potency_every", 0)),
        )
