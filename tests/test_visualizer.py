"""The Visualizer tab of the character builder site: one tall picture of a character's skills or passives, in the look of an identity teaser.

The plain functions (colours, wrapping, the WARNING strip, where the picture lands) are run in Node; the picture itself is checked in a real
browser when Playwright is installed (it is skipped otherwise, like the other browser tests).
"""
import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game import builder_rules  # noqa: E402

NODE = shutil.which("node")
SITE = os.path.join(ROOT, "docs", "builder")
CHECK_JS = os.path.join(ROOT, "tests", "visualizer_check.js")
PAGE_JS = os.path.join(ROOT, "tests", "visualizer_page.js")


def read(name: str) -> str:
    with open(os.path.join(SITE, name), encoding="utf-8") as handle:
        return handle.read()


class VisualizerIsPartOfTheSite(unittest.TestCase):
    def test_the_script_is_loaded_before_the_page_script_and_is_versioned(self):
        html = read("index.html")
        self.assertIn("visualizer.js", builder_rules.VERSIONED_FILES)
        self.assertLess(html.index('src="visualizer.js'), html.index('src="app.js'))

    def test_the_tab_is_the_last_one_and_has_a_picture_for_the_canvas(self):
        app = read("app.js")
        build = re.search(r"  function build\(\) \{.*?\n  \}\n", app, re.S).group(0)
        self.assertRegex(build, r"\['Visualizer', buildVisualizer\]\s*\n?\s*\]")
        self.assertIn("'canvas'", app)
        self.assertIn("Download picture", app)

    def test_the_lettering_is_shipped_with_the_page(self):
        css = read("style.css")
        for name in ("pretendard-latin-400.woff2", "pretendard-latin-600.woff2", "bebas-neue-latin.woff2", "Pretendard-OFL.txt", "BebasNeue-OFL.txt"):
            self.assertGreater(os.path.getsize(os.path.join(SITE, "fonts", name)), 1000, name)
        for needed in ('font-family: "Pretendard"', 'font-family: "Bebas Neue"', "fonts/pretendard-latin-400.woff2", "fonts/pretendard-latin-600.woff2", "fonts/bebas-neue-latin.woff2"):
            self.assertIn(needed, css, needed)
        script = read("visualizer.js")
        for family in ("Pretendard", "Bebas Neue", "Mikodacs"):         # the third is the wiki's own, used for names and titles once its file is added
            self.assertIn(family, script, family)
        self.assertIn("mikodacs.ttf", read(os.path.join("fonts", "README.txt")))

    def test_there_are_no_rarity_marks(self):
        # taken out on request: they are not a thing for now, so no drawing, no control and no saved setting for them
        for name in ("app.js", "visualizer.js"):
            self.assertNotRegex(read(name), r"(?i)rarity|\bmarks\b", name)

    def test_the_picture_never_asks_the_network_for_anything(self):
        script = read("visualizer.js")
        self.assertNotRegex(script, r"https?://")
        self.assertNotIn("fetch(", script)


