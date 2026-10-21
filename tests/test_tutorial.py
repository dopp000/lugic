"""Checks the /startingoff rules, the first-use tips, the saved progress and the Discord glue.

The full walkthrough through the real commands lives in tests/scenarios/tutorial_flow.py.
Run all tests from the repo root with:  python -m unittest discover tests
"""
import asyncio
import json
import os
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

import cogs.tutorial as tutorial_cog  # noqa: E402
from game import tutorial as tut  # noqa: E402

USER = 1
CHANNEL = 5


class FakeWorld(tut.World):
    def __init__(self, characters=(), battle=None):
        self._characters = list(characters)
        self._battle = battle

    def characters(self, user_id):
        return self._characters

    def battle(self, channel_id):
        return self._battle


def char(name="Rook", skills=False):
    return SimpleNamespace(name=name, skills={"quick jab": SimpleNamespace(name="Quick Jab")} if skills else {})


def cmd(command, /, **args):
    return tut.Event("command", USER, CHANNEL, command, args)


def started_progress(world, **kw):
    p = tut.Progress(**kw)
    tut.start(p, tut.Event("start", USER, CHANNEL), world)
    return p


class InTempDir(unittest.TestCase):
    def setUp(self):
        self._old = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old)
        self._tmp.cleanup()


class Steps(unittest.TestCase):
    def test_starting_with_a_character_skips_the_first_step(self):
        p = started_progress(FakeWorld([char()]))
        self.assertEqual(p.done, ["create"])
        self.assertEqual(tut.current_step(p).id, "view")

    def test_view_only_counts_for_your_own_character(self):
        world = FakeWorld([char()])
        p = started_progress(world)
        tut.handle_event(p, cmd("character view", name="Somebody Else"), world)
        self.assertEqual(tut.current_step(p).id, "view")
        update = tut.handle_event(p, cmd("character view", name="rook"), world)
        self.assertEqual([s.id for s in update.completed], ["view"])
        self.assertEqual(tut.current_step(p).id, "skill")

    def test_steps_only_finish_in_order(self):
        world = FakeWorld([char()])
        p = started_progress(world)
        update = tut.handle_event(p, cmd("character bp", name="Rook"), world)  # step 4 before step 2
        self.assertEqual(update.completed, [])
        self.assertEqual(tut.current_step(p).id, "view")

    def test_state_steps_cascade_through_what_is_already_true(self):
        world = FakeWorld([char(skills=True)])
        p = started_progress(world)
        update = tut.handle_event(p, cmd("character view", name="Rook"), world)
        self.assertEqual([s.id for s in update.completed], ["view", "skill"])
        self.assertEqual(tut.current_step(p).id, "bp")

    def test_skip_and_restart(self):
        world = FakeWorld([char()])
        p = started_progress(world)
        tut.skip(p, tut.Event("start", USER, CHANNEL), world)
        self.assertEqual(tut.current_step(p).id, "skill")
        tut.restart(p, tut.Event("start", USER, CHANNEL), world)
        self.assertEqual((p.done, p.skipped), (["create"], []))

    def test_skipping_everything_finishes(self):
        world = FakeWorld([char()])
        p = started_progress(world)
        for _ in range(len(tut.STEPS)):
            tut.skip(p, tut.Event("start", USER, CHANNEL), world)
        self.assertTrue(p.finished)
        self.assertIsNone(tut.current_step(p))

    def test_every_step_text_fills_in_cleanly(self):
        names = {"char": "Rook", "skill": "Quick Jab", "foe": "Dummy"}
        for step in tut.STEPS:
            for text in (step.instruction, step.done_text, step.title):
                rendered = tut.render(text, names)
                self.assertNotIn("{", rendered, step.id)
                self.assertNotIn("—", rendered)
                self.assertNotIn(" -- ", rendered)
            self.assertLess(len(tut.render(step.instruction, names)), 1000, step.id)
        self.assertEqual(len({s.id for s in tut.STEPS}), len(tut.STEPS))


class Tips(unittest.TestCase):
    def test_a_tip_shows_once(self):
        world = FakeWorld([char()])
        p = tut.Progress()
        first = tut.handle_event(p, cmd("character view", name="Rook"), world)
        second = tut.handle_event(p, cmd("character view", name="Rook"), world)
        self.assertIn("public:True", first.tip)
        self.assertIsNone(second.tip)

    def test_a_failed_first_try_does_not_use_up_the_tip(self):
        world = FakeWorld([])
        p = tut.Progress()
        tut.handle_event(p, cmd("character create", name="Rook"), world)  # nothing was created
        self.assertNotIn("character create", p.seen_tips)
        world._characters = [char()]
        update = tut.handle_event(p, cmd("character create", name="Rook"), world)
        self.assertIn("/request stats", update.tip)

    def test_tips_can_be_turned_off(self):
        world = FakeWorld([char()])
        p = tut.Progress(tips=False)
        update = tut.handle_event(p, cmd("character view", name="Rook"), world)
        self.assertIsNone(update.tip)
        self.assertFalse(update.pitch)

    def test_commands_without_a_tip_say_nothing(self):
        p = tut.Progress(pitched=True)
        update = tut.handle_event(p, cmd("character list"), FakeWorld())
        self.assertIsNone(tut.compose_message(update, p, FakeWorld(), cmd("character list")))

    def test_the_pointer_to_startingoff_shows_once_and_not_after_starting(self):
        world = FakeWorld()
        p = tut.Progress()
        first = tut.handle_event(p, cmd("character list"), world)
        again = tut.handle_event(p, cmd("character list"), world)
        self.assertTrue(first.pitch)
        self.assertFalse(again.pitch)
        started = tut.Progress(started=True)
        self.assertFalse(tut.handle_event(started, cmd("character list"), world).pitch)

    def test_every_tip_is_short_and_plain(self):
        for name, tip in tut.TIPS.items():
            self.assertLess(len(tip), 200, name)
            self.assertNotIn("—", tip)
            self.assertNotIn(" -- ", tip)


