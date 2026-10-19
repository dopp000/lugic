import copy
import random
from dataclasses import dataclass, field
from game.skills import Skill
from game.statuses import StatusInstance
from game.resistances import DEFAULT_RESISTANCES
from game.custom_statuses import CustomStatus
from game.emojis import register_custom_status_emoji
from game.supermoves import supermove_profile

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

    # Supermove bookkeeping: Sanity paid when this slot was declared (refunded
    # if it is cleared or replaced) and the id of the aftermath drain it queued.
    supermove_cost: int = 0
    drain_token: int = 0


@dataclass
class SanityDrain:
    """Sanity lost at each turn end for a few turns, queued by a Supermove."""
    token: int
    amount: int
    turns_left: int
    source: str


_drain_tokens = [0]


@dataclass
class Part:
    """One body part of a multi-part enemy, tied to one of its skill slots.
    Hits aimed at that slot use the part's resistances (if it has its own)
    and also drain the part's HP. The shared Core HP is the Fighter's own hp.
    See docs/ENGINEERING_NOTES.md#parts-and-core.
    """
    name: str
    hp: int
    max_hp: int
    resistances: dict[str, float] | None = None  # None = use the fighter's own
    broken: bool = False


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
    # Level and damage-percent steps, see game/build_points.py. Level only matters
    # in a clash: the higher side adds the gap to its Power every round.
    level: int = 1
    offense_level: int = 0
    defense_level: int = 0
    # Status ranks copied from the Character, see game/build_points.py.
    status_ranks: dict = field(default_factory=dict)
    defense_archetypes: list = field(default_factory=list)
    # Hidden skills built from the Character's passives (game/passives.py). Never in
    # `skills`, so they cannot be declared; the engine sweeps them with trigger_skills().
    passive_skills: list = field(default_factory=list)
    # Group tags copied from the Character (see Character.tags). Lowercase, for conditions.
    tags: list = field(default_factory=list)
    avatar_url: str | None = None
    # Decimal damage multipliers, 1.0 = Normal (see game/resistances.py).
    resistances: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RESISTANCES))

    # Discord user ID of whoever controls this fighter. See docs/ENGINEERING_NOTES.md#battle-comment-81.
    owner_id: int | None = None

    # The range each of this fighter's skill slots rolls its own Speed from, once per round (e.g. See docs/ENGINEERING_NOTES.md#battle-comment-89.
    speed_min: int | None = None
    speed_max: int | None = None

    # One rolled speed per skill slot, independent of whatever skill (if any) ends up assigned to that slot. See docs/ENGINEERING_NOTES.md#battle-comment-98.
    slot_speeds: list[int] = field(default_factory=list)

    skills: dict[str, Skill] = field(default_factory=dict)

    # Supermoves (E.G.O): times each has been declared this battle, and the
    # aftermath Sanity drains still running. See game/supermoves.py.
    supermove_uses: dict[str, int] = field(default_factory=dict)
    sanity_drains: list[SanityDrain] = field(default_factory=list)

    # Body parts of a multi-part enemy, keyed by skill slot number (1-based).
    parts: dict[int, Part] = field(default_factory=dict)

    # This fighter's own player-made statuses (copied from the Character).
    custom_statuses: dict[str, CustomStatus] = field(default_factory=dict)

    # Total Count this fighter has spent on each resource this battle
    # (status key -> total), drives a custom status's potency_every.
    consumed_totals: dict[str, int] = field(default_factory=dict)

    # Keyed by the CASTER's own slot number (1-based), not a plain list,
    # so a specific slot can be individually re-declared (moved) or
    # cancelled (undeclare) without disturbing the others.
    declared_actions: dict[int, DeclaredAction] = field(default_factory=dict)

    statuses: dict[str, StatusInstance] = field(default_factory=dict)

    # Round-scoped single-use flags for the two Counter-family Skill flags ([Counter] and [Clashable Counter], see SKILL_FLAG_TAGS in... See docs/ENGINEERING_NOTES.md#battle-comment-113.
    counter_used_this_round: bool = False
    clashable_counter_used_this_round: bool = False
    clashable_guard_used_this_round: bool = False

    # [Evade] is target-slot-specific (unlike Counter), so a flat
    # per-fighter bool isn't enough -- this tracks which of THIS
    # fighter's OWN slot numbers holding a declared [Evade] have
    # already resolved (won or lost) this round, so a second attack
    # landing on that same Evade's target slot correctly finds it
    # used-up rather than firing twice. Keyed by the Evade's OWN slot
    # number, not the attacker's slot. See find_eligible_evade in
    # cogs/battle.py.
    evade_used_slots: set[int] = field(default_factory=set)
    # id(Trigger) -> times it fired this round, for "(2 times per turn)" lines.
    trigger_uses: dict[int, int] = field(default_factory=dict)

    # Stagger. See docs/ENGINEERING_NOTES.md#battle-comment-122.
    stagger_thresholds: list[float] = field(default_factory=lambda: list(DEFAULT_STAGGER_THRESHOLDS))
    stagger_tiers_enabled: list[bool] = field(default_factory=lambda: [True, True, True])
    current_stagger_tier: int = 0
    stagger_clears_end_of_round: int | None = None

    # Shield HP from a [Guard] skill: an overhead pool consumed BEFORE regular HP, not a resistance or a damage-avoidance mechanic -- incoming... See docs/ENGINEERING_NOTES.md#battle-comment-137.
    shield: int = 0

    # Offense/Defense Level steps from trigger lines. They stack on the character's own level
    # and are wiped at turn end.
    temp_offense: int = 0
    temp_defense: int = 0
    # Survival rules from trigger lines. hp_floor and stagger_immune last one turn,
    # nullify_lethal lasts until it saves the unit. encounter_uses counts "(once per
    # Encounter)" lines for the whole battle (a Fighter lives for one battle only).
    hp_floor: int = 0
    stagger_immune: bool = False
    nullify_lethal: int = 0
    encounter_uses: dict = field(default_factory=dict)
    survival_notes: list = field(default_factory=list)
    death_reacted: bool = False  # Self On Death already fired for this fighter
    # "Use X as a follow-up attack" requests waiting for the combat loop: (order, skill name, target).
    followup_queue: list = field(default_factory=list)
    # "... next turn" effects waiting for the next round: (Trigger, target, ally) tuples.
    pending_effects: list = field(default_factory=list)

    # True once this fighter has hit 0 HP and been formally removed from
    # their side for the rest of THIS battle -- a one-way transition, not
    # just "currently at 0 HP". is_alive() only reflects the current HP
    # number and would flip back True the instant HP is set positive
    # again (e.g. an admin /battle setstatus heal); this flag is what
    # actually keeps an eliminated fighter out of the battle for good.
    # Set by combat()'s end-of-round elimination sweep, and checked by
    # setstatus (blocks HP changes on an eliminated fighter) and by
    # build_battle_embed (moves them into a separate Eliminated section,
    # out of their side's normal roster). Never cleared automatically --
    # only /battle end (starting a new Battle) resets it.
    eliminated: bool = False

    # Set when combat() strips an Immobilized fighter's declarations at
    # the start of a round. Turn end then consumes the Immobilized
    # status. An Immobilized that lands MID-combat has not blocked
    # anything yet, so it carries into next round. See
    # docs/ENGINEERING_NOTES.md#statuses-one-turn-debuffs.
    immobilized_blocked: bool = False

    # True from the turn end this fighter's Sanity hit Panic until the end
    # of the turn Panic blocked them, when Sanity resets to 0.
    panicked: bool = False

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
        from game.build_points import character_level
        fighter.level = character_level(character)
        fighter.offense_level = getattr(character, "offense_level", 0)
        fighter.defense_level = getattr(character, "defense_level", 0)
        fighter.status_ranks = dict(getattr(character, "status_ranks", {}) or {})
        fighter.defense_archetypes = list(getattr(character, "defense_archetypes", []) or [])
        fighter.tags = [" ".join(t.lower().split()) for t in (getattr(character, "tags", []) or [])]
        from game.passives import build_passive_skill
        kinds = {k: cs.kind for k, cs in character.custom_statuses.items()}
        fighter.passive_skills = [
            s for s in (build_passive_skill(p, kinds) for p in getattr(character, "passives", []) or []) if s
        ]
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
        # Deep-copied, not aliased -- so this fighter's own skills (which
        # a battle-scoped /battle addskill or /battle removeskill can
        # still freely add to or strip, same as before) never mutate the
        # character's own saved copy, and two simultaneous battles using
        # the same character don't secretly share one skill dict.
        fighter.skills = copy.deepcopy(character.skills)
        fighter.custom_statuses = copy.deepcopy(character.custom_statuses)
        for cs in fighter.custom_statuses.values():
            register_custom_status_emoji(cs)
        return fighter

    def trigger_skills(self) -> list:
        """Every skill the engine sweeps for passive-style timings: the known skills plus the hidden passives."""
        return list(self.skills.values()) + list(self.passive_skills)

    def roll_slot_speeds(self):
        """Rerolls every skill slot's own Speed within [speed_min, speed_max]. See docs/ENGINEERING_NOTES.md#battle-fighter-roll-slot-speeds for the full rationale."""
        low, high = self.speed_min, self.speed_max
        if high < low:
            low, high = high, low
        self.slot_speeds = [
            random.randint(low, high) if high > low else low
            for _ in range(self.skill_slots)
        ]

    def shift_slot_speeds(self, amount: int):
        """Adds `amount` (negative for Bind) to every slot's rolled Speed, floored at 0. Rerolled next round anyway, so a Bind wears off by itself."""
        self.slot_speeds = [max(0, sp + amount) for sp in self.slot_speeds]

    def slot_speed(self, slot: int) -> int:
        """1-based slot lookup. See docs/ENGINEERING_NOTES.md#battle-fighter-slot-speed for the full rationale."""
        if 1 <= slot <= len(self.slot_speeds):
            return self.slot_speeds[slot - 1]
        return 0

    def resistances_for_slot(self, slot: int | None) -> dict[str, float]:
        """The resistances that apply to a hit aimed at `slot`: the Part's own if it has them, else the fighter's."""
        part = self.parts.get(slot) if slot is not None else None
        if part is not None and part.resistances is not None:
            return part.resistances
        return self.resistances

    def slot_disabled(self, slot: int) -> bool:
        """True if this skill slot belongs to a broken Part."""
        part = self.parts.get(slot)
        return part is not None and part.broken

    def hit_part(self, slot: int | None, amount: int) -> list[str]:
        """Drains the Part at `slot` (if any) by `amount`. A Part at 0 HP breaks and its slot can't be declared until repaired. Returns log lines."""
        part = self.parts.get(slot) if slot is not None else None
        if part is None or amount <= 0:
            return []
        part.hp = max(0, part.hp - amount)
        if part.hp == 0 and not part.broken:
            part.broken = True
            return [f"{self.name}'s **{part.name}** (Slot {slot}) is broken! That slot can't act until it is repaired."]
        return []

    def get_status(self, name: str) -> StatusInstance | None:
        return self.statuses.get(name)

    def set_status_instance(self, instance: StatusInstance):
        cs = self.custom_statuses.get(instance.name)
        keeps = cs is not None and cs.keep_potency and instance.potency > 0
        if instance.count <= 0 and not keeps:
            self.statuses.pop(instance.name, None)
        else:
            self.statuses[instance.name] = instance

    def is_alive(self) -> bool:
        return self.hp > 0

    def can_act(self) -> bool:
        """False for a downed, Staggered or Immobilized fighter. /battle declare already refuses all three, so combat must not wait for them to declare."""
        return (
            self.is_alive()
            and self.current_stagger_tier == 0
            and self.get_status("immobilized") is None
        )

    def take_damage(self, amount: int) -> tuple[int, int]:
        """Consumes Shield first (1:1, no reduction of its own -- Shield isn't a resistance, it's a literal overhead HP pool), then spills the rest onto HP. Two survival rules can stop that spill: a charge of Nullify Lethal Damage cancels a hit that would kill, and an HP floor keeps HP from dropping below it (never raising HP that is already lower). Returns (absorbed by Shield, HP actually lost). See docs/ENGINEERING_NOTES.md#battle-fighter-take-damage for the full rationale."""
        shield_absorbed = min(self.shield, amount)
        self.shield -= shield_absorbed
        remaining = amount - shield_absorbed
        new_hp = max(0, self.hp - remaining)
        if new_hp <= 0 < self.hp and self.nullify_lethal > 0:
            self.nullify_lethal -= 1
            new_hp = self.hp
            self.survival_notes.append(f"{self.name}'s lethal damage was nullified!")
        floor = min(self.hp_floor, self.hp)
        if new_hp < floor:
            new_hp = floor
            self.survival_notes.append(f"{self.name}'s HP cannot drop below {floor} this turn.")
        lost = self.hp - new_hp
        self.hp = new_hp
        return shield_absorbed, lost

    def drain_survival_notes(self) -> list[str]:
        """Log lines for survival rules that just saved this fighter (cleared once read)."""
        notes, self.survival_notes = self.survival_notes, []
        return notes

    def check_stagger(self, current_round: int) -> int:
        """Checks current HP% against every ENABLED Stagger threshold and updates current_stagger_tier / stagger_clears_end_of_round if the deepest... See docs/ENGINEERING_NOTES.md#battle-fighter-check-stagger for the full rationale."""
        if self.max_hp <= 0:
            return self.current_stagger_tier

        if self.stagger_immune:
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

    def _refund_supermove(self, slot: int):
        """Gives back what a Supermove in `slot` cost and cancels its queued drain."""
        action = self.declared_actions.get(slot)
        if action is None or not action.supermove_cost:
            return
        self.sanity = min(SANITY_MAX, self.sanity + action.supermove_cost)
        name = action.skill.name
        self.supermove_uses[name] = max(0, self.supermove_uses.get(name, 0) - 1)
        self.sanity_drains = [d for d in self.sanity_drains if d.token != action.drain_token]

    def refund_all_supermoves(self):
        for slot in list(self.declared_actions):
            self._refund_supermove(slot)

    def supermove_check(self, slot: int, skill: Skill):
        """(profile, affordable) for declaring `skill` in `slot`. Counts a refund of whatever Supermove already sits in that slot. profile is None for a normal skill."""
        if "supermove" not in skill.tags:
            return None, True
        uses = self.supermove_uses.get(skill.name, 0)
        sanity = self.sanity
        existing = self.declared_actions.get(slot)
        if existing is not None and existing.supermove_cost:
            sanity += existing.supermove_cost
            if existing.skill.name == skill.name:
                uses = max(0, uses - 1)
        profile = supermove_profile(skill, uses)
        return profile, sanity >= profile.cost

    def declare_in_slot(
        self, slot: int, skill: Skill, target: "Fighter", target_slot: int,
        extra_targets: list[tuple["Fighter", int]] | None = None,
    ) -> bool:
        """Fills (or overwrites/moves) one specific skill slot. See docs/ENGINEERING_NOTES.md#battle-fighter-declare-in-slot for the full rationale. A [Supermove] pays its Sanity cost here and queues its aftermath drain (docs: #supermoves)."""
        if not (1 <= slot <= self.skill_slots):
            return False
        if not self.supermove_check(slot, skill)[1]:
            return False
        self._refund_supermove(slot)
        paid, token = 0, 0
        if "supermove" in skill.tags:
            profile = supermove_profile(skill, self.supermove_uses.get(skill.name, 0))
            paid = profile.cost
            self.sanity = max(SANITY_MIN, self.sanity - paid)
            self.supermove_uses[skill.name] = self.supermove_uses.get(skill.name, 0) + 1
            if profile.drain:
                _drain_tokens[0] += 1
                token = _drain_tokens[0]
                self.sanity_drains.append(
                    SanityDrain(token=token, amount=profile.drain, turns_left=profile.drain_turns, source=skill.name)
                )
        self.declared_actions[slot] = DeclaredAction(
            skill=skill, target=target, slot=slot, target_slot=target_slot,
            extra_targets=list(extra_targets) if extra_targets else [],
            supermove_cost=paid, drain_token=token,
        )
        return True

    def undeclare(self, slot: int) -> bool:
        """Clears one slot's declaration (refunding a Supermove's cost). Returns False if that slot wasn't declared in the first place."""
        self._refund_supermove(slot)
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
        self.evade_used_slots = set()
        self.trigger_uses = {}
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

    # The last few finished rounds (recap + full log), newest last, for /battle log.
    history: list = field(default_factory=list)

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
            if f.can_act()
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