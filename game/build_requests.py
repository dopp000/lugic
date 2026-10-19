"""Player build requests. Pure rules and storage, no Discord code.

A player cannot change anything that costs points (stats, resistances, status
ranks, defense archetypes, tags, identity passives), so they ask. A request
stores one change as plain data. `try_change` runs it on a copy of the
character and checks the Build Point rules, so a request that cannot work is
turned away before an admin sees it, and checked again when the admin approves.
"""

import copy
import datetime
import json
import os
from dataclasses import dataclass, field

from game.build_points import (
    DEFENSE_ARCHETYPES, RANKED_STATUSES, check_build, defense_label, passive_points_spent,
)
from game.conditions import MAX_TAG_LENGTH, MAX_TAGS, norm_tag
from game.passives import MAX_PASSIVES, followup_problem, parse_passive

REQUESTS_PATH = "data/requests.json"
KINDS = ("stats", "rank", "archetype", "tags", "passive")
KIND_LABELS = {
    "stats": "Stats", "rank": "Status rank", "archetype": "Defense archetype",
    "tags": "Group tags", "passive": "Identity passive",
}
MAX_OPEN_PER_PLAYER = 3
REASON_MAX = 300


@dataclass
class Request:
    id: int
    owner_id: int
    character: str
    kind: str
    params: dict
    reason: str = ""
    status: str = "open"  # open, approved, denied, cancelled
    created: str = ""
    decided_by: int | None = None
    decision_note: str = ""
    channel_id: int | None = None
    message_id: int | None = None


def _blank() -> dict:
    return {"next_id": 1, "review_channel": None, "requests": []}


