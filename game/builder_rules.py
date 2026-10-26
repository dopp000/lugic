"""The numbers the character builder site needs, taken from the bot's own rule modules.

The site (docs/builder/) runs in the browser and cannot import Python, so this module
writes docs/builder/rules.js from the same constants the bot checks against. Change a
price in game/build_points.py, run `python -m game.builder_rules`, and the site follows.
A test (tests/test_builder_site.py) fails when rules.js is out of date.
"""

import json
import os

from game import build_points as bp
from game.character import NAME_MAX_LENGTH, STARTING_SPEED
from game.character_import import AVATAR_MAX, PASSIVE_LINE_MAX, SKILL_LIMITS, VERSION as CODE_VERSION
from game.conditions import MAX_TAG_LENGTH, MAX_TAGS, SKILL_FLAG_TAGS
from game import line_spec
from game import wiki_assets
from game.help_content import STATUS_EFFECTS
from game.passives import MAX_PASSIVE_LINES, MAX_PASSIVES, PASSIVE_KINDS
from game.resistances import DAMAGE_TYPES, STATUS_RESISTANCE_TYPES
from game.statuses import (
    OTHER_DEBUFF_STATUSES, ONE_TURN_STATUSES, PERCENT_COUNT_STATUSES, POTENCY_TURN_STATUSES,
)

RULES_JS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "builder", "rules.js")
MAX_SKILLS = 12
MAX_COINS = 4

# Starting points for the "add a line" buttons on the site. tests/test_builder_site.py runs every
# one of them through the bot's own parser, so a button can never hand out a line the bot refuses.
SKILL_LINE_TEMPLATES = [
    {"label": "Coin Power +1", "line": "[On Use] Coin Power +1"},
    {"label": "Extra damage", "line": "[On Use] Deal +10% damage"},
    {"label": "Bonus vs Burn", "line": "[On Use] If the target has 3+ Burn, Deal +3 damage"},
    {"label": "Burn on hit", "line": ":Coin1: [On Hit] Inflict +2 Burn Count"},
]
# Lines that are only a tag (no effect text). A skill's defense type and Target Fixed live here.
SKILL_FLAG_BUTTONS = list(line_spec.FLAG_LABELS)

# Whole skills for "Add from an example". They need no status rank, and their Base Power and Coin Power fit a
# starting character (Power 6). tests/test_builder_site.py checks every one against the bot's own rules.
SKILL_EXAMPLES = [
    {"name": "Quick Jab", "blurb": "2 coins, Blunt", "base": 4, "coin": 2, "coins": 2, "type": "blunt", "triggers": ""},
    {"name": "Rending Slash", "blurb": "3 coins, Slash", "base": 4, "coin": 1, "coins": 3, "type": "slash",
     "triggers": "[On Use] Deal +10% damage"},
    {"name": "Piercing Shot", "blurb": "1 coin, Pierce", "base": 4, "coin": 2, "coins": 1, "type": "pierce",
     "triggers": "[On Use] Coin Power +1"},
    {"name": "Opening Blow", "blurb": "bonus vs Burn", "base": 4, "coin": 2, "coins": 2, "type": "blunt",
     "triggers": "[On Use] If the target has 3+ Burn, Deal +3 damage"},
]

PASSIVE_TEMPLATES = [
    {"name": "Steady Nerves", "kind": "passive", "cost": 2, "lines": ["[Combat Start] Gain 2 Protection"]},
    {"name": "Cover Fire", "kind": "support", "cost": 2, "lines": ["[Ally Is Hit] Gain 2 Protection"]},
    {"name": "Slow Burn", "kind": "passive", "cost": 3, "lines": ["[Turn Start] Gain +1 Charge Count"]},
]