class Saving(InTempDir):
    def test_progress_survives_a_restart(self):
        store = tut.TutorialStore()
        p = store.get(USER)
        p.started, p.done, p.seen_tips = True, ["create"], ["roll"]
        store.save()
        again = tut.TutorialStore().get(USER)
        self.assertEqual((again.started, again.done, again.seen_tips), (True, ["create"], ["roll"]))
        self.assertFalse(os.path.exists(tut.TUTORIAL_PATH + ".tmp"))

    def test_a_broken_file_does_not_crash_the_bot(self):
        os.makedirs("data")
        with open(tut.TUTORIAL_PATH, "w") as f:
            f.write("{not json")
        store = tut.TutorialStore()
        self.assertFalse(store.get(USER).started)
        self.assertTrue(os.path.exists(tut.TUTORIAL_PATH + ".bad"))

    def test_unknown_fields_in_an_old_file_are_ignored(self):
        os.makedirs("data")
        with open(tut.TUTORIAL_PATH, "w") as f:
            json.dump({str(USER): {"started": True, "from_a_later_version": 1}}, f)
        self.assertTrue(tut.TutorialStore().get(USER).started)


def fake_interaction(**kw):
    i = MagicMock()
    i.user.id = USER
    i.user.bot = False
    i.channel_id = CHANNEL
    i.namespace = {}
    i.response.type = None
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.followup.send = AsyncMock()
    return i


class Glue(InTempDir):
    def make_cog(self, world=None):
        cog = tutorial_cog.TutorialCog(MagicMock())
        cog.world = world or FakeWorld([char()])
        return cog

    def test_a_modal_response_gets_no_followup(self):
        cog = self.make_cog()
        i = fake_interaction()
        i.response.type = discord.InteractionResponseType.modal
        asyncio.run(cog.on_app_command_completion(i, SimpleNamespace(qualified_name="character addskill")))
        i.followup.send.assert_not_awaited()

    def test_the_startingoff_command_itself_is_not_treated_as_a_step(self):
        cog = self.make_cog()
        i = fake_interaction()
        asyncio.run(cog.on_app_command_completion(i, SimpleNamespace(qualified_name="startingoff")))
        i.followup.send.assert_not_awaited()

    def test_a_failed_followup_never_breaks_the_command(self):
        cog = self.make_cog()
        i = fake_interaction()
        i.followup.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=404, reason="x"), "gone"))
        i.namespace = {"name": "Rook"}
        with self.assertLogs("cogs.tutorial", "ERROR"):
            asyncio.run(cog.on_app_command_completion(i, SimpleNamespace(qualified_name="character view")))

    def test_the_embed_fits_discord_at_every_step(self):
        cog = self.make_cog()
        p = cog.store.get(USER)
        tut.start(p, tut.Event("start", USER, CHANNEL), cog.world)
        for _ in range(len(tut.STEPS) + 1):
            embed = cog.build_embed(USER, CHANNEL, welcome=True)
            self.assertLess(len(embed), 6000)
            self.assertLessEqual(len(embed.title or ""), 256)
            tut.skip(p, tut.Event("start", USER, CHANNEL), cog.world)
        self.assertIn("That's the basics", cog.build_embed(USER, CHANNEL).description)

    def test_the_buttons_work_and_save(self):
        async def go():
            cog = self.make_cog()
            asyncio.get_running_loop()
            i = fake_interaction()
            await cog.startingoff.callback(cog, i)
            view = i.response.send_message.call_args.kwargs["view"]
            p = cog.store.get(USER)
            self.assertEqual(tut.current_step(p).id, "view")
            await view.skip_button.callback(fake_interaction())
            self.assertEqual(tut.current_step(p).id, "skill")
            await view.tips_button.callback(fake_interaction())
            self.assertFalse(p.tips)
            self.assertEqual(view.tips_button.label, "Tips: off")
            await view.restart_button.callback(fake_interaction())
            self.assertEqual(tut.current_step(p).id, "view")
            self.assertTrue(tut.TutorialStore().get(USER).started)
            stranger = fake_interaction()
            stranger.user.id = 99
            self.assertFalse(await view.interaction_check(stranger))
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
