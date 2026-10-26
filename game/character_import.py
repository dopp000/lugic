"""Reads the code the character builder site makes. Pure rules, no Discord code.

The site (docs/builder/) turns a character into a short text code. A player pastes it
into /character import. This module opens the code, checks that it is shaped like a
character (types, sizes, names), and turns it into the plain-data "build" request
(game/build_requests.py). It does not check the Build Point rules: that is the job of
check_build and the skill form, which run on the result.

Code format: a prefix, then base64url text.
  LUGIC1Z.<base64url of zlib-compressed JSON>   (what the site makes)
  LUGIC1.<base64url of plain JSON>              (used when the browser cannot compress)
The JSON has "v": 1, a name, and the parts of the build. Skills are the same text the
skill form takes, so the form's own checks run on them unchanged.
"""

import base64
import binascii
import json
import re
import zlib
from dataclasses import dataclass, field

from game.build_points import (
    DEFENSE_ARCHETYPES, HP_BASE, POWER_BASE, PP_COST_MAX, RANK_MAX, RANKED_STATUSES,
)
from game.character import STARTING_SPEED, validate_character_name
from game.conditions import MAX_TAG_LENGTH, MAX_TAGS
from game.passives import MAX_PASSIVES, PASSIVE_FLAVOUR_MAX, PASSIVE_KINDS, clean_flavour, tidy_flavour
from game.resistances import ALL_RESISTANCE_TYPES

VERSION = 1
PREFIX_COMPRESSED = "LUGIC1Z."
PREFIX_PLAIN = "LUGIC1."
MAX_CODE_CHARS = 12000   # the paste box on Discord holds 4000, this only guards other callers
MAX_JSON_BYTES = 60000   # after unzipping, so a tiny code cannot grow into something huge
MAX_SKILLS = 12
AVATAR_MAX = 300
DEFAULT_SPEED = STARTING_SPEED   # what /character create gives, so a build equal to it needs no approval
SKILL_LIMITS = {"name": 100, "stats": 100, "weight": 3, "statuses": 400, "triggers": 4000}
PASSIVE_LINE_MAX = 300
IMPORT_NOTE = "Made on the character builder"


@dataclass
class BuildSpec:
    name: str
    avatar: str | None
    params: dict           # the "build" request params: stats, ranks, archetypes, tags, passives, skills
    pools: dict = field(default_factory=dict)   # what the site assumed (BP, SP, PP), only for messages


def encode_code(data: dict, compress: bool = True) -> str:
    """Makes a code the way the site does. The bot only needs this for tests and tools."""
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if compress:
        raw = zlib.compress(raw, 9)
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return (PREFIX_COMPRESSED if compress else PREFIX_PLAIN) + body


def decode_code(text: str) -> tuple[dict | None, str | None]:
    """Opens a pasted code. Returns (the JSON as a dict, None) or (None, a message for the player)."""
    cleaned = "".join(str(text).split()).strip("`'\"<>“”‘’")
    upper = cleaned.upper()
    if upper.startswith(PREFIX_COMPRESSED.upper()):
        body, packed = cleaned[len(PREFIX_COMPRESSED):], True
    elif upper.startswith(PREFIX_PLAIN.upper()):
        body, packed = cleaned[len(PREFIX_PLAIN):], False
    else:
        return None, (
            "That doesn't look like a code from the character builder. A code starts with LUGIC1. "
            "Copy the whole text from the Export box on the site."
        )
    if len(cleaned) > MAX_CODE_CHARS:
        return None, "That code is too long to be a character."
    if not body or not re.fullmatch(r"[A-Za-z0-9_\-+/=]+", body):
        return None, "The code is damaged. Copy it again from the Export box, all of it."
    body = body.replace("+", "-").replace("/", "_").rstrip("=")
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        if packed:
            unzip = zlib.decompressobj()
            raw = unzip.decompress(raw, MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES or unzip.unconsumed_tail:
                return None, "That code unpacks to far more than a character."
        elif len(raw) > MAX_JSON_BYTES:
            return None, "That code is too long to be a character."
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, zlib.error, ValueError, UnicodeDecodeError):
        return None, "The code is damaged, or it was cut short when it was copied. Copy it again, all of it."
    if not isinstance(data, dict):
        return None, "The code does not hold a character."
    version = data.get("v")
    if version != VERSION:
        return None, (
            "This code is from a different version of the builder "
            f"(it says {version!r}, this bot reads version {VERSION}). Reload the site and export it again."
        )
    return data, None


# ---- reading the pieces ------------------------------------------------------

