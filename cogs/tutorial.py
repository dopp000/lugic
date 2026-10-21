"""/startingoff: a walkthrough that asks you to do things and tells you what to try next.

It also sends a short tip the first time someone uses a command that has one.
Everything is a private (ephemeral) message. The rules and the saved progress live
in game/tutorial.py, this file only connects them to Discord.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from game import tutorial as tut
from game.character import list_characters_by_owner

log = logging.getLogger(__name__)

TUTORIAL_COLOR = 0x5865F2


class LiveWorld(tut.World):
    """What the steps look at while the bot is running."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def characters(self, user_id: int) -> list:
        return list_characters_by_owner(user_id)

    def battle(self, channel_id: int | None):
        cog = self.bot.get_cog("battle")
        if cog is None or channel_id is None:
            return None
        return cog.battles.get(channel_id)


def _progress_bar(done: int, total: int) -> str:
    return "▰" * done + "▱" * (total - done)


class StartingOffView(discord.ui.View):
    def __init__(self, cog: "TutorialCog", user_id: int, channel_id: int | None):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.channel_id = channel_id
        self._sync_buttons()

    @property
    def progress(self) -> tut.Progress:
        return self.cog.store.get(self.user_id)

    def _event(self) -> tut.Event:
        return tut.Event("start", self.user_id, self.channel_id)

    def _sync_buttons(self):
        self.skip_button.disabled = tut.current_step(self.progress) is None
        self.tips_button.label = "Tips: on" if self.progress.tips else "Tips: off"
        self.tips_button.style = (
            discord.ButtonStyle.success if self.progress.tips else discord.ButtonStyle.secondary
        )

    async def _redraw(self, interaction: discord.Interaction):
        self.cog.store.save()
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.cog.build_embed(self.user_id, self.channel_id), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Skip this step", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        tut.skip(self.progress, self._event(), self.cog.world)
        await self._redraw(interaction)

    @discord.ui.button(label="Start over", style=discord.ButtonStyle.secondary)
    async def restart_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        tut.restart(self.progress, self._event(), self.cog.world)
        await self._redraw(interaction)

    @discord.ui.button(label="Tips: on", style=discord.ButtonStyle.success)
    async def tips_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.progress.tips = not self.progress.tips
        await self._redraw(interaction)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class TutorialCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = tut.TutorialStore()
        self.world = LiveWorld(bot)

    def build_embed(self, user_id: int, channel_id: int | None, welcome: bool = False) -> discord.Embed:
        progress = self.store.get(user_id)
        step = tut.current_step(progress)
        total = len(tut.STEPS)
        if step is None:
            embed = discord.Embed(title="Starting off", description=tut.FINALE, color=TUTORIAL_COLOR)
            embed.set_footer(text=f"All {total} steps done")
            return embed
        done = len([s for s in tut.STEPS if s.id in progress.done or s.id in progress.skipped])
        names = tut.names_for(progress, self.world, user_id, channel_id)
        text = tut.render(step.instruction, names)
        if welcome:
            text = f"{tut.INTRO}\n\n{text}"
        embed = discord.Embed(title=f"{step.part}: {step.title}", description=text, color=TUTORIAL_COLOR)
        embed.add_field(
            name=f"Step {tut.step_number(step)} of {total}",
            value=_progress_bar(done, total),
            inline=False,
        )
        return embed

    @app_commands.command(
        name="startingoff",
        description="A quick walkthrough: it asks you to do things, then tells you what to try next",
    )
    async def startingoff(self, interaction: discord.Interaction):
        progress = self.store.get(interaction.user.id)
        first_time = not progress.started
        event = tut.Event("start", interaction.user.id, interaction.channel_id)
        tut.start(progress, event, self.world)
        self.store.save()
        view = StartingOffView(self, interaction.user.id, interaction.channel_id)
        await interaction.response.send_message(
            embed=self.build_embed(interaction.user.id, interaction.channel_id, welcome=first_time),
            view=view,
            ephemeral=True,
        )

    async def _react(self, interaction: discord.Interaction, event: tut.Event):
        progress = self.store.get(event.user_id)
        update = tut.handle_event(progress, event, self.world)
        if update.changed:
            self.store.save()
        text = tut.compose_message(update, progress, self.world, event)
        if text:
            await interaction.followup.send(text, ephemeral=True)

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        try:
            name = command.qualified_name
            if name == "startingoff" or interaction.user.bot:
                return
            try:
                args = dict(interaction.namespace)
            except Exception:
                args = {}
            event = tut.Event("command", interaction.user.id, interaction.channel_id, name, args)
            # A form answers with a modal, and nothing can follow that. The skill
            # steps are handled when the form is confirmed instead.
            if interaction.response.type == discord.InteractionResponseType.modal:
                return
            await self._react(interaction, event)
        except Exception:
            log.exception("Tutorial could not react to a command")

    @commands.Cog.listener()
    async def on_skill_saved(self, interaction: discord.Interaction, fighter):
        try:
            event = tut.Event("skill_saved", interaction.user.id, interaction.channel_id)
            await self._react(interaction, event)
        except Exception:
            log.exception("Tutorial could not react to a saved skill")

    @commands.Cog.listener()
    async def on_declare_locked(self, interaction: discord.Interaction, fighter):
        try:
            event = tut.Event("declare_locked", interaction.user.id, interaction.channel_id)
            await self._react(interaction, event)
        except Exception:
            log.exception("Tutorial could not react to a locked-in move")


async def setup(bot: commands.Bot):
    await bot.add_cog(TutorialCog(bot))