# The site's status list is the bot's own help text (game/help_content.py), so the two never disagree. The wiki marks effects
# as positive (yellow), negative (red) or neutral (brown); this is that grouping for the effects the bot has.
_SIN_OF_KEYWORD = {"burn": "wrath", "bleed": "lust", "tremor": "sloth", "rupture": "gluttony", "sinking": "gloom", "poise": "pride", "charge": "envy"}
_TONES = {"poise": "positive", "charge": "neutral", "panic": "negative"}
_FRAGILITY_NOTE = "Fragile, but only against {} hits. It adds to Fragile and to the other Fragility types."


# The short form shown on the site's status list. Every status is read the same way, in the same three slots, so the pattern is learnable:
# WHEN does it act, what DOES it do, and THEN what happens to it. The full wording stays in the bot's /help text (the "body" of each entry);
# tests/test_builder_site.py checks that every number written here also appears in that full text, so the short form cannot drift.
_QUICK = {
    "burn": ("Turn end", "Take Potency damage", "Count −1"),
    "bleed": ("You attack", "Take Potency damage per coin", "Count −1"),
    "tremor": ("Tremor Burst hits you", "Stagger sooner, +Potency%", "Count −1"),
    "rupture": ("A coin hits you", "Extra Potency damage, up to 10% of max HP", "Count −1"),
    "sinking": ("Turn end", "Lose Potency Sanity", "Does not fade alone"),
    "poise": ("Each coin", "5% Crit per Potency, a Crit deals 20% more", "Count −1 per Crit"),
    "charge": ("You Consume it", "Spend it for a payoff", "Count −1 at turn end"),
    "power_up": ("Each coin", "+Count Power", "Gone at turn end"),
    "power_down": ("Each coin", "−Potency Power", "Gone at turn end"),
    "haste": ("Each skill slot", "+Count Speed", "Gone at turn end"),
    "bind": ("Each skill slot", "−Potency Speed", "Gone at turn end"),
    "damage_up": ("Damage you deal", "+10% per Count, up to 10", "Gone at turn end"),
    "damage_down": ("Damage you deal", "−10% per Count, up to 10", "Gone at turn end"),
    "protection": ("Damage you take", "−10% per Count, up to 10", "Gone at turn end"),
    "fragile": ("Damage you take", "+10% per Count, up to 10", "Gone at turn end"),
    "paralyze": ("Each skill you use", "First Count coins deal 0", "Gone at turn end"),
    "poison": ("Turn end", "Take Count damage", "Count halves"),
    "immobilized": ("When you declare", "You can't act", "Ends after the round it stops you"),
    "panic": ("Sanity −45", "Can't act next turn", "Sanity resets to 0"),
}
# Opposites sit side by side on the site, so learning one teaches the other. Each pair is one thing that can go up or down.
_PAIRS = [
    {"axis": "Coin Power", "up": "power_up", "down": "power_down"},
    {"axis": "Speed", "up": "haste", "down": "bind"},
    {"axis": "Damage you deal", "up": "damage_up", "down": "damage_down"},
    {"axis": "Damage you take", "up": "protection", "down": "fragile", "downExtra": ["slash_fragility", "pierce_fragility", "blunt_fragility"]},
]
# What the colours say, in the common reading of each colour. The tone colours (what a status does to you) are the wiki's; the Sin colours are
# also the wiki's, and the hint is why the colour suits the status: fire is red, blood is orange, a quake is a yellow warning, and so on.
_COLOR_KEY = {
    "tones": [
        {"tone": "positive", "color": "Yellow", "means": "Helps you", "hint": "gold: reward, energy"},
        {"tone": "negative", "color": "Red", "means": "Hurts you", "hint": "red: danger, blood, stop"},
        {"tone": "neutral", "color": "Brown", "means": "Neither", "hint": "brown: plain, stored, waiting"},
    ],
    "sins": [
        {"sin": "wrath", "status": "burn", "color": "Red", "hint": "fire, anger"},
        {"sin": "lust", "status": "bleed", "color": "Orange", "hint": "blood, heat"},
        {"sin": "sloth", "status": "tremor", "color": "Yellow", "hint": "caution, shaking ground"},
        {"sin": "gluttony", "status": "rupture", "color": "Green", "hint": "bursting, rot"},
        {"sin": "gloom", "status": "sinking", "color": "Teal", "hint": "cold, deep water"},
        {"sin": "pride", "status": "poise", "color": "Blue", "hint": "calm, steady"},
        {"sin": "envy", "status": "charge", "color": "Purple", "hint": "stored energy, jealousy"},
    ],
}
_POTENCY_COUNT = "Potency is how strong, Count is how many times or how long. Gaining the same status again adds to both. Potency stops at 99."


