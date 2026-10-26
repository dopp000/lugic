"""The choices behind the rule-line picker on the character builder site.

A rule line is `[When] If <condition>, <effect>`, plus optional extras such as "(once per turn)".
The site lets a player pick each part from a list instead of typing it. This module is the list:
which timings, conditions and effects exist, what numbers they take, and which pairs work together.
It is plain data, written into docs/builder/rules.js by game/builder_rules.py.

Nothing here parses a line. The bot's parser (game/conditions.py) stays the only judge:
tests/test_line_picker.py builds lines from this data with docs/builder/lines.js and feeds every one
to the bot's own parser. A timing, condition or effect that the parser does not accept (or that it
accepts when the site says it should not) makes that test fail, so the picker cannot drift away from
the bot.
"""

from game.conditions import (
    ALL_TIMING_LOOKUP, FOLLOWUP_TIMINGS, PER_COIN_TIMINGS, POWER_TIMINGS, SELF_BUFF_STATUSES, TARGET_STATUSES,
    _SELECTOR_PHRASES,
)
from game.passives import PASSIVE_ONLY_TIMINGS, PASSIVE_TIMINGS

GROUPS = [
    {"id": "use", "label": "When the skill is used", "help": "Fires once while the skill is being used or clashed."},
    {"id": "coin", "label": "On one coin", "help": "Fires for one coin of the skill. Pick which coin."},
    {"id": "turn", "label": "Start and end of turns", "help": "Fires on its own, whatever the skill is doing."},
    {"id": "result", "label": "After a hit", "help": "Fires because of how a hit went."},
    {"id": "ally", "label": "When an ally acts or is hit", "help": "Reacts to something that happens to an ally."},
    {"id": "self", "label": "When this unit is hit (passives only)", "help": "Passives only: reacts to something that happens to its owner."},
]

# key = the text inside the brackets, as the bot spells it. help = one line on when it fires.
_TIMING_TEXT = [
    ("use", "Before Use", "Before the skill starts. Power effects (Deal, Coin Power) work here."),
    ("use", "On Use", "As the skill starts. Power effects (Deal, Coin Power) work here."),
    ("use", "Clash Start", "When a clash with this skill begins. Power effects work here."),
    ("use", "Clash Win", "When this skill wins its clash."),
    ("use", "Clash Lose", "When this skill loses its clash."),
    ("use", "Before Attack", "Before the coins land: after winning a clash, or when nobody clashes. Power effects work here."),
    ("use", "On Unopposed Attack", "When nobody clashes with this skill."),
    ("use", "Attack End", "After every coin has been resolved."),
    ("coin", "Coin Start", "When that coin is about to be tossed. Only Deal +N and Coin Power +N work here."),
    ("coin", "On Hit", "When that coin hits."),
    ("coin", "Heads Hit", "When that coin lands Heads and hits."),
    ("coin", "Tails Hit", "When that coin lands Tails and hits."),
    ("coin", "Hit After Clash Win", "When that coin hits after the clash was won."),
    ("coin", "Current Coin Attack End", "When that coin has finished resolving."),
    ("coin", "Heads Attack End", "When that coin has finished resolving and landed Heads."),
    ("coin", "Tails Attack End", "When that coin has finished resolving and landed Tails."),
    ("coin", "On Crit", "When that coin crits (the unit is holding Poise)."),
    ("coin", "On Crit - Heads Hit", "When that coin crits and lands Heads."),
    ("coin", "On Crit - Tails Hit", "When that coin crits and lands Tails."),
    ("turn", "Combat Start", "Once, when the battle begins."),
    ("turn", "Turn Start", "At the start of every turn."),
    ("turn", "Turn End", "At the end of every turn."),
    ("result", "On Evade", "When this unit evades an attack."),
    ("result", "Before Getting Hit", "Just before this unit is hit."),
    ("result", "On Stagger", "When this hit leaves the target Staggered."),
    ("result", "On Kill", "When this hit defeats the target. (The game calls it On Target Kill.)"),
    ("result", "On Crit Kill", "When a crit defeats the target."),
    ("result", "Failed Hit", "When the hit failed: it was evaded or did 0 damage."),
    ("result", "Failed Kill", "When the hit landed but the target is still standing."),
    ("result", "Hit After Clash Lose", "For the side that lost the clash, when its hit lands."),
    ("result", "Tremor Burst", "Against a target with Tremor: its Stagger payoff. Use it with a Tremor condition."),
    ("ally", "Ally On Hit", "When an ally hits an enemy."),
    ("ally", "Ally On Crit", "When an ally crits."),
    ("ally", "Ally On Kill", "When an ally defeats an enemy."),
    ("ally", "Ally Is Hit", "When an ally is hit."),
    ("ally", "Ally Before Getting Hit", "Just before an unopposed attack on an ally lands."),
    ("ally", "Ally On Stagger", "When an ally is Staggered."),
    ("ally", "Ally Turn Start", "At the start of every turn, for an ally."),
    ("ally", "Ally Combat Start", "When the battle begins, for an ally."),
    ("self", "Self Is Hit", "When this unit takes a landed hit."),
    ("self", "Self On Stagger", "When this unit becomes Staggered."),
    ("self", "Self On Kill", "When this unit defeats someone."),
    ("self", "Self On Death", "When this unit is defeated."),
]


