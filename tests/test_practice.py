"""Checks /practice: the Dummy's rules (game/practice.py) and the Discord glue (cogs/practice.py).

The whole fight through the real /battle commands lives in tests/scenarios/practice_flow.py.
Run all tests from the repo root with:  python -m unittest discover tests
"""
import asyncio
import os
import random
import sys
import tempfile
import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.simplefilter("ignore")

import discord  # noqa: E402

import cogs.battle as cb  # noqa: E402
import cogs.practice as practice_cog  # noqa: E402
from game import practice as pr  # noqa: E402
from game.battle import Battle, Fighter  # noqa: E402
from game.character import Character, save_character  # noqa: E402
from game.npc_tiers import NPC_TIERS  # noqa: E402
from game.skills import Skill  # noqa: E402

USER = 7
CHANNEL = 100


def battle_with_dummy(tier="fodder", slots=3):
    battle = Battle(channel_id=CHANNEL)
    player = Fighter("Rook", "A", owner_id=USER, skill_slots=slots)
    player.add_skill(Skill("Jab", 4, 2, 2))
    dummy = pr.build_dummy(tier)
    battle.add_fighter(player)
    battle.add_fighter(dummy)
    return battle, player, dummy


class Dummy(unittest.TestCase):
    def test_it_matches_the_tier_and_nobody_owns_it(self):
        for key in pr.PRACTICE_TIERS:
            d = pr.build_dummy(key)
            preset = NPC_TIERS[key]
            self.assertEqual((d.max_hp, d.skill_slots, d.power, d.level), (preset.hp, preset.skill_slots, preset.power, preset.level))
            self.assertIsNone(d.owner_id)
            self.assertEqual(d.side, "B")
            self.assertEqual(len(d.skills), 3)

    def test_an_unknown_tier_falls_back_to_the_default(self):
        self.assertEqual(pr.build_dummy("nope").max_hp, NPC_TIERS[pr.DEFAULT_TIER].hp)

    def test_every_tier_has_a_note(self):
        self.assertEqual(set(pr.TIER_NOTES), set(pr.PRACTICE_TIERS))

    def test_its_skills_cover_three_damage_types(self):
        self.assertEqual({s.damage_type for s in pr.dummy_skills()}, {"slash", "blunt", "pierce"})


