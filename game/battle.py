import random
from dataclasses import dataclass, field
from game.skills import Skill
from game.statuses import StatusInstance
from game.resistances import DEFAULT_RESISTANCES

SANITY_MIN = -45
SANITY_MAX = 45

# Clash win is +2 SP PER COIN in the winner's winning skill (variable, not flat -- see SANITY_PER_COIN_CLASH_WIN below, applied against... See docs/ENGINEERING_NOTES.md#battle-comment-10.
SANITY_PER_COIN_CLASH_WIN = 2
SANITY_CLASH_LOSS = 3
SANITY_PER_HEADS_UNOPPOSED = 2
SANITY_DRIFT_POSITIVE = 4
SANITY_DRIFT_NEGATIVE = 2

# Default Stagger thresholds as HP% (Tier 1 = mildest/first crossed as HP drops, Tier 3 = harshest/last crossed), and the incoming-damage... See docs/ENGINEERING_NOTES.md#battle-comment-20.
DEFAULT_STAGGER_THRESHOLDS = [0.55, 0.40, 0.25]
STAGGER_MULTIPLIERS = [1.5, 2.0, 2.5]

BATTLE_TYPES = {
    "spar": {"title": "Spar", "color": 0x57F287},
    "standard": {"title": "Proelium Commune", "color": 0xFEE75C},
    "fatal": {
        "title": "Proelium Fatale",
        "color": 0xED4245,
        "image": "https://cdn.discordapp.com/attachments/670849939294912545/1542197225252462702/ProeliumFatale.gif",
    },
}


@dataclass
class DeclaredAction:
    """One skill+target pairing occupying one of a fighter's skill slots, explicitly aimed at one specific slot on the target. See docs/ENGINEERING_NOTES.md#battle-declaredaction for the full rationale."""
    skill: Skill
    target: "Fighter"
    slot: int
    target_slot: int

    # Extra (fighter, slot) pairs this same [Attack Weight] action ALSO
    # splashes onto, picked automatically by declare() in fastest-Speed
    # order across every living enemy slot (any fighter, not just the
    # primary target). Only ever populated when skill.attack_weight > 1,
    # and ONLY ever matters on the UNOPPOSED/solo resolution path -- a
    # Clash is completely untouched by Attack Weight. See combat()'s
    # solo branch in cogs/battle.py for how the splash damage itself
    # gets applied (a flat, per-target resistance-adjusted echo of the
    # already-computed primary hit -- it does NOT re-toss coins, re-
    # consume the caster's Poise, or re-fire per-coin Triggers, which
    # only ever happen once, against the primary target).
    extra_targets: list[tuple["Fighter", int]] = field(default_factory=list)