def title(key: str) -> str:
    return key.replace("_", " ").title()


def timings() -> list[dict]:
    out = []
    for group, key, help_text in _TIMING_TEXT:
        internal = ALL_TIMING_LOOKUP[key.lower()]
        out.append({
            "key": key, "group": group, "help": help_text,
            "perCoin": key.lower() in PER_COIN_TIMINGS,
            "skill": internal not in PASSIVE_ONLY_TIMINGS,
            "passive": internal in PASSIVE_TIMINGS,
            "power": internal in POWER_TIMINGS,
            "coinStart": internal == "coin_start",
            "followup": internal in FOLLOWUP_TIMINGS,
            "assist": internal == "ally_before_hit",
        })
    return out


# Single-word statuses only: "If the target has any <status>," reads one word.
CONDITION_TARGET_STATUSES = [title(s) for s in TARGET_STATUSES if "_" not in s]
TARGET_STATUS_NAMES = [title(s) for s in TARGET_STATUSES]
SELF_STATUS_NAMES = [title(s) for s in SELF_BUFF_STATUSES]
REMOVABLE_NAMES = TARGET_STATUS_NAMES + [s for s in SELF_STATUS_NAMES if s not in TARGET_STATUS_NAMES]
def _selector_label(phrase: str) -> str:
    """'slowest speed' -> 'Slowest Speed', 'lowest hp percent' -> 'Lowest HP Percent' (whole words only, so Speed never becomes SPeed)."""
    return " ".join({"hp": "HP", "sp": "SP"}.get(word, word.capitalize()) for word in phrase.split())


SELECTOR_NAMES = [_selector_label(phrase) for phrase in _SELECTOR_PHRASES]

BASIS = [{"value": "Count", "label": "Count"}, {"value": "Potency", "label": "Potency"}]


def _num(name, label, low, high, default):
    return {"name": name, "kind": "number", "label": label, "min": low, "max": high, "default": default}


def _choice(name, label, options, default=None):
    opts = [o if isinstance(o, dict) else {"value": o, "label": o} for o in options]
    return {"name": name, "kind": "choice", "label": label, "options": opts, "default": opts[0]["value"] if default is None else default}


def _status(name, label, names):
    return {"name": name, "kind": "choice", "label": label, "options": [{"value": n, "label": n} for n in names], "default": names[0]}


WHO = [{"value": "this unit's", "label": "this unit's"}, {"value": "the target's", "label": "the target's"}]


