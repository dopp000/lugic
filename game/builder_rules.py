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
SKILL_FLAG_BUTTONS = ["Guard", "Evade", "Counter", "Clashable Guard", "Clashable Counter", "Duality", "Target Fixed"]

PASSIVE_TEMPLATES = [
    {"name": "Steady Nerves", "kind": "passive", "cost": 2, "lines": ["[Combat Start] Gain 2 Protection"]},
    {"name": "Cover Fire", "kind": "support", "cost": 2, "lines": ["[Ally Is Hit] Gain 2 Protection"]},
    {"name": "Slow Burn", "kind": "passive", "cost": 3, "lines": ["[Turn Start] Gain +1 Charge Count"]},
]


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
        "sp": {"perExtraLine": bp.SP_PER_EXTRA_LINE},
        "tags": {"max": MAX_TAGS, "maxLength": MAX_TAG_LENGTH},
        "skills": {
            "max": MAX_SKILLS, "maxCoins": MAX_COINS, "damageTypes": list(DAMAGE_TYPES),
            "flagTags": dict(SKILL_FLAG_TAGS), "flagButtons": SKILL_FLAG_BUTTONS,
            "lineTemplates": SKILL_LINE_TEMPLATES,
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
