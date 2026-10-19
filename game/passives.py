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


def followup_problem(character, parsed) -> str | None:
    """None when every follow-up line in `parsed` names a normal attack the character owns, else a message saying what is wrong."""
    for t in parsed:
        if t.effect_type != "followup_attack":
            continue
        owned = character.skills.get(t.status_name)
        if owned is None:
            return (
                f"{character.name} has no skill called `{t.status_name}` to use as a follow-up attack. "
                "Add it first (the name has to match)."
            )
        if owned.tags & {"guard", "evade", "counter", "clashable_guard", "clashable_counter", "supermove"}:
            return f"`{owned.name}` is a defense skill or a Supermove, a follow-up attack has to be a normal attack."
    return None


def tag_warnings(triggers, known_tags) -> list[str]:
    """Lines about tag conditions that name a tag nobody has. `known_tags` is a set of normalized tags (see norm_tag) from every saved character. A typo never matches and gives no error, so this is how a player finds out."""
    import difflib
    warnings: list[str] = []
    seen: set[str] = set()
    for t in triggers:
        c = t.condition
        if c.type not in ("side_tag_count", "target_tag") or c.status_name in seen:
            continue
        seen.add(c.status_name)
        if c.status_name in known_tags:
            continue
        close = difflib.get_close_matches(c.status_name, sorted(known_tags), n=1, cutoff=0.6)
        hint = f" Did you mean `{close[0]}`?" if close else ""
        warnings.append(f"No character has the tag `{c.status_name}`, so that line will never fire.{hint}")
    return warnings