def conditions() -> list[dict]:
    """`text` is the words that go between the bracket tag and the effect. It ends in a comma."""
    return [
        {"id": "target_side", "label": "The target is an ally or an enemy", "text": "If the target is an {side},",
         "params": [_choice("side", "Target", [{"value": "enemy", "label": "an enemy"}, {"value": "ally", "label": "an ally"}])]},
        {"id": "speed_faster", "label": "This unit is faster than the target", "text": "If this unit's Speed is faster than the target's,", "params": []},
        {"id": "speed_diff", "label": "Speed is higher or lower by N", "text": "If this unit's Speed is {n}+ {dir} than the target's,",
         "params": [_num("n", "By at least", 1, 20, 2), _choice("dir", "This unit is", ["higher", "lower"])]},
        {"id": "speed_at", "label": "This unit's Speed is N or more", "text": "At {n}+ Speed,", "params": [_num("n", "Speed", 1, 99, 5)]},
        {"id": "target_status_any", "label": "The target has a status", "text": "If the target has any {status},",
         "params": [_status("status", "Status", CONDITION_TARGET_STATUSES)]},
        {"id": "target_status_n", "label": "The target has N or more of a status", "text": "If the target has {n}+ {status} {basis},",
         "params": [_num("n", "At least", 1, 99, 3), _status("status", "Status", CONDITION_TARGET_STATUSES), _choice("basis", "Counting", BASIS)]},
        {"id": "self_status_n", "label": "This unit has N or more of a status", "text": "At {n}+ {status} {basis},",
         "params": [_num("n", "At least", 1, 99, 5), _status("status", "Status", SELF_STATUS_NAMES), _choice("basis", "Counting", BASIS)]},
        {"id": "negatives", "label": "The target has N different negative effects", "text": "If the target has {n}+ negative effects,",
         "params": [_num("n", "At least", 1, 15, 3)]},
        {"id": "hp", "label": "HP is above or below a percent", "text": "If {who} HP is {n}% or {dir},",
         "params": [_choice("who", "Whose", WHO), _num("n", "Percent", 0, 100, 50), _choice("dir", "Is", ["less", "more"])]},
        {"id": "sanity", "label": "Sanity is above or below a number", "text": "If {who} Sanity is {n}{dir},",
         "params": [_choice("who", "Whose", WHO), _num("n", "Sanity", -45, 45, 20),
                    _choice("dir", "Is", [{"value": "+", "label": "this or higher"}, {"value": " or less", "label": "this or lower"}])]},
        {"id": "morale", "label": "Low Morale or Panic", "text": "If {who} is {state},",
         "params": [_choice("who", "Who", [{"value": "this unit", "label": "this unit"}, {"value": "the target", "label": "the target"}]),
                    _choice("state", "State", ["Low Morale", "Panic"])]},
        {"id": "affinity", "label": "The target is Weak or Fatal to this skill's damage type", "text": "If the target is {kind} to this skill's damage type,",
         "params": [_choice("kind", "Is", ["Weak", "Fatal"])]},
        {"id": "defending", "label": "The target is using a Defense Skill", "text": "If the target is using a Defense Skill,", "params": []},
        {"id": "target_tag", "label": "The target has a group tag", "text": "If the target has the {tag} tag,",
         "params": [{"name": "tag", "kind": "tag", "label": "Tag", "default": ""}]},
        {"id": "side_tag", "label": "N units on your side have a group tag", "text": "If {n}+ units on your side have the {tag} tag,",
         "params": [_num("n", "At least", 1, 20, 2), {"name": "tag", "kind": "tag", "label": "Tag", "default": ""}]},
        {"id": "adjacent", "label": "Allies are next to this unit", "text": "If {n} adjacent,",
         "params": [_choice("n", "Allies", [{"value": "an ally is", "label": "An ally is"}, {"value": "2+ allies are", "label": "2 allies are"}])]},
        {"id": "defeated", "label": "N allies are defeated", "text": "If {n}+ allies are defeated,", "params": [_num("n", "At least", 1, 20, 1)]},
        {"id": "ally_rank", "label": "The ally has the fastest, lowest... (ally timings)", "text": "If the ally has the {selector},",
         "params": [_choice("selector", "Ally with the", SELECTOR_NAMES)]},
    ]


def _power(effect: dict) -> dict:
    return {**effect, "power": True}


