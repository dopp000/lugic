"""Checks the things Discord rejects outright: command text lengths, embed sizes, and a few safety rules.

Run all tests from the repo root with:  python -m unittest discover tests
"""
import asyncio
import os
import random
import sys
import tempfile
import unittest
import warnings
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.simplefilter("ignore")

import discord  # noqa: E402
from discord.ext import commands  # noqa: E402

import cogs.battle as battle_cog  # noqa: E402
from game.battle import Battle, Fighter, Part  # noqa: E402
from game.display import bar, budget_bar, hp_bar, sanity_bar  # noqa: E402
from game.help_content import HELP_CATEGORIES  # noqa: E402
from game.skills import Skill  # noqa: E402

EXTENSIONS = ["cogs.general", "cogs.battle", "cogs.character", "cogs.build_requests", "cogs.roll", "cogs.help"]


class InTempDir(unittest.TestCase):
    """Runs each test from an empty folder so saved files never land in the repo's data/."""

    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()


class CommandLimits(unittest.TestCase):
    """One bad description makes Discord refuse the whole command sync, so check every one."""

    def test_every_command_fits_discord(self):
        problems = []

        def walk(node, path):
            description = node.get("description", "")
            if not 1 <= len(description) <= 100:
                problems.append(f"{path}: description is {len(description)} characters (needs 1 to 100)")
            if not 1 <= len(node.get("name", "")) <= 32:
                problems.append(f"{path}: name length")
            for option in node.get("options") or []:
                walk(option, f"{path}/{option.get('name', '?')}")

        async def load():
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
            for extension in EXTENSIONS:
                await bot.load_extension(extension)
            for command in bot.tree.get_commands():
                node = command.to_dict(bot.tree)
                walk(node, "/" + node["name"])
                subcommands = [o for o in node.get("options", []) if o.get("type") in (1, 2)]
                if len(subcommands) > 25:
                    problems.append(f"/{node['name']}: {len(subcommands)} subcommands (the limit is 25)")

        asyncio.run(load())
        self.assertEqual(problems, [])

    def test_help_entries_fit_discord(self):
        for _key, title, blurb, entries in HELP_CATEGORIES:
            self.assertLessEqual(len(blurb), 100, title)
            for entry in entries:
                self.assertLessEqual(len(entry.summary), 100, entry.key)
                self.assertLessEqual(len(entry.body) * 1.25, 4096, entry.key)


class BattleDisplaySize(unittest.TestCase):
    def _battle(self, per_side):
        fighters = []
        for side in "AB":
            for i in range(per_side):
                f = Fighter(name=f"{side}-Fighter{i + 1}", side=side, hp=420, max_hp=500, power=10)
                f.slot_speeds = [10, 8, 6]
                f.skill_slots = 3
                f.sanity = -12
                fighters.append(f)
        return Battle(channel_id=1, fighters=fighters, battle_type="standard")

    def test_embed_stays_inside_discord_limits(self):
        for per_side in (1, 3, 4, 8, 10, 20):
            embed = battle_cog.build_battle_embed(self._battle(per_side))
            self.assertLessEqual(len(embed), 6000, f"{per_side} per side")
            self.assertLessEqual(len(embed.fields), 25, f"{per_side} per side")
            for field in embed.fields:
                self.assertLessEqual(len(field.value), 1024, f"{per_side} per side")

    def test_boss_with_many_parts_fits(self):
        battle = self._battle(1)
        boss = battle.fighters[0]
        boss.name = "The Extremely Long Named Distorted Abnormality"
        boss.skill_slots = 5
        boss.slot_speeds = [10, 9, 8, 7, 6]
        for slot in range(1, 6):
            boss.parts[slot] = Part(name=f"Left Claw {slot}", hp=100, max_hp=100)
        embed = battle_cog.build_battle_embed(battle)
        for field in embed.fields:
            self.assertLessEqual(len(field.value), 1024)


