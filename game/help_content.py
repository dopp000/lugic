"""
Player-facing reference content for /help (cogs/help.py). Plain text and
structure only, no Discord imports -- cogs/help.py turns this into
embeds and buttons.

Deliberately separate from README.md: the README is written for whoever
is developing this bot (code references, "see Combat Features", commit
hashes), this is written for whoever is PLAYING on it. Keep it short,
plain, and free of internal names a player never sees.

Only two categories exist so far (Skill Tags, Damage Math) -- the two
things explicitly asked for first. More categories (Status Effects,
Trigger Syntax, Commands) can slot in later the same way: a new list of
HelpEntry plus one new line in HELP_CATEGORIES.
"""

from dataclasses import dataclass


@dataclass
class HelpEntry:
    key: str      # stable id, also used for emoji lookups where one exists
    title: str    # short name, shown as the dropdown option / embed title
    summary: str  # one line, shown as the dropdown option's description
    body: str     # the full explanation, shown once this entry is picked


SKILL_TAGS: list[HelpEntry] = [
    HelpEntry(
        key="target_fixed",
        title="Target Fixed",
        summary="Locks this Clash slot -- no ally can take it over.",
        body=(
            "Normally, if a faster ally's attack is heading for the same enemy slot "
            "you're already Clashing, they can take your spot over (Clash-steal). "
            "**Target Fixed** turns that off for this skill: your Clash against that "
            "slot can't be taken over by an ally, no matter how much faster they are."
        ),
    ),
    HelpEntry(
        key="unclashable",
        title="Unclashable",
        summary="This attack can never be Clashed -- it always lands unopposed.",
        body=(
            "Even if an enemy declares a matching attack back at you, an "
            "**Unclashable** skill skips the Clash entirely and resolves as a "
            "normal unopposed hit. It never turns into a fight of coin tosses, no "
            "matter what the enemy does."
        ),
    ),
    HelpEntry(
        key="indiscriminate",
        title="Indiscriminate",
        summary="Meant to let a skill hit allies too -- not live yet.",
        body=(
            "This tag is recognized and saved on a skill, but it doesn't do anything "
            "yet: declaring an attack still blocks targeting your own side no matter "
            "what. Tagging a skill with it now just means it's ready for whenever "
            "that restriction gets lifted."
        ),
    ),
    HelpEntry(
        key="counter",
        title="Counter",
        summary="Strikes back at a faster unopposed attack, once per round.",
        body=(
            "**Counter** isn't tied to one slot -- declare it anywhere and it "
            "watches every unopposed attack aimed at you. If your Counter skill's "
            "Speed beats the attacker's, you fully redirect the hit and strike back "
            "instead, skipping their Evade entirely. Only fires once per round."
        ),
    ),
    HelpEntry(
        key="clashable_counter",
        title="Clashable Counter",
        summary="Like Counter, but it can also be Clashed back.",
        body=(
            "Works like Counter, but instead of just comparing Speed, it can be "
            "matched head-on: if the exact enemy you declared it against targets "
            "your exact slot back, it becomes a real Clash instead of an automatic "
            "redirect. If nothing matches it, it just sits idle that round -- unlike "
            "plain Counter, it never grabs some unrelated attack instead."
        ),
    ),
    HelpEntry(
        key="guard",
        title="Guard",
        summary="Never Clashes -- turns your Power into Shield instead.",
        body=(
            "A **Guard** skill never enters a Clash, even if an enemy declares a "
            "matching attack back at you -- it always resolves on its own, turning "
            "its final Power into **Shield**, an overhead pool that soaks incoming "
            "HP damage 1:1 before your real HP is touched. Shield clears at the end "
            "of every round; it never carries over. Fixed to exactly 1 coin."
        ),
    ),
    HelpEntry(
        key="clashable_guard",
        title="Clashable Guard",
        summary="Guard that can also be Clashed -- winning raises Stagger resistance.",
        body=(
            "Like Guard, but it can be matched head-on by the exact enemy you "
            "declared it against. Winning that Clash doesn't deal damage -- it "
            "raises the loser's Stagger thresholds instead, making them harder to "
            "Stagger. Losing checks your own Evade first, and only then takes "
            "reduced damage from the winner. Fixed to exactly 1 coin."
        ),
    ),
    HelpEntry(
        key="evade",
        title="Evade",
        summary="One coin flip to fully dodge one specific incoming attack.",
        body=(
            "Declared against one exact enemy and slot -- it only ever reacts to "
            "that exact attacker hitting that exact slot, nothing else. When that "
            "hit comes in, it's a single coin toss: win and the whole attack is "
            "dodged completely, lose and nothing is reduced. No partial dodges. "
            "Fixed to exactly 1 coin, and only usable once per round."
        ),
    ),
]


