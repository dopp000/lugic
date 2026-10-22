"""/character import: make a character from a code made on the character builder site.

The site (docs/builder/) writes a character as a pasted code. This turns it into a new,
saved character. Skills are the player's own to add, so a build that only holds skills is
applied straight away. A build that changes anything that costs points (stats, ranks, defense
types, tags, passives) is sent to the admins as one "build" request, the same Approve and Deny
buttons as /request, and nothing but the empty new character exists until it is approved.
Either way the whole build is checked first with the bot's own rules, and a build that would
not pass is turned away at once with the reason.
"""

import os

import discord

from cogs.build_check import run_build
from cogs.build_requests import post_for_review
from game.build_requests import MAX_OPEN_PER_PLAYER, cost_preview, create_request, open_count
from game.character import character_exists, save_character, starting_character
from game.character_import import IMPORT_NOTE, decode_code, needs_approval, pool_hint, read_spec


def site_url() -> str:
    """Where the builder site lives, set with CHARACTER_SITE_URL in the .env file. Empty when not set."""
    return os.getenv("CHARACTER_SITE_URL", "").strip()


def _site_tail() -> str:
    url = site_url()
    return f" Build one at {url}" if url else ""


class ImportModal(discord.ui.Modal, title="Import a character"):
    code = discord.ui.TextInput(
        label="Code from the character builder",
        style=discord.TextStyle.paragraph,
        placeholder="LUGIC1Z.eJy...",
        max_length=4000,
    )
    note = discord.ui.TextInput(
        label="Note for the admins (optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await handle_import(interaction, str(self.code), str(self.note))


async def handle_import(interaction: discord.Interaction, text: str, note: str = ""):
    """Everything /character import does after the code is pasted. Sends exactly one reply."""
    async def say(message: str):
        await interaction.response.send_message(message[:1900], ephemeral=True)

    data, error = decode_code(text)
    if error:
        await say(error + _site_tail())
        return
    spec, error = read_spec(data)
    if error:
        await say(f"{error} Nothing was created.")
        return
    if character_exists(spec.name):
        await say(
            f"A character named {spec.name} already exists. /character import makes new characters. "
            "For an existing one use /request and /character addskill."
        )
        return

    fresh = starting_character(interaction.user.id, spec.name, spec.avatar)
    summary, error, built = await run_build(fresh, spec.params, defer_pp=True)
    if error:
        await say(
            f"That build would not work, so nothing was created:\n{error}{pool_hint(spec, fresh)}\n"
            "Change it on the builder and export a new code."
        )
        return

    asks = needs_approval(spec.params)
    if asks and open_count(interaction.user.id) >= MAX_OPEN_PER_PLAYER:
        await say(
            f"You already have {MAX_OPEN_PER_PLAYER} open requests. Wait for an answer or cancel one with "
            "/request cancel, then import again."
        )
        return
    if character_exists(spec.name):
        await say(f"A character named {spec.name} already exists.")
        return

    if not asks:
        save_character(built)
        count = len(built.skills)
        learned = f"with {count} skill{'s' if count != 1 else ''}" if count else "with no skills"
        await say(
            f"Created **{built.name}** {learned}. Nothing in it costs points, so no admin has to answer. "
            "See it with /character view."
        )
        return

    built.pp_total = fresh.pp_total   # so the preview shows the Passive Points that are really there
    save_character(fresh)
    req = create_request(interaction.user.id, fresh.name, "build", spec.params, note.strip() or IMPORT_NOTE)
    posted = await post_for_review(interaction.client, req)
    tail = "An admin will answer it, and you get a DM." if posted else (
        "No review channel is set up yet, so an admin has to find it with /request list."
    )
    await say(
        f"Created **{fresh.name}** and sent its build to the admins as request #{req.id}: {summary}. {tail}\n"
        f"{cost_preview(fresh, built)}\n"
        f"Until it is approved, {fresh.name} has the starting stats and no skills."
        + (
            f"\nPassives cost Passive Points, which only admins give out. {fresh.name} has {fresh.pp_total}, "
            "so the admin has to add them before this can be approved."
            if spec.params.get("passives") else ""
        )
    )