def _load() -> dict:
    if not os.path.exists(REQUESTS_PATH):
        return _blank()
    try:
        with open(REQUESTS_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _blank()
    for key, value in _blank().items():
        data.setdefault(key, value)
    return data


def _save(data: dict):
    os.makedirs(os.path.dirname(REQUESTS_PATH), exist_ok=True)
    with open(REQUESTS_PATH + ".tmp", "w") as f:
        json.dump(data, f, indent=2)
    os.replace(REQUESTS_PATH + ".tmp", REQUESTS_PATH)


def _to_request(raw: dict) -> Request:
    return Request(**{k: v for k, v in raw.items() if k in Request.__dataclass_fields__})


def get_review_channel() -> int | None:
    return _load()["review_channel"]


def set_review_channel(channel_id: int | None):
    data = _load()
    data["review_channel"] = channel_id
    _save(data)


def create_request(owner_id: int, character: str, kind: str, params: dict, reason: str) -> Request:
    data = _load()
    req = Request(
        id=data["next_id"], owner_id=owner_id, character=character, kind=kind, params=params,
        reason=reason.strip()[:REASON_MAX],
        created=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    )
    data["next_id"] += 1
    data["requests"].append(req.__dict__)
    _save(data)
    return req


def get_request(request_id: int) -> Request | None:
    for raw in _load()["requests"]:
        if raw["id"] == request_id:
            return _to_request(raw)
    return None


def save_request(req: Request):
    data = _load()
    for i, raw in enumerate(data["requests"]):
        if raw["id"] == req.id:
            data["requests"][i] = req.__dict__
            break
    else:
        data["requests"].append(req.__dict__)
    _save(data)


def list_requests(owner_id: int | None = None, status: str | None = None) -> list[Request]:
    out = [_to_request(r) for r in _load()["requests"]]
    if owner_id is not None:
        out = [r for r in out if r.owner_id == owner_id]
    if status is not None:
        out = [r for r in out if r.status == status]
    return out


def open_count(owner_id: int) -> int:
    return len(list_requests(owner_id=owner_id, status="open"))


def has_open_duplicate(owner_id: int, character: str, kind: str) -> bool:
    return any(r.character.lower() == character.lower() and r.kind == kind
               for r in list_requests(owner_id=owner_id, status="open"))


def _apply(character, kind: str, params: dict) -> tuple[list[str], str | None]:
    """Changes `character` in place. Returns (what changed, error)."""
    changes: list[str] = []
    if kind == "stats":
        if params.get("hp") is not None:
            character.hp = character.max_hp = params["hp"]
            changes.append(f"HP {params['hp']}")
        if params.get("speed_min") is not None:
            character.speed_min, character.speed_max = params["speed_min"], params["speed_max"]
            changes.append(f"Speed {params['speed_min']}-{params['speed_max']}")
        if params.get("power") is not None:
            character.power = params["power"]
            changes.append(f"Power {params['power']}")
        if params.get("offense_level") is not None:
            character.offense_level = params["offense_level"]
            changes.append(f"Offense Level {params['offense_level']}")
        if params.get("defense_level") is not None:
            character.defense_level = params["defense_level"]
            changes.append(f"Defense Level {params['defense_level']}")
        if params.get("stagger_disabled_tiers") is not None:
            off = set(params["stagger_disabled_tiers"])
            if 1 in off or not off <= {2, 3}:
                return [], "Only Stagger Tier 2 and/or 3 can be removed."
            character.stagger_tiers_enabled = [True, 2 not in off, 3 not in off]
            changes.append("Stagger tiers removed: " + (", ".join(str(t) for t in sorted(off)) or "none"))
        if params.get("resistances"):
            character.resistances.update(params["resistances"])
            changes.append(", ".join(f"{t.capitalize()} {v}x" for t, v in params["resistances"].items()))
        if not changes:
            return [], "A stats request needs at least one change."
    elif kind == "rank":
        status, rank = params["status"], params["rank"]
        if status not in RANKED_STATUSES:
            return [], f"{status} is not a status that has ranks."
        if rank:
            character.status_ranks[status] = rank
            changes.append(f"{status.capitalize()} rank {rank}")
        else:
            character.status_ranks.pop(status, None)
            changes.append(f"no {status.capitalize()} rank")
    elif kind == "archetype":
        key = params["archetype"]
        if key not in DEFENSE_ARCHETYPES:
            return [], f"{key} is not a defense archetype."
        owned = [k for k in character.defense_archetypes if k != key]
        if params.get("own", True):
            owned.append(key)
        character.defense_archetypes = owned
        changes.append(("the " + defense_label(key) + " archetype") if params.get("own", True)
                       else ("no " + defense_label(key) + " archetype"))
    elif kind == "tags":
        chosen: list[str] = []
        for raw in params.get("tags", []):
            tag = " ".join(str(raw).strip().split())
            if not tag:
                continue
            if len(tag) > MAX_TAG_LENGTH:
                return [], f"A tag is at most {MAX_TAG_LENGTH} characters, `{tag[:40]}` is too long."
            if norm_tag(tag) in [norm_tag(t) for t in chosen]:
                continue
            chosen.append(tag)
        character.tags = chosen[:MAX_TAGS]
        changes.append("tags: " + (", ".join(character.tags) if character.tags else "none"))
    elif kind == "passive":
        entry = {
            "name": params["name"], "kind": params["kind"], "lines": params["lines"],
            "cost": params["cost"], "note": params.get("note", ""),
        }
        if any(p["name"].lower() == entry["name"].lower() for p in character.passives):
            return [], f"{character.name} already has a passive called {entry['name']}."
        if len(character.passives) >= MAX_PASSIVES:
            return [], f"A character can have at most {MAX_PASSIVES} passives."
        kinds = {k: cs.kind for k, cs in character.custom_statuses.items()}
        parsed, error = parse_passive(entry["lines"], kinds)
        if error:
            return [], error
        problem = followup_problem(character, parsed)
        if problem:
            return [], problem
        character.passives.append(entry)
        used = passive_points_spent(character)
        if used > character.pp_total:
            return [], (
                f"{character.name} has {character.pp_total} Passive Points and this would use {used}. "
                "An admin can grant more for a GM-approved moment."
            )
        changes.append(f"passive {entry['name']} ({entry['cost']} PP)")
    else:
        return [], f"Unknown request type {kind}."
    return changes, None


def try_change(character, kind: str, params: dict) -> tuple[str, str | None, "object | None"]:
    """Runs a change on a copy. Returns (summary, error, changed copy). The copy is what an approval saves."""
    trial = copy.deepcopy(character)
    changes, error = _apply(trial, kind, params)
    if error:
        return "", error, None
    if not character.bp_exempt:
        now = check_build(trial).problems
        before = check_build(character).problems
        fresh = [p for p in now if p not in before]
        # A change may leave an old problem standing, but may not add a new one
        # unless it also fixes at least as many (same rule as /character edit).
        if fresh and len(now) >= len(before):
            lines = "\n".join(f"- {p}" for p in now[:8])
            return "", f"That would break the Build Point rules:\n{lines}", None
    return ", ".join(changes), None, trial


def cost_preview(character, changed) -> str:
    """One line comparing the budget before and after a change, for the player and the admin."""
    before, after = check_build(character), check_build(changed)
    text = f"BP {before.spent}/{before.total} -> {after.spent}/{after.total}"
    if changed.passives != character.passives:
        text += f", PP {passive_points_spent(character)} -> {passive_points_spent(changed)}/{changed.pp_total}"
    return text


def known_tags() -> set[str]:
    """Every group tag any saved character has, normalized."""
    from game.character import list_all_characters
    return {norm_tag(t) for c in list_all_characters() for t in c.tags}


def describe_request(req: Request) -> str:
    """One short line saying what the request asks for."""
    p = req.params
    if req.kind == "stats":
        bits = []
        for key, label in (("hp", "HP"), ("power", "Power"), ("offense_level", "Offense Level"),
                           ("defense_level", "Defense Level")):
            if p.get(key) is not None:
                bits.append(f"{label} {p[key]}")
        if p.get("speed_min") is not None:
            bits.append(f"Speed {p['speed_min']}-{p['speed_max']}")
        if p.get("stagger_disabled_tiers") is not None:
            bits.append("remove Stagger tiers " + (", ".join(str(t) for t in sorted(p["stagger_disabled_tiers"])) or "none"))
        if p.get("resistances"):
            bits.append(", ".join(f"{t.capitalize()} {v}x" for t, v in p["resistances"].items()))
        return "; ".join(bits)
    if req.kind == "rank":
        return f"{p['status'].capitalize()} rank {p['rank']}" if p["rank"] else f"drop {p['status'].capitalize()} rank"
    if req.kind == "archetype":
        return ("take " if p.get("own", True) else "drop ") + defense_label(p["archetype"])
    if req.kind == "tags":
        return "tags: " + (", ".join(p.get("tags", [])) or "none")
    if req.kind == "passive":
        text = f"{p['name']} ({p['kind']}, asks {p['cost']} PP)"
        return text + "\n" + "\n".join(p["lines"])
    return req.kind
