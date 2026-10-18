"""
Plain-language helpers for skill authors: describe a parsed trigger in
words (for the skill preview), explain a failed trigger line with a
suggested fix, and summarize a skill's damage range. No Discord code.
See docs/ENGINEERING_NOTES.md#friendlier-errors-and-previews.
"""

import difflib
import re

from game.conditions import (
    ALL_TIMING_LOOKUP,
    SELF_BUFF_STATUSES,
    SKILL_FLAG_TAGS,
    TARGET_STATUSES,
    Trigger,
    TriggerParseError,
)
from game.emojis import status_label

_TIMING_TITLES = {internal: key.title() for key, internal in ALL_TIMING_LOOKUP.items()}

# Valid effect shapes, shown when a line's effect isn't understood and used
# to find the closest one.
EFFECT_TEMPLATES = [
    "Deal +N damage",
    "Coin Power +N",
    "Gain N Sanity",
    "Heal N HP",
    "Heal N% max HP",
    "Inflict +N Burn Count",
    "Inflict N Burn",
    "Inflict Burn Potency N, Count N",
    "Gain +N Charge Count",
    "Gain N Poise",
    "Gain Charge Potency N, Count N",
    "Remove Bleed",
    "Consume N Charge: Coin Power +N",
    "Coin Power +N per N Charge Potency",
    "Deal +N damage per N Charge Count",
]

CONDITION_EXAMPLES = [
    "If this unit's Speed is faster than the target's,",
    "If the target has any Burn,",
    "If the target has 3+ Burn,",
    "If this unit's Sanity is 20+,",
    "At 10+ Speed,",
    "At 5+ Poise,",
    "If this unit is Low Morale,",
]


def _name(key: str, names: dict[str, str] | None) -> str:
    if names and key in names:
        return names[key]
    return status_label(key)


def _describe_condition(t: Trigger, names) -> str:
    c = t.condition
    s = _name(c.status_name, names) if c.status_name else ""
    if c.type == "target_status":
        if c.min_count:
            return f"the target has {c.min_count}+ {s} Count"
        if c.min_potency > 1:
            return f"the target has {c.min_potency}+ {s} Potency"
        return f"the target has any {s}"
    if c.type == "caster_status":
        if c.min_count:
            return f"you have {c.min_count}+ {s} Count"
        if c.min_potency:
            return f"you have {c.min_potency}+ {s} Potency"
        return f"you have {s}"
    if c.type == "caster_sanity":
        return f"your Sanity is {c.value}+"
    if c.type == "caster_speed_at_least":
        return f"your slot Speed is {c.value}+"
    if c.type == "speed_faster":
        return "your slot is faster than the target's"
    if c.type == "first_hit_of_round":
        return "it is the first hit of the round"
    if c.type in ("caster_morale", "target_morale"):
        who = "you are" if c.type == "caster_morale" else "the target is"
        return f"{who} {'Panicked' if c.status_name == 'panic' else 'Low Morale'}"
    if c.type == "target_hp_pct":
        return f"target HP is {c.value}% or {'more' if c.comparison == 'gte' else 'less'}"
    return ""


def _describe_effect(t: Trigger, names) -> str:
    s = _name(t.status_name, names) if t.status_name else ""
    scale = ""
    if t.scale_status:
        scale = f" per {t.scale_per} {_name(t.scale_status, names)} {t.scale_basis.capitalize()}"
    if t.effect_type == "bonus_power":
        text = f"Deal +{t.effect_value} damage{scale}"
    elif t.effect_type == "bonus_coin_power":
        text = f"Coin Power +{t.effect_value}{scale}"
    elif t.effect_type == "inflict_status":
        text = f"Inflict {s} (Potency {t.effect_value}, Count {t.status_count})"
    elif t.effect_type == "gain_status":
        if t.effect_value:
            text = f"Gain {s} (Potency {t.effect_value}, Count {t.status_count})"
        else:
            text = f"Gain +{t.status_count} {s} Count"
    elif t.effect_type == "sanity_gain":
        text = f"Gain {t.effect_value} Sanity"
    elif t.effect_type == "heal":
        text = f"Heal {t.effect_value}{'% max HP' if t.heal_percent else ' HP'}"
    elif t.effect_type == "remove_status":
        text = f"Remove {s}"
    else:
        text = t.effect_type
    if t.consume_status:
        text = f"spend {t.consume_amount} {_name(t.consume_status, names)}, then {text}"
    if t.to_ally:
        text += " (on the ally)"
    return text


