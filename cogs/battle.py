import asyncio
import random

import discord
from discord.ext import commands
from discord import app_commands

from game.battle import (
    Battle, Fighter, BATTLE_TYPES,
    SANITY_PER_COIN_CLASH_WIN, SANITY_CLASH_LOSS, SANITY_PER_HEADS_UNOPPOSED,
    STAGGER_MULTIPLIERS,
)
from game.skills import Skill, SkillResult, ClashOutcome, resolve_skill, resolve_round_clash, resolve_triggers
from game.conditions import Trigger, TriggerContext, parse_trigger_text, TriggerParseError
from game.character import load_character
from game.statuses import StatusInstance, decay_after_trigger, apply_status, INFLICTABLE_STATUSES
from game.resistances import DAMAGE_TYPES, ALL_RESISTANCE_TYPES, apply_resistance
from game.emojis import status_emoji, coin_emoji, coin_roll_emoji, damage_type_emoji, stat_emoji, skill_slot_emoji, hint_emoji, attack_weight_icons, stagger_tier_emoji, stagger_action_emoji

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

# Magnitude (potency * count) thresholds for the status-based half of a fighter's Hint tier, checked highest first. See docs/ENGINEERING_NOTES.md#battle-comment-40.
STATUS_HINT_THRESHOLDS = [(15, 3), (6, 2), (1, 1)]


def _can_manage_fighter(interaction: discord.Interaction, fighter: Fighter) -> bool:
    """True if whoever's invoking this is either the fighter's own linked owner, or holds the server's admin role (ADMIN_ROLE_ID). See docs/ENGINEERING_NOTES.md#battle-can-manage-fighter for the full rationale."""
    if fighter.owner_id is not None and interaction.user.id == fighter.owner_id:
        return True
    if isinstance(interaction.user, discord.Member):
        return any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles)
    return False


class SkillConfirmView(discord.ui.View):
    def __init__(self, fighter: Fighter, skill: Skill, preview_text: str, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.fighter = fighter
        self.skill = skill
        self.preview_text = preview_text

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.fighter.add_skill(self.skill)

        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel is not None:
            await log_channel.send(
                f"{interaction.user.mention} confirmed a new skill for **{self.fighter.name}**:\n"
                f"{self.preview_text}"
            )

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"**Confirmed.** {self.fighter.name} learned {self.skill.name}.",
            view=self,
        )
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


