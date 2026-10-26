"""The rule-line picker on the builder site builds lines from game/line_spec.py with docs/builder/lines.js.

These tests hold the picker to the bot's own parser: a line the picker offers must parse, and a combination the
picker refuses must be one the parser refuses too. If the bot's rules move and the picker does not follow, a test
here fails.
"""

import json
import os
import shutil
import subprocess
import unittest

from game import line_spec
from game.conditions import ALL_TIMING_LOOKUP, TriggerParseError, parse_trigger_text
from game.passives import parse_passive

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
SPEC = line_spec.spec()
TIMINGS = {t["key"]: t for t in SPEC["timings"]}
EFFECTS = {e["id"]: e for e in SPEC["effects"]}
CONDITIONS = {c["id"]: c for c in SPEC["conditions"]}
ONCE = " (once per turn)"


def sample_values(param):
    """Every value worth trying for one parameter: its default, and the edges."""
    if param["kind"] == "number":
        return sorted({param["default"], param["min"], param["max"]})
    if param["kind"] == "choice":
        return [o["value"] for o in param["options"]]
    return ["N Corp"] if param["kind"] == "tag" else ["Quick Jab"]


def variants(params):
    """The default values, then each parameter taken through its values while the others stay on default."""
    base = {p["name"]: p["default"] if p["kind"] in ("number", "choice") else sample_values(p)[0] for p in params}
    out = [dict(base)]
    for p in params:
        for value in sample_values(p):
            if value != base[p["name"]]:
                out.append({**base, p["name"]: value})
    return out


def pick(timing, effect, cond="", cond_values=None, eff_values=None, **extra):
    return {
        "timing": timing, "coin": 1, "cond": cond, "condValues": cond_values or {}, "effect": effect, "effValues": eff_values or {},
        "target": "", "selector": SPEC["selectors"][0], "next": False, "limit": "", "chance": 0, **extra,
    }


def run_node(items):
    done = subprocess.run([NODE, os.path.join(ROOT, "tests", "lines_check.js")], input=json.dumps({"picks": items}),
                          capture_output=True, text=True, timeout=120)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


def bot_says(line, passive):
    """None when the bot takes the line, else its reason."""
    try:
        if passive:
            _, error = parse_passive([line])
            return error
        parse_trigger_text(line)
    except TriggerParseError as e:
        return e.reason
    return None


class SpecShape(unittest.TestCase):
    def test_every_timing_the_bot_knows_is_offered(self):
        offered = {t["key"].lower() for t in SPEC["timings"]}
        self.assertEqual(offered, set(ALL_TIMING_LOOKUP))

    def test_placeholders_match_parameters(self):
        import re
        for item in list(SPEC["conditions"]) + list(SPEC["effects"]):
            names = set(re.findall(r"\{(\w+)\}", item["text"]))
            self.assertEqual(names, {p["name"] for p in item["params"]}, item.get("id"))

    def test_flag_help_covers_every_flag_button(self):
        from game.builder_rules import SKILL_FLAG_BUTTONS
        self.assertEqual(set(SPEC["flagHelp"]), set(SKILL_FLAG_BUTTONS))


