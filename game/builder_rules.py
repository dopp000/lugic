"""The numbers the character builder site needs, taken from the bot's own rule modules.

The site (docs/builder/) runs in the browser and cannot import Python, so this module
writes docs/builder/rules.js from the same constants the bot checks against. Change a
price in game/build_points.py, run `python -m game.builder_rules`, and the site follows.
A test (tests/test_builder_site.py) fails when rules.js is out of date. The same command also writes the
fingerprints of the site's files into index.html (see VERSIONED_FILES), so run it after changing any of them.
"""

import hashlib
import json
import os
import re

from game import build_points as bp
from game.character import NAME_MAX_LENGTH, STARTING_SPEED
from game.character_import import AVATAR_MAX, PASSIVE_LINE_MAX, SKILL_LIMITS, VERSION as CODE_VERSION
from game.conditions import MAX_TAG_LENGTH, MAX_TAGS, SKILL_FLAG_TAGS
from game import line_spec
from game import wiki_assets
from game.help_content import STATUS_EFFECTS
from game.passives import MAX_PASSIVE_LINES, MAX_PASSIVES, PASSIVE_FLAVOUR_MAX, PASSIVE_KINDS
from game.resistances import DAMAGE_TYPES, STATUS_RESISTANCE_TYPES
from game.statuses import (
    OTHER_DEBUFF_STATUSES, ONE_TURN_STATUSES, PERCENT_COUNT_STATUSES, POTENCY_TURN_STATUSES, SANITY_LOW_MORALE, SANITY_PANIC,
)

RULES_JS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "builder", "rules.js")
MAX_SKILLS = 12

