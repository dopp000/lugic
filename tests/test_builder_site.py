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
        order = re.findall(r"sheet\.appendChild\((build\w+)\(\)\)", self.app)
        self.assertEqual(order, ["buildIdentity", "buildPoints", "buildStats", "buildResistances", "buildRanks", "buildDefense", "buildTags", "buildSkills", "buildPassives", "buildReference"])
        titles = re.findall(r"section\(\s*'([^']+)'", self.app)
        self.assertEqual(sorted(titles), sorted(["Identity", "Your points", "Stats", "Resistances", "Status ranks", "Defense types", "Group tags", "Passives", "Skills", "Status effects"]))
        self.assertIn("tablist", self.app)
        for needed in ("aria-selected", "aria-controls", "ArrowRight", "ArrowLeft", "Home", "End", "hashchange"):
            self.assertIn(needed, self.app, needed)
        self.assertIn('.tab[aria-selected="true"]', self.css)


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
