"""The character builder site (docs/builder/) must agree with the bot.

The site's rules engine is JavaScript, the bot's rules are Python. These tests run both on the
same builds and compare the answers, check that the site's codes open in the bot (and the bot's
open on the site), that every "add a line" button hands out text the bot's parser accepts, and
that docs/builder/rules.js is the file game/builder_rules.py would write today.

The comparison tests need node (version 18 or newer). They are skipped when it is not installed.
Run all tests from the repo root with:  python -m unittest discover tests
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.simplefilter("ignore")

from cogs.battle import _parse_status_tokens  # noqa: E402
from game import build_points as bp  # noqa: E402
from game import builder_rules  # noqa: E402
from game.build_points import (  # noqa: E402
    DEFENSE_ARCHETYPES, RANKED_STATUSES, check_build, skill_coin_excess, skill_power_problem, skill_rank_problems,
)
from game.build_requests import _apply  # noqa: E402
from game.character import starting_character  # noqa: E402
from game.character_import import decode_code, encode_code, needs_approval, read_spec  # noqa: E402
from game.conditions import parse_trigger_text  # noqa: E402
from game.passives import parse_passive  # noqa: E402
from game.skills import Skill  # noqa: E402
from game.help_content import HELP_CATEGORIES  # noqa: E402
from game.statuses import ONE_TURN_STATUSES, OTHER_DEBUFF_STATUSES, SANITY_LOW_MORALE, SANITY_PANIC  # noqa: E402

NODE = shutil.which("node")
PARITY_JS = os.path.join(ROOT, "tests", "builder_parity.js")
FLAGS_JS = os.path.join(ROOT, "tests", "builder_flags.js")
COIN_STATUSES = list(RANKED_STATUSES) + list(ONE_TURN_STATUSES) + list(OTHER_DEBUFF_STATUSES)
TRIGGER_CHOICES = ["", "[On Use] Coin Power +1", "[Guard]", "[Evade]\n[On Use] Deal +10% damage", "[Counter]", "[Duality]"]


def character_from_spec(data: dict):
    """The character the bot would hold after applying this code's build, without the approval rules in between."""
    spec, error = read_spec(data)
    assert error is None, error
    pools = data.get("pools") or {}
    c = starting_character(1, spec.name)
    c.bp_total = pools.get("bp", c.bp_total)
    c.sp_total = pools.get("sp", 0)
    c.pp_total = pools.get("pp", 0)
    params = spec.params
    if params.get("stats"):
        _, error = _apply(c, "stats", params["stats"])
        assert error is None, error
    c.status_ranks = dict(params.get("ranks") or {})
    c.defense_archetypes = list(params.get("archetypes") or [])
    c.passives = list(params.get("passives") or [])
    for raw in params.get("skills") or []:
        coins = int(raw["stats"].split(",")[2])
        base, coin, _, damage_type = [p.strip() for p in raw["stats"].split(",")]
        names, pots, counts, error = _parse_status_tokens(raw.get("statuses", "none"), coins)
        assert error is None, error
        triggers, flags = parse_trigger_text(raw.get("triggers", ""), {})
        c.add_skill(Skill(
            name=raw["name"], base_power=int(base), coin_power=int(coin), coins=coins, damage_type=damage_type.lower(),
            coin_statuses=names, coin_status_potencies=pots, coin_status_counts=counts, triggers=triggers, tags=flags,
        ))
    return c


# Flavour texts that stress the cleaning: Windows line breaks, blank lines, padding, very long, emoji, markdown, nothing at all.
FLAVOURS = [
    "A prayer said so often\nthat it stopped being a request.",
    "  padded  \r\n\r\n  and\r  split  \n\n\n",
    "x" * 900,
    "line one\n" + "y" * 395 + "\nline three",
    "Émile 夜 \U0001F339 *stars* _under_ ~~gone~~ `code` > quote",
    "   \n \r\n  ",
    "",
]
SPEC_FLAVOURS = [FLAVOURS[i] for i in (0, 1, 4, 5, 6)]      # the ones the bot accepts as they are (the others are too long until they are cut)


def random_spec(rng: random.Random, index: int) -> dict:
    """A build shaped like a real code but with numbers that are often wrong, so the problems get compared too."""
    spec: dict = {"v": 1, "name": f"Case {index}", "pools": {
        "bp": rng.choice([20, 30, 30, 45, 63, 84, 108]), "sp": rng.randint(0, 4), "pp": rng.randint(0, 6),
    }}
    stats: dict = {}
    if rng.random() < 0.8:
        stats["hp"] = rng.choice([100, 105, 110, 120, 130, 140, 150, 160, 170])
    if rng.random() < 0.8:
        stats["power"] = rng.randint(4, 14)
    if rng.random() < 0.8:
        low = rng.randint(0, 9)
        stats["speed"] = [low, low + rng.randint(0, 5)]
    if rng.random() < 0.3:
        stats["stagger_off"] = rng.choice([[2], [3], [2, 3]])
    if rng.random() < 0.4:
        stats["offense"] = rng.randint(0, 5)
    if rng.random() < 0.4:
        stats["defense"] = rng.randint(0, 5)
    if rng.random() < 0.6:
        allowed = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
        stats["resist"] = {k: rng.choice(allowed + [0.6]) for k in ("burn", "bleed", "tremor", "rupture", "sinking")}
        physical = rng.choice([[1, 1, 1], [0.5, 1, 1.5], [1.5, 0.5, 1], [0.5, 0.5, 1.5]])
        stats["resist"].update(dict(zip(("slash", "blunt", "pierce"), physical)))
    if stats:
        spec["stats"] = stats
    if rng.random() < 0.6:
        spec["ranks"] = {k: rng.randint(0, 5) for k in rng.sample(list(RANKED_STATUSES), rng.randint(1, 3))}
    if rng.random() < 0.5:
        spec["archetypes"] = rng.sample(list(DEFENSE_ARCHETYPES), rng.randint(1, 3))
    if rng.random() < 0.3:
        spec["passives"] = [
            {"name": f"Passive {i}", "kind": rng.choice(["passive", "support"]), "cost": rng.randint(1, 6),
             "lines": ["[Combat Start] Gain 2 Protection"], **({"flavour": SPEC_FLAVOURS[(index + i) % len(SPEC_FLAVOURS)]} if (index + i) % 3 else {})}
            for i in range(rng.randint(1, 3))
        ]
    skills = []
    for i in range(rng.randint(0, 4)):
        coins = rng.randint(1, 4)
        tokens = []
        for _ in range(coins):
            if rng.random() < 0.5:
                tokens.append("none")
            else:
                tokens.append(f"{rng.choice(COIN_STATUSES).capitalize()}:{rng.randint(0, 5)}:{rng.randint(0, 4)}")
        skill = {"name": f"Skill {i}", "stats": f"{rng.randint(0, 9)}, {rng.randint(0, 4)}, {coins}, {rng.choice(['Slash', 'Blunt', 'Pierce'])}"}
        if any(t != "none" for t in tokens):
            skill["statuses"] = ",".join(tokens)
        trigger = rng.choice(TRIGGER_CHOICES)
        if trigger:
            skill["triggers"] = trigger
        skills.append(skill)
    if skills:
        spec["skills"] = skills
    return spec


