"""/request: players ask for build changes, admins approve or deny them.

Everything that costs points (stats, resistances, status ranks, defense
archetypes, tags, identity passives) is admin only. A player files a request,
the bot checks it on a copy of the character, and an admin approves it with a
button. See docs/ENGINEERING_NOTES.md#player-requests.
"""

import re

import discord
from discord import app_commands
from discord.ext import commands

from game.build_points import (
    DEFENSE_ARCHETYPES, PP_COST_MAX, RANKED_STATUSES, defense_label,
)
from game.character import list_character_summaries, load_character, save_character
from game.build_requests import (
    KIND_LABELS, KINDS, MAX_OPEN_PER_PLAYER, REASON_MAX, Request, create_request, describe_request,
    get_request, get_review_channel, has_open_duplicate, list_requests, open_count, save_request,
    set_review_channel, try_change,
)
from cogs.character import _parse_resistance_tokens, dm_owner, is_admin

COLOR_OPEN = 0xF1C40F
COLOR_OK = 0x2ECC71
COLOR_NO = 0xE74C3C
COLOR_GREY = 0x95A5A6


def request_embed(req: Request) -> discord.Embed:
    color = {"open": COLOR_OPEN, "approved": COLOR_OK, "denied": COLOR_NO}.get(req.status, COLOR_GREY)
    embed = discord.Embed(
        title=f"Request #{req.id}: {KIND_LABELS.get(req.kind, req.kind)} for {req.character}",
        description=describe_request(req)[:3500],
        color=color,
    )
    embed.add_field(name="From", value=f"<@{req.owner_id}>", inline=True)
    embed.add_field(name="Status", value=req.status.capitalize(), inline=True)
    if req.reason:
        embed.add_field(name="Why", value=req.reason[:900], inline=False)
    if req.decided_by:
        note = f": {req.decision_note}" if req.decision_note else ""
        embed.add_field(name="Decided by", value=f"<@{req.decided_by}>{note}"[:900], inline=False)
    return embed


class ReviewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"req:(?P<action>approve|deny):(?P<id>\d+)"):
    """Approve/Deny buttons that keep working after a restart (the request id is in the custom_id)."""

    def __init__(self, action: str, request_id: int):
        style = discord.ButtonStyle.success if action == "approve" else discord.ButtonStyle.danger
        super().__init__(discord.ui.Button(
            label="Approve" if action == "approve" else "Deny", style=style,
            custom_id=f"req:{action}:{request_id}",
        ))
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match):
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can decide requests.", ephemeral=True)
            return
        req = get_request(self.request_id)
        if req is None or req.status != "open":
            await interaction.response.send_message("That request is already closed.", ephemeral=True)
            return
        if self.action == "deny":
            await interaction.response.send_modal(DenyModal(req.id))
        elif req.kind == "passive":
            await interaction.response.send_modal(CostModal(req.id))
        else:
            await approve(interaction, req)


def review_view(request_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ReviewButton("approve", request_id))
    view.add_item(ReviewButton("deny", request_id))
    return view


async def _show(interaction: discord.Interaction, req: Request):
    """Updates the review message the click came from (buttons gone once closed)."""
    embed = request_embed(req)
    view = review_view(req.id) if req.status == "open" else None
    if interaction.message is not None:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def approve(interaction: discord.Interaction, req: Request):
    """Applies the request to the character as it is right now, after checking it again."""
    character = load_character(req.character)
    if character is None:
        await interaction.response.send_message(
            f"There is no character called {req.character} any more. Deny the request.", ephemeral=True
        )
        return
    summary, error, changed = try_change(character, req.kind, req.params)
    if error:
        await interaction.response.send_message(
            f"This can't be applied right now, nothing changed:\n{error}"[:1900], ephemeral=True
        )
        return
    save_character(changed)
    req.status = "approved"
    req.decided_by = interaction.user.id
    save_request(req)
    await _show(interaction, req)
    await dm_owner(
        interaction.client, req.owner_id,
        f"Your request #{req.id} for **{req.character}** was approved: {summary}.",
    )


class DenyModal(discord.ui.Modal, title="Deny this request"):
    note = discord.ui.TextInput(
        label="Reason (the player will see it)", required=False, max_length=200,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, request_id: int):
        super().__init__()
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction):
        req = get_request(self.request_id)
        if req is None or req.status != "open":
            await interaction.response.send_message("That request is already closed.", ephemeral=True)
            return
        req.status = "denied"
        req.decided_by = interaction.user.id
        req.decision_note = str(self.note).strip()
        save_request(req)
        await _show(interaction, req)
        why = f" Reason: {req.decision_note}" if req.decision_note else ""
        await dm_owner(
            interaction.client, req.owner_id,
            f"Your request #{req.id} for **{req.character}** was denied.{why}",
        )