def _skill_preview_text(skill: Skill) -> str:
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
    if skill.triggers:
        trigger_lines = "\n".join(
            f"  Trigger: {t.condition.type} -> {t.effect_type}"
            f"{f' {t.effect_value}' if t.effect_type != 'inflict_status' else f' {t.status_name} {t.effect_value}/{t.status_count}'}"
            f" ({hint_emoji(t.hint_tier)})"
            for t in skill.triggers
        )
        text += f"\n{trigger_lines}"
    if skill.tags:
        text += f"\n  Flags: {', '.join(sorted(skill.tags))}"
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
            ":Coin1: [On Hit] Inflict 2 Rupture Potency, 1 Count\n"
            "[Target Fixed]"
        ),
        required=False,
        max_length=4000,
    )

    def __init__(self, target_fighter: Fighter):
        super().__init__()
        self.target_fighter = target_fighter

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
            await interaction.response.send_message(
                f"'{damage_type_raw}' isn't a valid damage type. Choose one of: "
                f"{', '.join(t.capitalize() for t in DAMAGE_TYPES)}.",
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

        try:
            triggers, flags = parse_trigger_text(self.triggers_input.value or "")
        except TriggerParseError as e:
            await interaction.response.send_message(
                f"Trigger line error on `{e.line.strip()}`: {e.reason}",
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

        preview_text = f"{self.target_fighter.name} would learn {self.skill_name.value}\n" + _skill_preview_text(skill)
        view = SkillConfirmView(self.target_fighter, skill, preview_text)
        await interaction.response.send_message(preview_text, view=view, ephemeral=True)


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
        self.caster.declare_in_slot(
            self.slot, self.skill, self.target, self.target_slot, self.extra_targets
        )

        for child in self.children:
            child.disabled = True
        extra_note = (
            f" (Attack Weight {attack_weight_icons(self.skill.attack_weight)} also reaches " + ", ".join(
                f"{f.name} Slot {s}" for f, s in self.extra_targets
            ) + ")"
            if self.extra_targets else ""
        )
        await interaction.response.edit_message(
            content=(
                f"**Locked in.** {self.caster.name}'s Slot {self.slot} ({self.skill.name}) "
                f"targets {self.target.name}'s Slot {self.target_slot}{extra_note}."
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
                f"action there will resolve unopposed unless you declare something else."
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
    """The worse of a fighter's currently active statuses, mapped to a tier by magnitude (potency * count). See docs/ENGINEERING_NOTES.md#battle-status-hint-tier for the full rationale."""
    best = 0
    for status in fighter.statuses.values():
        if status.count <= 0:
            continue
        magnitude = status.potency * status.count
        tier = 0
        for threshold, t in STATUS_HINT_THRESHOLDS:
            if magnitude >= threshold:
                tier = t
                break
        if status.name == "rupture":
            tier = min(3, max(tier, 1) + 1) if tier > 0 else 2
        best = max(best, tier)
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
        fighters = sorted(battle.side(side_name), key=lambda f: max(f.slot_speeds, default=0), reverse=True)
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

            lines.append(
                f"**{f.name}**{down_tag} ({stat_emoji('hp')} {f.hp}/{f.max_hp}, "
                f"{stat_emoji('sanity')} {f.sanity}){tag}\n{slot_line}{hint_line}"
            )
        embed.add_field(name=f"Side {side_name}", value="\n\n".join(lines), inline=False)

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


def apply_incoming_hit(
    attacker_skill: Skill, result: SkillResult, target: Fighter, caster: Fighter,
    skip_evasion: bool = False,
) -> tuple[int, list[str], list[Trigger], int]:
    """Applies each landed coin's damage/status, and collects whichever per-coin Triggers (on_hit/heads_hit/tails_hit) fired on that coin (see... See docs/ENGINEERING_NOTES.md#battle-apply-incoming-hit for the full rationale."""
    log: list[str] = []
    total_damage = 0
    per_coin_triggers: list[Trigger] = []
    evade_count = 0
    poise = caster.get_status("poise")
    evasion = None if skip_evasion else target.get_status("evasion")
    stagger_multiplier = (
        STAGGER_MULTIPLIERS[target.current_stagger_tier - 1]
        if target.current_stagger_tier > 0 else 1.0
    )

    for i, coin in enumerate(result.coin_results):
        if coin.is_evaded and not skip_evasion:
            evade_count += 1
            log.append(
                f"Coin {i + 1}: {status_emoji('evasion')} {target.name} evades the hit."
            )
            if evasion is not None:
                evasion = decay_after_trigger(evasion)
                target.set_status_instance(evasion)
            continue

        per_coin_triggers.extend(coin.fired_triggers)
        raw = coin.damage_dealt + (coin.crit_bonus_damage if coin.is_crit else 0)
        resisted = apply_resistance(raw, target.resistances.get(attacker_skill.damage_type, 0))
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
            resistance_pct = target.resistances.get(status_name, 0)
            raw_potency = (
                attacker_skill.coin_status_potencies[i]
                if i < len(attacker_skill.coin_status_potencies) else 0
            )
            added_potency = apply_resistance(raw_potency, resistance_pct)
            added_count = (
                attacker_skill.coin_status_counts[i]
                if i < len(attacker_skill.coin_status_counts) else 0
            )

            current = decayed_rupture if status_name == "rupture" else target.get_status(status_name)
            new_instance = apply_status(current, status_name, added_potency, added_count)
            target.set_status_instance(new_instance)
            log.append(
                f"Coin {i + 1}: {target.name} gains {status_emoji(status_name)} "
                f"{status_name.capitalize()}: {new_instance.potency}/{new_instance.count}"
            )

        total_damage += coin_total

    if stagger_multiplier != 1.0 and total_damage > 0:
        boosted = round(total_damage * stagger_multiplier)
        log.append(
            f"{stagger_tier_emoji(target.current_stagger_tier)} {target.name} is Stagger'd (Tier "
            f"{target.current_stagger_tier}): {total_damage} -> {boosted} damage (x{stagger_multiplier})"
        )
        total_damage = boosted

    return total_damage, log, per_coin_triggers, evade_count


def fire_evade_triggers(defender: Fighter, attacker: Fighter, battle: Battle, evade_count: int) -> list[str]:
    """Fires [On Evade] once per coin the defender actually evaded this hit (evade_count, from apply_incoming_hit above), swept across ALL of... See docs/ENGINEERING_NOTES.md#battle-fire-evade-triggers for the full rationale."""
    if evade_count <= 0:
        return []
    log: list[str] = []
    context = TriggerContext(caster=defender, target=attacker, battle=battle)
    for _ in range(evade_count):
        for skill in defender.skills.values():
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


def find_eligible_clashable_counter(defender: Fighter) -> tuple[int, Skill] | None:
    """Same idea as find_eligible_counter, for [Clashable Counter]. See docs/ENGINEERING_NOTES.md#battle-find-eligible-clashable-counter for the full rationale."""
    if defender.clashable_counter_used_this_round:
        return None
    for slot_num, action in defender.declared_actions.items():
        if "clashable_counter" in action.skill.tags:
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


def apply_counter_redirects(units: list, battle: Battle) -> list:
    """Runs once, right after `units` is built and speed-sorted, BEFORE any resolution happens -- transforms the list to reflect Counter and... See docs/ENGINEERING_NOTES.md#battle-apply-counter-redirects for the full rationale."""
    # Pass 1: Counter. Checks both the primary target AND any [Attack
    # Weight] extra_targets on the SAME entry -- a splash target with
    # an eligible Counter gets pulled out of extra_targets entirely and
    # turned into its own genuine solo retaliation unit, going through
    # the exact same full resolution pipeline as any real attack. This
    # only ever applies to plain [Counter], never [Clashable Counter]
    # (find_eligible_counter only ever matches the "counter" tag) --
    # Clashable variants are deliberately excluded from interacting
    # with a splash at all, same exception canon draws for Offset.
    #
    # A splash_fighter that IS the primary defender (their own OTHER
    # slot got auto-picked as a splash candidate) is skipped here on
    # purpose -- their Counter, if any, is already handled exactly once
    # by the defender check just below. Without this, a fighter holding
    # Counter who also got picked as their own splash target would
    # retaliate TWICE for one incoming attack.
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

    # Pass 2: Clashable Counter.
    final_units: list = []
    consumed: set[int] = set()
    for idx, u in enumerate(pass1):
        if idx in consumed:
            continue
        if u[0] != "solo":
            final_units.append(u)
            continue
        entry = u[1]
        caster = entry["caster"]
        if "clashable_counter" not in entry["skill"].tags:
            final_units.append(u)
            continue
        found = find_eligible_clashable_counter(caster)
        if found is None:
            final_units.append(u)
            continue

        target_idx = None
        for j, other in enumerate(pass1):
            if j == idx or j in consumed:
                continue
            if other[0] != "solo":
                continue
            if other[1]["target"] is caster:
                target_idx = j
                break

        if target_idx is None:
            # "Does not activate" -- fizzles entirely, no consumption.
            consumed.add(idx)
            continue

        caster.clashable_counter_used_this_round = True
        consumed.add(idx)
        consumed.add(target_idx)
        intercepted_entry = pass1[target_idx][1]
        intercepted_entry["is_clashable_counter_intercept"] = True
        entry["is_clashable_counter_intercept"] = True
        final_units.append(("clash", entry, intercepted_entry))

    # Pass 3: Clashable Guard. Runs against final_units (post Pass 2),
    # same scan-for-an-interceptable-solo-unit logic, own flag/tag.
    pass3_units: list = []
    consumed3: set[int] = set()
    for idx, u in enumerate(final_units):
        if idx in consumed3:
            continue
        if u[0] != "solo":
            pass3_units.append(u)
            continue
        entry = u[1]
        caster = entry["caster"]
        if "clashable_guard" not in entry["skill"].tags:
            pass3_units.append(u)
            continue
        found = find_eligible_clashable_guard(caster)
        if found is None:
            pass3_units.append(u)
            continue

        target_idx = None
        for j, other in enumerate(final_units):
            if j == idx or j in consumed3:
                continue
            if other[0] != "solo":
                continue
            if other[1]["target"] is caster:
                target_idx = j
                break

        if target_idx is None:
            consumed3.add(idx)
            continue

        caster.clashable_guard_used_this_round = True
        consumed3.add(idx)
        consumed3.add(target_idx)
        intercepted_entry = final_units[target_idx][1]
        intercepted_entry["is_clashable_guard_intercept"] = True
        entry["is_clashable_guard_intercept"] = True
        pass3_units.append(("clash", entry, intercepted_entry))

    return pass3_units


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
    for fighter in battle.fighters:
        if not fighter.is_alive():
            continue
        for skill in fighter.skills.values():
            context = TriggerContext(caster=fighter, target=None, battle=battle)
            for timing in timings:
                _, post_hit = resolve_triggers(skill, context, timing)
                log.extend(apply_trigger_effects(post_hit, fighter, None))
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
    for f in battle.fighters:
        if not f.is_alive():
            continue

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
    breakdown = (
        f" ({shield_absorbed} absorbed by {status_emoji('shield')} Shield, {hp_dmg} to HP)"
        if shield_absorbed > 0 else ""
    )
    fighter.set_status_instance(decay_after_trigger(bleed))
    return [
        f"{status_emoji('bleed')} {fighter.name} takes {total} Bleed damage from using "
        f"{skill.name}.{breakdown} ({fighter.name}: {fighter.hp}/{fighter.max_hp} HP)"
    ]


def _resolve_pre_roll_chain(
    skill: Skill, context: TriggerContext, timings: tuple[str, ...]
) -> tuple[Skill, list[Trigger]]:
    """Chains resolve_triggers across every pre-roll timing in `timings`, in order, folding each stage's bonus_power/bonus_coin_power into the... See docs/ENGINEERING_NOTES.md#battle-resolve-pre-roll-chain for the full rationale."""
    post_hit: list[Trigger] = []
    for timing in timings:
        skill, fired = resolve_triggers(skill, context, timing)
        post_hit.extend(fired)
    return skill, post_hit


def apply_trigger_effects(triggers: list[Trigger], caster: Fighter, target: Fighter | None) -> list[str]:
    """Applies the post-hit effects (inflict_status, sanity_gain, gain_status) from whichever triggers fired AND actually landed a hit. See docs/ENGINEERING_NOTES.md#battle-apply-trigger-effects for the full rationale."""
    log: list[str] = []
    for t in triggers:
        if t.effect_type == "inflict_status":
            if target is None:
                continue
            resistance_pct = target.resistances.get(t.status_name, 0)
            added_potency = apply_resistance(t.effect_value, resistance_pct)
            current = target.get_status(t.status_name)
            new_instance = apply_status(current, t.status_name, added_potency, t.status_count)
            target.set_status_instance(new_instance)
            log.append(
                f"Trigger: {target.name} gains {status_emoji(t.status_name)} "
                f"{t.status_name.capitalize()}: {new_instance.potency}/{new_instance.count}"
            )
        elif t.effect_type == "sanity_gain":
            caster.gain_sanity(t.effect_value)
            log.append(f"Trigger: {caster.name} Sanity +{t.effect_value} ({caster.sanity})")
        elif t.effect_type == "gain_status":
            current = caster.get_status(t.status_name)
            new_instance = apply_status(current, t.status_name, t.effect_value, t.status_count)
            caster.set_status_instance(new_instance)
            log.append(
                f"Trigger: {caster.name} gains {status_emoji(t.status_name)} "
                f"{t.status_name.capitalize()}: {new_instance.potency}/{new_instance.count}"
            )
    return log


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

        embeds: list[discord.Embed] = []
        current_lines: list[str] = []
        current_len = 0

        def flush():
            if current_lines:
                title = "Combat Log" if not embeds else f"Combat Log (cont. {len(embeds) + 1})"
                embeds.append(discord.Embed(title=title, description="\n".join(current_lines)[:4000], color=0x5865F2))

        for label, detail in self.entries:
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

        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class BattleCog(commands.GroupCog, name="battle"):
    """Commands for creating and managing battles."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.battles: dict[int, Battle] = {}

    @app_commands.command(name="create", description="Start a new battle in this channel")
    @app_commands.describe(battle_type="Spar, Standard Encounter, or Fatal Battle")
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

    @app_commands.command(name="addfighter", description="Add a fighter to the current battle")
    @app_commands.describe(
        side="A or B",
        character_name="Pull stats, avatar, and resistances from a saved character",
        name="Fighter's name, only used if not pulling from a saved character",
        hp="Starting HP, only used if not pulling from a saved character (default 100)",
        speed_min="Lowest a skill slot's Speed can roll (default: flat 10 if omitted)",
        speed_max="Highest a skill slot's Speed can roll (default: same as speed_min)",
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
                    f"No character named {character_name}.", ephemeral=True
                )
                return

            if battle.get_fighter(character.name) is not None:
                await interaction.response.send_message(
                    f"A fighter named {character.name} already exists in this battle.", ephemeral=True
                )
                return

            fighter = Fighter.from_character(character, side)
            if speed_min is not None:
                fighter.speed_min = speed_min
                fighter.speed_max = speed_max if speed_max is not None else speed_min
                fighter.roll_slot_speeds()
            battle.add_fighter(fighter)
            await interaction.response.send_message(
                f"Added {fighter.name} to Side {side}.", ephemeral=True
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
        description="Admin/testing tool: directly set a fighter's stats, resistances, and/or a status",
    )
    @app_commands.describe(
        fighter="Fighter name",
        status_name="A status to set, or 'none' to clear all statuses",
        potency="Potency for status_name",
        count="Count for status_name",
        hp="Set current HP",
        max_hp="Set max HP",
        sanity="Set Sanity",
        speed_min="Set the low end of this fighter's Speed range (needs speed_max too, re-rolls slots)",
        speed_max="Set the high end of this fighter's Speed range (needs speed_min too, re-rolls slots)",
        power="Set Power",
        resistance_input="Resistance(s) to set: 'Type:Value', comma-separated for several, e.g. 'slash:20,burn:-10'",
        stagger_thresholds="3 HP% values for Tier 1/2/3, comma-separated, descending, e.g. '55,40,25'",
        stagger_disabled_tiers="Which Stagger tiers to strip, comma-separated (only 2 and/or 3 -- Tier 1 can't be removed), or 'none' to re-enable all",
    )
    @app_commands.choices(
        status_name=[app_commands.Choice(name=s.capitalize(), value=s) for s in INFLICTABLE_STATUSES]
        + [app_commands.Choice(name="None (clear all statuses)", value="none")]
    )
    async def setstatus(
        self,
        interaction: discord.Interaction,
        fighter: str,
        status_name: app_commands.Choice[str] | None = None,
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
    ):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        target_fighter = battle.get_fighter(fighter)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {fighter}.", ephemeral=True)
            return

        if (speed_min is None) != (speed_max is None):
            await interaction.response.send_message(
                "speed_min and speed_max must be set together, or not at all.", ephemeral=True
            )
            return

        # Parsed and validated up front, before anything gets mutated -- same "all-or-nothing" principle as the rest of this command: a bad... See docs/ENGINEERING_NOTES.md#battle-comment-1171.
        resistance_changes: list[tuple[str, int]] = []
        if resistance_input is not None:
            for token in resistance_input.split(","):
                token = token.strip()
                if not token:
                    continue
                parts = token.split(":")
                if len(parts) != 2:
                    await interaction.response.send_message(
                        f"Resistance entry '{token}' is invalid. Use 'Type:Value', comma-separated "
                        f"for several, e.g. 'slash:20,burn:-10'.",
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
                try:
                    r_value = int(r_value_raw)
                except ValueError:
                    await interaction.response.send_message(
                        f"Resistance value '{r_value_raw}' for {r_type} isn't a whole number.",
                        ephemeral=True,
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

        if status_name is not None:
            if status_name.value == "none":
                target_fighter.statuses.clear()
                changes.append("all statuses cleared")
            else:
                target_fighter.set_status_instance(
                    StatusInstance(name=status_name.value, potency=potency, count=count)
                )
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

        for r_type, r_value in resistance_changes:
            target_fighter.resistances[r_type] = r_value
            changes.append(f"{r_type.capitalize()} resistance -> {r_value}%")

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

    @app_commands.command(name="addskill", description="Give a fighter a skill (opens a popup)")
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
            await interaction.response.send_message(f"No fighter named {fighter}.", ephemeral=True)
            return

        # The ONLY response for this interaction -- everything about the skill (name, stats, damage type, statuses, triggers) is now collected in... See docs/ENGINEERING_NOTES.md#battle-comment-1345.
        await interaction.response.send_modal(AddSkillModal(target_fighter))

    @app_commands.command(
        name="declare",
        description="Assign a skill to one of your slots, aimed at a specific slot on your target",
    )
    @app_commands.describe(
        fighter="Who is declaring",
        slot="Which of YOUR skill slots to use (1-3)",
        skill_name="Which skill they're using",
        target="Who they're targeting",
        target_slot="Which of the TARGET's skill slots to aim at (1-3)",
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
            await interaction.response.send_message(f"No fighter named {fighter}.", ephemeral=True)
            return

        if not caster.is_alive():
            await interaction.response.send_message(f"{caster.name} is down and can't act.", ephemeral=True)
            return

        if caster.current_stagger_tier > 0:
            await interaction.response.send_message(
                f"{stagger_action_emoji()} {caster.name} is Stagger'd (Tier {caster.current_stagger_tier}) "
                f"and can't act until it clears.",
                ephemeral=True,
            )
            return

        if not (1 <= slot <= caster.skill_slots):
            await interaction.response.send_message(
                f"{caster.name} only has skill slots 1-{caster.skill_slots}.", ephemeral=True
            )
            return

        skill = caster.get_skill(skill_name)
        if skill is None:
            await interaction.response.send_message(
                f"{caster.name} doesn't know a skill called {skill_name}.", ephemeral=True
            )
            return

        target_fighter = battle.get_fighter(target)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {target}.", ephemeral=True)
            return

        if target_fighter is caster:
            await interaction.response.send_message(
                f"{caster.name} can't target themselves.", ephemeral=True
            )
            return

        if target_fighter.side == caster.side:
            await interaction.response.send_message(
                f"{caster.name} can't target {target_fighter.name}, they're on the same side "
                f"(Side {caster.side}). Only enemies can be targeted.",
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
                    caster.declare_in_slot(slot, skill, target_fighter, target_slot)
                    await interaction.response.send_message(
                        f"{target_fighter.name}'s Slot {target_slot} is already clashing "
                        f"{partner.name}'s **{partner_action.skill.name}**, which is Target Fixed "
                        f"and can't be taken over. Falling through to unopposed against "
                        f"{target_fighter.name} instead.",
                        ephemeral=True,
                    )
                    await sync_battle_message(self.bot, battle)
                    return

                if partner.owner_id is None:
                    caster.declare_in_slot(slot, skill, target_fighter, target_slot)
                    await interaction.response.send_message(
                        f"{target_fighter.name}'s Slot {target_slot} is already clashing "
                        f"{partner.name}, and taking it over needs {partner.name}'s owner to "
                        f"approve, but {partner.name} has no linked owner. Falling through to "
                        f"unopposed against {target_fighter.name} instead.",
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
                            f"over your **{partner.name}**'s clash against **{target_fighter.name}**'s "
                            f"Slot {target_slot}, using **{skill.name}**.\n\n"
                            f"Approve gives it to them (your Slot {partner_slot} is cleared). "
                            f"Decline keeps your clash as declared, and their action falls through "
                            f"to unopposed instead.",
                            view=steal_view,
                        )
                        steal_view.message = dm_message
                        sent = True
                    except discord.Forbidden:
                        sent = False

                if not sent:
                    caster.declare_in_slot(slot, skill, target_fighter, target_slot)
                    await interaction.response.send_message(
                        f"Couldn't DM {partner.name}'s owner to ask for approval (DMs closed or "
                        f"user not found), so this falls through to unopposed against "
                        f"{target_fighter.name} instead.",
                        ephemeral=True,
                    )
                    await sync_battle_message(self.bot, battle)
                    return

                await interaction.response.send_message(
                    f"{target_fighter.name}'s Slot {target_slot} is already clashing "
                    f"{partner.name}. {partner.name}'s owner has been DMed to ask whether "
                    f"{caster.name} can take it over instead. Your action locks in either way, "
                    f"you'll just find out later whether it's a clash or unopposed.",
                    ephemeral=True,
                )
                return

        # Deliberately no preview of the target's own skill here, ever -- you don't get to see what an enemy is bringing before you commit,... See docs/ENGINEERING_NOTES.md#battle-comment-1523.

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
            f"combat actually runs.\n\n"
            f"Confirm to lock it in?"
        )

        await interaction.response.send_message(preview, view=view, ephemeral=True)

    @app_commands.command(name="undeclare", description="Cancel one of your declared skill slots")
    @app_commands.describe(fighter="Who is cancelling", slot="Which slot to clear (1-3)")
    async def undeclare(self, interaction: discord.Interaction, fighter: str, slot: int):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        caster = battle.get_fighter(fighter)
        if caster is None:
            await interaction.response.send_message(f"No fighter named {fighter}.", ephemeral=True)
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

    @app_commands.command(name="removefighter", description="Remove a fighter from the current battle")
    @app_commands.describe(fighter="Fighter to remove")
    async def removefighter(self, interaction: discord.Interaction, fighter: str):
        battle = self.battles.get(interaction.channel_id)
        if battle is None:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return

        target_fighter = battle.get_fighter(fighter)
        if target_fighter is None:
            await interaction.response.send_message(f"No fighter named {fighter}.", ephemeral=True)
            return

        if not _can_manage_fighter(interaction, target_fighter):
            await interaction.response.send_message(
                "Only this fighter's own owner or an admin can remove them.", ephemeral=True
            )
            return

        battle.fighters.remove(target_fighter)
        await interaction.response.send_message(f"Removed {target_fighter.name} from the battle.")
        await sync_battle_message(self.bot, battle)

    @app_commands.command(name="combat", description="Resolve everyone's declared actions this Combat Phase")
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

        # Fires [Combat Start] (first round of the battle only) and [Turn Start] (every round) against every living fighter's FULL skill list, not... See docs/ENGINEERING_NOTES.md#battle-comment-1611.
        passive_log = fire_passive_triggers(battle)

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

        def _never_clashes(skill_tags: set[str]) -> bool:
            return "unclashable" in skill_tags or "guard" in skill_tags

        def _is_plain_guard(skill_tags: set[str]) -> bool:
            return "guard" in skill_tags and "clashable_guard" not in skill_tags

        units = []
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
                fighter_a, fighter_b = entry_a["caster"], entry_b["caster"]
                if not fighter_a.is_alive() or not fighter_b.is_alive():
                    continue

                live_header = f"⚔️ **{fighter_a.name}** vs **{fighter_b.name}**"
                await render(live_header)
                await asyncio.sleep(0.3)

                context_a = TriggerContext(
                    caster=fighter_a, target=fighter_b, battle=battle,
                    is_first_hit_of_round=not first_action_done,
                )
                context_b = TriggerContext(
                    caster=fighter_b, target=fighter_a, battle=battle,
                    is_first_hit_of_round=not first_action_done,
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

                if winner_is_clashable_guard:
                    total_damage, status_log, per_coin_triggers, evade_count = 0, [], [], 0
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
                    total_damage, status_log, per_coin_triggers, evade_count = apply_incoming_hit(
                        winner_skill, outcome.winner_final_result, loser, winner
                    )
                    if loser_is_clashable_guard and total_damage > 0:
                        reduced = round(total_damage * (100 - GUARD_LOSE_DAMAGE_REDUCTION_PCT) / 100)
                        guard_note = (
                            f"{status_emoji('clashable_guard')} {loser.name}'s Clashable Guard mitigates the "
                            f"hit: {total_damage} -> {reduced} damage"
                        )
                        total_damage = reduced

                loser.take_damage(total_damage)
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
                # [On Evade] is the LOSER's own reaction -- they're the one who just got hit by the winner's final toss, see fire_evade_triggers for why... See docs/ENGINEERING_NOTES.md#battle-comment-1904.
                trigger_log += fire_evade_triggers(loser, winner, battle, evade_count)
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
                if entry_a.get("is_clashable_counter_intercept") or entry_b.get("is_clashable_counter_intercept"):
                    holder = entry_a["caster"] if entry_a.get("is_clashable_counter_intercept") else entry_b["caster"]
                    field_value = (
                        f"{status_emoji('clashable_counter')} {holder.name}'s Clashable Counter intercepts an unopposed attack!\n\n"
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

            else:
                _, entry = u
                fighter = entry["caster"]
                target = entry["target"]
                if not fighter.is_alive() or not target.is_alive():
                    continue

                is_counter = entry.get("is_counter_retaliation", False)
                is_guard = "guard" in entry["skill"].tags and not is_counter
                if is_counter:
                    live_header = f"{status_emoji('counter')} **{fighter.name}**'s Counter redirects -> strikes **{target.name}** back!"
                elif is_guard:
                    live_header = f"{status_emoji('guard')} **{fighter.name}** raises Guard!"
                else:
                    live_header = f"**{fighter.name}** -> **{target.name}** (unopposed)"
                await render(live_header)
                await asyncio.sleep(0.3)

                context = TriggerContext(
                    caster=fighter, target=target, battle=battle,
                    is_first_hit_of_round=not first_action_done,
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

                if is_guard:
                    # Guard never deals damage -- its Final Power (the Power reached after the last coin, same value a Clash would compare) becomes Shield HP... See docs/ENGINEERING_NOTES.md#battle-comment-2034.
                    shield_gained = result.final_power
                    fighter.shield += shield_gained
                    total_damage = 0
                    status_log: list[str] = []
                    evade_count = 0
                    _, unopposed_post_hit = resolve_triggers(adjusted_skill, context, "on_unopposed_attack")
                    _, attack_end_post_hit = resolve_triggers(adjusted_skill, context, "attack_end")
                    _, turn_end_post_hit = resolve_triggers(adjusted_skill, context, "turn_end")
                    trigger_log = apply_trigger_effects(
                        pre_roll_post_hit + unopposed_post_hit + attack_end_post_hit + turn_end_post_hit,
                        fighter, target,
                    )
                else:
                    total_damage, status_log, per_coin_triggers, evade_count = apply_incoming_hit(
                        adjusted_skill, result, target, fighter, skip_evasion=is_counter
                    )
                    target.take_damage(total_damage)
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
                        # [On Evade] is the TARGET's own reaction to this unopposed attack -- doesn't apply to a Counter retaliation, which explicitly bypasses it... See docs/ENGINEERING_NOTES.md#battle-comment-2075.
                        trigger_log += fire_evade_triggers(target, fighter, battle, evade_count)

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

                        splash_evasion = splash_fighter.get_status("evasion")

                        splash_evasion = splash_fighter.get_status("evasion")
                        if splash_evasion is not None and splash_evasion.count > 0:
                            splash_fighter.set_status_instance(decay_after_trigger(splash_evasion))
                            splash_log.append(
                                f"{status_emoji('evasion')} {splash_fighter.name} evades the Attack Weight splash "
                                f"(Slot {splash_slot})."
                            )
                            splash_log.extend(fire_evade_triggers(splash_fighter, fighter, battle, 1))
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
                                raw_coin, splash_fighter.resistances.get(adjusted_skill.damage_type, 0)
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
                                resistance_pct = splash_fighter.resistances.get(status_name, 0)
                                raw_potency = (
                                    adjusted_skill.coin_status_potencies[i]
                                    if i < len(adjusted_skill.coin_status_potencies) else 0
                                )
                                added_potency = apply_resistance(raw_potency, resistance_pct)
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
                                f"{stagger_tier_emoji(splash_fighter.current_stagger_tier)} {splash_fighter.name} is Stagger'd (Tier "
                                f"{splash_fighter.current_stagger_tier}): {splash_total} -> "
                                f"{boosted_splash_total} damage (x{splash_stagger_multiplier})"
                            )
                            splash_total = boosted_splash_total

                        splash_shield_absorbed, splash_hp_lost = splash_fighter.take_damage(splash_total)
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

                # ---- Animate: no attrition rounds for an unopposed attack, straight to the decisive toss ----
                final_header = live_header
                final_face = await animate_faces(final_header, result.coin_results)
                if not is_guard:
                    await animate_damage(final_header, final_face, result.coin_results, status_log)
                await asyncio.sleep(0.4)

                if is_guard:
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

                if is_guard:
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

    @app_commands.command(name="end", description="End the battle in this channel")
    async def end(self, interaction: discord.Interaction):
        if interaction.channel_id in self.battles:
            del self.battles[interaction.channel_id]
            await interaction.response.send_message("Battle ended.")
        else:
            await interaction.response.send_message("No active battle here.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BattleCog(bot))