def describe_trigger(t: Trigger, names: dict[str, str] | None = None) -> str:
    """'Coin 1, On Hit: if the target has 3+ Burn Count -> Deal +3 damage'."""
    when = _TIMING_TITLES.get(t.timing, t.timing)
    if t.coin_index:
        when = f"Coin {t.coin_index}, {when}"
    cond = _describe_condition(t, names)
    effect = _describe_effect(t, names)
    return f"{when}: " + (f"if {cond} -> " if cond else "") + effect


def damage_range(skill) -> tuple[int, int, int]:
    """(min, expected, max) total damage of one use, flat Deal / Coin Power bonuses counted as always firing."""
    n = skill.coins
    low = high = 0.0
    avg = 0.0
    for i in range(1, n + 1):
        low += skill.base_power
        high += skill.base_power + skill.coin_power * i
        avg += skill.base_power + skill.coin_power * i / 2
    for t in skill.triggers:
        first = t.coin_index or 1
        left = max(0, n - first + 1)
        if t.effect_type == "bonus_power":
            extra = t.effect_value * left
            low, avg, high = low + extra, avg + extra, high + extra
        elif t.effect_type == "bonus_coin_power":
            high += t.effect_value * left * (left + 1) / 2
            avg += t.effect_value * 0.5 * left * (left + 1) / 2
    return round(low), round(avg), round(high)


def _closest(word: str, options, n: int = 2) -> list[str]:
    return difflib.get_close_matches(word.lower(), [o.lower() for o in options], n=n, cutoff=0.55)


def friendly_error_text(full_text: str, error: TriggerParseError, custom_keys=None) -> str:
    """Turns a parse error into: which line, what is wrong, and the closest valid thing."""
    line = error.line.strip()
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]
    number = next((i for i, l in enumerate(lines, start=1) if line.lower() in l.lower()), None)
    if number:
        line = lines[number - 1]
    head = f"Trigger line {number}" if number else "Trigger line"
    out = [f"{head}: `{line}`", error.reason]
    reason = error.reason.lower()
    custom = list(custom_keys or [])

    if "recognized timing tag" in reason:
        m = re.search(r"\[([^\]]+)\]", error.reason)
        if m:
            options = list(ALL_TIMING_LOOKUP) + list(SKILL_FLAG_TAGS)
            close = _closest(m.group(1), options)
            if close:
                out.append("Did you mean " + " or ".join(f"[{c.title()}]" for c in close) + "?")
    elif "bracket timing tag" in reason:
        out.append("Example: `[On Use] Coin Power +1` or `:Coin1: [On Hit] Inflict +2 Burn Count`")
    elif "isn't a status" in reason or "isn't a self-buff" in reason:
        m = re.match(r"'([^']+)'", error.reason)
        if m:
            pool = TARGET_STATUSES + SELF_BUFF_STATUSES + custom
            close = _closest(m.group(1).replace(" ", "_"), pool)
            if close:
                out.append("Did you mean " + " or ".join(status_label(c) for c in close) + "?")
    elif "effect phrase not recognized" in reason:
        rest = re.sub(r"^\s*(?::coin\d+:\s*)?\[[^\]]*\]\s*", "", line)
        if re.match(r"(?i)(if|at|when)\b", rest) and "," not in rest:
            out.append("A condition needs a comma after it, like `If the target has any Burn, Deal +3 damage`.")
        elif re.match(r"(?i)(if|at)\b", rest):
            out.append("That condition wasn't understood. Supported ones: " + "; ".join(f"`{c}`" for c in CONDITION_EXAMPLES[:5]))
        shape = re.sub(r"\d+", "N", rest.split(",")[-1].strip().lower())
        close = difflib.get_close_matches(shape, [t.lower() for t in EFFECT_TEMPLATES], n=1, cutoff=0.5)
        if close:
            best = next(t for t in EFFECT_TEMPLATES if t.lower() == close[0])
            out.append(f"Closest valid effect: `{best}`")
        else:
            out.append("Valid effects look like: " + "; ".join(f"`{t}`" for t in EFFECT_TEMPLATES[:6]))
    out.append("Full list: /help, then Trigger Syntax.")
    return "\n".join(out)