def effects() -> list[dict]:
    """`text` is the effect words. Flags: power (changes the hit, only works before the coins land), flat (also works at Coin Start),
    ally (can be aimed at an ally), post (lands after the hit, so it can wait until next turn), followup, assist, needsLimit."""
    return [
        _power({"id": "dmg_pct", "group": "Damage and power", "label": "Deal +N% damage", "text": "Deal +{n}% damage", "params": [_num("n", "Percent", 1, 50, 10)]}),
        _power({"id": "dmg_flat", "group": "Damage and power", "label": "Deal +N damage", "text": "Deal +{n} damage", "flat": True, "params": [_num("n", "Damage", 1, 99, 3)]}),
        _power({"id": "coin_power", "group": "Damage and power", "label": "Coin Power +N", "text": "Coin Power +{n}", "flat": True, "params": [_num("n", "Coin Power", 1, 9, 1)]}),
        _power({"id": "clash_power", "group": "Damage and power", "label": "Clash Power +N", "text": "Clash Power +{n}", "params": [_num("n", "Clash Power", 1, 9, 1)]}),
        _power({"id": "final_power", "group": "Damage and power", "label": "Final Power +N", "text": "Final Power +{n}", "params": [_num("n", "Final Power", 1, 9, 1)]}),
        _power({"id": "dmg_pct_per", "group": "Damage and power", "label": "Deal +N% damage for every few of a target's status",
                "text": "Deal +{n}% damage per {per} {status} {basis} on the target (max {max}%)",
                "params": [_num("n", "Percent each", 1, 10, 5), _num("per", "For every", 1, 99, 6), _status("status", "Target status", TARGET_STATUS_NAMES),
                           _choice("basis", "Counting", BASIS), _num("max", "Cap (%)", 1, 50, 15)]}),
        _power({"id": "coin_power_per", "flat": True, "group": "Damage and power", "label": "Coin Power +N for every few of your own status",
                "text": "Coin Power +{n} per {per} {status} {basis} (max {max})",
                "params": [_num("n", "Coin Power each", 1, 5, 1), _num("per", "For every", 1, 99, 5), _status("status", "Your status", SELF_STATUS_NAMES),
                           _choice("basis", "Counting", BASIS), _num("max", "Cap", 1, 9, 3)]}),
        _power({"id": "dmg_flat_per", "flat": True, "group": "Damage and power", "label": "Deal +N damage for every few of your own status",
                "text": "Deal +{n} damage per {per} {status} {basis} (max {max})",
                "params": [_num("n", "Damage each", 1, 10, 1), _num("per", "For every", 1, 99, 5), _status("status", "Your status", SELF_STATUS_NAMES),
                           _choice("basis", "Counting", BASIS), _num("max", "Cap", 1, 99, 5)]}),
        _power({"id": "consume", "flat": True, "group": "Damage and power", "label": "Spend some of your status for Coin Power",
                "text": "Consume {m} {status}: Coin Power +{n}",
                "params": [_num("m", "Spend", 1, 99, 2), _status("status", "Your status", SELF_STATUS_NAMES), _num("n", "Coin Power", 1, 9, 1)]}),
        {"id": "inflict_count", "group": "Statuses", "label": "Inflict +N Count of a status", "text": "Inflict +{n} {status} Count", "post": True,
         "params": [_num("n", "Count", 1, 99, 2), _status("status", "Status", TARGET_STATUS_NAMES)]},
        {"id": "inflict_bare", "group": "Statuses", "label": "Inflict N of a status", "text": "Inflict {n} {status}", "post": True,
         "params": [_num("n", "Amount", 1, 99, 2), _status("status", "Status", TARGET_STATUS_NAMES)]},
        {"id": "inflict_both", "group": "Statuses", "label": "Inflict a status with Potency and Count", "text": "Inflict {status} Potency {p}, Count {n}", "post": True,
         "params": [_status("status", "Status", TARGET_STATUS_NAMES), _num("p", "Potency", 1, 99, 2), _num("n", "Count", 1, 99, 2)]},
        {"id": "gain_count", "group": "Statuses", "label": "Gain +N Count of a status", "text": "Gain +{n} {status} Count", "post": True, "ally": True,
         "params": [_num("n", "Count", 1, 99, 2), _status("status", "Status", SELF_STATUS_NAMES)]},
        {"id": "gain_bare", "group": "Statuses", "label": "Gain N of a status", "text": "Gain {n} {status}", "post": True, "ally": True,
         "params": [_num("n", "Amount", 1, 99, 2), _status("status", "Status", SELF_STATUS_NAMES)]},
        {"id": "gain_both", "group": "Statuses", "label": "Gain a status with Potency and Count", "text": "Gain {status} Potency {p}, Count {n}", "post": True, "ally": True,
         "params": [_status("status", "Status", SELF_STATUS_NAMES), _num("p", "Potency", 1, 99, 2), _num("n", "Count", 1, 99, 2)]},
        {"id": "remove", "group": "Statuses", "label": "Remove a status", "text": "Remove {status}", "post": True, "ally": True,
         "params": [_status("status", "Status", REMOVABLE_NAMES)]},
        {"id": "level_gain", "group": "Offense and Defense Level", "label": "Gain Offense or Defense Level", "text": "Gain {stat} Level +{n}", "post": True, "ally": True,
         "params": [_choice("stat", "Level", ["Offense", "Defense"]), _num("n", "Steps", 1, 5, 1)]},
        {"id": "level_inflict", "group": "Offense and Defense Level", "label": "Lower the target's Offense or Defense Level", "text": "Inflict {stat} Level -{n}", "post": True,
         "params": [_choice("stat", "Level", ["Offense", "Defense"]), _num("n", "Steps", 1, 5, 1)]},
        {"id": "sanity_gain", "group": "Sanity, HP and shield", "label": "Gain Sanity", "text": "Gain {n} Sanity", "post": True, "ally": True, "params": [_num("n", "Sanity", 1, 45, 3)]},
        {"id": "sanity_lose", "group": "Sanity, HP and shield", "label": "Lose Sanity", "text": "Lose {n} Sanity", "post": True, "params": [_num("n", "Sanity", 1, 45, 3)]},
        {"id": "heal", "group": "Sanity, HP and shield", "label": "Heal HP", "text": "Heal {n} HP", "post": True, "ally": True, "params": [_num("n", "HP", 1, 999, 5)]},
        {"id": "heal_pct", "group": "Sanity, HP and shield", "label": "Heal a percent of max HP", "text": "Heal {n}% max HP", "post": True, "ally": True, "params": [_num("n", "Percent", 1, 100, 10)]},
        {"id": "shield", "group": "Sanity, HP and shield", "label": "Gain Shield", "text": "Gain {n} Shield", "post": True, "ally": True, "params": [_num("n", "Shield", 1, 999, 5)]},
        {"id": "shield_pct", "group": "Sanity, HP and shield", "label": "Gain Shield as a percent of max HP", "text": "Gain {n}% max HP Shield", "post": True, "ally": True,
         "params": [_num("n", "Percent", 1, 100, 10)]},
        {"id": "hp_floor", "group": "Staying alive", "label": "HP cannot drop below N this turn", "text": "HP cannot drop below {n} this turn", "post": True, "params": [_num("n", "HP", 1, 999, 1)]},
        {"id": "nullify", "group": "Staying alive", "label": "Nullify the next lethal damage", "text": "Nullify the next lethal damage", "post": True, "params": []},
        {"id": "no_stagger", "group": "Staying alive", "label": "Cannot be Staggered this turn", "text": "Cannot be Staggered this turn", "post": True, "params": []},
        {"id": "recover_stagger", "group": "Staying alive", "label": "Recover from Stagger", "text": "Recover from Stagger", "post": True, "params": []},
        {"id": "followup", "group": "Allies and follow-ups", "label": "Use a skill as a follow-up attack", "text": "Use {skill} as a follow-up attack", "post": True, "followup": True,
         "needsLimit": True, "params": [{"name": "skill", "kind": "skill", "label": "Skill", "default": ""}]},
        {"id": "assist", "group": "Allies and follow-ups", "label": "Take the hit for the ally", "text": "Take the hit for the ally", "post": True, "assist": True, "params": []},
    ]