DAMAGE_MATH: list[HelpEntry] = [
    HelpEntry(
        key="basic_hit",
        title="The Basic Hit",
        summary="How Base Power, Coin Power, and coin flips add up.",
        body=(
            "Each skill tosses its coins one at a time, in order. **Heads** "
            "permanently raises Power by the skill's Coin Power for every coin "
            "from then on (including itself); **Tails** just hits at whatever "
            "Power you're already at. Whatever Power a coin lands on becomes that "
            "coin's damage, before resistance is applied."
        ),
    ),
    HelpEntry(
        key="resistance",
        title="Resistance",
        summary="A flat multiplier on the damage you take from a type or status.",
        body=(
            "Every damage type (Slash/Blunt/Pierce) and status (Burn/Bleed/Tremor/"
            "Rupture/Sinking) has its own resistance value, written as a "
            "multiplier: **1.0x is Normal**. Below 1.0 takes less damage, above "
            "1.0 takes more -- it's a straight multiply, so 0.75x really does mean "
            "25% less and 1.5x really does mean 50% more. Values move in steps of "
            "0.25 (0.75, 1.0, 1.25, 1.5...), and 0x is a real, full Immune."
        ),
    ),
    HelpEntry(
        key="stagger",
        title="Stagger Multiplier",
        summary="A staggered target takes extra damage on the NEXT hit.",
        body=(
            "Once a fighter crosses a Stagger threshold, the next hit THEY TAKE is "
            "multiplied: Tier 1 is 1.5x, Tier 2 is 2.0x, Tier 3 is 2.5x. This is "
            "applied once, to the hit's whole total after every coin (and any "
            "Rupture bonus) is already added up -- not per coin. The hit that "
            "causes the Stagger doesn't get a bonus; only the hit after it does."
        ),
    ),
    HelpEntry(
        key="rupture_crit",
        title="Rupture & Crit",
        summary="Two different bonus-damage sources, one resisted and one not.",
        body=(
            "**Crit** (from Poise) adds bonus damage to a coin BEFORE resistance "
            "is applied, so it's still reduced or boosted like any normal damage. "
            "**Rupture** works differently: its bonus damage (equal to its "
            "Potency, capped at 10% of the target's max HP) is added AFTER "
            "resistance, so it always lands at full value no matter what the "
            "target resists."
        ),
    ),
    HelpEntry(
        key="attack_weight",
        title="Attack Weight Splash",
        summary="One attack hitting several enemies, each with their own math.",
        body=(
            "Attack Weight automatically picks extra enemy targets (fastest Speed "
            "first) whenever an attack with Attack Weight above 1 resolves "
            "unopposed -- Clashes are never affected. Every splash target reuses "
            "the exact same coin results as the main target (no re-rolled coins, "
            "no extra Poise spent), but still gets its OWN resistance, Stagger "
            "check, and Evade roll -- two targets with different resistances can "
            "end up taking very different final damage from the same attack."
        ),
    ),
]


# Top-level /help menu: (category_key, title, blurb shown under the Home
# embed's category buttons, that category's entries). cogs/help.py reads
# this list to build the Home view -- add a line here (and a matching
# entry list above) to add a whole new category later.
HELP_CATEGORIES: list[tuple[str, str, str, list[HelpEntry]]] = [
    (
        "skill_tags",
        "Skill Tags",
        "What each [Bracket Tag] on a skill actually does.",
        SKILL_TAGS,
    ),
    (
        "damage_math",
        "Damage Math",
        "How a hit's final damage number gets calculated.",
        DAMAGE_MATH,
    ),
]
