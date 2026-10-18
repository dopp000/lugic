"""
Round recap: compares each fighter before and after a Combat Phase and
writes a short plain summary, so a busy round is easy to read at a glance.
No Discord code. See docs/ENGINEERING_NOTES.md#round-recap-and-log.
"""

from dataclasses import dataclass, field

from game.emojis import status_label, status_emoji

MAX_HISTORY = 5


@dataclass
class FighterSnap:
    hp: int
    max_hp: int
    sanity: int
    stagger: int
    shield: int
    alive: bool
    parts: dict[int, tuple[str, int, bool]]      # slot -> (name, hp, broken)
    statuses: dict[str, tuple[int, int]]         # key -> (potency, count)


@dataclass
class RoundRecord:
    """What /battle log can show again later for one finished round."""
    round_number: int
    recap: str
    entries: list[tuple[str, str]] = field(default_factory=list)


def take_snapshot(battle) -> dict[str, FighterSnap]:
    snap = {}
    for f in battle.fighters:
        snap[f.name] = FighterSnap(
            hp=f.hp, max_hp=f.max_hp, sanity=f.sanity, stagger=f.current_stagger_tier,
            shield=f.shield, alive=f.is_alive(),
            parts={n: (p.name, p.hp, p.broken) for n, p in f.parts.items()},
            statuses={k: (s.potency, s.count) for k, s in f.statuses.items()},
        )
    return snap


def _status_name(fighter, key: str) -> str:
    cs = fighter.custom_statuses.get(key)
    return cs.name if cs else status_label(key)


def build_recap(before: dict[str, FighterSnap], battle, round_number: int, labels: list[str]) -> str:
    """One line per fighter whose numbers changed, plus a one-line list of what was fought."""
    lines = [f"**Round {round_number} Recap**"]
    seen = []
    for label in labels:
        if label not in seen:
            seen.append(label)
    if seen:
        shown = seen[:8]
        extra = f" (+{len(seen) - len(shown)} more)" if len(seen) > len(shown) else ""
        lines.append("Fights: " + "; ".join(shown) + extra)

    for f in battle.fighters:
        old = before.get(f.name)
        if old is None:
            continue
        bits = []
        if f.hp != old.hp:
            bits.append(f"HP {old.hp} -> {f.hp} ({f.hp - old.hp:+d})")
        if f.sanity != old.sanity:
            bits.append(f"Sanity {old.sanity} -> {f.sanity} ({f.sanity - old.sanity:+d})")
        if f.current_stagger_tier > old.stagger:
            bits.append(f"Staggered (Tier {f.current_stagger_tier})")
        if old.shield and not f.shield:
            bits.append("Shield gone")
        for n, p in f.parts.items():
            prev = old.parts.get(n)
            if prev is None:
                continue
            if p.broken and not prev[2]:
                bits.append(f"{p.name} broke")
            elif p.hp != prev[1]:
                bits.append(f"{p.name} {prev[1]} -> {p.hp}")
        for key, st in f.statuses.items():
            prev = old.statuses.get(key)
            now = (st.potency, st.count)
            if prev != now:
                bits.append(f"{status_emoji(key)} {_status_name(f, key)} {now[0]}/{now[1]}")
        for key in old.statuses:
            if key not in f.statuses:
                bits.append(f"lost {status_emoji(key)} {_status_name(f, key)}")
        if old.alive and not f.is_alive():
            bits.append("down")
        if bits:
            lines.append(f"**{f.name}**: " + ", ".join(bits))

    if len(lines) == 1:
        lines.append("Nothing changed.")
    return "\n".join(lines)