def run_node(payload: dict, script: str = PARITY_JS) -> dict:
    done = subprocess.run(
        [NODE, script], input=json.dumps(payload), capture_output=True, text=True, timeout=120, cwd=ROOT,
    )
    if done.returncode != 0:
        raise AssertionError(f"node failed:\n{done.stderr}")
    return json.loads(done.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class SiteAgreesWithBot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(20261022)
        cls.specs = [random_spec(rng, i) for i in range(400)]
        cls.unicode_spec = {"v": 1, "name": "Éloïse 夜", "skills": [{"name": "Café Slash", "stats": "4, 2, 2, Slash"}]}
        cls.plain_code = encode_code(cls.specs[0], compress=False)
        cls.packed_code = encode_code(cls.unicode_spec, compress=True)
        cls.js = run_node({
            "specs": cls.specs,
            "encode": [cls.specs[1], cls.unicode_spec],
            "decode": [cls.plain_code, cls.packed_code, "LUGIC1Z.not-a-real-code", "hello"],
            "flavours": FLAVOURS + [None, 5, ["a"], "a\u00a0b\n\u00a0c"],
        })

    def test_prices_and_problems_match_check_build(self):
        for spec, got in zip(self.specs, self.js["checks"]):
            c = character_from_spec(spec)
            report = check_build(c)
            with self.subTest(spec=spec["name"]):
                self.assertEqual(report.spent, got["spent"])
                self.assertEqual(report.breakdown, got["breakdown"])
                self.assertEqual(report.problems, got["problems"])
                self.assertEqual(report.skill_problems, got["skillProblems"])
                for skill, js_rank in zip(c.skills.values(), got["skillRank"]):
                    self.assertEqual(skill_rank_problems(c.status_ranks, skill), js_rank)

    def test_what_the_site_exports_means_the_same_to_the_bot(self):
        for spec, got in zip(self.specs, self.js["checks"]):
            exported = got["toSpec"]
            c = character_from_spec(exported)
            report = check_build(c)
            with self.subTest(spec=spec["name"]):
                self.assertEqual(report.spent, got["spent"])
                self.assertEqual(report.breakdown, got["breakdown"])
                self.assertEqual(report.problems, got["problems"])
                self.assertEqual(report.skill_problems, got["skillProblems"])

    def test_codes_the_site_makes_open_in_the_bot(self):
        for spec, code in zip([self.specs[1], self.unicode_spec], self.js["codes"]):
            data, error = decode_code(code)
            self.assertIsNone(error)
            self.assertEqual(data, json.loads(json.dumps(spec)))
        self.assertTrue(self.js["codes"][0].startswith(("LUGIC1Z.", "LUGIC1.")))

    def test_codes_the_bot_makes_open_on_the_site(self):
        plain, packed, junk, hello = self.js["decoded"]
        self.assertEqual(plain["spec"], json.loads(json.dumps(self.specs[0])))
        self.assertEqual(packed["spec"], self.unicode_spec)
        self.assertIn("error", junk)
        self.assertIn("error", hello)

    def test_flavour_text_is_cleaned_the_same_on_the_site_and_in_the_bot(self):
        from game.passives import PASSIVE_FLAVOUR_MAX, clean_flavour
        texts = FLAVOURS + [None, 5, ["a"], "a\u00a0b\n\u00a0c"]
        for text, site in zip(texts, self.js["cleaned"]):
            with self.subTest(text=str(text)[:30]):
                self.assertEqual(clean_flavour(text), site)
                self.assertLessEqual(len(site), PASSIVE_FLAVOUR_MAX)
        self.assertEqual(self.js["cleaned"][0], "A prayer said so often\nthat it stopped being a request.")
        self.assertEqual(self.js["cleaned"][1], "padded\nand\nsplit")
        self.assertEqual(len(self.js["cleaned"][2]), PASSIVE_FLAVOUR_MAX)
        self.assertEqual(self.js["cleaned"][5], "")

    def test_the_flavour_text_the_site_exports_is_what_the_bot_reads(self):
        from game.passives import clean_flavour
        seen = 0
        for spec, got in zip(self.specs, self.js["checks"]):
            for sent, exported in zip(spec.get("passives") or [], got["toSpec"].get("passives") or []):
                with self.subTest(spec=spec["name"], passive=sent["name"]):
                    wanted = clean_flavour(sent.get("flavour"))
                    self.assertEqual(exported.get("flavour", ""), wanted)
                    self.assertEqual("flavour" in exported, bool(wanted))      # no flavour, no key: the code stays as small as it was
                    seen += 1
            parsed, error = read_spec(got["toSpec"])
            self.assertIsNone(error)
            for sent, read in zip(spec.get("passives") or [], parsed.params.get("passives") or []):
                self.assertEqual(read.get("flavour", ""), clean_flavour(sent.get("flavour")))
        self.assertGreater(seen, 40)           # the generator really did give passives flavour texts

    def test_needs_admin_flag_matches_the_bot(self):
        # The site tells the player whether an admin will have to answer. It has to say what the bot will do.
        for spec, got in zip(self.specs, self.js["checks"]):
            parsed, error = read_spec(got["toSpec"])
            self.assertIsNone(error)
            with self.subTest(spec=spec["name"]):
                self.assertEqual(needs_approval(parsed.params), got["toSpec"].get("stats") is not None or any(
                    got["toSpec"].get(k) for k in ("ranks", "archetypes", "tags", "passives")
                ))


@unittest.skipUnless(NODE, "node is not installed")
class FlaggedParts(unittest.TestCase):
    """The page turns a part of the form red when something in it breaks a rule. The engine says which part each message belongs to."""

    @classmethod
    def setUpClass(cls):
        cls.specs = {
            "clean": {"v": 1, "name": "X"},
            "badName": {"v": 1, "name": "a/b"},
            "longTag": {"v": 1, "name": "X", "tags": ["x" * 31]},
            "overBudget": {"v": 1, "name": "X", "pools": {"bp": 5}, "stats": {"hp": 170, "power": 14}},
            "badResistance": {"v": 1, "name": "X", "stats": {"resist": {"burn": 0.6}}},
            "missingRank": {"v": 1, "name": "X", "skills": [{"name": "Shot", "stats": "2, 1, 3, Pierce", "statuses": "Poise:2:2,none,none"}]},
            "tooStrongForItsCoins": {"v": 1, "name": "X", "skills": [{"name": "Wide", "stats": "4, 2, 3, Slash"}, {"name": "Tall", "stats": "4, 2, 1, Slash"}]},
            "examples": {"v": 1, "name": "X", "skills": [{
                "name": item["name"], "stats": f'{item["base"]}, {item["coin"]}, {item["coins"]}, {item["type"]}', "triggers": item["triggers"],
            } for item in builder_rules.SKILL_EXAMPLES]},
        }
        cls.js = run_node({
            "specs": list(cls.specs.values()),
            "strength": [["haste", 3, 1], ["Haste", 1, 3], ["haste", 0, 0], ["bind", 4, 1], ["Power Down", 2, 5], ["burn", 3, 2]],
            "tagLists": [["Dawn Office", "dawn   office", "N Corp"], ["", "  "], ["A", "a", "B", "b"]],
        }, FLAGS_JS)
        cls.review = dict(zip(cls.specs, cls.js["reviews"]))

    def test_a_clean_build_marks_nothing(self):
        self.assertEqual(self.review["clean"]["parts"], {})
        self.assertTrue(self.review["clean"]["ok"])

    def test_each_kind_of_mistake_lands_on_its_own_part(self):
        self.assertEqual(list(self.review["badName"]["parts"]), ["sec-identity"])
        self.assertEqual(list(self.review["longTag"]["parts"]), ["sec-group-tags"])
        self.assertEqual(list(self.review["badResistance"]["parts"]), ["sec-resistances"])

    def test_a_build_that_costs_too_much_marks_the_stats_and_the_points(self):
        parts = self.review["overBudget"]["parts"]
        self.assertEqual(sorted(parts), ["sec-stats", "sec-your-points"])
        self.assertEqual(len(parts["sec-your-points"]), 1)
        self.assertRegex(parts["sec-your-points"][0], r"^This build costs ")
        self.assertGreater(len(parts["sec-stats"]), 1)

    def test_every_problem_of_the_bots_check_is_placed_on_some_part(self):
        for name, review in self.review.items():
            with self.subTest(build=name):
                self.assertEqual(len(review["problems"]), len(review["problemParts"]))
                for ids in review["problemParts"]:
                    self.assertTrue(ids)
                    for part_id in ids:
                        self.assertIn(part_id, review["parts"])

    def test_a_message_shown_inside_a_skill_is_flagged_but_not_repeated_in_the_banner(self):
        review = self.review["missingRank"]
        self.assertFalse(review["ok"])
        self.assertEqual(list(review["parts"]), ["sec-skills"])
        self.assertRegex(review["parts"]["sec-skills"][0], r"needs a Poise rank")
        self.assertEqual(review["own"], {})

    def test_a_skill_over_the_limit_for_its_coins_is_flagged_with_the_bots_own_words(self):
        review = self.review["tooStrongForItsCoins"]
        self.assertFalse(review["ok"])
        mine = review["parts"]["sec-skills"]
        self.assertEqual(len(mine), 1)                                                # the 1-coin skill at 4/2 is fine
        self.assertIn(skill_power_problem(6, 4, 2, 3), mine[0])                        # same sentence the skill form says
        self.assertTrue(mine[0].endswith(" A higher Power stat raises the limit."))
        self.assertEqual(review["problems"], [])                                       # check_build's own list keeps the plain ceiling (saved skills are grandfathered)

    def test_the_example_skills_pass_the_sites_check_for_a_starting_character(self):
        self.assertTrue(self.review["examples"]["ok"], self.review["examples"]["parts"])

    def test_a_status_with_no_keyword_is_as_strong_as_the_larger_of_its_two_numbers(self):
        keywordless = [(got["keywordless"], got["strength"]) for got in self.js["strength"]]
        self.assertEqual(keywordless, [(True, 3), (True, 3), (True, 0), (True, 4), (True, 2), (False, 3)])
        # Bind and Power Down keep their strength in Potency: a Count next to it is not the strength
        self.assertEqual(self.js["strength"][4]["strength"], 2)

    def test_a_tag_written_twice_is_exported_once_like_the_bot_counts_it(self):
        self.assertEqual(self.js["exported"], [["Dawn Office", "N Corp"], None, ["A", "B"]])


class StatusGuide(unittest.TestCase):
    def test_every_status_the_builder_offers_is_in_the_list_with_text(self):
        rules = builder_rules.rules()
        offered = set(rules["ranks"]["statuses"]) | set(rules["nonKeyword"]["oneTurn"]) | set(rules["otherStatuses"])
        listed = {s["key"] for s in rules["statusGuide"]}
        self.assertEqual(offered - listed, set())
        for s in rules["statusGuide"]:
            with self.subTest(status=s["key"]):
                self.assertTrue(s["title"] and s["summary"] and s["body"])
                self.assertIn(s["tone"], ("positive", "negative", "neutral"))
                self.assertIn(s["group"], ("keyword", "buff", "debuff", "other"))
        self.assertEqual(len(listed), len(rules["statusGuide"]))   # no status twice

    def test_each_keyword_status_has_its_sin_and_the_text_is_the_bots_own(self):
        from game.help_content import STATUS_EFFECTS
        bot = {e.key: e for e in STATUS_EFFECTS}
        by_key = {s["key"]: s for s in builder_rules.rules()["statusGuide"]}
        self.assertEqual({k: by_key[k]["sin"] for k in ("burn", "bleed", "tremor", "rupture", "sinking", "poise", "charge")},
                         {"burn": "wrath", "bleed": "lust", "tremor": "sloth", "rupture": "gluttony", "sinking": "gloom", "poise": "pride", "charge": "envy"})
        self.assertEqual(by_key["burn"]["body"], bot["burn"].body)
        self.assertNotIn("sin", by_key["haste"])


class StatusShortForm(unittest.TestCase):
    """The site shows each status in three short slots (when, does, then). They must stay short, and must not say anything the full text does not."""

    def setUp(self):
        self.rules = builder_rules.rules()
        self.guide = {s["key"]: s for s in self.rules["statusGuide"]}

    def test_every_status_has_the_three_short_slots(self):
        for key, status in self.guide.items():
            with self.subTest(status=key):
                self.assertEqual(sorted(status["quick"]), ["does", "then", "when"])
                for slot, text in status["quick"].items():
                    self.assertTrue(text.strip(), slot)
                    self.assertLessEqual(len(text), 48, f"{key} {slot} is too long to scan")

    def test_every_number_in_the_short_form_is_in_the_full_text(self):
        for key, status in self.guide.items():
            full = set(re.findall(r"\d+", status["summary"] + " " + status["body"]))
            for slot, text in status["quick"].items():
                for number in re.findall(r"\d+", text):
                    with self.subTest(status=key, slot=slot, number=number):
                        self.assertIn(number, full)

    def test_each_status_is_listed_once_as_keyword_pair_or_other(self):
        pair_keys = []
        for pair in self.rules["statusPairs"]:
            pair_keys += [pair["up"], pair["down"]] + pair.get("downExtra", [])
            self.assertEqual(self.guide[pair["up"]]["tone"], "positive", pair)
            self.assertEqual(self.guide[pair["down"]]["tone"], "negative", pair)
        self.assertEqual(len(pair_keys), len(set(pair_keys)))
        for key in pair_keys:
            self.assertIn(key, self.guide)
            self.assertNotEqual(self.guide[key]["group"], "keyword")

    def test_the_colour_key_is_the_three_tones_and_nothing_else(self):
        key = self.rules["colorKey"]
        self.assertEqual(list(key), ["tones"])                      # no Sin colour cards, no explanation lines
        self.assertEqual({t["tone"] for t in key["tones"]}, {"positive", "negative", "neutral"})
        for entry in key["tones"]:
            self.assertEqual(sorted(entry), ["color", "means", "tone"], entry)
            self.assertTrue(entry["color"] and entry["means"], entry)

    def test_each_keyword_status_still_knows_its_sin_for_the_chip_in_its_row(self):
        self.assertEqual({k: s["sin"] for k, s in self.guide.items() if s["group"] == "keyword"},
                         {"burn": "wrath", "bleed": "lust", "tremor": "sloth", "rupture": "gluttony", "sinking": "gloom", "poise": "pride", "charge": "envy"})

    def test_the_potency_and_count_line_agrees_with_the_bot(self):
        self.assertIn("99", self.rules["potencyCount"])
        self.assertIn("99", self.rules["stackingNote"])


class Glossary(unittest.TestCase):
    """What an underlined word says when it is pointed at. Statuses use the status guide; the rest is the glossary."""

    def test_every_number_in_the_glossary_is_in_the_bots_help_text(self):
        help_text = " ".join(f"{e.title} {e.summary} {e.body}" for *_, entries in HELP_CATEGORIES for e in entries)
        known = set(re.findall(r"\d+(?:\.\d+)?", help_text))
        for key, text in builder_rules.rules()["glossary"].items():
            for number in re.findall(r"\d+(?:\.\d+)?", text):
                with self.subTest(word=key, number=number):
                    self.assertIn(number, known)

    def test_the_numbers_come_from_the_bots_constants(self):
        glossary = builder_rules.rules()["glossary"]
        self.assertIn(f"{bp.OFFDEF_PERCENT_PER_STEP}% more damage", glossary["offense_level"])
        self.assertIn(f"{bp.OFFDEF_PERCENT_PER_STEP}% less damage", glossary["defense_level"])
        self.assertIn(str(SANITY_LOW_MORALE), glossary["sanity"])
        self.assertIn(str(SANITY_PANIC), glossary["sanity"])

    def test_every_glossary_entry_is_short_enough_to_read_at_a_glance(self):
        for key, text in builder_rules.rules()["glossary"].items():
            self.assertLessEqual(len(text), 100, key)


class SkillExamples(unittest.TestCase):
    def test_every_example_skill_is_a_valid_build_for_a_starting_character(self):
        for item in builder_rules.SKILL_EXAMPLES:
            with self.subTest(skill=item["name"]):
                spec = {"v": 1, "name": "Tester", "skills": [{
                    "name": item["name"], "stats": f'{item["base"]}, {item["coin"]}, {item["coins"]}, {item["type"]}',
                    "statuses": "none", "triggers": item["triggers"],
                }]}
                c = character_from_spec(spec)
                report = check_build(c)
                self.assertTrue(report.ok, report.problems)
                self.assertEqual(report.skill_problems, [])
                self.assertLessEqual(len(item["blurb"]), 30)
                # and the skill form's stricter limit for its coin count (more coins, lower numbers)
                self.assertEqual(skill_coin_excess(c.power, item["base"], item["coin"], item["coins"]), 0, item["name"])


@unittest.skipUnless(NODE, "node is not installed")
class Preview(unittest.TestCase):
    def run_preview(self, lines, skills, **extra):
        done = subprocess.run([NODE, os.path.join(ROOT, "tests", "preview_check.js")], input=json.dumps({"lines": lines, "skills": skills, **extra}),
                              capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    def test_timing_tags_are_coloured_the_way_the_wiki_does(self):
        timings = ["On Use", "Clash Win", "Clash Lose", "On Hit", "Combat Start", "Before Attack", "Turn End", "Counter", "Guard", "Unclashable"]
        classes = dict(zip(timings, self.run_preview([], [], timings=timings)["classes"]))
        self.assertEqual(classes["On Use"], "use")
        self.assertEqual(classes["Clash Win"], "clashwin")
        self.assertEqual(classes["Clash Lose"], "clashlose")
        for green in ("On Hit", "Combat Start", "Before Attack", "Turn End"):
            self.assertEqual(classes[green], "hit", green)
        for tag in ("Counter", "Guard", "Unclashable"):
            self.assertEqual(classes[tag], "flag", tag)

    def test_status_words_take_the_tone_of_the_status_guide(self):
        guide = builder_rules.rules()["statusGuide"]
        keys = [s["key"] for s in guide]
        tones = self.run_preview([], [], tones=keys)["tones"]
        self.assertEqual(dict(zip(keys, tones)), {s["key"]: s["tone"] for s in guide})
        self.assertEqual(self.run_preview([], [], tones=["not_a_status"])["tones"], ["negative"])

    def test_tags_and_words_get_their_colours(self):
        out = self.run_preview(["[On Use] If the target has 3+ Burn, Deal +3 Slash damage", "[Before Use] Gain 2 Haste", "[On Hit] Inflict 2 Power Down"], [])
        first, second, third = out["tokens"]
        self.assertEqual(first[0], {"kind": "tag", "text": "[On Use]", "cls": "use"})
        self.assertIn({"kind": "status", "key": "burn", "text": "Burn"}, first)
        self.assertIn({"kind": "damage", "key": "slash", "text": "Slash"}, first)
        self.assertEqual(second[0]["cls"], "hit")        # every timing but On Use and the two clash results is green, as on the wiki
        self.assertIn({"kind": "status", "key": "haste", "text": "Haste"}, second)
        self.assertEqual(third[0]["cls"], "hit")
        self.assertIn({"kind": "status", "key": "power_down", "text": "Power Down"}, third)

    def test_a_status_is_coloured_but_the_plain_words_round_it_are_not(self):
        # "Gain 2 Protection": only Protection is a buff. "Gain 2" is plain text, not yellow.
        tokens = self.run_preview(["[Ally Is Hit] Gain 2 Protection", "[On Hit] Inflict 3 Burn"], [])["tokens"]
        self.assertEqual(tokens[0][1], {"kind": "text", "text": "Gain 2 "})
        self.assertEqual(tokens[0][2], {"kind": "status", "key": "protection", "text": "Protection"})
        self.assertEqual(tokens[1][1], {"kind": "text", "text": "Inflict 3 "})

    def test_effects_that_help_or_hurt_carry_their_tone_like_a_status_does(self):
        lines = [
            "[Turn Start] Heal 5 HP", "[Turn Start] Heal 10% max HP", "[On Use] Deal +10% damage", "[On Use] Coin Power +2", "[On Use] Final Power +3",
            "[Turn End] Gain 5 Sanity", "[Turn End] Gain 4 Shield", "[Turn End] Gain Offense Level +1", "[Turn End] Nullify the next lethal damage",
            "[Turn End] Lose 5 Sanity", "[On Hit] Inflict Defense Level -2",
        ]
        out = self.run_preview(lines, [])["tokens"]
        expect = ["positive"] * 9 + ["negative"] * 2
        for line, tokens, tone in zip(lines, out, expect):
            with self.subTest(line=line):
                toned = [t for t in tokens[1:] if t.get("tone")]
                self.assertTrue(toned, tokens)
                self.assertEqual({t["tone"] for t in toned}, {tone})
                self.assertEqual("".join(t["text"] for t in toned), line.split("] ", 1)[1])    # the whole phrase, nothing else

    def test_conditions_with_a_good_or_bad_word_are_coloured_and_the_rest_is_not(self):
        tokens = self.run_preview(["[On Use] If the target has 5+ negative effects, Deal +10% damage", "[Combat Start] If this unit is Low Morale, Coin Power +1"], [])["tokens"]
        first, second = tokens
        self.assertIn({"kind": "text", "text": "negative effects", "tone": "negative"}, first)
        self.assertIn({"kind": "text", "text": "If the target has 5+ "}, first)
        self.assertIn({"kind": "text", "text": "Low Morale", "tone": "negative"}, second)
        self.assertIn({"kind": "text", "text": "Coin Power +1", "tone": "positive"}, second)

    def test_a_status_inside_an_effect_keeps_its_own_tone(self):
        tokens = self.run_preview(["[On Use] Deal +10% damage per 5 Burn on the target (max 30%)"], [])["tokens"][0]
        self.assertIn({"kind": "status", "key": "burn", "text": "Burn"}, tokens)
        self.assertIn({"kind": "text", "text": "Deal +10% damage", "tone": "positive"}, tokens)

    def test_the_tones_live_in_the_pickers_data_and_every_toned_effect_is_coloured_from_its_own_wording(self):
        effects = builder_rules.rules()["lines"]["effects"]
        toned = [e for e in effects if e.get("tone")]
        self.assertEqual({e["tone"] for e in toned}, {"positive", "negative"})
        self.assertEqual({e["id"] for e in effects if not e.get("tone")},
                         {"inflict_count", "inflict_bare", "inflict_both", "gain_count", "gain_bare", "gain_both", "remove", "followup", "assist"})   # the status carries these
        lines = []
        for e in toned:
            values = {}
            for p in e["params"]:
                default = p.get("default", p["options"][0] if p.get("options") else 1)
                values[p["name"]] = default["value"] if isinstance(default, dict) else default
            if e["id"] == "consume":
                values["status"] = "Charge"
            if e["id"].endswith("_per"):
                values.update(per=5, max=3, basis="Count", status="Burn")
            lines.append("[On Use] " + e["text"].format(**values))
        out = self.run_preview(lines, [])["tokens"]
        for e, line, tokens in zip(toned, lines, out):
            with self.subTest(effect=e["id"], line=line):
                tones = {t["tone"] for t in tokens if t.get("tone")}
                self.assertEqual(tones, {e["tone"]})
                if not (e["id"].endswith("_per") or e["id"] == "consume"):
                    self.assertEqual("".join(t["text"] for t in tokens[1:] if t.get("tone")), line.split("] ", 1)[1])
        conditions = {c["id"]: c.get("tone") for c in builder_rules.rules()["lines"]["conditions"]}
        self.assertEqual({k for k, v in conditions.items() if v}, {"negatives", "morale"})

    def test_every_underlined_word_has_a_definition(self):
        rules = builder_rules.rules()
        asks = [["status", s["key"]] for s in rules["statusGuide"]]
        asks += [["damage", k] for k in rules["skills"]["damageTypes"]]
        asks += [["stat", k] for k in ("hp", "speed", "sanity", "offense_level", "defense_level")]
        flags = [k for k in rules["skills"]["flagTags"] if k != "supermove"]           # the picker has no help line for Supermove
        asks += [["flag", k] for k in flags]
        asks += [["timing", t["key"]] for t in rules["lines"]["timings"]]
        found = self.run_preview([], [], definitions=asks)["definitions"]
        for (kind, key), d in zip(asks, found):
            with self.subTest(kind=kind, key=key):
                self.assertIsNotNone(d)
                self.assertTrue(d["title"])
                self.assertTrue(d["lines"])            # the box is a list of "- line"s, like the wiki's
                for line in d["lines"]:
                    self.assertRegex(line, r"^[A-Z0-9*`\[(]")      # whole sentences, never a piece cut off mid-sentence (a lower-case start would be one)
        by_key = {(kind, key): d for (kind, key), d in zip(asks, found)}
        burn = by_key[("status", "burn")]
        body = next(s["body"] for s in rules["statusGuide"] if s["key"] == "burn")
        self.assertEqual(" ".join(burn["lines"]), body)          # the bot's own text, only cut into lines
        self.assertEqual((burn["title"], burn["icon"], burn["tone"]), ("Burn", "burn", "negative"))
        self.assertEqual(by_key[("status", "poise")]["tone"], "positive")
        self.assertEqual(by_key[("timing", "On Use")]["cls"], "use")
        self.assertEqual(by_key[("timing", "On Use")]["title"], "[On Use]")
        self.assertEqual(by_key[("timing", "Clash Win")]["cls"], "clashwin")
        self.assertEqual(by_key[("timing", "Clash Lose")]["cls"], "clashlose")
        self.assertEqual(by_key[("timing", "Before Attack")]["cls"], "hit")
        self.assertEqual((by_key[("flag", "counter")]["title"], by_key[("flag", "counter")]["cls"]), ("[Counter]", "flag"))
        self.assertEqual(self.run_preview([], [], definitions=[["status", "nope"], ["flag", "supermove"], ["timing", "nope"]])["definitions"], [None, None, None])

    def test_a_timing_or_status_in_running_text_is_found_and_coloured(self):
        text = ("This changes the hit before the coins are tossed, so at [On Kill] it would do nothing. "
                "Use Before Use, On Use, Clash Start or Before Attack (or Coin Start). Poise needs a Poise rank; Burn hurts.")
        tokens = self.run_preview([], [], prose=[text])["prose"][0]
        self.assertEqual("".join(t["text"] for t in tokens), text)            # nothing lost, nothing added
        found = [(t["kind"], t["text"], t.get("cls")) for t in tokens if t["kind"] != "text"]
        self.assertEqual(found, [
            ("tag", "[On Kill]", "hit"), ("timing", "Before Use", "hit"), ("timing", "On Use", "use"), ("timing", "Clash Start", "hit"),
            ("timing", "Before Attack", "hit"), ("timing", "Coin Start", "hit"),
            ("status", "Poise", None), ("status", "Poise", None), ("status", "Burn", None),
        ])
        self.assertEqual([t["key"] for t in tokens if t["kind"] == "status"], ["poise", "poise", "burn"])
        self.assertEqual([t["key"] for t in tokens if t["kind"] == "tag"], ["on kill"])

    def test_running_text_leaves_ordinary_words_and_unknown_brackets_alone(self):
        text = "The unit is on fire, so Burning is not a status, but burn is a word. [Not A Tag] and [x] stay plain; [Counter] is a skill-type tag."
        tokens = self.run_preview([], [], prose=[text])["prose"][0]
        self.assertEqual("".join(t["text"] for t in tokens), text)
        found = [(t["kind"], t["text"]) for t in tokens if t["kind"] != "text"]
        self.assertEqual(found, [("tag", "[Counter]")])          # only the real tag: a lower-case word or a longer word is not a status
        self.assertEqual([t["cls"] for t in tokens if t["kind"] == "tag"], ["flag"])
        self.assertEqual(self.run_preview([], [], prose=["", None])["prose"], [[], []])

    def test_a_frame_picked_by_hand_beats_the_automatic_one(self):
        def skill(frame=None):
            item = {"name": "S", "base": 4, "coin": 2, "coins": 2, "type": "slash", "statuses": [{"name": "burn", "pot": 1, "cnt": 1}, {"name": "", "pot": 0, "cnt": 0}], "triggers": ""}
            if frame is not None:
                item["frame"] = frame
            return item
        skills = [skill(), skill({"sin": "envy", "tier": 3}), skill({"sin": "none"}), skill({"sin": "sloth", "tier": 3}), skill({"sin": "nope", "tier": "x"}), skill({"sin": "def", "tier": 1})]
        out = self.run_preview([], skills)
        auto, envy3, plain, no_such_tier, nonsense, defense = [m["frame"] for m in out["models"]]
        self.assertEqual(auto, {"sin": "wrath", "tier": 1, "file": "borders/wrath1.png"})
        self.assertEqual(envy3, {"sin": "envy", "tier": 3, "file": "borders/envy3.png", "picked": True})
        self.assertEqual((plain["sin"], plain["file"], plain["picked"]), (None, None, True))      # the plain ring
        self.assertEqual(defense["file"], "borders/def1.png")
        self.assertEqual(no_such_tier, auto)         # Sloth has no tier 3, and a made-up frame is no frame: the automatic one shows
        self.assertEqual(nonsense, auto)
        self.assertEqual(out["autoFrames"], [m["frame"] for m in out["models"][:1]] * 6)      # the automatic frame ignores whatever was picked

    def test_a_passive_is_laid_out_with_its_lines_and_an_optional_flavour_text(self):
        passives = [
            {"name": "Grace of God", "kind": "support", "cost": 2, "lines": ["[Combat Start] Gain 2 Protection", "", "  [On Hit] Inflict 1 Burn  "], "flavour": "A prayer said so often\nthat it stopped being a request."},
            {"name": "  ", "kind": "nonsense", "cost": "x", "lines": None},
            {"name": "Plain", "kind": "passive", "cost": 1, "lines": ["[Turn End] Heal 5 HP"], "flavour": "   "},
        ]
        support, odd, plain = self.run_preview([], [], passives=passives)["passives"]
        self.assertEqual((support["name"], support["kind"], support["cost"]), ("Grace of God", "support", 2))
        self.assertEqual(len(support["lines"]), 2)                  # an empty line is not a line
        self.assertEqual(support["lines"][0][0], {"kind": "tag", "text": "[Combat Start]", "cls": "hit"})
        self.assertIn({"kind": "status", "key": "protection", "text": "Protection"}, support["lines"][0])
        self.assertEqual(support["flavour"], ["A prayer said so often", "that it stopped being a request."])      # one paragraph for each line written
        self.assertEqual((odd["name"], odd["kind"], odd["cost"], odd["lines"], odd["flavour"]), ("Unnamed passive", "passive", 0, [], []))
        self.assertEqual(plain["flavour"], [])                       # blank is no flavour at all, so nothing is drawn

    def test_flavour_text_is_trimmed_and_cut_to_its_limit(self):
        limit = self.run_preview([], [])["flavourMax"]
        self.assertEqual(limit, 400)
        out = self.run_preview([], [], flavours=["  one  ", "a\r\nb\r\n\r\nc", "x" * 1000, "", None, "\n\n"])["flavours"]
        self.assertEqual(out[0], ["one"])
        self.assertEqual(out[1], ["a", "b", "c"])           # Windows line breaks too, and an empty line is dropped
        self.assertEqual(out[2], ["x" * limit])
        self.assertEqual(out[3:], [[], [], []])

    def test_every_frame_can_be_picked_and_the_list_matches_the_images(self):
        choices = self.run_preview([], [])["frameChoices"]
        on_disk = set(os.listdir(os.path.join(ROOT, "docs", "builder", "borders")))
        self.assertEqual({c["file"].split("/")[1] for c in choices}, on_disk)
        self.assertEqual(len(choices), len(on_disk))
        self.assertEqual(choices[0], {"sin": "def", "tier": 1, "label": "Defense 1", "file": "borders/def1.png"})      # defense first, then the Sins
        self.assertEqual([c["label"] for c in choices if c["sin"] == "envy"], ["Envy 1", "Envy 2", "Envy 3"])
        self.assertEqual([c["label"] for c in choices if c["sin"] == "pride"], ["Pride 1", "Pride 2", "Pride 3"])
        self.assertEqual([c["sin"] for c in choices if c["tier"] == 1], ["def", "wrath", "lust", "sloth", "gluttony", "gloom", "pride", "envy"])

    def test_every_status_name_is_recognised(self):
        rules = builder_rules.rules()
        names = rules["ranks"]["statuses"] + rules["nonKeyword"]["oneTurn"] + rules["otherStatuses"]
        lines = ["Inflict " + n.replace("_", " ").title() for n in names]
        out = self.run_preview(lines, [])
        for name, tokens in zip(names, out["tokens"]):
            self.assertTrue(any(t["kind"] == "status" and t["key"] == name for t in tokens), name)

    def test_skill_type_tags_are_gold_and_leave_the_text_lines(self):
        skill = {"name": "S", "base": 4, "coin": 2, "coins": 2, "type": "blunt", "statuses": [], "triggers": "[Counter]\n[On Use] Coin Power +1"}
        model = self.run_preview([], [skill])["models"][0]
        self.assertEqual([f["text"] for f in model["flags"]], ["[Counter]"])
        self.assertEqual(model["flags"][0]["cls"], "flag")
        self.assertEqual(len(model["general"]), 1)

    def test_coin_lines_land_on_their_coin_and_statuses_read_like_the_bot(self):
        skill = {
            "name": "S", "base": 4, "coin": 2, "coins": 3, "type": "slash",
            "statuses": [{"name": "bleed", "pot": 3, "cnt": 0}, {"name": "", "pot": 0, "cnt": 0}, {"name": "burn", "pot": 2, "cnt": 3}],
            "triggers": ":Coin2: [On Hit] Inflict +2 Burn Count\n:Coin9: [On Hit] Coin Power +1",
        }
        model = self.run_preview([], [skill])["models"][0]
        text = lambda tokens: "".join(t["text"] for t in tokens)
        rows = [[text(line) for line in row["lines"]] for row in model["rows"]]
        self.assertEqual(rows[0], ["[On Hit]Inflict 3 Bleed"])
        self.assertEqual(rows[1], ["[On Hit]Inflict +2 Burn Count"])
        self.assertEqual(rows[2], ["[On Hit]Inflict Burn Potency 2, Count 3"])
        self.assertEqual(len(model["general"]), 1)      # a coin that does not exist falls back to the whole skill

    def frame_of(self, coins, statuses, triggers=""):
        coin = [{"name": n, "pot": 1, "cnt": 1} if n else {"name": "", "pot": 0, "cnt": 0} for n in statuses]
        skill = {"name": "S", "base": 4, "coin": 2, "coins": coins, "type": "slash", "statuses": coin, "triggers": triggers}
        return self.run_preview([], [skill])["models"][0]["frame"]

    def test_the_frame_follows_the_status_and_the_number_of_coins(self):
        self.assertEqual(self.frame_of(2, ["burn", ""]), {"sin": "wrath", "tier": 1, "file": "borders/wrath1.png"})
        self.assertEqual(self.frame_of(2, ["charge", ""])["file"], "borders/envy1.png")
        self.assertEqual(self.frame_of(3, ["", "sinking", ""])["file"], "borders/gloom2.png")
        self.assertEqual(self.frame_of(4, ["rupture", "", "", ""])["file"], "borders/gluttony3.png")
        self.assertEqual(self.frame_of(3, ["", "", ""], "[Guard]"), {"sin": "def", "tier": 1, "file": "borders/def1.png"})
        self.assertEqual(self.frame_of(2, ["", ""]), {"sin": None, "tier": 1, "file": None})

    def test_a_tier_with_no_image_uses_the_best_one_below_it(self):
        self.assertEqual(self.frame_of(4, ["tremor", "", "", ""])["file"], "borders/sloth2.png")   # Sloth has no tier 3 yet
        self.assertEqual(self.frame_of(4, ["burn", "", "", ""])["file"], "borders/wrath3.png")
        self.assertEqual(self.frame_of(4, ["poise", "", "", ""])["file"], "borders/pride3.png")

    def test_the_status_used_most_decides_the_sin_and_trigger_lines_count(self):
        self.assertEqual(self.frame_of(2, ["bleed", "rupture"], ":Coin1: [On Hit] Inflict +2 Rupture Count")["sin"], "gluttony")
        self.assertEqual(self.frame_of(2, ["bleed", ""], "[On Use] If the target has 3+ Charge, Deal +3 damage\n[On Hit] Gain 1 Charge\n[On Hit] Gain +1 Charge Count")["sin"], "envy")

    def test_every_frame_in_the_list_exists_and_every_image_is_in_the_list(self):
        tiers = self.run_preview([], [])["frameTiers"]
        listed = {f"{sin}{n}.png" for sin, ns in tiers.items() for n in ns}
        on_disk = set(os.listdir(os.path.join(ROOT, "docs", "builder", "borders")))
        self.assertEqual(listed, on_disk)

    def test_the_picture_covers_the_opening_and_cannot_be_dragged_off_it(self):
        def arts(*items):
            done = subprocess.run([NODE, os.path.join(ROOT, "tests", "preview_check.js")], input=json.dumps({"lines": [], "skills": [], "arts": list(items)}),
                                  capture_output=True, text=True, check=True)
            return json.loads(done.stdout)["arts"]
        square, wide, tall, zoomed, wild, junk = arts(
            {"src": "x", "a": 1, "x": 0, "y": 0, "zoom": 1},
            {"src": "x", "a": 2, "x": 0, "y": 0, "zoom": 1},
            {"src": "x", "a": 0.5, "x": 0, "y": 0, "zoom": 1},
            {"src": "x", "a": 1, "x": 0.25, "y": -0.25, "zoom": 2},
            {"src": "x", "a": 1, "x": 9, "y": -9, "zoom": 99},
            {"src": "x", "a": "no", "x": "no", "y": None, "zoom": "no"},
        )
        self.assertEqual(square["style"], {"width": 100, "height": 100, "left": 0, "top": 0})
        self.assertEqual(wide["style"], {"width": 200, "height": 100, "left": -50, "top": 0})      # wide: fills the height, sides cropped
        self.assertEqual(tall["style"], {"width": 100, "height": 200, "left": 0, "top": -50})
        self.assertEqual(zoomed["style"], {"width": 200, "height": 200, "left": -25, "top": -75})
        self.assertEqual((wild["clamped"]["zoom"], wild["clamped"]["x"], wild["clamped"]["y"]), (4, 1.5, -1.5))   # never past the edge
        self.assertEqual(junk["clamped"]["zoom"], 1)
        self.assertEqual((junk["clamped"]["x"], junk["clamped"]["y"]), (0, 0))

    def test_a_picture_shown_whole_in_its_own_shape_is_only_ever_resized(self):
        # the portrait's opening has the picture's own shape: zoom 1 shows all of it, edge to edge, whatever its shape
        def arts(*items):
            done = subprocess.run([NODE, os.path.join(ROOT, "tests", "preview_check.js")], input=json.dumps({"lines": [], "skills": [], "arts": list(items)}),
                                  capture_output=True, text=True, check=True)
            return json.loads(done.stdout)["arts"]
        shapes = [0.2, 0.375, 0.75, 1, 1.333, 2, 3.5]
        for shape, out in zip(shapes, arts(*[{"src": "x", "a": a, "x": 0, "y": 0, "zoom": 1, "open": a} for a in shapes])):
            with self.subTest(shape=shape):
                self.assertEqual(out["style"], {"width": 100, "height": 100, "left": 0, "top": 0})
                self.assertEqual((out["clamped"]["x"], out["clamped"]["y"]), (0, 0))

    def test_another_shape_cuts_the_picture_and_zoom_and_move_choose_what_shows(self):
        def arts(*items):
            done = subprocess.run([NODE, os.path.join(ROOT, "tests", "preview_check.js")], input=json.dumps({"lines": [], "skills": [], "arts": list(items)}),
                                  capture_output=True, text=True, check=True)
            return json.loads(done.stdout)["arts"]
        wide_in_tall, tall_in_wide, zoomed, dragged = arts(
            {"src": "x", "a": 2, "x": 0, "y": 0, "zoom": 1, "open": 0.75},       # a wide picture in a 3:4 opening: fills the height, the sides are cut
            {"src": "x", "a": 0.5, "x": 0, "y": 0, "zoom": 1, "open": 2},        # a tall picture in a wide opening: fills the width, top and bottom are cut
            {"src": "x", "a": 0.375, "x": 0, "y": 0, "zoom": 2, "open": 0.375},  # the same shape, zoomed in: twice the size, the middle shows
            {"src": "x", "a": 2, "x": 99, "y": 99, "zoom": 1, "open": 0.75},     # dragged far past the edge: stops at the edge
        )
        self.assertAlmostEqual(wide_in_tall["style"]["width"], 100 * 2 / 0.75)
        self.assertEqual((wide_in_tall["style"]["height"], wide_in_tall["style"]["top"]), (100, 0))
        self.assertAlmostEqual(wide_in_tall["style"]["left"], 50 - 50 * 2 / 0.75)
        self.assertEqual((tall_in_wide["style"]["width"], tall_in_wide["style"]["left"]), (100, 0))
        self.assertAlmostEqual(tall_in_wide["style"]["height"], 100 * 2 / 0.5)      # 100 * (opening shape / picture shape) = 400
        self.assertEqual(zoomed["style"], {"width": 200, "height": 200, "left": -50, "top": -50})
        self.assertAlmostEqual(dragged["clamped"]["x"], (2 / 0.75 - 1) / 2)       # it can move only as far as the picture reaches
        self.assertEqual(dragged["clamped"]["y"], 0)                              # and not at all where the picture already fits

    def test_an_empty_or_odd_skill_still_gives_a_model(self):
        skill = {"name": "  ", "base": "x", "coin": None, "coins": 99, "type": "pierce", "statuses": [], "triggers": None}
        model = self.run_preview([], [skill])["models"][0]
        self.assertEqual(model["name"], "Unnamed skill")
        self.assertEqual(model["coins"], builder_rules.MAX_COINS)
        self.assertEqual((model["base"], model["coin"]), (0, 0))


class WikiLook(unittest.TestCase):
    """The page uses the colours the Limbus Company wiki uses, taken from the wiki pages' own inline styles."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "docs", "builder", "style.css"), encoding="utf-8") as handle:
            cls.css = handle.read()
        with open(os.path.join(ROOT, "docs", "builder", "index.html"), encoding="utf-8") as handle:
            cls.html = handle.read()
        with open(os.path.join(ROOT, "docs", "builder", "app.js"), encoding="utf-8") as handle:
            cls.app = handle.read()

    def token(self, name):
        found = re.search(r"--" + re.escape(name) + r":\s*(#[0-9a-fA-F]{6})", self.css)
        self.assertIsNotNone(found, name)
        return found.group(1).lower()

    def test_the_wiki_palette_is_the_one_in_the_stylesheet(self):
        expected = {
            "wiki-positive": "#fac400", "wiki-negative": "#e20000", "wiki-neutral": "#9f6a3a",
            "wiki-tag-use": "#26cfff", "wiki-tag-hit": "#94f140", "wiki-tag-clash-win": "#ff7e00", "wiki-tag-clash-lose": "#e30000",
            "wiki-cream": "#eecea4", "wiki-bar": "#810000", "wiki-weight": "#fac700",
        }
        for name, colour in expected.items():
            with self.subTest(token=name):
                self.assertEqual(self.token(name), colour)

    def test_every_sin_has_its_dark_and_light_banner_colour(self):
        expected = {
            "wrath": ("#fe0000", "#520000"), "lust": ("#fb6500", "#521b00"), "sloth": ("#f7c729", "#4f3f06"),
            "gluttony": ("#9dfe00", "#2f5300"), "gloom": ("#0dc1eb", "#013d4b"), "pride": ("#0049d3", "#001142"),
            "none": ("#9f693a", "#301d0c"),
        }
        for sin, (light, dark) in expected.items():
            with self.subTest(sin=sin):
                self.assertEqual(self.token("sin-" + sin), light)
                self.assertEqual(self.token("sin-" + sin + "-dark"), dark)
        for sin in list(expected) + ["envy", "def"]:
            self.assertIn(".pv-sin-" + sin, self.css)

    def test_tones_and_resistances_have_colours(self):
        for tone in ("positive", "negative", "neutral"):
            self.assertIn(".pv-tone-" + tone, self.css)
            self.assertIn(".tone-" + tone, self.css)
        for word in ("fatal", "weak", "normal", "endure", "ineffective"):
            self.assertIn(".res-" + word, self.css)
        for word in ("Fatal", "Weak", "Normal", "Endure", "Ineffective"):
            self.assertIn(word, self.app)

    def test_every_part_of_the_form_is_its_own_tab(self):
        build = re.search(r"  function build\(\) \{.*?\n  \}\n", self.app, re.S).group(0)
        listed = re.findall(r"\['([^']+)', (build\w+)\]", build)
        self.assertEqual([name for _, name in listed], ["buildIdentity", "buildPoints", "buildStats", "buildResistances", "buildRanks", "buildDefense", "buildTags", "buildSkills", "buildPassives", "buildReference"])
        titles = re.findall(r"section\(\s*'([^']+)'", self.app)
        self.assertEqual(sorted(titles), sorted(["Identity", "Your points", "Stats", "Resistances", "Status ranks", "Defense types", "Group tags", "Passives", "Skills", "Status effects"]))
        self.assertEqual(sorted(title for title, _ in listed), sorted(titles))        # the name shown if a part fails to draw is the part's own
        self.assertIn("tablist", self.app)
        for needed in ("aria-selected", "aria-controls", "ArrowRight", "ArrowLeft", "Home", "End", "hashchange"):
            self.assertIn(needed, self.app, needed)
        self.assertIn('.tab[aria-selected="true"]', self.css)


class PageShape(unittest.TestCase):
    """What the page is made of: no header, a portrait that keeps its shape, and the wiki's font shipped with it."""

    @classmethod
    def setUpClass(cls):
        folder = os.path.join(ROOT, "docs", "builder")

        def read(name):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                return handle.read()

        cls.folder, cls.html, cls.css, cls.app, cls.engine = folder, read("index.html"), read("style.css"), read("app.js"), read("engine.js")

    def test_a_part_that_fails_to_draw_does_not_take_the_tabs_with_it(self):
        build = re.search(r"  function build\(\) \{.*?\n  \}\n", self.app, re.S).group(0)
        for builder in ("buildIdentity", "buildPoints", "buildStats", "buildResistances", "buildRanks", "buildDefense", "buildTags", "buildSkills", "buildPassives", "buildReference"):
            self.assertIn(builder + "]", build, builder)
            self.assertNotIn("appendChild(" + builder + "()", build, builder)       # each part goes through attempt(), none is called bare
        self.assertIn("attempt('the tabs'", build)
        tail = self.app[self.app.rindex("attempt('the card'"):]
        for step in ("attempt('the portrait', paintPortraitSize)", "attempt('the totals', update)", "attempt('the icons', loadIcons)"):
            self.assertIn(step, tail)
        for needed in ("Fetch fresh copies", "cache: 'reload'", "id: 'trouble'"):
            self.assertIn(needed, self.app, needed)
        self.assertIn(".trouble", self.css)
        self.assertIn("a script did not load", self.app)            # a script that is missing altogether is reported, not a blank page

    def test_the_page_has_no_header_and_keeps_load_and_start_over(self):
        self.assertNotIn("<header", self.html)
        self.assertIn('<h1 class="sr-only">', self.html)      # still named for screen readers and the browser tab
        for needed in ('id="loader"', 'id="loader-go"', 'id="reset"', 'id="tools"'):
            self.assertIn(needed, self.html)
        self.assertIn("getElementById('tools')", self.app)    # moved under the code in the card
        self.assertLess(self.html.index('<nav class="tabs"'), self.html.index('<main class="layout"'))

    def test_the_portrait_size_choice_only_ever_resizes(self):
        # width from the size choice, height from the picture's own shape: with the Original shape and no zoom nothing is cropped, zoomed or stretched
        frame = re.search(r"\n\.portrait-frame \{([^}]*)\}", self.css).group(1)
        self.assertIn("width: var(--pw", frame)
        self.assertIn("aspect-ratio: var(--ar", frame)
        for forbidden in ("object-fit", "max-height", "min-height", "transform", "scale"):
            self.assertNotIn(forbidden, frame, forbidden)
        self.assertNotIn("object-fit", " ".join(re.findall(r"[^\n]*\.portrait[^\n]*", self.css)))
        paint = re.search(r"function paintPortraitSize\(\) \{.*?\n  \}", self.app, re.S).group(0)
        self.assertEqual(re.findall(r"setProperty\('(--[\w-]+)'", paint), ["--pw"])      # the size choice sets the width and nothing else
        self.assertIn("class: 'plate' }, [cardParts.level", self.app)          # the Lv label sits under the picture, not on it
        self.assertNotIn("portraitImg, cardParts.level", self.app)

    def test_the_portrait_can_be_reshaped_moved_and_zoomed_and_starts_whole(self):
        shapes = re.search(r"var PORTRAIT_SHAPES = \[(.*?)\];", self.app, re.S).group(1)
        self.assertEqual(re.findall(r"id: '(\w+)'", shapes), ["original", "square", "tall", "wide", "banner"])
        self.assertIn("id: 'original', label: 'Original', ratio: null", shapes)       # Original has no shape of its own: the picture's
        start = re.search(r"var fit = \{([^}]*)\};", self.app).group(1)
        self.assertIn("shape: 'original'", start)
        self.assertIn("zoom: 1", start)
        self.assertRegex(start, r"x: 0, y: 0")
        # the opening is the picture's own shape unless a shape is picked, and the picture is placed in it by the same maths the skill pictures use
        self.assertIn("return shape && shape.ratio ? shape.ratio : own;", self.app)
        self.assertIn("window.LugicPreview.applyArt(img, { a: img.naturalWidth / img.naturalHeight, x: portraitFit.x, y: portraitFit.y, zoom: portraitFit.zoom }, o);", self.app)
        for needed in ("pointerdown", "wheel", "ArrowLeft", "ArrowUp", "touch-action"):
            self.assertIn(needed, self.app + self.css, needed)
        self.assertIn("another picture: start from the middle", self.app)          # a new picture is not placed by the old picture's numbers
        self.assertIn(".portrait-editor", self.css)
        self.assertIn("PORTRAIT_FIT_KEY", self.app)

    def test_the_portrait_and_the_skill_borders_are_not_part_of_the_code(self):
        spec = subprocess.run([NODE, "-e", (
            "const E=require('./docs/builder/engine.js'); const st=E.defaultState(); st.name='A';"
            "st.avatar='https://example.test/a.png'; st.skills=[Object.assign(E.newSkill(0),{name:'S',frame:{sin:'envy',tier:3},art:{src:'x',a:1,x:0,y:0,zoom:2}})];"
            "st.passives=[{name:'P',kind:'passive',cost:1,lines:['[Turn End] Heal 5 HP']}];"
            "process.stdout.write(JSON.stringify(E.toSpec(st)));")], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        for word in ("frame", "envy", "portrait", "zoom", '"art"'):
            self.assertNotIn(word, spec, word)
        self.assertIn('"passives"', spec)
        to_spec = re.search(r"function toSpec\(.*?\n  \}", self.engine, re.S).group(0)
        for word in ("portrait", "frame", "PORTRAIT"):
            self.assertNotIn(word, to_spec, word)

    def test_a_passive_can_have_a_flavour_text_that_goes_into_the_code(self):
        for needed in ("Flavour text (optional)", "R.limits.passiveFlavour", "p.flavour = flavour.value", "window.LugicPreview.renderPassive(p)"):
            self.assertIn(needed, self.app, needed)
        self.assertNotIn("FLAVOUR_KEY", self.app)          # not kept on the side any more: it is in the saved build and in the code
        self.assertNotIn("carryFlavours", self.app)
        to_spec = re.search(r"function toSpec\(.*?\n  \}", self.engine, re.S).group(0)
        self.assertIn("cleanFlavour(p.flavour)", to_spec)
        rule = re.search(r"\n\.pp-flavour \{([^}]*)\}", self.css).group(1)
        self.assertIn("font-style: italic", rule)          # brown and italic, as on the wiki
        self.assertIn("var(--wiki-brown-text)", rule)
        self.assertEqual(re.search(r"--wiki-brown-text:\s*(#[0-9a-fA-F]{6})", self.css).group(1).lower(), "#a16a3b")
        self.assertIn(".tip-flavour", self.css)           # the hover box has the same line for a status that has a story

    def test_a_flavour_text_survives_the_code_the_save_and_a_reload_on_the_site(self):
        out = subprocess.run([NODE, "-e", (
            "const E=require('./docs/builder/engine.js'); (async()=>{"
            "const st=E.defaultState(); st.name='A';"
            "st.passives=[{name:'P',kind:'support',cost:1,lines:['[Turn End] Heal 5 HP'],flavour:'  line one \\r\\n\\r\\n line two '},{name:'Q',kind:'passive',cost:1,lines:['[Turn End] Heal 5 HP']}];"
            "const code=await E.encode(E.toSpec(st)); const back=E.fromSpec((await E.decode(code)).spec);"
            "const long=E.defaultState(); long.passives=[{name:'L',kind:'passive',cost:1,lines:['[Turn End] Heal 5 HP'],flavour:'z'.repeat(500)}];"
            "process.stdout.write(JSON.stringify({back:back.passives.map(p=>p.flavour), spec:E.toSpec(st).passives.map(p=>Object.keys(p)), problems:E.review(long).perPassive[0]}));"
            "})()")], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        got = json.loads(out)
        self.assertEqual(got["back"], ["line one\nline two", ""])
        self.assertIn("flavour", got["spec"][0])
        self.assertNotIn("flavour", got["spec"][1])
        self.assertEqual(got["problems"], ["The flavour text is longer than 400 characters."])      # a pasted text that is too long is named

    def test_the_portrait_sizes_are_fixed_steps(self):
        steps = re.findall(r"\{ id: '(\w+)', label: '(\w+)', percent: (\d+) \}", self.app)
        self.assertEqual([(i, l) for i, l, _ in steps], [("small", "Small"), ("medium", "Medium"), ("large", "Large"), ("full", "Full")])
        percents = [int(p) for _, _, p in steps]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents[-1], 100)
        self.assertNotIn("portrait", re.search(r"function toSpec\(.*?\n  \}", self.engine, re.S).group(0))   # not part of the code

    def test_the_wiki_font_ships_with_the_page(self):
        for name in ("nunito-latin.woff2", "nunito-latin-ext.woff2", "Nunito-OFL.txt"):
            self.assertGreater(os.path.getsize(os.path.join(self.folder, "fonts", name)), 1000, name)
        self.assertIn("fonts/nunito-latin.woff2", self.css)

    def test_every_piece_of_text_uses_the_wikis_font_with_nunito_behind_it(self):
        # the wiki sets its names and descriptions in Mikodacs; its file is fonts/mikodacs.ttf, and while it is missing Nunito shows
        self.assertRegex(self.css, r'--font:\s*"Mikodacs",\s*"Nunito"')
        face = re.search(r'@font-face \{\s*font-family: "Mikodacs";([^}]*)\}', self.css).group(1)
        self.assertIn('url("fonts/mikodacs.ttf")', face)
        self.assertIn("font-weight: 400", face)          # one weight: the browser makes bold from it, as it does on the wiki
        self.assertIn("size-adjust: 110%", face)
        families = re.findall(r"font-family:\s*([^;}]+)[;}]", self.css)
        self.assertTrue(families)
        for family in families:
            self.assertTrue(family.strip() in ("var(--font)", "inherit", '"Mikodacs"', '"Nunito"'), family)   # no monospace, no private stack
        self.assertNotIn("monospace", self.css)

    def test_bold_text_is_white_and_inline_code_is_not_gold(self):
        self.assertEqual(re.search(r"--bold:\s*(#[0-9a-fA-F]{6})", self.css).group(1).lower(), "#ffffff")
        last = self.css[self.css.rindex("/* ---- bold is white"):]
        self.assertRegex(last, r"strong, b, h1, h2, h3, h4[^{]*\{ color: var\(--bold\); \}")
        code = re.search(r"\ncode \{([^}]*)\}", self.css).group(1)
        self.assertNotIn("--gold", code)

    def test_underlined_words_show_their_definition_in_the_wikis_hover_box(self):
        for needed in ("role: 'tooltip'", "pointerover", "focusin", "Escape", "LugicPreview.definition", "aria-describedby", "tip-title", "tip-line", "tip-flavour"):
            self.assertIn(needed, self.app, needed)
        box = re.search(r"\n\.tip \{([^}]*)\}", self.css).group(1)
        self.assertIn("pointer-events: none", box)                 # the box never takes the pointer, so it cannot flicker
        self.assertIn("border: 1px solid #604026", box)           # the wiki's black box with a brown edge and cream text
        self.assertIn("var(--wiki-cream)", box)
        self.assertRegex(self.css, r'\.tip-line::before \{ content: "-"')    # every line starts with "- "
        self.assertRegex(self.css, r"\.tip-flavour \{[^}]*font-style: italic")
        self.assertIn(".tip-title .ico img { width: 30px; height: 30px; }", self.css)     # a little bigger than the wiki's 25 so the picture reads at a glance

    def test_a_status_or_timing_can_be_pointed_at_wherever_it_is_named(self):
        # not only in the skill card: the pickers' lists, the coin's status list, the checking messages, the status chips and the rank names
        for needed in ("function prose(text)", "function pointAt(node, kind, key)", "function inlineRich(into, text)", "function statusChoice(value, label)",
                       "tip: { kind: 'status', key: key }", "tip: { kind: 'timing', key: ", "pointAt(button, current.tip.kind, current.tip.key)",
                       "if (item.tip) { pointAt(li, item.tip.kind, item.tip.key); }"):
            self.assertIn(needed, self.app, needed)
        self.assertGreaterEqual(self.app.count("prose("), 6)       # warnings, notes, problems, rule help are all run through it

    def test_lists_are_a_dropdown_that_colours_its_choices(self):
        for needed in ("function choice(options, value, onChange, settings)", "role: 'listbox'", "role: 'option'", "role: 'combobox'", "aria-activedescendant", "aria-expanded",
                       "'ArrowDown'", "'PageDown'", "'Home'", "'End'", "'Escape'", "typeahead", "choiceColour", "tone: statusTone(", "tone: e.tone", "tone: c.tone", "cls: window.LugicPreview.timingClass(t.key)"):
            self.assertIn(needed, self.app, needed)
        self.assertIn("return choice(options, value, onChange, settings);", self.app)      # the picker's lists and the coin status list use it
        for tone in ("positive", "negative", "neutral"):
            self.assertRegex(self.css, r"\.ch-" + tone + r" \{ color: var\(--" + tone + r"-text\); \}")
        for cls, colour in (("use", "use"), ("hit", "hit"), ("clashwin", "clash-win")):
            self.assertIn(f".choice-opt.pv-{cls}", self.css)
            self.assertIn(f"var(--wiki-tag-{colour})", self.css)
        self.assertIn(".choice-opt.pv-clashlose", self.css)
        self.assertNotIn('select[data-tone', self.css)       # the old native-list colouring is gone with the native list

    def test_the_skill_border_can_be_picked_by_hand_and_stays_on_the_device(self):
        for needed in ("function frameControls(skill, changed)", "P.frameChoices().forEach", "'Plain ring'", "'Automatic'", "role: 'radiogroup'", "'aria-checked'",
                       "FRAME_KEY = 'lugic-character-builder-frames-v1'", "border.refresh()", "delete skill.frame"):
            self.assertIn(needed, self.app, needed)
        for needed in (".frame-picker", ".frame-grid", ".frame-opt", '.frame-opt[aria-checked="true"]'):
            self.assertIn(needed, self.css, needed)
        self.assertIn("frame", re.search(r"function frameControls.*?\n  \}", self.app, re.S).group(0))

    def test_the_note_under_the_form_sits_right_under_it(self):
        main = re.search(r'<main class="layout">(.*?)</main>', self.html, re.S).group(1)
        column = re.search(r'<div class="main-col">(.*?)</div>\s*<aside', main, re.S).group(1)
        self.assertLess(column.index('id="sheet"'), column.index('class="foot"'))
        self.assertNotIn('class="foot"', self.html.split("</main>")[1])
        self.assertRegex(self.css, r"\.main-col \{[^}]*grid-template-columns: minmax\(0, 1fr\)")      # a wide card never pushes the form under the side card

    def test_the_rank_names_have_no_coloured_stripe(self):
        self.assertRegex(self.app, r"el\('span', \{ class: 'rank-name'[^}]*\}")
        self.assertNotRegex(self.css, r"\.rank-name \{[^}]*border-left")

    def test_the_attack_type_is_three_buttons_with_their_pictures_above_the_preview(self):
        card = self.app[self.app.index("function skillCard"):]
        self.assertIn("'aria-label': 'Attack type'", card)
        self.assertIn("R.skills.damageTypes.forEach", card)
        self.assertIn("icon(t)", card)
        self.assertNotIn("field('Damage type'", card)
        shown = card[card.index("class: 'skill-numbers'"):]
        self.assertLess(shown.index("class: 'skill-numbers'"), shown.index("class: 'type-pick'"))
        self.assertLess(shown.index("class: 'type-pick'"), shown.index("        preview,"))
        # a picture and a word inside one button are not each drawn as a button
        self.assertIn(".segment > span {", self.css)
        self.assertNotRegex(self.css, r"\n\.segment span \{")

    def test_a_status_with_no_keyword_has_no_count_box_and_one_number_for_both(self):
        for needed in ("E.isKeywordless(coin.name)", "E.coinStrength(", "cntBox.hidden = plain", "coin.pot = coin.cnt = size"):
            self.assertIn(needed, self.app, needed)
        self.assertIn("function isKeywordless", self.engine)
        self.assertIn("isKeywordless: isKeywordless, coinStrength: coinStrength", self.engine)

    def test_group_tags_are_a_dropdown_of_every_known_tag_with_other(self):
        tags = self.app[self.app.index("function buildTags"):]
        tags = tags[:tags.index("// ---- the rule-line picker")]
        for needed in ("knownTags()", "OTHER_TAG", "choice(options", "R.tags.max", "Other (write your own)", "maxlength", "repeats an earlier tag"):
            self.assertIn(needed, tags, needed)
        known = self.app[self.app.index("function knownTags"):][:700]
        self.assertIn("LUGIC_TAGS", known)
        self.assertIn("R.tags.suggested", known)
        loaded = re.findall(r'<script src="([\w.]+)', self.html)
        self.assertLess(loaded.index("tags.js"), loaded.index("app.js"))

    def test_the_text_lines_toggle_looks_like_a_button(self):
        rule = re.search(r"\.rule-text-mode > summary \{([^}]*)\}", self.css).group(1)
        for needed in ("cursor: pointer", "border:", "background:"):
            self.assertIn(needed, rule, needed)
        self.assertRegex(self.css, r"\.rule-text-mode > summary::before")       # the arrow that turns when it is open

    def test_a_part_that_breaks_a_rule_turns_red_with_its_colours_reversed(self):
        for needed in ("function markTabs", "r.parts", "r.own", "part-flag", "'flagged'"):
            self.assertIn(needed, self.app, needed)
        tab = re.search(r"\.tab\.flagged, \.tab\.flagged:hover \{([^}]*)\}", self.css).group(1)
        self.assertIn("background: var(--alert)", tab)               # red ground, dark letters: the tab's usual colours the other way round
        self.assertRegex(self.css, r"\.part\.flagged \{[^}]*outline: 2px solid var\(--alert\)")
        self.assertRegex(self.css, r"\.part\.flagged > h2 \{[^}]*background: var\(--alert\)")
        self.assertRegex(self.css, r"\.part-flag \{[^}]*background: var\(--alert\)")

    def test_pictures_are_a_little_bigger_without_making_a_row_taller(self):
        self.assertRegex(self.css, r"\.ico \{[^}]*margin-block: -0\.2em")
        self.assertRegex(self.css, r"\.ico img \{ width: 1\.5em; height: 1\.5em;")

    def test_no_script_uses_a_lookbehind_pattern(self):
        # older Safari cannot even parse one, and then the whole page is dead
        for name in ("app.js", "preview.js", "engine.js", "lines.js"):
            with open(os.path.join(self.folder, name), encoding="utf-8") as handle:
                self.assertNotRegex(handle.read(), r"\(\?<[=!]", name)

    def test_missing_icon_folders_do_not_break_the_page(self):
        # both lists are optional: a failed fetch becomes an empty list, and a slot can name a fallback ("coin coin_heads")
        self.assertIn("icons/index.json", self.app)
        self.assertIn("wiki/index.json", self.app)
        self.assertIn(".catch(function () { return {}; })", self.app)
        self.assertIn("split(' ')", self.app)


class Templates(unittest.TestCase):
    def test_every_skill_line_button_parses(self):
        for item in builder_rules.SKILL_LINE_TEMPLATES:
            with self.subTest(line=item["line"]):
                parse_trigger_text(item["line"], {})

    def test_every_tag_button_parses_as_a_flag(self):
        for label in builder_rules.SKILL_FLAG_BUTTONS:
            with self.subTest(tag=label):
                triggers, flags = parse_trigger_text(f"[{label}]", {})
                self.assertEqual(triggers, [])
                self.assertEqual(len(flags), 1)

    def test_every_passive_example_parses(self):
        for item in builder_rules.PASSIVE_TEMPLATES:
            with self.subTest(passive=item["name"]):
                _, error = parse_passive(item["lines"], {})
                self.assertIsNone(error)


class GeneratedRules(unittest.TestCase):
    def test_rules_js_is_up_to_date(self):
        with open(builder_rules.RULES_JS_PATH, encoding="utf-8") as handle:
            self.assertEqual(
                handle.read(), builder_rules.rules_js(),
                "docs/builder/rules.js is out of date. Run:  python -m game.builder_rules",
            )

    def test_tags_js_is_a_well_formed_list_that_has_the_suggested_tags(self):
        # It is written from the saved characters, which only exist where the bot runs, so it cannot be compared with a fresh run here.
        from game.conditions import norm_tag
        with open(builder_rules.TAGS_JS_PATH, encoding="utf-8") as handle:
            text = handle.read()
        match = re.fullmatch(r"//[^\n]*\nwindow\.LUGIC_TAGS = (\[.*\]);\n", text, re.S)
        self.assertTrue(match, "docs/builder/tags.js is not the shape python -m game.builder_rules writes")
        tags = json.loads(match.group(1))
        self.assertTrue(set(builder_rules.SUGGESTED_TAGS) <= set(tags))
        self.assertEqual(len({norm_tag(t) for t in tags}), len(tags))
        self.assertEqual(tags, sorted(tags, key=norm_tag))
        for tag in tags:
            self.assertTrue(tag.strip() and tag == " ".join(tag.split()) and len(tag) <= builder_rules.MAX_TAG_LENGTH, tag)

    def test_the_tag_list_is_the_saved_tags_and_the_suggestions_once_each(self):
        tags = builder_rules.site_tags(["Moon Guild", "moon   guild", "  dawn office ", "x" * 31, "", "Zebra"])
        self.assertIn("Moon Guild", tags)                          # the first spelling of a saved tag is kept
        self.assertEqual(sum(1 for t in tags if t.lower() == "moon guild"), 1)
        self.assertIn("Dawn Office", tags)                         # where a suggestion names the same tag, its spelling wins
        self.assertNotIn("dawn office", tags)
        self.assertNotIn("x" * 31, tags)                           # a tag too long for the bot is not offered
        self.assertEqual(tags, sorted(tags, key=str.lower))
        self.assertEqual(builder_rules.site_tags([]), sorted(builder_rules.SUGGESTED_TAGS, key=str.lower))
        self.assertIn("window.LUGIC_TAGS = [", builder_rules.tags_js(["A", "B"]))

    def test_index_html_names_the_current_version_of_every_file(self):
        with open(builder_rules.INDEX_HTML_PATH, encoding="utf-8", newline="") as handle:
            html = handle.read()
        self.assertEqual(
            html, builder_rules.versioned_index(html),
            "docs/builder/index.html names an old version of a site file. Run:  python -m game.builder_rules",
        )
        loaded = re.findall(r'(?:src|href)="([\w.-]+\.(?:js|css))(?:\?v=([0-9a-f]+))?"', html)
        self.assertEqual(sorted(name for name, _ in loaded), sorted(builder_rules.VERSIONED_FILES))
        for name, version in loaded:
            self.assertRegex(version, r"^[0-9a-f]{10}$", name)

    def test_a_changed_file_gets_a_new_address(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in builder_rules.VERSIONED_FILES:
                with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
                    handle.write("// " + name)
            page = '<link rel="stylesheet" href="style.css"><script src="app.js?v=0000000000"></script>'
            first = builder_rules.versioned_index(page, folder)
            self.assertRegex(first, r'href="style\.css\?v=[0-9a-f]{10}"')
            self.assertRegex(first, r'src="app\.js\?v=[0-9a-f]{10}"')
            self.assertNotIn("0000000000", first)
            self.assertEqual(builder_rules.versioned_index(first, folder), first)       # running it again changes nothing
            with open(os.path.join(folder, "app.js"), "w", encoding="utf-8") as handle:
                handle.write("// app.js changed")
            second = builder_rules.versioned_index(first, folder)
            self.assertNotEqual(second, first)
            self.assertEqual(re.search(r"style\.css\?v=(\w+)", second).group(1), re.search(r"style\.css\?v=(\w+)", first).group(1))


def _playwright_available() -> bool:
    return bool(NODE) and subprocess.run([NODE, "-e", "require('playwright')"], cwd=ROOT, capture_output=True).returncode == 0


@unittest.skipUnless(_playwright_available(), "needs Playwright for Node (only used to open the page in a browser)")
class PageInABrowser(unittest.TestCase):
    """Opens the real page. A page put together from two versions (one script an older copy) still has its tabs and says what failed."""

    @classmethod
    def setUpClass(cls):
        import functools
        import http.server

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        handler = functools.partial(Quiet, directory=os.path.join(ROOT, "docs", "builder"))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        run = subprocess.run([NODE, os.path.join(ROOT, "tests", "page_check.js"), base], cwd=ROOT, capture_output=True, text=True, timeout=120)
        if run.returncode != 0:
            raise unittest.SkipTest("the browser could not be started here: " + run.stderr[-300:])
        cls.found = json.loads(run.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_every_part_is_a_tab_and_only_one_is_open(self):
        fresh = self.found["fresh"]
        self.assertEqual(len(fresh["tabs"]), 10)
        self.assertEqual(fresh["parts"], 10)
        self.assertEqual(fresh["open"], ["sec-identity"])
        self.assertEqual(fresh["box"], "")

    def test_the_note_under_the_form_is_not_far_below_it(self):
        footer = self.found["behaviour"]["footer"]
        self.assertTrue(footer["beforeTools"])
        self.assertLess(footer["gap"], 40)

    def test_a_rule_broken_in_the_skills_turns_its_tab_and_its_part_red(self):
        flag = self.found["behaviour"]["flag"]
        self.assertEqual(flag["tab"], ["Skills"])
        self.assertTrue(flag["part"])
        self.assertTrue(flag["banner"])

    def test_the_attack_type_can_be_picked_and_shows_on_the_preview(self):
        attack = self.found["behaviour"]["attack"]
        self.assertEqual(attack["offered"], ["slash", "blunt", "pierce"])
        self.assertEqual(attack["checked"], ["blunt"])
        self.assertTrue(attack["preview"])
        self.assertFalse(attack["typeSelect"])

    def test_a_status_with_no_keyword_shows_only_a_potency_box_in_its_coin(self):
        self.assertEqual([coin["countHidden"] for coin in self.found["behaviour"]["coin"]], [False, True, False])

    def test_every_known_group_tag_is_in_the_dropdown_and_other_lets_one_be_written(self):
        behaviour = self.found["behaviour"]
        self.assertTrue(behaviour["known"])
        self.assertEqual(behaviour["tagOptions"], ["None"] + behaviour["known"] + ["Other (write your own)"])
        self.assertTrue(behaviour["tagOther"])

    def test_the_skill_card_and_the_stats_tab_say_what_the_limits_are_for_the_coins(self):
        behaviour = self.found["behaviour"]
        self.assertEqual(behaviour["limit"], ["At Power 6 with 3 coins: Base Power up to 2, Coin Power up to 1.",
                                              "At Power 6 with 4 coins: Base Power up to 1, Coin Power up to 1."])
        self.assertIn("Each coin after the first lowers both limits by 1 (Base / Coin): 2 coins 3 / 1, 3 coins 2 / 1, 4 coins 1 / 1.", behaviour["statsHint"])

    def test_nothing_runs_off_the_side_of_a_phone_on_any_tab(self):
        self.assertEqual(self.found["behaviour"]["phone"], [True] * 10)

    def test_an_older_copy_of_one_script_still_leaves_the_tabs_and_a_way_to_fix_it(self):
        stale = self.found["stale"]
        self.assertEqual(len(stale["tabs"]), 10)
        self.assertEqual(stale["open"], ["sec-identity"])
        self.assertEqual(stale["parts"], 10)
        self.assertTrue(stale["button"])
        self.assertIn("Part of this page could not be drawn", stale["box"])
        self.assertIn("Fetch fresh copies", stale["box"])


if __name__ == "__main__":
    unittest.main()
