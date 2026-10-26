"""The flavour text of a passive: kept in the code, read by the bot, saved on the passive and shown in italics on the character sheet.

Run all tests from the repo root with:  python -m unittest discover tests
"""
import asyncio
import os
import sys
import tempfile
import unittest
import warnings
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.simplefilter("ignore")

import discord  # noqa: E402

import cogs.build_requests as cb  # noqa: E402
import cogs.character as cc  # noqa: E402
import cogs.character_import as ci  # noqa: E402
from game import build_requests as br  # noqa: E402
from game.character import load_character, save_character, starting_character  # noqa: E402
from game.character_import import decode_code, encode_code, read_spec  # noqa: E402
from game.passives import PASSIVE_FLAVOUR_MAX, clean_flavour, tidy_flavour  # noqa: E402

STORY = "A prayer said so often\nthat it stopped being a request."
LINE = "[Combat Start] Gain 2 Protection"


def passive(flavour=None, name="Steady", **extra):
    item = {"name": name, "kind": "passive", "cost": 2, "lines": [LINE], **extra}
    if flavour is not None:
        item["flavour"] = flavour
    return item


def run(coro):
    return asyncio.run(coro)


def interaction(user_id: int = 7, admin: bool = False):
    i = MagicMock()
    i.user.id = user_id
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.client = MagicMock()
    i.client.get_user.return_value = MagicMock(send=AsyncMock())
    i.guild = MagicMock()
    i.user = MagicMock(spec=discord.Member)
    i.user.id = user_id
    i.user.guild_permissions.manage_guild = admin
    return i


def said(i) -> str:
    return i.response.send_message.call_args.args[0]