@unittest.skipUnless(NODE, "needs Node")
class VisualizerFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = {
            "lines": ["[On Use] Gain 2 Protection", "[Clash Lose] Lose 5 Sanity", "[On Hit] Inflict 2 Burn"],
            "wraps": [
                {"pieces": [{"text": "aaa bbb ccc", "color": "#111"}], "width": 70, "unit": 10},
                {"pieces": [{"text": "abcdefghijkl", "color": "#222"}], "width": 50, "unit": 10},
                {"pieces": [{"text": "red ", "color": "#f00"}, {"text": "and blue", "color": "#00f"}], "width": 1000, "unit": 10},
                {"pieces": [], "width": 50},
            ],
            "strips": [{"width": 1000, "word": 200, "bars": 100, "gap": 20}, {"width": 100, "word": 400, "bars": 100, "gap": 20}],
            "arts": [
                {"art": {"a": 2, "x": 0, "y": 0, "zoom": 1}, "width": 1500, "height": 1500},
                {"art": {"a": 0.5, "x": 0, "y": 0, "zoom": 2}, "width": 1500, "height": 3000},
                {"art": {"a": 1, "x": 5, "y": -5, "zoom": 1}, "width": 1500, "height": 1500},
                {"art": {"a": 1, "x": 0.1, "y": 0, "zoom": 3}, "width": 1500, "height": 2400},
            ],
        }
        done = subprocess.run([NODE, CHECK_JS], input=json.dumps(payload), capture_output=True, text=True, check=True)
        cls.out = json.loads(done.stdout)

    def test_the_picture_is_1500_wide_and_starts_as_a_square(self):
        self.assertEqual(self.out["constants"], {"width": 1500, "minHeight": 1500, "stripHeight": 64})

    def test_every_skill_sin_has_a_colour(self):
        self.assertEqual(sorted(self.out["sinKeys"]), sorted(["wrath", "lust", "sloth", "gluttony", "gloom", "pride", "envy", "none"]))

    def test_timing_tags_and_status_words_get_the_teasers_colours(self):
        use, lose, hit = self.out["pieces"]
        ink = self.out["colours"]
        self.assertEqual(use[0], {"text": "[On Use]", "color": ink["use"]})
        self.assertEqual(lose[0], {"text": "[Clash Lose]", "color": ink["lose"]})
        self.assertEqual(hit[0], {"text": "[On Hit]", "color": ink["hit"]})
        for line in (use, lose, hit):
            self.assertEqual(line[1], {"text": " ", "color": ink["cream"]})      # a tag is followed by its space
        self.assertEqual(use[-1], {"text": "Protection", "color": ink["positive"]})
        self.assertEqual(lose[-1], {"text": "Sanity", "color": ink["negative"]})
        self.assertEqual(hit[-1], {"text": "Burn", "color": ink["negative"]})

    def test_lines_wrap_at_the_width_without_splitting_words(self):
        lines = self.out["wraps"][0]
        self.assertEqual([line["text"] for line in lines], ["aaa bbb", "ccc"])
        self.assertTrue(all(line["width"] <= 70 for line in lines))

    def test_a_word_wider_than_the_line_is_cut_where_it_no_longer_fits(self):
        lines = self.out["wraps"][1]
        self.assertEqual([line["text"] for line in lines], ["abcde", "fghij", "kl"])
        self.assertTrue(all(line["width"] <= 50 for line in lines))

    def test_each_word_keeps_its_own_colour_and_empty_text_is_one_empty_line(self):
        self.assertEqual(self.out["wraps"][2][0]["colours"], ["#f00", "#00f", "#00f"])
        self.assertEqual(self.out["wraps"][3], [{"text": "", "width": 0, "colours": []}])

    def test_the_strip_repeats_the_word_and_the_bars_across_the_width_and_is_cut_off_at_both_ends(self):
        layout = self.out["strips"][0]
        self.assertEqual([part["kind"] for part in layout[:4]], ["word", "bars", "word", "bars"])
        self.assertLess(layout[0]["x"], 0)                                   # starts left of the edge: the first word is cut
        words = [part["x"] for part in layout if part["kind"] == "word"]
        self.assertEqual({b - a for a, b in zip(words, words[1:])}, {200 + 20 + 100 + 20})
        self.assertGreaterEqual(layout[-2]["x"], 0)
        self.assertLess(layout[-2]["x"], 1000)                               # the last word starts inside the picture: its end is cut
        for part, nxt in zip(layout, layout[1:]):
            self.assertLess(part["x"], nxt["x"])
        self.assertTrue(self.out["strips"][1])                               # a word wider than the picture still draws

    def test_the_picture_always_covers_the_whole_canvas(self):
        for rect, size in zip(self.out["arts"], [(1500, 1500), (1500, 3000), (1500, 1500), (1500, 2400)]):
            width, height = size
            self.assertLessEqual(round(rect["x"], 3), 0)
            self.assertLessEqual(round(rect["y"], 3), 0)
            self.assertGreaterEqual(round(rect["x"] + rect["w"], 3), width)
            self.assertGreaterEqual(round(rect["y"] + rect["h"], 3), height)

    def test_a_picture_at_zoom_one_that_already_fits_cannot_be_pushed_off(self):
        rect = self.out["arts"][2]
        self.assertEqual((rect["x"], rect["y"], rect["w"], rect["h"]), (0, 0, 1500, 1500))