class Planning(unittest.TestCase):
    def test_every_slot_gets_a_move_at_a_real_enemy_slot(self):
        battle, player, dummy = battle_with_dummy("elite")
        for seed in range(20):
            plan = pr.plan_moves(battle, dummy, random.Random(seed))
            self.assertEqual([m.slot for m in plan], [1, 2, 3])
            for m in plan:
                self.assertIs(m.target, player)
                self.assertIn(m.target_slot, (1, 2, 3))
                self.assertIn(m.skill.name.lower(), dummy.skills)

    def test_slots_aim_at_different_enemy_slots_when_there_is_room(self):
        battle, player, dummy = battle_with_dummy("elite")
        for seed in range(20):
            plan = pr.plan_moves(battle, dummy, random.Random(seed))
            self.assertEqual(len({m.target_slot for m in plan}), 3)

    def test_max_moves_limits_how_many_slots_act(self):
        battle, player, dummy = battle_with_dummy("elite")
        plan = pr.plan_moves(battle, dummy, random.Random(1), max_moves=1)
        self.assertEqual([m.slot for m in plan], [1])
        self.assertEqual(len(pr.plan_moves(battle, dummy, random.Random(1), max_moves=2)), 2)

    def test_each_tier_has_a_move_count_within_its_slots(self):
        for key in pr.PRACTICE_TIERS:
            self.assertTrue(1 <= pr.MOVES_PER_ROUND[key] <= NPC_TIERS[key].skill_slots)

    def test_it_copes_with_fewer_enemy_slots_than_its_own(self):
        battle, player, dummy = battle_with_dummy("elite", slots=1)
        plan = pr.plan_moves(battle, dummy, random.Random(1))
        self.assertEqual(len(plan), 3)
        self.assertTrue(all(m.target_slot == 1 for m in plan))

    def test_it_never_aims_at_its_own_side_or_at_the_fallen(self):
        battle, player, dummy = battle_with_dummy()
        friend = Fighter("Pal", "B")
        down = Fighter("Down", "A", hp=0)
        battle.add_fighter(friend)
        battle.add_fighter(down)
        for seed in range(30):
            for m in pr.plan_moves(battle, dummy, random.Random(seed)):
                self.assertIs(m.target, player)

    def test_a_dummy_that_cannot_act_plans_nothing(self):
        battle, player, dummy = battle_with_dummy()
        dummy.current_stagger_tier = 1
        self.assertEqual(pr.plan_moves(battle, dummy), [])
        self.assertFalse(pr.needs_plan(dummy))

    def test_no_enemies_means_no_plan(self):
        battle, player, dummy = battle_with_dummy()
        player.hp = 0
        self.assertEqual(pr.plan_moves(battle, dummy), [])

    def test_applying_the_plan_declares_every_slot_and_only_the_player_is_missing(self):
        battle, player, dummy = battle_with_dummy()
        plan = pr.plan_moves(battle, dummy, random.Random(2))
        self.assertTrue(pr.needs_plan(dummy))
        self.assertEqual(pr.apply_plan(dummy, plan), len(plan))
        self.assertFalse(pr.needs_plan(dummy))
        self.assertFalse(battle.all_declared())
        player.declare_in_slot(1, player.get_skill("Jab"), dummy, 1)
        self.assertTrue(battle.all_declared())

    def test_the_clash_the_plan_describes_is_a_real_clash(self):
        battle, player, dummy = battle_with_dummy()
        plan = pr.plan_moves(battle, dummy, random.Random(4))
        pr.apply_plan(dummy, plan)
        first = plan[0]
        player.declare_in_slot(first.target_slot, player.get_skill("Jab"), dummy, first.slot)
        partner = battle.find_mutual_clash_partner(dummy, first.slot)
        self.assertEqual(partner, (player, first.target_slot))
        text = pr.describe_plan(plan, dummy)
        self.assertIn(f"{player.name}'s Slot {first.target_slot} on {dummy.name}'s Slot {first.slot}", text)

    def test_a_single_move_is_described_as_it(self):
        battle, player, dummy = battle_with_dummy()
        plan = pr.plan_moves(battle, dummy, random.Random(4), max_moves=1)
        self.assertIn("To Clash it,", pr.describe_plan(plan, dummy))
        plan = pr.plan_moves(battle, dummy, random.Random(4))
        self.assertIn("To Clash the first one,", pr.describe_plan(plan, dummy))

    def test_a_different_slot_is_not_a_clash(self):
        battle, player, dummy = battle_with_dummy()
        plan = pr.plan_moves(battle, dummy, random.Random(4))
        pr.apply_plan(dummy, plan)
        first = plan[0]
        other = next(s for s in (1, 2, 3) if s != first.target_slot)
        player.declare_in_slot(other, player.get_skill("Jab"), dummy, first.slot)
        self.assertIsNone(battle.find_mutual_clash_partner(dummy, first.slot))


class Outcome(unittest.TestCase):
    def test_still_going(self):
        battle, player, dummy = battle_with_dummy()
        self.assertIsNone(pr.outcome(battle))

    def test_won_lost_and_draw(self):
        battle, player, dummy = battle_with_dummy()
        dummy.hp = 0
        self.assertEqual(pr.outcome(battle), "won")
        player.hp = 0
        self.assertEqual(pr.outcome(battle), "draw")
        dummy.hp = 10
        self.assertEqual(pr.outcome(battle), "lost")

    def test_an_eliminated_fighter_counts_even_with_hp_restored(self):
        battle, player, dummy = battle_with_dummy()
        dummy.hp = 5
        dummy.eliminated = True
        self.assertEqual(pr.outcome(battle), "won")

    def test_the_end_text_counts_rounds(self):
        self.assertIn("after 1 round.", pr.end_text("won", 1))
        self.assertIn("after 3 rounds.", pr.end_text("lost", 3))
        self.assertIn("/practice start", pr.end_text("draw", 2))


