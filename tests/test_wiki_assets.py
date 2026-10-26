"""game/wiki_assets.py: the list of wiki images the builder site asks for, and the saving of them."""

import json
import os
import tempfile
import unittest
import urllib.error

from game import builder_rules, wiki_assets

PNG = b"\x89PNG\r\n\x1a\n" + b"data"


class Manifest(unittest.TestCase):
    def test_the_site_gets_every_icon_it_can_use(self):
        names = set(wiki_assets.ASSETS)
        for sin in ("wrath", "lust", "sloth", "gluttony", "gloom", "pride", "envy"):
            self.assertIn("sin_" + sin, names)
        for key in ("slash", "pierce", "blunt", "hp", "speed", "coin"):
            self.assertIn(key, names)
        for status in builder_rules.status_guide():
            self.assertIn(status["key"], names, status["key"])   # a wiki icon for every status in the list

    def test_every_title_is_a_png_and_every_name_is_a_plain_file_name(self):
        for name, title in wiki_assets.ASSETS.items():
            self.assertTrue(title.endswith(".png"), title)
            self.assertRegex(name, r"^[a-z0-9_]+$")

    def test_addresses_follow_the_wiki_rules(self):
        self.assertEqual(wiki_assets.url_for("Power Up.png"), "https://limbuscompany.wiki.gg/images/Power_Up.png")
        self.assertEqual(wiki_assets.url_for("SPEED.png"), "https://limbuscompany.wiki.gg/images/SPEED.png")
        self.assertTrue(wiki_assets.fallback_url_for("Power Up.png").endswith("/Special:FilePath/Power_Up.png"))


class Linking(unittest.TestCase):
    """The page can point its pictures straight at the wiki, so nothing has to be downloaded."""

    def test_every_name_has_an_address_on_the_wiki_and_the_site_gets_the_same_list(self):
        urls = wiki_assets.remote_urls()
        self.assertEqual(set(urls), set(wiki_assets.ASSETS))
        for name, address in urls.items():
            self.assertTrue(address.startswith("https://limbuscompany.wiki.gg/images/"), name)
            self.assertNotIn(" ", address)
        self.assertEqual(builder_rules.rules()["wikiImages"], urls)

    def test_the_page_names_the_wiki_and_does_not_send_a_referrer(self):
        folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "builder")
        with open(os.path.join(folder, "app.js"), encoding="utf-8") as handle:
            app = handle.read()
        with open(os.path.join(folder, "index.html"), encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn("img.referrerPolicy = 'no-referrer'", app)
        self.assertIn("wikiBroken = true", app)            # one refused picture switches linking off for the visit
        self.assertIn("limbuscompany.wiki.gg", html)         # the footer says where the icons come from
        self.assertNotIn("sends nothing anywhere", html)     # no longer true once the icons are linked


class Download(unittest.TestCase):
    def test_images_are_saved_and_the_manifest_points_at_them(self):
        assets = {"burn": "Burn.png", "power_up": "Power Up.png"}
        asked = []

        def getter(title):
            asked.append(title)
            return PNG

        with tempfile.TemporaryDirectory() as folder:
            files, failed = wiki_assets.download(assets, folder, getter, pause=0)
            self.assertEqual(failed, [])
            self.assertEqual(files, {"burn": "wiki/burn.png", "power_up": "wiki/power_up.png"})
            self.assertEqual(open(os.path.join(folder, "burn.png"), "rb").read(), PNG)
            wiki_assets.write_manifest(files, folder)
            self.assertEqual(json.load(open(os.path.join(folder, "index.json"))), files)
            self.assertTrue(os.path.exists(os.path.join(folder, "CREDITS.txt")))
            wiki_assets.download(assets, folder, getter, pause=0)
            self.assertEqual(sorted(asked), ["Burn.png", "Power Up.png"])   # the second run found them and asked for nothing

    def test_a_failed_or_non_picture_answer_is_reported_and_nothing_is_saved_for_it(self):
        def getter(title):
            if title == "Burn.png":
                raise urllib.error.URLError("no route")
            return b"<html>not found</html>"

        with tempfile.TemporaryDirectory() as folder:
            files, failed = wiki_assets.download({"burn": "Burn.png", "bleed": "Bleed.png"}, folder, getter, pause=0)
            self.assertEqual(files, {})
            self.assertEqual(sorted(failed), ["bleed", "burn"])
            self.assertEqual(os.listdir(folder), [])


if __name__ == "__main__":
    unittest.main()