def status_guide() -> list[dict]:
    """One entry per status the builder knows, in the order a reader wants them: keywords, buffs, debuffs, other."""
    by_key = {e.key: e for e in STATUS_EFFECTS}
    keywords = list(bp.RANKED_STATUSES)
    buffs = ["power_up", "haste", "damage_up", "protection"]
    debuffs = ["bind", "power_down", "fragile", "slash_fragility", "pierce_fragility", "blunt_fragility", "damage_down", "paralyze", "poison", "immobilized"]
    out = []

    def add(key, group, tone):
        entry = by_key.get(key)
        if entry is None and key.endswith("_fragility"):
            kind = key[: -len("_fragility")]
            entry = by_key["fragile"]
            title, summary, body = f"{kind.title()} Fragility", _FRAGILITY_NOTE.format(kind.title()), by_key["fragile"].body
        else:
            title, summary, body = entry.title, entry.summary, entry.body
        item = {"key": key, "title": title, "summary": summary, "body": body, "group": group, "tone": tone}
        if key in _QUICK:
            item["quick"] = dict(zip(("when", "does", "then"), _QUICK[key]))
        elif key.endswith("_fragility"):
            when, does, then = _QUICK["fragile"]
            item["quick"] = {"when": f"{key[: -len('_fragility')].title()} damage you take", "does": does, "then": then}
        if key in _SIN_OF_KEYWORD:
            item["sin"] = _SIN_OF_KEYWORD[key]
        out.append(item)

    for key in keywords:
        add(key, "keyword", _TONES.get(key, "negative"))
    for key in buffs:
        add(key, "buff", "positive")
    for key in debuffs:
        add(key, "debuff", "negative")
    add("panic", "other", "negative")
    return out


