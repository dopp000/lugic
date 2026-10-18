from dataclasses import replace
import asyncio
import copy
import difflib
import random
import re

import discord
from discord.ext import commands
from discord import app_commands

from game.battle import (
    Battle, Fighter, Part, BATTLE_TYPES,
    SANITY_PER_COIN_CLASH_WIN, SANITY_CLASH_LOSS, SANITY_PER_HEADS_UNOPPOSED,
    STAGGER_MULTIPLIERS,
)
from game.custom_statuses import slugify
from game.supermoves import describe_bp, describe_profile, supermove_only_tags, supermove_profile
from game.trigger_text import describe_trigger, damage_range, friendly_error_text
from game.recap import RoundRecord, MAX_HISTORY, take_snapshot, build_recap
from game.skills import Skill, SkillResult, ClashOutcome, resolve_skill, resolve_round_clash, resolve_triggers
from game.conditions import Trigger, TriggerContext, parse_trigger_text, TriggerParseError
from game.character import Character, load_character, list_character_summaries
from game.build_points import (
    OFFDEF_PERCENT_PER_STEP, SP_PER_EXTRA_LINE, check_build, skill_points_spent, skill_power_problem, skill_rank_problems,
    skill_archetype, skill_defense_problem, nonkeyword_grants,
)
from game.passives import PASSIVE_ONLY_TIMINGS
from game.npc_tiers import NPC_TIERS, describe as describe_tier
from game.statuses import (
    StatusInstance, decay_after_trigger, apply_status, INFLICTABLE_STATUSES,
    SETTABLE_STATUSES, ONE_TURN_STATUSES, POTENCY_TURN_STATUSES, fragility_bonus,
    damage_down_penalty, damage_up_bonus, protection_reduction,
    magnitude_to_hint_tier, SANITY_PANIC,
)
from game.resistances import DAMAGE_TYPES, ALL_RESISTANCE_TYPES, DEFAULT_RESISTANCES, apply_resistance, parse_resistance_multiplier
from game.emojis import status_label, status_emoji, coin_emoji, coin_roll_emoji, damage_type_emoji, stat_emoji, skill_slot_emoji, hint_emoji, attack_weight_icons, stagger_tier_emoji, stagger_action_emoji

LOG_CHANNEL_ID = 1538071557560213595  # #bot-combat-logs
ADMIN_ROLE_ID = 1468446442430533737  # can manage any fighter, not just their own

# Clashable Guard tuning knobs -- winning replaces normal clash damage with raising the loser's Stagger thresholds by this many percentage... See docs/ENGINEERING_NOTES.md#battle-comment-23.
GUARD_STAGGER_THRESHOLD_RAISE = 0.10
GUARD_LOSE_DAMAGE_REDUCTION_PCT = 25

# Offset: when a Clash forms between two skills that are BOTH tagged as one of these defense-type tags, the Clash cancels outright -- no... See docs/ENGINEERING_NOTES.md#battle-comment-31.
DEFENSE_TAGS = {"guard", "clashable_guard", "counter", "clashable_counter"}

# Rupture's fixed per-hit bonus damage is capped at this fraction of
# the TARGET's own max HP, regardless of Rupture's stored Potency --
# matches canon Limbus's own cap, so a huge Rupture stack can't one-shot
# through a small-HP-pool fighter.
RUPTURE_DAMAGE_CAP_PCT = 0.10


def _can_manage_fighter(interaction: discord.Interaction, fighter: Fighter) -> bool:
    """True if whoever's invoking this is either the fighter's own linked owner, or holds the server's admin role (ADMIN_ROLE_ID). See docs/ENGINEERING_NOTES.md#battle-can-manage-fighter for the full rationale."""
    if fighter.owner_id is not None and interaction.user.id == fighter.owner_id:
        return True
    if isinstance(interaction.user, discord.Member):
        return any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles)
    return False


def _is_admin(interaction: discord.Interaction) -> bool:
    """True if whoever's invoking this holds the server's admin role (ADMIN_ROLE_ID). Unlike _can_manage_fighter, there's no "it's your own fighter" fallback -- this is for flat admin-only tools like /battle setstatus. See docs/ENGINEERING_NOTES.md#battle-is-admin for the full rationale."""
    if isinstance(interaction.user, discord.Member):
        return any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles)
    return False


def _parse_resistance_list(raw: str) -> tuple[dict[str, float] | None, str | None]:
    """'slash:0.5,pierce:1.5' -> full resistance dict (unlisted types stay 1.0). Returns (dict, error)."""
    result = dict(DEFAULT_RESISTANCES)
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        bits = token.split(":")
        if len(bits) != 2:
            return None, f"'{token}' is invalid. Use 'Type:Value', comma-separated, e.g. 'slash:0.5,pierce:1.5'."
        r_type, r_raw = bits[0].strip().lower(), bits[1].strip()
        if r_type not in ALL_RESISTANCE_TYPES:
            return None, f"'{r_type}' isn't a valid resistance type. Choose from: {', '.join(ALL_RESISTANCE_TYPES)}."
        value, error = parse_resistance_multiplier(r_raw)
        if error:
            return None, f"{r_type}: {error}"
        result[r_type] = value
    return result, None


def _mystatus_text(fighter: Fighter) -> str:
    """Private, exact view of one fighter's own numbers (the battle embed only shows hint tiers). See docs/ENGINEERING_NOTES.md#mystatus."""
    from game.statuses import morale_state

    lines = [f"**{fighter.name}**  HP {fighter.hp}/{fighter.max_hp}  Sanity {fighter.sanity}"]
    morale = morale_state(fighter)
    if morale == "panic":
        lines.append("Panic: cannot act")
    elif morale == "low_morale":
        lines.append("Low Morale")
    if fighter.shield > 0:
        lines.append(f"Shield {fighter.shield}")
    if fighter.current_stagger_tier > 0:
        lines.append(f"Staggered (Tier {fighter.current_stagger_tier})")

    if not fighter.statuses:
        lines.append("No statuses right now.")
    else:
        for key in sorted(fighter.statuses):
            st = fighter.statuses[key]
            cs = fighter.custom_statuses.get(key)
            line = f"{status_emoji(key)} {cs.name if cs else status_label(key)}: Potency {st.potency}, Count {st.count}"
            if cs and cs.description:
                line += f"  ({cs.description})"
            lines.append(line)

    for drain in fighter.sanity_drains:
        lines.append(
            f"E.G.O aftermath ({drain.source}): -{drain.amount} Sanity at each turn end, {drain.turns_left} more turn(s)"
        )
    if fighter.supermove_uses:
        lines.append(
            "Supermoves used: " + ", ".join(f"{n} x{c}" for n, c in sorted(fighter.supermove_uses.items()) if c)
        )

    if fighter.consumed_totals:
        spent = ", ".join(
            f"{(fighter.custom_statuses[k].name if k in fighter.custom_statuses else status_label(k))} {n}"
            for k, n in sorted(fighter.consumed_totals.items())
        )
        lines.append(f"Total spent this battle: {spent}")
    return "\n".join(lines)


class SkillConfirmView(discord.ui.View):
    def __init__(
        self, fighter: Fighter, skill: Skill, preview_text: str, timeout: float = 60,
        on_saved=None, old_skill_name: str | None = None, old_preview_text: str | None = None,
    ):
        super().__init__(timeout=timeout)
        self.fighter = fighter
        self.skill = skill
        self.preview_text = preview_text
        # Optional no-arg callback, run right after add_skill below.
        # /battle addskill leaves this unset (a battle-scoped Fighter has
        # nothing further to persist); /character addskill passes
        # save_character(character) here, so confirming the SAME modal
        # also writes the skill to the character's JSON file, not just
        # into memory for this one battle. See docs/ENGINEERING_NOTES.md#battle-skillconfirmview for the full rationale.
        self.on_saved = on_saved
        # Both set only by EditSkillModal (/battle editskill, /character
        # editskill) -- old_skill_name is the skill's name BEFORE this
        # edit, lowercased same as Fighter.skills' own keys, so Confirm
        # knows to remove that entry too if the edit renamed it (plain
        # add_skill alone would otherwise leave the old name's entry
        # behind, next to the new one, rather than replacing it).
        # old_preview_text is the skill's full preview from BEFORE the
        # edit, so the admin log below can show a before/after instead
        # of just the end result. See docs/ENGINEERING_NOTES.md#battle-skillconfirmview.
        self.old_skill_name = old_skill_name
        self.old_preview_text = old_preview_text

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.old_skill_name is not None and self.old_skill_name != self.skill.name.lower():
            self.fighter.remove_skill(self.old_skill_name)
            drawbacks = getattr(self.fighter, "supermove_drawbacks", None)
            if drawbacks and self.old_skill_name in drawbacks:
                drawbacks[self.skill.name.lower()] = drawbacks.pop(self.old_skill_name)
        self.fighter.add_skill(self.skill)
        if self.on_saved is not None:
            self.on_saved()

        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel is not None:
            if self.old_preview_text is not None:
                await log_channel.send(
                    f"{interaction.user.mention} edited a skill for **{self.fighter.name}**:\n"
                    f"**Before:**\n{self.old_preview_text}\n**After:**\n{self.preview_text}"
                )
            else:
                await log_channel.send(
                    f"{interaction.user.mention} confirmed a new skill for **{self.fighter.name}**:\n"
                    f"{self.preview_text}"
                )

        for child in self.children:
            child.disabled = True
        confirm_text = (
            f"**Confirmed.** {self.fighter.name}'s **{self.skill.name}** skill was updated."
            if self.old_preview_text is not None
            else f"**Confirmed.** {self.fighter.name} learned {self.skill.name}."
        )
        await interaction.response.edit_message(content=confirm_text, view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="Cancelled. No skill was added.",
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


def _skill_preview_text(skill: Skill, custom_names: dict | None = None) -> str:
    coin_bits = []
    for i in range(skill.coins):
        tag = coin_emoji("base")
        if skill.coin_statuses[i]:
            tag += f" ({skill.coin_status_potencies[i]}{status_emoji(skill.coin_statuses[i])}{skill.coin_status_counts[i]})"
        coin_bits.append(tag)
    text = (
        f"**{skill.name}** (Base {skill.base_power}, +{skill.coin_power} Coin Power, "
        f"{skill.coins} coins, {damage_type_emoji(skill.damage_type)} {skill.damage_type.capitalize()})\n"
        + " ".join(coin_bits)
    )
    if skill.attack_weight > 1:
        text += f"\n  Attack Weight: {attack_weight_icons(skill.attack_weight)} ({skill.attack_weight})"
    low, avg, high = damage_range(skill)
    text += f"\n  Expected damage: about {avg} (lowest {low}, highest {high}) before resistances"
    if skill.triggers:
        names = {k: cs.name for k, cs in (custom_names or {}).items()}
        trigger_lines = "\n".join(
            f"  - {describe_trigger(t, names)} ({hint_emoji(t.hint_tier)})" for t in skill.triggers
        )
        text += f"\n{trigger_lines}"
    if skill.tags:
        text += f"\n  Flags: {', '.join(sorted(skill.tags))}"
    if "supermove" in skill.tags:
        text += f"\n  E.G.O Supermove: {describe_profile(supermove_profile(skill))}. Each reuse in a battle costs 50% more."
    return text


def _parse_status_tokens(
    status_input: str, coins: int
) -> tuple[list[str | None], list[int], list[int], str | None]:
    """Parses the comma-separated per-coin status string ('none' or 'Name:Potency:Count' per coin) into three aligned lists. See docs/ENGINEERING_NOTES.md#battle-parse-status-tokens for the full rationale."""
    tokens = [t.strip() for t in status_input.split(",")]
    if len(tokens) == 1 and tokens[0].lower() == "none":
        tokens = ["none"] * coins

    if len(tokens) != coins:
        return [], [], [], (
            f"Statuses needs exactly {coins} comma-separated entries (one per coin), "
            f"got {len(tokens)}. Use 'none' for a coin with no status. "
            f"Example: {','.join(['none'] * coins)}"
        )

    coin_statuses: list[str | None] = []
    coin_status_potencies: list[int] = []
    coin_status_counts: list[int] = []

    for i, token in enumerate(tokens):
        if token.lower() == "none":
            coin_statuses.append(None)
            coin_status_potencies.append(0)
            coin_status_counts.append(0)
            continue

        parts = token.split(":")
        if len(parts) != 3:
            return [], [], [], f"Coin {i + 1} entry '{token}' is invalid. Use 'Name:Potency:Count' or 'none'."

        status_name_raw, potency_raw, count_raw = parts
        try:
            potency = int(potency_raw)
            count = int(count_raw)
        except ValueError:
            return [], [], [], f"Coin {i + 1} entry '{token}' has a non-numeric potency/count."

        coin_statuses.append(status_name_raw.strip().lower())
        coin_status_potencies.append(potency)
        coin_status_counts.append(count)

    return coin_statuses, coin_status_potencies, coin_status_counts, None


def _format_status_tokens(skill: Skill) -> str:
    """The reverse of _parse_status_tokens above, for EditSkillModal's pre-fill. See docs/ENGINEERING_NOTES.md#battle-format-skill-text for the full rationale."""
    tokens = []
    for name, potency, count in zip(
        skill.coin_statuses, skill.coin_status_potencies, skill.coin_status_counts
    ):
        tokens.append("none" if name is None else f"{name.capitalize()}:{potency}:{count}")
    return ",".join(tokens)


def _format_trigger_text(skill: Skill) -> str:
    """The reverse of parse_trigger_text (game/conditions.py), for EditSkillModal's pre-fill. See docs/ENGINEERING_NOTES.md#battle-format-skill-text for the full rationale."""
    lines = [t.raw_text for t in skill.triggers]
    lines += [f"[{tag.replace('_', ' ').title()}]" for tag in sorted(skill.tags)]
    return "\n".join(lines)


class AddSkillModal(discord.ui.Modal, title="New Skill"):
    """The full skill-creation popup: everything /battle addskill used to collect as 6 required slash-command options (base_power, coin_power,... See docs/ENGINEERING_NOTES.md#battle-addskillmodal for the full rationale."""

    skill_name = discord.ui.TextInput(
        label="Skill Name",
        style=discord.TextStyle.short,
        max_length=100,
    )
    stats_input = discord.ui.TextInput(
        label="Base Power, Coin Power, Coins, Damage Type",
        style=discord.TextStyle.short,
        placeholder="5, 5, 3, Blunt",
    )
    attack_weight_input = discord.ui.TextInput(
        label="Attack Weight (optional, default 1)",
        style=discord.TextStyle.short,
        placeholder="1",
        default="1",
        required=False,
    )
    status_input = discord.ui.TextInput(
        label="Per-coin statuses, or 'none'",
        style=discord.TextStyle.short,
        placeholder="none   or   Tremor:2:0,Tremor:2:0,Tremor:2:0",
        default="none",
        required=False,
    )
    triggers_input = discord.ui.TextInput(
        label="Triggers (one per line, blank for none)",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "[On Use] Coin Power +1\n"
            ":Coin1: [On Hit] Inflict Rupture Potency 2, Count 1\n"
            "[Target Fixed]"
        ),
        required=False,
        max_length=4000,
    )

    def __init__(self, target_fighter, on_saved=None):
        super().__init__()
        self.target_fighter = target_fighter
        # Passed straight through to SkillConfirmView -- see its own
        # on_saved docstring. target_fighter is typed loosely (not
        # `Fighter`) on purpose: /character addskill passes a
        # game.character.Character here instead, which this modal never
        # needs to tell apart from a real Fighter -- both have .name,
        # .add_skill(), .get_skill(), .remove_skill(), which is all this
        # class or SkillConfirmView ever touch on it.
        self.on_saved = on_saved

    async def on_submit(self, interaction: discord.Interaction):
        stats_parts = [p.strip() for p in self.stats_input.value.split(",")]
        if len(stats_parts) != 4:
            await interaction.response.send_message(
                "Base Power, Coin Power, Coins, Damage Type needs exactly 4 comma-separated "
                "values, e.g. '5, 5, 3, Blunt'.",
                ephemeral=True,
            )
            return

        base_power_raw, coin_power_raw, coins_raw, damage_type_raw = stats_parts
        try:
            base_power = int(base_power_raw)
            coin_power = int(coin_power_raw)
            coins = int(coins_raw)
        except ValueError:
            await interaction.response.send_message(
                "Base Power, Coin Power, and Coins must all be whole numbers.",
                ephemeral=True,
            )
            return

        if not (1 <= coins <= 4):
            await interaction.response.send_message("Coins must be between 1 and 4.", ephemeral=True)
            return


        damage_type = damage_type_raw.strip().lower()
        if damage_type not in DAMAGE_TYPES:
            close = difflib.get_close_matches(damage_type, DAMAGE_TYPES, n=1, cutoff=0.5)
            guess = f" Did you mean {close[0].capitalize()}?" if close else ""
            await interaction.response.send_message(
                f"'{damage_type_raw}' isn't a valid damage type. Choose one of: "
                f"{', '.join(t.capitalize() for t in DAMAGE_TYPES)}.{guess}",
                ephemeral=True,
            )
            return

        attack_weight_raw = (self.attack_weight_input.value or "1").strip() or "1"
        try:
            attack_weight = int(attack_weight_raw)
        except ValueError:
            await interaction.response.send_message(
                "Attack Weight must be a whole number.", ephemeral=True
            )
            return
        if attack_weight < 1:
            await interaction.response.send_message(
                "Attack Weight must be at least 1 (1 means no splash).", ephemeral=True
            )
            return

        coin_statuses, coin_status_potencies, coin_status_counts, status_error = _parse_status_tokens(
            self.status_input.value or "none", coins
        )
        if status_error:
            await interaction.response.send_message(status_error, ephemeral=True)
            return

        custom_kinds = {k: cs.kind for k, cs in getattr(self.target_fighter, "custom_statuses", {}).items()}
        try:
            triggers, flags = parse_trigger_text(self.triggers_input.value or "", custom_kinds)
        except TriggerParseError as e:
            await interaction.response.send_message(
                friendly_error_text(self.triggers_input.value or "", e, list(custom_kinds))[:1900],
                ephemeral=True,
            )
            return
        if any(t.timing in PASSIVE_ONLY_TIMINGS for t in triggers):
            await interaction.response.send_message(
                "Self Is Hit, Self On Stagger, Self On Kill and Self On Death are only for passives, an admin sets those "
                "with /character passive. They would never fire on a skill.",
                ephemeral=True,
            )
            return

        # Guard, Evade, and Clashable Guard are all fixed to exactly 1
        # coin -- the whole defense-skill cascade (find_eligible_evade,
        # the Clashable Guard/Counter interception passes) assumes one
        # clean win/lose roll per declared defense skill.
        ONE_COIN_ONLY_TAGS = {"guard", "evade", "clashable_guard"}
        offending_tags = flags & ONE_COIN_ONLY_TAGS
        if offending_tags and coins != 1:
            await interaction.response.send_message(
                f"{', '.join(t.replace('_', ' ').title() for t in sorted(offending_tags))} "
                f"must be exactly 1 coin (got {coins}).",
                ephemeral=True,
            )
            return

        # Attack Weight (hitting extra targets) is a Supermove power. Admins and
        # exempt characters (NPCs, bosses) can still use it on any skill.
        if (
            attack_weight > 1
            and "supermove" not in flags
            and not _is_admin(interaction)
            and not getattr(self.target_fighter, "bp_exempt", False)
        ):
            await interaction.response.send_message(
                "Attack Weight above 1 is only for a [Supermove]. Set it to 1, or add "
                "[Supermove] to the trigger box.",
                ephemeral=True,
            )
            return

        if "supermove" in flags and flags & {"duality", "guard", "evade", "clashable_guard", "counter", "clashable_counter"}:
            await interaction.response.send_message(
                "A [Supermove] has to be an attack skill, it can't also be Duality, Guard, Evade or Counter.",
                ephemeral=True,
            )
            return

        is_super = "supermove" in flags
        privileged = _is_admin(interaction) or getattr(self.target_fighter, "bp_exempt", False)

        # Power is the ceiling on a normal skill's numbers (game/build_points.py).
        # A Supermove is priced in Build Points instead. Admins are exempt so
        # they can build enemies and bosses freely.
        if not is_super and not privileged:
            power_issue = skill_power_problem(self.target_fighter.power, base_power, coin_power)
            if power_issue:
                await interaction.response.send_message(
                    f"{power_issue} A higher Power stat raises the limit (an admin sets it).",
                    ephemeral=True,
                )
                return

        # Supermoves are bought with Build Points, so an admin adds them for now.
        if is_super and not privileged:
            await interaction.response.send_message(
                "A [Supermove] is bought with Build Points, so only an admin can add one. "
                "Ask an admin to add it to your character.",
                ephemeral=True,
            )
            return

        draft = Skill(
            name=self.skill_name.value, base_power=base_power, coin_power=coin_power, coins=coins,
            damage_type=damage_type, coin_statuses=coin_statuses,
            coin_status_potencies=coin_status_potencies, coin_status_counts=coin_status_counts,
            triggers=triggers, tags=flags, attack_weight=attack_weight,
        )
        if not is_super and not privileged:
            only = supermove_only_tags(draft)
            if only:
                await interaction.response.send_message(
                    f"{', '.join(only)} can only be used on a [Supermove]. Take it out, or ask an "
                    "admin to make this a Supermove.",
                    ephemeral=True,
                )
                return

        # A saved character has to stay inside its Build Points. Only Supermoves
        # change the price of a skill, so only they need this check.
        old_skill = getattr(self, "existing_skill", None)
        touches_super = is_super or (old_skill is not None and "supermove" in old_skill.tags)
        touches_effects = (not is_super and bool(nonkeyword_grants(draft))) or (
            old_skill is not None and "supermove" not in old_skill.tags and bool(nonkeyword_grants(old_skill))
        )
        if (touches_super or touches_effects) and isinstance(self.target_fighter, Character) and not self.target_fighter.bp_exempt:
            before = {p for p in check_build(self.target_fighter).all_problems()}
            trial = copy.deepcopy(self.target_fighter)
            if old_skill is not None:
                trial.skills.pop(old_skill.name.lower(), None)
            trial.skills[draft.name.lower()] = draft
            fresh = [p for p in check_build(trial).all_problems() if p not in before]
            if fresh:
                await interaction.response.send_message(
                    f"{self.target_fighter.name} cannot take this skill:\n"
                    + "\n".join(f"- {p}" for p in fresh[:6])
                    + "\nGrant more Build Points with /character grantbp, set a drawback first with "
                    "/character drawback, or change the skill.",
                    ephemeral=True,
                )
                return

        # A normal skill can only use the statuses the character has ranks in. Skills
        # saved before this rule are grandfathered: an edit is only refused if it
        # has more rank problems than the skill it replaces. Supermoves skip this.
        if not is_super and not privileged:
            ranks = getattr(self.target_fighter, "status_ranks", None) or {}
            rank_issues = skill_rank_problems(ranks, draft)
            if rank_issues:
                before_issues = skill_rank_problems(ranks, old_skill) if old_skill is not None else []
                if len(rank_issues) > len(before_issues):
                    await interaction.response.send_message(
                        "This skill goes past the character's status ranks:\n"
                        + "\n".join(f"- {p}" for p in rank_issues[:6])
                        + "\nAn admin sets ranks with /character rank (see /help, Build Points).",
                        ephemeral=True,
                    )
                    return

        # A Guard / Evade / Counter style skill needs the matching defense archetype.
        # Skills saved before this rule are grandfathered: only a new skill, or an
        # edit that switches to a different archetype, is checked.
        if not is_super and not privileged:
            owned_def = getattr(self.target_fighter, "defense_archetypes", None) or []
            def_issue = skill_defense_problem(owned_def, draft)
            if def_issue and (old_skill is None or skill_archetype(old_skill) != skill_archetype(draft)):
                await interaction.response.send_message(
                    f"{def_issue} An admin buys it with /character archetype (see /help, Build Points).",
                    ephemeral=True,
                )
                return

        # Extra trigger lines are bought with Skill Points (admins grant them).
        # Skills saved before this rule are grandfathered: only a change that
        # raises the Skill Points used past what the character has is refused.
        if isinstance(self.target_fighter, Character) and not self.target_fighter.bp_exempt:
            used_before = skill_points_spent(self.target_fighter)
            sp_trial = copy.deepcopy(self.target_fighter)
            if old_skill is not None:
                sp_trial.skills.pop(old_skill.name.lower(), None)
            sp_trial.skills[draft.name.lower()] = draft
            used_after = skill_points_spent(sp_trial)
            if used_after > used_before and used_after > self.target_fighter.sp_total:
                await interaction.response.send_message(
                    f"This skill has {len(draft.triggers)} trigger lines. The first line is free and each "
                    f"extra line costs {SP_PER_EXTRA_LINE} Skill Point. {self.target_fighter.name} would use "
                    f"{used_after} but has {self.target_fighter.sp_total}. An admin can grant more with "
                    "/character grantsp, or remove a trigger line.",
                    ephemeral=True,
                )
                return

        skill = Skill(
            name=self.skill_name.value,
            base_power=base_power,
            coin_power=coin_power,
            coins=coins,
            damage_type=damage_type,
            coin_statuses=coin_statuses,
            coin_status_potencies=coin_status_potencies,
            coin_status_counts=coin_status_counts,
            triggers=triggers,
            tags=flags,
            attack_weight=attack_weight,
        )

        preview_text = self._preview_header(skill) + "\n" + _skill_preview_text(
            skill, getattr(self.target_fighter, "custom_statuses", None)
        )
        if is_super:
            drawback = (getattr(self.target_fighter, "supermove_drawbacks", None) or {}).get(skill.name.lower(), {})
            preview_text += f"\nBuild Points: {describe_bp(skill, drawback.get('tiers', []))}"
        view = self._build_confirm_view(skill, preview_text)
        await interaction.response.send_message(preview_text, view=view, ephemeral=True)

    def _preview_header(self, skill: Skill) -> str:
        """Overridden by EditSkillModal. See docs/ENGINEERING_NOTES.md#battle-addskillmodal-hooks for the full rationale."""
        return f"{self.target_fighter.name} would learn {self.skill_name.value}"

    def _build_confirm_view(self, skill: Skill, preview_text: str) -> "SkillConfirmView":
        """Overridden by EditSkillModal. See docs/ENGINEERING_NOTES.md#battle-addskillmodal-hooks for the full rationale."""
        return SkillConfirmView(self.target_fighter, skill, preview_text, on_saved=self.on_saved)