@dataclass
class Fighter:
    name: str
    side: str
    hp: int = 100
    max_hp: int = 100
    sanity: int = 0
    speed: int = 10  # legacy flat speed, kept as the default for speed_min/max below
    power: int = 6
    skill_slots: int = 3
    avatar_url: str | None = None
    resistances: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RESISTANCES))

    # Discord user ID of whoever controls this fighter. See docs/ENGINEERING_NOTES.md#battle-comment-81.
    owner_id: int | None = None

    # The range each of this fighter's skill slots rolls its own Speed from, once per round (e.g. See docs/ENGINEERING_NOTES.md#battle-comment-89.
    speed_min: int | None = None
    speed_max: int | None = None

    # One rolled speed per skill slot, independent of whatever skill (if any) ends up assigned to that slot. See docs/ENGINEERING_NOTES.md#battle-comment-98.
    slot_speeds: list[int] = field(default_factory=list)

    skills: dict[str, Skill] = field(default_factory=dict)

    # Keyed by the CASTER's own slot number (1-based), not a plain list,
    # so a specific slot can be individually re-declared (moved) or
    # cancelled (undeclare) without disturbing the others.
    declared_actions: dict[int, DeclaredAction] = field(default_factory=dict)

    statuses: dict[str, StatusInstance] = field(default_factory=dict)

    # Round-scoped single-use flags for the two Counter-family Skill flags ([Counter] and [Clashable Counter], see SKILL_FLAG_TAGS in... See docs/ENGINEERING_NOTES.md#battle-comment-113.
    counter_used_this_round: bool = False
    clashable_counter_used_this_round: bool = False
    clashable_guard_used_this_round: bool = False

    # Stagger. See docs/ENGINEERING_NOTES.md#battle-comment-122.
    stagger_thresholds: list[float] = field(default_factory=lambda: list(DEFAULT_STAGGER_THRESHOLDS))
    stagger_tiers_enabled: list[bool] = field(default_factory=lambda: [True, True, True])
    current_stagger_tier: int = 0
    stagger_clears_end_of_round: int | None = None

    # Shield HP from a [Guard] skill: an overhead pool consumed BEFORE regular HP, not a resistance or a damage-avoidance mechanic -- incoming... See docs/ENGINEERING_NOTES.md#battle-comment-137.
    shield: int = 0

    def __post_init__(self):
        if self.speed_min is None:
            self.speed_min = self.speed
        if self.speed_max is None:
            self.speed_max = self.speed
        if not self.slot_speeds:
            self.roll_slot_speeds()

    @classmethod
    def from_character(cls, character, side: str) -> "Fighter":
        fighter = cls(
            name=character.name,
            side=side,
            hp=character.hp,
            max_hp=character.max_hp,
            speed=character.speed,
            power=character.power,
            avatar_url=character.avatar_url,
            resistances=dict(character.resistances),
            owner_id=character.owner_id,
        )
        # If the saved character has its own speed range (set via /character edit), it takes over from the flat speed default that __post_init__... See docs/ENGINEERING_NOTES.md#battle-comment-166.
        if character.speed_min is not None:
            fighter.speed_min = character.speed_min
            fighter.speed_max = (
                character.speed_max if character.speed_max is not None else character.speed_min
            )
            fighter.roll_slot_speeds()
        if character.stagger_thresholds is not None:
            fighter.stagger_thresholds = list(character.stagger_thresholds)
        if character.stagger_tiers_enabled is not None:
            fighter.stagger_tiers_enabled = list(character.stagger_tiers_enabled)
        return fighter

    def roll_slot_speeds(self):
        """Rerolls every skill slot's own Speed within [speed_min, speed_max]. See docs/ENGINEERING_NOTES.md#battle-fighter-roll-slot-speeds for the full rationale."""
        low, high = self.speed_min, self.speed_max
        if high < low:
            low, high = high, low
        self.slot_speeds = [
            random.randint(low, high) if high > low else low
            for _ in range(self.skill_slots)
        ]

    def slot_speed(self, slot: int) -> int:
        """1-based slot lookup. See docs/ENGINEERING_NOTES.md#battle-fighter-slot-speed for the full rationale."""
        if 1 <= slot <= len(self.slot_speeds):
            return self.slot_speeds[slot - 1]
        return 0

    def get_status(self, name: str) -> StatusInstance | None:
        return self.statuses.get(name)

    def set_status_instance(self, instance: StatusInstance):
        if instance.count <= 0:
            self.statuses.pop(instance.name, None)
        else:
            self.statuses[instance.name] = instance

    def is_alive(self) -> bool:
        return self.hp > 0

    def take_damage(self, amount: int) -> tuple[int, int]:
        """Consumes Shield first (1:1, no reduction of its own -- Shield isn't a resistance, it's a literal overhead HP pool), then spills... See docs/ENGINEERING_NOTES.md#battle-fighter-take-damage for the full rationale."""
        shield_absorbed = min(self.shield, amount)
        self.shield -= shield_absorbed
        remaining = amount - shield_absorbed
        self.hp = max(0, self.hp - remaining)
        return shield_absorbed, remaining

    def check_stagger(self, current_round: int) -> int:
        """Checks current HP% against every ENABLED Stagger threshold and updates current_stagger_tier / stagger_clears_end_of_round if the deepest... See docs/ENGINEERING_NOTES.md#battle-fighter-check-stagger for the full rationale."""
        if self.max_hp <= 0:
            return self.current_stagger_tier

        hp_pct = self.hp / self.max_hp
        deepest = 0
        for i, threshold in enumerate(self.stagger_thresholds):
            if not self.stagger_tiers_enabled[i]:
                continue
            if hp_pct <= threshold:
                deepest = i + 1

        if deepest > 0 and deepest >= self.current_stagger_tier:
            self.current_stagger_tier = deepest
            # Tier 1 clears by the end of THIS round; Tier 2/3 persist
            # through one full extra round, clearing at the end of the
            # NEXT round instead.
            self.stagger_clears_end_of_round = (
                current_round if deepest == 1 else current_round + 1
            )

        return self.current_stagger_tier

    def clear_expired_stagger(self, ending_round: int):
        """Called once, at the end of a round's combat() resolution (right before battle.round_number increments), for every living fighter. See docs/ENGINEERING_NOTES.md#battle-fighter-clear-expired-stagger for the full rationale."""
        if (
            self.current_stagger_tier > 0
            and self.stagger_clears_end_of_round is not None
            and self.stagger_clears_end_of_round <= ending_round
        ):
            self.current_stagger_tier = 0
            self.stagger_clears_end_of_round = None

    def gain_sanity(self, amount: int):
        self.sanity = min(SANITY_MAX, self.sanity + amount)

    def lose_sanity(self, amount: int):
        self.sanity = max(SANITY_MIN, self.sanity - amount)

    def heads_chance(self) -> int:
        return 50 + self.sanity

    def apply_sanity_drift(self):
        if self.sanity > 0:
            self.sanity = max(0, self.sanity - SANITY_DRIFT_POSITIVE)
        elif self.sanity < 0:
            self.sanity = min(0, self.sanity + SANITY_DRIFT_NEGATIVE)

    def add_skill(self, skill: Skill):
        self.skills[skill.name.lower()] = skill

    def get_skill(self, name: str) -> Skill | None:
        return self.skills.get(name.lower())

    def remove_skill(self, name: str) -> bool:
        return self.skills.pop(name.lower(), None) is not None

    def declare_in_slot(
        self, slot: int, skill: Skill, target: "Fighter", target_slot: int,
        extra_targets: list[tuple["Fighter", int]] | None = None,
    ) -> bool:
        """Fills (or overwrites/moves) one specific skill slot. See docs/ENGINEERING_NOTES.md#battle-fighter-declare-in-slot for the full rationale."""
        if not (1 <= slot <= self.skill_slots):
            return False
        self.declared_actions[slot] = DeclaredAction(
            skill=skill, target=target, slot=slot, target_slot=target_slot,
            extra_targets=list(extra_targets) if extra_targets else [],
        )
        return True

    def undeclare(self, slot: int) -> bool:
        """Clears one slot's declaration. Returns False if that slot
        wasn't declared in the first place.
        """
        return self.declared_actions.pop(slot, None) is not None

    def get_declared_skill_in_slot(self, slot: int) -> Skill | None:
        action = self.declared_actions.get(slot)
        return action.skill if action else None

    def has_declared(self) -> bool:
        return len(self.declared_actions) > 0

    def slots_filled(self) -> int:
        return len(self.declared_actions)

    def clear_declaration(self):
        self.declared_actions = {}
        self.counter_used_this_round = False
        self.clashable_counter_used_this_round = False
        self.clashable_guard_used_this_round = False
        self.shield = 0

    def __str__(self):
        return f"{self.name} (HP {self.hp}/{self.max_hp}, Sanity {self.sanity}, Speed {self.speed})"


