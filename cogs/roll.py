import discord
from discord.ext import commands
from discord import app_commands

from game.dice import roll


class RollCog(commands.Cog):
    """Quick dice-expression rolling with the math shown as subtext."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="roll", description="Roll a dice expression, e.g. 1d20+4+2")
    @app_commands.describe(expression="Dice expression, e.g. 1d20+4+2 or 2d6-1")
    async def roll(self, interaction: discord.Interaction, expression: str):
        try:
            result = roll(expression)
        except ValueError as e:
            await interaction.response.send_message(
                f"Couldn't parse '{expression}': {e}", ephemeral=True
            )
            return

        # Public reply: total as the main line, full breakdown as small
        # subtext underneath using Discord's "-#" subtext markdown.
        await interaction.response.send_message(
            f"**{interaction.user.display_name} rolls: {result.total}**\n"
            f"-# {result.breakdown()}"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RollCog(bot))