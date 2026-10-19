import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""
Drives the REAL /help command end to end: Home -> category button ->
topic dropdown -> detail embed -> Back -> Home, for both categories
(Skill Tags and Damage Math), using a minimal fake Discord interaction
layer (same pattern as the other test scripts this session).
"""
import asyncio
import sys

sys.path.insert(0, ROOT)

import discord  # noqa: E402
import cogs.help as help_mod  # noqa: E402


class FakeResponse:
    def __init__(self):
        self.calls = []

    async def send_message(self, content=None, **kwargs):
        self.calls.append(("send_message", content, kwargs))

    async def edit_message(self, content=None, **kwargs):
        self.calls.append(("edit_message", content, kwargs))


class FakeUser:
    def __init__(self, uid, name):
        self.id = uid
        self.name = name


class FakeInteraction:
    def __init__(self, user):
        self.user = user
        self.response = FakeResponse()


def last_call(interaction, kind):
    for call in reversed(interaction.response.calls):
        if call[0] == kind:
            return call[1], call[2]
    return None, {}


async def click_button(view, label, interaction):
    for child in view.children:
        if isinstance(child, discord.ui.Button) and getattr(child, "label", None) == label:
            await child.callback(interaction)
            return
    raise RuntimeError(f"No button labeled {label!r} on {view!r} (has: "
                        f"{[getattr(c, 'label', None) for c in view.children]})")


async def select_option(view, value, interaction):
    for child in view.children:
        if isinstance(child, discord.ui.Select):
            # .values is read-only, normally populated from the real
            # interaction payload Discord sends, set the private
            # backing field directly to simulate a pick.
            child._values = [value]
            await child.callback(interaction)
            return
    raise RuntimeError(f"No select found on {view!r}")


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


async def main():
    user = FakeUser(1, "Tester")

    # --- /help ---
    help_i = FakeInteraction(user)
    cog = help_mod.HelpCog(bot=None)
    await help_mod.HelpCog.help_command.callback(cog, help_i)
    content, kwargs = last_call(help_i, "send_message")
    check("help command sent an embed + view", kwargs.get("embed") is not None and kwargs.get("view") is not None)
    check("help response is ephemeral", kwargs.get("ephemeral") is True)
    home_view = kwargs["view"]
    check("home view has one button per category", len(home_view.children) == len(help_mod.HELP_CATEGORIES))

    for key, title, _blurb, entries in help_mod.HELP_CATEGORIES:
        print(f"--- category: {title} ---")

        # --- click the category button ---
        cat_i = FakeInteraction(user)
        await click_button(home_view, title, cat_i)
        content, kwargs = last_call(cat_i, "edit_message")
        check(f"[{title}] category click edited the message", kwargs.get("embed") is not None)
        category_view = kwargs["view"]
        check(f"[{title}] category view has a select + Home button", len(category_view.children) == 2)

        # --- pick every topic in this category and check the detail embed ---
        for entry in entries:
            pick_i = FakeInteraction(user)
            await select_option(category_view, entry.key, pick_i)
            content, kwargs = last_call(pick_i, "edit_message")
            embed = kwargs.get("embed")
            check(f"[{title}] picking '{entry.title}' produced an embed", embed is not None)
            check(f"[{title}] '{entry.title}' embed title mentions the topic", entry.title in embed.title)
            check(f"[{title}] '{entry.title}' embed body matches content", embed.description == help_mod.decorate_text(entry.body))
            detail_view = kwargs["view"]
            check(f"[{title}] '{entry.title}' detail view has Back + Home", len(detail_view.children) == 2)

            # --- Back returns to the category's topic list ---
            back_i = FakeInteraction(user)
            await click_button(detail_view, "Back", back_i)
            content, kwargs = last_call(back_i, "edit_message")
            check(f"[{title}] Back from '{entry.title}' returns to category list", len(kwargs["view"].children) == 2)

            # --- Home from the detail view jumps straight to Home ---
            home_i = FakeInteraction(user)
            await click_button(detail_view, "Home", home_i)
            content, kwargs = last_call(home_i, "edit_message")
            check(
                f"[{title}] Home from '{entry.title}' detail jumps to the top-level menu",
                len(kwargs["view"].children) == len(help_mod.HELP_CATEGORIES),
            )

        # --- Home from the category list ---
        cat_home_i = FakeInteraction(user)
        await click_button(category_view, "Home", cat_home_i)
        content, kwargs = last_call(cat_home_i, "edit_message")
        check(f"[{title}] Home from category list jumps to the top-level menu",
              len(kwargs["view"].children) == len(help_mod.HELP_CATEGORIES))

    # --- sanity: every dropdown option's emoji is either None or a real PartialEmoji with an id ---
    for key, title, _blurb, entries in help_mod.HELP_CATEGORIES:
        view = help_mod.HelpCategoryView(key, title, entries)
        select = next(c for c in view.children if isinstance(c, discord.ui.Select))
        for opt in select.options:
            check(
                f"[{title}] option '{opt.label}' emoji is safe (None or has a real id)",
                opt.emoji is None or (isinstance(opt.emoji, discord.PartialEmoji) and opt.emoji.id is not None),
            )

    print("\nAll /help checks passed.")


asyncio.run(main())
