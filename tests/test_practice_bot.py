"""Checks the random practice bot: the generator (game/practice_bot.py) and /practice bot with its setup commands (cogs/practice.py).

The whole fight through the real /battle commands lives in tests/scenarios/practice_bot_flow.py.
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
from game import practice_bot as pb  # noqa: E402
from game.build_points import (  # noqa: E402
    NONKEYWORD_CAP, STARTING_BP, character_level, check_build, max_base_power, max_coin_power,
    passive_points_spent, skill_points_spent, skill_rank_problems,
)
from game.character import Character, load_character, save_character  # noqa: E402
from game.passives import MAX_PASSIVES, parse_passive  # noqa: E402
from game.skills import Skill  # noqa: E402

USER = 7
OTHER = 8
CHANNEL = 100


def banned_dashes(text):
    return "—" in text or " -- " in text


class Generator(unittest.TestCase):
    def bots(self, skills=True, passives=True):
        for level in range(1, 6):
            for seed in range(12):
                yield level, seed, pb.generate_bot(
                    pb.bp_for_level(level), 6, 5, rng=random.Random(seed), skills=skills, passives=passives,
                )

    def test_levels_map_to_the_smallest_pool_that_reaches_them(self):
        self.assertEqual([pb.bp_for_level(n) for n in range(1, 6)], [30, 45, 63, 84, 108])
        self.assertEqual(pb.bp_for_level(0), 30)
        self.assertEqual(pb.bp_for_level(9), 108)

    def test_every_bot_is_a_legal_build_at_its_level(self):
        for level, seed, c in self.bots():
            report = check_build(c)
            self.assertTrue(report.ok, (level, seed, report.problems))
            self.assertEqual(character_level(c), level, (level, seed))
            self.assertEqual(report.total, pb.bp_for_level(level))

    def test_skill_and_passive_points_stay_inside_the_pools(self):
        for level, seed, c in self.bots():
            self.assertLessEqual(skill_points_spent(c), c.sp_total, (level, seed))
            self.assertLessEqual(passive_points_spent(c), c.pp_total, (level, seed))
            self.assertLessEqual(len(c.passives), MAX_PASSIVES)

    def test_with_no_skill_points_no_skill_has_extra_trigger_lines(self):
        for level in range(1, 6):
            for seed in range(8):
                c = pb.generate_bot(pb.bp_for_level(level), 0, 0, rng=random.Random(seed))
                self.assertEqual(skill_points_spent(c), 0)
                self.assertFalse(c.passives)
                for s in c.skills.values():
                    self.assertLessEqual(len(s.triggers), 1, s.name)

    def test_skills_fit_the_bots_power_ranks_and_the_skill_form(self):
        for level, seed, c in self.bots():
            self.assertTrue(c.skills, (level, seed))
            for s in c.skills.values():
                self.assertLessEqual(s.base_power, max_base_power(c.power), (level, seed, s.name))
                self.assertLessEqual(s.coin_power, max_coin_power(c.power), (level, seed, s.name))
                self.assertFalse(skill_rank_problems(c.status_ranks, s), (level, seed, s.name))
                self.assertIn(s.coins, (1, 2, 3, 4))
                self.assertNotIn("supermove", s.tags)

    def test_skill_names_are_unique_and_every_rank_is_used(self):
        for level, seed, c in self.bots():
            names = [s.name.lower() for s in c.skills.values()]
            self.assertEqual(len(names), len(set(names)))
            used = {n for s in c.skills.values() for n in s.coin_statuses if n}
            for status, rank in c.status_ranks.items():
                if rank:
                    self.assertIn(status, used, (level, seed, status))

    def test_the_bot_only_buys_what_its_ai_can_play(self):
        for level, seed, c in self.bots():
            self.assertFalse(c.defense_archetypes, (level, seed))

    def test_passives_parse_and_have_unique_names(self):
        for level, seed, c in self.bots():
            names = [p["name"].lower() for p in c.passives]
            self.assertEqual(len(names), len(set(names)))
            for p in c.passives:
                self.assertIsNone(parse_passive(p["lines"])[1], p)

    def test_a_manual_bot_has_no_skills_or_passives_and_keeps_points_back(self):
        for level, seed, c in self.bots(skills=False, passives=False):
            self.assertFalse(c.skills)
            self.assertFalse(c.passives)
            report = check_build(c)
            self.assertTrue(report.ok, (level, seed, report.problems))
            self.assertLessEqual(report.spent, report.total - NONKEYWORD_CAP, (level, seed))

    def test_skills_only_and_passives_only_work_on_their_own(self):
        c = pb.generate_bot(63, 4, 4, rng=random.Random(1), skills=True, passives=False)
        self.assertTrue(c.skills and not c.passives)
        c = pb.generate_bot(63, 4, 4, rng=random.Random(1), skills=False, passives=True)
        self.assertTrue(c.passives and not c.skills)

    def test_the_same_seed_gives_the_same_bot(self):
        a = pb.generate_bot(84, 5, 4, rng=random.Random(3))
        b = pb.generate_bot(84, 5, 4, rng=random.Random(3))
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_different_seeds_give_different_bots(self):
        seen = {str(pb.generate_bot(63, 4, 3, rng=random.Random(s)).to_dict()) for s in range(10)}
        self.assertGreater(len(seen), 5)

    def test_name_and_owner_are_passed_through(self):
        c = pb.generate_bot(45, name="Practice Bot", owner_id=USER, rng=random.Random(0))
        self.assertEqual((c.name, c.owner_id), ("Practice Bot", USER))

    def test_points_of_reads_a_character_and_defaults_for_a_new_one(self):
        c = Character(owner_id=USER, name="X", bp_total=63, sp_total=3, pp_total=2)
        self.assertEqual(pb.points_of(c), (63, 3, 2))
        self.assertEqual(pb.points_of(Character(owner_id=USER, name="Y")), (STARTING_BP, 0, 0))

    def test_the_summary_text(self):
        for level, seed, c in self.bots():
            text = pb.bot_text(c)
            self.assertFalse(banned_dashes(text), text)
            self.assertLess(len(text), 1500, (level, seed, len(text)))
            self.assertIn(f"Level {level}", text)
            self.assertIn("Skills:", text)
            for s in c.skills.values():
                self.assertIn(s.name, text)

    def test_unspent_points_are_reported_instead_of_hidden(self):
        found = False
        for level, seed, c in self.bots():
            report = check_build(c)
            left = report.total - report.spent
            text = pb.bot_text(c)
            if left > pb.UNSPENT_NOTE_AT:
                found = True
                self.assertIn(f"{left} Build Points are unspent", text)
            else:
                self.assertNotIn("unspent", text)
        self.assertTrue(found, "no sample had unspent points, so the note was never checked")

    def test_a_manual_bot_summary_says_why_points_are_kept(self):
        c = pb.generate_bot(63, 3, 2, rng=random.Random(0), skills=False, passives=False)
        text = pb.bot_text(c)
        self.assertIn("Skills: none yet.", text)
        self.assertIn("Passives: none yet.", text)
        self.assertIn("Effects on the skills you add can cost some", text)


class Words(unittest.TestCase):
    def test_the_bot_start_text_and_end_text(self):
        c = pb.generate_bot(63, 3, 2, rng=random.Random(0))
        foe = pr.build_dummy("fodder", "Bot")
        player = pr.build_trainee(USER)
        for manual in (False, True):
            for trainee in (False, True):
                text = pr.bot_start_text(player, foe, pb.bot_text(c), "Practice Strike", True, trainee, manual)
                self.assertFalse(banned_dashes(text))
                self.assertLess(len(text), 2000)
                self.assertIn("/battle combat", text)
                self.assertIn("/practice stop", text)
        for result in ("won", "lost", "draw"):
            text = pr.end_text(result, 3, "Bot")
            self.assertFalse(banned_dashes(text))
            self.assertIn("/practice bot", text)
        self.assertIn("Bot went down after 3 rounds", pr.end_text("won", 3, "Bot"))

    def test_the_manual_message_points_at_the_setup_commands(self):
        c = pb.generate_bot(30, 0, 0, rng=random.Random(0), skills=False, passives=False)
        foe = pr.build_dummy("fodder", "Bot")
        text = pr.bot_start_text(pr.build_trainee(USER), foe, pb.bot_text(c), None, True, True, True)
        self.assertIn("/practice addskill", text)
        self.assertIn("/practice addpassive", text)
        self.assertIn("won't run until it has a skill", text)
        random_text = pr.bot_start_text(pr.build_trainee(USER), foe, pb.bot_text(c), None, True, True, False)
        self.assertIn("/practice sheet", random_text)
        self.assertNotIn("/practice addskill", random_text)


# ---- Discord glue ----------------------------------------------------------

def fake_interaction(user_id=USER, channel_id=CHANNEL, admin=False):
    i = MagicMock()
    if admin:
        i.user = MagicMock(spec=discord.Member)
        i.user.roles = [SimpleNamespace(id=cb.ADMIN_ROLE_ID)]
    i.user.id = user_id
    i.user.bot = False
    i.channel_id = channel_id
    i.channel = MagicMock()
    i.channel.send = AsyncMock()
    i.response.send_message = AsyncMock()
    i.response.send_modal = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.followup.send = AsyncMock()
    i.original_response = AsyncMock(return_value=MagicMock(id=555))
    i.client = MagicMock()
    i.client.get_channel = MagicMock(return_value=None)
    return i


def said(i):
    """The first thing a command answered, as text ('embed' for an embed)."""
    args, kwargs = i.response.send_message.call_args
    return args[0] if args else "embed"


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

    def run_bot(self, user_id=USER, **kw):
        i = fake_interaction(user_id)
        args = dict(
            character=None, level=None, skill_points=None, passive_points=None, skills="random", passives="random",
            attacks=None, show_plan=True,
        )
        args.update(kw)
        asyncio.run(self.cog.versus.callback(self.cog, i, **args))
        return i

    def make_character(self, name, owner=USER, bp=STARTING_BP, sp=0, pp=0, skills=True):
        c = Character(owner_id=owner, name=name, bp_total=bp, sp_total=sp, pp_total=pp)
        if skills:
            c.add_skill(Skill("Jab", 4, 2, 2))
        save_character(c)
        return c

    def session(self):
        return self.cog.sessions[CHANNEL]

    def battle(self):
        return self.battle_cog.battles[CHANNEL]

    def foe(self):
        return self.battle().get_fighter(self.session().foe_name)

    def call(self, command, user_id=USER, admin=False, **kw):
        i = fake_interaction(user_id, admin=admin)
        asyncio.run(command.callback(self.cog, i, **kw))
        return i

    # ---- starting

    def test_with_no_character_the_bot_is_a_level_1_match(self):
        i = self.run_bot()
        char = self.session().bot_char
        self.assertEqual(pb.points_of(char), (30, 0, 0))
        self.assertEqual(char.name, "Bot")
        self.assertEqual(self.foe().side, "B")
        self.assertIsNotNone(self.battle().get_fighter("Trainee"))
        self.assertEqual(said(i), "embed")
        self.assertTrue(i.followup.send.call_args.kwargs["ephemeral"])
        self.assertIn("Level 1", i.followup.send.call_args.args[0])
        self.assertEqual(self.battle().message_id, 555)

    def test_the_bot_copies_the_points_of_the_chosen_character(self):
        self.make_character("Rook", bp=63, sp=3, pp=2)
        self.run_bot()
        self.assertEqual(pb.points_of(self.session().bot_char), (63, 3, 2))
        self.assertEqual(self.foe().level, 3)

    def test_the_options_override_the_copied_points(self):
        self.make_character("Rook", bp=63, sp=3, pp=2)
        self.run_bot(level=5, skill_points=1, passive_points=4)
        self.assertEqual(pb.points_of(self.session().bot_char), (108, 1, 4))
        self.assertEqual(self.foe().level, 5)

    def test_zero_points_are_a_real_choice_not_a_blank(self):
        self.make_character("Rook", bp=63, sp=3, pp=2)
        self.run_bot(skill_points=0, passive_points=0)
        char = self.session().bot_char
        self.assertEqual((char.sp_total, char.pp_total), (0, 0))
        self.assertFalse(char.passives)

    def test_nobody_owns_the_bot_and_it_has_already_picked(self):
        self.run_bot()
        foe = self.foe()
        self.assertIsNone(foe.owner_id)
        self.assertTrue(foe.has_declared())
        self.assertTrue(foe.skills)

    def test_the_bot_is_never_saved_as_a_character(self):
        self.run_bot()
        self.assertIsNone(load_character("Bot"))

    def test_the_fighter_carries_the_characters_skills_and_passives(self):
        self.make_character("Rook", bp=63, sp=4, pp=3)
        self.run_bot()
        char, foe = self.session().bot_char, self.foe()
        self.assertEqual(set(foe.skills), set(char.skills))
        self.assertEqual(len(foe.passive_skills), len(char.passives))
        self.assertEqual(foe.max_hp, char.max_hp)
        self.assertEqual(foe.power, char.power)

    def test_a_character_called_bot_does_not_clash_with_the_bot(self):
        self.make_character("Bot")
        self.run_bot()
        self.assertEqual(self.session().foe_name, "Practice Bot")
        self.assertIsNotNone(self.battle().get_fighter("Bot"))
        self.assertIsNotNone(self.battle().get_fighter("Practice Bot"))

    def test_attacks_limits_how_many_moves_it_makes(self):
        self.run_bot(level=4, skill_points=4, attacks=1)
        self.assertEqual(len(self.foe().declared_actions), 1)
        self.assertEqual(self.session().max_moves, 1)

    def test_by_default_it_uses_every_slot(self):
        self.run_bot(level=4, skill_points=4)
        foe = self.foe()
        self.assertEqual(len(foe.declared_actions), foe.skill_slots)

    def test_the_public_message_names_the_bots_moves_and_cannot_ping(self):
        self.make_character("@everyone")
        i = self.run_bot()
        text = i.channel.send.call_args.args[0]
        self.assertIn("Bot is going to do this", text)
        mentions = i.channel.send.call_args.kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone or mentions.users or mentions.roles)

    def test_hiding_the_plan_posts_no_moves(self):
        i = self.run_bot(show_plan=False)
        text = i.channel.send.call_args.args[0]
        for s in self.foe().skills.values():
            self.assertNotIn(s.name, text)

    def test_an_unknown_or_foreign_character_is_refused(self):
        self.make_character("Theirs", owner=OTHER)
        self.assertIn("belongs to someone else", said(self.run_bot(character="Theirs")))
        self.assertIn("No character named", said(self.run_bot(character="Nobody")))
        self.assertNotIn(CHANNEL, self.battle_cog.battles)

    def test_a_second_fight_is_refused_while_one_runs(self):
        self.run_bot()
        first = self.battle()
        self.assertIn("already running", said(self.run_bot()))
        self.assertIs(self.battle(), first)

    def test_a_normal_battle_is_left_alone(self):
        self.battle_cog.battles[CHANNEL] = object()
        self.assertIn("already a battle", said(self.run_bot()))

    def test_a_dummy_fight_and_a_bot_fight_replace_each_other_once_finished(self):
        self.run_bot()
        self.session().finished = True
        i = fake_interaction()
        asyncio.run(self.cog.start.callback(self.cog, i, character=None, tier="fodder", show_plan=True))
        self.assertIsNone(self.session().bot_char)
        self.session().finished = True
        self.run_bot()
        self.assertIsNotNone(self.session().bot_char)

    # ---- setting it up by hand

    def manual(self, **kw):
        kw.setdefault("skills", "manual")
        kw.setdefault("passives", "manual")
        return self.run_bot(**kw)

    def test_a_manual_bot_has_nothing_and_the_player_is_told_once_per_round(self):
        i = self.manual(level=2, skill_points=2, passive_points=2)
        char = self.session().bot_char
        self.assertFalse(char.skills or char.passives)
        self.assertFalse(self.foe().skills)
        self.assertIn("no skills yet", i.channel.send.call_args.args[0])
        self.assertIn("/practice addskill", i.followup.send.call_args.args[0])
        again = fake_interaction()
        asyncio.run(self.cog._check(CHANNEL, again.channel))
        again.channel.send.assert_not_awaited()

    def test_it_says_so_again_in_a_new_round(self):
        self.manual()
        battle = self.battle()
        battle.history.append(object())
        battle.start_new_round()
        i = fake_interaction()
        asyncio.run(self.cog._check(CHANNEL, i.channel))
        i.channel.send.assert_awaited_once()
        self.assertIn("no skills yet", i.channel.send.call_args.args[0])

    def fill_skill_form(self, values):
        i = fake_interaction()
        asyncio.run(self.cog.addskill.callback(self.cog, i))
        modal = i.response.send_modal.call_args.args[0]
        modal.skill_name._value, modal.stats_input._value, modal.status_input._value = values
        modal.attack_weight_input._value = ""
        modal.triggers_input._value = ""
        return modal

    def submit_skill(self, values, admin=False):
        """Fills in the real skill form and presses Confirm. Returns (the form's reply interaction, the confirm interaction or None)."""
        modal = self.fill_skill_form(values)
        i = fake_interaction(admin=admin)
        asyncio.run(modal.on_submit(i))
        view = i.response.send_message.call_args.kwargs.get("view")
        if view is None:
            return i, None
        i2 = fake_interaction()
        asyncio.run(view.confirm.callback(i2))
        asyncio.run(self.cog.on_skill_saved(i2, self.session().bot_char))
        return i, i2

    def test_adding_a_skill_gives_the_bot_a_move(self):
        self.manual()
        _, done = self.submit_skill(("Chop", "4, 2, 2, Slash", "none"))
        self.assertIsNotNone(done)
        self.assertIn("chop", self.session().bot_char.skills)
        self.assertIn("chop", self.foe().skills)
        self.assertTrue(self.foe().has_declared())
        done.channel.send.assert_awaited()
        self.assertIn("Chop", done.channel.send.call_args.args[0])

    def test_nothing_the_bot_learns_is_written_to_disk(self):
        self.manual(passive_points=2)
        self.submit_skill(("Chop", "4, 2, 2, Slash", "none"))
        self.passive("Thick Skin", 1)
        self.assertIsNone(load_character("Bot"))
        self.assertFalse(os.path.exists(os.path.join("data", "characters")) and os.listdir(os.path.join("data", "characters")))

    def test_a_practice_skill_never_reaches_the_admin_log(self):
        self.manual()
        _, done = self.submit_skill(("Chop", "4, 2, 2, Slash", "none"))
        done.client.get_channel.assert_not_called()

    def test_a_skill_above_the_bots_power_is_refused_with_a_practice_hint(self):
        self.manual()
        i, done = self.submit_skill(("Giant Blow", "12, 5, 4, Blunt", "none"))
        self.assertIsNone(done)
        self.assertNotIn("giant blow", self.session().bot_char.skills)
        self.assertNotIn("/charadmin", said(i))

    def test_a_skill_needing_a_rank_the_bot_lacks_is_refused_without_admin_talk(self):
        self.manual()
        char = self.session().bot_char
        char.status_ranks = {}
        i, done = self.submit_skill(("Torch", "4, 2, 2, Pierce", "Burn:1:1"))
        self.assertIsNone(done)
        self.assertNotIn("torch", char.skills)
        self.assertNotIn("/charadmin", said(i))

    def test_extra_trigger_lines_cost_the_bots_skill_points(self):
        self.manual(skill_points=0)
        modal = self.fill_skill_form(("Twin", "4, 2, 2, Slash", "none"))
        modal.triggers_input._value = "[On Use] Coin Power +1\n[On Use] Deal +10% damage"
        i = fake_interaction()
        asyncio.run(modal.on_submit(i))
        self.assertIsNone(i.response.send_message.call_args.kwargs.get("view"))
        self.assertIn("skill_points", said(i))
        self.assertNotIn("/charadmin", said(i))

    def test_extra_trigger_lines_work_with_skill_points(self):
        self.manual(skill_points=2)
        modal = self.fill_skill_form(("Twin", "4, 2, 2, Slash", "none"))
        modal.triggers_input._value = "[On Use] Coin Power +1\n[On Use] Deal +10% damage"
        i = fake_interaction()
        asyncio.run(modal.on_submit(i))
        self.assertIsNotNone(i.response.send_message.call_args.kwargs.get("view"))

    def test_the_skill_form_cannot_be_opened_without_a_bot(self):
        i = self.call(self.cog.addskill)
        self.assertIn("no practice bot", said(i))
        i.response.send_modal.assert_not_called()
        self.call(self.cog.start, character=None, tier="fodder", show_plan=True)
        i = self.call(self.cog.addskill)
        self.assertIn("no practice bot", said(i), "a Dummy fight has no bot to teach")

    def test_only_the_player_or_an_admin_can_change_the_bot(self):
        self.manual()
        for command, kw in (
            (self.cog.addskill, {}),
            (self.cog.removeskill, {"skill_name": "x"}),
            (self.cog.removepassive, {"passive_name": "x"}),
            (self.cog.addpassive, dict(
                passive_name="X", kind=SimpleNamespace(value="passive", name="Passive"), cost=1,
                line_1="[Turn Start] Gain 3 Sanity", line_2=None, line_3=None,
            )),
        ):
            i = self.call(command, user_id=OTHER, **kw)
            self.assertIn("Only whoever started", said(i), command.name)
        i = self.call(self.cog.addskill, user_id=OTHER, admin=True)
        i.response.send_modal.assert_awaited_once()

    def test_anyone_can_look_at_the_sheet(self):
        self.run_bot()
        i = self.call(self.cog.sheet, user_id=OTHER)
        self.assertEqual(said(i), "embed")
        self.assertTrue(i.response.send_message.call_args.kwargs["ephemeral"])

    def test_remove_skill_updates_the_fighter_and_replans(self):
        self.manual()
        self.submit_skill(("Chop", "4, 2, 2, Slash", "none"))
        self.submit_skill(("Stab", "4, 2, 2, Pierce", "none"))
        foe = self.foe()
        self.assertTrue(foe.has_declared())
        i = self.call(self.cog.removeskill, skill_name="Chop")
        self.assertIn("forgot Chop", said(i))
        self.assertNotIn("chop", foe.skills)
        self.assertTrue(foe.has_declared())
        for action in foe.declared_actions.values():
            self.assertEqual(action.skill.name, "Stab")

    def test_removing_the_last_skill_leaves_nothing_declared_and_says_so(self):
        self.manual()
        self.submit_skill(("Chop", "4, 2, 2, Slash", "none"))
        i = self.call(self.cog.removeskill, skill_name="chop")
        self.assertFalse(self.foe().skills)
        self.assertFalse(self.foe().has_declared())
        self.assertIn("no skills now", said(i))
        self.assertIn("/practice addskill", said(i))

    def test_removing_an_unknown_skill_says_so(self):
        self.manual()
        self.assertIn("doesn't know a skill called Nope", said(self.call(self.cog.removeskill, skill_name="Nope")))

    def passive(self, name="Thick Skin", cost=1, line="[Combat Start] Gain 10 Shield", **kw):
        return self.call(
            self.cog.addpassive, passive_name=name, kind=SimpleNamespace(value="passive", name="Passive"), cost=cost,
            line_1=line, line_2=None, line_3=None, **kw,
        )

    def test_a_passive_is_paid_with_the_bots_passive_points(self):
        self.manual(passive_points=3)
        i = self.passive("Thick Skin", 2)
        self.assertIn("2/3 PP used", said(i))
        self.assertEqual([p["name"] for p in self.session().bot_char.passives], ["Thick Skin"])
        self.assertEqual(len(self.foe().passive_skills), 1)
        too_much = self.passive("Heavy", 2, "[Turn Start] Gain 3 Sanity")
        self.assertIn("has 3 Passive Points and this would use 4", said(too_much))
        self.assertEqual(len(self.session().bot_char.passives), 1)

    def test_a_passive_that_costs_too_much_is_refused(self):
        self.manual(passive_points=1)
        i = self.passive("Thick Skin", 2)
        self.assertIn("has 1 Passive Points and this would use 2", said(i))
        self.assertFalse(self.session().bot_char.passives)
        self.assertFalse(self.foe().passive_skills)

    def test_a_passive_that_does_not_parse_is_refused(self):
        self.manual(passive_points=3)
        i = self.passive("Nonsense", 1, "[On Use] Make tea")
        self.assertFalse(self.session().bot_char.passives)
        self.assertNotEqual(said(i), "embed")

    def test_duplicate_names_and_the_passive_limit(self):
        self.manual(level=3, passive_points=12)
        self.passive("A", 1, "[Turn Start] Gain 3 Sanity")
        self.assertIn("already has a passive called a", said(self.passive("a", 1)))
        self.passive("B", 1, "[Combat Start] Gain 10 Shield")
        self.passive("C", 1, "[Turn Start] Gain 4 Shield")
        self.assertEqual(len(self.session().bot_char.passives), MAX_PASSIVES)
        self.assertIn(f"at most {MAX_PASSIVES}", said(self.passive("D", 1, "[Turn Start] Gain 5 Shield")))

    def test_removing_a_passive_frees_its_points(self):
        self.manual(passive_points=3)
        self.passive("Thick Skin", 3)
        i = self.call(self.cog.removepassive, passive_name="thick skin")
        self.assertIn("0/3 PP used", said(i))
        self.assertFalse(self.foe().passive_skills)
        self.assertIn("no passive called", said(self.call(self.cog.removepassive, passive_name="thick skin")))

    def test_the_bots_passives_and_skills_survive_the_battle_embed_refresh(self):
        self.manual(passive_points=3)
        self.passive("Thick Skin", 1)
        self.submit_skill(("Chop", "4, 2, 2, Slash", "none"))
        self.assertEqual(len(self.foe().passive_skills), 1)
        self.assertIn("chop", self.foe().skills)

    # ---- finishing

    def test_the_end_text_names_the_bot(self):
        self.run_bot()
        battle = self.battle()
        foe = self.foe()
        foe.hp = 0
        i = fake_interaction()
        asyncio.run(self.cog._check(CHANNEL, i.channel))
        text = i.channel.send.call_args.args[0]
        self.assertIn("Bot went down after", text)
        self.assertTrue(self.session().finished)

    def test_stop_clears_a_bot_fight_and_forgets_the_bot(self):
        self.run_bot()
        i = self.call(self.cog.stop)
        self.assertIn("Practice over", said(i))
        self.assertNotIn(CHANNEL, self.battle_cog.battles)
        self.assertNotIn(CHANNEL, self.cog.sessions)
        self.assertIn("no practice bot", said(self.call(self.cog.sheet)))

    def test_a_stranger_cannot_stop_it(self):
        self.run_bot()
        self.assertIn("Only whoever started", said(self.call(self.cog.stop, user_id=OTHER)))
        self.assertIn(CHANNEL, self.cog.sessions)

    def test_battle_end_drops_the_session_and_the_commands_stop_working(self):
        self.run_bot()
        del self.battle_cog.battles[CHANNEL]
        self.assertIn("no practice bot", said(self.call(self.cog.sheet)))

    def test_a_new_round_plans_again_within_the_move_limit(self):
        self.run_bot(level=4, skill_points=4, attacks=2)
        battle = self.battle()
        for _ in range(3):
            battle.history.append(object())
            battle.start_new_round()
            i = fake_interaction()
            asyncio.run(self.cog._check(CHANNEL, i.channel))
            self.assertEqual(len(self.foe().declared_actions), 2)
            i.channel.send.assert_awaited()

    def test_only_the_bots_own_skill_saves_trigger_a_check(self):
        self.manual()
        before = fake_interaction()
        asyncio.run(self.cog.on_skill_saved(before, Character(owner_id=USER, name="Someone")))
        before.channel.send.assert_not_awaited()

    def test_a_failing_listener_never_breaks_the_command(self):
        self.manual()

        async def boom(*a, **k):
            raise RuntimeError("boom")

        self.cog._check = boom
        i = fake_interaction()
        asyncio.run(self.cog.on_skill_saved(i, self.session().bot_char))

    def test_autocomplete_lists_only_this_bots_skills_and_passives(self):
        self.run_bot(level=3, skill_points=3, passive_points=3)
        char = self.session().bot_char
        i = fake_interaction()
        skills = asyncio.run(self.cog.removeskill_autocomplete(i, ""))
        self.assertEqual({c.value for c in skills}, {s.name for s in char.skills.values()})
        passives = asyncio.run(self.cog.removepassive_autocomplete(i, ""))
        self.assertEqual({c.value for c in passives}, {p["name"] for p in char.passives})
        none = asyncio.run(self.cog.removeskill_autocomplete(fake_interaction(channel_id=999), ""))
        self.assertEqual(none, [])


if __name__ == "__main__":
    unittest.main()
