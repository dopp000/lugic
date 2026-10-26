"""game/emoji_icons.py: collects the bot's emoji ids and saves them for the builder site."""

import json
import os
import tempfile
import unittest
import urllib.error

from game import emoji_icons, emojis


class Collect(unittest.TestCase):
    def test_every_filled_id_is_found_and_none_is_skipped_silently(self):
        found = emoji_icons.collect()
        self.assertEqual(found["burn"], emojis.STATUS_EMOJI_IDS["burn"])
        self.assertEqual(found["slash"], emojis.DAMAGE_TYPE_EMOJI_IDS["slash"])
        self.assertEqual(found["hp"], emojis.STAT_EMOJI_IDS["hp"])
        self.assertEqual(found["coin_heads"], emojis.COIN_EMOJI_IDS["heads"])
        self.assertEqual(found["stagger_2"], emojis.STAGGER_TIER_EMOJI_IDS[2])
        self.assertTrue(all(isinstance(v, int) for v in found.values()))

    def test_the_names_the_page_asks_for_exist(self):
        found = emoji_icons.collect()
        for name in ("burn", "bleed", "tremor", "rupture", "sinking", "poise", "charge", "hp", "speed", "coin_heads"):
            self.assertIn(name, found, name)


class Download(unittest.TestCase):
    def test_images_are_saved_once_per_id_and_the_manifest_points_at_them(self):
        names = {"panic": 5, "low_morale": 5, "burn": 6}
        calls = []

        def getter(emoji_id):
            calls.append(emoji_id)
            return b"png" + str(emoji_id).encode()

        with tempfile.TemporaryDirectory() as folder:
            files, failed = emoji_icons.download(names, folder, getter)
            self.assertEqual(failed, [])
            self.assertEqual(sorted(calls), [5, 6])
            self.assertEqual(files["panic"], files["low_morale"])
            self.assertEqual(files["burn"], "icons/6.png")
            self.assertEqual(open(os.path.join(folder, "6.png"), "rb").read(), b"png6")
            path = emoji_icons.write_manifest(files, folder)
            self.assertEqual(json.load(open(path))["burn"], "icons/6.png")

    def test_a_failed_download_is_reported_and_left_out(self):
        def getter(emoji_id):
            if emoji_id == 7:
                raise urllib.error.URLError("no route")
            return b"x"

        with tempfile.TemporaryDirectory() as folder:
            files, failed = emoji_icons.download({"a": 7, "b": 8}, folder, getter)
            self.assertEqual(failed, ["a"])
            self.assertEqual(list(files), ["b"])

    def test_an_empty_answer_counts_as_a_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            files, failed = emoji_icons.download({"a": 9}, folder, lambda i: b"")
            self.assertEqual((files, failed), ({}, ["a"]))


if __name__ == "__main__":
    unittest.main()
