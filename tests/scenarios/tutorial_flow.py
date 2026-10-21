"""Plays the whole /startingoff walkthrough through the real commands and forms.

A fake player makes a character, adds a skill through the real skill form, then runs a
practice fight. After each command the tutorial's listener runs, and the script checks
that the right step finished at the right moment and that nothing finished early.
"""
import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, asyncio, random; sys.path.insert(0, ROOT)
import warnings; warnings.simplefilter("ignore")
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from discord.ext import commands

import cogs.battle as cb
import cogs.character as cc
from game import tutorial as tut
from game.build_points import check_build
from game.character import load_character

USER = 7
CHANNEL = 100
SENT = []


def fake_interaction(args):
    i = MagicMock()
    i.user.id = USER
    i.user.bot = False
    i.channel_id = CHANNEL
    i.channel = MagicMock(spec=discord.TextChannel)
    i.channel.id = CHANNEL
    i.channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()))
    i.namespace = dict(args)
    i.response.type = None
    i.response.defer = AsyncMock()
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    msg = MagicMock(edit=AsyncMock(), delete=AsyncMock())
    i.followup.send = AsyncMock(side_effect=lambda text=None, **k: SENT.append(text) or msg)
    i.original_response = AsyncMock(return_value=MagicMock(id=555, edit=AsyncMock(), delete=AsyncMock()))
    i.client = MagicMock()
    i.client.get_channel = MagicMock(return_value=None)
    return i


