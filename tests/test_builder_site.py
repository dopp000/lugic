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
import shutil
import subprocess
import sys
import unittest
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.simplefilter("ignore")

from cogs.battle import _parse_status_tokens  # noqa: E402
from game import builder_rules  # noqa: E402
from game.build_points import (  # noqa: E402
    DEFENSE_ARCHETYPES, RANKED_STATUSES, check_build, skill_rank_problems,
)
from game.build_requests import _apply  # noqa: E402
from game.character import starting_character  # noqa: E402
from game.character_import import decode_code, encode_code, needs_approval, read_spec  # noqa: E402
from game.conditions import parse_trigger_text  # noqa: E402
from game.passives import parse_passive  # noqa: E402
from game.skills import Skill  # noqa: E402
from game.statuses import ONE_TURN_STATUSES, OTHER_DEBUFF_STATUSES  # noqa: E402

NODE = shutil.which("node")
PARITY_JS = os.path.join(ROOT, "tests", "builder_parity.js")
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
             "lines": ["[Combat Start] Gain 2 Protection"]}
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


def run_node(payload: dict) -> dict:
    done = subprocess.run(
        [NODE, PARITY_JS], input=json.dumps(payload), capture_output=True, text=True, timeout=120, cwd=ROOT,
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

    def test_needs_admin_flag_matches_the_bot(self):
        # The site tells the player whether an admin will have to answer. It has to say what the bot will do.
        for spec, got in zip(self.specs, self.js["checks"]):
            parsed, error = read_spec(got["toSpec"])
            self.assertIsNone(error)
            with self.subTest(spec=spec["name"]):
                self.assertEqual(needs_approval(parsed.params), got["toSpec"].get("stats") is not None or any(
                    got["toSpec"].get(k) for k in ("ranks", "archetypes", "tags", "passives")
                ))


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


if __name__ == "__main__":
    unittest.main()