class InTempDir(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()


class Cleaning(unittest.TestCase):
    def test_line_breaks_padding_and_empty_lines_are_tidied(self):
        self.assertEqual(clean_flavour("  one  \r\n\r\n  two\r  three  \n\n\n"), "one\ntwo\nthree")
        self.assertEqual(clean_flavour(STORY), STORY)

    def test_nothing_and_not_text_are_no_flavour(self):
        for value in (None, "", "   \n \r\n  ", 5, ["a"], {"a": 1}):
            self.assertEqual(clean_flavour(value), "", repr(value))

    def test_it_is_cut_at_the_limit_but_tidying_alone_does_not_cut(self):
        long = "x" * (PASSIVE_FLAVOUR_MAX + 100)
        self.assertEqual(clean_flavour(long), "x" * PASSIVE_FLAVOUR_MAX)
        self.assertEqual(tidy_flavour(long), long)
        self.assertEqual(clean_flavour(clean_flavour(long)), clean_flavour(long))       # cleaning twice changes nothing


class ReadingFlavour(unittest.TestCase):
    def read(self, *passives):
        return read_spec({"v": 1, "name": "Rook", "passives": list(passives)})

    def test_flavour_is_kept_tidied(self):
        spec, error = self.read(passive("  A prayer said so often \r\n\r\n that it stopped being a request.  "))
        self.assertIsNone(error)
        self.assertEqual(spec.params["passives"][0]["flavour"], STORY)

    def test_no_flavour_leaves_the_passive_as_it_was(self):
        spec, error = self.read(passive(), passive("", name="Blank"), passive("  \n ", name="Spaces"))
        self.assertIsNone(error)
        for item in spec.params["passives"]:
            self.assertNotIn("flavour", item)

    def test_a_flavour_that_is_not_text_is_refused_with_the_name(self):
        for value in (5, ["a"], {"a": 1}, True):
            spec, error = self.read(passive(value))
            self.assertIsNone(spec, repr(value))
            self.assertIn("flavour text of Steady has to be text", error)

    def test_the_limit_is_on_the_tidied_text(self):
        spec, error = self.read(passive("x" * PASSIVE_FLAVOUR_MAX))
        self.assertIsNone(error)
        spec, error = self.read(passive("x" * (PASSIVE_FLAVOUR_MAX + 1)))
        self.assertIsNone(spec)
        self.assertIn(f"longer than {PASSIVE_FLAVOUR_MAX} characters", error)
        padded = "   " + "x" * PASSIVE_FLAVOUR_MAX + "\r\n\r\n   "         # long as typed, within the limit once tidied
        self.assertIsNone(self.read(passive(padded))[1])

    def test_it_travels_in_a_code(self):
        code = encode_code({"v": 1, "name": "Rook", "passives": [passive(STORY)]})
        data, error = decode_code(code)
        self.assertIsNone(error)
        spec, error = read_spec(data)
        self.assertIsNone(error)
        self.assertEqual(spec.params["passives"][0]["flavour"], STORY)


class SavedOnTheCharacter(InTempDir):
    def import_build(self, *passives):
        i = interaction()
        run(ci.handle_import(i, encode_code({"v": 1, "name": "Pax", "passives": list(passives)}), ""))
        return said(i)

    def approve_with_points(self, points: int):
        pax = load_character("Pax")
        pax.pp_total = points
        save_character(pax)
        admin = interaction(1, admin=True)
        run(cb.approve(admin, br.get_request(1)))
        return br.get_request(1)

    def test_an_imported_passive_keeps_its_flavour_through_the_request(self):
        self.assertIn("request #1", self.import_build(passive(STORY, cost=2), passive(name="Plain", cost=1)))
        self.assertEqual(load_character("Pax").passives, [])
        text = br.describe_request(br.get_request(1))
        self.assertIn("Flavour text of Steady: A prayer said so often / that it stopped being a request.", text)
        self.assertNotIn("Flavour text of Plain", text)
        self.assertEqual(self.approve_with_points(3).status, "approved")
        kept = {p["name"]: p for p in load_character("Pax").passives}
        self.assertEqual(kept["Steady"]["flavour"], STORY)
        self.assertNotIn("flavour", kept["Plain"])         # nothing written for a passive with no story

    def test_a_single_passive_request_keeps_it_too(self):
        starter = starting_character(7, "Pax")
        starter.pp_total = 3
        save_character(starter)
        params = {"name": "Steady", "kind": "passive", "lines": [LINE], "cost": 2, "note": "", "flavour": "  " + STORY + "  "}
        summary, error, changed = br.try_change(starter, "passive", params)
        self.assertIsNone(error)
        self.assertEqual(changed.passives[0]["flavour"], STORY)
        self.assertIn("Flavour text: A prayer said so often / that it stopped being a request.", br.describe_request(br.Request(0, 0, "Pax", "passive", params)))
        params.pop("flavour")
        _, error, changed = br.try_change(starter, "passive", params)
        self.assertIsNone(error)
        self.assertNotIn("flavour", changed.passives[0])

    def test_a_request_that_somehow_holds_too_much_flavour_is_cut_not_trusted(self):
        starter = starting_character(7, "Pax")
        starter.pp_total = 3
        params = {"name": "Steady", "kind": "passive", "lines": [LINE], "cost": 2, "note": "", "flavour": "y" * 5000}
        _, error, changed = br.try_change(starter, "passive", params)
        self.assertIsNone(error)
        self.assertEqual(len(changed.passives[0]["flavour"]), PASSIVE_FLAVOUR_MAX)


class Commands(InTempDir):
    def setUp(self):
        super().setUp()
        pax = starting_character(7, "Pax")
        pax.pp_total = 4
        save_character(pax)

    def cog(self, cls):
        bot = MagicMock()
        bot.get_user.return_value.send = AsyncMock()           # the owner's DM goes nowhere
        return cls(bot)

    def test_the_admin_command_stores_the_flavour_and_has_the_option(self):
        names = [p.name for p in cc.CharacterAdminCog.passive.parameters]
        self.assertIn("flavour", names)
        i = interaction(1, admin=True)
        run(cc.CharacterAdminCog.passive.callback(
            self.cog(cc.CharacterAdminCog), i, "Pax", "Steady", MagicMock(value="passive", name="Passive"), 2, LINE, flavour="  " + STORY + "\r\n"))
        self.assertIn("now has", said(i))
        self.assertEqual(load_character("Pax").passives[0]["flavour"], STORY)

    def test_the_admin_command_without_flavour_writes_none(self):
        i = interaction(1, admin=True)
        run(cc.CharacterAdminCog.passive.callback(
            self.cog(cc.CharacterAdminCog), i, "Pax", "Steady", MagicMock(value="passive", name="Passive"), 2, LINE))
        self.assertNotIn("flavour", load_character("Pax").passives[0])

    def test_a_player_can_ask_for_a_passive_with_a_flavour_text(self):
        names = [p.name for p in cb.RequestCog.passive.parameters]
        self.assertIn("flavour", names)
        i = interaction(7)
        i.client.get_channel = MagicMock(return_value=None)
        run(cb.RequestCog.passive.callback(
            self.cog(cb.RequestCog), i, "Pax", "Steady", MagicMock(value="passive"), 2, LINE, "It is canon.", flavour=STORY))
        self.assertEqual(br.get_request(1).params["flavour"], STORY)

    def test_the_options_are_limited_by_discord_to_the_flavour_limit(self):
        for command in (cc.CharacterAdminCog.passive, cb.RequestCog.passive):
            flavour = next(p for p in command.parameters if p.name == "flavour")
            self.assertEqual(flavour.max_value, PASSIVE_FLAVOUR_MAX)
            self.assertFalse(flavour.required)
            self.assertLessEqual(len(flavour.description), 100)


class OnTheSheet(unittest.TestCase):
    def sheet(self, passives):
        c = starting_character(7, "Pax")
        c.pp_total = 12
        c.passives = passives
        return cc.build_character_embed(c, "Owner")

    def field(self, embed, name):
        return next(f.value for f in embed.fields if f.name == name)

    def test_the_story_is_shown_in_italics_under_its_passive(self):
        embed = self.sheet([
            {"name": "Grace of God", "kind": "support", "cost": 2, "lines": [LINE], "flavour": STORY},
            {"name": "Plain", "kind": "passive", "cost": 1, "lines": ["[Turn Start] Gain 3 Sanity"]},
        ])
        self.assertEqual(self.field(embed, "Passives").split("\n"), [
            f"Support **Grace of God**: {LINE}", "*A prayer said so often*", "*that it stopped being a request.*",
            "**Plain**: [Turn Start] Gain 3 Sanity",
        ])

    def test_a_passive_without_a_story_looks_as_it_did(self):
        embed = self.sheet([{"name": "Plain", "kind": "passive", "cost": 1, "lines": ["[Turn Start] Gain 3 Sanity"]}])
        self.assertEqual(self.field(embed, "Passives"), "**Plain**: [Turn Start] Gain 3 Sanity")

    def test_markdown_in_a_story_cannot_break_the_sheet(self):
        embed = self.sheet([{"name": "P", "kind": "passive", "cost": 1, "lines": [LINE], "flavour": "A *bold* claim, _really_ and ||hidden|| and `code`"}])
        story = self.field(embed, "Passives").split("\n")[1]
        self.assertTrue(story.startswith("*") and story.endswith("*"))
        self.assertIn("\\*bold\\*", story)
        self.assertIn("\\_really\\_", story)

    def test_the_field_and_the_whole_sheet_stay_inside_discords_limits(self):
        biggest = [
            {"name": f"P{i}", "kind": "passive", "cost": 1, "lines": ["[Combat Start] Gain 2 Protection " + "x" * 260] * 3, "flavour": "y" * PASSIVE_FLAVOUR_MAX}
            for i in range(3)
        ]
        middle = [
            {"name": f"P{i}", "kind": "support", "cost": 1, "lines": ["x" * 120], "flavour": ("word " * 90).strip() + "\n" + "second paragraph " * 10}
            for i in range(3)
        ]
        for passives in (biggest, middle):
            embed = self.sheet(passives)
            self.assertLessEqual(len(self.field(embed, "Passives")), 1000)
            self.assertLessEqual(len(embed), 6000)
            self.assertLessEqual(len(embed.fields), 25)

    def test_the_rules_are_never_given_up_for_a_story(self):
        passives = [
            {"name": f"P{i}", "kind": "passive", "cost": 1, "lines": ["[Combat Start] Gain 2 Protection " + "x" * 100], "flavour": "story " * 60}
            for i in range(3)
        ]
        text = cc.passives_text(passives)
        for i in range(3):
            self.assertIn(f"**P{i}**: [Combat Start] Gain 2 Protection " + "x" * 100, text)
        self.assertLessEqual(len(text), 1000)

    def test_the_room_is_shared_so_one_long_story_cannot_push_out_the_others(self):
        passives = [
            {"name": "A", "kind": "passive", "cost": 1, "lines": ["x" * 100], "flavour": "long " * 80},
            {"name": "B", "kind": "passive", "cost": 1, "lines": ["x" * 100], "flavour": "Short tale here."},
            {"name": "C", "kind": "passive", "cost": 1, "lines": ["x" * 100], "flavour": "long " * 80},
        ]
        text = cc.passives_text(passives)
        lines = text.split("\n")
        self.assertIn("*Short tale here.*", lines)
        self.assertEqual(sum(1 for l in lines if l.startswith("**")), 3)
        first = next(l for l in lines if l.startswith("*") and not l.startswith("**"))
        self.assertTrue(first.endswith("…*"))                      # the first long story is cut short with …, to its share of the room
        self.assertLessEqual(len(first) + 1, (1000 - 3 * 105) // 3)
        third = lines[-1]
        self.assertGreater(len(third), len(first))                 # what the short one did not use goes to the last
        self.assertLessEqual(len(text), 1000)

    def test_a_story_is_cut_with_an_ellipsis_when_it_does_not_fit_and_never_mid_escape(self):
        passives = [{"name": "A", "kind": "passive", "cost": 1, "lines": ["x" * 900], "flavour": "*" * 300}]
        text = cc.passives_text(passives)
        self.assertLessEqual(len(text), 1000)
        last = text.split("\n")[-1]
        self.assertTrue(last.startswith("*") and last.endswith("…*"))
        self.assertNotIn("\\", last[1:-1].replace("\\*", ""))       # every backslash is the front half of an escaped star: no escape is cut in two


if __name__ == "__main__":
    unittest.main()