async def main():
    bot = MagicMock()
    battle_cog = cb.BattleCog(bot)
    char_cog = cc.CharacterCog(bot)
    bot.get_cog = lambda name: battle_cog if name == "battle" else None
    chan = MagicMock()
    chan.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()))
    chan.send = AsyncMock(return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()))
    bot.get_channel = lambda channel_id: chan
    tcog = __import__("cogs.tutorial", fromlist=["x"]).TutorialCog(bot)

    async def run(command_name, callback, owner, args, **extra):
        """Runs one command like Discord would, then lets the tutorial react. Returns the tutorial's message."""
        SENT.clear()
        i = fake_interaction(args)
        await callback(owner, i, **args, **extra)
        await tcog.on_app_command_completion(i, SimpleNamespace(qualified_name=command_name))
        return i, "\n".join(t for t in SENT if t)

    async def confirm_skill(modal_owner_target, values, on_saved=None):
        """Fills in and submits the real skill form, then presses Confirm on its preview."""
        modal = cb.AddSkillModal(modal_owner_target, on_saved=on_saved)
        modal.skill_name._value = values[0]
        modal.stats_input._value = values[1]
        modal.attack_weight_input._value = ""
        modal.status_input._value = values[2]
        modal.triggers_input._value = ""
        i = fake_interaction({})
        await modal.on_submit(i)
        args, kwargs = i.response.send_message.call_args
        view = kwargs.get("view")
        assert view is not None, ("the form refused the tutorial's example values:", args, kwargs)
        i2 = fake_interaction({})
        await view.confirm.callback(i2)
        # the real confirm dispatches this; the cog's listener is what reacts
        assert i2.client.dispatch.call_args[0][0] == "skill_saved"
        SENT.clear()
        await tcog.on_skill_saved(i2, modal_owner_target)
        return "\n".join(t for t in SENT if t)

    # ---- before starting: a tip and the pointer, but no steps
    _, text = await run("character create", char_cog.create.callback, char_cog, {"name": "Rook"})
    assert "Tip:" in text and "/request stats" in text, text
    assert "/startingoff" in text, text
    assert "Step 1" not in text, text
    print("ok  tip and pointer before starting, no steps yet")

    # ---- start: the character already exists, so step 1 is already done
    i = fake_interaction({})
    await tcog.startingoff.callback(tcog, i)
    embed = i.response.send_message.call_args.kwargs["embed"]
    assert "Look at your sheet" in embed.title and "Rook" in embed.description, (embed.title, embed.description)
    print("ok  starting with a character already made skips step 1")

    # ---- step 2: view someone else's name does nothing, own name finishes it
    _, text = await run("character view", char_cog.view.callback, char_cog, {"name": "Nobody"})
    assert "Step 3" not in text, text
    _, text = await run("character view", char_cog.view.callback, char_cog, {"name": "Rook"})
    assert "✅" in text and "Step 3 of 10: Teach it a skill" in text and "Quick Jab" in text, text
    assert "public:True" in text, text   # the first-use tip for view rides along
    print("ok  view finishes step 2 and points to the skill form")

    # ---- step 3: the skill form, with exactly the values the step tells the player to type
    character = load_character("Rook")
    text = await confirm_skill(character, ("Quick Jab", "4, 2, 2, Blunt", "none"), on_saved=lambda: cc.save_character(character))
    assert "✅" in text and "Step 4 of 10: Check your Build Points" in text, text
    assert load_character("Rook").skills, "skill was not saved to the character"
    problems = check_build(load_character("Rook")).all_problems()
    assert not problems, ("the example skill breaks the Build Point rules:", problems)
    print("ok  the example skill is valid, saves, and finishes step 3")

    # ---- step 4
    _, text = await run("character bp", char_cog.bp.callback, char_cog, {"name": "Rook"})
    assert "Step 5 of 10: Start a battle" in text, text
    print("ok  bp finishes step 4")

    # ---- step 5: a battle in the channel
    _, text = await run("battle create", battle_cog.create.callback, battle_cog, {})
    assert "Step 6 of 10: Join it" in text and "character_name:Rook" in text, text
    print("ok  battle create finishes step 5")

    # ---- step 6 and 7
    _, text = await run("battle addfighter", battle_cog.addfighter.callback, battle_cog, {"side": "A", "character_name": "Rook"})
    assert "Step 7 of 10: Add something to hit" in text and "Dummy" in text, text
    _, text = await run("battle addfighter", battle_cog.addfighter.callback, battle_cog, {"side": "B", "name": "Dummy"})
    assert "Step 8" not in text, text           # no skill on Dummy yet
    battle = battle_cog.battles[CHANNEL]
    dummy = battle.get_fighter("Dummy")
    text = await confirm_skill(dummy, ("Quick Jab", "4, 2, 2, Blunt", "none"))
    assert "✅" in text and "Step 8 of 10: Pick your moves" in text, text
    assert "fighter:Rook slot:1 skill_name:Quick Jab target:Dummy target_slot:1" in text, text
    print("ok  joining, adding Dummy and its skill finish steps 6 and 7")

    # ---- step 8: both sides declare, and each has to press Confirm to lock the move in
    async def declare_and_lock(fighter, target):
        args = dict(fighter=fighter, target=target, slot=1, skill_name="Quick Jab", target_slot=1)
        i, text = await run("battle declare", battle_cog.declare.callback, battle_cog, args)
        view = i.response.send_message.call_args.kwargs.get("view")
        assert view is not None, ("declare did not offer a Confirm button:", i.response.send_message.call_args)
        assert not battle.get_fighter(fighter).has_declared(), "declare locked in before Confirm"
        assert "Step 9" not in text, text          # nothing finishes until the move is locked in
        i2 = fake_interaction({})
        await view.confirm.callback(i2)
        assert i2.client.dispatch.call_args[0][0] == "declare_locked"
        SENT.clear()
        await tcog.on_declare_locked(i2, battle.get_fighter(fighter))
        return "\n".join(t for t in SENT if t)

    text = await declare_and_lock("Rook", "Dummy")
    assert "Step 9" not in text, text              # Dummy has not declared yet
    text = await declare_and_lock("Dummy", "Rook")
    assert "Step 9 of 10: Run the round" in text, text
    print("ok  locking in both moves finishes step 8")

    # ---- step 9: the round
    real_sleep = cb.asyncio.sleep
    async def no_sleep(*a, **k): return None
    cb.asyncio.sleep = no_sleep
    random.seed(3)
    _, text = await run("battle combat", battle_cog.combat.callback, battle_cog, {})
    cb.asyncio.sleep = real_sleep
    assert "Step 10 of 10: Clean up" in text, text
    print("ok  combat finishes step 9")

    # ---- step 10: end, and the walkthrough finishes
    _, text = await run("battle end", battle_cog.end.callback, battle_cog, {})
    assert "/character say" in text and "Step" not in text, text
    assert tcog.store.get(USER).finished
    print("ok  battle end finishes the walkthrough")

    # ---- the tips were recorded
    progress = tcog.store.get(USER)
    assert "battle combat" in progress.seen_tips and "character create" in progress.seen_tips
    print("ok  tips were recorded once")

asyncio.run(main())