class Words(unittest.TestCase):
    def texts(self):
        battle, player, dummy = battle_with_dummy()
        plan = pr.plan_moves(battle, dummy, random.Random(0))
        yield pr.describe_plan(plan, dummy)
        yield pr.describe_plan(plan, dummy, show=False)
        yield pr.describe_plan([], dummy)
        for result in ("won", "lost", "draw"):
            yield pr.end_text(result, 2)
        yield pr.start_text(player, dummy, None, True)
        yield pr.start_text(player, dummy, "Practice Strike", False)
        yield pr.start_text(player, dummy, "Practice Strike", True, trainee=True)
        yield from pr.TIER_NOTES.values()

    def test_no_dashes_the_style_guide_bans(self):
        for text in self.texts():
            self.assertNotIn("—", text)
            self.assertNotIn(" -- ", text)

    def test_hidden_plan_does_not_leak_the_moves(self):
        battle, player, dummy = battle_with_dummy()
        plan = pr.plan_moves(battle, dummy, random.Random(0))
        text = pr.describe_plan(plan, dummy, show=False)
        for m in plan:
            self.assertNotIn(m.skill.name, text)

    def test_every_message_fits_in_one_discord_message(self):
        for text in self.texts():
            self.assertLess(len(text), 1800)

    def test_the_start_text_names_the_real_commands(self):
        battle, player, dummy = battle_with_dummy()
        text = pr.start_text(player, dummy, None, True)
        self.assertIn("/battle declare fighter:Rook slot:1 skill_name:Jab target:Dummy target_slot:1", text)
        self.assertIn("/battle combat", text)
        self.assertIn("/practice stop", text)


# ---- Discord glue ----------------------------------------------------------

def fake_interaction(user_id=USER, channel_id=CHANNEL, mention_name=None):
    i = MagicMock()
    i.user.id = user_id
    i.user.bot = False
    i.channel_id = channel_id
    i.channel = MagicMock()
    i.channel.send = AsyncMock()
    i.response.send_message = AsyncMock()
    i.followup.send = AsyncMock()
    i.original_response = AsyncMock(return_value=MagicMock(id=555))
    return i


class InTempDir(unittest.TestCase):
    def setUp(self):
        self._old = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old)
        self._tmp.cleanup()