class EditSkillModal(AddSkillModal):
    """Same popup as AddSkillModal, pre-filled from a skill the fighter already knows. See docs/ENGINEERING_NOTES.md#battle-editskillmodal for the full rationale."""

    def __init__(self, target_fighter, existing_skill: Skill, on_saved=None):
        super().__init__(target_fighter, on_saved=on_saved)
        self.title = "Edit Skill"
        self.existing_skill = existing_skill

        self.skill_name.default = existing_skill.name
        self.stats_input.default = (
            f"{existing_skill.base_power}, {existing_skill.coin_power}, "
            f"{existing_skill.coins}, {existing_skill.damage_type.capitalize()}"
        )
        self.attack_weight_input.default = str(existing_skill.attack_weight)
        self.status_input.default = _format_status_tokens(existing_skill)
        self.triggers_input.default = _format_trigger_text(existing_skill)

    def _preview_header(self, skill: Skill) -> str:
        return f"{self.target_fighter.name}'s **{self.existing_skill.name}** would become:"

    def _build_confirm_view(self, skill: Skill, preview_text: str) -> "SkillConfirmView":
        old_preview_text = (
            f"{self.target_fighter.name}'s {self.existing_skill.name} (before)\n"
            + _skill_preview_text(self.existing_skill, getattr(self.target_fighter, "custom_statuses", None))
        )
        return SkillConfirmView(
            self.target_fighter, skill, preview_text, on_saved=self.on_saved,
            old_skill_name=self.existing_skill.name.lower(), old_preview_text=old_preview_text,
        )


# Discord silently rejects an ENTIRE modal with a 400 (which the caller only ever sees as a generic "The application did not respond") if... See docs/ENGINEERING_NOTES.md#battle-comment-272.
def _check_modal_field_limits(modal_cls: type[discord.ui.Modal]) -> None:
    for field_name in dir(modal_cls):
        field = getattr(modal_cls, field_name, None)
        if not isinstance(field, discord.ui.TextInput):
            continue
        label = field.label or ""
        if len(label) > 45:
            raise ValueError(
                f"{modal_cls.__name__}.{field_name} label is {len(label)} chars "
                f"(Discord's limit is 45): {label!r}"
            )
        placeholder = field.placeholder or ""
        if len(placeholder) > 100:
            raise ValueError(
                f"{modal_cls.__name__}.{field_name} placeholder is {len(placeholder)} chars "
                f"(Discord's limit is 100): {placeholder!r}"
            )


_check_modal_field_limits(AddSkillModal)


class ClashDeclareView(discord.ui.View):
    def __init__(
        self,
        caster: Fighter,
        skill: Skill,
        target: Fighter,
        slot: int,
        target_slot: int,
        bot: commands.Bot,
        battle: Battle,
        timeout: float = 60,
        extra_targets: list[tuple[Fighter, int]] | None = None,
    ):
        super().__init__(timeout=timeout)
        self.caster = caster
        self.skill = skill
        self.target = target
        self.slot = slot
        self.target_slot = target_slot
        self.bot = bot
        self.battle = battle
        self.extra_targets = extra_targets or []

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        profile, _ = self.caster.supermove_check(self.slot, self.skill)
        if not self.caster.declare_in_slot(
            self.slot, self.skill, self.target, self.target_slot, self.extra_targets
        ):
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content=f"{self.caster.name} no longer has the Sanity to use **{self.skill.name}**.",
                view=self,
            )
            self.stop()
            return

        for child in self.children:
            child.disabled = True
        sm_note = f"\nE.G.O: {self.skill.name} {describe_profile(profile)}. Paid." if profile else ""
        extra_note = (
            f" (Attack Weight {attack_weight_icons(self.skill.attack_weight)} also reaches " + ", ".join(
                f"{f.name} Slot {s}" for f, s in self.extra_targets
            ) + ")"
            if self.extra_targets else ""
        )
        await interaction.response.edit_message(
            content=(
                f"**Locked in.** {self.caster.name}'s Slot {self.slot} ({self.skill.name}) "
                f"targets {self.target.name}'s Slot {self.target_slot}{extra_note}.{sm_note}\n-# Only you can see this. Others see that you declared, not what."
            ),
            view=self,
        )
        self.stop()
        await sync_battle_message(self.bot, self.battle)


