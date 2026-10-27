"""More coins lower a skill's Base Power and Coin Power limits.

Coin Power carries on to the coins after it, so a skill's expected total power grows faster than its number of coins. With one flat limit a
4-coin skill did about five times what a 1-coin skill did at the same Power, and more coins was simply better. Each coin after the first now
lowers both limits by COIN_CAP_STEP (never below MIN_COIN_CAP). Skills saved before that are grandfathered.
"""
import asyncio
import os
import re
import sys
import unittest
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.simplefilter("ignore")

from cogs.battle import EditSkillModal  # noqa: E402
from cogs.build_check import _Answer, check_skill  # noqa: E402
from game import tutorial  # noqa: E402
from game.build_points import (  # noqa: E402
    COIN_CAP_STEP, MIN_COIN_CAP, check_build, max_base_power, max_coin_power, skill_coin_excess, skill_power_problem,
)
from game.character import starting_character  # noqa: E402
from game.practice_bot import generate_bot  # noqa: E402
from game.skills import Skill  # noqa: E402

import random  # noqa: E402


def expected_total(coins: int, base: int, coin: int, heads: float = 0.5) -> float:
    """Average of the coins' powers added up: coin number i is worth base + coin power x heads x i."""
    return coins * base + coin * heads * coins * (coins + 1) / 2


