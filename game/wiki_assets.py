"""Downloads game images from the Limbus Company wiki for the character builder site.

The wiki pages the project owner shared (Status Effects, a character identity page, an enemy page) use small game icons:
the seven Sin symbols, the damage types, HP and Speed, coins, tiers, and an icon for every status. The site (docs/builder/)
can show them next to the bot's own emoji, but only if the pictures sit beside the page. Run this once from a machine with
internet access:

    python -m game.wiki_assets

It saves one image per name in docs/builder/wiki/, writes docs/builder/wiki/index.json (name to file) and a CREDITS.txt
saying where they came from. The page reads index.json when it loads and prefers these pictures over the emoji ones; with
no wiki folder it simply keeps using the emoji, so skipping this step breaks nothing. Run it again if the list below grows.

The site can also link these pictures straight from the wiki without any download (see remote_urls and the "wikiImages" list in
rules.js); downloading is for a page that loads faster, works offline, and does not lean on the wiki's servers.

The names on the left are the ones the site asks for (a status uses the bot's own key, so `burn` is the Burn icon). The
file titles on the right are the wiki's own, as seen in the pages' image tags. The images are Project Moon's game art, hosted
by the wiki; this is for the owner's fan project, so credit the wiki and Project Moon (CREDITS.txt does).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "builder", "wiki")
BASE = "https://limbuscompany.wiki.gg/images/"
USER_AGENT = "lugic-wiki-assets/1.0 (fan project builder site; small one-off download)"
PAUSE = 0.25   # seconds between requests, to be gentle with the wiki

# site name -> wiki file title
ASSETS: dict[str, str] = {
    # the seven Sins
    "sin_wrath": "LcbSinWrath.png", "sin_lust": "LcbSinLust.png", "sin_sloth": "LcbSinSloth.png",
    "sin_gluttony": "LcbSinGluttony.png", "sin_gloom": "LcbSinGloom.png", "sin_pride": "LcbSinPride.png", "sin_envy": "LcbSinEnvy.png",
    # damage types, stats and skill kinds
    "slash": "Slash.png", "pierce": "Pierce.png", "blunt": "Blunt.png",
    "hp": "HP.png", "speed": "SPEED.png", "defense": "Defense.png", "sanity": "Sanity.png", "evade": "Evade.png",
    "skill_attack": "SkillAttack.png", "skill_defense": "SkillDefense.png",
    # coins and tiers
    "coin": "Coin.png", "tier_1": "Tier1.png", "tier_2": "Tier2.png", "tier_3": "Tier3.png", "tier_4": "Tier4.png",
    # keyword statuses
    "burn": "Burn.png", "bleed": "Bleed.png", "tremor": "Tremor.png", "rupture": "Rupture.png",
    "sinking": "Sinking.png", "poise": "Poise.png", "charge": "Charge.png",
    # buffs and debuffs, in the same pairs the site's status list uses
    "power_up": "Power Up.png", "power_down": "Power Down.png",
    "haste": "Haste.png", "bind": "Bind.png",
    "damage_up": "Damage Up.png", "damage_down": "Damage Down.png",
    "protection": "Protection.png", "fragile": "Fragile.png",
    "slash_fragility": "Slash Fragility.png", "pierce_fragility": "Pierce Fragility.png", "blunt_fragility": "Blunt Fragility.png",
    "paralyze": "Paralyze.png", "poison": "Poison.png", "immobilized": "Immobilized.png", "panic": "General Panic.png",
    # Offense and Defense Level
    "offense_level_up": "Offense Level Up.png", "offense_level_down": "Offense Level Down.png",
    "defense_level_up": "Defense Level Up.png", "defense_level_down": "Defense Level Down.png",
}


def url_for(title: str) -> str:
    """The address of a wiki file: spaces become underscores, the rest is percent-encoded."""
    return BASE + urllib.parse.quote(title.replace(" ", "_"))


def fallback_url_for(title: str) -> str:
    """MediaWiki's own "give me this file" address, which redirects to wherever the file really is."""
    return "https://limbuscompany.wiki.gg/wiki/Special:FilePath/" + urllib.parse.quote(title.replace(" ", "_"))


def remote_urls(assets: dict[str, str] = ASSETS) -> dict[str, str]:
    """Every name with its address on the wiki. The site can point its pictures straight at these, so downloading is optional."""
    return {name: url_for(title) for name, title in sorted(assets.items())}


def fetch(title: str) -> bytes:
    """The direct image address first; if the wiki answers with anything but a picture, ask MediaWiki's file redirector."""
    last: Exception | None = None
    for address in (url_for(title), fallback_url_for(title)):
        request = urllib.request.Request(address, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = response.read()
            if looks_like_png(data):
                return data
        except (urllib.error.URLError, OSError) as error:
            last = error
    if last is not None:
        raise last
    raise ValueError("not a PNG: " + title)


def looks_like_png(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def download(assets: dict[str, str] = ASSETS, directory: str = ASSET_DIR, getter=fetch, pause: float = PAUSE) -> tuple[dict[str, str], list[str]]:
    """Saves each image as <name>.png. Returns ({name: relative file}, [names that failed]). Files already there are kept."""
    os.makedirs(directory, exist_ok=True)
    files: dict[str, str] = {}
    failed: list[str] = []
    for name in sorted(assets):
        filename = f"{name}.png"
        path = os.path.join(directory, filename)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            files[name] = "wiki/" + filename
            continue
        try:
            data = getter(assets[name])
        except (urllib.error.URLError, OSError, ValueError):
            failed.append(name)
            continue
        if not data or not looks_like_png(data):   # an error page or a redirect page is not a picture
            failed.append(name)
            continue
        with open(path, "wb") as handle:
            handle.write(data)
        files[name] = "wiki/" + filename
        if pause:
            time.sleep(pause)
    return files, failed


def write_manifest(files: dict[str, str], directory: str = ASSET_DIR) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "index.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(files, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with open(os.path.join(directory, "CREDITS.txt"), "w", encoding="utf-8") as handle:
        handle.write(
            "These images come from the Limbus Company wiki (https://limbuscompany.wiki.gg), which hosts game art that belongs to\n"
            "Project Moon. They are used here for a non-commercial fan project. Each file is named after the site name in\n"
            "game/wiki_assets.py; the wiki's own file title is listed there.\n"
        )
    return path


def main() -> int:
    files, failed = download()
    write_manifest(files)
    print(f"Saved {len(files)} of {len(ASSETS)} images in {ASSET_DIR}")
    if failed:
        print("Could not download: " + ", ".join(failed))
        print("Check that this machine can reach limbuscompany.wiki.gg, then run it again (files already saved are kept).")
    return 1 if failed and not files else 0


if __name__ == "__main__":
    sys.exit(main())
