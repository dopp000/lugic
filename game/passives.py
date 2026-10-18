"""Identity-style passives: named, always-on rules that react on their own.

A passive is a few trigger lines kept on the Character (not in its skill list), so
it can never be declared and takes no skill slot. In battle each one becomes a
hidden Skill on `Fighter.passive_skills`, and the engine sweeps those alongside the
normal skills for the passive timings. See docs/ENGINEERING_NOTES.md#passives.
"""

from game.conditions import POWER_TIMINGS, TriggerParseError, parse_trigger_text
from game.skills import Skill

PASSIVE_KINDS = ("passive", "support")  # Support Passive is a label, it works the same
MAX_PASSIVES = 3
MAX_PASSIVE_LINES = 3

# Timings only a passive may use. They are swept over passive skills only, so on a
# normal skill they would never fire.
PASSIVE_ONLY_TIMINGS = {"self_is_hit", "self_on_stagger", "self_on_kill", "self_on_death"}

# Timings where a passive shapes every skill its owner uses (Before Use, On Use,
# Clash Start in a clash, Before Attack on an unopposed attack or a clash win).
# This is the only way a passive can add damage or Coin Power.
PASSIVE_ATTACK_TIMINGS = set(POWER_TIMINGS)

# Everything the engine actually fires for a passive.
PASSIVE_TIMINGS = PASSIVE_ONLY_TIMINGS | PASSIVE_ATTACK_TIMINGS | {
    "turn_start", "combat_start", "ally_turn_start", "ally_combat_start", "on_evade",
    "ally_on_hit", "ally_on_crit", "ally_on_kill", "ally_is_hit", "ally_on_stagger", "ally_before_hit",
}


def parse_passive(lines: list[str], custom_kinds: dict[str, str] | None = None, strict: bool = True):
    """Returns (triggers, error). Every line must parse and use a timing a passive reacts to. `strict=False` is only for re-reading a passive that was already saved, so one saved before a parser check existed still loads."""
    text = "\n".join(l.strip() for l in lines if l and l.strip())
    if not text:
        return [], "A passive needs at least one trigger line."
    try:
        triggers, flags = parse_trigger_text(text, custom_kinds or {}, strict=strict)
    except TriggerParseError as e:
        return [], f"Line `{e.line.strip()}`: {e.reason}"
    if flags:
        return [], "A passive cannot carry skill tags like [Guard] or [Supermove]."
    for t in triggers:
        if t.timing not in PASSIVE_TIMINGS:
            return [], (
                f"`{t.raw_text.strip() or t.timing}` uses a timing a passive never reacts to. "
                "Use Turn Start, Combat Start, Ally Turn Start, Ally Combat Start, On Evade, "
                "Before Use, On Use, Clash Start, Before Attack, "
                "Ally On Hit / Crit / Kill / Is Hit / On Stagger / Before Getting Hit, or Self Is Hit / Self On Stagger / Self On Kill / Self On Death."
            )
    return triggers, None


def build_passive_skill(passive: dict, custom_kinds: dict[str, str] | None = None) -> Skill | None:
    """The hidden Skill a battle fighter carries for one saved passive (None if it no longer parses)."""
    triggers, error = parse_passive(passive.get("lines", []), custom_kinds, strict=False)
    if error:
        return None
    return Skill(
        name=passive.get("name", "Passive"), base_power=0, coin_power=0, coins=1,
        triggers=triggers, tags={"passive"},
    )
