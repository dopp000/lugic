"""Ready-made enemy tiers for admins. Pure data, no Discord code.

An admin adds one of these to a battle with /battle addnpc. It becomes a normal
fighter for that battle only: it is not saved as a character and skips every
Build Point check. Tune the numbers here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NpcTier:
    key: str
    label: str
    hp: int
    speed_min: int
    speed_max: int
    power: int
    level: int
    skill_slots: int
    # Which Stagger tiers exist (Tier 1 always does) and the HP fractions they start at.
    stagger_tiers_enabled: tuple[bool, bool, bool]
    stagger_thresholds: tuple[float, float, float]


NPC_TIERS: dict[str, NpcTier] = {
    "fodder": NpcTier("fodder", "Fodder", 60, 1, 3, 6, 1, 2, (True, False, False), (0.55, 0.40, 0.25)),
    "elite": NpcTier("elite", "Elite", 150, 3, 5, 8, 3, 3, (True, True, True), (0.55, 0.40, 0.25)),
    "boss": NpcTier("boss", "Boss", 400, 4, 7, 10, 5, 4, (True, True, True), (0.45, 0.30, 0.15)),
}


def describe(tier: NpcTier) -> str:
    stagger = sum(tier.stagger_tiers_enabled)
    return (
        f"{tier.label}: {tier.hp} HP, Speed {tier.speed_min}-{tier.speed_max}, Power {tier.power}, "
        f"Level {tier.level}, {tier.skill_slots} skill slots, {stagger} Stagger tier(s)"
    )
