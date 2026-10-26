"""Statuses with no keyword (Haste, Fragile, Bind and so on) have one number, and the game calls it Potency.

The bot keeps the number of some of them in Count (see COUNT_TURN_STATUSES), so a player who writes the Potency ("Gain 3 Haste" is potency 3,
count 1 to the line parser) must get the same strength as one who writes the Count. These tests pin that down, plus the wording the help and
the site use for them.
"""
import asyncio
import os
import sys
import unittest
import warnings
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.simplefilter("ignore")

import discord  # noqa: E402
from discord import app_commands  # noqa: E402

import cogs.battle as battle_cog  # noqa: E402
from game import builder_rules, line_spec  # noqa: E402
from game.battle import Battle, Fighter  # noqa: E402
from game.help_content import STATUS_EFFECTS  # noqa: E402
from game.statuses import (  # noqa: E402
    COUNT_TURN_STATUSES, POTENCY_TURN_STATUSES, SANITY_LOW_MORALE, SANITY_PANIC, apply_status,
)


class KeywordlessStrength(unittest.TestCase):
    def test_the_potency_a_player_writes_is_how_strong_a_count_turn_status_is(self):
        for name in sorted(COUNT_TURN_STATUSES):
            with self.subTest(status=name):
                by_potency = apply_status(None, name, 3, 1)       # "Gain 3 Haste" as the line parser reads it
                by_count = apply_status(None, name, 1, 3)
                both = apply_status(None, name, 3, 3)
                self.assertEqual(by_potency.count, 3)
                self.assertEqual(by_count.count, 3)
                self.assertEqual(both.count, 3)

    def test_gaining_one_again_adds_its_strength(self):
        for name in sorted(COUNT_TURN_STATUSES):
            with self.subTest(status=name):
                first = apply_status(None, name, 2, 1)
                again = apply_status(first, name, 2, 1)
                self.assertEqual(first.count, 2)
                self.assertEqual(again.count, 4)

    def test_a_status_that_gives_no_strength_stays_nothing(self):
        for name in sorted(COUNT_TURN_STATUSES):
            with self.subTest(status=name):
                self.assertEqual(apply_status(None, name, 0, 0).count, 0)

    def test_statuses_that_keep_their_strength_in_potency_are_untouched(self):
        for name in sorted(POTENCY_TURN_STATUSES):
            with self.subTest(status=name):
                got = apply_status(None, name, 4, 1)
                self.assertEqual((got.potency, got.count), (4, 1))

    def test_a_keyword_status_still_keeps_both_numbers_apart(self):
        got = apply_status(None, "burn", 3, 2)
        self.assertEqual((got.potency, got.count), (3, 2))
        got = apply_status(None, "poise", 1, 4)
        self.assertEqual((got.potency, got.count), (1, 4))


class SetStatusCommand(unittest.TestCase):
    """/battle setstatus: an admin who types a Potency for a status with no keyword gets that strength, the same as one who types a Count."""

    def _set(self, status, potency, count):
        fighters = [Fighter(name="Rien", side="A", hp=100, max_hp=100, power=8), Fighter(name="Foe", side="B", hp=100, max_hp=100, power=8)]
        fighters[0].slot_speeds = [5, 5]
        battle = Battle(channel_id=1, fighters=fighters)
        cog = battle_cog.BattleCog(MagicMock())
        cog.battles[1] = battle
        interaction = MagicMock()
        interaction.channel_id = 1
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.roles = [MagicMock(id=battle_cog.ADMIN_ROLE_ID)]
        interaction.response.send_message = AsyncMock()
        real = battle_cog.sync_battle_message
        battle_cog.sync_battle_message = AsyncMock()
        try:
            asyncio.run(battle_cog.BattleCog.setstatus.callback(
                cog, interaction, fighter="Rien", status_name=app_commands.Choice(name=status.title(), value=status), potency=potency, count=count))
        finally:
            battle_cog.sync_battle_message = real
        return fighters[0]

    def test_a_potency_alone_is_the_strength(self):
        rien = self._set("haste", potency=3, count=0)
        self.assertEqual(rien.get_status("haste").count, 3)
        self.assertEqual(rien.slot_speeds, [8, 8])

    def test_a_count_alone_still_works(self):
        rien = self._set("haste", potency=0, count=2)
        self.assertEqual(rien.get_status("haste").count, 2)
        self.assertEqual(rien.slot_speeds, [7, 7])

    def test_bind_still_reads_its_potency(self):
        rien = self._set("bind", potency=2, count=0)
        self.assertEqual(rien.get_status("bind").potency, 2)
        self.assertEqual(rien.slot_speeds, [3, 3])

    def test_a_keyword_status_keeps_both_numbers_as_typed(self):
        rien = self._set("burn", potency=4, count=2)
        self.assertEqual((rien.get_status("burn").potency, rien.get_status("burn").count), (4, 2))


class KeywordlessWording(unittest.TestCase):
    """The help and the site say Potency for these, never Count."""

    def test_the_help_text_of_each_count_turn_status_names_a_potency_and_no_count(self):
        bodies = {e.key: e.body for e in STATUS_EFFECTS}
        for name in sorted(COUNT_TURN_STATUSES):
            key = "fragile" if name.endswith("_fragility") else name       # the help has one page for Fragile and its three damage types
            with self.subTest(status=name):
                self.assertIn("Potency", bodies[key])
                self.assertNotIn("Count", bodies[key])

    def test_the_site_says_the_same(self):
        guide = {s["key"]: s for s in builder_rules.rules()["statusGuide"]}
        for name in sorted(COUNT_TURN_STATUSES):
            with self.subTest(status=name):
                text = " ".join(guide[name]["quick"].values()) + " " + guide[name]["body"]
                self.assertIn("Potency", text)
                self.assertNotIn("Count", text)

    def test_the_help_says_that_these_statuses_have_one_number(self):
        from game.help_content import HELP_CATEGORIES
        text = " ".join(str(part) for part in HELP_CATEGORIES)
        self.assertRegex(text, r"no keyword and only a \*\*Potency\*\*")


class LowMoraleAndPanicRow(unittest.TestCase):
    def test_the_row_gives_the_whole_range_from_low_morale_to_panic(self):
        guide = {s["key"]: s for s in builder_rules.rules()["statusGuide"]}
        row = guide["panic"]["quick"]
        self.assertIn(f"{abs(SANITY_LOW_MORALE)}", row["when"])
        self.assertIn(f"{abs(SANITY_PANIC)}", row["when"])
        self.assertIn("and below", row["when"])
        self.assertIn("Low Morale", row["does"])
        self.assertIn("Panic", row["does"])


class SelectorLabels(unittest.TestCase):
    def test_labels_are_written_as_words_with_hp_and_sp_in_capitals(self):
        self.assertIn("Slowest Speed", line_spec.SELECTOR_NAMES)
        self.assertIn("Fastest Speed", line_spec.SELECTOR_NAMES)
        self.assertIn("Highest HP", line_spec.SELECTOR_NAMES)
        self.assertIn("Lowest SP", line_spec.SELECTOR_NAMES)
        for label in line_spec.SELECTOR_NAMES:
            with self.subTest(label=label):
                self.assertNotIn("SPeed", label)
                self.assertNotRegex(label, r"\bSp\b|\bHp\b")

    def test_each_label_is_still_the_phrase_the_parser_reads(self):
        for label, phrase in zip(line_spec.SELECTOR_NAMES, line_spec._SELECTOR_PHRASES):
            self.assertEqual(label.lower(), phrase)


if __name__ == "__main__":
    unittest.main()
