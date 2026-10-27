"""/character import: reading codes from the builder site, and what the bot does with a build.

Run all tests from the repo root with:  python -m unittest discover tests
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
import warnings
import zlib
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.simplefilter("ignore")

import discord  # noqa: E402

import cogs.build_requests as cb  # noqa: E402
import cogs.character_import as ci  # noqa: E402
from game import build_requests as br  # noqa: E402
from game.build_points import check_build  # noqa: E402
from game.character import character_exists, load_character, save_character  # noqa: E402
from game.character_import import (  # noqa: E402
    MAX_JSON_BYTES, decode_code, encode_code, needs_approval, read_spec,
)

SKILLS = [
    {"name": "Quick Jab", "stats": "3, 1, 2, Blunt"},
    {"name": "Chop", "stats": "3, 1, 2, Slash", "triggers": "[On Use] Coin Power +1"},
]


def packed(raw: bytes, prefix: str = "LUGIC1Z.") -> str:
    return prefix + base64.urlsafe_b64encode(raw).decode().rstrip("=")


class InTempDir(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()


def interaction(user_id: int = 7):
    i = MagicMock()
    i.user.id = user_id
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.client = MagicMock()
    i.client.get_channel = MagicMock(return_value=None)
    i.message = MagicMock()
    return i


def said(i) -> str:
    return i.response.send_message.call_args.args[0]


async def do_import(spec: dict, user_id: int = 7, note: str = ""):
    i = interaction(user_id)
    await ci.handle_import(i, encode_code(spec), note)
    return said(i)


def run(coro):
    return asyncio.run(coro)


class ReadingCodes(unittest.TestCase):
    def test_both_forms_open(self):
        spec = {"v": 1, "name": "Rook", "skills": SKILLS}
        for compress in (True, False):
            data, error = decode_code(encode_code(spec, compress=compress))
            self.assertIsNone(error)
            self.assertEqual(data, spec)

    def test_pasted_text_is_forgiven(self):
        code = encode_code({"v": 1, "name": "Rook"})
        for messy in (f"  {code}\n", f"`{code}`", f"\"{code}\"", code[:20] + "\n" + code[20:], "lugic1z." + code[len("LUGIC1Z."):]):
            data, error = decode_code(messy)
            self.assertIsNone(error, messy)

    def test_junk_is_told_in_plain_words(self):
        for text in ("", "hello", "LUGIC1Z.", "LUGIC1Z.!!!", "LUGIC1Z.AAAA", packed(b"not zlib"), packed(b"[1,2]", "LUGIC1.")):
            data, error = decode_code(text)
            self.assertIsNone(data, text)
            self.assertTrue(error)

    def test_wrong_version_is_named(self):
        data, error = decode_code(encode_code({"v": 2, "name": "Rook"}))
        self.assertIsNone(data)
        self.assertIn("different version", error)

    def test_a_small_code_cannot_unpack_into_something_huge(self):
        bomb = zlib.compress(b'{"v":1,"name":"' + b"a" * (MAX_JSON_BYTES * 3) + b'"}', 9)
        self.assertLess(len(bomb), 4000)
        data, error = decode_code(packed(bomb))
        self.assertIsNone(data)
        self.assertIn("far more than a character", error)


class ReadingBuilds(unittest.TestCase):
    def ok(self, **extra):
        spec, error = read_spec({"v": 1, "name": "Rook", **extra})
        self.assertIsNone(error)
        return spec

    def bad(self, **extra):
        spec, error = read_spec({"v": 1, "name": "Rook", **extra})
        self.assertIsNone(spec)
        self.assertTrue(error)
        return error

    def test_what_equals_a_new_character_is_dropped(self):
        spec = self.ok(stats={"hp": 100, "power": 6, "speed": [1, 3], "offense": 0, "resist": {"burn": 1.0}})
        self.assertEqual(spec.params, {})
        self.assertFalse(needs_approval(spec.params))

    def test_skills_alone_need_no_approval_and_everything_else_does(self):
        self.assertFalse(needs_approval(self.ok(skills=SKILLS).params))
        for extra in ({"stats": {"hp": 110}}, {"ranks": {"burn": 1}}, {"archetypes": ["guard"]}, {"tags": ["N Corp"]},
                      {"passives": [{"name": "P", "cost": 1, "lines": ["[Combat Start] Gain 2 Protection"]}]}):
            self.assertTrue(needs_approval(self.ok(**extra).params), extra)

    def test_bad_names_and_links(self):
        self.bad(name="")
        self.bad(name="a/b")
        self.bad(name="x" * 80)
        self.bad(avatar="http://example.com/a.png")
        self.bad(avatar="javascript:alert(1)")

    def test_bad_shapes(self):
        self.bad(stats="nope")
        self.bad(stats={"hp": "lots"})
        self.bad(stats={"speed": [1]})
        self.bad(stats={"resist": {"fire": 0.5}})
        self.bad(ranks={"fire": 1})
        self.bad(ranks={"burn": 9})
        self.bad(archetypes=["dodge"])
        self.bad(tags=["a", "b", "c", "d"])
        self.bad(passives=[{"name": "P", "cost": 99, "lines": ["x"]}])
        self.bad(passives=[{"name": "P", "cost": 1, "lines": []}])
        self.bad(skills=[{"name": "A"}])
        self.bad(skills=[{"name": "A", "stats": "1, 1, 1, Slash"}, {"name": "a", "stats": "1, 1, 1, Slash"}])
        self.bad(skills=[{"name": f"S{i}", "stats": "1, 1, 1, Slash"} for i in range(13)])


class ImportFlows(InTempDir):
    def test_skills_only_is_made_at_once(self):
        reply = run(do_import({"v": 1, "name": "Rook", "skills": SKILLS}))
        self.assertIn("Created **Rook** with 2 skills", reply)
        self.assertNotIn("request", reply)
        c = load_character("Rook")
        self.assertEqual(sorted(c.skills), ["chop", "quick jab"])
        self.assertEqual(c.owner_id, 7)
        self.assertEqual(br.list_requests(status="open"), [])

    def test_an_existing_name_is_refused(self):
        run(do_import({"v": 1, "name": "Rook"}))
        reply = run(do_import({"v": 1, "name": "rook", "skills": SKILLS}, user_id=8))
        self.assertIn("already exists", reply)
        self.assertEqual(load_character("Rook").owner_id, 7)
        self.assertEqual(load_character("Rook").skills, {})

    def test_a_build_that_costs_points_goes_to_the_admins_and_applies_on_approval(self):
        spec = {
            "v": 1, "name": "Kane", "stats": {"hp": 140, "power": 8, "speed": [2, 5]}, "ranks": {"bleed": 2},
            "skills": [{"name": "Rend", "stats": "4, 1, 2, Slash", "statuses": "Bleed:2:2,none"}],
        }
        reply = run(do_import(spec, note="please"))
        self.assertIn("request #1", reply)
        self.assertIn("starting stats and no skills", reply)
        waiting = load_character("Kane")
        self.assertEqual((waiting.max_hp, waiting.power, waiting.skills, waiting.status_ranks), (100, 6, {}, {}))
        req = br.get_request(1)
        self.assertEqual((req.kind, req.status, req.reason), ("build", "open", "please"))

        admin = interaction(1)
        client = MagicMock()
        client.get_user.return_value = MagicMock(send=AsyncMock())
        admin.client = client
        run(cb.approve(admin, req))
        self.assertEqual(br.get_request(1).status, "approved")
        built = load_character("Kane")
        self.assertEqual((built.max_hp, built.power, built.status_ranks), (140, 8, {"bleed": 2}))
        self.assertIn("rend", built.skills)
        self.assertTrue(check_build(built).ok)

    def test_a_build_over_the_budget_creates_nothing(self):
        reply = run(do_import({"v": 1, "name": "Big", "stats": {"hp": 160, "power": 12, "speed": [6, 8]}}))
        self.assertIn("nothing was created", reply)
        self.assertIn("Change it on the builder", reply)
        self.assertFalse(character_exists("Big"))
        self.assertEqual(br.list_requests(status="open"), [])

    def test_a_skill_the_form_would_refuse_creates_nothing(self):
        cases = {
            "Torch": ({"name": "Torch", "stats": "3, 1, 2, Pierce", "statuses": "Burn:1:1,none"}, "needs a Burn rank"),
            "Twin": ({"name": "Twin", "stats": "3, 1, 2, Slash", "triggers": "[On Use] Coin Power +1\n[On Use] Deal +10% damage"}, "Skill Point"),
            "Mighty": ({"name": "Mighty", "stats": "9, 2, 2, Slash"}, "above the limit"),
            "Shield": ({"name": "Shield", "stats": "4, 2, 1, Blunt", "triggers": "[Guard]"}, "defense archetype"),
            "Nuke": ({"name": "Nuke", "stats": "3, 1, 2, Blunt", "triggers": "[Supermove]"}, "admin"),
            "Wide": ({"name": "Wide", "stats": "3, 1, 2, Blunt", "weight": "3"}, "Supermove"),
        }
        for name, (skill, expect) in cases.items():
            with self.subTest(skill=name):
                reply = run(do_import({"v": 1, "name": f"Char {name}", "skills": [skill]}))
                self.assertIn("nothing was created", reply)
                self.assertIn(expect, reply)
                self.assertFalse(character_exists(f"Char {name}"))

    def test_the_player_is_never_sent_to_an_admin_command(self):
        reply = run(do_import({"v": 1, "name": "Shield", "skills": [
            {"name": "Block", "stats": "4, 2, 1, Blunt", "triggers": "[Guard]"}]}))
        self.assertNotIn("/charadmin", reply)

    def test_a_skill_can_use_a_rank_the_same_build_buys(self):
        reply = run(do_import({"v": 1, "name": "Hex", "ranks": {"burn": 2}, "skills": [
            {"name": "Torch", "stats": "3, 1, 2, Pierce", "statuses": "Burn:2:1,none"}]}))
        self.assertIn("request #1", reply)

    def test_a_defense_type_the_build_buys_covers_its_skill(self):
        reply = run(do_import({"v": 1, "name": "Warden", "archetypes": ["guard"], "skills": [
            {"name": "Block", "stats": "4, 2, 1, Blunt", "triggers": "[Guard]"}]}))
        self.assertIn("request #1", reply)

    def test_passives_wait_for_an_admin_to_hand_out_the_points(self):
        spec = {"v": 1, "name": "Pax", "passives": [
            {"name": "Steady", "kind": "passive", "cost": 2, "lines": ["[Combat Start] Gain 2 Protection"]}]}
        reply = run(do_import(spec))
        self.assertIn("request #1", reply)
        self.assertIn("Passive Points", reply)
        self.assertEqual(load_character("Pax").passives, [])

        admin = interaction(1)
        admin.client = MagicMock()
        admin.client.get_user.return_value = MagicMock(send=AsyncMock())
        run(cb.approve(admin, br.get_request(1)))
        self.assertIn("nothing changed", said(admin))
        self.assertEqual(br.get_request(1).status, "open")

        pax = load_character("Pax")
        pax.pp_total = 2
        save_character(pax)
        admin = interaction(1)
        admin.client = MagicMock()
        admin.client.get_user.return_value = MagicMock(send=AsyncMock())
        run(cb.approve(admin, br.get_request(1)))
        self.assertEqual(br.get_request(1).status, "approved")
        self.assertEqual([p["name"] for p in load_character("Pax").passives], ["Steady"])

    def test_a_bad_passive_line_is_refused_with_the_reason(self):
        reply = run(do_import({"v": 1, "name": "Pax", "passives": [
            {"name": "Odd", "kind": "passive", "cost": 1, "lines": ["[Clash Win] Heal 5"]}]}))
        self.assertIn("nothing was created", reply)
        self.assertFalse(character_exists("Pax"))

    def test_the_open_request_cap_is_checked_before_anything_is_saved(self):
        for n in range(br.MAX_OPEN_PER_PLAYER):
            br.create_request(7, f"Other {n}", "tags", {"tags": ["x"]}, "")
        reply = run(do_import({"v": 1, "name": "Late", "stats": {"hp": 110}}))
        self.assertIn("open requests", reply)
        self.assertFalse(character_exists("Late"))
        # Skills alone make no request, so the cap does not apply to them.
        self.assertIn("Created **Late**", run(do_import({"v": 1, "name": "Late", "skills": SKILLS})))

    def test_a_damaged_or_foreign_code_says_where_to_get_one(self):
        os.environ.pop("CHARACTER_SITE_URL", None)
        i = interaction()
        run(ci.handle_import(i, "hello"))
        self.assertIn("LUGIC1", said(i))
        os.environ["CHARACTER_SITE_URL"] = "https://example.github.io/lugic/builder/"
        try:
            i = interaction()
            run(ci.handle_import(i, "hello"))
            self.assertIn("https://example.github.io/lugic/builder/", said(i))
        finally:
            os.environ.pop("CHARACTER_SITE_URL", None)

    def test_the_pool_the_site_assumed_is_pointed_out(self):
        reply = run(do_import({
            "v": 1, "name": "Dreamer", "pools": {"bp": 63, "sp": 0, "pp": 0},
            "stats": {"hp": 160, "power": 12, "speed": [6, 8]},
        }))
        self.assertIn("nothing was created", reply)
        self.assertIn("Build Points: the builder was set to 63", reply)


class ModalAndCommand(unittest.TestCase):
    def test_the_import_popup_fits_discord(self):
        modal = ci.ImportModal()
        children = modal.children
        self.assertLessEqual(len(children), 5)
        for child in children:
            self.assertLessEqual(len(child.label), 45)
            self.assertLessEqual(len(child.placeholder or ""), 100)
            self.assertLessEqual(child.max_length or 0, 4000)
        self.assertEqual(children[0].style.value, discord.TextStyle.paragraph.value)
        self.assertTrue(children[0].required)
        self.assertFalse(children[1].required)

    def test_the_command_exists_and_opens_the_popup(self):
        from cogs.character import CharacterCog
        names = [c.name for c in CharacterCog.__cog_app_commands__]
        self.assertIn("import", names)
        cog = CharacterCog.__new__(CharacterCog)
        i = interaction()
        i.response.send_modal = AsyncMock()
        run(CharacterCog.import_character.callback(cog, i))
        self.assertIsInstance(i.response.send_modal.call_args.args[0], ci.ImportModal)


if __name__ == "__main__":
    unittest.main()