TARGETS = [
    {"value": "", "label": "This unit"},
    {"value": " to the ally", "label": "The ally"},
    {"value": " to the adjacent allies", "label": "The adjacent allies"},
    {"value": " to the ally with the {selector}", "label": "The ally with the fastest, lowest..."},
]

LIMITS = [
    {"value": "", "label": "No limit"},
    {"value": " (once per turn)", "label": "Once per turn"},
    {"value": " (2 times per turn)", "label": "2 times per turn"},
    {"value": " (3 times per turn)", "label": "3 times per turn"},
    {"value": " (once per Encounter)", "label": "Once per Encounter"},
    {"value": " (2 times per Encounter)", "label": "2 times per Encounter"},
]

CHANCE = {"min": 1, "max": 99}

def flag_help() -> dict:
    """One line for each tag that stands alone on a line, taken from the bot's own /help text so the two cannot disagree."""
    from game.help_content import SKILL_TAGS
    by_key = {e.key: e.summary for e in SKILL_TAGS}
    return {label: by_key[label.lower().replace(" ", "_")] for label in FLAG_LABELS}


FLAG_LABELS = ["Guard", "Evade", "Counter", "Clashable Guard", "Clashable Counter", "Duality", "Target Fixed",
               "Unclashable", "Indiscriminate", "Unfocused Volley"]


# What an effect or a condition is worth, in the wiki's three colours: yellow helps, red hurts. The site colours the words of a rule line, and the
# choice in the picker, by it. An effect that depends on its status (Gain, Inflict, Remove) has no tone of its own: the status carries it.
_POSITIVE_EFFECTS = (
    "dmg_pct", "dmg_flat", "coin_power", "clash_power", "final_power", "dmg_pct_per", "coin_power_per", "dmg_flat_per", "consume", "level_gain",
    "sanity_gain", "heal", "heal_pct", "shield", "shield_pct", "hp_floor", "nullify", "no_stagger", "recover_stagger",
)
_NEGATIVE_EFFECTS = ("level_inflict", "sanity_lose")
_NEGATIVE_CONDITIONS = ("negatives", "morale")


def _toned(items: list[dict], positive: tuple = (), negative: tuple = ()) -> list[dict]:
    return [{**item, "tone": "positive" if item["id"] in positive else "negative"} if item["id"] in positive + negative else item for item in items]


def spec() -> dict:
    return {
        "groups": GROUPS, "timings": timings(), "conditions": _toned(conditions(), negative=_NEGATIVE_CONDITIONS),
        "effects": _toned(effects(), _POSITIVE_EFFECTS, _NEGATIVE_EFFECTS),
        "targets": TARGETS, "limits": LIMITS, "chance": CHANCE, "selectors": SELECTOR_NAMES, "flagHelp": flag_help(),
    }
