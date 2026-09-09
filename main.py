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
    "cogs.roll",
]


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


async def main():
    async with bot:
        for cog in INITIAL_COGS:
            await bot.load_extension(cog)
            print(f"Loaded {cog}")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())