"""
Player-facing reference content for /help (cogs/help.py). Plain text and
structure only, no Discord imports -- cogs/help.py turns this into
embeds and buttons.

Deliberately separate from README.md: the README is written for whoever
is developing this bot (code references, "see Combat Features", commit
hashes), this is written for whoever is PLAYING on it. Keep it short,
plain, and free of internal names a player never sees.

Five categories exist so far (Skill Tags, Damage Math, Status Effects, Trigger Syntax,
Commands). More can slot in later the same way: a new list of
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
        key="duality",
        title="Duality",
        summary="Attacks an enemy, or helps an ally. You pick when you declare.",
        body=(
            "A **Duality** skill works two ways depending on who you aim it at.\n"
            "- **Aimed at an enemy:** it is a normal attack. It Clashes as usual unless "
            "it also has **[Unclashable]**.\n"
            "- **Aimed at an ally (or yourself):** nobody takes damage and nothing Clashes, "
            "but the coins still flip and its lines still fire. Use the condition "
            "`If the target is an ally,` to write lines that only happen in this mode "
            "(and `If the target is an enemy,` for the attack mode).\n"
            "Add **to the ally** to a line to put its effect on the ally you aimed at, for "
            "example `[Attack End] If the target is an ally, Heal 8 HP to the ally`. "
            "Heal, Gain, Gain Sanity and Remove (clear a status) can go to an ally. "
            "Lines without it affect you. Old skills tagged `[Support]` now count as Duality."
        ),
    ),
    HelpEntry(
        key="supermove",
        title="Supermove",
        summary="A powerful E.G.O-style skill with a Sanity price.",
        body=(
            "Put **[Supermove]** on its own line to make an attack skill a Supermove. "
            "The bot prices it from how hard it hits (its expected total damage):\n"
            "- **Cost:** about damage / 6 Sanity (at least 4), paid the moment you "
            "lock it in. You need at least that much Sanity to declare it. Undeclaring "
            "gives it back.\n"
            "- **Aftermath:** a skill that hits for about 40+ keeps draining Sanity at every "
            "turn end afterwards (5 for 2 turns, 8 for 3 turns at 80+, 10 for 3 turns at 130+). "
            "The drain can push you into Panic.\n"
            "- **Reuse:** every time you use the same Supermove again in one battle, the "
            "cost and the drain are 50% bigger.\n"
            "It can't also be Duality, Guard, Evade or Counter. Check `/battle mystatus` "
            "to see a drain that is still running."
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
    HelpEntry(
        key="parts_core",
        title="Parts and Core",
        summary="Multi-part enemies: aim at a body part, drain the Core.",
        body=(
            "Some enemies are built from **Parts**. Each Part is one of the enemy's "
            "skill slots, so aiming at its Slot 2 means hitting that Part. A hit "
            "uses the Part's own resistances (if it has any), lowers the Part's HP, "
            "and also lowers the enemy's main HP, the **Core**. Killing the Core "
            "defeats the enemy.\n"
            "When a Part reaches 0 HP it is **broken**: that slot can't be used "
            "from the next round on, until an admin repairs it. A broken Part keeps "
            "draining the Core when hit. Part names and HP show under the enemy in "
            "the battle display."
        ),
    ),
]


# Top-level /help menu: (category_key, title, blurb shown under the Home
# embed's category buttons, that category's entries). cogs/help.py reads
# this list to build the Home view -- add a line here (and a matching
# entry list above) to add a whole new category later.
STATUS_EFFECTS: list[HelpEntry] = [
    HelpEntry(
        key="stacking",
        title="Potency and Count",
        summary="The two numbers on every status, and what the warning icons mean.",
        body=(
            "Most statuses have two numbers. **Potency** is how strong it is. **Count** is how many times it can still happen, or how long it lasts. Gaining the same status again adds to both. Potency tops out at 99.\n\nSome statuses only last for the turn they land in and are wiped at turn end (Bind, Fragile and friends). Others stay until they run out. Each status page says which.\n\nYou usually can't see exact numbers on an enemy. The icons under a fighter's name hint at how dangerous their statuses and passives are."
        ),
    ),
    HelpEntry(
        key="burn",
        title="Burn",
        summary="Damage to you at turn end, then it fades.",
        body=(
            "At the end of the turn you take fixed damage equal to Burn's **Potency**, then its **Count** drops by 1. At 0 Count it is gone. Shield soaks the damage first."
        ),
    ),
    HelpEntry(
        key="bleed",
        title="Bleed",
        summary="Hurts you whenever you attack, then it fades.",
        body=(
            "Each time you use an attack skill, you take fixed damage equal to Bleed's **Potency** for **every coin** in that skill, and its Count drops by 1. Defense skills (Guard, Counter and so on) don't count as attacking."
        ),
    ),
    HelpEntry(
        key="tremor",
        title="Tremor",
        summary="Tremor Burst makes the target easier to Stagger.",
        body=(
            "Tremor does nothing alone. When a skill with a **[Tremor Burst]** line hits a target holding Tremor, the target's Stagger thresholds rise by Tremor's **Potency** in percentage points, which means they Stagger sooner. Then Tremor's Count drops by 1."
        ),
    ),
    HelpEntry(
        key="rupture",
        title="Rupture",
        summary="Hits on you deal extra fixed damage.",
        body=(
            "Every coin that hits you deals extra fixed damage equal to Rupture's **Potency**, and its Count drops by 1 each time. The extra damage is capped at 10% of your max HP per coin, so a huge stack can't one-shot you."
        ),
    ),
    HelpEntry(
        key="sinking",
        title="Sinking",
        summary="Drains your Sanity at turn end.",
        body=(
            "At the end of the turn you lose Sanity equal to Sinking's **Potency**. It does not fade on its own. Low Sanity makes your coins land tails more often and can lead to Low Morale and Panic."
        ),
    ),
    HelpEntry(
        key="poise",
        title="Poise",
        summary="Chance to Crit.",
        body=(
            "Each coin has a **5% Crit chance per Potency** (up to 100%). A Crit adds **20% more damage** to that coin and uses up 1 Count. Poise is something you gain on yourself."
        ),
    ),
    HelpEntry(
        key="charge",
        title="Charge",
        summary="A stored resource you spend with Consume.",
        body=(
            "Charge does nothing by itself. Skills and passives give it to you, and a **Consume** line spends it for a payoff, for example `Consume 3 Charge: Coin Power +2`. Its Count drops by 1 each turn end, up to a cap of 20."
        ),
    ),
    HelpEntry(
        key="bind",
        title="Bind",
        summary="Makes you slower this turn.",
        body=(
            "Every skill slot loses Speed equal to Bind's **Potency**, down to a minimum of 0. Lasts for the turn, then it's wiped."
        ),
    ),
    HelpEntry(
        key="power_down",
        title="Power Down",
        summary="Weakens every coin this turn.",
        body=(
            "Each coin loses Final Power equal to Power Down's **Potency** (never below 0). Lasts for the turn."
        ),
    ),
    HelpEntry(
        key="fragile",
        title="Fragile",
        summary="You take more damage this turn.",
        body=(
            "You take **10% more damage per Count**, up to 10 Count (+100%). **Slash, Pierce and Blunt Fragility** work the same but only against that damage type. All last for the turn and add together."
        ),
    ),
    HelpEntry(
        key="damage_down",
        title="Damage Down",
        summary="You deal less damage this turn.",
        body=(
            "Your hits deal **10% less damage per Count**, up to 10 Count. Lasts for the turn."
        ),
    ),
    HelpEntry(
        key="paralyze",
        title="Paralyze",
        summary="Your first coins deal nothing this turn.",
        body=(
            "The **first X coins** of each skill you use have their power fixed to 0, where X is Paralyze's **Count**. Lasts for the turn."
        ),
    ),
    HelpEntry(
        key="poison",
        title="Poison",
        summary="Damage at turn end, then it halves.",
        body=(
            "At the end of the turn you take fixed damage equal to Poison's **Count**, then the Count is **halved**. It keeps going across turns until it hits 0."
        ),
    ),
    HelpEntry(
        key="immobilized",
        title="Immobilized",
        summary="You can't act.",
        body=(
            "You can't declare any skills, and anything you already declared is dropped when combat starts. It wears off after the round it stops you. If you get Immobilized during combat, you sit out the next round instead."
        ),
    ),
    HelpEntry(
        key="power_up",
        title="Power Up",
        summary="Stronger coins this turn.",
        body=(
            "Each coin gains Final Power equal to Power Up's **Count**. A buff you gain on yourself. Lasts for the turn."
        ),
    ),
    HelpEntry(
        key="haste",
        title="Haste",
        summary="Faster this turn.",
        body=(
            "Every skill slot gains Speed equal to Haste's **Count**. A buff you gain on yourself. Lasts for the turn."
        ),
    ),
    HelpEntry(
        key="damage_up",
        title="Damage Up",
        summary="You deal more damage this turn.",
        body=(
            "Your hits deal **10% more damage per Count**, up to 10 Count. Lasts for the turn. Damage Up, Damage Down, Fragile and Protection all add together into one number."
        ),
    ),
    HelpEntry(
        key="protection",
        title="Protection",
        summary="You take less damage this turn.",
        body=(
            "You take **10% less damage per Count**, up to 10 Count. Lasts for the turn."
        ),
    ),
    HelpEntry(
        key="panic",
        title="Low Morale and Panic",
        summary="What happens at very low Sanity.",
        body=(
            "At **-30 Sanity** you are on **Low Morale**. It has no effect on its own, but some characters have skills that care about it. At **-45 Sanity** you **Panic**: you can't act next turn, then your Sanity resets to 0."
        ),
    ),
    HelpEntry(
        key="custom",
        title="Custom statuses",
        summary="Resources a character makes for themselves.",
        body=(
            "A character can have its own statuses (like a Pulsation counter), made with `/character addstatus`. A **buff** is gained by the owner, a **debuff** is inflicted on enemies. Skills spend them with `Consume N Name: payoff`, and some can turn spent Count into permanent Potency. Ask your GM what yours do."
        ),
    ),
]

TRIGGER_SYNTAX: list[HelpEntry] = [
    HelpEntry(
        key="line_shape",
        title="How a line is written",
        summary="Tag, optional condition, one effect.",
        body=(
            "Each line of a skill's trigger box looks like this:\n"
            "`[Timing Tag] condition, effect`\n"
            "The **tag** says when it fires. The **condition** is optional and says "
            "when it is allowed to. The **effect** is what happens. A line holds ONE "
            "effect, so if you want two, write two lines and repeat the tag. "
            "Blank lines are ignored. A tag on its own line (like `[Duality]`) just "
            "switches a skill flag on.\n"
            "Per-coin tags need a coin number first: `:Coin1: [On Hit] ...`.\n"
            "Add `(2 times per turn)` or `(once per turn)` to a line to cap how often it can fire each round."
        ),
    ),
    HelpEntry(
        key="skill_timings",
        title="Skill timings",
        summary="Tags that fire once per skill, no coin number.",
        body=(
            "**Turn Start**, **Combat Start**: every skill a fighter knows fires these, "
            "even if it was not declared (good for passives).\n"
            "**Before Use**, **On Use**: right after the skill is declared and combat begins.\n"
            "**Clash Start**, **Clash Win**, **Clash Lose**: around a Clash.\n"
            "**Before Attack**, **On Unopposed Attack**, **Attack End**, **Turn End**: "
            "before and after the attack itself.\n"
            "**On Evade**, **Before Getting Hit**: when you dodge or get hit.\n"
            "**On Stagger**, **On Kill**, **On Crit Kill**: after a hit that Staggers, "
            "kills, or crit-kills. **Hit After Clash Lose**: for the side that lost a Clash.\n"
            "**Tremor Burst**: cashes in Tremor on the target."
        ),
    ),
    HelpEntry(
        key="coin_timings",
        title="Coin timings",
        summary="Tags that fire for one specific coin.",
        body=(
            "These need a coin number: `:Coin2: [On Hit] Inflict +2 Burn Count`.\n"
            "**Coin Start**: just before that coin is flipped.\n"
            "**On Hit**: when that coin lands. **Heads Hit** / **Tails Hit**: only on that face.\n"
            "**Hit After Clash Win**: only if you won the Clash first.\n"
            "**On Crit**: the coin crits (you hold Poise). **On Crit - Heads Hit** / "
            "**On Crit - Tails Hit**: crit and that face.\n"
            "**Current Coin Attack End**, **Heads Attack End**, **Tails Attack End**: "
            "after that coin's damage is done."
        ),
    ),
    HelpEntry(
        key="conditions",
        title="Conditions",
        summary="Put a rule in front of the effect.",
        body=(
            "Start the effect with a condition and a comma:\n"
            "`If this unit's Speed is faster than the target's,`\n"
            "`If the target has any Burn,` or `If the target has 3+ Burn,`\n"
            "`If this unit's Sanity is 20+,`\n"
            "`At 10+ Speed,` (your slot speed) or `At 5+ Poise,` (a status you hold)\n"
            "`If this unit is Low Morale,` / `If the target is Panic,`\n"
            "`If the target is an ally,` / `If the target is an enemy,` (for Duality skills)\n"
            "`N+` checks **Count** by default. Write `Potency` after the name to check "
            "Potency instead: `At 3+ Poise Potency,`. A custom status with two words "
            "needs the comma: `At 2+ Death Mark, ...`."
        ),
    ),
    HelpEntry(
        key="damage_effects",
        title="Damage, Coin Power, Sanity, Heal",
        summary="The simple number effects.",
        body=(
            "`Deal +4 damage`: flat extra damage on top of the hit.\n"
            "`Coin Power +2`: raises that coin's power.\n"
            "`Gain 5 Sanity`: your Sanity goes up.\n"
            "`Heal 8 HP` or `Heal 10% max HP`: restores HP (up to 100%).\n"
            "Percent damage and chaining effects with `;` are not supported."
        ),
    ),
    HelpEntry(
        key="status_lines",
        title="Inflict, Gain, Remove",
        summary="Put statuses on enemies, yourself, or clear them.",
        body=(
            "**Inflict** puts a status on the target:\n"
            "`Inflict +2 Burn Count` (Potency 1, Count 2)\n"
            "`Inflict 3 Burn` (Potency 3, Count 1)\n"
            "`Inflict Burn Potency 3, Count 2` (both spelled out)\n"
            "**Gain** does the same for yourself, for Poise, Charge, the buffs and your custom buffs: "
            "`Gain +2 Charge Count`, `Gain 2 Poise`, `Gain Pulsation Potency 2, Count 3`.\n"
            "**Remove** clears a status entirely: `Remove Bleed`."
        ),
    ),
    HelpEntry(
        key="consume",
        title="Consume and scaling",
        summary="Spend a resource for a payoff.",
        body=(
            "**Consume** spends Count for an effect: `Consume 3 Pulsation: Coin Power +2`. "
            "If you have less Count than the cost, nothing happens and nothing is spent. "
            "Potency stays.\n"
            "**Scaling** gives a bonus for every few points you hold: "
            "`Coin Power +1 per 3 Pulsation Potency` or `Deal +2 damage per 5 Pulsation Count`. "
            "It works on Coin Power and Deal damage."
        ),
    ),
    HelpEntry(
        key="ally_lines",
        title="Allies",
        summary="Aim an effect at an ally, or react to one.",
        body=(
            "End a line with **to the ally** to put its effect on the ally a `[Duality]` "
            "skill is aimed at: `[On Use] Heal 8 HP to the ally`. Heal, Gain, Gain Sanity "
            "and Remove can do this.\n"
            "**[Ally ...]** tags sit on your own skill and fire when an ally does something: "
            "`[Ally On Hit]`, `[Ally On Crit]`, `[Ally On Kill]`, `[Ally Is Hit]`, "
            "`[Ally On Stagger]`, `[Ally Turn Start]`, `[Ally Combat Start]`. "
            "The effect lands on you unless the line ends with `to the ally`."
        ),
    ),
    HelpEntry(
        key="hints",
        title="Hint tiers",
        summary="How loudly a status shows in the battle display.",
        body=(
            "Add `(Hint 1)`, `(Hint 2)` or `(Hint 3)` anywhere on a line to set how big "
            "the effect looks in the public battle display. Leave it off and the bot works "
            "it out from the size of the status being inflicted. Exact numbers stay private, "
            "use `/battle mystatus` to see your own."
        ),
    ),
    HelpEntry(
        key="examples",
        title="Worked examples",
        summary="Full skills you can copy and change.",
        body=(
            "**A burner:**\n"
            "`:Coin1: [On Hit] Inflict +2 Burn Count`\n"
            "`:Coin2: [On Hit] If the target has 3+ Burn, Deal +3 damage`\n"
            "**A charge fighter:**\n"
            "`[Turn Start] Gain +1 Charge Count`\n"
            "`[On Use] Consume 3 Charge: Coin Power +2`\n"
            "**A medic:**\n"
            "`[Duality]`\n"
            "`[Attack End] If the target is an ally, Heal 10% max HP to the ally (2 times per turn)`\n"
            "**A reaction:**\n"
            "`[Ally Is Hit] Gain 2 Protection`"
        ),
    ),
]


COMMANDS: list[HelpEntry] = [
    HelpEntry(
        key="battle_flow",
        title="Running a battle",
        summary="The order things happen in, start to finish.",
        body=(
            "1. `/battle create` starts a battle in the channel.\n"
            "2. `/battle addfighter` puts each fighter on Side A or B, from a saved "
            "character or as a one-off.\n"
            "3. Fighters need skills: saved ones come with the character, or use "
            "`/battle addskill` for this battle only.\n"
            "4. Each round everyone uses `/battle declare` to lock in skills, in secret.\n"
            "5. `/battle combat` resolves the round once everyone has declared. Speeds "
            "reroll every round, and the bot pings the owners who can act in the new round.\n"
            "6. Repeat 4 and 5 until one side is done, then `/battle end`.\n"
            "Most name and slot options suggest choices as you type."
        ),
    ),
    HelpEntry(
        key="battle_setup",
        title="Battle setup",
        summary="create, addfighter, removefighter.",
        body=(
            "`/battle create` starts a battle. Pick Spar, Standard (Proelium Commune) "
            "or Fatal (Proelium Fatale).\n"
            "`/battle addfighter` adds someone. Give a `character_name` to load a saved "
            "character (best), or a plain `name` for a one-off fighter. You can set the "
            "Speed range the slots roll from.\n"
            "`/battle removefighter` takes a fighter out. Only the fighter's owner or "
            "an admin can."
        ),
    ),
    HelpEntry(
        key="battle_actions",
        title="Declaring and fighting",
        summary="declare, undeclare, combat, log, end, mystatus.",
        body=(
            "`/battle declare` locks a skill into one of your slots, aimed at a slot "
            "on an enemy. Whether it Clashes depends on the enemy aiming back at you. "
            "Duality skills can be aimed at an ally instead.\n"
            "`/battle undeclare` clears one slot so you can change your mind.\n"
            "`/battle combat` resolves the round.\n"
            "`/battle log` shows the recap and full log of a recent round again.\n"
            "`/battle end` closes the battle.\n"
            "`/battle mystatus` shows you, privately, the exact statuses and numbers "
            "on your own fighter."
        ),
    ),
    HelpEntry(
        key="battle_skills",
        title="Battle skills",
        summary="addskill, skills, editskill, removeskill (this battle only).",
        body=(
            "`/battle addskill` opens a form to teach a fighter a skill for THIS "
            "battle only. Use `/character addskill` to keep one forever.\n"
            "`/battle skills` lists what a fighter knows.\n"
            "`/battle editskill` reopens the form with the skill filled in.\n"
            "`/battle removeskill` forgets one.\n"
            "Only the fighter's owner or an admin can add, edit or remove skills."
        ),
    ),
    HelpEntry(
        key="battle_admin",
        title="Admin battle tools",
        summary="setstatus, addpart, removepart, repairpart.",
        body=(
            "Admins only.\n"
            "`/battle setstatus` directly sets a fighter's HP, Sanity, Speed, Power, "
            "resistances, Stagger thresholds, or places one status.\n"
            "`/battle addpart` makes one of a fighter's skill slots a body Part with its "
            "own HP and resistances, for multi-part enemies. `removepart` undoes it and "
            "`repairpart` fixes a broken one. See Parts and Core under Damage Math."
        ),
    ),
    HelpEntry(
        key="character_basics",
        title="Characters",
        summary="create, list, view, edit, resistance, delete.",
        body=(
            "`/character create` makes a saved character (name and optional avatar).\n"
            "`/character list` shows yours. `/character view` shows one's stats, only "
            "to you unless you set `public`.\n"
            "`/character edit` changes only the fields you fill in. Name, avatar and "
            "resistances are yours to change. HP, Speed range, Power and Stagger cost "
            "Build Points, so only an admin can change those (see Build Points and levels).\n"
            "`/character bp` shows a character's level, Build Points and anything to fix.\n"
            "Admins also have `/character grantbp`, `exempt` and `recheck`.\n"
            "`/character resistance` is a shortcut for setting resistances.\n"
            "`/character delete` removes one for good."
        ),
    ),
    HelpEntry(
        key="build_points",
        title="Build Points and levels",
        summary="The one budget that pays for HP, Power, Speed and Stagger.",
        body=(
            "Every character has a pool of **Build Points (BP)**. You start with 30, and "
            "an admin grants more for scenes and events. Your **level** is just a label "
            "on your total: Level 2 at 45 BP, 3 at 63, 4 at 84, 5 at 108.\n"
            "**What costs BP:**\n"
            "- **HP:** 100 is free, up to 160 (about 1 to 8 BP).\n"
            "- **Power:** 6 is free, 1 BP per point, up to 14.\n"
            "- **Speed range:** between 1 and 8, at least 2 wide. The higher the average, the more it costs (up to 16 BP).\n"
            "- **Stagger:** removing Tier 2 costs 5 BP, Tier 3 costs 7, both 12. Tier 1 can never go.\n"
            "**Limits that stop one-sided builds:** no single stat can use more than 40% of "
            "your pool, and Power plus Speed, or Power plus HP, can use at most 60%.\n"
            "**Power sets how strong your skills can be.** A skill's Base Power can be at "
            "most 2 + 0.4 x Power, and its Coin Power at most 1 + 0.2 x Power (rounded down). "
            "At Power 6 that is Base 4 and Coin 2.\n"
            "If a build breaks these rules it is turned away, and a character that breaks "
            "them cannot join a battle until an admin fixes it. You get a message when that happens."
        ),
    ),
    HelpEntry(
        key="character_skills",
        title="Character skills and statuses",
        summary="Permanent skills and your own custom statuses.",
        body=(
            "`/character addskill`, `skills`, `editskill` and `removeskill` work like "
            "the battle versions, but the skills are saved on the character for every "
            "battle.\n"
            "`/character addstatus` creates a custom status or resource for a character "
            "(like Pulsation), with an emoji and rules. `statuses` lists them and "
            "`removestatus` deletes one. Admins see new ones in the log channel. "
            "See Custom statuses under Status Effects."
        ),
    ),
    HelpEntry(
        key="character_say",
        title="Speaking in character",
        summary="/character say posts as your character.",
        body=(
            "`/character say` sends your message using the character's name and avatar, "
            "so it reads like they said it. Handy for roleplay between battle rounds."
        ),
    ),
    HelpEntry(
        key="roll",
        title="Dice and help",
        summary="/roll and /help.",
        body=(
            "`/roll` rolls a dice expression like `1d20+4+2` or `2d6-1`. It shows the "
            "total and the full breakdown to the whole channel.\n"
            "`/help` opens this guidebook."
        ),
    ),
]


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
    (
        "status_effects",
        "Status Effects",
        "What each status does and how long it lasts.",
        STATUS_EFFECTS,
    ),
    (
        "trigger_syntax",
        "Trigger Syntax",
        "How to write the trigger lines on a skill.",
        TRIGGER_SYNTAX,
    ),
    (
        "commands",
        "Commands",
        "What every slash command is for.",
        COMMANDS,
    ),
]