@unittest.skipUnless(NODE, "node is not installed")
class PickerAgreesWithBot(unittest.TestCase):
    def check(self, items):
        answers = run_node(items)
        self.assertEqual(len(answers), len(items))
        wrong = []
        for item, answer in zip(items, answers):
            passive = item["passive"]
            if answer["problem"] is None:
                reason = bot_says(answer["line"], passive)
                if reason:
                    wrong.append(f"picker offered {answer['line']!r} but the bot says: {reason}")
            elif item.get("expect_bot_refuses"):
                line = item["line_for_bot"]
                if bot_says(line, passive) is None:
                    wrong.append(f"picker refuses {line!r} ({answer['problem']}) but the bot takes it")
        self.assertEqual(wrong[:10], [], f"{len(wrong)} disagreements")
        return answers

    def test_every_timing_with_every_effect(self):
        items = []
        for t in SPEC["timings"]:
            for e in SPEC["effects"]:
                for values in variants(e["params"])[:1]:
                    for limit in ("", ONCE):
                        for passive in (False, True):
                            items.append({"pick": pick(t["key"], e["id"], eff_values=values, limit=limit), "passive": passive})
        answers = self.check(items)
        # The picker says yes to a good number of them, so the test above is not passing on an empty list.
        self.assertGreater(sum(1 for a in answers if a["problem"] is None), 500)

    def test_refused_pairs_are_refused_by_the_bot_too(self):
        """Only the pairs the picker refuses for a game reason (not a wrong-context one) are held to the bot."""
        items = []
        for t in SPEC["timings"]:
            for e in SPEC["effects"]:
                values = variants(e["params"])[0]
                for limit in ("", ONCE):
                    # Compose the line the way the picker would, by asking it with the check turned off for the context.
                    items.append({"pick": pick(t["key"], e["id"], eff_values=values, limit=limit), "passive": False, "ctx": (t["key"], e["id"], limit)})
        answers = run_node(items)
        # Rebuild each refused pair's line with node-free text: the template filled with defaults.
        wrong = []
        for item, answer in zip(items, answers):
            if answer["problem"] is None:
                continue
            t, e, limit = item["ctx"]
            timing, effect = TIMINGS[t], EFFECTS[e]
            if not timing["skill"]:
                continue  # passive-only timing on a skill: the bot lets it through, the picker hides it
            text = effect["text"]
            for p in effect["params"]:
                text = text.replace("{" + p["name"] + "}", str(variants(effect["params"])[0][p["name"]]))
            line = (":Coin1: " if timing["perCoin"] else "") + f"[{t}] {text}{limit}"
            if bot_says(line, False) is None:
                wrong.append(f"picker refuses {line!r} ({answer['problem']}) but the bot takes it")
        self.assertEqual(wrong[:10], [], f"{len(wrong)} disagreements")

    def test_every_effect_with_every_value(self):
        items = []
        for e in SPEC["effects"]:
            timing = "Ally Before Getting Hit" if e.get("assist") else "Ally On Hit" if e.get("followup") else "On Use"
            for values in variants(e["params"]):
                items.append({"pick": pick(timing, e["id"], eff_values=values, limit=ONCE if e.get("needsLimit") else ""), "passive": False})
        answers = self.check(items)
        self.assertTrue(all(a["problem"] is None for a in answers), [a for a in answers if a["problem"]][:3])

    def test_every_condition_with_every_value(self):
        items = []
        for c in SPEC["conditions"]:
            for values in variants(c["params"]):
                for timing in ("On Use", "Turn Start", "Ally On Hit"):
                    items.append({"pick": pick(timing, "sanity_gain", cond=c["id"], cond_values=values, eff_values={"n": 3}), "passive": False})
        answers = self.check(items)
        self.assertTrue(all(a["problem"] is None for a in answers), [a for a in answers if a["problem"]][:3])

    def test_aims_extras_and_limits(self):
        items = []
        for e in SPEC["effects"]:
            if not e.get("ally"):
                continue
            values = variants(e["params"])[0]
            for target in [x["value"] for x in SPEC["targets"]]:
                for nxt in (False, True):
                    for limit in [x["value"] for x in SPEC["limits"]]:
                        for chance in (0, 1, 99):
                            for selector in SPEC["selectors"][:3]:
                                items.append({"pick": pick("Turn Start", e["id"], eff_values=values, target=target, next=nxt, limit=limit,
                                                           chance=chance, selector=selector), "passive": True})
        answers = self.check(items)
        self.assertTrue(all(a["problem"] is None for a in answers), [a for a in answers if a["problem"]][:3])

    def test_what_the_picker_refuses(self):
        cases = [
            (pick("On Hit", "dmg_pct"), "before the coins are tossed"),
            (pick("Coin Start", "dmg_pct"), "before the coins are tossed"),
            (pick("Ally On Hit", "followup", eff_values={"skill": "Quick Jab"}), "needs a limit"),
            (pick("On Use", "followup", eff_values={"skill": "Quick Jab"}, limit=ONCE), "follow-up"),
            (pick("Turn Start", "assist"), "Ally Before Getting Hit"),
            (pick("On Use", "dmg_pct", next=True), "Next turn"),
            (pick("Turn Start", "inflict_count", target=" to the ally", eff_values={"n": 1, "status": "Burn"}), "this unit"),
            (pick("On Use", "sanity_gain", cond="side_tag", cond_values={"n": 2, "tag": ""}, eff_values={"n": 3}), "tag"),
            (pick("Turn Start", "sanity_gain", eff_values={"n": 0}), "from 1 to"),
            (pick("Turn Start", "sanity_gain", eff_values={"n": 3}, chance=100), "chance"),
            (pick("Self Is Hit", "sanity_gain", eff_values={"n": 3}), "passive"),
        ]
        answers = run_node([{"pick": c[0], "passive": False} for c in cases])
        for (_, needle), answer in zip(cases, answers):
            self.assertIsNotNone(answer["problem"], needle)
            self.assertIn(needle.lower(), answer["problem"].lower())
        # and the one that is only wrong for a skill is fine for a passive
        ok = run_node([{"pick": pick("Self Is Hit", "sanity_gain", eff_values={"n": 3}), "passive": True}])
        self.assertIsNone(ok[0]["problem"])

    def test_the_spec_in_rules_js_is_current(self):
        from game import builder_rules
        self.assertEqual(builder_rules.rules()["lines"], SPEC)


if __name__ == "__main__":
    unittest.main()
