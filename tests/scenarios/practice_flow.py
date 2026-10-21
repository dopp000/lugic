"""Plays a whole /practice fight through the real commands, the way Discord would run them.

A fake player starts a practice fight, picks moves with the real /battle declare (including its
Confirm button) and rolls rounds with the real /battle combat. After every command the practice
and tutorial listeners run, like the bot would run them. The script checks that the Dummy always
has its moves ready, that combat never waits on it, that a mirrored move really Clashes, that
the fight ends once, and that the walkthrough counts /practice start for its battle steps.
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
from game.character import Character, save_character
from game.skills import Skill

USER = 7
CHANNEL = 100
SENT = []        # private (ephemeral) followups
PUBLIC = []      # what the practice cog posted to the channel
EDITS = []       # every edit of the combat message


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
    i.response.edit_message = AsyncMock()
    msg = MagicMock(edit=AsyncMock(side_effect=lambda **k: EDITS.append(k)), delete=AsyncMock())
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

    rook = Character(owner_id=USER, name="Rook")
    rook.add_skill(Skill("Jab", 4, 2, 2))
    save_character(rook)

    # the walkthrough is already past the character steps
    progress = tcog.store.get(USER)
    progress.started = True
    progress.done = ["create", "view", "skill", "bp"]

    async def after(name, i):
        """Lets every cog react to a finished command, then returns the walkthrough's private message."""
        SENT.clear()
        command = SimpleNamespace(qualified_name=name)
        await pcog.on_app_command_completion(i, command)
        await tcog.on_app_command_completion(i, command)
        return "\n".join(t for t in SENT if t)

    # ---- start: one command sets up the fight, and the walkthrough skips ahead
    PUBLIC.clear()
    i = fake_interaction()
    await pcog.start.callback(pcog, i, character=None, tier="fodder", show_plan=True)
    text = await after("practice start", i)
    battle = battle_cog.battles[CHANNEL]
    dummy = battle.get_fighter("Dummy")
    assert battle.get_fighter("Rook") is not None and dummy.has_declared()
    assert "Step 8 of 10: Pick your moves" in text, text
    assert "✅" in text and "Dummy is ready to fight back" in text, text
    assert PUBLIC and "Dummy is going to do this" in PUBLIC[-1], PUBLIC
    print("ok  /practice start sets up the fight and the walkthrough jumps to step 8")

    real_sleep = cb.asyncio.sleep
    async def no_sleep(*a, **k): return None
    cb.asyncio.sleep = no_sleep
    random.seed(11)

    async def declare(slot, target_slot):
        args = dict(fighter="Rook", slot=slot, skill_name="Jab", target="Dummy", target_slot=target_slot)
        i = fake_interaction(args)
        await battle_cog.declare.callback(battle_cog, i, **args)
        view = i.response.send_message.call_args.kwargs.get("view")
        assert view is not None, i.response.send_message.call_args
        i2 = fake_interaction()
        await view.confirm.callback(i2)
        assert i2.client.dispatch.call_args[0][0] == "declare_locked"
        SENT.clear()
        await pcog.on_declare_locked(i2, battle.get_fighter("Rook"))
        await tcog.on_declare_locked(i2, battle.get_fighter("Rook"))
        return "\n".join(t for t in SENT if t)

    async def combat():
        i = fake_interaction()
        EDITS.clear(); PUBLIC.clear()
        await battle_cog.combat.callback(battle_cog, i)
        refused = [c for c in i.response.send_message.call_args_list if "Not everyone" in str(c)]
        assert not refused, "combat waited on the Dummy"
        return i

    clashes = 0
    unopposed = 0
    finished_text = None
    CLASH_HEADER = "\u2694\ufe0f **Rook** vs **Dummy**"
    player = battle.get_fighter("Rook")
    for round_no in range(1, 60):
        assert battle.round_number == round_no
        can_act = dummy.can_act()
        assert dummy.has_declared() or not can_act, f"round {round_no}: the Dummy has not picked"
        plan = [(s, a.target_slot) for s, a in sorted(dummy.declared_actions.items())]
        # odd rounds answer the first move (a Clash), even rounds aim past every move (unopposed)
        expect = None
        if plan and round_no % 2 == 1:
            (first_slot, first_target), expect = plan[0], "clash"
            slot, target_slot = first_target, first_slot
        elif plan:
            aimed = {t for _, t in plan}
            slot, target_slot, expect = next(s for s in (1, 2, 3) if s not in aimed), 1, "unopposed"
        else:
            slot, target_slot = 1, 1       # the Dummy is Staggered this round
        if not player.can_act():
            expect = None                      # Rook is Staggered, so there is no move to answer with
        if player.can_act():
            text = await declare(slot, target_slot)
            if round_no == 1:
                assert "Step 9 of 10: Run the round" in text, text
                print("ok  locking in the one move finishes step 8 (the Dummy had already picked)")
        assert battle.all_declared(), f"round {round_no}: combat would refuse to run"
        i = await combat()
        description = " ".join(str(e.get("embed").description) for e in EDITS if e.get("embed") is not None)
        if expect == "clash":
            assert CLASH_HEADER in description, (round_no, plan, slot, target_slot, description[:300])
            clashes += 1
        elif expect == "unopposed":
            assert CLASH_HEADER not in description and "(unopposed)" in description, (round_no, description[:300])
            unopposed += 1
        await after("battle combat", i)
        if round_no == 1:
            print("ok  combat runs without declaring for the Dummy, and its next plan is posted")
        result_posts = [p for p in PUBLIC if p and ("You win" in p or "knocked out" in p or "Both sides" in p)]
        if pcog.sessions[CHANNEL].finished:
            finished_text = result_posts[-1] if result_posts else None
            break
        assert PUBLIC, (round_no, "nothing was posted for the new round")
        if dummy.can_act():
            assert any(p and "Dummy is going to do this" in p for p in PUBLIC), (round_no, PUBLIC)
            assert dummy.has_declared()
            if player.can_act():
                assert not battle.all_declared()      # now it is the player's turn to pick
        else:
            assert any(p and "can't act" in p for p in PUBLIC), (round_no, PUBLIC)
    cb.asyncio.sleep = real_sleep

    assert finished_text, "the fight never ended"
    assert f"after {battle.round_number - 1} round" in finished_text, finished_text
    print(f"ok  the fight ended after {battle.round_number - 1} rounds: {finished_text.split('.')[0]}")
    assert clashes >= 1 and unopposed >= 1, (clashes, unopposed)
    print(f"ok  answering the Dummy's move always Clashed ({clashes} rounds), aiming past it never did ({unopposed} rounds)")

    # ---- the result is announced once, however many commands follow
    PUBLIC.clear()
    await pcog._check(CHANNEL, fake_interaction().channel)
    assert not PUBLIC, PUBLIC
    print("ok  nothing more is posted once the fight is over")

    # ---- go again, then stop: the walkthrough counts /practice stop as cleaning up
    i = fake_interaction()
    await pcog.start.callback(pcog, i, character="Rook", tier="elite", show_plan=False)
    new_battle = battle_cog.battles[CHANNEL]
    assert new_battle is not battle and new_battle.round_number == 1
    assert len(new_battle.get_fighter("Dummy").declared_actions) == 3
    print("ok  starting again replaces the finished fight")

    for step in ("battle", "join", "foe", "declare", "combat"):
        if step not in progress.done:
            progress.done.append(step)
    i = fake_interaction()
    await pcog.stop.callback(pcog, i)
    text = await after("practice stop", i)
    assert CHANNEL not in battle_cog.battles and CHANNEL not in pcog.sessions
    assert progress.finished and "/practice start" in text, (progress, text)
    print("ok  /practice stop clears the channel and finishes the walkthrough")


asyncio.run(main())
