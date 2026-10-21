import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = False  # slash-command only, don't need this

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

# List every cog file here (without .py). As we add more, this list grows.
INITIAL_COGS = [
    "cogs.general",
    "cogs.battle",
    "cogs.character",
    "cogs.build_requests",
    "cogs.roll",
    "cogs.help",
    "cogs.tutorial",
    "cogs.practice",
]


@bot.event
async def on_ready():
    # on_ready fires again after every reconnect. Syncing commands each time
    # would burn Discord's sync limit on a bot that stays online, so only the
    # first one syncs.
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    if getattr(bot, "_commands_synced", False):
        return
    try:
        synced = await bot.tree.sync()
        bot._commands_synced = True
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


async def main():
    # Shows discord.py's own warnings and errors (reconnects, rate limits) in
    # the terminal or in `journalctl` when the bot runs as a service.
    discord.utils.setup_logging()
    async with bot:
        for cog in INITIAL_COGS:
            await bot.load_extension(cog)
            print(f"Loaded {cog}")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())