# Group tags offered in the site's drop-down besides the ones saved characters already use (see tags_js). The names are the factions the
# passive survey found identities counting; a tag is free text and is matched ignoring case, so any other spelling still works ("Other").
SUGGESTED_TAGS = ["Blade Lineage", "Dawn Office", "Heishou", "Kurokumo", "Liu Association", "N Corp", "Seven Association", "The Middle", "The Ring"]
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
# starting character (Power 6) for their number of coins (4/2 for 1 coin, 3/1 for 2, 2/1 for 3, 1/1 for 4).
# tests/test_builder_site.py checks every one against the bot's own rules.
SKILL_EXAMPLES = [
    {"name": "Quick Jab", "blurb": "2 coins, Blunt", "base": 3, "coin": 1, "coins": 2, "type": "blunt", "triggers": ""},
    {"name": "Rending Slash", "blurb": "3 coins, Slash", "base": 2, "coin": 1, "coins": 3, "type": "slash",
     "triggers": "[On Use] Deal +10% damage"},
    {"name": "Piercing Shot", "blurb": "1 coin, Pierce", "base": 4, "coin": 2, "coins": 1, "type": "pierce",
     "triggers": "[On Use] Coin Power +1"},
    {"name": "Opening Blow", "blurb": "bonus vs Burn", "base": 3, "coin": 1, "coins": 2, "type": "blunt",
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
    "power_up": ("Each coin", "+Potency Power", "Gone at turn end"),
    "power_down": ("Each coin", "−Potency Power", "Gone at turn end"),
    "haste": ("Each skill slot", "+Potency Speed", "Gone at turn end"),
    "bind": ("Each skill slot", "−Potency Speed", "Gone at turn end"),
    "damage_up": ("Damage you deal", "+10% per Potency, up to 10", "Gone at turn end"),
    "damage_down": ("Damage you deal", "−10% per Potency, up to 10", "Gone at turn end"),
    "protection": ("Damage you take", "−10% per Potency, up to 10", "Gone at turn end"),
    "fragile": ("Damage you take", "+10% per Potency, up to 10", "Gone at turn end"),
    "paralyze": ("Each skill you use", "First Potency coins deal 0", "Gone at turn end"),
    "poison": ("Turn end", "Take Count damage", "Count halves"),
    "immobilized": ("When you declare", "You can't act", "Ends after the round it stops you"),
    "panic": ("Sanity −30 and below, up to −45", "Low Morale. Panic at −45: can't act next turn", "Sanity resets to 0"),
}
# Opposites sit side by side on the site, so learning one teaches the other. Each pair is one thing that can go up or down.
_PAIRS = [
    {"axis": "Coin Power", "up": "power_up", "down": "power_down"},
    {"axis": "Speed", "up": "haste", "down": "bind"},
    {"axis": "Damage you deal", "up": "damage_up", "down": "damage_down"},
    {"axis": "Damage you take", "up": "protection", "down": "fragile", "downExtra": ["slash_fragility", "pierce_fragility", "blunt_fragility"]},
]
# What the colours say. The three tone colours (what a status does to you) are the wiki's. Nothing else is explained: the colour is the hint.
_COLOR_KEY = {
    "tones": [
        {"tone": "positive", "color": "Yellow", "means": "Helps you"},
        {"tone": "negative", "color": "Red", "means": "Hurts you"},
        {"tone": "neutral", "color": "Brown", "means": "Neither"},
    ],
}
# What an underlined word says when the reader points at it (statuses use their own entry in the status guide, skill-type tags use
# line_spec's flag help). Every number here is checked against the bot's /help text by tests/test_builder_site.py.
_GLOSSARY = {
    "slash": "A damage type. A unit's Resistances can make it take 0.5x, 1.0x or 1.5x of it.",
    "pierce": "A damage type. A unit's Resistances can make it take 0.5x, 1.0x or 1.5x of it.",
    "blunt": "A damage type. A unit's Resistances can make it take 0.5x, 1.0x or 1.5x of it.",
    "hp": "Health. Damage lowers it, Heal raises it.",
    "speed": "Rolled for every skill slot each round, from the unit's Speed range. Higher is faster.",
    "sanity": f"At {SANITY_LOW_MORALE} Sanity a unit is on Low Morale. At {SANITY_PANIC} it Panics and can't act next turn.",
    "offense_level": f"0 to {bp.OFFDEF_MAX}. Every step is {bp.OFFDEF_PERCENT_PER_STEP}% more damage you deal.",
    "defense_level": f"0 to {bp.OFFDEF_MAX}. Every step is {bp.OFFDEF_PERCENT_PER_STEP}% less damage you take.",
}
_POTENCY_COUNT = "Potency is how strong, Count is how many times or how long. Gaining the same status again adds to both. Potency stops at 99. Statuses with no keyword (the up and down list) only have Potency."


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
            "passiveName": 40, "passiveLine": PASSIVE_LINE_MAX, "passiveFlavour": PASSIVE_FLAVOUR_MAX, "discordBox": 4000,
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
        "glossary": _GLOSSARY,
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
        "tags": {"max": MAX_TAGS, "maxLength": MAX_TAG_LENGTH, "suggested": SUGGESTED_TAGS},
        "skills": {
            "max": MAX_SKILLS, "maxCoins": MAX_COINS, "damageTypes": list(DAMAGE_TYPES),
            "coinCapStep": bp.COIN_CAP_STEP, "minCoinCap": bp.MIN_COIN_CAP,
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


# The page's own files, in the order index.html loads them. Browsers keep a file for a few minutes after it was fetched (GitHub
# Pages allows ten), so right after a new version goes online a visitor can get the new app.js with the old preview.js, and the
# page breaks. Each file's address in index.html therefore carries a short fingerprint of its content (preview.js?v=3fa9...), so a
# changed file is always fetched fresh and a page is never made of two versions.
SITE_FOLDER = os.path.dirname(RULES_JS_PATH)
VERSIONED_FILES = ["style.css", "rules.js", "tags.js", "engine.js", "preview.js", "lines.js", "visualizer.js", "app.js"]
INDEX_HTML_PATH = os.path.join(SITE_FOLDER, "index.html")
TAGS_JS_PATH = os.path.join(SITE_FOLDER, "tags.js")


def site_tags(saved=None) -> list[str]:
    """The group tags the site's drop-down lists: every tag a saved character has plus the suggestions, once each (ignoring case and
    spacing), in alphabetical order. Where both spell a tag, the suggestion's spelling is used. `saved` is a list of tag texts; left
    out, it is read from the bot's saved characters (data/characters), which only exist where the bot runs."""
    from game.conditions import norm_tag
    if saved is None:
        from game.character import list_all_characters
        saved = [tag for character in list_all_characters() for tag in character.tags]
    found = {}
    for tag in saved:
        spelled = " ".join(str(tag).split())
        if spelled and len(spelled) <= MAX_TAG_LENGTH:
            found.setdefault(norm_tag(spelled), spelled)
    for tag in SUGGESTED_TAGS:
        found[norm_tag(tag)] = tag
    return [found[key] for key in sorted(found)]


def tags_js(tags=None) -> str:
    """The text of docs/builder/tags.js: the tag list the page loads before app.js."""
    return (
        "// Generated by game/builder_rules.py from the suggested tags and the tags saved characters have. Do not edit by hand: run  python -m game.builder_rules\n"
        "window.LUGIC_TAGS = " + json.dumps(site_tags() if tags is None else tags, ensure_ascii=False) + ";\n"
    )


def write_tags_js(path: str = TAGS_JS_PATH) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(tags_js())


def file_version(name: str, folder: str = SITE_FOLDER) -> str:
    with open(os.path.join(folder, name), "rb") as handle:
        return hashlib.sha1(handle.read()).hexdigest()[:10]


def versioned_index(html: str, folder: str = SITE_FOLDER) -> str:
    """index.html with the fingerprint of every file in VERSIONED_FILES written into its address."""
    for name in VERSIONED_FILES:
        pattern = r'((?:href|src)=")' + re.escape(name) + r'(?:\?v=[0-9a-f]+)?"'
        html = re.sub(pattern, lambda found: f'{found.group(1)}{name}?v={file_version(name, folder)}"', html)
    return html


def write_index_html(path: str = INDEX_HTML_PATH) -> None:
    with open(path, encoding="utf-8", newline="") as handle:
        html = handle.read()
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(versioned_index(html, os.path.dirname(path)))


if __name__ == "__main__":
    write_rules_js()
    write_tags_js()
    write_index_html()
    print(f"Wrote {RULES_JS_PATH}, {TAGS_JS_PATH} and the file versions in {INDEX_HTML_PATH}")
