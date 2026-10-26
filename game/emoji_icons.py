"""Downloads the bot's custom emoji as image files for the character builder site.

The emoji live on Discord, keyed by id in game/emojis.py. The site (docs/builder/) can show
the same icons next to statuses and stats, but only if the pictures sit beside the page. Run
this once from a machine with internet access:

    python -m game.emoji_icons

It saves one image per emoji in docs/builder/icons/ and writes docs/builder/icons/index.json
(name to file). The page reads index.json when it loads; with no icons folder it simply shows
text, so skipping this step breaks nothing. Run it again after uploading new emoji.
"""

import json
import os
import sys
import urllib.error
import urllib.request

from game import emojis

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "builder", "icons")
CDN = "https://cdn.discordapp.com/emojis/{id}.png?size=96&quality=lossless"
USER_AGENT = "lugic-emoji-icons/1.0"


def collect() -> dict[str, int]:
    """Every emoji the bot knows by id, as {name the site uses: emoji id}. Unfilled (None) ids are skipped."""
    found: dict[str, int] = {}

    def take(prefix: str, table: dict) -> None:
        for key, emoji_id in table.items():
            if isinstance(emoji_id, int):
                found[f"{prefix}{key}"] = emoji_id

    take("", emojis.STATUS_EMOJI_IDS)
    take("", emojis.DAMAGE_TYPE_EMOJI_IDS)
    take("", emojis.STAT_EMOJI_IDS)
    take("coin_", emojis.COIN_EMOJI_IDS)
    take("stagger_", emojis.STAGGER_TIER_EMOJI_IDS)
    take("slot_", emojis.SKILL_SLOT_EMOJI_IDS)
    return found


def fetch(emoji_id: int) -> bytes:
    request = urllib.request.Request(CDN.format(id=emoji_id), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def download(names: dict[str, int], directory: str = ICON_DIR, getter=fetch) -> tuple[dict[str, str], list[str]]:
    """Saves each distinct emoji once. Returns ({name: relative file}, [names that failed])."""
    os.makedirs(directory, exist_ok=True)
    files: dict[str, str] = {}
    failed: list[str] = []
    saved: dict[int, str] = {}
    for name in sorted(names):
        emoji_id = names[name]
        if emoji_id not in saved:
            try:
                data = getter(emoji_id)
            except (urllib.error.URLError, OSError, ValueError):
                failed.append(name)
                continue
            if not data:
                failed.append(name)
                continue
            filename = f"{emoji_id}.png"
            with open(os.path.join(directory, filename), "wb") as handle:
                handle.write(data)
            saved[emoji_id] = filename
        files[name] = "icons/" + saved[emoji_id]
    return files, failed


def write_manifest(files: dict[str, str], directory: str = ICON_DIR) -> str:
    path = os.path.join(directory, "index.json")
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(files, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def main() -> int:
    names = collect()
    files, failed = download(names)
    write_manifest(files)
    print(f"Saved {len(set(files.values()))} images for {len(files)} names in {ICON_DIR}")
    if failed:
        print("Could not download: " + ", ".join(failed))
        print("Check that this machine can reach cdn.discordapp.com, then run it again.")
    return 1 if failed and not files else 0


if __name__ == "__main__":
    sys.exit(main())
