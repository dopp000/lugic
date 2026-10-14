import discord
from discord.ext import commands
from discord import app_commands

from game.help_content import HELP_CATEGORIES, HelpEntry
from game.emojis import (
    help_category_emoji, skill_tag_emoji,
    HELP_CATEGORY_EMOJI_IDS, SKILL_TAG_EMOJI_IDS,
)

HELP_COLOR = 0x5865F2

# category_key -> {entry_key: emoji_id}. Only "skill_tags" entries have a
# per-entry icon lookup right now (several already reuse an existing
# uploaded status icon -- see SKILL_TAG_EMOJI_IDS); "damage_math" has no
# icons of its own yet, so it isn't listed here at all, and
# _entry_emoji_id just returns None for it.
_ENTRY_EMOJI_IDS: dict[str, dict[str, int | None]] = {
    "skill_tags": SKILL_TAG_EMOJI_IDS,
}


def _entry_emoji_id(category_key: str, entry_key: str) -> int | None:
    return _ENTRY_EMOJI_IDS.get(category_key, {}).get(entry_key)


def _entry_text_icon(category_key: str, entry_key: str) -> str:
    """Safe to drop into embed/message TEXT (falls back to plain
    ':Name:' text if no real ID exists yet)."""
    if category_key == "skill_tags":
        return skill_tag_emoji(entry_key)
    return ""


def _select_emoji(emoji_id: int | None, display_name: str) -> discord.PartialEmoji | None:
    """Only ever returns a real emoji object when a real ID exists.
    Button/SelectOption's own `emoji=` kwarg needs an actual emoji
    object, not display text -- passing the placeholder ':Name:' text
    that emoji_tag() uses for embed bodies would risk Discord rejecting
    the whole interaction, so this returns None instead until the ID is
    filled in (see the note on HELP_CATEGORY_EMOJI_IDS in
    game/emojis.py).
    """
    if emoji_id is None:
        return None
    return discord.PartialEmoji(name=display_name, id=emoji_id)


def _category_select_emoji(category_key: str) -> discord.PartialEmoji | None:
    return _select_emoji(HELP_CATEGORY_EMOJI_IDS.get(category_key), category_key.capitalize())


def _entry_select_emoji(category_key: str, entry_key: str) -> discord.PartialEmoji | None:
    return _select_emoji(_entry_emoji_id(category_key, entry_key), entry_key.capitalize())


def _home_embed() -> discord.Embed:
    return discord.Embed(
        title="Lugic Guidebook",
        description="Pick a category below, then narrow it down to what you need.",
        color=HELP_COLOR,
    )


def _category_embed(title: str) -> discord.Embed:
    icon = help_category_emoji_for_title(title)
    return discord.Embed(
        title=f"{icon} {title}".strip(),
        description="Pick a topic below.",
        color=HELP_COLOR,
    )


def help_category_emoji_for_title(title: str) -> str:
    # Small helper so callers can pass the human title around instead of
    # threading the category_key everywhere; titles are unique in
    # HELP_CATEGORIES so this is an unambiguous reverse lookup.
    for key, cat_title, _, _ in HELP_CATEGORIES:
        if cat_title == title:
            return help_category_emoji(key)
    return ""


def _entry_embed(category_title: str, category_key: str, entry: HelpEntry) -> discord.Embed:
    icon = _entry_text_icon(category_key, entry.key)
    title = f"{icon} {entry.title}".strip()
    embed = discord.Embed(title=title, description=entry.body, color=HELP_COLOR)
    embed.set_footer(text=f"{category_title} -- Lugic Guidebook")
    return embed


class HelpHomeView(discord.ui.View):
    """The /help entry point: one button per top-level category."""

    def __init__(self, timeout: float = 300):
        super().__init__(timeout=timeout)
        for key, title, _blurb, entries in HELP_CATEGORIES:
            self.add_item(_CategoryButton(key, title, entries))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class _CategoryButton(discord.ui.Button):
    def __init__(self, key: str, title: str, entries: list[HelpEntry]):
        super().__init__(
            label=title,
            style=discord.ButtonStyle.primary,
            emoji=_category_select_emoji(key),
        )
        self.key = key
        self.title_text = title
        self.entries = entries

    async def callback(self, interaction: discord.Interaction):
        view = HelpCategoryView(self.key, self.title_text, self.entries)
        await interaction.response.edit_message(embed=_category_embed(self.title_text), view=view)


class HelpCategoryView(discord.ui.View):
    """One category's topic list (a dropdown) plus a Home button back to
    the top-level /help menu."""

    def __init__(self, category_key: str, category_title: str, entries: list[HelpEntry], timeout: float = 300):
        super().__init__(timeout=timeout)
        self.category_key = category_key
        self.category_title = category_title
        self.entries = entries
        self.add_item(_TopicSelect(category_key, category_title, entries))

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, emoji="\U0001f3e0")
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_home_embed(), view=HelpHomeView())

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class _TopicSelect(discord.ui.Select):
    def __init__(self, category_key: str, category_title: str, entries: list[HelpEntry]):
        self.category_key = category_key
        self.category_title = category_title
        self.entries_by_key = {e.key: e for e in entries}
        options = [
            discord.SelectOption(
                label=entry.title,
                value=entry.key,
                description=entry.summary[:100],
                emoji=_entry_select_emoji(category_key, entry.key),
            )
            for entry in entries
        ]
        super().__init__(placeholder=f"Pick a topic in {category_title}...", options=options)

    async def callback(self, interaction: discord.Interaction):
        entry = self.entries_by_key[self.values[0]]
        embed = _entry_embed(self.category_title, self.category_key, entry)
        view = HelpDetailView(self.category_key, self.category_title, list(self.entries_by_key.values()))
        await interaction.response.edit_message(embed=embed, view=view)


class HelpDetailView(discord.ui.View):
    """Shown after picking one specific topic -- a Back button returns to
    this category's full list, Home jumps straight to the top-level menu.
    """

    def __init__(self, category_key: str, category_title: str, entries: list[HelpEntry], timeout: float = 300):
        super().__init__(timeout=timeout)
        self.category_key = category_key
        self.category_title = category_title
        self.entries = entries

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="◀️")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HelpCategoryView(self.category_key, self.category_title, self.entries)
        await interaction.response.edit_message(embed=_category_embed(self.category_title), view=view)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, emoji="\U0001f3e0")
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_home_embed(), view=HelpHomeView())

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class HelpCog(commands.Cog, name="help"):
    """The player-facing guidebook: /help drills down from broad
    categories to one specific topic's full explanation. Content lives in
    game/help_content.py, kept separate from README.md on purpose -- the
    README is written for whoever develops this bot, this is written for
    whoever plays on it.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Browse the guidebook: skill tags, damage math, and more")
    async def help_command(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=_home_embed(), view=HelpHomeView(), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