def rules() -> dict:
    """Every rule number the site uses, as plain data."""
    return {
        "codeVersion": CODE_VERSION,
        "limits": {
            "name": NAME_MAX_LENGTH, "avatar": AVATAR_MAX, "skillName": SKILL_LIMITS["name"],
            "statuses": SKILL_LIMITS["statuses"], "triggers": SKILL_LIMITS["triggers"],
            "passiveName": 40, "passiveLine": PASSIVE_LINE_MAX, "discordBox": 4000,
        },
        "startingBp": bp.STARTING_BP,
        "levels": [list(pair) for pair in bp.LEVEL_THRESHOLDS],
        "hp": {"base": bp.HP_BASE, "max": bp.HP_MAX, "step": bp.HP_STEP, "costs": bp.HP_STEP_COSTS},
        "power": {"base": bp.POWER_BASE, "max": bp.POWER_MAX, "costs": bp.POWER_STEP_COSTS},
        "speed": {
            "lowest": bp.SPEED_LOWEST, "highest": bp.SPEED_HIGHEST, "minSpread": bp.SPEED_MIN_SPREAD,
            "avgCosts": {str(k): v for k, v in bp.SPEED_AVG_COSTS.items()}, "start": list(STARTING_SPEED),
        },
        "stagger": {str(k): v for k, v in bp.STAGGER_REMOVE_COSTS.items()},
        "resist": {
            "statusTypes": list(STATUS_RESISTANCE_TYPES),
            "damageTypes": list(DAMAGE_TYPES),
            "statusCosts": {str(k): v for k, v in bp.STATUS_RESIST_COSTS.items()},
            "refundCap": bp.RESIST_REFUND_CAP,
            "physicalSets": [list(s) for s in bp.PHYSICAL_SETS],
        },
        "offdef": {"max": bp.OFFDEF_MAX, "costs": bp.OFFDEF_STEP_COSTS, "percentPerStep": bp.OFFDEF_PERCENT_PER_STEP},
        "ranks": {
            "statuses": list(bp.RANKED_STATUSES), "max": bp.RANK_MAX, "maxStatuses": bp.RANK_MAX_STATUSES,
            "costs": bp.RANK_COSTS, "budgets": bp.RANK_BUDGETS, "potencyCaps": bp.RANK_POTENCY_CAPS,
        },
        "nonKeyword": {
            "cap": bp.NONKEYWORD_CAP, "sizeCaps": bp.NONKEYWORD_SIZE_CAPS, "percentCap": bp.NONKEYWORD_PERCENT_CAP,
            "oneTurn": list(ONE_TURN_STATUSES), "potencyTurn": list(POTENCY_TURN_STATUSES),
            "percentCount": list(PERCENT_COUNT_STATUSES),
        },
        "otherStatuses": list(OTHER_DEBUFF_STATUSES),
        "statusGuide": status_guide(),
        "wikiImages": wiki_assets.remote_urls(),
        "statusPairs": _PAIRS,
        "colorKey": _COLOR_KEY,
        "potencyCount": _POTENCY_COUNT,
        "stackingNote": next(e.body for e in STATUS_EFFECTS if e.key == "stacking"),
        "defense": {
            "archetypes": dict(bp.DEFENSE_ARCHETYPES), "max": bp.DEFENSE_MAX, "minSpeed": bp.DEFENSE_MIN_SPEED,
            "needsSpeed": sorted(bp.DEFENSE_NEEDS_SPEED), "needsNoTier2": sorted(bp.DEFENSE_NEEDS_NO_TIER2),
            "order": list(bp._DEFENSE_ORDER),
        },
        "caps": {"single": bp.SINGLE_STAT_SHARE, "pair": bp.PAIR_SHARE},
        "passives": {
            "max": MAX_PASSIVES, "maxLines": MAX_PASSIVE_LINES, "costMax": bp.PP_COST_MAX,
            "kinds": list(PASSIVE_KINDS), "templates": PASSIVE_TEMPLATES,
        },
        "lines": line_spec.spec(),
        "sp": {"perExtraLine": bp.SP_PER_EXTRA_LINE},
        "tags": {"max": MAX_TAGS, "maxLength": MAX_TAG_LENGTH},
        "skills": {
            "max": MAX_SKILLS, "maxCoins": MAX_COINS, "damageTypes": list(DAMAGE_TYPES),
            "flagTags": dict(SKILL_FLAG_TAGS), "flagButtons": SKILL_FLAG_BUTTONS,
            "lineTemplates": SKILL_LINE_TEMPLATES, "examples": SKILL_EXAMPLES,
        },
    }


def rules_js() -> str:
    """The text of docs/builder/rules.js."""
    body = json.dumps(rules(), indent=2, sort_keys=True, ensure_ascii=False)
    return (
        "// Generated by game/builder_rules.py. Do not edit by hand: run  python -m game.builder_rules\n"
        "(function (root) {\n"
        f"  var rules = {body.replace(chr(10), chr(10) + '  ')};\n"
        "  if (typeof module === 'object' && module.exports) { module.exports = rules; }\n"
        "  else { root.LUGIC_RULES = rules; }\n"
        "})(typeof self !== 'undefined' ? self : this);\n"
    )


def write_rules_js(path: str = RULES_JS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rules_js())


if __name__ == "__main__":
    write_rules_js()
    print(f"Wrote {RULES_JS_PATH}")