class Glue(InTempDir):
    def setUp(self):
        super().setUp()
        self.bot = MagicMock()
        self.battle_cog = cb.BattleCog(self.bot)
        self.bot.get_cog = lambda name: self.battle_cog if name == "battle" else None
        chan = MagicMock()
        chan.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        self.bot.get_channel = lambda channel_id: chan
        self.cog = practice_cog.PracticeCog(self.bot)

    def run_start(self, user_id=USER, **kw):
        i = fake_interaction(user_id)
        args = dict(character=None, tier="fodder", show_plan=True)
        args.update(kw)
        asyncio.run(self.cog.start.callback(self.cog, i, **args))
        return i

    def reply(self, i):
        """The first thing a command said back, as text (an embed counts as 'embed')."""
        args, kwargs = i.response.send_message.call_args
        return args[0] if args else "embed"

    def make_character(self, name, owner=USER, skills=True):
        c = Character(owner_id=owner, name=name)
        if skills:
            c.add_skill(Skill("Jab", 4, 2, 2))
        save_character(c)

    def test_with_no_character_you_fight_as_a_trainee(self):
        i = self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        player = battle.get_fighter("Trainee")
        self.assertEqual((player.side, player.owner_id), ("A", USER))
        self.assertTrue(player.skills)
        dummy = battle.get_fighter("Dummy")
        self.assertEqual((dummy.side, dummy.owner_id), ("B", None))
        self.assertTrue(dummy.has_declared(), "the Dummy should have picked before the player")
        self.assertEqual(len(dummy.declared_actions), 1, "the easy Dummy attacks once a round")
        self.assertEqual(battle.message_id, 555)
        self.assertEqual(self.reply(i), "embed")
        guide = i.followup.send.call_args
        self.assertTrue(guide.kwargs["ephemeral"])
        self.assertIn("Trainee", guide.args[0])
        self.assertIn("/character create", guide.args[0])
        i.channel.send.assert_awaited_once()
        self.assertIn("Dummy is going to do this", i.channel.send.call_args.args[0])

    def test_the_public_message_can_never_ping(self):
        self.make_character("@everyone")
        i = self.run_start()
        mentions = i.channel.send.call_args.kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)

    def test_your_only_character_is_used(self):
        self.make_character("Rook")
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        self.assertIsNotNone(battle.get_fighter("Rook"))
        self.assertIsNone(battle.get_fighter("Trainee"))

    def test_a_character_without_skills_is_lent_one_for_this_fight_only(self):
        self.make_character("Rook", skills=False)
        i = self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        self.assertIn("practice strike", battle.get_fighter("Rook").skills)
        self.assertIn("lent", i.followup.send.call_args.args[0])
        from game.character import load_character
        self.assertEqual(load_character("Rook").skills, {})

    def test_several_characters_means_you_must_pick(self):
        self.make_character("Rook")
        self.make_character("Bishop")
        i = self.run_start()
        self.assertIn("several characters", self.reply(i))
        self.assertNotIn(CHANNEL, self.battle_cog.battles)
        self.run_start(character="bishop")
        self.assertIsNotNone(self.battle_cog.battles[CHANNEL].get_fighter("Bishop"))

    def test_someone_elses_or_an_unknown_character_is_refused(self):
        self.make_character("Rook", owner=99)
        self.assertIn("belongs to someone else", self.reply(self.run_start(character="Rook")))
        self.assertIn("No character named", self.reply(self.run_start(character="Ghost")))
        self.assertNotIn(CHANNEL, self.battle_cog.battles)

    def test_a_second_start_while_one_is_running_is_refused(self):
        self.run_start()
        first = self.battle_cog.battles[CHANNEL]
        i = self.run_start()
        self.assertIn("already running", self.reply(i))
        self.assertIs(self.battle_cog.battles[CHANNEL], first)

    def test_a_normal_battle_in_the_channel_is_left_alone(self):
        normal = Battle(channel_id=CHANNEL)
        self.battle_cog.battles[CHANNEL] = normal
        i = self.run_start()
        self.assertIn("already a battle", self.reply(i))
        self.assertIs(self.battle_cog.battles[CHANNEL], normal)
        self.assertNotIn(CHANNEL, self.cog.sessions)

    def test_a_character_called_dummy_does_not_clash_with_the_dummy(self):
        self.make_character("Dummy")
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        self.assertEqual({f.name for f in battle.fighters}, {"Dummy", "Practice Dummy"})

    def test_the_tougher_dummy_has_more_of_everything(self):
        self.run_start(tier="elite")
        dummy = self.battle_cog.battles[CHANNEL].get_fighter("Dummy")
        self.assertEqual((dummy.max_hp, dummy.skill_slots), (150, 3))
        self.assertEqual(len(dummy.declared_actions), 3)

    def test_hiding_the_plan_posts_no_moves(self):
        i = self.run_start(show_plan=False)
        text = i.channel.send.call_args.args[0]
        self.assertIn("keeping them to itself", text)
        self.assertNotIn("Wild Swing", text)

    # ---- stopping

    def test_stop_clears_the_channel(self):
        self.run_start()
        i = fake_interaction()
        asyncio.run(self.cog.stop.callback(self.cog, i))
        self.assertNotIn(CHANNEL, self.battle_cog.battles)
        self.assertNotIn(CHANNEL, self.cog.sessions)
        self.assertIn("Practice over", self.reply(i))

    def test_a_stranger_cannot_stop_your_practice(self):
        self.run_start()
        i = fake_interaction(user_id=99)
        i.user = MagicMock(id=99, spec=discord.User)
        asyncio.run(self.cog.stop.callback(self.cog, i))
        self.assertIn("Only whoever started", self.reply(i))
        self.assertIn(CHANNEL, self.battle_cog.battles)

    def test_stop_with_nothing_running(self):
        i = fake_interaction()
        asyncio.run(self.cog.stop.callback(self.cog, i))
        self.assertIn("no practice fight", self.reply(i))
        self.battle_cog.battles[CHANNEL] = Battle(channel_id=CHANNEL)
        i = fake_interaction()
        asyncio.run(self.cog.stop.callback(self.cog, i))
        self.assertIn("/battle end", self.reply(i))

    def test_battle_end_drops_the_session(self):
        self.run_start()
        del self.battle_cog.battles[CHANNEL]
        self.assertIsNone(self.cog.session_for(CHANNEL))
        self.assertNotIn(CHANNEL, self.cog.sessions)
        self.run_start()          # a fresh one starts fine
        self.assertIn(CHANNEL, self.cog.sessions)

    # ---- between rounds

    def finish_round(self, battle):
        """What the end of /battle combat leaves behind: moves cleared, round number up."""
        battle.history.append(object())
        battle.start_new_round()

    def check(self, i=None):
        i = i or fake_interaction()
        asyncio.run(self.cog._check(CHANNEL, i.channel))
        return i

    def test_nothing_happens_while_the_round_is_still_the_same(self):
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        before = dict(battle.get_fighter("Dummy").declared_actions)
        i = self.check()
        i.channel.send.assert_not_awaited()
        self.assertEqual(battle.get_fighter("Dummy").declared_actions, before)

    def test_a_new_round_gets_a_new_plan(self):
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        self.finish_round(battle)
        dummy = battle.get_fighter("Dummy")
        self.assertFalse(dummy.has_declared())
        i = self.check()
        self.assertTrue(dummy.has_declared())
        i.channel.send.assert_awaited_once()
        self.assertIn("Dummy is going to do this", i.channel.send.call_args.args[0])
        # a second look in the same round does not plan or post again
        i2 = self.check()
        i2.channel.send.assert_not_awaited()

    def test_each_new_plan_keeps_to_the_tiers_move_count(self):
        for tier, count in (("fodder", 1), ("elite", 3)):
            self.cog.sessions.clear()
            self.battle_cog.battles.clear()
            self.run_start(tier=tier)
            battle = self.battle_cog.battles[CHANNEL]
            for _ in range(3):
                self.finish_round(battle)
                self.check()
                self.assertEqual(len(battle.get_fighter("Dummy").declared_actions), count)

    def test_the_battle_message_is_refreshed_after_the_new_plan(self):
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        self.finish_round(battle)
        chan = self.bot.get_channel(CHANNEL)
        chan.fetch_message.reset_mock()
        self.check()
        chan.fetch_message.assert_awaited()

    def test_a_staggered_dummy_sits_the_round_out(self):
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        dummy = battle.get_fighter("Dummy")
        self.finish_round(battle)
        dummy.current_stagger_tier = 1
        i = self.check()
        self.assertFalse(dummy.has_declared())
        self.assertIn("can't act", i.channel.send.call_args.args[0])
        # combat does not wait on it, so the player only has to declare
        player = battle.get_fighter("Trainee")
        player.declare_in_slot(1, next(iter(player.skills.values())), dummy, 1)
        self.assertTrue(battle.all_declared())

    def test_the_fight_ends_once_and_a_new_one_can_start(self):
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        battle.get_fighter("Dummy").hp = 0
        self.finish_round(battle)
        i = self.check()
        text = i.channel.send.call_args.args[0]
        self.assertIn("You win", text)
        self.assertIn("after 1 round.", text)
        self.assertTrue(self.cog.sessions[CHANNEL].finished)
        self.assertFalse(self.check().channel.send.await_count, "the result must be announced once")
        self.run_start()
        fresh = self.battle_cog.battles[CHANNEL]
        self.assertIsNot(fresh, battle)
        self.assertFalse(self.cog.sessions[CHANNEL].finished)
        self.assertEqual(fresh.round_number, 1)

    def test_losing_is_announced_kindly(self):
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        battle.get_fighter("Trainee").hp = 0
        self.finish_round(battle)
        self.assertIn("knocked out", self.check().channel.send.call_args.args[0])

    def test_only_a_combat_command_or_a_locked_move_triggers_a_check(self):
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        self.finish_round(battle)
        i = fake_interaction()
        asyncio.run(self.cog.on_app_command_completion(i, SimpleNamespace(qualified_name="roll")))
        i.channel.send.assert_not_awaited()
        asyncio.run(self.cog.on_app_command_completion(i, SimpleNamespace(qualified_name="battle combat")))
        i.channel.send.assert_awaited_once()

    def test_a_locked_move_repairs_a_missing_plan(self):
        self.run_start()
        battle = self.battle_cog.battles[CHANNEL]
        battle.get_fighter("Dummy").declared_actions = {}
        i = fake_interaction()
        asyncio.run(self.cog.on_declare_locked(i, battle.get_fighter("Trainee")))
        self.assertTrue(battle.get_fighter("Dummy").has_declared())

    def test_a_failure_in_the_listener_never_breaks_the_command(self):
        self.run_start()
        self.finish_round(self.battle_cog.battles[CHANNEL])
        i = fake_interaction()
        i.channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "boom"))
        with self.assertLogs("cogs.practice", "WARNING"):
            asyncio.run(self.cog.on_app_command_completion(i, SimpleNamespace(qualified_name="battle combat")))
        self.cog._check = AsyncMock(side_effect=RuntimeError("bug"))
        with self.assertLogs("cogs.practice", "ERROR"):
            asyncio.run(self.cog.on_app_command_completion(i, SimpleNamespace(qualified_name="battle combat")))

    def test_the_listeners_ignore_channels_with_no_practice(self):
        i = fake_interaction(channel_id=4242)
        asyncio.run(self.cog.on_app_command_completion(i, SimpleNamespace(qualified_name="battle combat")))
        i.channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
