# Discord custom emoji IDs, keyed by name. Fill any remaining None values
# in once you've uploaded that icon as a custom emoji in your server.
#
# HOW TO GET AN EMOJI'S ID:
# In any channel where the emoji is available, type a backslash right
# before it, e.g.  \:Burn:  then send the message. Discord expands it
# to the full tag, like <:Burn:1234567890123456789>, copy the number
# from there into the dict below.
#
# Anything left as None falls back to plain ":Name:" text instead of a
# real icon, so the bot keeps working while you fill these in gradually.

STATUS_EMOJI_IDS: dict[str, int | None] = {
    "burn": 1538062892094460024,
    "rupture": 1538063110001401906,
    "bleed": 1538062845793533992,
    "tremor": 1538063202930393179,
    "charge": 1538062873241059338,
    "tremor_burst": 1538063181967130624,  # Tremor Burst is fully built (fire_tremor_burst in cogs/battle.py) -- this comment used to say "not wired yet," that's stale now
    "sinking": 1538063130729512962,
    "poise": 1538062820300816384,
    "evasion": 1538065517896671252,  # uploaded as "Evade", not "Evasion" -- see _STATUS_DISPLAY_NAMES below
    "counter": 1543225884310245406,
    "clashable_counter": 1546006725578985533,
    # Shield/Guard/Clashable Guard are all fully built too (see Combat
    # Features in README.md) -- same stale-comment cleanup as
    # tremor_burst above, flagged there since the last README pass.
    "shield": 1544679323192266904,   # Guard's overhead HP pool
    "guard": 1545222381600243762,
    "clashable_guard": 1546005005834649640,
    "stagger": 1545221789037363252,
    # Panic and Low Morale intentionally share one icon -- same as how
    # they share design (see README) -- rather than needing two uploads.
    "panic": 1544676724862623834,
    "low_morale": 1544676724862623834,
    # Non-Sin debuffs.
    "bind": 1561188364370055198,
    "fragile": 1538062954354704384,
    "slash_fragility": 1561188811093057646,
    "pierce_fragility": 1561188699285364776,
    "blunt_fragility": 1561188379540856875,
    "power_down": 1538063056406323263,
    "damage_down": 1561189223804047480,
    "paralyze": 1561189630936875081,
    "poison": 1561189616995008512,
    "immobilized": 1561189600481771572,
    # Buff icons uploaded ahead of time, no status behind them yet.
    "power_up": 1538063077856256000,
    "protection": None,
    "haste": 1561190558767124512,
    "damage_up": 1561189128106803260,
}

# Stagger has its own tiered icon set, since real Limbus visually
# distinguishes how badly Stagger'd a unit is, not just whether they
# are. Tier 1 deliberately reuses STATUS_EMOJI_IDS["stagger"] above
# (the original, only icon before tiers 2/3 existed) rather than a
# fresh upload, so nothing needs re-uploading for the mildest tier.
STAGGER_TIER_EMOJI_IDS: dict[int, int | None] = {
    1: STATUS_EMOJI_IDS["stagger"],
    2: 1547098766773981244,
    3: 1547098781105922048,
}

# Shown whenever a Stagger'd fighter is BLOCKED from acting (see the
# current_stagger_tier check in /battle declare) -- distinct from the
# tiered icons above, which represent Stagger's damage-multiplier
# severity, not the "can't act this turn" consequence.
STAGGER_ACTION_EMOJI_ID: int | None = 1547098287541198928

# Internal keys stay semantic (base/tails/heads) so the rest of the
# codebase never has to know or care what the emoji is actually named on
# Discord's side. Only this file needs to track that mapping.
COIN_EMOJI_IDS: dict[str, int | None] = {
    "base": 1538062791339147264,   # displayed on Discord as "Coin"
    "tails": 1538062765980131370,  # displayed on Discord as "CoinIcon"
    "heads": 1538062747609206845,  # displayed on Discord as "CoinIconGlow"
}
_COIN_DISPLAY_NAMES = {"base": "Coin", "tails": "CoinIcon", "heads": "CoinIconGlow"}

DAMAGE_TYPE_EMOJI_IDS: dict[str, int | None] = {
    "slash": 1538065502210097204,
    "blunt": 1538065584602751019,
    "pierce": 1538065614739079198,
}

