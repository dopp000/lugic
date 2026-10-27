"""Plays /practice bot fights through the real commands, the way Discord would run them.

A fake player fights random bots at several Levels and seeds with the real /battle declare (and
its Confirm button) and /battle combat. After each command the practice listener runs. The
script checks that the bot always has its moves ready, that combat never waits on it, that the
fight ends once with the bot's name in the message, and that nothing the bot did broke a rule.
Then it builds a bot by hand through the real skill form and checks the walkthrough counts
/practice bot for its battle step.
"""
import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, asyncio, random; sys.path.insert(0, ROOT)
import warnings; warnings.simplefilter("ignore")
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

import cogs.battle as cb
import cogs.practice as cp
import cogs.tutorial as ct
from game.build_points import check_build, skill_points_spent, passive_points_spent
from game.character import Character, save_character
from game.skills import Skill

USER = 7
CHANNEL = 100
SENT = []        # private (ephemeral) followups
PUBLIC = []      # what the practice cog posted to the channel


def fake_interaction(args=None):
    i = MagicMock()
    i.user.id = USER
    i.user.bot = False
    i.channel_id = CHANNEL
    i.channel = MagicMock(spec=discord.TextChannel)
    i.channel.id = CHANNEL
    status = MagicMock(id=900, edit=AsyncMock(), delete=AsyncMock())
    i.channel.send = AsyncMock(side_effect=lambda text=None, **k: (PUBLIC.append(text), status)[1] if text else status)
    i.namespace = dict(args or {})
    i.response.type = None
    i.response.defer = AsyncMock()
    i.response.send_message = AsyncMock()
    i.response.send_modal = AsyncMock()
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
    bot.get_cog = lambda name: battle_cog if name == "battle" else None
    chan = MagicMock()
    chan.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()))
    chan.send = AsyncMock(return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()))
    bot.get_channel = lambda channel_id: chan
    pcog = cp.PracticeCog(bot)
    tcog = ct.TutorialCog(bot)

    rook = Character(owner_id=USER, name="Rook", bp_total=63, sp_total=3, pp_total=2)
    rook.add_skill(Skill("Jab", 4, 2, 2))
    save_character(rook)

    real_sleep = cb.asyncio.sleep
    async def no_sleep(*a, **k): return None
    cb.asyncio.sleep = no_sleep

    async def reset():
        battle_cog.battles.clear()
        pcog.sessions.clear()

    async def start(**opts):
        args = dict(
            character="Rook", level=None, skill_points=None, passive_points=None, skills="random", passives="random",
            attacks=None, show_plan=True,
        )
        args.update(opts)
        PUBLIC.clear(); SENT.clear()
        i = fake_interaction()
        await pcog.versus.callback(pcog, i, **args)
        return i

    async def declare(battle, slot, target, target_slot):
        args = dict(fighter="Rook", slot=slot, skill_name="Jab", target=target, target_slot=target_slot)
        i = fake_interaction(args)
        await battle_cog.declare.callback(battle_cog, i, **args)
        view = i.response.send_message.call_args.kwargs.get("view")
        assert view is not None, i.response.send_message.call_args
        i2 = fake_interaction()
        await view.confirm.callback(i2)
        await pcog.on_declare_locked(i2, battle.get_fighter("Rook"))

    async def play(label, max_rounds=80, **opts):
        await reset()
        await start(**opts)
        battle = battle_cog.battles[CHANNEL]
        session = pcog.sessions[CHANNEL]
        foe = battle.get_fighter(session.foe_name)
        player = battle.get_fighter("Rook")
        assert foe.owner_id is None and foe.side == "B"
        for round_no in range(1, max_rounds + 1):
            assert battle.round_number == round_no, (label, round_no)
            assert foe.has_declared() or not foe.can_act(), (label, round_no, "the bot has not picked")
            if player.can_act():
                for slot in range(1, min(player.skill_slots, 3) + 1):
                    await declare(battle, slot, foe.name, 1 + (slot + round_no) % foe.skill_slots)
            assert battle.all_declared(), (label, round_no, "combat would refuse to run")
            PUBLIC.clear()
            i = fake_interaction()
            await battle_cog.combat.callback(battle_cog, i)
            refused = [c for c in i.response.send_message.call_args_list if "Not everyone" in str(c)]
            assert not refused, (label, round_no, "combat waited on the bot")
            await pcog.on_app_command_completion(i, SimpleNamespace(qualified_name="battle combat"))
            if session.finished:
                ends = [p for p in PUBLIC if p and (f"{foe.name} went down" in p or "knocked out" in p or "Both sides" in p)]
                assert len(ends) == 1, (label, PUBLIC)
                assert "/practice bot" in ends[0], ends[0]
                return round_no, ends[0], session.bot_char
            assert any(p and ("is going to do this" in p or "keeping them to itself" in p or "can't act" in p) for p in PUBLIC), (
                label, round_no, PUBLIC)
        raise AssertionError(f"{label}: the fight never ended in {max_rounds} rounds")

    # ---- whole fights against random bots, matching Rook and at each Level
    results = {"won": 0, "lost": 0}
    settings = [dict(), dict(level=1), dict(level=2, skill_points=2), dict(level=4, skill_points=5, passive_points=4),
                dict(level=5, skill_points=8, passive_points=6, attacks=2), dict(attacks=1, show_plan=False)]
    for n, extra in enumerate(settings):
        for seed in range(3):
            random.seed(100 * n + seed)
            rounds, text, char = await play(f"setting {n} seed {seed}", **extra)
            report = check_build(char)
            assert report.ok, (n, seed, report.problems)
            assert skill_points_spent(char) <= char.sp_total and passive_points_spent(char) <= char.pp_total
            results["won" if "went down" in text else "lost"] += 1
    print(f"ok  {sum(results.values())} fights against random bots ended once, each with a legal bot "
          f"(wins {results['won']}, losses {results['lost']})")

    # ---- a bot with the same points as Rook, and one with the options changed
    await reset()
    await start()
    char = pcog.sessions[CHANNEL].bot_char
    assert (char.bp_total, char.sp_total, char.pp_total) == (63, 3, 2), char
    assert load_missing("Bot")
    await reset()
    await start(level=1, skill_points=0, passive_points=0)
    char = pcog.sessions[CHANNEL].bot_char
    assert (char.bp_total, char.sp_total, char.pp_total) == (30, 0, 0) and not char.passives
    print("ok  the bot copies Rook's points and the options change them")

    # ---- a bot built by hand through the real skill form
    await reset()
    PUBLIC.clear(); SENT.clear()
    i = await start(skills="manual", passives="manual", level=2, skill_points=1, passive_points=2)
    battle = battle_cog.battles[CHANNEL]
    session = pcog.sessions[CHANNEL]
    foe = battle.get_fighter("Bot")
    assert not foe.skills and not foe.has_declared()
    assert any(p and "no skills yet" in p for p in PUBLIC), PUBLIC
    print("ok  a bot with no skills says so")

    async def add_skill(name, stats, statuses, triggers=""):
        i = fake_interaction()
        await pcog.addskill.callback(pcog, i)
        modal = i.response.send_modal.call_args.args[0]
        modal.skill_name._value, modal.stats_input._value, modal.status_input._value = name, stats, statuses
        modal.attack_weight_input._value = ""
        modal.triggers_input._value = triggers
        i2 = fake_interaction()
        await modal.on_submit(i2)
        view = i2.response.send_message.call_args.kwargs.get("view")
        if view is None:
            return i2.response.send_message.call_args.args[0]
        i3 = fake_interaction()
        await view.confirm.callback(i3)
        assert i3.client.dispatch.call_args[0][0] == "skill_saved"
        assert i3.client.get_channel.call_count == 0, "a practice skill was sent to the admin log"
        PUBLIC.clear()
        await pcog.on_skill_saved(i3, session.bot_char)
        return None

    refusal = await add_skill("Two Line", "3, 1, 2, Slash", "none", "[On Use] Coin Power +1\n[On Use] Deal +10% damage\n[Attack End] Heal 4 HP")
    assert refusal and "skill_points" in refusal and "/charadmin" not in refusal, refusal
    assert (await add_skill("Chop", "3, 1, 2, Slash", "none", "[On Use] Coin Power +1\n[On Use] Deal +10% damage")) is None
    assert "chop" in foe.skills and foe.has_declared(), "the new skill did not give the bot a move"
    assert any(p and "Bot is going to do this" in p for p in PUBLIC), PUBLIC
    print("ok  the skill form checks the bot's Skill Points, and a saved skill gives it a move at once")

    i = fake_interaction()
    await pcog.addpassive.callback(
        pcog, i, passive_name="Thick Skin", kind=SimpleNamespace(value="passive", name="Passive"), cost=2,
        line_1="[Combat Start] Gain 10 Shield", line_2=None, line_3=None,
    )
    assert "2/2 PP used" in i.response.send_message.call_args.args[0]
    assert len(foe.passive_skills) == 1
    print("ok  a passive is paid from the bot's Passive Points and reaches the fighter")

    await declare(battle, 1, "Bot", 1)
    i = fake_interaction()
    await battle_cog.combat.callback(battle_cog, i)
    assert battle.history, "the round did not run"
    await pcog.on_app_command_completion(i, SimpleNamespace(qualified_name="battle combat"))
    assert battle.round_number == 2 and (foe.has_declared() or not foe.can_act())
    print("ok  a round runs against the hand-built bot and it picks again")

    i = fake_interaction()
    await pcog.sheet.callback(pcog, i)
    assert i.response.send_message.call_args.kwargs["embed"] is not None
    i = fake_interaction()
    await pcog.removeskill.callback(pcog, i, skill_name="Chop")
    assert "no skills now" in i.response.send_message.call_args.args[0] and not foe.skills
    assert not foe.declared_actions
    print("ok  sheet works, and removing the last skill clears its moves")

    # ---- the walkthrough counts /practice bot as its battle step
    await reset()
    progress = tcog.store.get(USER)
    progress.started = True
    progress.done = ["create", "view", "skill", "bp"]
    i = await start()
    SENT.clear()
    await tcog.on_app_command_completion(i, SimpleNamespace(qualified_name="practice bot"))
    text = "\n".join(t for t in SENT if t)
    assert "Step 8 of 10: Pick your moves" in text and "Bot" in text, text
    print("ok  the walkthrough counts /practice bot and jumps to picking moves")

    cb.asyncio.sleep = real_sleep


def load_missing(name):
    from game.character import load_character
    return load_character(name) is None


asyncio.run(main())