class CostModal(discord.ui.Modal, title="Passive Points price"):
    cost = discord.ui.TextInput(label=f"Passive Points it costs (1 to {PP_COST_MAX})", max_length=2)

    def __init__(self, request_id: int):
        super().__init__()
        self.request_id = request_id
        req = get_request(request_id)
        self.cost.default = str(req.params.get("cost", 1)) if req else "1"

    async def on_submit(self, interaction: discord.Interaction):
        req = get_request(self.request_id)
        if req is None or req.status != "open":
            await interaction.response.send_message("That request is already closed.", ephemeral=True)
            return
        try:
            price = int(str(self.cost).strip())
        except ValueError:
            price = 0
        if not 1 <= price <= PP_COST_MAX:
            await interaction.response.send_message(f"The price has to be a whole number from 1 to {PP_COST_MAX}.", ephemeral=True)
            return
        req.params["cost"] = price
        await approve(interaction, req)


class RequestCog(commands.GroupCog, name="request"):
    """Ask an admin to change your build, and the admin side of answering."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def cog_load(self):
        self.bot.add_dynamic_items(ReviewButton)

    async def cog_unload(self):
        self.bot.remove_dynamic_items(ReviewButton)

    # ---- filing -------------------------------------------------------

    async def _submit(self, interaction: discord.Interaction, name: str, kind: str, params: dict, reason: str):
        character = load_character(name)
        if character is None:
            await interaction.response.send_message(f"No character named {name}. Pick a name from the list that shows as you type.", ephemeral=True)
            return
        if interaction.user.id != character.owner_id and not is_admin(interaction):
            await interaction.response.send_message("You can only ask for changes to your own characters.", ephemeral=True)
            return
        if open_count(interaction.user.id) >= MAX_OPEN_PER_PLAYER:
            await interaction.response.send_message(
                f"You already have {MAX_OPEN_PER_PLAYER} open requests. Wait for an answer or cancel one with /request cancel.",
                ephemeral=True,
            )
            return
        if has_open_duplicate(interaction.user.id, character.name, kind):
            await interaction.response.send_message(
                f"You already have an open {KIND_LABELS[kind].lower()} request for {character.name}. Cancel it first to change it.",
                ephemeral=True,
            )
            return
        summary, error, _ = try_change(character, kind, params)
        if error:
            await interaction.response.send_message(f"That request would not work, so it was not sent:\n{error}"[:1900], ephemeral=True)
            return
        req = create_request(interaction.user.id, character.name, kind, params, reason)

        posted = False
        channel_id = get_review_channel()
        channel = interaction.client.get_channel(channel_id) if channel_id else None
        if channel is not None:
            try:
                msg = await channel.send(embed=request_embed(req), view=review_view(req.id))
                req.channel_id, req.message_id = channel.id, msg.id
                save_request(req)
                posted = True
            except discord.HTTPException:
                posted = False
        tail = "An admin will answer it, and you get a DM." if posted else (
            "No review channel is set up yet, so an admin has to find it with /request list."
        )
        await interaction.response.send_message(f"Sent request #{req.id}: {summary}. {tail}", ephemeral=True)

    @app_commands.command(name="stats", description="Ask for a stat change (HP, Speed, Power, Offense/Defense, Stagger, resistances)")
    @app_commands.describe(
        name="Your character",
        hp="New max HP", speed_min="Lowest Speed (needs speed_max)", speed_max="Highest Speed (needs speed_min)",
        power="New Power",
        offense_level="Offense Level 0 to 5", defense_level="Defense Level 0 to 5",
        stagger_disabled_tiers="Stagger tiers to remove (2 and/or 3, comma-separated), or 'none'",
        resistance_types="Resistance(s), e.g. 'slash,burn' (needs values)",
        values="Multiplier for each type, e.g. '1.25,0.75' (needs resistance_types)",
        reason="Why you want it (story or plan)",
    )
    async def stats(
        self, interaction: discord.Interaction, name: str, reason: str,
        hp: int | None = None, speed_min: int | None = None, speed_max: int | None = None,
        power: int | None = None,
        offense_level: app_commands.Range[int, 0, 5] | None = None,
        defense_level: app_commands.Range[int, 0, 5] | None = None,
        stagger_disabled_tiers: str | None = None,
        resistance_types: str | None = None, values: str | None = None,
    ):
        if (speed_min is None) != (speed_max is None):
            await interaction.response.send_message("speed_min and speed_max go together, give both or neither.", ephemeral=True)
            return
        if (resistance_types is None) != (values is None):
            await interaction.response.send_message("resistance_types and values go together, give both or neither.", ephemeral=True)
            return
        params: dict = {}
        for key, val in (("hp", hp), ("power", power), ("offense_level", offense_level), ("defense_level", defense_level)):
            if val is not None:
                params[key] = val
        if speed_min is not None:
            params["speed_min"], params["speed_max"] = speed_min, speed_max
        if stagger_disabled_tiers is not None:
            if stagger_disabled_tiers.strip().lower() == "none":
                params["stagger_disabled_tiers"] = []
            else:
                try:
                    params["stagger_disabled_tiers"] = sorted({int(t) for t in stagger_disabled_tiers.split(",") if t.strip()})
                except ValueError:
                    await interaction.response.send_message("stagger_disabled_tiers must be whole numbers (2 and/or 3), or 'none'.", ephemeral=True)
                    return
        if resistance_types is not None:
            parsed, error = _parse_resistance_tokens(resistance_types, values)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return
            params["resistances"] = parsed
        await self._submit(interaction, name, "stats", params, reason)

    @app_commands.command(name="rank", description="Ask for a status rank (Bleed, Burn, ...), or 0 to drop one")
    @app_commands.choices(status=[app_commands.Choice(name=s.capitalize(), value=s) for s in RANKED_STATUSES])
    @app_commands.describe(name="Your character", status="Which status", rank="0 drops it, 1 to 5 sets it", reason="Why you want it")
    async def rank(
        self, interaction: discord.Interaction, name: str, status: app_commands.Choice[str],
        rank: app_commands.Range[int, 0, 5], reason: str,
    ):
        await self._submit(interaction, name, "rank", {"status": status.value, "rank": rank}, reason)

    @app_commands.command(name="archetype", description="Ask for a defense archetype (Guard, Evade, Counter...), or to drop one")
    @app_commands.choices(archetype=[app_commands.Choice(name=defense_label(k), value=k) for k in DEFENSE_ARCHETYPES])
    @app_commands.describe(name="Your character", archetype="Which archetype", own="True to take it, false to drop it", reason="Why you want it")
    async def archetype(
        self, interaction: discord.Interaction, name: str, archetype: app_commands.Choice[str],
        reason: str, own: bool = True,
    ):
        await self._submit(interaction, name, "archetype", {"archetype": archetype.value, "own": own}, reason)

    @app_commands.command(name="tags", description="Ask for group tags (up to 3) that passive conditions can count")
    @app_commands.describe(
        name="Your character", reason="Why (which group they belong to)",
        tag_1="First tag, e.g. N Corp (leave all empty to clear them)", tag_2="Second tag", tag_3="Third tag",
    )
    async def tags(
        self, interaction: discord.Interaction, name: str, reason: str,
        tag_1: str | None = None, tag_2: str | None = None, tag_3: str | None = None,
    ):
        chosen = [t for t in (tag_1, tag_2, tag_3) if t and t.strip()]
        await self._submit(interaction, name, "tags", {"tags": chosen}, reason)

    @app_commands.command(name="passive", description="Ask for an identity passive (always-on rules)")
    @app_commands.choices(kind=[
        app_commands.Choice(name="Passive", value="passive"),
        app_commands.Choice(name="Support Passive", value="support"),
    ])
    @app_commands.describe(
        name="Your character", passive_name="Name of the passive", kind="Passive or Support Passive",
        cost="Passive Points you think it should cost (the admin sets the final price)",
        line_1="First rule, e.g. [On Use] If the target has any Burn, Deal +10% damage (see /help)",
        line_2="Second rule (optional)", line_3="Third rule (optional)",
        reason="Where it comes from and why",
    )
    async def passive(
        self, interaction: discord.Interaction, name: str, passive_name: str,
        kind: app_commands.Choice[str], cost: app_commands.Range[int, 1, 6], line_1: str, reason: str,
        line_2: str | None = None, line_3: str | None = None,
    ):
        title = passive_name.strip()
        if not title or len(title) > 40:
            await interaction.response.send_message("Give the passive a name of 1 to 40 characters.", ephemeral=True)
            return
        lines = [l.strip() for l in (line_1, line_2, line_3) if l and l.strip()]
        params = {"name": title, "kind": kind.value, "lines": lines, "cost": cost, "note": ""}
        await self._submit(interaction, name, "passive", params, reason)

    # ---- the player's own ---------------------------------------------

    @app_commands.command(name="mine", description="See your requests and how they were answered")
    async def mine(self, interaction: discord.Interaction):
        mine = list_requests(owner_id=interaction.user.id)[-15:]
        if not mine:
            await interaction.response.send_message("You have not asked for anything yet.", ephemeral=True)
            return
        lines = []
        for r in reversed(mine):
            what = describe_request(r).splitlines()[0]
            note = f" ({r.decision_note})" if r.decision_note else ""
            lines.append(f"#{r.id} {r.status}: {r.character}, {KIND_LABELS[r.kind].lower()}, {what}{note}")
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @app_commands.command(name="cancel", description="Withdraw one of your open requests")
    @app_commands.describe(request_id="The number shown when you sent it, or in /request mine")
    async def cancel(self, interaction: discord.Interaction, request_id: int):
        req = get_request(request_id)
        if req is None or (req.owner_id != interaction.user.id and not is_admin(interaction)):
            await interaction.response.send_message("No request with that number is yours.", ephemeral=True)
            return
        if req.status != "open":
            await interaction.response.send_message("That request is already closed.", ephemeral=True)
            return
        req.status = "cancelled"
        save_request(req)
        await interaction.response.send_message(f"Request #{req.id} withdrawn.", ephemeral=True)
        await self._refresh_message(req)

    async def _refresh_message(self, req: Request):
        """Takes the buttons off the review message of a request closed some other way."""
        if not (req.channel_id and req.message_id):
            return
        try:
            channel = self.bot.get_channel(req.channel_id) or await self.bot.fetch_channel(req.channel_id)
            msg = await channel.fetch_message(req.message_id)
            await msg.edit(embed=request_embed(req), view=None)
        except (discord.HTTPException, AttributeError):
            pass

    # ---- admin --------------------------------------------------------

    @app_commands.command(name="channel", description="Admin only: send new requests to a channel (default: this one)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="Where requests go. Leave empty to use this channel")
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can set the review channel.", ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Pick a text channel.", ephemeral=True)
            return
        set_review_channel(target.id)
        await interaction.response.send_message(f"New requests will be posted in {target.mention}.", ephemeral=True)

    @app_commands.command(name="list", description="Admin only: show the open requests")
    @app_commands.default_permissions(manage_guild=True)
    async def list_open(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can see every request.", ephemeral=True)
            return
        open_ones = list_requests(status="open")
        if not open_ones:
            await interaction.response.send_message("No open requests.", ephemeral=True)
            return
        lines = [
            f"#{r.id} <@{r.owner_id}> {r.character}, {KIND_LABELS[r.kind].lower()}: {describe_request(r).splitlines()[0]}"
            for r in open_ones[:20]
        ]
        extra = f"\n...and {len(open_ones) - 20} more" if len(open_ones) > 20 else ""
        await interaction.response.send_message(
            "\n".join(lines)[:1800] + extra + "\nUse /request review with a number to get the buttons.", ephemeral=True
        )

    @app_commands.command(name="review", description="Admin only: post a request here with its Approve/Deny buttons")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(request_id="The request number")
    async def review(self, interaction: discord.Interaction, request_id: int):
        if not is_admin(interaction):
            await interaction.response.send_message("Only an admin can review requests.", ephemeral=True)
            return
        req = get_request(request_id)
        if req is None:
            await interaction.response.send_message("No request with that number.", ephemeral=True)
            return
        view = review_view(req.id) if req.status == "open" else None
        await interaction.response.send_message(embed=request_embed(req), view=view, ephemeral=True)

    async def _name_autocomplete(self, interaction: discord.Interaction, current: str):
        cur = current.lower()
        see_all = is_admin(interaction)
        names = sorted(
            n for n, owner in list_character_summaries()
            if cur in n.lower() and (see_all or owner == interaction.user.id)
        )
        return [app_commands.Choice(name=n[:100], value=n) for n in names][:25]

    for _command in (stats, rank, archetype, tags, passive):
        _command.autocomplete("name")(_name_autocomplete)
    del _command


async def setup(bot: commands.Bot):
    await bot.add_cog(RequestCog(bot))
