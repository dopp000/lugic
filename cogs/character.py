import copy
import datetime

import discord
from discord.ext import commands
from discord import app_commands

from game.character import (
    Character,
    save_character,
    load_character,
    character_exists,
    delete_character,
    list_characters_by_owner,
    list_character_summaries,
    list_all_characters,
)
from game.build_points import (
    check_build, build_summary, level_for, next_level_at, character_level,
    DEFENSE_ARCHETYPES, OFFDEF_PERCENT_PER_STEP, PP_MAX_GRANT, passive_points_spent, status_pp_cost, RANKED_STATUSES, defense_label, STARTING_BP, skill_points_spent,
)
from game.resistances import ALL_RESISTANCE_TYPES, DAMAGE_TYPES, parse_resistance_multiplier
from game.emojis import status_emoji, damage_type_emoji, register_custom_status_emoji
from game.passives import MAX_PASSIVES, parse_passive
from game.custom_statuses import (
    CustomStatus, KINDS, LIFETIMES, MAX_CUSTOM_STATUSES_PER_CHARACTER, slugify, validate_name,
)

# Reused rather than duplicated: the skill-creation form (stat parsing,
# 1-coin-only Guard/Evade/Clashable Guard enforcement, the Trigger DSL)
# is the exact same one /battle addskill uses -- this cog just points it
# at a saved Character instead of a battle-scoped Fighter, and tells it
# to also save_character() on confirm. See
# docs/ENGINEERING_NOTES.md#battle-skillconfirmview for why that class
# accepts either kind of target.
import cogs.battle as battle_cog

WEBHOOK_NAME = "Character Proxy"


async def resolve_owner_name(guild: discord.Guild | None, owner_id: int) -> str:
    if guild is None:
        return "Unknown"
    member = guild.get_member(owner_id)
    if member is None:
        try:
            member = await guild.fetch_member(owner_id)
        except discord.NotFound:
            return "Unknown"
    return member.display_name


def build_character_embed(character: Character, owner_name: str) -> discord.Embed:
    embed = discord.Embed(title=character.name)
    if character.avatar_url:
        embed.set_thumbnail(url=character.avatar_url)
    embed.add_field(name="HP", value=f"{character.hp}/{character.max_hp}", inline=True)

    speed_display = (
        f"{character.speed_min}-{character.speed_max}"
        if character.speed_min is not None
        else str(character.speed)
    )
    embed.add_field(name="Speed", value=speed_display, inline=True)
    embed.add_field(name="Power", value=str(character.power), inline=True)

    resist_lines = []
    for k, v in character.resistances.items():
        icon = damage_type_emoji(k) if k in DAMAGE_TYPES else status_emoji(k)
        resist_lines.append(f"{icon} {k.capitalize()}: {v}x")
    embed.add_field(name="Resistances", value="\n".join(resist_lines), inline=False)

    report = check_build(character)
    level = character_level(character)
    build_line = f"Level {level}, {report.spent}/{report.total} BP spent"
    if character.defense_archetypes:
        build_line += "\nDefense: " + ", ".join(defense_label(k) for k in character.defense_archetypes)
    if any(character.status_ranks.values()):
        build_line += "\nStatus ranks: " + ", ".join(f"{k.capitalize()} {v}" for k, v in character.status_ranks.items() if v)
    if character.offense_level or character.defense_level:
        build_line += (
            f"\nOffense Level {character.offense_level} (+{character.offense_level * OFFDEF_PERCENT_PER_STEP}% damage), "
            f"Defense Level {character.defense_level} (-{character.defense_level * OFFDEF_PERCENT_PER_STEP}% damage taken)"
        )
    if character.bp_exempt:
        build_line += " (exempt from Build Point checks)"
    elif not report.ok:
        build_line += f"\n⚠ {len(report.all_problems())} thing(s) to fix, see /character bp"
    embed.add_field(name="Build", value=build_line, inline=False)

    if character.passives:
        embed.add_field(
            name="Passives",
            value="\n".join(
                f"{'Support ' if p.get('kind') == 'support' else ''}**{p['name']}**: " + "; ".join(p.get("lines", []))
                for p in character.passives
            )[:1000],
            inline=False,
        )

    if character.custom_statuses:
        embed.add_field(
            name="Custom statuses",
            value="\n".join(
                f"{status_emoji(cs.key)} {cs.name} ({cs.kind}, {cs.lifetime})"
                for cs in character.custom_statuses.values()
            ),
            inline=False,
        )

    embed.set_footer(text=f"Owner: {owner_name}")
    return embed


def _problem_lines(problems: list[str], limit: int = 8) -> str:
    shown = problems[:limit]
    text = "\n".join(f"- {p}" for p in shown)
    if len(problems) > limit:
        text += f"\n- ...and {len(problems) - limit} more"
    return text


async def dm_owner(bot: commands.Bot, owner_id: int, text: str) -> bool:
    """Sends the owner a DM. Returns False (never raises) when DMs are closed or the user can't be found."""
    try:
        user = bot.get_user(owner_id) or await bot.fetch_user(owner_id)
        await user.send(text[:1900])
        return True
    except (discord.HTTPException, AttributeError):
        return False


def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    return interaction.user.guild_permissions.manage_guild


async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    existing = await channel.webhooks()
    for webhook in existing:
        if webhook.name == WEBHOOK_NAME:
            return webhook
    return await channel.create_webhook(name=WEBHOOK_NAME)


def _parse_resistance_tokens(resistance_types: str, values: str) -> tuple[dict[str, float], str | None]:
    """Parses the paired comma-separated 'resistance_types'/'values' strings
    (e.g. 'slash,burn' + '1.25,0.75') into a {type: value} dict, matched
    positionally. Returns (parsed, error) -- error is None on success, or a
    user-facing message on failure (ignore parsed in that case). Shared by
    /character resistance and the resistance_types/values params on
    /character edit, so both give identical validation and error wording.
    """
    types = [t.strip().lower() for t in resistance_types.split(",")]
    value_tokens = [v.strip() for v in values.split(",")]

    if len(types) != len(value_tokens):
        return {}, (
            f"Got {len(types)} resistance type(s) but {len(value_tokens)} value(s) -- "
            f"these need to line up 1:1, e.g. resistance_types 'slash,burn' values '1.25,0.75'."
        )

    for t in types:
        if t not in ALL_RESISTANCE_TYPES:
            return {}, (
                f"'{t}' isn't a valid resistance type. Choose from: {', '.join(ALL_RESISTANCE_TYPES)}."
            )

    parsed: dict[str, float] = {}
    for t, v in zip(types, value_tokens):
        value, error = parse_resistance_multiplier(v)
        if error:
            return {}, f"Value for {t}: {error}"
        parsed[t] = value

    return parsed, None