STAT_EMOJI_IDS: dict[str, int | None] = {
    "speed": 1538062414665486457,
    "hp": 1538062502896607232,
    "sanity": 1544677030245703771,
    "offense_level": 1538062452082745474,
    "defense_level": 1547097971840122921,
}
_STAT_DISPLAY_NAMES = {
    "speed": "SPEED", "hp": "HP", "sanity": "Sanity",
    "offense_level": "Offense_Level", "defense_level": "Defense_Level",
}

# Severity tiers for the small "-# hint" line under a fighter's name on the
# battle-view embed. 1 = mild, 2 = dangerous, 3 = highly dangerous.
# Which specific statuses/passives map to which tier still needs a real
# classification pass. These are just the icons themselves for now.
HINT_EMOJI_IDS: dict[int, int | None] = {
    1: 1538077024319442964,
    2: 1538077039666659388,
    3: 1538077057425215569,
}

SKILL_SLOT_EMOJI_IDS: dict[int, int | None] = {
    1: 1538062724368437308,
    2: 1538062706542903306,
    3: 1538062687362097282,
    4: 1538062570856906842,
    5: 1538062524963098655,
}

# Animated emoji use a different tag format: <a:Name:ID> instead of <:Name:ID>.
COIN_ROLL_EMOJI_ID: int | None = 1538091919995965471

# /help's top-level category buttons (cogs/help.py). None for now, same
# gradual-fill convention as everywhere else in this file -- falls back
# to plain ":Name:" text in embed/message bodies until these get
# uploaded and filled in. Note: unlike every other icon in this file,
# these are NEVER passed into a Button/SelectOption's own `emoji=`
# kwarg while still None -- that field needs a real emoji object, and
# the plain-text fallback below would get rejected there. cogs/help.py
# only uses emoji_tag()'s output for embed/message text, and builds its
# own discord.PartialEmoji directly from these same IDs (skipping the
# kwarg entirely when one's still None) for the structural emoji= spot.
HELP_CATEGORY_EMOJI_IDS: dict[str, int | None] = {
    "skill_tags": None,
    "damage_math": None,
}

# Skill-flag tags whose icon is one already uploaded for a different
# purpose -- reused by ID rather than re-uploading the same icon twice.
# The three with no match yet (no existing mechanic looks like them)
# default to None, same convention as above.
SKILL_TAG_EMOJI_IDS: dict[str, int | None] = {
    "counter": STATUS_EMOJI_IDS["counter"],
    "clashable_counter": STATUS_EMOJI_IDS["clashable_counter"],
    "guard": STATUS_EMOJI_IDS["guard"],
    "clashable_guard": STATUS_EMOJI_IDS["clashable_guard"],
    "evade": STATUS_EMOJI_IDS["evasion"],
    "target_fixed": None,
    "unclashable": None,
    "indiscriminate": None,
}

def emoji_tag(display_name: str, emoji_id: int | None, animated: bool = False) -> str:
    """Builds a real Discord custom emoji tag if an ID is known, otherwise
    falls back to plain ":Name:" text (visible as literal characters,
    not an icon, until the ID gets filled in above).

    Animated emoji need the "a" prefix: <a:Name:ID> instead of <:Name:ID>.
    """
    if emoji_id is None:
        return f":{display_name}:"
    prefix = "a" if animated else ""
    return f"<{prefix}:{display_name}:{emoji_id}>"


# Emoji for player-made statuses, filled in when a custom status is
# created or a Character joins a battle: key -> (emoji_id, animated).
# Global, so two characters using the same status name with different
# emoji means the last one registered wins (cosmetic only).
CUSTOM_STATUS_EMOJIS: dict[str, tuple[int, bool]] = {}


def register_custom_status_emoji(cs) -> None:
    if cs.emoji_id is not None:
        CUSTOM_STATUS_EMOJIS[cs.key] = (cs.emoji_id, cs.animated)


def status_label(status_name: str) -> str:
    """'slash_fragility' -> 'Slash Fragility', for logs and embeds."""
    return status_name.replace("_", " ").title()