def _playwright_available() -> bool:
    return bool(NODE) and subprocess.run([NODE, "-e", "require('playwright')"], cwd=ROOT, capture_output=True).returncode == 0


@unittest.skipUnless(_playwright_available(), "needs Playwright for Node (only used to open the page in a browser)")
class VisualizerInABrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        handler = functools.partial(Quiet, directory=SITE)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        run = subprocess.run([NODE, PAGE_JS, base], cwd=ROOT, capture_output=True, text=True, timeout=240)
        if run.returncode != 0:
            raise unittest.SkipTest("the browser could not be started here: " + run.stderr[-300:])
        cls.found = json.loads(run.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_it_is_the_last_tab_and_nothing_failed_on_the_page(self):
        self.assertEqual(self.found["tab"][-1], "Visualizer")
        self.assertEqual(self.found["errors"], [])
        self.assertEqual(self.found["tallErrors"], [])

    def test_a_small_build_is_a_1500_px_square_and_says_what_it_shows(self):
        self.assertEqual(self.found["small"], [1500, 1500])
        self.assertEqual(self.found["label"], "Rien: 1 skill, as a picture")

    def test_more_skills_make_it_taller_but_never_wider(self):
        width, height = self.found["tall"]
        self.assertEqual(width, 1500)
        self.assertGreater(height, 1500)

    def test_the_passives_and_the_strip_words_change_the_picture_and_the_passives_start_square_too(self):
        self.assertEqual(self.found["passivesSize"], [1500, 1500])
        self.assertNotEqual(self.found["passivesFingerprint"], self.found["skillsFingerprint"])
        self.assertNotEqual(self.found["stripWordFingerprint"], self.found["skillsFingerprint"])

    def test_without_a_picture_the_strip_letters_are_the_dim_accent_colour(self):
        self.assertEqual(self.found["noPicture"]["cyan"], 0)
        self.assertGreater(self.found["noPicture"]["bright"], 1000)           # the letters and bars are there

    def test_with_a_picture_the_body_is_dimmed_and_the_strip_letters_show_it_inverted_at_full_colour(self):
        # the picture is pure red: dimmed behind the page, and through the letters pure cyan, which is red inverted, not darkened or see-through
        look = self.found["withPicture"]
        red, green, blue = look["body"]
        self.assertTrue(40 < red < 200 and green < 30 and blue < 30, look["body"])
        self.assertGreater(look["cyan"], 2000)
        self.assertEqual(self.found["pictureSize"], [1500, 1500])

    def test_the_picture_is_kept_for_the_next_visit_and_removing_it_takes_the_colour_away(self):
        self.assertEqual(self.found["afterReload"]["cyan"], self.found["withPicture"]["cyan"])
        self.assertEqual(self.found["afterRemove"]["cyan"], 0)

    def test_download_saves_the_same_picture_as_a_png(self):
        download = self.found["download"]
        self.assertEqual(download["name"], "Rien-skills.png")
        self.assertEqual(download["signature"], "89504e470d0a1a0a")
        self.assertEqual((download["width"], download["height"]), (1500, 1500))
        self.assertGreater(download["bytes"], 20000)

    def test_on_a_phone_the_picture_shrinks_to_the_screen(self):
        self.assertTrue(self.found["phone"]["fits"])
        self.assertLessEqual(self.found["phone"]["shown"], 390)


if __name__ == "__main__":
    unittest.main()