def _int(value, label: str, low: int, high: int) -> tuple[int | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        return None, f"{label} has to be a whole number."
    value = int(value)
    if not low <= value <= high:
        return None, f"{label} has to be between {low} and {high}."
    return value, None


def _text(value, label: str, limit: int, required: bool = True) -> tuple[str | None, str | None]:
    if value is None and not required:
        return "", None
    if not isinstance(value, str):
        return None, f"{label} has to be text."
    value = value.strip()
    if required and not value:
        return None, f"{label} is empty."
    if len(value) > limit:
        return None, f"{label} is longer than {limit} characters."
    return value, None


def _read_stats(raw) -> tuple[dict, str | None]:
    """Only what differs from a new character is kept. Out-of-range numbers are left for check_build to explain."""
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        return {}, "The stats part of the code is not readable."
    out: dict = {}
    for key, label, base, param in (
        ("hp", "HP", HP_BASE, "hp"), ("power", "Power", POWER_BASE, "power"),
        ("offense", "Offense Level", 0, "offense_level"), ("defense", "Defense Level", 0, "defense_level"),
    ):
        if key not in raw:
            continue
        value, error = _int(raw[key], label, 0, 1000)
        if error:
            return {}, error
        if value != base:
            out[param] = value
    if "speed" in raw:
        speed = raw["speed"]
        if not (isinstance(speed, list) and len(speed) == 2):
            return {}, "Speed has to be a lowest and a highest number."
        lo, error = _int(speed[0], "Lowest Speed", 0, 100)
        if error:
            return {}, error
        hi, error = _int(speed[1], "Highest Speed", 0, 100)
        if error:
            return {}, error
        if (lo, hi) != DEFAULT_SPEED:
            out["speed_min"], out["speed_max"] = lo, hi
    if raw.get("stagger_off"):
        tiers = raw["stagger_off"]
        if not (isinstance(tiers, list) and all(isinstance(t, int) and not isinstance(t, bool) for t in tiers)):
            return {}, "Stagger tiers to remove have to be numbers."
        out["stagger_disabled_tiers"] = sorted(set(tiers))
    if raw.get("resist"):
        resist = raw["resist"]
        if not isinstance(resist, dict):
            return {}, "Resistances are not readable."
        chosen: dict[str, float] = {}
        for key, value in resist.items():
            key = str(key).lower()
            if key not in ALL_RESISTANCE_TYPES:
                return {}, f"{key} is not a resistance type."
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 5:
                return {}, f"{key.capitalize()} resistance has to be a number from 0 to 5."
            if round(float(value), 2) != 1.0:
                chosen[key] = round(float(value), 2)
        if chosen:
            out["resistances"] = chosen
    return out, None


def _read_ranks(raw) -> tuple[dict, str | None]:
    if not raw:
        return {}, None
    if not isinstance(raw, dict):
        return {}, "Status ranks are not readable."
    out: dict[str, int] = {}
    for key, value in raw.items():
        key = str(key).lower()
        if key not in RANKED_STATUSES:
            return {}, f"{key} is not a status that can have a rank."
        rank, error = _int(value, f"{key.capitalize()} rank", 0, RANK_MAX)
        if error:
            return {}, error
        if rank:
            out[key] = rank
    return out, None


def _read_archetypes(raw) -> tuple[list, str | None]:
    if not raw:
        return [], None
    if not isinstance(raw, list):
        return [], "Defense archetypes are not readable."
    out: list[str] = []
    for key in raw:
        key = str(key).lower()
        if key not in DEFENSE_ARCHETYPES:
            return [], f"{key} is not a defense archetype."
        if key not in out:
            out.append(key)
    return out, None


def _read_tags(raw) -> tuple[list, str | None]:
    if not raw:
        return [], None
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        return [], "Group tags are not readable."
    tags = [" ".join(t.split()) for t in raw if t.strip()]
    if len(tags) > MAX_TAGS:
        return [], f"A character can have at most {MAX_TAGS} group tags."
    for tag in tags:
        if len(tag) > MAX_TAG_LENGTH:
            return [], f"A group tag is at most {MAX_TAG_LENGTH} characters, `{tag[:40]}` is too long."
    return tags, None


def _read_passives(raw) -> tuple[list, str | None]:
    if not raw:
        return [], None
    if not isinstance(raw, list):
        return [], "Passives are not readable."
    if len(raw) > MAX_PASSIVES:
        return [], f"A character can have at most {MAX_PASSIVES} passives."
    out = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            return [], "A passive in the code is not readable."
        name, error = _text(item.get("name"), "A passive's name", 40)
        if error:
            return [], error
        if name.lower() in seen:
            return [], f"Two passives are called {name}."
        seen.add(name.lower())
        kind = item.get("kind", "passive")
        if kind not in PASSIVE_KINDS:
            return [], f"Passive {name} has to be a Passive or a Support Passive."
        cost, error = _int(item.get("cost"), f"The price of {name}", 1, PP_COST_MAX)
        if error:
            return [], error
        lines = item.get("lines")
        if not (isinstance(lines, list) and 1 <= len(lines) <= 3 and all(isinstance(l, str) for l in lines)):
            return [], f"Passive {name} needs one to three rule lines."
        lines = [l.strip() for l in lines if l.strip()]
        if not lines or any(len(l) > PASSIVE_LINE_MAX for l in lines):
            return [], f"Passive {name} needs one to three rule lines of at most {PASSIVE_LINE_MAX} characters."
        entry = {"name": name, "kind": kind, "lines": lines, "cost": cost, "note": IMPORT_NOTE}
        flavour = item.get("flavour")
        if flavour is not None:       # optional: the story behind the passive, shown in italics on the character sheet
            if not isinstance(flavour, str):
                return [], f"The flavour text of {name} has to be text."
            if len(tidy_flavour(flavour)) > PASSIVE_FLAVOUR_MAX:
                return [], f"The flavour text of {name} is longer than {PASSIVE_FLAVOUR_MAX} characters."
            if clean_flavour(flavour):
                entry["flavour"] = clean_flavour(flavour)
        out.append(entry)
    return out, None


def _read_skills(raw) -> tuple[list, str | None]:
    if not raw:
        return [], None
    if not isinstance(raw, list):
        return [], "Skills are not readable."
    if len(raw) > MAX_SKILLS:
        return [], f"A code can hold at most {MAX_SKILLS} skills."
    out = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            return [], "A skill in the code is not readable."
        skill: dict[str, str] = {}
        for key, limit in SKILL_LIMITS.items():
            default = {"weight": "1", "statuses": "none", "triggers": ""}.get(key)
            value = item.get(key, default)
            text, error = _text(value, f"A skill's {key}", limit, required=key in ("name", "stats"))
            if error:
                return [], error
            skill[key] = text
        if skill["name"].lower() in seen:
            return [], f"Two skills are called {skill['name']}."
        seen.add(skill["name"].lower())
        out.append(skill)
    return out, None


def read_spec(data: dict) -> tuple[BuildSpec | None, str | None]:
    """Turns the code's JSON into a BuildSpec, or says in plain words what is wrong with it."""
    name, error = _text(data.get("name"), "The name", 200)
    if error:
        return None, error
    name_problem = validate_character_name(name)
    if name_problem:
        return None, name_problem
    avatar = data.get("avatar")
    if avatar in (None, ""):
        avatar = None
    else:
        if not isinstance(avatar, str) or not re.fullmatch(r"https://\S+", avatar.strip()) or len(avatar.strip()) > AVATAR_MAX:
            return None, f"The portrait link has to start with https:// and be at most {AVATAR_MAX} characters."
        avatar = avatar.strip()
    params: dict = {}
    for key, reader in (
        ("stats", _read_stats), ("ranks", _read_ranks), ("archetypes", _read_archetypes),
        ("tags", _read_tags), ("passives", _read_passives), ("skills", _read_skills),
    ):
        value, error = reader(data.get(key))
        if error:
            return None, error
        if value:
            params[key] = value
    pools = data.get("pools") if isinstance(data.get("pools"), dict) else {}
    pools = {k: v for k, v in pools.items() if k in ("bp", "sp", "pp") and isinstance(v, int) and not isinstance(v, bool)}
    return BuildSpec(name=name, avatar=avatar, params=params, pools=pools), None


def needs_approval(params: dict) -> bool:
    """True when the build changes anything that costs points. Skills alone do not: players add those themselves."""
    return any(params.get(k) for k in ("stats", "ranks", "archetypes", "tags", "passives"))


def pool_hint(spec: BuildSpec, character) -> str:
    """When the site was set to bigger pools than the character has, say so. Empty when they agree."""
    bits = []
    for key, label, have in (
        ("bp", "Build Points", character.bp_total), ("sp", "Skill Points", character.sp_total),
        ("pp", "Passive Points", character.pp_total),
    ):
        planned = spec.pools.get(key)
        if planned is not None and planned > have:
            bits.append(f"{label}: the builder was set to {planned}, a new character has {have}")
    if not bits:
        return ""
    return "\n" + "; ".join(bits) + ". Set the pools on the site to what you really have."