class CharacterCog(commands.GroupCog, name="character"):
    """Commands for creating and viewing persistent characters."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="create", description="Create a new character")
    @app_commands.describe(
        name="Character name",
        avatar="Character avatar image",
    )
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        avatar: discord.Attachment | None = None,
    ):
        if character_exists(name):
            await interaction.response.send_message(
                f"A character named {name} already exists.", ephemeral=True
            )
            return

        avatar_url = avatar.url if avatar else None
        # A fresh character starts as a valid Level 1 build: HP 100, Power 6
        # and a 1-3 Speed range (a flat Speed 10 would break the Speed 1-8 rule).
        character = Character(
            owner_id=interaction.user.id, name=name, avatar_url=avatar_url,
            speed_min=1, speed_max=3, bp_total=STARTING_BP,
        )
        save_character(character)

        embed = discord.Embed(
            title="Character Created",
            description=f"**{character.name}** is ready to fight.",
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.add_field(
            name="Build Points",
            value=(
                f"You start with {STARTING_BP} BP. Stat changes are made by admins: "
                "ask one to set your HP, Speed, Power and Stagger. See /character bp."
            ),
            inline=False,
        )
        embed.set_footer(text="Use /character view to see stats. You and admins can view them.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="list", description="List your saved characters")
    async def list_characters(self, interaction: discord.Interaction):
        characters = list_characters_by_owner(interaction.user.id)
        if not characters:
            await interaction.response.send_message(
                "You haven't created any characters yet. Use /character create to make one.",
                ephemeral=True,
            )
            return

        lines = [f"- {c.name}" for c in characters]
        embed = discord.Embed(
            title=f"{interaction.user.display_name}'s Characters",
            description="\n".join(lines),
        )
        embed.set_footer(text="Use /character view name:<name> for full details.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="view", description="View a character's stats")
    @app_commands.describe(
        name="Character name",
        public="Show this to everyone in the channel instead of just you",
    )
    async def view(self, interaction: discord.Interaction, name: str, public: bool = False):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        is_owner = interaction.user.id == character.owner_id
        if not (is_owner or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can view its stats.", ephemeral=True
            )
            return

        owner_name = await resolve_owner_name(interaction.guild, character.owner_id)
        embed = build_character_embed(character, owner_name)
        await interaction.response.send_message(embed=embed, ephemeral=not public)

    @app_commands.command(name="edit", description="Edit a character (only fill in what you want to change; HP, Speed, Power, Stagger and resistances are admin only)")
    @app_commands.describe(
        name="Character to edit",
        new_name="Rename the character",
        avatar="New avatar image",
        hp="New max HP, also heals to full (admin only, costs Build Points)",
        speed_min="Lowest a skill slot's Speed can roll each round (needs speed_max too)",
        speed_max="Highest a skill slot's Speed can roll each round (needs speed_min too)",
        power="New Power (admin only, costs Build Points)",
        resistance_types="Resistance(s) to set, e.g. 'slash,burn' (admin only, needs values too)",
        values="Multiplier for each type above, same order, comma-separated, e.g. '1.25,0.75' (needs resistance_types too)",
        stagger_thresholds="3 HP% values for Tier 1/2/3, comma-separated, descending, e.g. '55,40,25'",
        stagger_disabled_tiers="Which Stagger tiers to strip (2 and/or 3, comma-separated; Tier 1 can't be removed), or 'none'",
        offense_level="Offense Level 0 to 5: 4% more damage dealt per step (admin only, costs Build Points)",
        defense_level="Defense Level 0 to 5: 4% less damage taken per step (admin only, costs Build Points)",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: str | None = None,
        avatar: discord.Attachment | None = None,
        hp: int | None = None,
        speed_min: int | None = None,
        speed_max: int | None = None,
        power: int | None = None,
        resistance_types: str | None = None,
        values: str | None = None,
        stagger_thresholds: str | None = None,
        stagger_disabled_tiers: str | None = None,
        offense_level: app_commands.Range[int, 0, 5] | None = None,
        defense_level: app_commands.Range[int, 0, 5] | None = None,
    ):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        is_owner = interaction.user.id == character.owner_id
        if not (is_owner or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can edit it.", ephemeral=True
            )
            return

        if (speed_min is None) != (speed_max is None):
            await interaction.response.send_message(
                "speed_min and speed_max must be set together -- provide both, or neither.",
                ephemeral=True,
            )
            return

        build_fields_used = any(
            v is not None
            for v in (hp, speed_min, power, stagger_thresholds, stagger_disabled_tiers, resistance_types,
                      offense_level, defense_level)
        )
        if build_fields_used and not is_admin(interaction):
            await interaction.response.send_message(
                "HP, Speed, Power, Stagger, resistances and Offense/Defense Level are part of the build, so only an admin "
                "can change them. Ask an admin to make the change for you.",
                ephemeral=True,
            )
            return

        if (resistance_types is None) != (values is None):
            await interaction.response.send_message(
                "resistance_types and values must be set together -- provide both, or neither.",
                ephemeral=True,
            )
            return

        parsed_resistances: dict[str, int] = {}
        if resistance_types is not None:
            parsed_resistances, error = _parse_resistance_tokens(resistance_types, values)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return

        # Same up-front, all-or-nothing validation pattern as
        # resistances above, and as /battle setstatus's own
        # stagger_thresholds/stagger_disabled_tiers -- parsed and
        # checked before anything gets mutated.
        parsed_stagger_thresholds: list[float] | None = None
        if stagger_thresholds is not None:
            parts = [p.strip() for p in stagger_thresholds.split(",")]
            if len(parts) != 3:
                await interaction.response.send_message(
                    "stagger_thresholds needs exactly 3 comma-separated values (Tier 1/2/3), "
                    "e.g. '55,40,25'.",
                    ephemeral=True,
                )
                return
            try:
                pct_values = [int(p) for p in parts]
            except ValueError:
                await interaction.response.send_message(
                    "stagger_thresholds values must be whole numbers (percentages).",
                    ephemeral=True,
                )
                return
            if not (pct_values[0] > pct_values[1] > pct_values[2]):
                await interaction.response.send_message(
                    f"stagger_thresholds must be strictly descending (Tier 1 > Tier 2 > Tier 3), "
                    f"got {pct_values[0]}, {pct_values[1]}, {pct_values[2]}.",
                    ephemeral=True,
                )
                return
            parsed_stagger_thresholds = [v / 100 for v in pct_values]

        parsed_disabled_tiers: set[int] | None = None
        if stagger_disabled_tiers is not None:
            if stagger_disabled_tiers.strip().lower() == "none":
                parsed_disabled_tiers = set()
            else:
                try:
                    parsed_disabled_tiers = {
                        int(t.strip()) for t in stagger_disabled_tiers.split(",") if t.strip()
                    }
                except ValueError:
                    await interaction.response.send_message(
                        "stagger_disabled_tiers must be whole numbers (2 and/or 3), or 'none'.",
                        ephemeral=True,
                    )
                    return
                if 1 in parsed_disabled_tiers:
                    await interaction.response.send_message(
                        "Tier 1 Stagger can never be removed.", ephemeral=True
                    )
                    return
                if not parsed_disabled_tiers.issubset({2, 3}):
                    await interaction.response.send_message(
                        "stagger_disabled_tiers can only contain 2 and/or 3.", ephemeral=True
                    )
                    return

        # Build Point check on a COPY, before anything real changes: a build
        # that breaks the rules is turned away and nothing is saved.
        if build_fields_used and not character.bp_exempt:
            trial = copy.deepcopy(character)
            if hp is not None:
                trial.hp = trial.max_hp = hp
            if speed_min is not None:
                trial.speed_min, trial.speed_max = speed_min, speed_max
            if power is not None:
                trial.power = power
            if parsed_disabled_tiers is not None:
                trial.stagger_tiers_enabled = [
                    True, 2 not in parsed_disabled_tiers, 3 not in parsed_disabled_tiers,
                ]
            if parsed_resistances:
                trial.resistances.update(parsed_resistances)
            if offense_level is not None:
                trial.offense_level = offense_level
            if defense_level is not None:
                trial.defense_level = defense_level
            trial_report = check_build(trial)
            before_problems = check_build(character).problems
            fresh = [p for p in trial_report.problems if p not in before_problems]
            # A change may leave an old problem standing, but it may not add a
            # new one unless it also fixes at least as many.
            if trial_report.problems and fresh and len(trial_report.problems) >= len(before_problems):
                await interaction.response.send_message(
                    f"That build breaks the Build Point rules, so nothing was changed:\n"
                    f"{_problem_lines(trial_report.problems)}\n"
                    f"(This build costs {trial_report.spent} of {trial_report.total} BP. "
                    f"An admin can add BP with /character grantbp.)",
                    ephemeral=True,
                )
                return

        changes = []

        if new_name is not None and new_name != character.name:
            if character_exists(new_name):
                await interaction.response.send_message(
                    f"A character named {new_name} already exists.", ephemeral=True
                )
                return
            delete_character(character.name)
            changes.append(f"name -> {new_name}")
            character.name = new_name

        if avatar is not None:
            character.avatar_url = avatar.url
            changes.append("avatar updated")

        if hp is not None:
            character.hp = hp
            character.max_hp = hp
            changes.append(f"HP -> {hp}")

        if speed_min is not None:
            character.speed_min = speed_min
            character.speed_max = speed_max
            changes.append(f"Speed range -> {character.speed_min}-{character.speed_max}")

        if power is not None:
            character.power = power
            changes.append(f"Power -> {power}")

        if offense_level is not None:
            character.offense_level = offense_level
            changes.append(f"Offense Level -> {offense_level} (+{offense_level * OFFDEF_PERCENT_PER_STEP}% damage dealt)")

        if defense_level is not None:
            character.defense_level = defense_level
            changes.append(f"Defense Level -> {defense_level} (-{defense_level * OFFDEF_PERCENT_PER_STEP}% damage taken)")

        if parsed_resistances:
            character.resistances.update(parsed_resistances)
            summary = ", ".join(f"{t.capitalize()} {v}x" for t, v in parsed_resistances.items())
            changes.append(f"resistances -> {summary}")

        if parsed_stagger_thresholds is not None:
            character.stagger_thresholds = parsed_stagger_thresholds
            changes.append(
                f"Stagger thresholds -> {pct_values[0]}%/{pct_values[1]}%/{pct_values[2]}%"
            )

        if parsed_disabled_tiers is not None:
            character.stagger_tiers_enabled = [
                True,
                2 not in parsed_disabled_tiers,
                3 not in parsed_disabled_tiers,
            ]
            enabled_desc = ", ".join(
                f"Tier {t}" for t in (1, 2, 3) if t not in parsed_disabled_tiers
            )
            changes.append(f"Stagger tiers active -> {enabled_desc}")

        if not changes:
            await interaction.response.send_message(
                "Nothing to change, no fields were provided.", ephemeral=True
            )
            return

        save_character(character)
        await interaction.response.send_message(
            f"Updated {character.name}: {', '.join(changes)}.", ephemeral=True
        )
        if build_fields_used and interaction.user.id != character.owner_id:
            await dm_owner(
                self.bot, character.owner_id,
                f"An admin changed your character **{character.name}**: {', '.join(changes)}.",
            )

    @app_commands.command(
        name="resistance",
        description="Set one or more of a character's resistances (admin only)",
    )
    @app_commands.describe(
        name="Character name",
        resistance_types="Which resistance(s) to set, comma-separated for several, e.g. 'slash,burn'",
        values="Multiplier for each type above, same order, comma-separated, e.g. '1.25,0.75'. "
        "1.0 is Normal, steps of 0.25; above 1.0 takes more damage, below takes less, 0 is immune",
    )
    async def resistance(
        self,
        interaction: discord.Interaction,
        name: str,
        resistance_types: str,
        values: str,
    ):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        if not is_admin(interaction):
            await interaction.response.send_message(
                "Resistances are part of the build, so only an admin can change them. "
                "Ask an admin to make the change for you.",
                ephemeral=True,
            )
            return

        parsed, error = _parse_resistance_tokens(resistance_types, values)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        if not character.bp_exempt:
            trial = copy.deepcopy(character)
            trial.resistances.update(parsed)
            now = check_build(trial).problems
            before = check_build(character).problems
            if [p for p in now if p not in before] and len(now) >= len(before):
                await interaction.response.send_message(
                    "That would break the Build Point rules, so nothing was changed:\n"
                    f"{_problem_lines(now)}",
                    ephemeral=True,
                )
                return

        character.resistances.update(parsed)
        save_character(character)

        summary = ", ".join(f"{t.capitalize()} {v}x" for t, v in parsed.items())
        report = check_build(character)
        await interaction.response.send_message(
            f"Set {character.name}'s resistances: {summary}. Now {report.spent}/{report.total} BP spent.",
            ephemeral=True,
        )
        if interaction.user.id != character.owner_id and not character.bp_exempt:
            await dm_owner(
                self.bot, character.owner_id,
                f"An admin changed the resistances on **{character.name}** ({summary}). "
                f"Now {report.spent}/{report.total} BP spent.",
            )

    @app_commands.command(
        name="addskill",
        description="Teach this character a new skill, saved permanently (opens a form)",
    )
    @app_commands.describe(name="Which character learns this skill")
    async def addskill(self, interaction: discord.Interaction, name: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        is_owner = interaction.user.id == character.owner_id
        if not (is_owner or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can teach it a skill.", ephemeral=True
            )
            return

        await interaction.response.send_modal(
            battle_cog.AddSkillModal(character, on_saved=lambda: save_character(character))
        )

    @app_commands.command(name="skills", description="Show every skill this character permanently knows")
    @app_commands.describe(name="Character to check")
    async def skills(self, interaction: discord.Interaction, name: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        is_owner = interaction.user.id == character.owner_id
        if not (is_owner or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can view its skills.", ephemeral=True
            )
            return

        if not character.skills:
            await interaction.response.send_message(
                f"{character.name} doesn't know any skills yet. Use /character addskill.",
                ephemeral=True,
            )
            return

        blocks = [battle_cog._skill_preview_text(skill, character.custom_statuses) for skill in character.skills.values()]
        text = f"**{character.name}** knows {len(blocks)} skill(s):\n\n" + "\n\n".join(blocks)
        if len(text) > 1900:
            text = text[:1900] + "\n...(truncated, too many/long skills to show at once)"
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(
        name="removeskill",
        description="Remove a skill from a character's known skills, permanently",
    )
    @app_commands.describe(name="Character to remove a skill from", skill_name="Which skill to remove")
    async def removeskill(self, interaction: discord.Interaction, name: str, skill_name: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        is_owner = interaction.user.id == character.owner_id
        if not (is_owner or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can remove one of their skills.",
                ephemeral=True,
            )
            return

        if character.remove_skill(skill_name):
            character.supermove_drawbacks.pop(skill_name.strip().lower(), None)
            save_character(character)
            await interaction.response.send_message(
                f"Removed **{skill_name}** from {character.name}.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"{character.name} doesn't know a skill called '{skill_name}'.", ephemeral=True
            )

    async def _skill_name_autocomplete_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Shared by /character removeskill and /character editskill's skill_name option -- same approach as battle_cog's own version of this. See docs/ENGINEERING_NOTES.md#battle-skill-name-autocomplete for the full rationale."""
        character_name = getattr(interaction.namespace, "name", None)
        if not character_name:
            return []
        character = load_character(character_name)
        if character is None:
            return []
        current_lower = current.lower()
        return [
            app_commands.Choice(name=skill.name, value=skill.name)
            for skill in character.skills.values()
            if current_lower in skill.name.lower()
        ][:25]

    @removeskill.autocomplete("skill_name")
    async def removeskill_skill_name_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._skill_name_autocomplete_choices(interaction, current)

    @app_commands.command(
        name="editskill",
        description="Edit a skill this character already knows, permanently (opens a form)",
    )
    @app_commands.describe(name="Character whose skill to edit", skill_name="Which skill to edit")
    async def editskill(self, interaction: discord.Interaction, name: str, skill_name: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        is_owner = interaction.user.id == character.owner_id
        if not (is_owner or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can edit one of their skills.",
                ephemeral=True,
            )
            return

        existing_skill = character.get_skill(skill_name)
        if existing_skill is None:
            await interaction.response.send_message(
                f"{character.name} doesn't know a skill called '{skill_name}'.", ephemeral=True
            )
            return

        await interaction.response.send_modal(
            battle_cog.EditSkillModal(character, existing_skill, on_saved=lambda: save_character(character))
        )

    @editskill.autocomplete("skill_name")
    async def editskill_skill_name_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._skill_name_autocomplete_choices(interaction, current)

    @app_commands.command(
        name="addstatus",
        description="Create a custom status or resource for a character (e.g. Pulsation)",
    )
    @app_commands.describe(
        name="Which character owns this status",
        status_name="The status's name, letters and spaces only",
        kind="Buff: the character gains it. Debuff: it gets inflicted on enemies",
        lifetime="Persistent: stays until used up. Turn: wiped at every turn end",
        emoji_id="ID of an existing Discord emoji the bot can see (optional)",
        description="What it does, shown to admins and in /character statuses",
        keep_potency="Stay on the fighter at 0 Count while it has Potency (a permanent buff)",
        potency_every="Every this many Count spent adds +1 Potency (0 = off, like Pulsation's 10)",
    )
    @app_commands.choices(
        kind=[app_commands.Choice(name=k.capitalize(), value=k) for k in KINDS],
        lifetime=[app_commands.Choice(name=l.capitalize(), value=l) for l in LIFETIMES],
    )
    async def addstatus(
        self,
        interaction: discord.Interaction,
        name: str,
        status_name: str,
        kind: app_commands.Choice[str],
        lifetime: app_commands.Choice[str],
        emoji_id: str | None = None,
        description: str = "",
        keep_potency: bool = False,
        potency_every: app_commands.Range[int, 0, 99] = 0,
    ):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        if not (interaction.user.id == character.owner_id or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can add a status to it.", ephemeral=True
            )
            return

        error = validate_name(status_name)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        key = slugify(status_name)
        if key in character.custom_statuses:
            await interaction.response.send_message(
                f"{character.name} already has a status called {status_name}. Remove it first to redo it.",
                ephemeral=True,
            )
            return
        if len(character.custom_statuses) >= MAX_CUSTOM_STATUSES_PER_CHARACTER:
            await interaction.response.send_message(
                f"A character can have at most {MAX_CUSTOM_STATUSES_PER_CHARACTER} custom statuses.",
                ephemeral=True,
            )
            return

        pp_cost = status_pp_cost(keep_potency, potency_every)
        if not character.bp_exempt:
            trial_used = passive_points_spent(character) + pp_cost
            if trial_used > character.pp_total:
                await interaction.response.send_message(
                    f"A unique status costs Passive Points (1, +1 if it keeps Potency, +1 if it gains Potency "
                    f"from Count). This one costs {pp_cost}, and {character.name} has "
                    f"{character.pp_total - passive_points_spent(character)} left. An admin gives them with "
                    "/character grantpp, only for GM-approved moments.",
                    ephemeral=True,
                )
                return
        else:
            pp_cost = 0

        parsed_emoji_id = None
        animated = False
        if emoji_id:
            if not emoji_id.strip().isdigit():
                await interaction.response.send_message(
                    "emoji_id must be the number ID of a Discord emoji.", ephemeral=True
                )
                return
            parsed_emoji_id = int(emoji_id.strip())
            emoji = interaction.client.get_emoji(parsed_emoji_id)
            if emoji is None:
                await interaction.response.send_message(
                    "I can't see an emoji with that ID. It has to be from a server the bot is in.",
                    ephemeral=True,
                )
                return
            animated = emoji.animated

        cs = CustomStatus(
            name=status_name.strip(), key=key, kind=kind.value, lifetime=lifetime.value,
            emoji_id=parsed_emoji_id, animated=animated, description=description.strip()[:300],
            keep_potency=keep_potency, potency_every=potency_every,
        )
        character.custom_statuses[key] = cs
        if pp_cost:
            character.status_pp[key] = pp_cost
        save_character(character)
        register_custom_status_emoji(cs)

        log_channel = interaction.client.get_channel(battle_cog.LOG_CHANNEL_ID)
        if log_channel is not None:
            await log_channel.send(
                f"{interaction.user.mention} created a custom status for **{character.name}**: "
                f"{status_emoji(key)} **{cs.name}** ({cs.kind}, {cs.lifetime})\n"
                f"{cs.description or '(no description)'}"
            )

        usage = (
            f"`Gain +2 {cs.name} Count` in a trigger line"
            if cs.kind == "buff" else f"`Inflict +2 {cs.name} Count` in a trigger line"
        )
        await interaction.response.send_message(
            f"Created {status_emoji(key)} **{cs.name}** for {character.name}. Use it with {usage}.",
            ephemeral=True,
        )

    @app_commands.command(name="statuses", description="Show a character's custom statuses")
    @app_commands.describe(name="Character to check")
    async def statuses(self, interaction: discord.Interaction, name: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        if not character.custom_statuses:
            await interaction.response.send_message(
                f"{character.name} has no custom statuses. Use /character addstatus.", ephemeral=True
            )
            return
        lines = [
            f"{status_emoji(cs.key)} **{cs.name}** ({cs.kind}, {cs.lifetime}"
            f"{', keeps Potency' if cs.keep_potency else ''}"
            f"{f', +1 Potency per {cs.potency_every} spent' if cs.potency_every else ''}): "
            f"{cs.description or 'no description'}"
            for cs in character.custom_statuses.values()
        ]
        await interaction.response.send_message(
            f"**{character.name}**'s custom statuses:\n" + "\n".join(lines), ephemeral=True
        )

    @app_commands.command(name="removestatus", description="Remove one of a character's custom statuses")
    @app_commands.describe(name="Character to remove it from", status_name="Which status to remove")
    async def removestatus(self, interaction: discord.Interaction, name: str, status_name: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        if not (interaction.user.id == character.owner_id or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can remove a status.", ephemeral=True
            )
            return
        removed = character.custom_statuses.pop(slugify(status_name), None)
        character.status_pp.pop(slugify(status_name), None)
        if removed is None:
            await interaction.response.send_message(
                f"{character.name} has no custom status called '{status_name}'.", ephemeral=True
            )
            return
        save_character(character)
        log_channel = interaction.client.get_channel(battle_cog.LOG_CHANNEL_ID)
        if log_channel is not None:
            await log_channel.send(
                f"{interaction.user.mention} removed the custom status **{removed.name}** from **{character.name}**."
            )
        await interaction.response.send_message(
            f"Removed **{removed.name}** from {character.name}. Skills that mention it "
            "keep running, but the line has to be changed before they can be edited and re-saved.",
            ephemeral=True,
        )

    @removestatus.autocomplete("status_name")
    async def removestatus_autocomplete(self, interaction: discord.Interaction, current: str):
        character_name = getattr(interaction.namespace, "name", None)
        character = load_character(character_name) if character_name else None
        if character is None:
            return []
        cur = current.lower()
        return [
            app_commands.Choice(name=cs.name, value=cs.name)
            for cs in character.custom_statuses.values() if cur in cs.name.lower()
        ][:25]

    @app_commands.command(name="delete", description="Delete one of your characters")
    @app_commands.describe(name="Character to delete")
    async def delete(self, interaction: discord.Interaction, name: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        is_owner = interaction.user.id == character.owner_id
        if not (is_owner or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can delete it.", ephemeral=True
            )
            return

        delete_character(character.name)
        await interaction.response.send_message(f"Deleted {character.name}.", ephemeral=True)

    @app_commands.command(name="say", description="Speak in character, using this character's name and avatar")
    @app_commands.describe(
        name="Character name",
        message="What to say",
    )
    async def say(self, interaction: discord.Interaction, name: str, message: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        is_owner = interaction.user.id == character.owner_id
        if not (is_owner or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can speak as it.", ephemeral=True
            )
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This only works in a regular text channel.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            webhook = await get_or_create_webhook(interaction.channel)
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to manage webhooks in this channel. "
                "An admin needs to grant me the Manage Webhooks permission.",
                ephemeral=True,
            )
            return

        await webhook.send(
            content=message,
            username=character.name,
            avatar_url=character.avatar_url,
        )
        await interaction.followup.send("Sent.", ephemeral=True)

    @app_commands.command(name="bp", description="See a character's level, Build Points and anything that breaks the rules")
    @app_commands.describe(name="Character to check")
    async def bp(self, interaction: discord.Interaction, name: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        if not (interaction.user.id == character.owner_id or is_admin(interaction)):
            await interaction.response.send_message(
                "Only the owner or an admin can view its Build Points.", ephemeral=True
            )
            return
        report = check_build(character)
        text = f"**{character.name}**\n{build_summary(report)}"
        nxt = next_level_at(report.total)
        text += f"\nBP pool: {report.total}" + (f" (next level at {nxt})" if nxt else " (top level)")
        if character.bp_exempt:
            text += "\nThis character is exempt from Build Point checks."
        elif report.ok:
            text += "\nEverything is within the rules."
        else:
            text += f"\n**To fix:**\n{_problem_lines(report.all_problems(), 12)}"
        if character.pp_total or character.passives or character.status_pp:
            text += f"\nPassive Points: {passive_points_spent(character)}/{character.pp_total} used"
            for p in character.passives:
                label = "Support Passive" if p.get("kind") == "support" else "Passive"
                text += f"\n- {label} {p['name']}: {p.get('cost', 0)} PP" + (f" ({p['note']})" if p.get("note") else "")
            for k, v in character.status_pp.items():
                if k in character.custom_statuses:
                    text += f"\n- Unique status {character.custom_statuses[k].name}: {v} PP"
        if character.defense_archetypes:
            text += "\nDefense archetypes: " + ", ".join(defense_label(k) for k in character.defense_archetypes)
        if character.status_ranks:
            text += "\nStatus ranks: " + ", ".join(f"{k.capitalize()} {v}" for k, v in character.status_ranks.items() if v)
        text += f"\nSkill Points: {skill_points_spent(character)}/{character.sp_total} used (the first trigger line on a skill is free)"
        notes = [
            f"- {k}: {', '.join(t.capitalize() for t in d.get('tiers', []))}" + (f" ({d['note']})" if d.get("note") else "")
            for k, d in character.supermove_drawbacks.items() if d.get("tiers")
        ]
        if notes:
            text += "\n**Supermove drawbacks:**\n" + "\n".join(notes)
        if character.bp_log:
            recent = character.bp_log[-5:]
            text += "\n**Recent BP changes:**\n" + "\n".join(
                f"- {e.get('amount', 0):+d} BP: {e.get('reason') or 'no reason given'}" for e in recent
            )
        await interaction.response.send_message(text[:1900], ephemeral=True)

    @app_commands.command(name="grantbp", description="Admin only: give a character Build Points (or take some away)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        name="Character to change",
        amount="Build Points to add (use a negative number to remove)",
        reason="Why, e.g. the scene or event (shown in the history and sent to the owner)",
    )
    async def grantbp(self, interaction: discord.Interaction, name: str, amount: int, reason: str | None = None):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can change Build Points.", ephemeral=True)
            return
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        new_total = max(0, character.bp_total + amount)
        applied = new_total - character.bp_total
        character.bp_total = new_total
        character.bp_log.append({
            "amount": applied,
            "reason": reason or "",
            "by": interaction.user.id,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        })
        save_character(character)
        report = check_build(character)
        msg = (
            f"{character.name} now has {report.total} BP (Level {report.level}), "
            f"{report.spent} spent."
        )
        owner_text = (
            f"Your character **{character.name}** {'gained' if applied >= 0 else 'lost'} "
            f"{abs(applied)} BP" + (f" ({reason})" if reason else "") +
            f". Now Level {report.level}, {report.spent}/{report.total} BP spent."
        )
        if report.problems and not character.bp_exempt:
            msg += f"\nThe build is now over the rules:\n{_problem_lines(report.problems)}"
            owner_text += (
                f"\nYour build now breaks the rules and needs adjusting:\n{_problem_lines(report.problems)}\n"
                "Ask an admin to change your stats. Until it is fixed this character cannot join battles."
            )
        await interaction.response.send_message(msg, ephemeral=True)
        if interaction.user.id != character.owner_id:
            await dm_owner(self.bot, character.owner_id, owner_text)

    @app_commands.command(name="rank", description="Admin only: set a character's rank (0 to 5) in a status like Bleed or Burn")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(status=[app_commands.Choice(name=s.capitalize(), value=s) for s in RANKED_STATUSES])
    @app_commands.describe(
        name="Character to change",
        status="Which status",
        rank="0 removes it. Rank 1 to 5 costs 1 / 2 / 4 / 7 / 11 BP, and a character can own at most 2 statuses",
    )
    async def rank(
        self, interaction: discord.Interaction, name: str,
        status: app_commands.Choice[str], rank: app_commands.Range[int, 0, 5],
    ):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Status ranks are part of the build, so only an admin can change them.", ephemeral=True
            )
            return
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        if not character.bp_exempt:
            trial = copy.deepcopy(character)
            if rank:
                trial.status_ranks[status.value] = rank
            else:
                trial.status_ranks.pop(status.value, None)
            now = check_build(trial).problems
            before = check_build(character).problems
            if [p for p in now if p not in before] and len(now) >= len(before):
                await interaction.response.send_message(
                    "That would break the Build Point rules, so nothing was changed:\n"
                    f"{_problem_lines(now)}",
                    ephemeral=True,
                )
                return
        if rank:
            character.status_ranks[status.value] = rank
        else:
            character.status_ranks.pop(status.value, None)
        save_character(character)
        report = check_build(character)
        what = f"{status.name} rank {rank}" if rank else f"no {status.name} rank"
        await interaction.response.send_message(
            f"{character.name} now has {what}. {report.spent}/{report.total} BP spent.", ephemeral=True
        )
        if interaction.user.id != character.owner_id and not character.bp_exempt:
            await dm_owner(
                self.bot, character.owner_id,
                f"An admin set **{character.name}** to {what}. Now {report.spent}/{report.total} BP spent.",
            )

    @app_commands.command(name="archetype", description="Admin only: give or take away a defense archetype (Guard, Evade, Counter...)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(archetype=[app_commands.Choice(name=defense_label(k), value=k) for k in DEFENSE_ARCHETYPES])
    @app_commands.describe(
        name="Character to change",
        archetype="Guard 3 BP, Evade 4, Counter 5, Clashable Guard 6, Clashable Counter 7 (at most 2 owned)",
        own="True to give it, false to take it away",
    )
    async def archetype(
        self, interaction: discord.Interaction, name: str,
        archetype: app_commands.Choice[str], own: bool = True,
    ):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Defense archetypes are part of the build, so only an admin can change them.", ephemeral=True
            )
            return
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        def apply(target):
            owned = [k for k in target.defense_archetypes if k != archetype.value]
            if own:
                owned.append(archetype.value)
            target.defense_archetypes = owned

        if not character.bp_exempt:
            trial = copy.deepcopy(character)
            apply(trial)
            now = check_build(trial).problems
            before = check_build(character).problems
            if [p for p in now if p not in before] and len(now) >= len(before):
                await interaction.response.send_message(
                    "That would break the Build Point rules, so nothing was changed:\n"
                    f"{_problem_lines(now)}",
                    ephemeral=True,
                )
                return
        apply(character)
        save_character(character)
        report = check_build(character)
        what = f"the {archetype.name} archetype" if own else f"no {archetype.name} archetype"
        await interaction.response.send_message(
            f"{character.name} now has {what}. {report.spent}/{report.total} BP spent.", ephemeral=True
        )
        if interaction.user.id != character.owner_id and not character.bp_exempt:
            await dm_owner(
                self.bot, character.owner_id,
                f"An admin gave **{character.name}** {what}. Now {report.spent}/{report.total} BP spent.",
            )

    @app_commands.command(name="grantpp", description="Admin only: give a character Passive Points for a GM-approved moment")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        name="Character to change",
        amount="1 for a solid character-beat scene, 2 for an arc-culminating moment (max 2 per event). Negative removes",
        reason="The moment that earned it (required, shown in the history and sent to the owner)",
    )
    async def grantpp(self, interaction: discord.Interaction, name: str, amount: int, reason: str):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can change Passive Points.", ephemeral=True)
            return
        if amount > PP_MAX_GRANT:
            await interaction.response.send_message(
                f"One event gives at most {PP_MAX_GRANT} Passive Points. Grant {PP_MAX_GRANT} now, and again for another event.",
                ephemeral=True,
            )
            return
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        new_total = max(0, character.pp_total + amount)
        applied = new_total - character.pp_total
        character.pp_total = new_total
        character.pp_log.append({
            "amount": applied,
            "reason": reason,
            "by": interaction.user.id,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        })
        save_character(character)
        used = passive_points_spent(character)
        msg = f"{character.name} now has {new_total} Passive Points, {used} used."
        if used > new_total:
            msg += "\nThey use more than they have, so the build is flagged until a passive or status is removed or more PP are granted."
        await interaction.response.send_message(msg, ephemeral=True)
        if interaction.user.id != character.owner_id:
            await dm_owner(
                self.bot, character.owner_id,
                f"Your character **{character.name}** {'gained' if applied >= 0 else 'lost'} "
                f"{abs(applied)} Passive Points ({reason}). Now {used}/{new_total} used.",
            )

    @app_commands.command(name="passive", description="Admin only: give a character an identity passive (always-on rules), paid with Passive Points")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(kind=[
        app_commands.Choice(name="Passive", value="passive"),
        app_commands.Choice(name="Support Passive", value="support"),
    ])
    @app_commands.describe(
        name="Character",
        passive_name="Name of the passive",
        kind="Passive or Support Passive (a label, they work the same)",
        cost="Passive Points it costs (1 to 6), your call as GM",
        line_1="First rule, e.g. [Turn Start] Gain 1 Charge Count. Timings: Turn Start, Combat Start, Ally ..., On Evade, Self Is Hit, Self On Stagger, Self On Kill",
        line_2="Second rule (optional)",
        line_3="Third rule (optional)",
        note="What the passive is, for the record",
    )
    async def passive(
        self, interaction: discord.Interaction, name: str, passive_name: str,
        kind: app_commands.Choice[str], cost: app_commands.Range[int, 1, 6], line_1: str,
        line_2: str | None = None, line_3: str | None = None, note: str | None = None,
    ):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can give a passive.", ephemeral=True)
            return
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        title = passive_name.strip()
        if not title or len(title) > 40:
            await interaction.response.send_message("Give the passive a name of 1 to 40 characters.", ephemeral=True)
            return
        if any(p["name"].lower() == title.lower() for p in character.passives):
            await interaction.response.send_message(
                f"{character.name} already has a passive called {title}. Remove it first to redo it.", ephemeral=True
            )
            return
        if len(character.passives) >= MAX_PASSIVES:
            await interaction.response.send_message(
                f"A character can have at most {MAX_PASSIVES} passives.", ephemeral=True
            )
            return
        lines = [l.strip() for l in (line_1, line_2, line_3) if l and l.strip()]
        kinds = {k: cs.kind for k, cs in character.custom_statuses.items()}
        _, error = parse_passive(lines, kinds)
        if error:
            await interaction.response.send_message(error[:1900], ephemeral=True)
            return
        entry = {"name": title, "kind": kind.value, "lines": lines, "cost": cost, "note": (note or "").strip()}
        trial = copy.deepcopy(character)
        trial.passives.append(entry)
        used = passive_points_spent(trial)
        if used > character.pp_total:
            await interaction.response.send_message(
                f"{character.name} has {character.pp_total} Passive Points and this would use {used}. "
                "Grant more with /character grantpp (GM-approved moments only).",
                ephemeral=True,
            )
            return
        character.passives.append(entry)
        save_character(character)
        await interaction.response.send_message(
            f"{character.name} now has the {kind.name} **{title}** ({cost} PP). {used}/{character.pp_total} PP used.",
            ephemeral=True,
        )
        if interaction.user.id != character.owner_id:
            await dm_owner(
                self.bot, character.owner_id,
                f"An admin gave **{character.name}** the {kind.name} **{title}** ({cost} PP)."
                + (f" {entry['note']}" if entry["note"] else ""),
            )

    @app_commands.command(name="removepassive", description="Admin only: take an identity passive away from a character (frees its Passive Points)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(name="Character", passive_name="The passive to remove")
    async def removepassive(self, interaction: discord.Interaction, name: str, passive_name: str):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can remove a passive.", ephemeral=True)
            return
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        keep = [p for p in character.passives if p["name"].lower() != passive_name.strip().lower()]
        if len(keep) == len(character.passives):
            await interaction.response.send_message(f"{character.name} has no passive called {passive_name}.", ephemeral=True)
            return
        character.passives = keep
        save_character(character)
        await interaction.response.send_message(
            f"Removed {passive_name} from {character.name}. {passive_points_spent(character)}/{character.pp_total} PP used.",
            ephemeral=True,
        )

    @removepassive.autocomplete("passive_name")
    async def removepassive_name_autocomplete(self, interaction: discord.Interaction, current: str):
        character = load_character(getattr(interaction.namespace, "name", "") or "")
        if character is None:
            return []
        cur = current.lower()
        return [app_commands.Choice(name=p["name"][:100], value=p["name"]) for p in character.passives if cur in p["name"].lower()][:25]

    @app_commands.command(name="grantsp", description="Admin only: give a character Skill Points (or take some away)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        name="Character to change",
        amount="Skill Points to add (use a negative number to remove)",
        reason="Why, e.g. the scene or event (shown in the history and sent to the owner)",
    )
    async def grantsp(self, interaction: discord.Interaction, name: str, amount: int, reason: str | None = None):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can change Skill Points.", ephemeral=True)
            return
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        new_total = max(0, character.sp_total + amount)
        applied = new_total - character.sp_total
        character.sp_total = new_total
        character.sp_log.append({
            "amount": applied,
            "reason": reason or "",
            "by": interaction.user.id,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        })
        save_character(character)
        used = skill_points_spent(character)
        msg = f"{character.name} now has {new_total} Skill Points, {used} used."
        if used > new_total:
            msg += "\nThey use more than they have. Existing skills keep working, but new trigger lines are refused until the total catches up."
        await interaction.response.send_message(msg, ephemeral=True)
        if interaction.user.id != character.owner_id:
            await dm_owner(
                self.bot, character.owner_id,
                f"Your character **{character.name}** {'gained' if applied >= 0 else 'lost'} "
                f"{abs(applied)} Skill Points" + (f" ({reason})" if reason else "") +
                f". Now {used}/{new_total} used.",
            )

    @app_commands.command(name="drawback", description="Admin only: attach a drawback to a Supermove so it costs fewer Build Points")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        name="Character",
        skill_name="The Supermove. Set this before adding the skill if the price would not fit otherwise",
        first="Drawback size: Minor -2 BP, Moderate -4, Severe -7 (Severe must be the only Supermove)",
        second="A second Minor or Moderate drawback on the same Supermove (optional)",
        note="What the drawback is, so it can be checked later",
    )
    @app_commands.choices(
        first=[app_commands.Choice(name=n.capitalize(), value=n) for n in ("none", "minor", "moderate", "severe")],
        second=[app_commands.Choice(name=n.capitalize(), value=n) for n in ("none", "minor", "moderate")],
    )
    async def drawback(
        self,
        interaction: discord.Interaction,
        name: str,
        skill_name: str,
        first: app_commands.Choice[str],
        second: app_commands.Choice[str] | None = None,
        note: str = "",
    ):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can set a drawback.", ephemeral=True)
            return
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        tiers = [t for t in (first.value, second.value if second else "none") if t != "none"]
        if "severe" in tiers and len(tiers) > 1:
            await interaction.response.send_message(
                "A Severe drawback already takes every slot, so it cannot be combined with another one.",
                ephemeral=True,
            )
            return
        key = skill_name.strip().lower()
        existing = character.skills.get(key)
        if not tiers:
            character.supermove_drawbacks.pop(key, None)
            msg = f"Cleared the drawback on {skill_name}."
        else:
            character.supermove_drawbacks[key] = {"tiers": tiers, "note": note.strip()}
            msg = f"{skill_name} now has: {', '.join(t.capitalize() for t in tiers)}" + (f" ({note.strip()})" if note.strip() else "") + "."
            if existing is None:
                msg += " The character does not know that skill yet, the drawback will apply once it is added."
            elif "supermove" not in existing.tags:
                msg += " That skill is not a Supermove, so this changes nothing yet."
        save_character(character)
        report = check_build(character)
        msg += f"\nNow {report.spent}/{report.total} BP spent."
        if report.problems and not character.bp_exempt:
            msg += f"\nThe build is over the rules:\n{_problem_lines(report.problems)}"
        await interaction.response.send_message(msg[:1900], ephemeral=True)
        if not character.bp_exempt:
            await dm_owner(
                self.bot, character.owner_id,
                f"An admin changed the drawback on **{character.name}**'s {skill_name}. "
                f"Now {report.spent}/{report.total} BP spent." + (f"\n{msg.splitlines()[0]}" if note else ""),
            )

    @drawback.autocomplete("skill_name")
    async def drawback_skill_name_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._skill_name_autocomplete_choices(interaction, current)

    @app_commands.command(name="exempt", description="Admin only: skip Build Point checks for a character (NPCs, bosses)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        name="Character to change",
        exempt="True to skip the checks, false to apply them again",
        level="Level this character fights at, 1 to 10 (0 clears it). Level gaps add Power in clashes",
    )
    async def exempt(self, interaction: discord.Interaction, name: str, exempt: bool, level: app_commands.Range[int, 0, 10] | None = None):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can do this.", ephemeral=True)
            return
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        character.bp_exempt = exempt
        level_note = ""
        if level is not None:
            character.level_override = level or None
            level_note = f" Level set to {level}." if level else " Level now follows the Build Point pool."
        save_character(character)
        await interaction.response.send_message(
            f"{character.name} is {'now exempt from' if exempt else 'no longer exempt from'} Build Point checks.{level_note}",
            ephemeral=True,
        )

    @app_commands.command(name="recheck", description="Admin only: check every character against the Build Point rules and tell owners what to fix")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(notify="Send each owner a DM listing what to fix (default: yes)")
    async def recheck(self, interaction: discord.Interaction, notify: bool = True):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can do this.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        flagged = []
        total = 0
        for character in list_all_characters():
            if character.bp_exempt:
                continue
            total += 1
            report = check_build(character)
            if not report.ok:
                flagged.append((character, report))
        if not flagged:
            await interaction.followup.send(f"Checked {total} characters. All are within the rules.", ephemeral=True)
            return
        lines = []
        for character, report in flagged:
            sent = None
            if notify:
                sent = await dm_owner(
                    self.bot, character.owner_id,
                    f"The Build Point rules changed, and your character **{character.name}** "
                    f"needs adjusting:\n{_problem_lines(report.all_problems(), 10)}\n"
                    "Ask an admin to update your stats and skills. Until then this character "
                    "cannot join battles.",
                )
            tag = "" if sent is None else (" (owner notified)" if sent else " (could not DM the owner)")
            lines.append(f"- {character.name}: {len(report.all_problems())} issue(s){tag}")
        head = f"Checked {total} characters, {len(flagged)} need fixing:\n"
        await interaction.followup.send((head + "\n".join(lines))[:1900], ephemeral=True)

    async def _character_name_autocomplete(self, interaction: discord.Interaction, current: str):
        """Suggests your own saved characters (admins see everyone's). See docs/ENGINEERING_NOTES.md#autocomplete."""
        cur = current.lower()
        see_all = is_admin(interaction)
        names = sorted(
            name for name, owner in list_character_summaries()
            if cur in name.lower() and (see_all or owner == interaction.user.id)
        )
        return [app_commands.Choice(name=n[:100], value=n) for n in names][:25]

    for _command in (
        view, edit, resistance, addskill, skills, removeskill, editskill,
        addstatus, statuses, removestatus, delete, say, bp, grantbp, exempt, drawback,
        rank, archetype, grantsp, grantpp, passive, removepassive,
    ):
        _command.autocomplete("name")(_character_name_autocomplete)
    del _command


async def setup(bot: commands.Bot):
    await bot.add_cog(CharacterCog(bot))