@dataclass
class Battle:
    """One active fight, scoped to a single Discord channel."""

    channel_id: int
    fighters: list[Fighter] = field(default_factory=list)
    round_number: int = 1
    started: bool = False
    battle_type: str = "spar"
    message_id: int | None = None

    def add_fighter(self, fighter: Fighter):
        self.fighters.append(fighter)

    def get_fighter(self, name: str) -> Fighter | None:
        for f in self.fighters:
            if f.name.lower() == name.lower():
                return f
        return None

    def side(self, side: str) -> list[Fighter]:
        return [f for f in self.fighters if f.side == side]

    def all_declared(self) -> bool:
        """True once every living fighter has used at least one skill slot."""
        return all(
            f.has_declared()
            for f in self.fighters
            if f.is_alive()
        )

    def find_mutual_clash_partner(self, target: Fighter, target_slot: int) -> tuple[Fighter, int] | None:
        """Checks whether target's target_slot is currently locked in a real mutual clash with some other fighter: that fighter's own declared... See docs/ENGINEERING_NOTES.md#battle-battle-find-mutual-clash-partner for the full rationale."""
        target_action = target.declared_actions.get(target_slot)
        if target_action is None:
            return None
        partner = target_action.target
        partner_slot = target_action.target_slot
        partner_action = partner.declared_actions.get(partner_slot)
        if partner_action is None:
            return None
        if partner_action.target is target and partner_action.target_slot == target_slot:
            return partner, partner_slot
        return None

    def start_new_round(self):
        self.round_number += 1
        for f in self.fighters:
            f.clear_declaration()
            f.apply_sanity_drift()
            f.roll_slot_speeds()

    def summary(self) -> str:
        lines = [f"**Round {self.round_number}**"]
        for side_name in ("A", "B"):
            lines.append(f"\n__Side {side_name}__")
            for f in self.side(side_name):
                status = "Down" if not f.is_alive() else str(f)
                lines.append(f"- {status}")
        return "\n".join(lines)