class Bars(unittest.TestCase):
    def test_bar_shape(self):
        self.assertEqual(len(bar(0.5, "X", 8)), 8)
        self.assertEqual(hp_bar(0, 100), "⬛" * 8)
        self.assertEqual(hp_bar(100, 100), "🟩" * 8)
        self.assertEqual(hp_bar(1, 1000).count("🟥"), 1)  # alive always shows a mark
        self.assertEqual(hp_bar(999, 1000).count("⬛"), 1)  # not full unless full

    def test_colors_follow_the_numbers(self):
        self.assertIn("🟩", hp_bar(60, 100))
        self.assertIn("🟨", hp_bar(40, 100))
        self.assertIn("🟥", hp_bar(20, 100))
        self.assertIn("🟦", sanity_bar(10))
        self.assertIn("🟧", sanity_bar(-30))
        self.assertIn("🟥", budget_bar(70, 63))


class CharacterFiles(InTempDir):
    def test_bad_names_are_refused(self):
        from game.character import validate_character_name

        for bad in ("../evil", "a/b", "a\\b", "..hidden", "   ", "x" * 41, 'say "hi"', "a:b"):
            self.assertIsNotNone(validate_character_name(bad), bad)
        for good in ("Ryoshu", "Mr. Smith", "D'Artagnan", "Zoë Ünal", "x" * 40):
            self.assertIsNone(validate_character_name(good), good)

    def test_path_stays_inside_characters_folder(self):
        from game.character import CHARACTERS_DIR, _character_path

        for name in ("../../evil", "a/b", "..\\x"):
            path = os.path.abspath(_character_path(name))
            self.assertEqual(os.path.dirname(path), os.path.abspath(CHARACTERS_DIR), name)

    def test_saving_leaves_no_temp_files(self):
        import game.build_requests as requests
        from game.character import CHARACTERS_DIR, Character, load_character, save_character

        save_character(Character(owner_id=1, name="Foo Bar"))
        save_character(Character(owner_id=1, name="Foo Bar"))
        self.assertEqual(os.listdir(CHARACTERS_DIR), ["foo_bar.json"])
        self.assertEqual(load_character("foo bar").name, "Foo Bar")
        requests._save(requests._blank())
        self.assertEqual(sorted(os.listdir("data")), ["characters", "requests.json"])


class CombatAnimation(InTempDir):
    """The combat message is edited many times. Discord throttles that, so the number of edits and the gap between them matter."""

    def _run(self, coins_each):
        random.seed(11)
        a = Fighter(name="Ayla", side="A", hp=800, max_hp=800, power=12)
        b = Fighter(name="Bram", side="B", hp=800, max_hp=800, power=10)
        for f in (a, b):
            f.slot_speeds = [10, 9, 8]
            f.skill_slots = 3
        skill_a = Skill(name="Strike", base_power=7, coin_power=2, coins=coins_each)
        skill_b = Skill(name="Jab", base_power=7, coin_power=2, coins=coins_each)
        a.skills["s"], b.skills["s"] = skill_a, skill_b
        self.assertTrue(a.declare_in_slot(1, skill_a, b, 1))
        self.assertTrue(b.declare_in_slot(1, skill_b, a, 1))
        battle = Battle(channel_id=1, fighters=[a, b])
        battle.started = True
        cog = battle_cog.BattleCog(MagicMock())
        cog.battles[1] = battle

        interaction = MagicMock()
        interaction.channel_id = 1
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        clock = [0.0]
        edits = []

        async def edit(embed=None, **kwargs):
            edits.append(clock[0])

        message = MagicMock()
        message.edit = edit
        interaction.followup.send = AsyncMock(return_value=message)
        interaction.channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()))

        real_sleep = battle_cog.asyncio.sleep

        async def fake_sleep(seconds, *args, **kwargs):
            clock[0] += seconds

        battle_cog.asyncio.sleep = fake_sleep
        try:
            asyncio.run(battle_cog.BattleCog.combat.callback(cog, interaction))
        finally:
            battle_cog.asyncio.sleep = real_sleep
        return edits

    def test_edits_are_few_and_spaced(self):
        edits = self._run(3)
        self.assertLessEqual(len(edits), 30)
        gaps = [later - earlier for earlier, later in zip(edits, edits[1:])]
        self.assertGreaterEqual(min(gaps), 1.0)


if __name__ == "__main__":
    unittest.main()