class TheLimits(unittest.TestCase):
    def test_one_coin_is_the_plain_ceiling_it_always_was(self):
        for power in range(0, 15):
            self.assertEqual(max_base_power(power), (20 + 4 * power) // 10)
            self.assertEqual(max_coin_power(power), (5 + power) // 5)
            self.assertEqual(max_base_power(power, 1), max_base_power(power))

    def test_each_extra_coin_lowers_both_limits_and_neither_goes_below_one(self):
        self.assertEqual((COIN_CAP_STEP, MIN_COIN_CAP), (1, 1))
        self.assertEqual([max_base_power(6, n) for n in (1, 2, 3, 4)], [4, 3, 2, 1])
        self.assertEqual([max_coin_power(6, n) for n in (1, 2, 3, 4)], [2, 1, 1, 1])
        self.assertEqual([max_base_power(12, n) for n in (1, 2, 3, 4)], [6, 5, 4, 3])
        self.assertEqual([max_coin_power(12, n) for n in (1, 2, 3, 4)], [3, 2, 1, 1])
        for power in range(0, 15):
            for n in (1, 2, 3, 4):
                self.assertGreaterEqual(max_base_power(power, n), MIN_COIN_CAP)
                self.assertGreaterEqual(max_coin_power(power, n), MIN_COIN_CAP)
                if n > 1:
                    self.assertLessEqual(max_base_power(power, n), max_base_power(power, n - 1))
                    self.assertLessEqual(max_coin_power(power, n), max_coin_power(power, n - 1))

    def test_a_skill_at_the_limit_no_longer_runs_away_with_its_coins(self):
        for power in range(6, 13):
            totals = [expected_total(n, max_base_power(power, n), max_coin_power(power, n)) for n in (1, 2, 3, 4)]
            with self.subTest(power=power, totals=totals):
                self.assertEqual(totals, sorted(totals))                 # another coin is never a loss
                self.assertLess(totals[3] / totals[0], 3)                # but 4 coins are nowhere near the 5x a flat limit gave
        flat = [expected_total(n, max_base_power(6), max_coin_power(6)) for n in (1, 2, 3, 4)]
        self.assertGreater(flat[3] / flat[0], 5)                          # what it was

    def test_the_message_says_which_limit_and_why_it_is_lower(self):
        self.assertEqual(skill_power_problem(6, 5, 2), "Base Power 5 is above the limit of 4 for Power 6.")        # one coin: as before
        text = skill_power_problem(6, 4, 2, 3)
        self.assertEqual(text, "Base Power 4 is above the limit of 2 and Coin Power 2 is above the limit of 1 for Power 6 with 3 coins "
                               "(each coin after the first lowers both limits by 1).")
        self.assertIsNone(skill_power_problem(6, 2, 1, 3))

    def test_how_far_over_is_the_two_numbers_added_up(self):
        self.assertEqual(skill_coin_excess(6, 4, 2, 4), (4 - 1) + (2 - 1))
        self.assertEqual(skill_coin_excess(6, 3, 1, 2), 0)
        self.assertEqual(skill_coin_excess(6, 4, 2, 1), 0)


class SavedSkillsAreGrandfathered(unittest.TestCase):
    def test_the_build_check_still_holds_a_saved_skill_to_the_plain_ceiling_only(self):
        c = starting_character(1, "Old")
        c.add_skill(Skill("Four", 4, 2, 4))                 # fine before the rule, over the 4-coin limit now
        c.add_skill(Skill("Too much", 5, 2, 1))             # over the plain ceiling: flagged as it always was
        problems = check_build(c).skill_problems
        self.assertEqual(len(problems), 1)
        self.assertIn("Too much", problems[0])


def run(coro):
    return asyncio.run(coro)


async def edit(character, old, stats, name=None):
    """Presses Save on the edit form for `old` with these numbers. Returns (skill, None) or (None, the refusal)."""
    modal = EditSkillModal(character, old)
    modal.skill_name._value = name or old.name
    modal.stats_input._value = stats
    modal.attack_weight_input._value = "1"
    modal.status_input._value = "none"
    modal.triggers_input._value = ""
    answer = _Answer()
    await modal.on_submit(answer)
    return (answer.view.skill, None) if answer.view is not None else (None, answer.reply)


class TheSkillForm(unittest.TestCase):
    def setUp(self):
        self.rook = starting_character(1, "Rook")          # Power 6

    def test_a_new_skill_is_held_to_the_limit_for_its_coins(self):
        for stats, fits in (("4, 2, 1, Blunt", True), ("3, 1, 2, Blunt", True), ("2, 1, 3, Blunt", True), ("1, 1, 4, Blunt", True),
                            ("4, 2, 2, Blunt", False), ("3, 2, 2, Blunt", False), ("4, 1, 2, Blunt", False),
                            ("3, 1, 3, Blunt", False), ("2, 1, 4, Blunt", False)):
            with self.subTest(stats=stats):
                skill, why = run(check_skill(self.rook, {"name": "Test", "stats": stats}))
                self.assertEqual(skill is not None, fits, why)
                if not fits:
                    self.assertIn("each coin after the first lowers both limits by 1", why)

    def test_the_refusal_names_the_limit_and_how_to_get_past_it(self):
        _, why = run(check_skill(self.rook, {"name": "Test", "stats": "4, 2, 3, Slash"}))
        self.assertIn("Base Power 4 is above the limit of 2", why)
        self.assertIn("Coin Power 2 is above the limit of 1", why)
        self.assertIn("fewer coins", why)

    def test_a_higher_power_raises_the_limit_for_every_coin_count(self):
        self.rook.power = 12
        skill, why = run(check_skill(self.rook, {"name": "Test", "stats": "4, 1, 3, Slash"}))
        self.assertIsNotNone(skill, why)
        skill, _ = run(check_skill(self.rook, {"name": "Test", "stats": "5, 2, 3, Slash"}))
        self.assertIsNone(skill)

    def test_an_exempt_character_is_not_held_to_it(self):
        self.rook.bp_exempt = True
        skill, why = run(check_skill(self.rook, {"name": "Boss", "stats": "6, 3, 4, Slash"}))
        self.assertIsNotNone(skill, why)

    def test_an_old_skill_can_still_be_renamed_or_reworded_or_lowered(self):
        old = Skill("Four", 4, 2, 4, damage_type="slash")
        self.rook.add_skill(old)
        skill, why = run(edit(self.rook, old, "4, 2, 4, Slash", name="Four Again"))
        self.assertIsNotNone(skill, why)
        skill, why = run(edit(self.rook, old, "3, 2, 4, Slash"))
        self.assertIsNotNone(skill, why)                                 # still over the limit, but less than before

    def test_an_old_skill_cannot_get_further_over_or_a_fitting_one_more_coins(self):
        low = Skill("Low", 3, 2, 4, damage_type="slash")
        self.rook.add_skill(low)
        skill, why = run(edit(self.rook, low, "4, 2, 4, Slash"))
        self.assertIsNone(skill)
        self.assertIn("limit of 1", why)
        fine = Skill("Fine", 4, 2, 1, damage_type="slash")
        self.rook.add_skill(fine)
        skill, why = run(edit(self.rook, fine, "4, 2, 4, Slash"))
        self.assertIsNone(skill)


class WhatHandsOutSkills(unittest.TestCase):
    def test_the_skill_the_tutorial_teaches_is_accepted_by_the_form(self):
        step = tutorial.STEP_BY_ID["skill"]
        numbers = re.search(r"`(\d+, \d+, \d+, \w+)`", step.instruction if hasattr(step, "instruction") else str(step)).group(1)
        skill, why = run(check_skill(starting_character(1, "Newbie"), {"name": "Quick Jab", "stats": numbers}))
        self.assertIsNotNone(skill, why)

    def test_the_practice_bot_follows_the_same_limits(self):
        for seed in range(60):
            bot = generate_bot(30 + 20 * (seed % 4), sp_total=2, rng=random.Random(seed))
            for skill in bot.skills.values():
                with self.subTest(seed=seed, skill=skill.name):
                    self.assertEqual(skill_coin_excess(bot.power, skill.base_power, skill.coin_power, skill.coins), 0)


if __name__ == "__main__":
    unittest.main()