class StealApprovalView(discord.ui.View):
    def __init__(
        self,
        stealer: Fighter,
        stealer_skill: Skill,
        stealer_slot: int,
        ally: Fighter,
        ally_slot: int,
        target: Fighter,
        target_slot: int,
        bot: commands.Bot,
        battle: Battle,
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self.stealer = stealer
        self.stealer_skill = stealer_skill
        self.stealer_slot = stealer_slot
        self.ally = ally
        self.ally_slot = ally_slot
        self.target = target
        self.target_slot = target_slot
        self.bot = bot
        self.battle = battle
        self.responded = False
        self.message: discord.Message | None = None

    def _lock_in_unopposed(self):
        self.stealer.declare_in_slot(
            self.stealer_slot, self.stealer_skill, self.target, self.target_slot
        )

    async def _notify_overtaken(self, previous_holder: Fighter):
        if previous_holder.owner_id is None:
            return
        owner = self.bot.get_user(previous_holder.owner_id)
        if owner is None:
            try:
                owner = await self.bot.fetch_user(previous_holder.owner_id)
            except discord.NotFound:
                return
        try:
            await owner.send(
                f"{self.stealer.name} has taken over the clash against {self.target.name}'s "
                f"Slot {self.target_slot} instead of {previous_holder.name}. {previous_holder.name}'s "
                f"action there will hit without a Clash unless you declare something else."
            )
        except discord.Forbidden:
            pass

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.responded:
            await interaction.response.send_message("This request was already answered.", ephemeral=True)
            return
        self.responded = True

        target_action = self.target.declared_actions.get(self.target_slot)
        previous_holder = target_action.target if target_action is not None else None

        if previous_holder is self.ally:
            self.ally.undeclare(self.ally_slot)

        if target_action is not None:
            target_action.target = self.stealer
            target_action.target_slot = self.stealer_slot

        self.stealer.declare_in_slot(
            self.stealer_slot, self.stealer_skill, self.target, self.target_slot
        )

        if previous_holder is not None and previous_holder is not self.ally:
            await self._notify_overtaken(previous_holder)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=(
                f"Approved. {self.stealer.name}'s Slot {self.stealer_slot} now clashes "
                f"{self.target.name}'s Slot {self.target_slot} instead. Your Slot {self.ally_slot} "
                f"was cleared, use /battle declare again if you want to act elsewhere."
            ),
            view=self,
        )
        self.stop()
        await sync_battle_message(self.bot, self.battle)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.responded:
            await interaction.response.send_message("This request was already answered.", ephemeral=True)
            return
        self.responded = True
        self._lock_in_unopposed()

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"Declined. Your clash against {self.target.name} continues as declared.",
            view=self,
        )
        self.stop()
        await sync_battle_message(self.bot, self.battle)

    async def on_timeout(self):
        if self.responded:
            return
        self.responded = True
        self._lock_in_unopposed()

        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    content=(
                        "No response in time, treated as a decline. Your clash "
                        f"against {self.target.name} continues as declared."
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass
        await sync_battle_message(self.bot, self.battle)


def format_skill_result(result: SkillResult, survived: list[bool] | None = None) -> str:
    lines = [
        f"**{result.skill.name}** (Base {result.skill.base_power}, "
        f"+{result.skill.coin_power} Coin Power, {result.skill.coins} coins)"
    ]
    total = 0
    for i, c in enumerate(result.coin_results, start=1):
        face = coin_emoji("heads") if c.heads else coin_emoji("tails")
        alive = survived[i - 1] if survived is not None else True

        status_tag = ""
        idx = i - 1
        if idx < len(result.skill.coin_statuses) and result.skill.coin_statuses[idx]:
            name = result.skill.coin_statuses[idx]
            potency = result.skill.coin_status_potencies[idx]
            count = result.skill.coin_status_counts[idx]
            status_tag = f" {potency}{status_emoji(name)}{count}"

        line = f"  Coin {i}: {face} Power {c.power_after}, {c.damage_dealt} damage{status_tag}"
        if not alive:
            line = f"~~{line.strip()}~~ (destroyed)"
            lines.append(f"  {line}")
        else:
            lines.append(line)
            total += c.damage_dealt
    lines.append(f"  **Total: {total} damage**")
    return "\n".join(lines)


def format_clash_rounds(outcome: ClashOutcome, name_a: str, name_b: str) -> str:
    lines = []
    for i, r in enumerate(outcome.rounds, start=1):
        line = (
            f"Round {i}: {name_a} ({r.coins_a_before} coins) Power {r.result_a.final_power} "
            f"vs {name_b} ({r.coins_b_before} coins) Power {r.result_b.final_power}"
        )
        if r.loser == "a":
            line += f" -> {name_a} loses a coin"
        elif r.loser == "b":
            line += f" -> {name_b} loses a coin"
        else:
            line += " -> tie, nobody loses a coin"
        lines.append(line)
    return "\n".join(lines)


def _status_hint_tier(fighter: Fighter) -> int:
    """The worse of a fighter's currently active statuses, mapped to a tier by magnitude (potency * count) via magnitude_to_hint_tier (game/statuses.py). See docs/ENGINEERING_NOTES.md#battle-status-hint-tier for the full rationale."""
    best = 0
    for status in fighter.statuses.values():
        if status.count <= 0:
            continue
        best = max(best, magnitude_to_hint_tier(status.potency * status.count, status.name))
    return best


def _skill_hint_tier(fighter: Fighter, battle: Battle) -> int:
    """The highest hint_tier among the fighter's known skills whose trigger condition currently reads true against at least one living enemy. See docs/ENGINEERING_NOTES.md#battle-skill-hint-tier for the full rationale."""
    enemies = [f for f in battle.fighters if f.side != fighter.side and f.is_alive()]
    if not enemies:
        return 0
    best = 0
    for skill in fighter.skills.values():
        for trigger in skill.triggers:
            for enemy in enemies:
                context = TriggerContext(caster=fighter, target=enemy, battle=battle)
                from game.conditions import evaluate_condition
                if evaluate_condition(trigger.condition, context):
                    best = max(best, trigger.hint_tier)
                    break
    return best


def compute_hint_tier(fighter: Fighter, battle: Battle) -> int | None:
    """The tier to show on this fighter's Hint line: the higher of their live-triggerable skill danger and their active status danger. See docs/ENGINEERING_NOTES.md#battle-compute-hint-tier for the full rationale."""
    tier = max(_skill_hint_tier(fighter, battle), _status_hint_tier(fighter))
    return tier if tier > 0 else None


def build_battle_embed(battle: Battle) -> discord.Embed:
    type_info = BATTLE_TYPES.get(battle.battle_type, BATTLE_TYPES["spar"])
    embed = discord.Embed(title=type_info["title"], color=type_info["color"])
    if "image" in type_info:
        embed.set_image(url=type_info["image"])

    for side_name in ("A", "B"):
        fighters = sorted(
            (f for f in battle.side(side_name) if not f.eliminated),
            key=lambda f: max(f.slot_speeds, default=0), reverse=True,
        )
        if not fighters:
            continue

        speed_icon = stat_emoji("speed")
        lines = []
        for f in fighters:
            filled = f.slots_filled()
            slot_parts = []
            for i in range(f.skill_slots):
                slot_num = i + 1
                speed_val = f.slot_speeds[i] if i < len(f.slot_speeds) else "?"
                icon = coin_emoji("base") if slot_num in f.declared_actions else skill_slot_emoji(slot_num)
                slot_parts.append(f"{icon}`{speed_val}`{speed_icon}")
            slot_line = " ".join(slot_parts)

            if filled == 0:
                tag = ""
            elif filled >= f.skill_slots:
                tag = " -- **DECLARED**"
            else:
                tag = f" -- **{filled}/{f.skill_slots} declared**"
            down_tag = " (Down)" if not f.is_alive() else ""

            hint_tier = compute_hint_tier(f, battle)
            # Just the icon now, no trailing " Hint" text -- the emoji
            # alone is the signal, spelling it out was redundant.
            hint_line = f"\n-# {hint_emoji(hint_tier)}" if hint_tier else ""

            parts_line = ""
            if f.parts:
                bits = [
                    f"Slot {n} {p.name} " + ("(broken)" if p.broken else f"{p.hp}/{p.max_hp}")
                    for n, p in sorted(f.parts.items())
                ]
                parts_line = "\n-# Parts: " + ", ".join(bits)

            lines.append(
                f"**{f.name}**{down_tag} ({stat_emoji('hp')} {f.hp}/{f.max_hp}, "
                f"{stat_emoji('sanity')} {f.sanity}){tag}\n{slot_line}{parts_line}{hint_line}"
            )
        embed.add_field(name=f"Side {side_name}", value="\n\n".join(lines), inline=False)

    # Eliminated fighters are pulled out of their side's normal roster
    # entirely and listed here instead, across both sides combined --
    # once eliminated they're done for the rest of THIS battle (see
    # Fighter.eliminated's docstring), so there's no slot/speed/declare
    # info left worth showing for them, just the fact that they're out.
    eliminated_fighters = [f for f in battle.fighters if f.eliminated]
    if eliminated_fighters:
        embed.add_field(
            name="Eliminated",
            value="\n".join(f"{f.name}" for f in eliminated_fighters),
            inline=False,
        )

    if not battle.fighters:
        embed.description = "No fighters added yet. Use /battle addfighter."

    embed.set_footer(text=f"Round {battle.round_number}")
    return embed


async def sync_battle_message(bot: commands.Bot, battle: Battle):
    if battle.message_id is None:
        return
    channel = bot.get_channel(battle.channel_id)
    if channel is None:
        return
    try:
        message = await channel.fetch_message(battle.message_id)
    except (discord.NotFound, discord.Forbidden):
        return
    await message.edit(embed=build_battle_embed(battle))


def resolve_evade(
    defender: Fighter, attacker: Fighter, attacker_slot: int, battle: Battle, skip_evade: bool = False,
) -> tuple[bool, list[str]]:
    """Rolls the ONE deciding coin for whichever declared [Evade] the
    defender has aimed at this exact (attacker, attacker_slot), if
    any. Called BEFORE apply_incoming_hit at every hit-resolution call
    site -- Evade no longer lives inside apply_incoming_hit's own
    per-coin loop, it's a real declared skill with its own win/lose
    roll now.

    Whole-hit, not per-coin: a successful Evade dodges the ENTIRE
    incoming hit, a failed one dodges nothing. Splitting a multi-coin
    attack across a partial Evade failure is a separate, later piece.

    Marks the Evade's own slot used in evade_used_slots regardless of
    win or lose -- rolling the coin at all spends it for the round.

    skip_evade mirrors the old skip_evasion behavior for a Counter
    retaliation strike, which bypasses the target's Evade entirely.
    """
    if skip_evade:
        return False, []
    found = find_eligible_evade(defender, attacker, attacker_slot)
    if found is None:
        return False, []
    evade_slot, evade_skill = found
    defender.evade_used_slots.add(evade_slot)

    evade_result = resolve_skill(evade_skill, defender.heads_chance())
    won = evade_result.coin_results[0].heads
    log = [
        f"{status_emoji('evasion')} {defender.name}'s Evade (Slot {evade_slot}) "
        + ("succeeds! The hit is fully dodged." if won else "fails, the hit lands.")
    ]
    return won, log


def _damage_mods(
    target: Fighter, attacker: Fighter, damage_type: str, skill_pct: int = 0,
) -> tuple[float, list[str]]:
    """Fragile / type Fragility / Damage Up / Damage Down / Protection folded into one multiplier, plus a log description of each part that applies. They add together, floored at 0. `skill_pct` is the "Deal +N% damage" total the skill picked up this use, it sits in the same additive pile as Damage Up. See docs/ENGINEERING_NOTES.md#statuses-one-turn-debuffs."""
    frag = fragility_bonus(target.statuses, damage_type)
    up = damage_up_bonus(attacker.statuses)
    down = damage_down_penalty(attacker.statuses)
    prot = protection_reduction(target.statuses)
    parts = []
    if frag > 0:
        parts.append(f"{target.name} Fragile +{round(frag * 100)}%")
    if prot > 0:
        parts.append(f"{target.name} Protection -{round(prot * 100)}%")
    if up > 0:
        parts.append(f"{attacker.name} Damage Up +{round(up * 100)}%")
    if down > 0:
        parts.append(f"{attacker.name} Damage Down -{round(down * 100)}%")
    off_steps = max(0, min(5, getattr(attacker, "offense_level", 0) + getattr(attacker, "temp_offense", 0)))
    def_steps = max(0, min(5, getattr(target, "defense_level", 0) + getattr(target, "temp_defense", 0)))
    off = off_steps * OFFDEF_PERCENT_PER_STEP / 100
    dfn = def_steps * OFFDEF_PERCENT_PER_STEP / 100
    if off > 0:
        parts.append(f"{attacker.name} Offense Level +{round(off * 100)}%")
    if dfn > 0:
        parts.append(f"{target.name} Defense Level -{round(dfn * 100)}%")
    extra = skill_pct / 100
    if extra > 0:
        parts.append(f"{attacker.name} skill/passive +{skill_pct}% damage")
    return max(0.0, 1 + frag + up + extra - down - prot + off - dfn), parts


def apply_incoming_hit(
    attacker_skill: Skill, result: SkillResult, target: Fighter, caster: Fighter,
    target_slot: int | None = None,
) -> tuple[int, list[str], list[Trigger]]:
    """Applies each landed coin's damage/status, and collects whichever
    per-coin Triggers (on_hit/heads_hit/tails_hit) fired on that coin.
    Whether this hit is evaded at all is decided BEFORE this function
    is ever called, via resolve_evade above -- this always applies
    full damage/status, the caller skips the call entirely on a
    successful Evade.
    """
    log: list[str] = []
    total_damage = 0
    per_coin_triggers: list[Trigger] = []
    resist = target.resistances_for_slot(target_slot)
    poise = caster.get_status("poise")
    stagger_multiplier = (
        STAGGER_MULTIPLIERS[target.current_stagger_tier - 1]
        if target.current_stagger_tier > 0 else 1.0
    )

    for i, coin in enumerate(result.coin_results):
        per_coin_triggers.extend(coin.fired_triggers)
        raw = coin.damage_dealt + (coin.crit_bonus_damage if coin.is_crit else 0)
        resisted = apply_resistance(raw, resist.get(attacker_skill.damage_type, 1.0))
        if resisted != raw:
            log.append(
                f"Coin {i + 1}: ({damage_type_emoji(attacker_skill.damage_type)} "
                f"{attacker_skill.damage_type.capitalize()} resistance: {raw} -> {resisted})"
            )
        coin_total = resisted

        if coin.is_crit:
            log.append(
                f"Coin {i + 1}: {status_emoji('poise')} Crit! {caster.name}'s Poise adds "
                f"+{coin.crit_bonus_damage} damage."
            )
            if poise is not None:
                poise = decay_after_trigger(poise)
                caster.set_status_instance(poise)

        decayed_rupture = None
        rupture = target.get_status("rupture")
        if rupture is not None:
            rupture_damage = min(rupture.potency, round(target.max_hp * RUPTURE_DAMAGE_CAP_PCT))
            coin_total += rupture_damage
            log.append(
                f"Coin {i + 1}: {status_emoji('rupture')} Rupture triggers on "
                f"{target.name}: +{rupture_damage} damage"
            )
            decayed_rupture = decay_after_trigger(rupture)
            target.set_status_instance(decayed_rupture)

        status_name = attacker_skill.coin_statuses[i] if i < len(attacker_skill.coin_statuses) else None
        if status_name:
            resistance_multiplier = resist.get(status_name, 1.0)
            raw_potency = (
                attacker_skill.coin_status_potencies[i]
                if i < len(attacker_skill.coin_status_potencies) else 0
            )
            added_potency = apply_resistance(raw_potency, resistance_multiplier)
            added_count = (
                attacker_skill.coin_status_counts[i]
                if i < len(attacker_skill.coin_status_counts) else 0
            )

            current = decayed_rupture if status_name == "rupture" else target.get_status(status_name)
            new_instance = apply_status(current, status_name, added_potency, added_count)
            target.set_status_instance(new_instance)
            log.append(
                f"Coin {i + 1}: {target.name} gains {status_emoji(status_name)} "
                f"{status_label(status_name)}: {new_instance.potency}/{new_instance.count}"
            )

        total_damage += coin_total

    if stagger_multiplier != 1.0 and total_damage > 0:
        boosted = round(total_damage * stagger_multiplier)
        log.append(
            f"{stagger_tier_emoji(target.current_stagger_tier)} {target.name} is Staggered (Tier "
            f"{target.current_stagger_tier}): {total_damage} -> {boosted} damage (x{stagger_multiplier})"
        )
        total_damage = boosted

    mod, parts = _damage_mods(target, caster, attacker_skill.damage_type, result.skill.damage_pct)
    if parts and total_damage > 0:
        boosted = round(total_damage * mod)
        log.append(
            f"{status_emoji('fragile')} {', '.join(parts)}: {total_damage} -> {boosted} damage"
        )
        total_damage = boosted

    return total_damage, log, per_coin_triggers


def fire_evade_triggers(defender: Fighter, attacker: Fighter, battle: Battle) -> list[str]:
    """Fires [On Evade] once, swept across ALL of the defender's own
    known skills, whenever resolve_evade above just won. No more
    evade_count loop -- a successful Evade is one whole-hit dodge now.
    """
    log: list[str] = []
    context = TriggerContext(caster=defender, target=attacker, battle=battle)
    for skill in defender.trigger_skills():
        _, post_hit = resolve_triggers(skill, context, "on_evade")
        log.extend(apply_trigger_effects(post_hit, defender, attacker))
    return log


def find_eligible_counter(defender: Fighter, attacker_slot_speed: int) -> tuple[int, Skill] | None:
    """Counter is a reactive Skill-level mechanic, not a status: any skill flagged [Counter] that `defender` has DECLARED this round (in ANY of... See docs/ENGINEERING_NOTES.md#battle-find-eligible-counter for the full rationale."""
    if defender.counter_used_this_round:
        return None
    for slot_num, action in defender.declared_actions.items():
        if "counter" in action.skill.tags and defender.slot_speed(slot_num) > attacker_slot_speed:
            return slot_num, action.skill
    return None

def find_eligible_evade(defender: Fighter, attacker: Fighter, attacker_slot: int) -> tuple[int, Skill] | None:
    """[Evade] is a real declared skill now, strictly slot-specific,
    unlike Counter. Only eligible if `defender` has an [Evade]-tagged
    skill declared this round aimed exactly at (attacker,
    attacker_slot), and that specific Evade slot hasn't already rolled
    this round -- tracked per Evade-holder's OWN slot number in
    evade_used_slots, since a fighter can hold more than one declared
    Evade at once. No speed comparison -- Evade never steals or gets
    stolen from.
    """
    for slot_num, action in defender.declared_actions.items():
        if slot_num in defender.evade_used_slots:
            continue
        if "evade" not in action.skill.tags:
            continue
        if action.target is attacker and action.target_slot == attacker_slot:
            return slot_num, action.skill
    return None

def find_eligible_clashable_guard(defender: Fighter) -> tuple[int, Skill] | None:
    """Same shape as find_eligible_clashable_counter, for [Clashable Guard]. See docs/ENGINEERING_NOTES.md#battle-find-eligible-clashable-guard for the full rationale."""
    if defender.clashable_guard_used_this_round:
        return None
    for slot_num, action in defender.declared_actions.items():
        if "clashable_guard" in action.skill.tags:
            return slot_num, action.skill
    return None

def find_eligible_clashable_counter(defender: Fighter) -> tuple[int, Skill] | None:
    """Same shape as find_eligible_clashable_guard, for [Clashable Counter]."""
    if defender.clashable_counter_used_this_round:
        return None
    for slot_num, action in defender.declared_actions.items():
        if "clashable_counter" in action.skill.tags:
            return slot_num, action.skill
    return None


def apply_counter_redirects(units: list, battle: Battle) -> list:
    """Runs once, right after `units` is built and speed-sorted, BEFORE
    any resolution happens -- transforms the list to reflect plain
    [Counter] redirects. Not slot-specific, no fall-through, no
    target-matching -- it fires against any unopposed attack that
    reaches its holder, regardless of which slot got hit.

    Clashable Guard/Clashable Counter interception used to also live
    here as two more static passes, but that was wrong on two counts:
    it ran once before any resolution happened (so it could never see
    which of a fighter's OTHER defenses had already been spent earlier
    in the round -- needed for the "already-used Evade falls through to
    an unused Clashable" rule), and it matched by "is ANY unopposed
    attack aimed at this caster" rather than the exact (attacker,
    attacker_slot) the Clashable defense actually declared as its own
    target -- so a Clashable Counter aimed at protecting an ally never
    actually intercepted what it was pointed at. Clashable Guard/Counter
    interception now happens via the exact-target Pass 0 (see the
    `entries` setup in combat(), right before mutual-pairing) for the
    proactive/declared-target case, with the live mid-round fall-through
    case still to be built as its own follow-up.
    """
    pass1: list = []
    for u in units:
        if u[0] != "solo":
            pass1.append(u)
            continue
        entry = u[1]
        attacker, defender = entry["caster"], entry["target"]
        attacker_slot_speed = attacker.slot_speed(entry["slot"])

        remaining_extra_targets = []
        for splash_fighter, splash_slot in entry.get("extra_targets", []):
            if splash_fighter is defender:
                remaining_extra_targets.append((splash_fighter, splash_slot))
                continue
            splash_found = find_eligible_counter(splash_fighter, attacker_slot_speed)
            if splash_found is None:
                remaining_extra_targets.append((splash_fighter, splash_slot))
                continue
            splash_counter_slot, splash_counter_skill = splash_found
            splash_fighter.counter_used_this_round = True
            pass1.append(("solo", {
                "caster": splash_fighter, "skill": splash_counter_skill, "target": attacker,
                "slot": splash_counter_slot, "target_slot": entry["slot"], "used": True,
                "is_counter_retaliation": True,
            }))
        entry["extra_targets"] = remaining_extra_targets

        found = find_eligible_counter(defender, attacker_slot_speed)
        if found is None:
            pass1.append(u)
            continue
        counter_slot, counter_skill = found
        defender.counter_used_this_round = True
        redirected_entry = {
            "caster": defender, "skill": counter_skill, "target": attacker,
            "slot": counter_slot, "target_slot": entry["slot"], "used": True,
            "is_counter_retaliation": True,
        }
        pass1.append(("solo", redirected_entry))

    return pass1


# Every pre-roll (pre-toss) skill-level timing, in firing order, for a side that's about to enter a Clash. See docs/ENGINEERING_NOTES.md#battle-comment-867.
PRE_ROLL_CLASH_TIMINGS = ("before_use", "on_use", "clash_start")

# Same idea for a side making an unopposed attack. See docs/ENGINEERING_NOTES.md#battle-comment-887.
PRE_ROLL_SOLO_TIMINGS = ("before_use", "on_use", "before_attack")


def fire_passive_triggers(battle: Battle) -> list[str]:
    """The persistent Fighter-level buff store this engine was missing: fires [Combat Start] (once per battle) and [Turn Start] (every round)... See docs/ENGINEERING_NOTES.md#battle-fire-passive-triggers for the full rationale."""
    timings = ["turn_start"]
    if not battle.started:
        timings.append("combat_start")

    log: list[str] = []
    # "... next turn" effects queued last round land now, before anything else fires.
    for fighter in battle.fighters:
        queued, fighter.pending_effects = fighter.pending_effects, []
        for trig, tgt, ally in queued:
            if fighter.is_alive():
                log.extend(apply_trigger_effects([replace(trig, delayed=False)], fighter, tgt, ally=ally))
    for fighter in battle.fighters:
        if not fighter.is_alive():
            continue
        for skill in fighter.trigger_skills():
            context = TriggerContext(caster=fighter, target=None, battle=battle)
            for timing in timings:
                _, post_hit = resolve_triggers(skill, context, timing)
                log.extend(apply_trigger_effects(post_hit, fighter, None))

    # [Ally Turn Start] / [Ally Combat Start]: once per living ally of the
    # owner, with that ally as the context's ally.
    ally_timings = ["ally_turn_start"] + ([] if battle.started else ["ally_combat_start"])
    for owner in battle.fighters:
        if not owner.is_alive():
            continue
        for ally_fighter in _living_allies(battle, owner):
            for skill in owner.trigger_skills():
                context = TriggerContext(caster=owner, target=None, battle=battle, ally=ally_fighter)
                for timing in ally_timings:
                    _, post_hit = resolve_triggers(skill, context, timing)
                    log.extend(apply_trigger_effects(post_hit, owner, None, ally=ally_fighter))
    return log


def _living_allies(battle: Battle, fighter: Fighter) -> list[Fighter]:
    """Everyone on `fighter`'s side other than `fighter`, still in the fight."""
    return [
        f for f in battle.fighters
        if f is not fighter and f.side == fighter.side and f.is_alive() and not f.eliminated
    ]


def fire_ally_reactions(
    battle: Battle, actor: Fighter, event: str, other: Fighter | None = None,
) -> list[str]:
    """Fires one [Ally ...] timing (`event`, e.g. 'ally_on_hit') for every living ally of `actor`, sweeping each of THEIR skills. `actor` is the fighter the event happened to or was done by, `other` is the enemy involved. See docs/ENGINEERING_NOTES.md#ally-reactions."""
    log: list[str] = []
    for owner in _living_allies(battle, actor):
        context = TriggerContext(caster=owner, target=other, battle=battle, ally=actor)
        for skill in owner.trigger_skills():
            _, post_hit = resolve_triggers(skill, context, event)
            log.extend(apply_trigger_effects(post_hit, owner, other, ally=actor))
    return log


def fire_self_reactions(battle: Battle, owner: Fighter, event: str, other: Fighter | None = None) -> list[str]:
    """Fires a passive-only timing (Self Is Hit / Self On Stagger / Self On Kill) on `owner`'s passives. `other` is the enemy involved."""
    log: list[str] = []
    context = TriggerContext(caster=owner, target=other, battle=battle)
    for skill in owner.passive_skills:
        _, post_hit = resolve_triggers(skill, context, event)
        log.extend(apply_trigger_effects(post_hit, owner, other))
    return log


def fire_hit_ally_reactions(
    battle: Battle, attacker: Fighter, target: Fighter, damage: int, was_crit: bool,
    stagger_before: int, evaded: bool,
) -> list[str]:
    """All the [Ally ...] reactions to one landed attack: the attacker's allies hear On Hit / On Crit / On Kill, the target's allies hear Is Hit / On Stagger (only if this hit newly Staggered them). Nothing fires for an evaded or zero-damage hit."""
    if evaded or damage <= 0:
        return []
    log = fire_self_reactions(battle, target, "self_is_hit", attacker)
    if not target.is_alive():
        log += fire_self_reactions(battle, attacker, "self_on_kill", target)
        log += fire_self_reactions(battle, target, "self_on_death", attacker)
    if stagger_before == 0 and target.current_stagger_tier > 0:
        log += fire_self_reactions(battle, target, "self_on_stagger", attacker)
    log += fire_ally_reactions(battle, attacker, "ally_on_hit", target)
    if was_crit:
        log += fire_ally_reactions(battle, attacker, "ally_on_crit", target)
    if not target.is_alive():
        log += fire_ally_reactions(battle, attacker, "ally_on_kill", target)
    log += fire_ally_reactions(battle, target, "ally_is_hit", attacker)
    if stagger_before == 0 and target.current_stagger_tier > 0:
        log += fire_ally_reactions(battle, target, "ally_on_stagger", attacker)
    return log


def apply_turn_end_status_ticks(battle: Battle) -> list[str]:
    """Fires the Turn-End status payoffs -- Burn, Sinking, and Charge's
    own decay -- against every living fighter. Called once, right
    before this round's results get finalized into the embed (so the
    damage/log lines actually show up), and before
    Fighter.clear_expired_stagger/battle.start_new_round() run.

    Burn: fixed Potency damage (through the normal Shield-first
    take_damage path, same as any other damage), Count -1.

    Sinking: fixed Sanity loss equal to Potency. No Count to decay --
    Sinking has no Count concept in its own spec, it persists at
    whatever Potency it's at every Turn End until removed some other
    way (re-inflicted to a different Potency, cleared, etc.), it
    doesn't wear off on its own.

    Charge: not a damage-dealing status at all here -- its actual
    payoff (converting into something else) requires a specific
    passive, which isn't a universal engine behavior. This only
    handles its own built-in Count decay, no log line for it since
    nothing visibly happened.
    """
    log: list[str] = []
    # Player-made statuses with a "turn" lifetime are wiped from EVERY
    # fighter, since a debuff made by one character sits on another.
    turn_custom_keys = {
        k for fi in battle.fighters for k, cs in fi.custom_statuses.items() if cs.lifetime == "turn"
    }
    for f in battle.fighters:
        f.temp_offense = 0
        f.temp_defense = 0
        f.hp_floor = 0
        f.stagger_immune = False
        for name in ONE_TURN_STATUSES:
            f.statuses.pop(name, None)
        for name in turn_custom_keys:
            f.statuses.pop(name, None)
        if f.immobilized_blocked:
            f.statuses.pop("immobilized", None)
            f.immobilized_blocked = False
            if f.panicked:
                f.panicked = False
                f.sanity = 0
                log.append(f"{status_emoji('panic')} {f.name} recovers from Panic. (Sanity reset to 0)")
        if not f.is_alive():
            continue

        # Supermove aftermath: queued Sanity drains tick before the Panic check,
        # so a drain that hits the floor Panics this same turn.
        for drain in list(f.sanity_drains):
            f.lose_sanity(drain.amount)
            drain.turns_left -= 1
            left = f"{drain.turns_left} turn(s) left" if drain.turns_left > 0 else "last tick"
            log.append(
                f"{f.name} loses {drain.amount} Sanity to the aftermath of {drain.source}. "
                f"({f.name}: {f.sanity} SP, {left})"
            )
            if drain.turns_left <= 0:
                f.sanity_drains.remove(drain)

        # Panic: Sanity at the floor Immobilizes next turn, Sanity resets
        # to 0 once that turn has been served.
        if not f.panicked and f.sanity <= SANITY_PANIC:
            f.panicked = True
            f.statuses["immobilized"] = StatusInstance(name="immobilized", potency=1, count=1)
            log.append(
                f"{status_emoji('panic')} {f.name} is Panicked and cannot act next turn."
            )

        poison = f.get_status("poison")
        if poison is not None and poison.count > 0:
            shield_absorbed, hp_dmg = f.take_damage(poison.count)
            breakdown = (
                f" ({shield_absorbed} absorbed by {status_emoji('shield')} Shield, {hp_dmg} to HP)"
                if shield_absorbed > 0 else ""
            )
            log.append(
                f"{status_emoji('poison')} {f.name} takes {poison.count} Poison damage.{breakdown} "
                f"({f.name}: {f.hp}/{f.max_hp} HP)"
            )
            log.extend(f.drain_survival_notes())
            f.set_status_instance(StatusInstance(name="poison", potency=poison.potency, count=poison.count // 2))

        burn = f.get_status("burn")
        if burn is not None and burn.count > 0:
            shield_absorbed, hp_dmg = f.take_damage(burn.potency)
            breakdown = (
                f" ({shield_absorbed} absorbed by {status_emoji('shield')} Shield, {hp_dmg} to HP)"
                if shield_absorbed > 0 else ""
            )
            log.append(
                f"{status_emoji('burn')} {f.name} takes {burn.potency} Burn damage.{breakdown} "
                f"({f.name}: {f.hp}/{f.max_hp} HP)"
            )
            log.extend(f.drain_survival_notes())
            f.set_status_instance(decay_after_trigger(burn))

        sinking = f.get_status("sinking")
        if sinking is not None and sinking.potency > 0:
            f.lose_sanity(sinking.potency)
            log.append(
                f"{status_emoji('sinking')} {f.name} loses {sinking.potency} Sanity to Sinking. "
                f"({f.name}: {f.sanity} SP)"
            )

        charge = f.get_status("charge")
        if charge is not None and charge.count > 0:
            f.set_status_instance(decay_after_trigger(charge))

    return log


def fire_kill_triggers(
    caster: Fighter, target: Fighter, skill: Skill, context: TriggerContext, was_crit: bool
) -> list[str]:
    """Fires [On Kill] on the attacker's own skill if `target` was just
    reduced to 0 HP by this hit -- and [On Crit Kill] too if any coin
    in this same hit was also a Crit. Call this AFTER take_damage has
    already been applied, so target.is_alive() reflects the real
    post-hit state.
    """
    if target.is_alive():
        return []
    _, kill_post_hit = resolve_triggers(skill, context, "on_kill")
    log = apply_trigger_effects(kill_post_hit, caster, target)
    if was_crit:
        _, crit_kill_post_hit = resolve_triggers(skill, context, "on_crit_kill")
        log += apply_trigger_effects(crit_kill_post_hit, caster, target)
    return log


def fire_tremor_burst(caster: Fighter, target: Fighter, skill: Skill, context: TriggerContext) -> list[str]:
    """Fires [Tremor Burst] on the attacker's skill, only if the target
    currently holds Tremor AND the skill's own condition on that line
    passes. Unlike a normal Trigger effect, this timing carries its own
    BUILT-IN payoff -- raising the target's ENABLED Stagger thresholds
    by Tremor's own Potency (as percentage points, same convention as
    GUARD_STAGGER_THRESHOLD_RAISE), then decaying Tremor's own Count by
    1 -- since "raise stagger threshold" isn't one of the generic
    effect types (inflict_status/sanity_gain/bonus_power/gain_status)
    a Trigger line could express on its own.
    """
    tremor = target.get_status("tremor")
    if tremor is None or tremor.count <= 0:
        return []
    _, fired = resolve_triggers(skill, context, "tremor_burst")
    if not fired:
        return []

    # Apply whatever generic effect the Trigger line itself wrote
    # (inflict_status/sanity_gain/etc, same as any other timing) FIRST,
    # separately from the built-in payoff below -- a skill author
    # writing "[Tremor Burst] ..., Gain 1 Sanity" expects both the
    # threshold raise AND their own +1 Sanity, not just one of them.
    log = apply_trigger_effects(fired, caster, target)

    raised = []
    for i in range(3):
        if target.stagger_tiers_enabled[i]:
            target.stagger_thresholds[i] = min(1.0, target.stagger_thresholds[i] + tremor.potency / 100)
            raised.append(f"Tier {i + 1} -> {round(target.stagger_thresholds[i] * 100)}%")
    target.set_status_instance(decay_after_trigger(tremor))

    if raised:
        log.append(
            f"{status_emoji('tremor')} Tremor Burst on {target.name}! Stagger thresholds rise: "
            + ", ".join(raised)
        )
    return log


def apply_bleed_self_damage(fighter: Fighter, skill: Skill) -> list[str]:
    """Bleed damages the BLEEDING fighter themselves once per
    non-Defense skill they actually use and land (a Clash's final
    decisive toss, or a solo unopposed toss) -- fixed Potency damage
    PER COIN in that resolution, Count -1 per use (not per coin).
    Guard/Clashable Guard/Counter/Clashable Counter don't count as
    "attacking" for this purpose (DEFENSE_TAGS), matching Bleed's own
    "non-defense skill" wording. `skill` here is whatever
    ALREADY-RESOLVED skill this fighter just used (their own coins,
    already tossed) -- this just needs its coin count, not the result
    object itself.
    """
    if DEFENSE_TAGS & skill.tags:
        return []
    bleed = fighter.get_status("bleed")
    if bleed is None or bleed.count <= 0:
        return []
    total = bleed.potency * skill.coins
    shield_absorbed, hp_dmg = fighter.take_damage(total)
    notes = fighter.drain_survival_notes()
    breakdown = (
        f" ({shield_absorbed} absorbed by {status_emoji('shield')} Shield, {hp_dmg} to HP)"
        if shield_absorbed > 0 else ""
    )
    fighter.set_status_instance(decay_after_trigger(bleed))
    return [
        f"{status_emoji('bleed')} {fighter.name} takes {total} Bleed damage from using "
        f"{skill.name}.{breakdown} ({fighter.name}: {fighter.hp}/{fighter.max_hp} HP)"
    ] + notes


def _resolve_pre_roll_chain(
    skill: Skill, context: TriggerContext, timings: tuple[str, ...]
) -> tuple[Skill, list[Trigger]]:
    """Chains resolve_triggers across every pre-roll timing in `timings`, in order, folding each stage's bonus_power/bonus_coin_power into the... See docs/ENGINEERING_NOTES.md#battle-resolve-pre-roll-chain for the full rationale."""
    post_hit: list[Trigger] = []
    for timing in timings:
        skill, fired = resolve_triggers(skill, context, timing)
        post_hit.extend(fired)
    return skill, post_hit


# Matches a Discord custom-emoji tag, animated or not -- <:Name:ID> or
# <a:Name:ID> -- so _warn_wrap below can skip over them instead of
# uppercasing their contents. Case matters inside a real tag (the "a"
# animated-prefix is literal, and some clients key off the name's exact
# case), so this is NOT just cosmetic -- see _warn_wrap's own docstring.
_EMOJI_TAG_RE = re.compile(r"<a?:\w+:\d+>")


def _warn_wrap(line: str, hint_tier: int) -> str:
    """Makes a dangerous (hint_tier 2 or 3) Trigger's fight-log line
    actually stand out: bolded, capitalized, and wrapped in that tier's
    own hint_emoji on both sides -- e.g. a tier-2 Burn trigger reads
    "(warning icon) **TRIGGER: RIZE GAINS BURN: 3/2** (warning icon)"
    instead of blending in with routine tier-1 lines. Only ever called
    for tier >= 2 -- mild (tier 1) lines are left as plain text, so the
    formatting itself carries the "this matters" signal.

    Uppercasing skips over any Discord custom-emoji tag already in
    `line` (e.g. status_emoji's output) rather than applying .upper() to
    the whole string -- a tag's "a" animated prefix is case-sensitive,
    and mangling it would silently break that one emoji's render. See
    docs/ENGINEERING_NOTES.md#battle-warn-wrap.
    """
    parts = _EMOJI_TAG_RE.split(line)
    tags = _EMOJI_TAG_RE.findall(line)
    upper = parts[0].upper()
    for tag, part in zip(tags, parts[1:]):
        upper += tag + part.upper()
    icon = hint_emoji(hint_tier)
    return f"{icon} **{upper}** {icon}"


def _bump_level(who: Fighter, t: Trigger):
    if t.status_name == "offense":
        who.temp_offense += t.effect_value
    else:
        who.temp_defense += t.effect_value


def _level_text(t: Trigger) -> str:
    word = "rises" if t.effect_value > 0 else "falls"
    return f"{t.status_name.title()} Level {word} by {abs(t.effect_value)} this turn"


def apply_trigger_effects(
    triggers: list[Trigger], caster: Fighter, target: Fighter | None, ally: Fighter | None = None,
) -> list[str]:
    """Applies the post-hit effects (inflict_status, sanity_gain, gain_status, heal, remove_status) from whichever triggers fired AND actually landed a hit. Dangerous ones (hint_tier 2+) get run through _warn_wrap so they stand out in the fight log. A trigger written with 'to the ally' lands on `ally` (the fighter a [Support] skill was aimed at) instead of the caster, and is skipped if there is no ally. See docs/ENGINEERING_NOTES.md#battle-apply-trigger-effects and #ally-support for the full rationale."""
    log: list[str] = []
    for t in triggers:
        if t.delayed:
            caster.pending_effects.append((t, target, ally))
            continue
        if t.effect_type == "level_mod" and t.on_target:
            if target is None:
                continue
            _bump_level(target, t)
            log.append(f"Trigger: {target.name} {_level_text(t)}")
            continue
        if t.effect_type == "inflict_status":
            if target is None:
                continue
            resistance_multiplier = target.resistances.get(t.status_name, 1.0)
            added_potency = apply_resistance(t.effect_value, resistance_multiplier)
            current = target.get_status(t.status_name)
            new_instance = apply_status(current, t.status_name, added_potency, t.status_count)
            target.set_status_instance(new_instance)
            if t.status_name == "bind":
                target.shift_slot_speeds(-added_potency)
            line = (
                f"Trigger: {target.name} gains {status_emoji(t.status_name)} "
                f"{status_label(t.status_name)}: {new_instance.potency}/{new_instance.count}"
            )
            log.append(_warn_wrap(line, t.hint_tier) if t.hint_tier >= 2 else line)
            continue

        who = t.selected_ally if t.ally_select else (ally if t.to_ally else caster)
        if who is None:
            continue

        if t.effect_type == "heal":
            if not who.is_alive() or who.eliminated:
                continue
            amount = (
                max(1, round(who.max_hp * t.effect_value / 100)) if t.heal_percent else t.effect_value
            )
            healed = min(amount, who.max_hp - who.hp)
            who.hp += healed
            log.append(f"Trigger: {who.name} heals {healed} HP ({who.hp}/{who.max_hp})")
        elif t.effect_type == "sanity_gain":
            who.gain_sanity(t.effect_value)
            line = f"Trigger: {who.name} Sanity +{t.effect_value} ({who.sanity})"
            log.append(_warn_wrap(line, t.hint_tier) if t.hint_tier >= 2 else line)
        elif t.effect_type == "gain_shield":
            if not who.is_alive() or who.eliminated:
                continue
            amount = max(1, round(who.max_hp * t.effect_value / 100)) if t.heal_percent else t.effect_value
            who.shield += amount
            log.append(f"Trigger: {who.name} gains {status_emoji('shield')} {amount} Shield ({who.shield})")
        elif t.effect_type == "level_mod":
            _bump_level(who, t)
            log.append(f"Trigger: {who.name} {_level_text(t)}")
        elif t.effect_type == "hp_floor":
            if not who.is_alive():
                continue
            who.hp_floor = max(who.hp_floor, t.effect_value)
            log.append(f"Trigger: {who.name}'s HP cannot drop below {who.hp_floor} this turn")
        elif t.effect_type == "nullify_lethal":
            if not who.is_alive():
                continue
            who.nullify_lethal = 1
            log.append(f"Trigger: {who.name} will nullify the next lethal damage")
        elif t.effect_type == "stagger_immune":
            if not who.is_alive():
                continue
            who.stagger_immune = True
            log.append(f"Trigger: {who.name} cannot be Staggered this turn")
        elif t.effect_type == "remove_stagger":
            if who.is_alive() and who.current_stagger_tier > 0:
                who.current_stagger_tier = 0
                who.stagger_clears_end_of_round = None
                log.append(f"Trigger: {who.name} recovers from Stagger")
        elif t.effect_type == "sanity_loss":
            who.lose_sanity(t.effect_value)
            line = f"Trigger: {who.name} Sanity -{t.effect_value} ({who.sanity})"
            log.append(_warn_wrap(line, t.hint_tier) if t.hint_tier >= 2 else line)
        elif t.effect_type == "gain_status":
            current = who.get_status(t.status_name)
            new_instance = apply_status(current, t.status_name, t.effect_value, t.status_count)
            who.set_status_instance(new_instance)
            if t.status_name == "haste":
                who.shift_slot_speeds(new_instance.count - (current.count if current else 0))
            line = (
                f"Trigger: {who.name} gains {status_emoji(t.status_name)} "
                f"{status_label(t.status_name)}: {new_instance.potency}/{new_instance.count}"
            )
            log.append(_warn_wrap(line, t.hint_tier) if t.hint_tier >= 2 else line)
        elif t.effect_type == "remove_status":
            removed = who.statuses.pop(t.status_name, None)
            if removed is None:
                continue
            if t.status_name == "bind":
                who.shift_slot_speeds(removed.potency)
            elif t.status_name == "haste":
                who.shift_slot_speeds(-removed.count)
            log.append(
                f"Trigger: {who.name} loses {status_emoji(t.status_name)} {status_label(t.status_name)}"
            )
    return log


def build_round_ping(battle: Battle) -> str | None:
    """The "declare now" message for a freshly opened round: one mention per owner, listing their fighters that can act. None if nobody with a linked owner can act, or a side has no one left standing (the battle looks over). See docs/ENGINEERING_NOTES.md#round-ping."""
    for side in ("A", "B"):
        if not any(f.is_alive() and not f.eliminated for f in battle.side(side)):
            return None
    by_owner: dict[int, list[str]] = {}
    for f in battle.fighters:
        if f.owner_id is not None and not f.eliminated and f.can_act():
            by_owner.setdefault(f.owner_id, []).append(f.name)
    if not by_owner:
        return None
    parts = [f"<@{owner}> ({', '.join(names)})" for owner, names in by_owner.items()]
    return f"**Round {battle.round_number}** is open. Declare with `/battle declare`: " + ", ".join(parts)


def _log_embeds(entries: list[tuple[str, str]], title: str = "Combat Log") -> list[discord.Embed]:
    """Packs a round's (label, detail) log entries into at most 10 embeds. Shared by the Full Log button and /battle log."""
    embeds: list[discord.Embed] = []
    current_lines: list[str] = []
    current_len = 0

    def flush():
        if current_lines:
            name = title if not embeds else f"{title} (cont. {len(embeds) + 1})"
            embeds.append(discord.Embed(title=name, description="\n".join(current_lines)[:4000], color=0x5865F2))

    for label, detail in entries:
        block = f"**{label}**\n{detail}"
        if current_lines and current_len + len(block) > 3800:
            flush()
            current_lines = []
            current_len = 0
        current_lines.append(block)
        current_len += len(block)
    flush()

    if len(embeds) > 10:
        embeds = embeds[:10]
        embeds[-1].set_footer(text="Some actions this round were too numerous to include in full.")
    return embeds


class CombatLogView(discord.ui.View):
    """One button, "Full Log" -- anyone can click it to see the FULL breakdown of everything that happened this Combat Phase (every attrition... See docs/ENGINEERING_NOTES.md#battle-combatlogview for the full rationale."""

    def __init__(self, entries: list[tuple[str, str]], timeout: float = 600):
        super().__init__(timeout=timeout)
        self.entries = entries

    @discord.ui.button(label="Full Log", style=discord.ButtonStyle.secondary)
    async def full_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.entries:
            await interaction.response.send_message("Nothing happened this Combat Phase.", ephemeral=True)
            return

        embeds = _log_embeds(self.entries)
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class BattleCog(commands.GroupCog, name="battle"):
    """Commands for creating and managing battles."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.battles: dict[int, Battle] = {}

    @app_commands.command(
        name="create",
        description="Start a new battle in this channel (one battle per channel)",
    )
    @app_commands.describe(battle_type="Pick the kind of battle: Spar, Standard Encounter, or Fatal Battle")
    @app_commands.choices(
        battle_type=[
            app_commands.Choice(name="Spar", value="spar"),
            app_commands.Choice(name="Standard Encounter (Proelium Commune)", value="standard"),
            app_commands.Choice(name="Fatal Battle (Proelium Fatale)", value="fatal"),
        ]
    )
    async def create(self, interaction: discord.Interaction, battle_type: app_commands.Choice[str] = None):
        channel_id = interaction.channel_id
        if channel_id in self.battles:
            await interaction.response.send_message(
                "There's already an active battle in this channel. Use /battle end first.",
                ephemeral=True,
            )
            return

        battle = Battle(channel_id=channel_id, battle_type=battle_type.value if battle_type else "spar")
        self.battles[channel_id] = battle

        await interaction.response.send_message(embed=build_battle_embed(battle))
        message = await interaction.original_response()
        battle.message_id = message.id

    @app_commands.command(
        name="addnpc",
        description="Admin only: add a ready-made enemy (Fodder, Elite or Boss) to this battle",
    )
    @app_commands.choices(
        side=[app_commands.Choice(name="Side A", value="A"), app_commands.Choice(name="Side B", value="B")],
        tier=[app_commands.Choice(name=t.label, value=t.key) for t in NPC_TIERS.values()],
    )
    @app_commands.describe(
        side="Which side it fights on",
        tier="Fodder (weak), Elite (solid) or Boss (big and hard to stagger)",
        name="Name for this enemy (default: the tier name and a number)",
        hp="Replace the tier's HP",
        level="Replace the tier's Level (1 to 10)",
    )
    async def addnpc(
        self,
        interaction: discord.Interaction,
        side: str,
        tier: str,
        name: str | None = None,
        hp: app_commands.Range[int, 1, 100000] | None = None,
        level: app_commands.Range[int, 1, 10] | None = None,
    ):
        if not _is_admin(interaction):
            await interaction.response.send_message("Only an admin can add a ready-made enemy.", ephemeral=True)
            return
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message(
                "No active battle here yet. Use /battle create first.", ephemeral=True
            )
            return
        preset = NPC_TIERS.get(tier)
        if preset is None:
            await interaction.response.send_message("Unknown tier.", ephemeral=True)
            return
        if name is None:
            n = 1
            while battle.get_fighter(f"{preset.label} {n}") is not None:
                n += 1
            name = f"{preset.label} {n}"
        elif battle.get_fighter(name) is not None:
            await interaction.response.send_message(
                f"A fighter named {name} already exists in this battle.", ephemeral=True
            )
            return
        max_hp = hp or preset.hp
        fighter = Fighter(
            name=name, side=side.upper(), hp=max_hp, max_hp=max_hp, power=preset.power,
            skill_slots=preset.skill_slots, speed_min=preset.speed_min, speed_max=preset.speed_max,
            level=level or preset.level, owner_id=interaction.user.id,
        )
        fighter.stagger_tiers_enabled = list(preset.stagger_tiers_enabled)
        fighter.stagger_thresholds = list(preset.stagger_thresholds)
        battle.add_fighter(fighter)
        await interaction.response.send_message(
            f"Added {name} to Side {side.upper()}: {describe_tier(preset)}"
            + (f" (HP {max_hp})" if hp else "") + (f" (Level {fighter.level})" if level else "")
            + ". It is for this battle only. Give it skills with /battle addskill.",
            ephemeral=True,
        )
        await sync_battle_message(self.bot, battle)

    @app_commands.command(
        name="addfighter",
        description="Add a fighter to the current battle, from a saved character or as a one-off",
    )
    @app_commands.choices(side=[app_commands.Choice(name="Side A", value="A"), app_commands.Choice(name="Side B", value="B")])
    @app_commands.describe(
        side="Which side to join",
        character_name="Load one of your saved /character profiles into this battle (recommended)",
        name="Name for a one-off fighter -- skip this if you used character_name instead",
        hp="Starting HP for a one-off fighter, default 100 (ignored if using character_name)",
        speed_min="Lowest Speed a skill slot can roll each round (default: flat 10 if omitted)",
        speed_max="Highest Speed a skill slot can roll each round (default: same as speed_min)",
    )
    async def addfighter(
        self,
        interaction: discord.Interaction,
        side: str,
        character_name: str | None = None,
        name: str | None = None,
        hp: int = 100,
        speed_min: int | None = None,
        speed_max: int | None = None,
    ):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message(
                "No active battle here yet. Use /battle create first.", ephemeral=True
            )
            return

        side = side.upper()
        if side not in ("A", "B"):
            await interaction.response.send_message("Side must be A or B.", ephemeral=True)
            return

        if character_name is None and name is None:
            await interaction.response.send_message(
                "Provide either character_name (a saved character) or name (a one-off fighter).",
                ephemeral=True,
            )
            return

        if character_name is not None and name is not None:
            await interaction.response.send_message(
                "Use character_name OR name, not both. character_name pulls a saved character, "
                "name creates a one-off fighter for just this battle.",
                ephemeral=True,
            )
            return

        if character_name is not None:
            character = load_character(character_name)
            if character is None:
                await interaction.response.send_message(
                    f"No character named {character_name}. Pick a name from the list that shows as you type.", ephemeral=True
                )
                return

            if battle.get_fighter(character.name) is not None:
                await interaction.response.send_message(
                    f"A fighter named {character.name} already exists in this battle.", ephemeral=True
                )
                return

            build_note = ""
            if not character.bp_exempt:
                build_report = check_build(character)
                if not build_report.ok:
                    issues = "\n".join(f"- {p}" for p in build_report.all_problems()[:6])
                    if not _is_admin(interaction):
                        await interaction.response.send_message(
                            f"{character.name} breaks the Build Point rules and can't join a battle "
                            f"until it is fixed:\n{issues}\nAsk an admin to adjust the stats "
                            f"(see /character bp).",
                            ephemeral=True,
                        )
                        return
                    build_note = f"\nNote: {character.name} breaks the Build Point rules (added anyway because you are an admin)."

            fighter = Fighter.from_character(character, side)
            if speed_min is not None:
                fighter.speed_min = speed_min
                fighter.speed_max = speed_max if speed_max is not None else speed_min
                fighter.roll_slot_speeds()
            battle.add_fighter(fighter)
            await interaction.response.send_message(
                f"Added {fighter.name} to Side {side}.{build_note}", ephemeral=True
            )
            await sync_battle_message(self.bot, battle)
            return

        if battle.get_fighter(name) is not None:
            await interaction.response.send_message(
                f"A fighter named {name} already exists in this battle.", ephemeral=True
            )
            return

        fighter = Fighter(name=name, side=side, hp=hp, max_hp=hp, owner_id=interaction.user.id)
        if speed_min is not None:
            fighter.speed_min = speed_min
            fighter.speed_max = speed_max if speed_max is not None else speed_min
            fighter.roll_slot_speeds()
        battle.add_fighter(fighter)
        await interaction.response.send_message(f"Added {name} to Side {side} ({hp} HP).", ephemeral=True)
        await sync_battle_message(self.bot, battle)

    @app_commands.command(
        name="setstatus",
        description="Admin only: directly set a fighter's stats, resistances, and/or a status",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        fighter="Fighter name",
        status_name="A status to set, or 'none' to clear all statuses",
        custom_status="A player-made status to set instead (pick from the list)",
        potency="Potency for status_name",
        count="Count for status_name",
        hp="Set current HP",
        max_hp="Set max HP",
        sanity="Set Sanity",
        speed_min="Set the low end of this fighter's Speed range (needs speed_max too, re-rolls slots)",
        speed_max="Set the high end of this fighter's Speed range (needs speed_min too, re-rolls slots)",
        power="Set Power",
        resistance_input="Resistance(s) to set: 'Type:Value', comma-separated for several, e.g. 'slash:1.25,burn:0.75' (1.0 = Normal, steps of 0.25)",
        stagger_thresholds="3 HP% values for Tier 1/2/3, comma-separated, descending, e.g. '55,40,25'",
        stagger_disabled_tiers="Which Stagger tiers to strip (2 and/or 3, comma-separated; Tier 1 can't be removed), or 'none'",
        level="This fighter's Level for this battle (1 to 10); the higher Level adds the gap to its clash Power",
    )
    @app_commands.choices(
        status_name=[app_commands.Choice(name=s.replace('_', ' ').title(), value=s) for s in SETTABLE_STATUSES]
        + [app_commands.Choice(name="None (clear all statuses)", value="none")]
    )
    async def setstatus(
        self,
        interaction: discord.Interaction,
        fighter: str,
        status_name: app_commands.Choice[str] | None = None,
        custom_status: str | None = None,
        potency: int = 0,
        count: int = 0,
        hp: int | None = None,
        max_hp: int | None = None,
        sanity: int | None = None,
        speed_min: int | None = None,
        speed_max: int | None = None,
        power: int | None = None,
        resistance_input: str | None = None,
        stagger_thresholds: str | None = None,
        stagger_disabled_tiers: str | None = None,
        level: app_commands.Range[int, 1, 10] | None = None,
    ):
        if not _is_admin(interaction):
            await interaction.response.send_message(
                "Only an admin can use /battle setstatus.", ephemeral=True
            )
            return

        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        target_fighter = battle.get_fighter(fighter)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        # A one-way door: once eliminated (see Fighter.eliminated's
        # docstring), nothing can bring a fighter back into THIS battle,
        # including an admin heal via hp/max_hp here. Blocking the whole
        # command rather than just the hp param, since letting other
        # fields (resistances, speed, stagger config...) still change on
        # an eliminated fighter would be a pointless no-op at best and a
        # confusing half-state at worst -- start a new Battle instead.
        if target_fighter.eliminated:
            await interaction.response.send_message(
                f"{target_fighter.name} has been eliminated and cannot act in this battle.",
                ephemeral=True,
            )
            return

        if (speed_min is None) != (speed_max is None):
            await interaction.response.send_message(
                "speed_min and speed_max must be set together, or not at all.", ephemeral=True
            )
            return

        # Parsed and validated up front, before anything gets mutated -- same "all-or-nothing" principle as the rest of this command: a bad... See docs/ENGINEERING_NOTES.md#battle-comment-1171.
        resistance_changes: list[tuple[str, float]] = []
        if resistance_input is not None:
            for token in resistance_input.split(","):
                token = token.strip()
                if not token:
                    continue
                parts = token.split(":")
                if len(parts) != 2:
                    await interaction.response.send_message(
                        f"Resistance entry '{token}' is invalid. Use 'Type:Value', comma-separated "
                        f"for several, e.g. 'slash:1.25,burn:0.75'.",
                        ephemeral=True,
                    )
                    return
                r_type, r_value_raw = parts[0].strip().lower(), parts[1].strip()
                if r_type not in ALL_RESISTANCE_TYPES:
                    await interaction.response.send_message(
                        f"'{r_type}' isn't a valid resistance type. Choose from: "
                        f"{', '.join(ALL_RESISTANCE_TYPES)}.",
                        ephemeral=True,
                    )
                    return
                r_value, error = parse_resistance_multiplier(r_value_raw)
                if error:
                    await interaction.response.send_message(
                        f"Resistance value for {r_type}: {error}", ephemeral=True
                    )
                    return
                resistance_changes.append((r_type, r_value))

        # Same up-front, all-or-nothing validation pattern as resistances
        # above -- parsed and checked before anything gets mutated.
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

        changes = []

        if custom_status is not None:
            key = slugify(custom_status)
            known = {
                k: cs for fi in battle.fighters for k, cs in fi.custom_statuses.items()
            }
            if key not in known:
                await interaction.response.send_message(
                    f"No custom status called '{custom_status}' in this battle.", ephemeral=True
                )
                return
            custom_inst = StatusInstance(name=key, potency=potency, count=count)
            target_fighter.set_status_instance(custom_inst)
            changes.append(f"{status_emoji(key)} {known[key].name} -> {potency}/{count}")

        if status_name is not None:
            if status_name.value == "none":
                target_fighter.statuses.clear()
                changes.append("all statuses cleared")
            else:
                old = target_fighter.get_status(status_name.value)
                new_inst = StatusInstance(name=status_name.value, potency=potency, count=count)
                if status_name.value in ONE_TURN_STATUSES:
                    if status_name.value in POTENCY_TURN_STATUSES:
                        new_inst = apply_status(None, status_name.value, potency, 1 if potency > 0 else 0)
                    else:
                        new_inst = apply_status(None, status_name.value, 1 if count > 0 else 0, count)
                    if status_name.value == "bind":
                        target_fighter.shift_slot_speeds(-(new_inst.potency - (old.potency if old else 0)))
                    if status_name.value == "haste":
                        target_fighter.shift_slot_speeds(new_inst.count - (old.count if old else 0))
                target_fighter.set_status_instance(new_inst)
                changes.append(
                    f"{status_emoji(status_name.value)} {status_name.name} -> {potency}/{count}"
                )

        if hp is not None:
            target_fighter.hp = hp
            changes.append(f"HP -> {hp}")

        if max_hp is not None:
            target_fighter.max_hp = max_hp
            changes.append(f"Max HP -> {max_hp}")

        if sanity is not None:
            target_fighter.sanity = sanity
            changes.append(f"Sanity -> {sanity}")

        if speed_min is not None:
            target_fighter.speed_min = speed_min
            target_fighter.speed_max = speed_max
            target_fighter.roll_slot_speeds()
            changes.append(f"Speed range -> {speed_min}-{speed_max} (slots re-rolled)")

        if power is not None:
            target_fighter.power = power
            changes.append(f"Power -> {power}")

        if level is not None:
            target_fighter.level = level
            changes.append(f"Level -> {level}")

        for r_type, r_value in resistance_changes:
            target_fighter.resistances[r_type] = r_value
            changes.append(f"{r_type.capitalize()} resistance -> {r_value}x")

        if parsed_stagger_thresholds is not None:
            target_fighter.stagger_thresholds = parsed_stagger_thresholds
            changes.append(
                f"Stagger thresholds -> {pct_values[0]}%/{pct_values[1]}%/{pct_values[2]}%"
            )

        if parsed_disabled_tiers is not None:
            target_fighter.stagger_tiers_enabled = [
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

        await interaction.response.send_message(f"Updated {target_fighter.name}: {', '.join(changes)}.")
        await sync_battle_message(self.bot, battle)

    @setstatus.autocomplete("custom_status")
    async def setstatus_custom_status_autocomplete(self, interaction: discord.Interaction, current: str):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            return []
        seen: dict[str, str] = {}
        for fi in battle.fighters:
            for k, cs in fi.custom_statuses.items():
                seen.setdefault(k, cs.name)
        cur = current.lower()
        return [
            app_commands.Choice(name=name, value=name)
            for k, name in seen.items() if cur in name.lower()
        ][:25]

    @app_commands.command(
        name="addskill",
        description="Teach a fighter a skill for THIS battle only (use /character addskill to keep it)",
    )
    @app_commands.describe(fighter="Which fighter learns this skill")
    async def addskill(
        self,
        interaction: discord.Interaction,
        fighter: str,
    ):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        target_fighter = battle.get_fighter(fighter)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        if not _can_manage_fighter(interaction, target_fighter):
            await interaction.response.send_message(
                "Only the owner or an admin can add a skill to them.",
                ephemeral=True,
            )
            return

        # The ONLY response for this interaction -- everything about the skill (name, stats, damage type, statuses, triggers) is now collected in... See docs/ENGINEERING_NOTES.md#battle-comment-1345.
        await interaction.response.send_modal(AddSkillModal(target_fighter))

    async def _admin_part_target(self, interaction: discord.Interaction, fighter: str, slot: int):
        """Shared checks for /battle addpart, removepart and repairpart. Returns the Fighter or None (after replying)."""
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return None
        if not _is_admin(interaction):
            await interaction.response.send_message("Only an admin can manage Parts.", ephemeral=True)
            return None
        target = battle.get_fighter(fighter)
        if target is None:
            await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
            return None
        if not (1 <= slot <= target.skill_slots):
            await interaction.response.send_message(
                f"{target.name} only has skill slots 1-{target.skill_slots}.", ephemeral=True
            )
            return None
        return target

    @app_commands.command(
        name="addpart",
        description="Make one of a fighter's skill slots a body Part with its own HP (admin only)",
    )
    @app_commands.describe(
        fighter="The multi-part enemy",
        slot="Which of its skill slots is this Part (also the slot attackers aim at)",
        name="Part name, e.g. Left Claw",
        hp="This Part's HP. Hits to this slot also drain the fighter's own (Core) HP",
        resistances="Optional own resistances for this Part, e.g. 'slash:0.5,pierce:1.5'",
    )
    async def addpart(
        self, interaction: discord.Interaction, fighter: str, slot: int, name: str,
        hp: app_commands.Range[int, 1, 100000], resistances: str | None = None,
    ):
        target = await self._admin_part_target(interaction, fighter, slot)
        if target is None:
            return
        part_resistances = None
        if resistances:
            part_resistances, error = _parse_resistance_list(resistances)
            if error:
                await interaction.response.send_message(f"Resistances: {error}", ephemeral=True)
                return
        target.parts[slot] = Part(name=name.strip()[:40], hp=hp, max_hp=hp, resistances=part_resistances)
        await interaction.response.send_message(
            f"{target.name} Slot {slot} is now **{name.strip()[:40]}** ({hp} HP).", ephemeral=True
        )
        await sync_battle_message(self.bot, self.battles[interaction.channel_id])

    @app_commands.command(name="removepart", description="Turn a Part back into a normal skill slot (admin only)")
    @app_commands.describe(fighter="The multi-part enemy", slot="Which slot's Part to remove")
    async def removepart(self, interaction: discord.Interaction, fighter: str, slot: int):
        target = await self._admin_part_target(interaction, fighter, slot)
        if target is None:
            return
        if target.parts.pop(slot, None) is None:
            await interaction.response.send_message(f"{target.name} Slot {slot} isn't a Part.", ephemeral=True)
            return
        await interaction.response.send_message(f"{target.name} Slot {slot} is a normal slot again.", ephemeral=True)
        await sync_battle_message(self.bot, self.battles[interaction.channel_id])

    @app_commands.command(name="repairpart", description="Restore a Part's HP and un-break its slot (admin only)")
    @app_commands.describe(
        fighter="The multi-part enemy", slot="Which slot's Part to repair",
        hp="HP to set it to (leave blank for full)",
    )
    async def repairpart(
        self, interaction: discord.Interaction, fighter: str, slot: int,
        hp: app_commands.Range[int, 1, 100000] | None = None,
    ):
        target = await self._admin_part_target(interaction, fighter, slot)
        if target is None:
            return
        part = target.parts.get(slot)
        if part is None:
            await interaction.response.send_message(f"{target.name} Slot {slot} isn't a Part.", ephemeral=True)
            return
        part.hp = min(hp, part.max_hp) if hp is not None else part.max_hp
        part.broken = False
        await interaction.response.send_message(
            f"{target.name}'s {part.name} is repaired ({part.hp}/{part.max_hp}).", ephemeral=True
        )
        await sync_battle_message(self.bot, self.battles[interaction.channel_id])

    @app_commands.command(
        name="mystatus",
        description="Privately see the exact statuses and resources on your own fighter",
    )
    @app_commands.describe(fighter="Which fighter to look at (leave blank for all the fighters you control)")
    async def mystatus(self, interaction: discord.Interaction, fighter: str | None = None):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        if fighter:
            target = battle.get_fighter(fighter)
            if target is None:
                await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
                return
            if not _can_manage_fighter(interaction, target):
                await interaction.response.send_message(
                    "You can only look at the statuses of your own fighter (admins can see anyone's).",
                    ephemeral=True,
                )
                return
            mine = [target]
        else:
            mine = [f for f in battle.fighters if f.owner_id == interaction.user.id]
            if not mine:
                await interaction.response.send_message(
                    "You don't control a fighter in this battle. Pass a fighter name if you're an admin.",
                    ephemeral=True,
                )
                return

        text = "\n\n".join(_mystatus_text(f) for f in mine)
        if len(text) > 1900:
            text = text[:1900] + "\n...(truncated)"
        await interaction.response.send_message(text, ephemeral=True)

    @mystatus.autocomplete("fighter")
    async def mystatus_fighter_autocomplete(self, interaction: discord.Interaction, current: str):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            return []
        current_lower = current.lower()
        return [
            app_commands.Choice(name=f.name, value=f.name)
            for f in battle.fighters
            if current_lower in f.name.lower() and _can_manage_fighter(interaction, f)
        ][:25]

    @app_commands.command(name="skills", description="Show every skill a fighter currently knows")
    @app_commands.describe(fighter="Which fighter's skills to show")
    async def skills(self, interaction: discord.Interaction, fighter: str):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        target_fighter = battle.get_fighter(fighter)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        if not target_fighter.skills:
            await interaction.response.send_message(
                f"{target_fighter.name} doesn't know any skills yet.", ephemeral=True
            )
            return

        blocks = [_skill_preview_text(skill, target_fighter.custom_statuses) for skill in target_fighter.skills.values()]
        text = f"**{target_fighter.name}** knows {len(blocks)} skill(s):\n\n" + "\n\n".join(blocks)
        if len(text) > 1900:
            text = text[:1900] + "\n...(truncated, too many/long skills to show at once)"
        await interaction.response.send_message(text, ephemeral=True)

    async def _skill_name_autocomplete_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Shared by /battle removeskill and /battle editskill's skill_name option. See docs/ENGINEERING_NOTES.md#battle-skill-name-autocomplete for the full rationale."""
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            return []
        fighter_name = getattr(interaction.namespace, "fighter", None)
        if not fighter_name:
            return []
        target_fighter = battle.get_fighter(fighter_name)
        if target_fighter is None:
            return []
        current_lower = current.lower()
        return [
            app_commands.Choice(name=skill.name, value=skill.name)
            for skill in target_fighter.skills.values()
            if current_lower in skill.name.lower()
        ][:25]

    @app_commands.command(
        name="removeskill",
        description="Remove a skill from a fighter's known skills (owner or admin only)",
    )
    @app_commands.describe(fighter="Which fighter to remove a skill from", skill_name="Which skill to remove")
    async def removeskill(self, interaction: discord.Interaction, fighter: str, skill_name: str):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        target_fighter = battle.get_fighter(fighter)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        if not _can_manage_fighter(interaction, target_fighter):
            await interaction.response.send_message(
                "Only the owner or an admin can remove one of their skills.",
                ephemeral=True,
            )
            return

        if target_fighter.remove_skill(skill_name):
            await interaction.response.send_message(
                f"Removed **{skill_name}** from {target_fighter.name}.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"{target_fighter.name} doesn't know a skill called '{skill_name}'.", ephemeral=True
            )

    @removeskill.autocomplete("skill_name")
    async def removeskill_skill_name_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._skill_name_autocomplete_choices(interaction, current)

    @app_commands.command(
        name="editskill",
        description="Edit a skill a fighter already knows, for THIS battle (owner or admin only)",
    )
    @app_commands.describe(fighter="Which fighter's skill to edit", skill_name="Which skill to edit")
    async def editskill(self, interaction: discord.Interaction, fighter: str, skill_name: str):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        target_fighter = battle.get_fighter(fighter)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        if not _can_manage_fighter(interaction, target_fighter):
            await interaction.response.send_message(
                "Only the owner or an admin can edit one of their skills.",
                ephemeral=True,
            )
            return

        existing_skill = target_fighter.get_skill(skill_name)
        if existing_skill is None:
            await interaction.response.send_message(
                f"{target_fighter.name} doesn't know a skill called '{skill_name}'.", ephemeral=True
            )
            return

        await interaction.response.send_modal(EditSkillModal(target_fighter, existing_skill))

    @editskill.autocomplete("skill_name")
    async def editskill_skill_name_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._skill_name_autocomplete_choices(interaction, current)

    @app_commands.command(
        name="declare",
        description="Lock in a skill for this round, aimed at one of the target's slots",
    )
    @app_commands.describe(
        fighter="Which fighter is acting",
        slot="Which of your fighter's own skill slots to use (1-3)",
        skill_name="Which skill they're using",
        target="Which enemy fighter to aim at",
        target_slot="Which of the target's slots to aim at -- this decides if it Clashes (1-3)",
    )
    async def declare(
        self,
        interaction: discord.Interaction,
        fighter: str,
        slot: int,
        skill_name: str,
        target: str,
        target_slot: int,
    ):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        caster = battle.get_fighter(fighter)
        if caster is None:
            await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        if not _can_manage_fighter(interaction, caster):
            await interaction.response.send_message(
                "Only the owner or an admin can declare for them.", ephemeral=True
            )
            return

        if not caster.is_alive():
            await interaction.response.send_message(f"{caster.name} is down and can't act.", ephemeral=True)
            return

        if caster.get_status("immobilized") is not None:
            await interaction.response.send_message(
                f"{status_emoji('immobilized')} {caster.name} is Immobilized and can't act this turn.",
                ephemeral=True,
            )
            return

        if caster.current_stagger_tier > 0:
            await interaction.response.send_message(
                f"{stagger_action_emoji()} {caster.name} is Staggered (Tier {caster.current_stagger_tier}) "
                f"and can't act until it clears.",
                ephemeral=True,
            )
            return

        if not (1 <= slot <= caster.skill_slots):
            await interaction.response.send_message(
                f"{caster.name} only has skill slots 1-{caster.skill_slots}.", ephemeral=True
            )
            return

        if caster.slot_disabled(slot):
            part = caster.parts[slot]
            await interaction.response.send_message(
                f"{caster.name}'s {part.name} (Slot {slot}) is broken and can't act until it is repaired.",
                ephemeral=True,
            )
            return

        skill = caster.get_skill(skill_name)
        if skill is None:
            await interaction.response.send_message(
                f"{caster.name} doesn't know a skill called {skill_name}.", ephemeral=True
            )
            return

        sm_profile, sm_affordable = caster.supermove_check(slot, skill)
        if not sm_affordable:
            await interaction.response.send_message(
                f"{caster.name} can't afford **{skill.name}** right now: it {describe_profile(sm_profile)}, "
                f"and {caster.name} has {caster.sanity} Sanity.",
                ephemeral=True,
            )
            return
        sm_note = (
            f"\nE.G.O: {skill.name} {describe_profile(sm_profile)}. The cost is paid when you lock it in."
            if sm_profile else ""
        )

        target_fighter = battle.get_fighter(target)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {target}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        # A [Duality] skill aimed at an ally (or the caster) is a support
        # action: no Clash, no target slot. Aimed at an enemy it is a
        # normal attack and goes through the flow below.
        if "duality" in skill.tags and target_fighter.side == caster.side:
            if not target_fighter.is_alive() or target_fighter.eliminated:
                await interaction.response.send_message(
                    f"{target_fighter.name} is down and can't be helped.", ephemeral=True
                )
                return
            caster.declare_in_slot(slot, skill, target_fighter, 1)
            await interaction.response.send_message(
                f"{caster.name} declared **{skill.name}** (Duality, on an ally) on {target_fighter.name} "
                f"in Slot {slot}.",
                ephemeral=True,
            )
            await sync_battle_message(self.bot, battle)
            return

        if target_fighter is caster:
            await interaction.response.send_message(
                f"{caster.name} can't target themselves.", ephemeral=True
            )
            return

        if target_fighter.side == caster.side:
            await interaction.response.send_message(
                f"{caster.name} can't target {target_fighter.name}, they're on the same side "
                f"(Side {caster.side}). Only enemies can be targeted, unless the skill has the "
                f"[Duality] flag.",
                ephemeral=True,
            )
            return

        # Same one-way door as everywhere else eliminated is checked
        # (see Fighter.eliminated's docstring) -- an eliminated fighter
        # is gone for the rest of this battle, on both sides of an
        # action: they already can't act as a caster (is_alive() above
        # covers that, since elimination only ever happens at 0 HP),
        # but nothing was stopping someone from still aiming a skill AT
        # one. Checked before target_slot validation below, since an
        # eliminated fighter's own skill_slots are irrelevant once
        # they're out of the fight.
        if target_fighter.eliminated:
            await interaction.response.send_message(
                f"{target_fighter.name} has been eliminated and can't be targeted.",
                ephemeral=True,
            )
            return

        # Indiscriminate skills hit a random enemy slot, not one the
        # caster chooses -- whatever target_slot was typed in gets
        # thrown out and replaced here, before it's ever validated.
        indiscriminate_note = ""
        if "indiscriminate" in skill.tags:
            target_slot = random.randint(1, target_fighter.skill_slots)
            indiscriminate_note = (
                f" ({skill.name} is Indiscriminate -- randomly hits Slot {target_slot})"
            )

        if not (1 <= target_slot <= target_fighter.skill_slots):
            await interaction.response.send_message(
                f"{target_fighter.name} only has skill slots 1-{target_fighter.skill_slots}.",
                ephemeral=True,
            )
            return

        caster_speed = caster.slot_speed(slot)
        target_speed = target_fighter.slot_speed(target_slot)

        # [Attack Weight]: auto-pick (attack_weight - 1) extra enemy
        # slots to splash onto if this ends up resolving unopposed --
        # ANY living enemy is eligible (not just target_fighter's other
        # slots), fastest Speed first, excluding the primary
        # (target_fighter, target_slot) already chosen. Exactly ONE
        # candidate slot per distinct Fighter -- picking that Fighter's
        # own fastest eligible slot as the representative -- since HP,
        # Shield, and every status live on the Fighter object, not the
        # slot. Without this dedup, a single multi-slot enemy could be
        # picked more than once and take/gain everything N times over
        # for one Attack Weight cast. Clashes are completely unaffected
        # by this -- see combat()'s solo branch.
        #
        # Computed up here, before the clash-steal check below, so
        # EVERY path that can end up calling declare_in_slot() --
        # including the three early-return fallbacks inside the
        # clash-steal block -- carries the same splash targets. It used
        # to live after that block and only reach the main
        # ClashDeclareView() call, silently dropping Attack Weight's
        # splash whenever one of those fallbacks fired instead.
        extra_targets: list[tuple[Fighter, int]] = []
        attack_weight_note = ""
        if skill.attack_weight > 1:
            candidates = []
            for f in battle.fighters:
                if f.side == caster.side or not f.is_alive():
                    continue
                eligible_slots = [
                    s for s in range(1, f.skill_slots + 1)
                    if not (f is target_fighter and s == target_slot)
                ]
                if not eligible_slots:
                    continue
                best_slot = max(eligible_slots, key=lambda s: f.slot_speed(s))
                candidates.append((f, best_slot))
            candidates.sort(key=lambda fs: fs[0].slot_speed(fs[1]), reverse=True)
            extra_targets = candidates[: skill.attack_weight - 1]
            if extra_targets:
                attack_weight_note = (
                    f"\n\n**Attack Weight {attack_weight_icons(skill.attack_weight)}**: also splashes onto "
                    + ", ".join(f"{f.name} Slot {s}" for f, s in extra_targets)
                    + " if this resolves unopposed (Clashes are unaffected)."
                )

        partner_info = battle.find_mutual_clash_partner(target_fighter, target_slot)
        if partner_info is not None:
            partner, partner_slot = partner_info
            partner_action = partner.declared_actions.get(partner_slot)
            partner_is_target_fixed = (
                partner_action is not None and "target_fixed" in partner_action.skill.tags
            )
            if (
                partner is not caster
                and partner.side == caster.side
                and caster_speed > partner.slot_speed(partner_slot)
                and caster_speed > target_speed
            ):
                if partner_is_target_fixed:
                    caster.declare_in_slot(slot, skill, target_fighter, target_slot, extra_targets)
                    await interaction.response.send_message(
                        f"{target_fighter.name}'s Slot {target_slot} is already clashing "
                        f"{partner.name}'s **{partner_action.skill.name}**, which is Target Fixed "
                        f"so no one can take its place. Your attack will hit "
                        f"{target_fighter.name} without a Clash instead.{sm_note}",
                        ephemeral=True,
                    )
                    await sync_battle_message(self.bot, battle)
                    return

                if partner.owner_id is None:
                    caster.declare_in_slot(slot, skill, target_fighter, target_slot, extra_targets)
                    await interaction.response.send_message(
                        f"{target_fighter.name}'s Slot {target_slot} is already clashing "
                        f"{partner.name}, and taking their place needs {partner.name}'s owner to "
                        f"agree, but {partner.name} has no linked owner. Your attack will hit "
                        f"{target_fighter.name} without a Clash instead.{sm_note}",
                        ephemeral=True,
                    )
                    await sync_battle_message(self.bot, battle)
                    return

                owner = self.bot.get_user(partner.owner_id)
                if owner is None:
                    try:
                        owner = await self.bot.fetch_user(partner.owner_id)
                    except discord.NotFound:
                        owner = None

                steal_view = StealApprovalView(
                    caster, skill, slot, partner, partner_slot, target_fighter, target_slot,
                    self.bot, battle,
                )

                sent = False
                if owner is not None:
                    try:
                        dm_message = await owner.send(
                            f"**{caster.name}** (Slot {slot}, {stat_emoji('speed')}{caster_speed}) wants to take "
                            f"your **{partner.name}**'s place in the Clash against **{target_fighter.name}**'s "
                            f"Slot {target_slot}, using **{skill.name}**.\n\n"
                            f"Approve gives it to them (your Slot {partner_slot} is cleared). "
                            f"Decline keeps your clash as declared, and their attack will hit "
                            f"without a Clash instead.",
                            view=steal_view,
                        )
                        steal_view.message = dm_message
                        sent = True
                    except discord.Forbidden:
                        sent = False

                if not sent:
                    caster.declare_in_slot(slot, skill, target_fighter, target_slot, extra_targets)
                    await interaction.response.send_message(
                        f"Couldn't DM {partner.name}'s owner to ask for approval (DMs closed or "
                        f"user not found), so your attack will hit "
                        f"{target_fighter.name} without a Clash instead.{sm_note}",
                        ephemeral=True,
                    )
                    await sync_battle_message(self.bot, battle)
                    return

                await interaction.response.send_message(
                    f"{target_fighter.name}'s Slot {target_slot} is already clashing "
                    f"{partner.name}. {partner.name}'s owner has been DMed to ask whether "
                    f"{caster.name} can take their place instead. Your skill is locked in either way, "
                    f"you'll just find out later whether it's a Clash or a plain hit.{sm_note}",
                    ephemeral=True,
                )
                return

        # Deliberately no preview of the target's own skill here, ever -- you don't get to see what an enemy is bringing before you commit,... See docs/ENGINEERING_NOTES.md#battle-comment-1523.

        view = ClashDeclareView(
            caster, skill, target_fighter, slot, target_slot, self.bot, battle,
            extra_targets=extra_targets,
        )
        speed_icon = stat_emoji("speed")
        preview = (
            f"**{caster.name}**'s Slot {slot} ({speed_icon}{caster_speed}) locks in **{skill.name}** "
            f"aimed at {target_fighter.name}'s Slot {target_slot}.{indiscriminate_note}{attack_weight_note}\n\n"
            f"This only becomes a Clash if {target_fighter.name} targets your Slot {slot} back with "
            f"their Slot {target_slot}. Otherwise it resolves unopposed. You won't know which until "
            f"combat actually runs.{sm_note}\n\n"
            f"Confirm to lock it in?"
        )

        await interaction.response.send_message(preview, view=view, ephemeral=True)

    @app_commands.command(name="undeclare", description="Cancel one of your declared skill slots")
    @app_commands.describe(fighter="Which fighter is cancelling a declared action", slot="Which slot to clear (1-3)")
    async def undeclare(self, interaction: discord.Interaction, fighter: str, slot: int):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        caster = battle.get_fighter(fighter)
        if caster is None:
            await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        if not _can_manage_fighter(interaction, caster):
            await interaction.response.send_message(
                "Only the owner or an admin can undeclare for them.", ephemeral=True
            )
            return

        if caster.undeclare(slot):
            await interaction.response.send_message(
                f"Cleared {caster.name}'s Slot {slot}.", ephemeral=True
            )
            await sync_battle_message(self.bot, battle)
        else:
            await interaction.response.send_message(
                f"{caster.name}'s Slot {slot} wasn't declared.", ephemeral=True
            )

    @app_commands.command(
        name="removefighter",
        description="Remove a fighter from the current battle (owner or admin only)",
    )
    @app_commands.describe(fighter="Fighter to remove")
    async def removefighter(self, interaction: discord.Interaction, fighter: str):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        target_fighter = battle.get_fighter(fighter)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {fighter}. Pick a name from the list that shows as you type.", ephemeral=True)
            return

        if not _can_manage_fighter(interaction, target_fighter):
            await interaction.response.send_message(
                "Only the owner or an admin can remove them.", ephemeral=True
            )
            return

        battle.fighters.remove(target_fighter)
        await interaction.response.send_message(f"Removed {target_fighter.name} from the battle.")
        await sync_battle_message(self.bot, battle)

    @app_commands.command(name="combat", description="Resolve this round once everyone has declared their actions")
    async def combat(self, interaction: discord.Interaction):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        if not battle.all_declared():
            await interaction.response.send_message(
                "Not everyone has declared yet. Use /battle declare for each fighter first.",
                ephemeral=True,
            )
            return

        # This whole command can take a while now (animating every attrition round coin-by-coin), so acknowledge the interaction immediately and... See docs/ENGINEERING_NOTES.md#battle-comment-1604.
        await interaction.response.defer()
        recap_before = take_snapshot(battle)

        # Fires [Combat Start] (first round of the battle only) and [Turn Start] (every round) against every living fighter's FULL skill list, not... See docs/ENGINEERING_NOTES.md#battle-comment-1611.
        passive_log = fire_passive_triggers(battle)

        for f in battle.fighters:
            if f.is_alive() and f.get_status("immobilized") is not None:
                f.refund_all_supermoves()
                f.declared_actions = {}
                f.immobilized_blocked = True
                passive_log.append(f"{status_emoji('immobilized')} {f.name} is Immobilized and cannot act this turn.")

        entries = []
        for f in battle.fighters:
            if not f.is_alive():
                continue
            for slot_num, action in f.declared_actions.items():
                entries.append({
                    "caster": f, "skill": action.skill, "target": action.target,
                    "slot": slot_num, "target_slot": action.target_slot,
                    "extra_targets": action.extra_targets, "used": False,
                })

        # A [Duality] skill aimed at an ally never clashes or deals
        # damage: pulled out of the entries here, before any pairing
        # happens, and resolved right away fastest slot first. Its coins
        # still flip (so Coin Power, coin and Attack End lines all run),
        # and its lines land on the ally if they end in 'to the ally',
        # otherwise on the caster. See docs/ENGINEERING_NOTES.md#duality.
        def _is_ally_duality(e) -> bool:
            return "duality" in e["skill"].tags and e["target"].side == e["caster"].side

        support_entries = [e for e in entries if _is_ally_duality(e)]
        entries = [e for e in entries if not _is_ally_duality(e)]
        support_entries.sort(key=lambda e: e["caster"].slot_speed(e["slot"]), reverse=True)
        for e in support_entries:
            sup_caster, sup_ally = e["caster"], e["target"]
            if not sup_caster.is_alive():
                continue
            sup_context = TriggerContext(
                caster=sup_caster, target=sup_ally, battle=battle, ally=sup_ally,
                caster_slot_speed=sup_caster.slot_speed(e["slot"]),
                target_slot_speed=sup_ally.slot_speed(e.get("target_slot") or 0),
            )
            sup_skill, sup_pre = _resolve_pre_roll_chain(e["skill"], sup_context, PRE_ROLL_SOLO_TIMINGS)
            sup_result = resolve_skill(sup_skill, sup_caster.heads_chance(), sup_context)
            _, sup_end = resolve_triggers(sup_skill, sup_context, "attack_end")
            sup_coin = [t for c in sup_result.coin_results for t in c.fired_triggers]
            sup_lines = apply_trigger_effects(sup_pre + sup_coin + sup_end, sup_caster, None, ally=sup_ally)
            ally_note = f" on {sup_ally.name}" if sup_ally is not sup_caster else ""
            passive_log.append(f"**{sup_caster.name}** uses **{e['skill'].name}**{ally_note}.")
            passive_log.extend(sup_lines)

        DEFENSE_TAGS_NO_ATTACK = {"guard", "evade", "counter", "clashable_guard", "clashable_counter"}

        def _is_incoming_attack(skill_tags: set[str]) -> bool:
            """True for anything that represents a real incoming attack a
            Clashable defense could actually intercept -- i.e. not
            another defense skill sitting in the same `entries` list.
            """
            return not (skill_tags & DEFENSE_TAGS_NO_ATTACK)

        units = []

        # Pass 0: exact-target Clashable Guard/Clashable Counter
        # interception. Runs FIRST, before normal mutual-attack pairing,
        # scanning every declared Clashable Guard/Counter for whether
        # its own declared (target, target_slot) matches a real,
        # still-unused incoming attack this round. This replaces the
        # old apply_counter_redirects Pass 2/3, which never actually
        # checked the Clashable defense's own declared target at all --
        # it just grabbed whichever unopposed attack happened to be
        # aimed at the caster, disregarding what was actually declared.
        # A Clashable defense that mutually targets the exact attack
        # that's also targeting IT back is just a special case of this
        # same exact-match check, so the separate tight mutual-pairing
        # case below no longer needs its own handling for Clashable
        # tags -- see DEFENSE_TAGS_NO_ATTACK's addition to
        # _never_clashes just below, which keeps any Clashable defense
        # that DIDN'T find a match here from ever being treated as an
        # attacker by the normal pairing loop.
        #
        # Only handles the PROACTIVE/declared-target case. The live
        # mid-round fall-through case (an attack landing on a slot
        # whose Evade was already spent earlier this round, redirecting
        # to a still-unused Clashable elsewhere on the same fighter) is
        # a separate, not-yet-built piece -- see apply_counter_redirects's
        # docstring.
        for cd_entry in entries:
            if cd_entry["used"]:
                continue
            tags = cd_entry["skill"].tags
            if "clashable_guard" not in tags and "clashable_counter" not in tags:
                continue
            caster = cd_entry["caster"]
            already_used = (
                caster.clashable_guard_used_this_round if "clashable_guard" in tags
                else caster.clashable_counter_used_this_round
            )
            if already_used:
                continue

            exact_target = None
            for other in entries:
                if other is cd_entry or other["used"]:
                    continue
                if (
                    other["caster"] is cd_entry["target"]
                    and other["slot"] == cd_entry["target_slot"]
                    and _is_incoming_attack(other["skill"].tags)
                ):
                    exact_target = other
                    break
            if exact_target is None:
                continue

            cd_entry["used"] = True
            exact_target["used"] = True
            if "clashable_guard" in tags:
                caster.clashable_guard_used_this_round = True
            else:
                caster.clashable_counter_used_this_round = True
            units.append(("clash", cd_entry, exact_target))

        def _never_clashes(skill_tags: set[str]) -> bool:
            # clashable_guard/clashable_counter are included here too --
            # any real interception was already claimed by Pass 0 above,
            # so anything with these tags still unused at this point has
            # no valid target this round and should just idle, never
            # act as if it were an attack of its own. Plain "counter" is
            # deliberately NOT excluded here, matching original scope --
            # it's handled entirely by apply_counter_redirects.
            return bool(
                skill_tags & {"unclashable", "guard", "evade", "clashable_guard", "clashable_counter"}
            )

        def _is_plain_guard(skill_tags: set[str]) -> bool:
            return "guard" in skill_tags and "clashable_guard" not in skill_tags

        for i, entry in enumerate(entries):
            if entry["used"]:
                continue

            # Offset: two PLAIN [Guard] skills mutually targeting each
            # other's exact slot cancel completely, no effect either
            # way -- neither generates Shield. Matches canon's own
            # exception clause: [Clashable Guard] is deliberately
            # excluded (a genuine Clash there is allowed to happen
            # normally, see the clash branch's Clashable Guard reward
            # handling further down). Checked BEFORE the normal
            # _never_clashes exclusion below, since that would
            # otherwise just split these into two independent solo
            # Guards (each still raising its own Shield), which is
            # exactly what Offset says should NOT happen here.
            if _is_plain_guard(entry["skill"].tags):
                offset_partner = None
                for other in entries[i + 1:]:
                    if other["used"]:
                        continue
                    if (
                        _is_plain_guard(other["skill"].tags)
                        and other["caster"] is entry["target"]
                        and other["target"] is entry["caster"]
                        and other["target_slot"] == entry["slot"]
                        and entry["target_slot"] == other["slot"]
                    ):
                        offset_partner = other
                        break
                if offset_partner is not None:
                    entry["used"] = True
                    offset_partner["used"] = True
                    units.append(("offset", entry, offset_partner))
                    continue

            match = None
            # An Unclashable OR (plain, non-Clashable) Guard skill on EITHER side forces this to resolve unopposed, even if both sides'... See docs/ENGINEERING_NOTES.md#battle-comment-1637.
            if not _never_clashes(entry["skill"].tags):
                for other in entries[i + 1:]:
                    if other["used"]:
                        continue
                    if _never_clashes(other["skill"].tags):
                        continue
                    if (
                        other["caster"] is entry["target"]
                        and other["target"] is entry["caster"]
                        and other["target_slot"] == entry["slot"]
                        and entry["target_slot"] == other["slot"]
                    ):
                        match = other
                        break
            entry["used"] = True
            if match is not None:
                match["used"] = True
                units.append(("clash", entry, match))
            else:
                units.append(("solo", entry))

        def unit_speed(u):
            # [Guard] must always resolve before anything else, regardless of its own slot's Speed -- it's raising a defensive Shield, not racing to... See docs/ENGINEERING_NOTES.md#battle-comment-1671.
            if u[0] == "clash" or u[0] == "offset":
                a_speed = u[1]["caster"].slot_speed(u[1]["slot"])
                b_speed = u[2]["caster"].slot_speed(u[2]["slot"])
                is_guard_unit = "guard" in u[1]["skill"].tags or "guard" in u[2]["skill"].tags
                return (1 if is_guard_unit else 0, max(a_speed, b_speed))
            is_guard_unit = "guard" in u[1]["skill"].tags
            return (1 if is_guard_unit else 0, u[1]["caster"].slot_speed(u[1]["slot"]))

        units.sort(key=unit_speed, reverse=True)

        # Transforms `units` to reflect Counter / Clashable Counter interceptions -- see apply_counter_redirects's docstring above. See docs/ENGINEERING_NOTES.md#battle-comment-1690.
        units = apply_counter_redirects(units, battle)

        # Fall-through support: any still-idle Clashable Guard/Counter
        # solo unit (Pass 0 didn't find its own exact declared target)
        # is pulled OUT of the main speed-ordered list here, rather than
        # resolving its "nothing to intercept" idle turn at its own
        # natural speed position. It stays available, grouped by its
        # OWN caster, for the live fall-through check in the main loop's
        # plain-attack branch below: any attack that finds no live Evade
        # waiting for it may claim one of that SAME fighter's still-
        # unclaimed entries here instead of landing as a plain hit.
        # Whatever's left unclaimed once the whole loop finishes gets
        # its real "idle" turn rendered at the very end (see just after
        # the main loop below), in no particular order among themselves
        # since a no-op has nothing left to be ordered against.
        deferred_clashable_by_fighter: dict[int, list[dict]] = {}
        still_active_units = []
        for u in units:
            if u[0] == "solo" and (u[1]["skill"].tags & {"clashable_guard", "clashable_counter"}):
                deferred_clashable_by_fighter.setdefault(id(u[1]["caster"]), []).append(u[1])
            else:
                still_active_units.append(u)
        units = still_active_units

        # locked_lines holds everything PERMANENTLY decided so far this Combat Phase: the passive-trigger block (if any), then one one-line... See docs/ENGINEERING_NOTES.md#battle-comment-1697.
        locked_lines: list[str] = []
        if passive_log:
            locked_lines.append("**Passive Triggers**\n" + "\n".join(passive_log))

        combat_title = f"Combat Phase, Round {battle.round_number}"
        initial_embed = discord.Embed(
            title=combat_title,
            description="\n\n".join(locked_lines) or "Starting Combat Phase...",
            color=0x5865F2,
        )
        combat_message = await interaction.followup.send(embed=initial_embed, wait=True)

        async def render(live_block: str | None):
            """Re-renders the ONE shared combat_message: every locked (finished) line, plus whatever the current unit's live animation looks like right... See docs/ENGINEERING_NOTES.md#battle-battlecog-combat-render for the full rationale."""
            parts = []
            base = "\n\n".join(locked_lines)
            if base:
                parts.append(base)
            if live_block:
                parts.append(live_block)
            description = "\n\n".join(parts) if parts else "Nothing declared this round."
            if len(description) > 4000:
                description = description[-4000:]
            embed = discord.Embed(title=combat_title, description=description, color=0x5865F2)
            await combat_message.edit(embed=embed)

        # Coin-by-coin animation is pure PRESENTATION over results that are already fully computed the instant resolve_skill/... See docs/ENGINEERING_NOTES.md#battle-comment-1732.
        COIN_FACE_DELAY = 0.55
        COIN_DETAIL_DELAY = 0.4

        async def animate_faces(live_header: str, coin_results: list) -> str:
            """Phase one: reveals each coin's face one at a time, rolling icon first, mirroring a real Limbus clash flipping its coins in sequence. See docs/ENGINEERING_NOTES.md#battle-battlecog-combat-animate-faces for the full rationale."""
            n = len(coin_results)
            revealed: list[str] = []
            for c in coin_results:
                pending = revealed + [coin_roll_emoji()] * (n - len(revealed))
                await render(live_header + "\n  " + " ".join(pending))
                await asyncio.sleep(COIN_FACE_DELAY)
                revealed.append(coin_emoji("heads") if c.heads else coin_emoji("tails"))
            face_line = "  " + " ".join(revealed)
            await render(live_header + "\n" + face_line)
            await asyncio.sleep(0.35)
            return face_line

        async def animate_power(live_header: str, face_line: str, coin_results: list) -> str:
            """Phase two for an ATTRITION ROUND toss: once every coin's face is showing, reveal each coin's running Power one at a time (this is a... See docs/ENGINEERING_NOTES.md#battle-battlecog-combat-animate-power for the full rationale."""
            lines = []
            for i, c in enumerate(coin_results, start=1):
                lines.append(f"  Coin {i}: Power {c.power_after}")
                await render(live_header + "\n" + face_line + "\n" + "\n".join(lines))
                await asyncio.sleep(COIN_DETAIL_DELAY)
            return "\n".join(lines)

        async def animate_damage(live_header: str, face_line: str, coin_results: list, hit_log: list[str]) -> str:
            """Phase two for the FINAL DECISIVE toss -- "once all the coins break, it goes through the animation again for the damage output one by one... See docs/ENGINEERING_NOTES.md#battle-battlecog-combat-animate-damage for the full rationale."""
            lines = []
            for i in range(1, len(coin_results) + 1):
                coin_lines = [l for l in hit_log if l.startswith(f"Coin {i}:")]
                lines.extend(coin_lines if coin_lines else [f"Coin {i}: no additional effect"])
                await render(live_header + "\n" + face_line + "\n" + "\n".join(lines))
                await asyncio.sleep(COIN_DETAIL_DELAY)
            return "\n".join(lines)

        summary_lines: list[str] = []
        full_log_entries: list[tuple[str, str]] = []
        # Tracks whether we've resolved anything yet this Combat Phase, for
        # the first_hit_of_round Trigger condition. One clash counts as a
        # single event (both sides share the same flag value).
        first_action_done = False

        async def resolve_clash_unit(entry_a: dict, entry_b: dict):
            """The full Clash resolution -- everything from the original
            inline `if u[0] == "clash":` branch, pulled out into its own
            callable closure so it can ALSO be invoked reactively, mid-
            loop, for a fall-through interception (a plain attack that
            finds no live Evade waiting for it may redirect into an
            ad-hoc Clash against a still-unused Clashable Guard/Counter
            on the same target fighter -- see the fall-through check in
            the main loop's plain-attack branch below). Nothing about
            the actual resolution logic changed from the original inline
            version; this is a pure extraction, not a rewrite.
            """
            nonlocal first_action_done
            fighter_a, fighter_b = entry_a["caster"], entry_b["caster"]
            if not fighter_a.is_alive() or not fighter_b.is_alive():
                return

            live_header = f"⚔️ **{fighter_a.name}** vs **{fighter_b.name}**"
            await render(live_header)
            await asyncio.sleep(0.3)

            speed_a = fighter_a.slot_speed(entry_a["slot"])
            speed_b = fighter_b.slot_speed(entry_b["slot"])
            context_a = TriggerContext(
                caster=fighter_a, target=fighter_b, battle=battle,
                is_first_hit_of_round=not first_action_done,
                caster_slot_speed=speed_a, target_slot_speed=speed_b,
            )
            context_b = TriggerContext(
                caster=fighter_b, target=fighter_a, battle=battle,
                is_first_hit_of_round=not first_action_done,
                caster_slot_speed=speed_b, target_slot_speed=speed_a,
            )
            # Full pre-roll chain for both sides -- see PRE_ROLL_CLASH_TIMINGS / _resolve_pre_roll_chain above for firing order and the documented... See docs/ENGINEERING_NOTES.md#battle-comment-1802.
            skill_a, pre_roll_post_hit_a = _resolve_pre_roll_chain(
                entry_a["skill"], context_a, PRE_ROLL_CLASH_TIMINGS
            )
            skill_b, pre_roll_post_hit_b = _resolve_pre_roll_chain(
                entry_b["skill"], context_b, PRE_ROLL_CLASH_TIMINGS
            )
            first_action_done = True

            # Fully resolved instantly, same as always -- everything
            # below this point is presentation over already-decided
            # numbers, see the animate_* helpers' docstrings above.
            outcome = resolve_round_clash(
                skill_a, skill_b,
                heads_chance_a=fighter_a.heads_chance(),
                heads_chance_b=fighter_b.heads_chance(),
                context_a=context_a,
                context_b=context_b,
                power_bonus_a=max(0, fighter_a.level - fighter_b.level),
                power_bonus_b=max(0, fighter_b.level - fighter_a.level),
            )
            winner = fighter_a if outcome.winner == "a" else fighter_b
            loser = fighter_b if winner is fighter_a else fighter_a
            winner_skill = skill_a if outcome.winner == "a" else skill_b
            loser_skill = skill_b if outcome.winner == "a" else skill_a
            winner_context = context_a if outcome.winner == "a" else context_b
            loser_context = context_b if outcome.winner == "a" else context_a
            winner_pre_roll_post_hit = pre_roll_post_hit_a if outcome.winner == "a" else pre_roll_post_hit_b

            # Clash win Sanity scales with the winner's coin count --
            # NOT a flat amount -- computed once here so both the
            # actual gain and the display text below agree.
            clash_win_sanity = SANITY_PER_COIN_CLASH_WIN * outcome.winner_final_result.skill.coins
            winner.gain_sanity(clash_win_sanity)
            loser.lose_sanity(SANITY_CLASH_LOSS)

            # [Clash Win] and [Attack End] only fire for the winner -- the loser never actually lands a hit, so neither an on-hit-family Trigger nor... See docs/ENGINEERING_NOTES.md#battle-comment-1839.
            _, clash_win_post_hit = resolve_triggers(winner_skill, winner_context, "clash_win")
            _, attack_end_post_hit = resolve_triggers(winner_skill, winner_context, "attack_end")
            _, turn_end_post_hit_winner = resolve_triggers(winner_skill, winner_context, "turn_end")
            _, clash_lose_post_hit = resolve_triggers(loser_skill, loser_context, "clash_lose")
            _, hit_after_clash_lose_post_hit = resolve_triggers(loser_skill, loser_context, "hit_after_clash_lose")
            _, turn_end_post_hit_loser = resolve_triggers(loser_skill, loser_context, "turn_end")

            # Clashable Guard replaces normal clash damage with its own reward/mitigation, on EITHER side -- see GUARD_STAGGER_THRESHOLD_RAISE /... See docs/ENGINEERING_NOTES.md#battle-comment-1853.
            winner_is_clashable_guard = "clashable_guard" in winner_skill.tags
            loser_is_clashable_guard = "clashable_guard" in loser_skill.tags
            guard_note: str | None = None
            winner_slot = entry_a["slot"] if outcome.winner == "a" else entry_b["slot"]
            loser_slot = entry_b["slot"] if outcome.winner == "a" else entry_a["slot"]

            if winner_is_clashable_guard:
                total_damage, status_log, per_coin_triggers = 0, [], []
                evaded = False
                raised = []
                for i in range(3):
                    if loser.stagger_tiers_enabled[i]:
                        loser.stagger_thresholds[i] = min(
                            1.0, loser.stagger_thresholds[i] + GUARD_STAGGER_THRESHOLD_RAISE
                        )
                        raised.append(f"Tier {i + 1} -> {round(loser.stagger_thresholds[i] * 100)}%")
                if raised:
                    guard_note = (
                        f"{status_emoji('clashable_guard')} {winner.name}'s Clashable Guard succeeds! "
                        f"{loser.name}'s Stagger thresholds rise: {', '.join(raised)}"
                    )
            else:
                # A Clash's own winner-vs-loser hit checks the loser's
                # declared Evade the same as any other incoming hit --
                # if it was aimed at exactly this winning slot, it
                # gets its shot here, before damage is even computed.
                evaded, status_log = resolve_evade(loser, winner, winner_slot, battle)
                if evaded:
                    total_damage, per_coin_triggers = 0, []
                else:
                    total_damage, hit_log, per_coin_triggers = apply_incoming_hit(
                        winner_skill, outcome.winner_final_result, loser, winner, loser_slot
                    )
                    status_log += hit_log
                if not evaded and loser_is_clashable_guard and total_damage > 0:
                    reduced = round(total_damage * (100 - GUARD_LOSE_DAMAGE_REDUCTION_PCT) / 100)
                    guard_note = (
                        f"{status_emoji('clashable_guard')} {loser.name}'s Clashable Guard mitigates the "
                        f"hit: {total_damage} -> {reduced} damage"
                    )
                    total_damage = reduced

            loser.take_damage(total_damage)
            status_log += loser.drain_survival_notes()
            status_log += loser.hit_part(loser_slot, total_damage)
            loser_stagger_before = loser.current_stagger_tier
            loser.check_stagger(battle.round_number)
            stagger_post_hit: list[Trigger] = []
            if loser.current_stagger_tier > 0:
                _, stagger_post_hit = resolve_triggers(winner_skill, winner_context, "on_stagger")
            winner_was_crit = any(c.is_crit for c in outcome.winner_final_result.coin_results)
            kill_log = fire_kill_triggers(winner, loser, winner_skill, winner_context, winner_was_crit)
            tremor_log = fire_tremor_burst(winner, loser, winner_skill, winner_context)
            bleed_log = apply_bleed_self_damage(winner, winner_skill)
            trigger_log = apply_trigger_effects(
                winner_pre_roll_post_hit + clash_win_post_hit + attack_end_post_hit
                + turn_end_post_hit_winner + per_coin_triggers
                + outcome.winner_before_attack_post_hit + stagger_post_hit,
                winner, loser,
            )
            trigger_log += kill_log + tremor_log + bleed_log
            trigger_log += apply_trigger_effects(
                clash_lose_post_hit + hit_after_clash_lose_post_hit + turn_end_post_hit_loser, loser, winner
            )
            trigger_log += fire_hit_ally_reactions(
                battle, winner, loser, total_damage, winner_was_crit, loser_stagger_before, evaded
            )
            # [On Evade] is the LOSER's own reaction -- they're the one who just got hit by the winner's final toss, see fire_evade_triggers for why... See docs/ENGINEERING_NOTES.md#battle-comment-1904.
            if evaded:
                trigger_log += fire_evade_triggers(loser, winner, battle)
            if guard_note:
                trigger_log.append(guard_note)
            # ---- Animate every attrition round in full ----
            round_summaries: list[str] = []
            for round_idx, r in enumerate(outcome.rounds, start=1):
                round_header = (
                    live_header + "\n" + "\n".join(round_summaries)
                    + ("\n" if round_summaries else "")
                    + f"Round {round_idx}: {fighter_a.name} ({r.coins_a_before} coins) "
                    + f"vs {fighter_b.name} ({r.coins_b_before} coins)"
                )

                a_face = await animate_faces(f"{round_header}\n{fighter_a.name}:", r.result_a.coin_results)
                a_power = await animate_power(f"{round_header}\n{fighter_a.name}:", a_face, r.result_a.coin_results)
                a_block = f"{fighter_a.name}:\n{a_face}\n{a_power}"

                b_face = await animate_faces(f"{round_header}\n{a_block}\n{fighter_b.name}:", r.result_b.coin_results)
                b_power = await animate_power(f"{round_header}\n{a_block}\n{fighter_b.name}:", b_face, r.result_b.coin_results)

                if r.loser == "a":
                    result_line = f"{fighter_a.name} loses a coin"
                elif r.loser == "b":
                    result_line = f"{fighter_b.name} loses a coin"
                else:
                    result_line = "Tie, nobody loses a coin"

                full_round_block = f"{round_header}\n{a_block}\n{fighter_b.name}:\n{b_face}\n{b_power}\n{result_line}"
                await render(full_round_block)
                await asyncio.sleep(0.6)

                round_summaries.append(
                    f"Round {round_idx}: {fighter_a.name} Power {r.result_a.final_power} vs "
                    f"{fighter_b.name} Power {r.result_b.final_power} -> {result_line}"
                )

            # ---- Animate the winner's final decisive toss ----
            final_header = (
                live_header + "\n" + "\n".join(round_summaries)
                + f"\n\n**{winner.name}'s final attack** "
                + f"({outcome.winner_final_result.skill.coins} coins remaining):"
            )
            final_face = await animate_faces(final_header, outcome.winner_final_result.coin_results)
            await animate_damage(final_header, final_face, outcome.winner_final_result.coin_results, status_log)
            await asyncio.sleep(0.4)

            # ---- Same full detail text + one-line summary as before, for the Full Log button and locked history ----
            field_value = format_clash_rounds(outcome, fighter_a.name, fighter_b.name)
            level_gap = fighter_a.level - fighter_b.level
            if level_gap:
                higher = fighter_a if level_gap > 0 else fighter_b
                field_value = (
                    f"Level gap: {higher.name} (Level {higher.level}) gets +{abs(level_gap)} "
                    f"Power in every clash round.\n\n" + field_value
                )
            intercept_holder_entry = (
                entry_a if entry_a.get("is_clashable_counter_intercept")
                else entry_b if entry_b.get("is_clashable_counter_intercept")
                else None
            )
            if intercept_holder_entry is not None:
                holder = intercept_holder_entry["caster"]
                intercept_name = (
                    "Clashable Guard" if "clashable_guard" in intercept_holder_entry["skill"].tags
                    else "Clashable Counter"
                )
                intercept_tag = "clashable_guard" if intercept_name == "Clashable Guard" else "clashable_counter"
                field_value = (
                    f"{status_emoji(intercept_tag)} {holder.name}'s {intercept_name} intercepts an unopposed attack!\n\n"
                    + field_value
                )
            field_value += (
                f"\n\n**{winner.name}'s final attack** "
                f"({outcome.winner_final_result.skill.coins} coins remaining):\n"
            )
            field_value += format_skill_result(outcome.winner_final_result)
            field_value += (
                f"\n\n**{winner.name} wins the clash.** {loser.name} takes {total_damage} damage. "
                f"({loser.name}: {loser.hp}/{loser.max_hp} HP)"
            )
            field_value += (
                f"\n{winner.name} Sanity +{clash_win_sanity} ({winner.sanity}), "
                f"{loser.name} Sanity -{SANITY_CLASH_LOSS} ({loser.sanity})"
            )
            if status_log:
                field_value += "\n" + "\n".join(status_log)
            if trigger_log:
                field_value += "\n" + "\n".join(trigger_log)

            summary_line = (
                f"⚔️ **{winner.name}** beats **{loser.name}** -- {total_damage} damage "
                f"({loser.name}: {loser.hp}/{loser.max_hp} HP)"
            )
            summary_lines.append(summary_line)
            full_log_entries.append((f"{fighter_a.name} vs {fighter_b.name}", field_value))

            # Lock this unit's summary permanently into the message,
            # clear the live block, and move on to the next unit.
            locked_lines.append(summary_line)
            await render(None)

        for u in units:
            if u[0] == "offset":
                _, entry_a, entry_b = u
                fighter_a, fighter_b = entry_a["caster"], entry_b["caster"]
                if not fighter_a.is_alive() or not fighter_b.is_alive():
                    continue
                live_header = (
                    f"⚖️ **{fighter_a.name}**'s Guard offsets **{fighter_b.name}**'s Guard "
                    f"-- both cancel, no effect."
                )
                await render(live_header)
                await asyncio.sleep(0.5)
                summary_line = (
                    f"⚖️ **{fighter_a.name}** and **{fighter_b.name}** Offset each other -- "
                    f"both Guards cancel, no Shield for either side."
                )
                summary_lines.append(summary_line)
                full_log_entries.append((f"{fighter_a.name} vs {fighter_b.name} (Offset)", live_header))
                locked_lines.append(summary_line)
                await render(None)
                continue
            if u[0] == "clash":
                _, entry_a, entry_b = u
                await resolve_clash_unit(entry_a, entry_b)
                continue
            else:
                _, entry = u
                fighter = entry["caster"]
                target = entry["target"]
                if not fighter.is_alive() or not target.is_alive():
                    continue

                is_counter = entry.get("is_counter_retaliation", False)
                is_guard = "guard" in entry["skill"].tags and not is_counter
                is_evade_readying = "evade" in entry["skill"].tags and not is_counter
                is_clashable_defense_idle = (
                    bool(entry["skill"].tags & {"clashable_guard", "clashable_counter"}) and not is_counter
                )
                is_plain_attack = not (is_counter or is_guard or is_evade_readying or is_clashable_defense_idle)

                # Fall-through: this is checked and, if eligible, fully
                # handled BEFORE any of the normal solo-branch work below
                # (context/pre-roll/resolve_skill/Sanity gain) -- letting
                # any of that run first and discarding it afterward would
                # be wrong, not just wasteful, since fighter.gain_sanity
                # for an "unopposed hit" mutates real state that must
                # NOT happen if this turns out to be a redirected Clash
                # instead (Clashes award Sanity under a completely
                # different rule). find_eligible_evade is a pure lookup,
                # safe to call here just to check without committing --
                # only resolve_evade (used later, for a genuine plain
                # hit) actually marks a slot used.
                if is_plain_attack:
                    would_be_evaded = find_eligible_evade(target, fighter, entry["slot"]) is not None
                    if not would_be_evaded:
                        available = deferred_clashable_by_fighter.get(id(target), [])
                        if available:
                            available.sort(key=lambda e: e["caster"].slot_speed(e["slot"]), reverse=True)
                            clashable_entry = available.pop(0)
                            is_cg = "clashable_guard" in clashable_entry["skill"].tags
                            if is_cg:
                                target.clashable_guard_used_this_round = True
                            else:
                                target.clashable_counter_used_this_round = True
                            entry["is_clashable_counter_intercept"] = True
                            clashable_entry["is_clashable_counter_intercept"] = True
                            await resolve_clash_unit(entry, clashable_entry)
                            continue

                if is_counter:
                    live_header = f"{status_emoji('counter')} **{fighter.name}**'s Counter redirects -> strikes **{target.name}** back!"
                elif is_guard:
                    live_header = f"{status_emoji('guard')} **{fighter.name}** raises Guard!"
                elif is_evade_readying:
                    live_header = (
                        f"{status_emoji('evasion')} **{fighter.name}** readies Evade against "
                        f"**{target.name}**'s Slot {entry['target_slot']}!"
                    )
                elif is_clashable_defense_idle:
                    idle_tag = "clashable_guard" if "clashable_guard" in entry["skill"].tags else "clashable_counter"
                    live_header = (
                        f"{status_emoji(idle_tag)} **{fighter.name}** readies "
                        f"{'Clashable Guard' if idle_tag == 'clashable_guard' else 'Clashable Counter'} -- "
                        f"nothing to intercept this round."
                    )
                else:
                    live_header = f"**{fighter.name}** -> **{target.name}** (unopposed)"
                await render(live_header)
                await asyncio.sleep(0.3)

                context = TriggerContext(
                    caster=fighter, target=target, battle=battle,
                    is_first_hit_of_round=not first_action_done,
                    caster_slot_speed=fighter.slot_speed(entry["slot"]),
                    target_slot_speed=target.slot_speed(entry.get("target_slot") or 0),
                )

                # Full pre-roll chain -- see PRE_ROLL_SOLO_TIMINGS / _resolve_pre_roll_chain above. See docs/ENGINEERING_NOTES.md#battle-comment-2015.
                adjusted_skill, pre_roll_post_hit = _resolve_pre_roll_chain(
                    entry["skill"], context, PRE_ROLL_SOLO_TIMINGS
                )
                first_action_done = True

                result = resolve_skill(adjusted_skill, fighter.heads_chance(), context)
                heads_landed = sum(1 for c in result.coin_results if c.heads)
                sanity_gain = heads_landed * SANITY_PER_HEADS_UNOPPOSED
                fighter.gain_sanity(sanity_gain)

                if is_evade_readying:
                    # Evade's OWN declared entry never deals damage or
                    # rolls its coin here -- its real win/lose roll only
                    # happens reactively, via resolve_evade, at the exact
                    # moment its watched slot is actually attacked. If
                    # that never happens this round, this entry is a
                    # pure no-op. evade_used_slots is untouched here --
                    # only resolve_evade marks a slot used.
                    total_damage = 0
                    status_log: list[str] = []
                    trigger_log: list[str] = []
                elif is_clashable_defense_idle:
                    # Same idle shape as is_evade_readying above -- Pass
                    # 0 (see the `entries` setup earlier in combat())
                    # already checked whether this Clashable defense's
                    # own declared target matched a real attack this
                    # round, and it didn't, so it never actually clashed
                    # anything and never marks itself used. It's simply
                    # sitting here unresolved for the round.
                    total_damage = 0
                    status_log: list[str] = []
                    trigger_log: list[str] = []
                elif is_guard:
                    # Guard never deals damage -- its Final Power (the Power reached after the last coin, same value a Clash would compare) becomes Shield HP... See docs/ENGINEERING_NOTES.md#battle-comment-2034.
                    shield_gained = result.final_power
                    fighter.shield += shield_gained
                    total_damage = 0
                    status_log: list[str] = []
                    _, unopposed_post_hit = resolve_triggers(adjusted_skill, context, "on_unopposed_attack")
                    _, attack_end_post_hit = resolve_triggers(adjusted_skill, context, "attack_end")
                    _, turn_end_post_hit = resolve_triggers(adjusted_skill, context, "turn_end")
                    trigger_log = apply_trigger_effects(
                        pre_roll_post_hit + unopposed_post_hit + attack_end_post_hit + turn_end_post_hit,
                        fighter, target,
                    )
                else:
                    attacker_slot = entry["slot"]
                    evaded, status_log = resolve_evade(target, fighter, attacker_slot, battle, skip_evade=is_counter)
                    if evaded:
                        total_damage, per_coin_triggers = 0, []
                    else:
                        total_damage, hit_log, per_coin_triggers = apply_incoming_hit(
                            adjusted_skill, result, target, fighter, entry["target_slot"]
                        )
                        status_log += hit_log
                    target.take_damage(total_damage)
                    status_log += target.drain_survival_notes()
                    status_log += target.hit_part(entry["target_slot"], total_damage)
                    target_stagger_before = target.current_stagger_tier
                    target.check_stagger(battle.round_number)
                    stagger_post_hit: list[Trigger] = []
                    if target.current_stagger_tier > 0:
                        _, stagger_post_hit = resolve_triggers(adjusted_skill, context, "on_stagger")
                    fighter_was_crit = any(c.is_crit for c in result.coin_results)
                    kill_log = fire_kill_triggers(fighter, target, adjusted_skill, context, fighter_was_crit)
                    tremor_log = fire_tremor_burst(fighter, target, adjusted_skill, context)
                    bleed_log = apply_bleed_self_damage(fighter, adjusted_skill)
                    if is_counter:
                        _, before_getting_hit_post = resolve_triggers(adjusted_skill, context, "before_getting_hit")
                        trigger_log = apply_trigger_effects(before_getting_hit_post + stagger_post_hit, fighter, target)
                        trigger_log += kill_log + tremor_log + bleed_log
                    else:
                        _, unopposed_post_hit = resolve_triggers(adjusted_skill, context, "on_unopposed_attack")
                        _, attack_end_post_hit = resolve_triggers(adjusted_skill, context, "attack_end")
                        _, turn_end_post_hit = resolve_triggers(adjusted_skill, context, "turn_end")
                        trigger_log = apply_trigger_effects(
                            pre_roll_post_hit + unopposed_post_hit + attack_end_post_hit
                            + turn_end_post_hit + per_coin_triggers + stagger_post_hit,
                            fighter, target,
                        )
                        trigger_log += kill_log + tremor_log + bleed_log
                        # [On Evade] is the TARGET's own reaction to this unopposed attack -- doesn't apply to a Counter retaliation, which explicitly bypasses it via skip_evade above. See docs/ENGINEERING_NOTES.md#battle-comment-2075.
                        if evaded:
                            trigger_log += fire_evade_triggers(target, fighter, battle)
                    trigger_log += fire_hit_ally_reactions(
                        battle, fighter, target, total_damage, fighter_was_crit,
                        target_stagger_before, evaded,
                    )

                # [Attack Weight]: splash the ALREADY-COMPUTED hit onto
                # any extra enemy slots this skill also reaches (picked
                # in declare(), fastest-Speed order). Deliberately does
                # NOT re-toss coins, re-consume the caster's Poise, or
                # re-fire per-coin Triggers -- all of that only ever
                # happens once, against the primary target above. Each
                # splash target DOES get its own resistance applied
                # (different fighters can resist differently), its own
                # Stagger check, its own Evasion check (the whole splash
                # is either fully dodged or not, treated as one single
                # coin-equivalent hit rather than per-coin), its own
                # plain Counter check (handled earlier, in
                # apply_counter_redirects, before this loop ever runs --
                # a splash target with an eligible Counter has already
                # been pulled out of extra_targets and turned into its
                # own real retaliation unit, so it never reaches this
                # loop body at all), AND -- same as the primary target
                # -- its own per-coin Rupture and coin-status accrual,
                # walked coin-by-coin exactly like apply_incoming_hit
                # above, so a multi-coin skill inflicts the correct
                # per-coin total on a splash target too (not zero, and
                # not the primary target's own Rupture bonus leaking
                # over -- each splash target's Rupture is entirely its
                # own, checked and decayed against ITS OWN status, never
                # the primary target's). This is inflicted exactly ONCE
                # per splash target regardless of how many of that
                # Fighter's slots the attack notionally reached, since
                # candidate selection above already dedupes to one
                # representative slot per distinct Fighter.
                # [Clashable Guard]/[Clashable Counter] are deliberately
                # excluded from all of this (no special interaction with
                # a splash at all, same exception canon draws for
                # Offset) -- a splash into one of those slots just deals
                # plain damage below, same as any other target with no
                # defense skill declared. Guard never splashes (it deals
                # no damage to begin with).
                splash_log: list[str] = []
                if not is_guard:
                    for splash_fighter, splash_slot in entry.get("extra_targets", []):
                        if not splash_fighter.is_alive():
                            continue

                        # If this splash slot belongs to the SAME Fighter
                        # as the primary target (one of their other slots
                        # got auto-picked in declare()), it's a real
                        # "reach" narratively, but mechanically a no-op:
                        # this Fighter already took the full primary hit
                        # above, and splashing onto them again would
                        # double the damage/status a single enemy takes
                        # from one cast. Total effect stays identical to
                        # a skill with no Attack Weight at all.
                        if splash_fighter is target:
                            splash_log.append(
                                f"Attack Weight also reaches {splash_fighter.name} Slot {splash_slot}, "
                                f"but that's the same target already hit above -- no extra effect."
                            )
                            continue

                        splash_evaded, splash_evade_log = resolve_evade(
                            splash_fighter, fighter, entry["slot"], battle
                        )
                        if splash_evaded:
                            splash_log.extend(splash_evade_log)
                            splash_log.extend(fire_evade_triggers(splash_fighter, fighter, battle))
                            continue

                        # Stagger multiplier parity: captured BEFORE this
                        # splash's own damage lands, exactly like
                        # apply_incoming_hit does for the primary target --
                        # a target already staggered when the splash
                        # arrives gets the multiplier; a target the splash
                        # itself newly staggers does not.
                        splash_stagger_multiplier = (
                            STAGGER_MULTIPLIERS[splash_fighter.current_stagger_tier - 1]
                            if splash_fighter.current_stagger_tier > 0 else 1.0
                        )

                        splash_total = 0
                        for i, coin in enumerate(result.coin_results):
                            if coin.is_evaded:
                                continue
                            raw_coin = coin.damage_dealt + (coin.crit_bonus_damage if coin.is_crit else 0)
                            resisted_coin = apply_resistance(
                                raw_coin, splash_fighter.resistances_for_slot(splash_slot).get(adjusted_skill.damage_type, 1.0)
                            )
                            coin_total = resisted_coin

                            decayed_splash_rupture = None
                            splash_rupture = splash_fighter.get_status("rupture")
                            if splash_rupture is not None:
                                splash_rupture_damage = min(
                                    splash_rupture.potency,
                                    round(splash_fighter.max_hp * RUPTURE_DAMAGE_CAP_PCT),
                                )
                                coin_total += splash_rupture_damage
                                splash_log.append(
                                    f"Coin {i + 1}: {status_emoji('rupture')} Rupture triggers on "
                                    f"{splash_fighter.name}: +{splash_rupture_damage} damage"
                                )
                                decayed_splash_rupture = decay_after_trigger(splash_rupture)
                                splash_fighter.set_status_instance(decayed_splash_rupture)

                            status_name = (
                                adjusted_skill.coin_statuses[i]
                                if i < len(adjusted_skill.coin_statuses) else None
                            )
                            if status_name:
                                resistance_multiplier = splash_fighter.resistances_for_slot(splash_slot).get(status_name, 1.0)
                                raw_potency = (
                                    adjusted_skill.coin_status_potencies[i]
                                    if i < len(adjusted_skill.coin_status_potencies) else 0
                                )
                                added_potency = apply_resistance(raw_potency, resistance_multiplier)
                                added_count = (
                                    adjusted_skill.coin_status_counts[i]
                                    if i < len(adjusted_skill.coin_status_counts) else 0
                                )
                                current = (
                                    decayed_splash_rupture if status_name == "rupture"
                                    else splash_fighter.get_status(status_name)
                                )
                                new_instance = apply_status(current, status_name, added_potency, added_count)
                                splash_fighter.set_status_instance(new_instance)
                                splash_log.append(
                                    f"Coin {i + 1}: {splash_fighter.name} gains "
                                    f"{status_emoji(status_name)} {status_name.capitalize()}: "
                                    f"{new_instance.potency}/{new_instance.count}"
                                )

                            splash_total += coin_total

                        if splash_stagger_multiplier != 1.0 and splash_total > 0:
                            boosted_splash_total = round(splash_total * splash_stagger_multiplier)
                            splash_log.append(
                                f"{stagger_tier_emoji(splash_fighter.current_stagger_tier)} {splash_fighter.name} is Staggered (Tier "
                                f"{splash_fighter.current_stagger_tier}): {splash_total} -> "
                                f"{boosted_splash_total} damage (x{splash_stagger_multiplier})"
                            )
                            splash_total = boosted_splash_total

                        splash_mod, splash_parts = _damage_mods(
                            splash_fighter, fighter, adjusted_skill.damage_type, adjusted_skill.damage_pct
                        )
                        if splash_parts and splash_total > 0:
                            frag_splash_total = round(splash_total * splash_mod)
                            splash_log.append(
                                f"{status_emoji('fragile')} {', '.join(splash_parts)}: "
                                f"{splash_total} -> {frag_splash_total} damage"
                            )
                            splash_total = frag_splash_total

                        splash_shield_absorbed, splash_hp_lost = splash_fighter.take_damage(splash_total)
                        splash_log.extend(splash_fighter.drain_survival_notes())
                        splash_log += splash_fighter.hit_part(splash_slot, splash_total)
                        splash_stagger_before = splash_fighter.current_stagger_tier
                        splash_fighter.check_stagger(battle.round_number)
                        splash_breakdown = (
                            f" ({splash_shield_absorbed} absorbed by {status_emoji('shield')} Shield, "
                            f"{splash_hp_lost} to HP)"
                            if splash_shield_absorbed > 0 else ""
                        )
                        splash_log.append(
                            f"⯀ Attack Weight splashes "
                            f"{splash_fighter.name} Slot {splash_slot} for {splash_total} damage."
                            f"{splash_breakdown} ({splash_fighter.name}: {splash_fighter.hp}/{splash_fighter.max_hp} HP)"
                        )

                        # [On Stagger] parity: the primary target already
                        # fires this on the attacker's skill via
                        # stagger_post_hit further up when THEY get
                        # staggered -- a splash target getting staggered
                        # by the same attack deserves the same trigger,
                        # with its OWN Fighter as the target (so an
                        # inflict_status effect lands on the actual
                        # splash target, not the primary one). Own
                        # TriggerContext for the same reason.
                        if splash_fighter.current_stagger_tier > 0:
                            splash_stagger_context = TriggerContext(
                                caster=fighter, target=splash_fighter, battle=battle,
                            )
                            _, splash_stagger_post_hit = resolve_triggers(
                                adjusted_skill, splash_stagger_context, "on_stagger"
                            )
                            splash_log.extend(
                                apply_trigger_effects(splash_stagger_post_hit, fighter, splash_fighter)
                            )

                        # [Ally ...] reactions hear a splash hit the same as a
                        # primary one: its attacker's allies get On Hit / On
                        # Crit / On Kill, the splash target's allies get Is Hit
                        # / On Stagger (evaded or zero-damage splashes stay silent).
                        splash_log.extend(
                            fire_hit_ally_reactions(
                                battle, fighter, splash_fighter, splash_total,
                                any(c.is_crit for c in result.coin_results),
                                splash_stagger_before, False,
                            )
                        )

                # ---- Animate: no attrition rounds for an unopposed attack, straight to the decisive toss ----
                final_header = live_header
                final_face = await animate_faces(final_header, result.coin_results)
                if not is_guard and not is_evade_readying and not is_clashable_defense_idle:
                    await animate_damage(final_header, final_face, result.coin_results, status_log)
                await asyncio.sleep(0.4)

                if is_evade_readying:
                    field_value = f"{fighter.name} readies Evade against {target.name}'s Slot {entry['target_slot']}."
                elif is_clashable_defense_idle:
                    idle_name = "Clashable Guard" if "clashable_guard" in entry["skill"].tags else "Clashable Counter"
                    field_value = f"{fighter.name} readies {idle_name} -- nothing to intercept this round."
                elif is_guard:
                    field_value = format_skill_result(result)
                    field_value += (
                        f"\n\n{status_emoji('shield')} {fighter.name} gains {shield_gained} Shield HP. "
                        f"(Shield: {fighter.shield})"
                    )
                    field_value += f"\n{fighter.name} Sanity +{sanity_gain} ({fighter.sanity})"
                    if trigger_log:
                        field_value += "\n" + "\n".join(trigger_log)
                else:
                    field_value = format_skill_result(result)
                    field_value += (
                        f"\n\n{target.name} takes {total_damage} damage. "
                        f"({target.name}: {target.hp}/{target.max_hp} HP)"
                    )
                    field_value += f"\n{fighter.name} Sanity +{sanity_gain} ({fighter.sanity})"
                    if status_log:
                        field_value += "\n" + "\n".join(status_log)
                    if trigger_log:
                        field_value += "\n" + "\n".join(trigger_log)
                if splash_log:
                    field_value += "\n" + "\n".join(splash_log)

                if is_evade_readying:
                    summary_line = (
                        f"{status_emoji('evasion')} **{fighter.name}** readies Evade against "
                        f"{target.name}'s Slot {entry['target_slot']}."
                    )
                    full_log_entries.append((f"{fighter.name}'s Evade", field_value))
                elif is_clashable_defense_idle:
                    idle_tag = "clashable_guard" if "clashable_guard" in entry["skill"].tags else "clashable_counter"
                    idle_name = "Clashable Guard" if idle_tag == "clashable_guard" else "Clashable Counter"
                    summary_line = (
                        f"{status_emoji(idle_tag)} **{fighter.name}** readies {idle_name} -- "
                        f"nothing to intercept this round."
                    )
                    full_log_entries.append((f"{fighter.name}'s {idle_name}", field_value))
                elif is_guard:
                    summary_line = (
                        f"{status_emoji('guard')} **{fighter.name}** raises Guard -- +{shield_gained} Shield "
                        f"({fighter.shield} total)"
                    )
                    full_log_entries.append((f"{fighter.name}'s Guard", field_value))
                elif is_counter:
                    summary_line = (
                        f"{status_emoji('counter')} **{fighter.name}**'s Counter redirects and strikes **{target.name}** back "
                        f"-- {total_damage} damage ({target.name}: {target.hp}/{target.max_hp} HP)"
                    )
                    full_log_entries.append((f"{fighter.name}'s Counter -> {target.name}", field_value))
                else:
                    summary_line = (
                        f"🗡️ **{fighter.name}** hits **{target.name}** (unopposed) -- {total_damage} damage "
                        f"({target.name}: {target.hp}/{target.max_hp} HP)"
                    )
                    full_log_entries.append((f"{fighter.name} -> {target.name} (unopposed)", field_value))
                summary_lines.append(summary_line)

                locked_lines.append(summary_line)
                await render(None)

        # Whatever's still sitting in deferred_clashable_by_fighter never
        # got claimed by any attack this round (see the fall-through
        # check above) -- render each one's real "nothing to intercept"
        # idle turn now, at the very end, same shape as the old inline
        # is_clashable_defense_idle case used to render at its own
        # natural speed position.
        for leftover_entries in deferred_clashable_by_fighter.values():
            for idle_entry in leftover_entries:
                idle_fighter = idle_entry["caster"]
                idle_tag = "clashable_guard" if "clashable_guard" in idle_entry["skill"].tags else "clashable_counter"
                idle_name = "Clashable Guard" if idle_tag == "clashable_guard" else "Clashable Counter"
                idle_summary = (
                    f"{status_emoji(idle_tag)} **{idle_fighter.name}** readies {idle_name} -- "
                    f"nothing to intercept this round."
                )
                summary_lines.append(idle_summary)
                full_log_entries.append((f"{idle_fighter.name}'s {idle_name}", idle_summary))
                locked_lines.append(idle_summary)

        # Everything's already locked into locked_lines as the phase
        # went along -- this is just the final static render, no
        # "live" block left since the last unit already locked itself in.
        footer_note = None
        # Turn-End status payoffs (Burn/Sinking damage, Charge decay) --
        # must run BEFORE final_description is joined below, so the
        # damage/log lines actually show up in this round's results,
        # and BEFORE clear_expired_stagger/start_new_round() further
        # down (this IS the "Turn End" moment those come right after).
        turn_end_status_log = apply_turn_end_status_ticks(battle)
        if turn_end_status_log:
            locked_lines.append("**Turn End**\n" + "\n".join(turn_end_status_log))

        # Elimination: anyone who hit 0 HP this round (from any source --
        # a clash loss, an unopposed hit, Attack Weight splash, Bleed's
        # own self-damage, etc.) is flagged out for good. Checked once
        # here at end-of-round rather than at every individual take_
        # damage call site, since there are many of those and this
        # catches all of them uniformly. eliminated is a ONE-WAY flag
        # (see Fighter's docstring) -- once set, it never gets cleared
        # by combat() itself, only by starting a new Battle entirely.
        # Must run BEFORE final_description is joined below, so the
        # announcement actually shows up in this round's results.
        newly_eliminated = [f for f in battle.fighters if not f.is_alive() and not f.eliminated]
        for f in newly_eliminated:
            f.eliminated = True
        if newly_eliminated:
            locked_lines.append(
                "**Eliminated**\n" + "\n".join(f"💀 {f.name} has been eliminated." for f in newly_eliminated)
            )

        final_description = "\n\n".join(locked_lines)
        if len(final_description) > 4000:
            final_description = final_description[:4000] + "\n...(truncated)"
            footer_note = "Some results this round were too numerous to display fully."
        if len(full_log_entries) > 60:
            footer_note = (
                (footer_note + " " if footer_note else "")
                + "Some actions this round were too numerous to include in the Full Log."
            )

        final_embed = discord.Embed(
            title=f"{combat_title} -- Results",
            description=final_description or "Nothing declared this round.",
            color=0x5865F2,
        )
        if footer_note:
            final_embed.set_footer(text=footer_note)

        recap_text = build_recap(
            recap_before, battle, battle.round_number, [label for label, _ in full_log_entries]
        )
        recap_body = recap_text.split("\n", 1)[1] if "\n" in recap_text else recap_text
        final_embed.add_field(
            name=f"Round {battle.round_number} Recap", value=recap_body[:1000], inline=False
        )
        battle.history.append(RoundRecord(battle.round_number, recap_text, list(full_log_entries)))
        del battle.history[:-MAX_HISTORY]

        # Whatever [Combat Start] triggers were going to fire this battle already fired above (or didn't, if nobody had one declared) -- this... See docs/ENGINEERING_NOTES.md#battle-comment-2154.
        battle.started = True

        # Clear any Stagger whose expiry round has now been reached --
        # must happen BEFORE start_new_round() bumps round_number, since
        # clear_expired_stagger checks against the round that's ENDING.
        for f in battle.fighters:
            f.clear_expired_stagger(battle.round_number)

        battle.start_new_round()

        # Single consolidated "Full Log" button replaces the old
        # per-action CombatRevealView -- see CombatLogView's docstring.
        log_view = CombatLogView(full_log_entries) if full_log_entries else None
        await combat_message.edit(embed=final_embed, view=log_view)

        # Once the whole phase is done, post the updated battle status as a FRESH message in the channel (not just a silent edit of whatever the... See docs/ENGINEERING_NOTES.md#battle-comment-2173.
        status_message = await interaction.channel.send(embed=build_battle_embed(battle))
        battle.message_id = status_message.id

        ping_text = build_round_ping(battle)
        if ping_text:
            try:
                await interaction.channel.send(ping_text, allowed_mentions=discord.AllowedMentions(users=True))
            except discord.HTTPException:
                pass

    @app_commands.command(name="log", description="Read an earlier round's recap and full log again (only you see it)")
    @app_commands.describe(round="Which round (leave blank for the latest)")
    async def log(self, interaction: discord.Interaction, round: int | None = None):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return
        if not battle.history:
            await interaction.response.send_message("No round has finished yet.", ephemeral=True)
            return
        if round is None:
            record = battle.history[-1]
        else:
            record = next((r for r in battle.history if r.round_number == round), None)
            if record is None:
                kept = ", ".join(str(r.round_number) for r in battle.history)
                await interaction.response.send_message(
                    f"Round {round} isn't kept. The last {MAX_HISTORY} rounds are: {kept}.", ephemeral=True
                )
                return
        embeds = [discord.Embed(title=f"Round {record.round_number}", description=record.recap[:4000], color=0x5865F2)]
        embeds += _log_embeds(record.entries, title=f"Round {record.round_number} Log")[:9]
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    @log.autocomplete("round")
    async def log_round_autocomplete(self, interaction: discord.Interaction, current: str):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            return []
        return [
            app_commands.Choice(name=f"Round {r.round_number}", value=r.round_number)
            for r in reversed(battle.history)
            if not current or str(r.round_number).startswith(current.strip())
        ]

    @app_commands.command(name="end", description="End the battle in this channel")
    async def end(self, interaction: discord.Interaction):
        if interaction.channel_id in self.battles:
            del self.battles[interaction.channel_id]
            await interaction.response.send_message("Battle ended.")
        else:
            await interaction.response.send_message("No active battle here.", ephemeral=True)

    # ------------------------------------------------------------------
    # Autocomplete: suggest fighter, target, skill, slot and character names
    # as you type. Registered here, after every command exists, through two
    # small factories. See docs/ENGINEERING_NOTES.md#autocomplete.
    # ------------------------------------------------------------------

    def _fighter_choices(
        self, interaction: discord.Interaction, current: str, *, manage_only: bool, living_only: bool = False,
    ) -> list[app_commands.Choice[str]]:
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            return []
        cur = current.lower()
        out = []
        for f in battle.fighters:
            if cur not in f.name.lower():
                continue
            if manage_only and not _can_manage_fighter(interaction, f):
                continue
            if living_only and (not f.is_alive() or f.eliminated):
                continue
            out.append(app_commands.Choice(name=f.name[:100], value=f.name))
        return out[:25]

    @declare.autocomplete("target")
    async def declare_target_autocomplete(self, interaction: discord.Interaction, current: str):
        """Enemies for an attack. A Duality skill can also aim at allies (and yourself)."""
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            return []
        caster = battle.get_fighter(getattr(interaction.namespace, "fighter", None) or "")
        skill = caster.get_skill(getattr(interaction.namespace, "skill_name", None) or "") if caster else None
        cur = current.lower()
        out = []
        for f in battle.fighters:
            if not f.is_alive() or f.eliminated or cur not in f.name.lower():
                continue
            if caster is not None:
                duality = skill is not None and "duality" in skill.tags
                if not duality and (f.side == caster.side or f is caster):
                    continue
            out.append(app_commands.Choice(name=f.name[:100], value=f.name))
        return out[:25]

    @declare.autocomplete("slot")
    async def declare_slot_autocomplete(self, interaction: discord.Interaction, current: str):
        battle = self.battles.get(interaction.channel_id)
        caster = battle.get_fighter(getattr(interaction.namespace, "fighter", None) or "") if battle else None
        if caster is None:
            return []
        out = []
        for n in range(1, caster.skill_slots + 1):
            if caster.slot_disabled(n):
                continue
            label = f"Slot {n} (Speed {caster.slot_speed(n)})"
            action = caster.declared_actions.get(n)
            if action is not None:
                label += f" - declared: {action.skill.name}"
            if current and not str(n).startswith(current.strip()):
                continue
            out.append(app_commands.Choice(name=label[:100], value=n))
        return out

    @declare.autocomplete("target_slot")
    async def declare_target_slot_autocomplete(self, interaction: discord.Interaction, current: str):
        battle = self.battles.get(interaction.channel_id)
        target = battle.get_fighter(getattr(interaction.namespace, "target", None) or "") if battle else None
        if target is None:
            return []
        out = []
        for n in range(1, target.skill_slots + 1):
            label = f"Slot {n} (Speed {target.slot_speed(n)})"
            part = target.parts.get(n)
            if part is not None:
                label += f" - {part.name} " + ("(broken)" if part.broken else f"{part.hp}/{part.max_hp}")
            if current and not str(n).startswith(current.strip()):
                continue
            out.append(app_commands.Choice(name=label[:100], value=n))
        return out

    @undeclare.autocomplete("slot")
    async def undeclare_slot_autocomplete(self, interaction: discord.Interaction, current: str):
        battle = self.battles.get(interaction.channel_id)
        caster = battle.get_fighter(getattr(interaction.namespace, "fighter", None) or "") if battle else None
        if caster is None:
            return []
        return [
            app_commands.Choice(name=f"Slot {n}: {a.skill.name}"[:100], value=n)
            for n, a in sorted(caster.declared_actions.items())
            if not current or str(n).startswith(current.strip())
        ]

    @declare.autocomplete("skill_name")
    async def declare_skill_name_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._skill_name_autocomplete_choices(interaction, current)

    @addfighter.autocomplete("character_name")
    async def addfighter_character_autocomplete(self, interaction: discord.Interaction, current: str):
        battle = self.battles.get(interaction.channel_id)
        cur = current.lower()
        rows = [
            (name, owner) for name, owner in list_character_summaries()
            if cur in name.lower() and (battle is None or battle.get_fighter(name) is None)
        ]
        rows.sort(key=lambda r: (r[1] != interaction.user.id, r[0].lower()))
        return [app_commands.Choice(name=n[:100], value=n) for n, _ in rows][:25]

    def _part_slot_choices(self, interaction: discord.Interaction, current: str, *, only_parts: bool):
        battle = self.battles.get(interaction.channel_id)
        target = battle.get_fighter(getattr(interaction.namespace, "fighter", None) or "") if battle else None
        if target is None:
            return []
        out = []
        for n in range(1, target.skill_slots + 1):
            part = target.parts.get(n)
            if only_parts and part is None:
                continue
            label = f"Slot {n}" + (f" - {part.name}" if part else "")
            if current and not str(n).startswith(current.strip()):
                continue
            out.append(app_commands.Choice(name=label[:100], value=n))
        return out

    @addpart.autocomplete("slot")
    async def addpart_slot_autocomplete(self, interaction: discord.Interaction, current: str):
        return self._part_slot_choices(interaction, current, only_parts=False)

    @removepart.autocomplete("slot")
    async def removepart_slot_autocomplete(self, interaction: discord.Interaction, current: str):
        return self._part_slot_choices(interaction, current, only_parts=True)

    @repairpart.autocomplete("slot")
    async def repairpart_slot_autocomplete(self, interaction: discord.Interaction, current: str):
        return self._part_slot_choices(interaction, current, only_parts=True)

    async def _fighter_ac_manage(self, interaction: discord.Interaction, current: str):
        return self._fighter_choices(interaction, current, manage_only=True)

    async def _fighter_ac_all(self, interaction: discord.Interaction, current: str):
        return self._fighter_choices(interaction, current, manage_only=False)

    # Command objects are plain class attributes here, so the `fighter`
    # option of each one can be pointed at the right suggestion method.
    for _command, _suggest in (
        (declare, _fighter_ac_manage),
        (undeclare, _fighter_ac_manage),
        (addskill, _fighter_ac_manage),
        (removeskill, _fighter_ac_manage),
        (editskill, _fighter_ac_manage),
        (removefighter, _fighter_ac_manage),
        (skills, _fighter_ac_all),
        (setstatus, _fighter_ac_all),
        (addpart, _fighter_ac_all),
        (removepart, _fighter_ac_all),
        (repairpart, _fighter_ac_all),
    ):
        _command.autocomplete("fighter")(_suggest)
    del _command, _suggest


async def setup(bot: commands.Bot):
    await bot.add_cog(BattleCog(bot))