def status_emoji(status_name: str) -> str:
    key = status_name.lower()
    if key in CUSTOM_STATUS_EMOJIS:
        eid, animated = CUSTOM_STATUS_EMOJIS[key]
        return emoji_tag(key.title().replace(" ", "_"), eid, animated)
    # Internal status keys don't always match the emoji's actual name on
    # Discord -- the engine calls this status "evasion" everywhere (it
    # reads better next to poise/charge/counter), but the uploaded emoji
    # itself is named "Evade". Only entries that DON'T just match
    # key.capitalize() need to be listed here.
    display_names = {
        "evasion": "Evade",
        "clashable_guard": "Clashable_Guard",
        "clashable_counter": "Clashable_Counter",
        "slash_fragility": "Slash_Fragility",
        "pierce_fragility": "Pierce_Fragility",
        "blunt_fragility": "Blunt_Fragility",
        "power_down": "Power_Down",
        "power_up": "Power_Up",
        "damage_up": "Damage_Up",
        "damage_down": "Damage_Down",
    }
    display_name = display_names.get(key, status_name.capitalize())
    return emoji_tag(display_name, STATUS_EMOJI_IDS.get(key))


def coin_emoji(face: str) -> str:
    """face is 'base', 'heads', or 'tails'."""
    key = face.lower()
    return emoji_tag(_COIN_DISPLAY_NAMES[key], COIN_EMOJI_IDS.get(key))


def coin_roll_emoji() -> str:
    """The animated "currently rolling" coin, for the coin-by-coin reveal."""
    return emoji_tag("CoinRoll", COIN_ROLL_EMOJI_ID, animated=True)


def damage_type_emoji(damage_type: str) -> str:
    """damage_type is 'slash', 'blunt', or 'pierce'."""
    key = damage_type.lower()
    return emoji_tag(damage_type.capitalize(), DAMAGE_TYPE_EMOJI_IDS.get(key))


def stat_emoji(stat_name: str) -> str:
    """stat_name is 'speed', 'hp', or 'sanity'."""
    key = stat_name.lower()
    return emoji_tag(_STAT_DISPLAY_NAMES[key], STAT_EMOJI_IDS.get(key))


def stagger_tier_emoji(tier: int) -> str:
    """tier is 1, 2, or 3 -- the fighter's current Stagger severity (see
    Fighter.current_stagger_tier in game/battle.py). Only meaningful for
    tier >= 1; callers are expected to check current_stagger_tier > 0
    before calling this, same as every other Stagger-gated call site.
    """
    return emoji_tag(f"Stagger_{tier}", STAGGER_TIER_EMOJI_IDS.get(tier))


def stagger_action_emoji() -> str:
    """The distinct 'can't act this turn' icon shown when /battle declare
    rejects a Stagger'd caster -- not the same icon as the tiered
    damage-multiplier icons above, since this represents the action-lock
    consequence of Stagger, not its severity.
    """
    return emoji_tag("Stagger_Action", STAGGER_ACTION_EMOJI_ID)

def hint_emoji(tier: int) -> str:
    """tier is 1 (mild), 2 (dangerous), or 3 (highly dangerous)."""
    return emoji_tag(f"Hint_{tier}", HINT_EMOJI_IDS.get(tier))


def skill_slot_emoji(slot_number: int) -> str:
    """slot_number is 1 through 5."""
    return emoji_tag(f"SkillSlot{slot_number}", SKILL_SLOT_EMOJI_IDS.get(slot_number))

def attack_weight_icons(weight: int) -> str:
    """Attack Weight has no custom Discord emoji -- Limbus itself just
    repeats one glyph (⯀) once per point of weight, so this needs no ID
    lookup or emoji_tag() wrapping. weight 1 (the default, no real Attack
    Weight) still renders as a single ⯀.
    """
    return "⯀" * max(weight, 1)


def help_category_emoji(category_key: str) -> str:
    """For /help's top-level category buttons (cogs/help.py), safe to
    drop into embed/message TEXT only -- see the "never passed as a
    Button/SelectOption emoji=" note on HELP_CATEGORY_EMOJI_IDS above.
    """
    display_names = {"skill_tags": "Skill_Tags", "damage_math": "Damage_Math"}
    key = category_key.lower()
    display_name = display_names.get(key, key.capitalize())
    return emoji_tag(display_name, HELP_CATEGORY_EMOJI_IDS.get(key))


def skill_tag_emoji(tag_key: str) -> str:
    """For a skill-flag tag entry in /help (cogs/help.py), safe to drop
    into embed/message TEXT only -- same caveat as help_category_emoji
    above.
    """
    display_names = {
        "target_fixed": "Target_Fixed",
        "clashable_counter": "Clashable_Counter",
        "clashable_guard": "Clashable_Guard",
    }
    key = tag_key.lower()
    display_name = display_names.get(key, key.capitalize())
    return emoji_tag(display_name, SKILL_TAG_EMOJI_IDS.get(key))