import dataclasses
import random
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from game.statuses import magnitude_to_hint_tier, morale_state

if TYPE_CHECKING:
    # Only for type hints below -- importing these for real would be a
    # circular import (game.battle -> game.skills -> game.conditions).
    from game.battle import Fighter, Battle

# ---------------------------------------------------------------------------
# Condition types
# ---------------------------------------------------------------------------

CONDITION_TYPES = [
    "always", "target_status", "target_hp_pct", "caster_sanity",
    "caster_status", "first_hit_of_round", "speed_faster", "caster_speed_at_least",
    "caster_morale", "target_morale", "target_is_ally", "target_is_enemy",
    "caster_hp_pct", "target_sanity", "speed_diff", "target_affinity",
    "target_negative_count", "target_defending",
]

# Skill tags that count as a Defense Skill for "If the target is using a Defense Skill".
DEFENSE_SKILL_TAGS = {"guard", "evade", "counter", "clashable_guard", "clashable_counter"}

# A resistance multiplier above 1.0 is Weak, this much or more is Fatal.
WEAK_ABOVE = 1.0
FATAL_AT = 1.5

EFFECT_TYPES = [
    "bonus_power", "bonus_coin_power", "bonus_damage_pct", "bonus_clash_power", "bonus_final_power",
    "inflict_status", "gain_status", "sanity_gain", "sanity_loss", "heal", "remove_status",
    "gain_shield", "level_mod",
]

# Effects that land after the toss, once a hit connects (everything but the three
# power effects below).
POST_HIT_EFFECTS = (
    "inflict_status", "sanity_gain", "sanity_loss", "gain_status", "heal", "remove_status",
    "gain_shield", "level_mod",
)

# Effects that reshape the hit itself. They only do something at the timings
# below, because the engine reads them before the coins are tossed (Coin Start
# also works for the two flat ones). Anywhere else they would be ignored, so the
# parser refuses them there instead of saving a line that does nothing.
POWER_EFFECTS = ("bonus_power", "bonus_coin_power", "bonus_damage_pct", "bonus_clash_power", "bonus_final_power")
# Only these two may also sit on a :CoinN: [Coin Start] line.
COIN_START_EFFECTS = ("bonus_power", "bonus_coin_power")
POWER_TIMINGS = ("before_use", "on_use", "clash_start", "before_attack")

# One "Deal +N% damage" line can add at most this much (assumption, tune freely).
MAX_DAMAGE_PCT = 50

COMPARISONS = {
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
}

# Skill-level timing tags: fire once per skill resolution, at that point
# in the resolution pipeline. Keys are the lowercase text inside the
# brackets, values are the internal timing name used everywhere else.
SKILL_LEVEL_TIMINGS = {
    "turn start": "turn_start",
    "combat start": "combat_start",
    "turn end": "turn_end",
    "before use": "before_use",
    "on use": "on_use",
    "clash start": "clash_start",
    "clash win": "clash_win",
    "clash lose": "clash_lose",
    "before attack": "before_attack",
    "on unopposed attack": "on_unopposed_attack",
    "attack end": "attack_end",
    # [On Evade] and [Before Getting Hit] both live here, not in
    # PER_COIN_TIMINGS, even though they're each about a single
    # incoming coin. Both are evaluated as a passive sweep across ALL
    # of the DEFENDER's own known skills (same pattern fire_passive_
    # triggers uses for [Combat Start]/[Turn Start]), since the
    # defender isn't the one whose skill is being resolved when they
    # get attacked, so there's no coin_index of theirs to attach either
    # to. See find_eligible_evade and apply_incoming_hit in
    # cogs/battle.py for the actual mechanics these fire off of.
    # caster on the context is the defender (the one
    # reacting), target is the attacker, so a condition like "if
    # attacker has Rupture" still reads naturally off target_status.
    "on evade": "on_evade",
    "before getting hit": "before_getting_hit",
    # Fires on the ATTACKER's own skill, right after this hit's damage
    # is applied, if the TARGET is now Stagger'd -- either freshly
    # triggered by this exact hit, or already Stagger'd from an earlier
    # hit and still qualifying. Evaluated with caster=attacker,
    # target=defender, same as every other skill-level timing (not a
    # defender-side passive sweep like on_evade/before_getting_hit
    # above, since this is genuinely about the ATTACKING skill's own
    # resolution). See Fighter.check_stagger in game/battle.py and
    # STAGGER_MULTIPLIERS in cogs/battle.py for the actual mechanic.
    "on stagger": "on_stagger",
    # Fires on the attacker's skill right after damage lands, if this
    # hit dropped the target's HP to 0 (on_kill), or if it did so AND
    # was a Crit coin (on_crit_kill). Both caster=attacker, target=the
    # fighter just defeated.
    "on kill": "on_kill",
    "on crit kill": "on_crit_kill",
    # Fires on the ATTACKER's skill, for the LOSER of a Clash
    # specifically -- symmetric to the existing per-coin
    # hit_after_clash_win, but for the side that lost. caster=loser,
    # target=winner (mirrors clash_lose's own caster/target convention).
    "hit after clash lose": "hit_after_clash_lose",
    # Fires on an attacker's skill against a target who currently holds
    # Tremor, applying Tremor's own built-in payoff (raise the target's
    # enabled Stagger thresholds by Tremor's Potency, decay Tremor's own
    # Count by 1) rather than a generic Trigger effect -- "raise stagger
    # threshold" isn't one of the existing effect types
    # (inflict_status/sanity_gain/bonus_power/gain_status), so this
    # timing carries its own built-in engine behavior, evaluated by the
    # skill author's own condition (e.g. "[Tremor Burst] If the target
    # has any Tremor,") same as any other timing.
    "tremor burst": "tremor_burst",
    # [Ally ...] reaction timings. They sit on the OWNER's skills (swept
    # like [Turn Start]) and fire when an ALLY of the owner (same side,
    # not the owner) does or suffers the named thing. caster is the
    # owner, ally on the context is the acting ally, target is the enemy
    # involved (if any). 'to the ally' on an effect puts it on that ally.
    # See docs/ENGINEERING_NOTES.md#ally-reactions.
    "ally on hit": "ally_on_hit",
    "ally on crit": "ally_on_crit",
    "ally on kill": "ally_on_kill",
    "ally is hit": "ally_is_hit",
    "ally on stagger": "ally_on_stagger",
    "ally turn start": "ally_turn_start",
    "ally combat start": "ally_combat_start",
    # Passive-only reactions of the owner itself (see game/passives.py): when this
    # fighter takes a landed hit, is newly Staggered, or defeats someone.
    "self is hit": "self_is_hit",
    "self on stagger": "self_on_stagger",
    "self on kill": "self_on_kill",
}

# Per-coin timing tags: need a :CoinN: prefix on the line, fire once per
# coin at that point in that specific coin's own resolution.
PER_COIN_TIMINGS = {
    "coin start": "coin_start",
    "on hit": "on_hit",
    "heads hit": "heads_hit",
    "tails hit": "tails_hit",
    "hit after clash win": "hit_after_clash_win",
    "current coin attack end": "current_coin_attack_end",
    "heads attack end": "heads_attack_end",
    "tails attack end": "tails_attack_end",
    # A coin Crits if the caster is currently holding Poise (see the
    # Poise-break rule documented on resolve_skill in game/skills.py) --
    # independent of that coin's own face, which is why [On Crit -
    # Heads Hit]/[On Crit - Tails Hit] exist as their own sub-timings
    # rather than being implied by [Heads Hit]/[Tails Hit].
    "on crit": "on_crit",
    "on crit - heads hit": "on_crit_heads_hit",
    "on crit - tails hit": "on_crit_tails_hit",
}

# Recognized by name, but nothing in the engine backs them yet. Parsed so
# the error can name the specific tag and the missing system, instead of
# a generic "unknown tag". Empty for now -- Counter and Evade (the last
# two items that lived here) are both built now; kept as a dict, not
# removed entirely, so a future unbuilt timing has somewhere to go.
UNSUPPORTED_TIMINGS = {}

ALL_TIMING_LOOKUP = {**SKILL_LEVEL_TIMINGS, **PER_COIN_TIMINGS}


# Bracket tags that are plain skill metadata (no condition, no effect,
# just a flag), stored on Skill.tags instead of becoming a Trigger.
# These live on their own line with no effect text after the tag.
SKILL_FLAG_TAGS = {
    "clashable counter": "clashable_counter",
    "counter": "counter",
    "target fixed": "target_fixed",
    "indiscriminate": "indiscriminate",
    "unclashable": "unclashable",
    "guard": "guard",
    "clashable guard": "clashable_guard",
    "evade": "evade",
    "duality": "duality",
    "support": "duality",  # old name, still accepted
    "supermove": "supermove",
}

# The only statuses a caster can hold on themselves as a self-buff
# resource right now. "Gain N <Status>" and "At N+ <Status>" (when the
# thing named isn't Speed) both check against this list, anything else
# is an unmodeled custom resource (Strider, Assist Defense, Deathrite,
# named Identity resources, etc) and gets rejected with a clear message,
# matching this pass's scope.
#
# NOTE: neither "counter" nor "evasion" live in this list -- both used
# to be a status/resource here, but are now Skill-level mechanics
# instead ([Counter]/[Evade] skill flags, see SKILL_FLAG_TAGS above,
# plus find_eligible_counter/find_eligible_evade and
# apply_counter_redirects in cogs/battle.py). A declared skill, not a
# stat a caster holds. "Gain N Evasion" is no longer valid trigger
# text for this reason -- it now falls through to the same "not a
# tracked resource" rejection any other unrecognized name would get.
SELF_BUFF_STATUSES = ["poise", "charge", "power_up", "haste", "damage_up", "protection"]

# Target-facing statuses, mirrors statuses.py's INFLICTABLE_STATUSES.
# Duplicated here rather than imported so this module doesn't need to
# import statuses.py just for one list.
TARGET_STATUSES = [
    "burn", "bleed", "tremor", "rupture", "sinking",
    "bind", "fragile", "slash_fragility", "pierce_fragility", "blunt_fragility",
    "power_down", "damage_down", "paralyze", "poison", "immobilized",
]


@dataclass
class Condition:
    """One check against battle state. type is one of CONDITION_TYPES.

    always -- no fields used, evaluates True unconditionally. Used for
    a trigger that is pure timing (e.g. "[Clash Win] Inflict +2 Rupture
    Count" has nothing to check beyond "did Clash Win happen").
    target_status / caster_status -- status_name/min_potency/min_count.
    "At 5+ Charge" and "If the target has 5+ Burn" check Count (min_count);
    adding the word Potency ("At 5+ Charge Potency") checks min_potency
    instead. Skills saved before this change keep their old min_potency.
    target_hp_pct / caster_sanity -- comparison/value. target_hp_pct has
    no recognized phrase in the parser yet (not in this pass's locked
    condition scope, only reachable by constructing a Condition
    directly), kept here so evaluate_condition and the dataclass shape
    stay ready for it once/if it gets a phrase added.
    speed_faster -- no fields, reads caster_slot_speed/target_slot_speed
    off the TriggerContext directly.
    caster_speed_at_least -- value only, checked against
    caster_slot_speed.
    caster_morale / target_morale -- status_name is "low_morale" (true at
    -30 Sanity or lower, or Panicked) or "panic" (true at -45 or while
    Panicked). See morale_state in game/statuses.py.
    first_hit_of_round -- no fields, reads is_first_hit_of_round.
    """
    type: str
    status_name: str | None = None
    min_potency: int = 0
    min_count: int = 0
    comparison: str = "gte"
    value: int = 0

    def to_dict(self) -> dict:
        """Plain dict of every field -- Condition is flat (no nested
        dataclasses, no sets), so this is just a 1:1 field dump, no
        special-casing needed. Used by Trigger.to_dict so a Skill's
        triggers can round-trip through a saved Character's JSON file.
        """
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Condition":
        return cls(**data)


@dataclass
class TriggerContext:
    """Everything a Condition might need.

    caster_slot_speed/target_slot_speed are the rolled Speed of the
    specific slots involved in this resolution, not a fighter-wide
    value, since each slot rolls its own Speed independently. The
    caller (combat() in cogs/battle.py) is responsible for passing the
    right slot's value in here, this module has no notion of slots.

    target can be None for triggers that never reference one
    (caster_sanity, caster_status, first_hit_of_round, most Combat/Turn
    Start/End triggers) -- any target-dependent condition just
    evaluates False rather than raising if target is missing.
    """
    caster: "Fighter"
    target: "Fighter | None"
    battle: "Battle"
    ally: "Fighter | None" = None
    is_first_hit_of_round: bool = False
    caster_slot_speed: int = 0
    target_slot_speed: int = 0


def evaluate_condition(condition: Condition, context: TriggerContext, skill=None) -> bool:
    """Pure evaluation, no side effects. Safe to call speculatively (e.g.
    for Hint display) against a target the caster hasn't actually
    declared on yet. `skill` is the skill being used, only the Weak/Fatal
    check needs it (to know the damage type); without it that check is False.
    """
    if condition.type == "always":
        return True

    if condition.type == "target_status":
        if context.target is None:
            return False
        status = context.target.get_status(condition.status_name)
        if status is None:
            return False
        return status.potency >= condition.min_potency and status.count >= condition.min_count

    if condition.type == "target_hp_pct":
        if context.target is None or context.target.max_hp <= 0:
            return False
        pct = (context.target.hp / context.target.max_hp) * 100
        return COMPARISONS[condition.comparison](pct, condition.value)

    if condition.type == "caster_sanity":
        return COMPARISONS[condition.comparison](context.caster.sanity, condition.value)

    if condition.type == "caster_status":
        status = context.caster.get_status(condition.status_name)
        if status is None:
            return False
        return status.potency >= condition.min_potency and status.count >= condition.min_count

    if condition.type in ("caster_morale", "target_morale"):
        who = context.caster if condition.type == "caster_morale" else context.target
        if who is None:
            return False
        state = morale_state(who)
        if condition.status_name == "panic":
            return state == "panic"
        return state in ("low_morale", "panic")

    if condition.type in ("target_is_ally", "target_is_enemy"):
        if context.target is None:
            return False
        same_side = context.target.side == context.caster.side
        return same_side if condition.type == "target_is_ally" else not same_side

    if condition.type == "first_hit_of_round":
        return context.is_first_hit_of_round

    if condition.type == "speed_faster":
        if context.target is None:
            return False
        return context.caster_slot_speed > context.target_slot_speed

    if condition.type == "caster_speed_at_least":
        return context.caster_slot_speed >= condition.value

    if condition.type == "caster_hp_pct":
        if context.caster.max_hp <= 0:
            return False
        pct = (context.caster.hp / context.caster.max_hp) * 100
        return COMPARISONS[condition.comparison](pct, condition.value)

    if condition.type == "target_sanity":
        if context.target is None:
            return False
        return COMPARISONS[condition.comparison](context.target.sanity, condition.value)

    if condition.type == "speed_diff":
        if context.target is None:
            return False
        return COMPARISONS[condition.comparison](
            context.caster_slot_speed - context.target_slot_speed, condition.value
        )

    if condition.type == "target_affinity":
        if context.target is None or skill is None:
            return False
        mult = context.target.resistances.get(skill.damage_type, 1.0)
        if condition.status_name == "fatal":
            return mult >= FATAL_AT
        return mult > WEAK_ABOVE

    if condition.type == "target_negative_count":
        if context.target is None:
            return False
        active = 0
        for name in TARGET_STATUSES:
            st = context.target.get_status(name)
            if st is not None and (st.count > 0 or st.potency > 0):
                active += 1
        return active >= condition.value

    if condition.type == "target_defending":
        if context.target is None:
            return False
        return any(
            DEFENSE_SKILL_TAGS & set(a.skill.tags)
            for a in context.target.declared_actions.values()
        )

    return False


@dataclass
class Trigger:
    """A Condition plus what happens if it's true, plus when to check it.

    timing is one of the internal names in ALL_TIMING_LOOKUP, it decides
    both WHEN this fires during resolution and, implicitly, whether it
    needs coin_index set (any PER_COIN_TIMINGS value) or not (any
    SKILL_LEVEL_TIMINGS value).

    coin_index is 1-based, matching the skill's own coin_statuses
    indexing, and is None for skill-level timings.

    hint_tier (1-3) can be hand-set by whoever built the skill, written
    as a trailing "(Hint N)" on the line -- that always wins when
    present. Left off, an inflict_status trigger gets it worked out
    automatically from its own potency*count (see parse_trigger_text
    below); any other effect type defaults to 1.

    raw_text is the original line, kept around for addskill's preview
    and for error messages, so a malformed line can be echoed back
    exactly instead of reconstructed from parsed pieces.
    """
    condition: Condition
    effect_type: str
    timing: str
    coin_index: int | None = None
    effect_value: int = 0
    status_name: str | None = None
    status_count: int = 0
    hint_tier: int = 1
    raw_text: str = ""
    # "Consume N X: <effect>" lines: the trigger only fires if the
    # caster can pay N Count of X, which is then spent. See pay_cost.
    consume_status: str | None = None
    consume_amount: int = 0
    # Scaled bonus_power / bonus_coin_power: effect_value for every
    # scale_per of the caster's scale_status Potency or Count
    # (scale_basis), e.g. "Coin Power +1 per 3 Pulsation Potency".
    scale_status: str | None = None
    scale_per: int = 0
    scale_basis: str = "potency"
    # "... per 3 Burn Count on the target": read the scale from the target, not the caster.
    scale_target: bool = False
    # "(max 3)" / "(max 15%)" on a scaled line: the scaled amount never goes above this. 0 = no cap.
    scale_max: int = 0
    # heal effect: effect_value is the amount, heal_percent says it is a
    # percentage of the caster's max HP instead of flat HP.
    heal_percent: bool = False
    # Effect lands on the ally this skill was aimed at (a [Support] skill)
    # instead of the caster. Only for heal, gain_status, sanity_gain and
    # remove_status.
    to_ally: bool = False
    # "(2 times per turn)" / "(once per turn)": 0 means no limit.
    max_per_turn: int = 0
    # "(30% chance)": rolled each time the line would fire, 0 = always.
    chance: int = 0
    # "... next turn": queued and applied when the next round opens.
    delayed: bool = False
    # level_mod only: lands on the target (Inflict) instead of the user (Gain).
    on_target: bool = False

    def to_dict(self) -> dict:
        """Used so a Skill carrying this Trigger can be saved to a
        Character's JSON file and read back unchanged. condition is the
        one nested dataclass field, so it gets its own to_dict call
        rather than a blind dataclasses.asdict (which would still work
        here since Condition has no sets either, but spelling it out
        keeps this symmetric with Skill.to_dict below, which does need
        the custom handling for its set field).
        """
        return {
            "condition": self.condition.to_dict(),
            "effect_type": self.effect_type,
            "timing": self.timing,
            "coin_index": self.coin_index,
            "effect_value": self.effect_value,
            "status_name": self.status_name,
            "status_count": self.status_count,
            "hint_tier": self.hint_tier,
            "raw_text": self.raw_text,
            "consume_status": self.consume_status,
            "consume_amount": self.consume_amount,
            "scale_status": self.scale_status,
            "scale_per": self.scale_per,
            "scale_basis": self.scale_basis,
            "scale_target": self.scale_target,
            "scale_max": self.scale_max,
            "heal_percent": self.heal_percent,
            "to_ally": self.to_ally,
            "max_per_turn": self.max_per_turn,
            "chance": self.chance,
            "delayed": self.delayed,
            "on_target": self.on_target,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Trigger":
        data = dict(data)
        data["condition"] = Condition.from_dict(data["condition"])
        return cls(**data)


def pay_cost(trigger: Trigger, context: "TriggerContext") -> bool:
    """For a Consume trigger whose condition already passed: spends its cost from the caster and returns True, or returns False (nothing spent) if the caster can't afford it. Triggers with no cost always return True. Only Count is spent, Potency is left as it is. This DOES change state, call it only at the moment a trigger really fires. See docs/ENGINEERING_NOTES.md#consume-triggers."""
    caster = context.caster
    if trigger.max_per_turn and caster.trigger_uses.get(id(trigger), 0) >= trigger.max_per_turn:
        return False
    if trigger.chance and random.randint(1, 100) > trigger.chance:
        return False
    if not _pay_consume(trigger, context):
        return False
    if trigger.max_per_turn:
        caster.trigger_uses[id(trigger)] = caster.trigger_uses.get(id(trigger), 0) + 1
    return True


def _pay_consume(trigger: "Trigger", context: "TriggerContext") -> bool:
    """The Consume half of pay_cost."""
    if trigger.consume_status is None:
        return True
    from game.statuses import StatusInstance
    caster = context.caster
    current = caster.get_status(trigger.consume_status)
    if current is None or current.count < trigger.consume_amount:
        return False
    new_potency = current.potency
    cs = caster.custom_statuses.get(trigger.consume_status)
    if cs is not None and cs.potency_every > 0:
        before = caster.consumed_totals.get(trigger.consume_status, 0)
        after = before + trigger.consume_amount
        caster.consumed_totals[trigger.consume_status] = after
        new_potency = min(99, new_potency + after // cs.potency_every - before // cs.potency_every)
    caster.set_status_instance(StatusInstance(
        name=current.name, potency=new_potency, count=current.count - trigger.consume_amount,
    ))
    return True


def effective_value(trigger: Trigger, context: "TriggerContext") -> int:
    """A trigger's bonus amount, scaled by the caster's (or, with 'on the target', the target's) current Potency or Count if the line says so ('+1 per 3 Pulsation Potency' gives 1 for every full 3), then held to the line's '(max N)' if it has one. Read right after any cost was paid, so a spend that raised Potency counts."""
    if trigger.scale_status is None or trigger.scale_per < 1:
        return trigger.effect_value
    who = context.target if trigger.scale_target else context.caster
    inst = who.get_status(trigger.scale_status) if who is not None else None
    if inst is None:
        return 0
    stat = inst.potency if trigger.scale_basis == "potency" else inst.count
    value = trigger.effect_value * (stat // trigger.scale_per)
    if trigger.scale_max:
        value = min(value, trigger.scale_max)
    return value


class TriggerParseError(ValueError):
    """Raised on a malformed trigger line. Carries the offending line so
    the caller can show exactly which one failed, instead of a generic
    error against the whole block.
    """

    def __init__(self, line: str, reason: str):
        self.line = line
        self.reason = reason
        super().__init__(f"'{line.strip()}': {reason}")


_HINT_RE = re.compile(r"\(hint:?\s*(\d)\)", re.IGNORECASE)
_TAG_RE = re.compile(r"^\s*(?::coin(\d+):\s*)?\[([^\]]+)\]\s*(.*)$", re.IGNORECASE)

# Strips exactly one trailing parenthetical "annotation" off an effect
# phrase before matching it against the flat-effect patterns below, so
# notes like "(once per turn)" or "(max 2)" don't block an otherwise
# well-formed line. It is NOT applied before the ';' chained-effect
# check further down in _parse_effect_text, so a real formula clause
# living inside that same parenthetical (e.g. "for every 2 Speed
# difference; max 2") still correctly fails to parse instead of being
# silently accepted as something else.
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")

_COND_TARGET_SIDE = re.compile(r"if (?:the )?target is an? (ally|enemy),?\s*(.*)", re.IGNORECASE)
_PER_TURN_RE = re.compile(r"\(\s*(?:(\d+) times?|once|twice) per turn\s*\)", re.IGNORECASE)
_COND_SPEED_FASTER = re.compile(r"if this unit'?s speed is faster than the target'?s,?\s*(.*)", re.IGNORECASE)
_COND_TARGET_STATUS_ANY = re.compile(r"if (?:the )?target has any ([a-z]+),?\s*(.*)", re.IGNORECASE)
_COND_TARGET_STATUS_N = re.compile(
    r"if (?:the )?target has (\d+)\+ ([a-z]+)(?: (potency|count))?,?\s*(.*)", re.IGNORECASE
)
_COND_CASTER_SANITY = re.compile(r"if this unit'?s sanity is (\d+)\+,?\s*(.*)", re.IGNORECASE)
_COND_MORALE = re.compile(
    r"if (this unit|the target) is (low morale|panic(?:ked)?),?\s*(.*)", re.IGNORECASE
)
_COND_HP = re.compile(
    r"if (this unit|the target)'?s? hp is (\d+)% or (more|less),?\s*(.*)", re.IGNORECASE
)
_COND_SANITY_ANY = re.compile(
    r"if (this unit|the target)'?s? sanity is (-?\d+)(\+| or more| or less),?\s*(.*)", re.IGNORECASE
)
_COND_SPEED_DIFF = re.compile(
    r"if this unit'?s speed is (\d+)\+ (higher|lower) than the target'?s,?\s*(.*)", re.IGNORECASE
)
_COND_AFFINITY = re.compile(
    r"if the target is (weak|fatal) to (?:this|the) skill'?s damage type,?\s*(.*)", re.IGNORECASE
)
_COND_NEGATIVE = re.compile(
    r"if the target has (\d+)\+ (?:different )?negative effects?,?\s*(.*)", re.IGNORECASE
)
_COND_DEFENDING = re.compile(
    r"if the target is using an? defen[cs]e skill,?\s*(.*)", re.IGNORECASE
)
_COND_AT_N_THING = re.compile(r"at (\d+)\+ ([a-z]+)(?: (potency|count))?,?\s*(.*)", re.IGNORECASE)
# Multi-word (custom) status names need the comma to know where the name stops.
_COND_AT_N_THING_COMMA = re.compile(r"at (\d+)\+? ([a-z]+(?: [a-z]+)*?),\s*(.*)", re.IGNORECASE)


def _threshold_condition(ctype: str, status: str, n: int, basis: str | None) -> "Condition":
    """'At 5+ Burn' checks Count by default; add 'Potency' to check Potency instead."""
    if basis and basis.lower() == "potency":
        return Condition(type=ctype, status_name=status, min_potency=n)
    return Condition(type=ctype, status_name=status, min_count=n)


def _split_basis(thing: str, known) -> tuple[str, str | None]:
    """'pulsation potency' -> ('pulsation', 'potency'), only when the rest is a known status."""
    for word in ("potency", "count"):
        if thing.endswith(" " + word):
            base = thing[: -len(word) - 1].strip()
            if "_".join(base.split()) in known:
                return base, word
    return thing, None

_EFFECT_BONUS_POWER_PCT = re.compile(r"deal \+(\d+)%\s*damage", re.IGNORECASE)
_EFFECT_BONUS_POWER = re.compile(r"deal \+(\d+)\s*damage", re.IGNORECASE)
_EFFECT_COIN_POWER = re.compile(r"coin power \+(\d+)", re.IGNORECASE)
_EFFECT_SANITY = re.compile(r"gain (\d+) sanity", re.IGNORECASE)
_EFFECT_SANITY_LOSS = re.compile(r"lose (\d+) sanity", re.IGNORECASE)
_EFFECT_CLASH_POWER = re.compile(r"clash power \+(\d+)", re.IGNORECASE)
_EFFECT_FINAL_POWER = re.compile(r"final power \+(\d+)", re.IGNORECASE)
_EFFECT_SHIELD = re.compile(r"gain (\d+)(%)?(?: max hp)? shield", re.IGNORECASE)
_EFFECT_LEVEL = re.compile(r"(gain|inflict) (offense|defense) level ([+-])(\d+)", re.IGNORECASE)
_NEXT_TURN_RE = re.compile(r"\s+next turn\s*$", re.IGNORECASE)
_CHANCE_NOTE_RE = re.compile(r"(\d+)\s*%\s*chance", re.IGNORECASE)
_EFFECT_INFLICT_EXPLICIT = re.compile(
    r"inflict ([a-z ]+?) potency:?\s*(\d+),?\s*count:?\s*(\d+)", re.IGNORECASE
)
_EFFECT_INFLICT_COUNT = re.compile(r"inflict \+(\d+) ([a-z ]+?) count", re.IGNORECASE)
_EFFECT_INFLICT_BARE = re.compile(r"inflict (\d+) ([a-z ]+)", re.IGNORECASE)
_EFFECT_GAIN_STATUS = re.compile(r"gain (\d+) ([a-z ]+)", re.IGNORECASE)
_TO_ALLY_RE = re.compile(r"\s+to (?:the |an |that )?ally\s*$", re.IGNORECASE)
_EFFECT_REMOVE = re.compile(r"remove ([a-z ]+)", re.IGNORECASE)
_EFFECT_HEAL = re.compile(r"heal (\d+)\s*(%)?(?: max)?\s*(?:hp)?", re.IGNORECASE)
_EFFECT_SCALED = re.compile(
    r"(deal|coin power) \+(\d+)(%)?(?: damage)? per (\d+) ([a-z ]+?) (potency|count)( on the target)?",
    re.IGNORECASE,
)

# Notes the bot understands at the end of a line, besides (Hint N) and (once per
# turn) which are peeled off earlier: "(max 3)" or "(max 15%)" on a scaled line.
_NOTE_RE = re.compile(r"\s*\(([^()]*)\)\s*$")
_MAX_NOTE_RE = re.compile(r"max\s+(\d+)\s*(%)?", re.IGNORECASE)

NOTE_HELP = (
    "Notes the bot enforces: (Hint 1-3), (once per turn), (N times per turn), (N% chance), and "
    "(max N) on a line that scales with 'per N <status> Count'."
)


def _take_notes(rest: str, line: str, strict: bool) -> tuple[str, int, bool, int]:
    """Peels trailing '(...)' notes off a line. Returns (the line without them, the '(max N)' value or 0, whether it was written 'N%', the '(N% chance)' value or 0). A note the bot cannot enforce used to be dropped silently, so a player wrote '(50% chance)' and believed it worked. In strict mode (anything new or edited) that is now an error; saved passives are re-read with strict off so old ones keep loading."""
    max_value, max_percent, chance = 0, False, 0
    while True:
        m = _NOTE_RE.search(rest)
        if not m:
            return rest, max_value, max_percent, chance
        note = m.group(1).strip()
        rest = rest[: m.start()]
        cm = _CHANCE_NOTE_RE.fullmatch(note)
        if cm:
            chance = int(cm.group(1))
            if strict and not 1 <= chance <= 99:
                raise TriggerParseError(line, "a chance has to be between 1% and 99%")
            continue
        mm = _MAX_NOTE_RE.fullmatch(note)
        if mm:
            max_value, max_percent = int(mm.group(1)), mm.group(2) is not None
            continue
        if not strict:
            continue
        low = note.lower()
        if "encounter" in low or "battle" in low or "scene" in low:
            why = "per-Encounter limits aren't supported yet, so it would fire every time"
        else:
            why = "the bot can't enforce it, so it would be ignored"
        raise TriggerParseError(line, f"the note ({note}) can't be used: {why}. {NOTE_HELP}")
_EFFECT_CONSUME = re.compile(r"consume (\d+) ([a-z ]+?):\s*(.+)", re.IGNORECASE)
_EFFECT_GAIN_COUNT = re.compile(r"gain \+(\d+) ([a-z ]+?) count", re.IGNORECASE)
_EFFECT_GAIN_EXPLICIT = re.compile(
    r"gain ([a-z ]+?) potency:?\s*(\d+),?\s*count:?\s*(\d+)", re.IGNORECASE
)

# The player-made statuses the line being parsed is allowed to use
# (key -> "buff" or "debuff"). Set by parse_trigger_text for the length
# of one call and always reset, parsing never awaits so this is safe.
_active_custom: dict[str, str] = {}


def _check_inflictable(status: str, text: str):
    if status in TARGET_STATUSES or _active_custom.get(status) == "debuff":
        return
    raise TriggerParseError(
        text,
        f"'{status}' isn't a status that can be inflicted. Built-in: "
        f"{', '.join(s.replace('_', ' ').title() for s in TARGET_STATUSES)}. "
        "A custom status has to be created first (/character addstatus) and be a debuff.",
    )


def _check_gainable(status: str, text: str):
    if status in SELF_BUFF_STATUSES or _active_custom.get(status) == "buff":
        return
    raise TriggerParseError(
        text,
        f"'{status}' isn't a self-buff this character can gain. Built-in: "
        f"{', '.join(s.replace('_', ' ').title() for s in SELF_BUFF_STATUSES)}. "
        "A custom resource has to be created first (/character addstatus) and be a buff.",
    )


def _parse_condition_and_effect_text(text: str) -> tuple[Condition, str]:
    """Splits a line's remaining text (after the bracket tag) into a
    Condition and whatever's left over to hand to the effect parser.
    Returns Condition(type='always') with the full text unchanged if no
    condition phrase is recognized, since plenty of valid lines are pure
    effects with no gate beyond their timing tag.
    """
    m = _COND_TARGET_SIDE.match(text)
    if m:
        return Condition(type=f"target_is_{m.group(1).lower()}"), m.group(2)

    m = _COND_SPEED_FASTER.match(text)
    if m:
        return Condition(type="speed_faster"), m.group(1)

    m = _COND_SPEED_DIFF.match(text)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 99:
            raise TriggerParseError(text, "The Speed difference must be between 1 and 99.")
        if m.group(2).lower() == "higher":
            return Condition(type="speed_diff", comparison="gte", value=n), m.group(3)
        return Condition(type="speed_diff", comparison="lte", value=-n), m.group(3)

    m = _COND_HP.match(text)
    if m:
        who, pct, direction, rest = m.groups()
        pct = int(pct)
        if not 0 <= pct <= 100:
            raise TriggerParseError(text, "An HP percentage must be between 0 and 100.")
        ctype = "caster_hp_pct" if who.lower() == "this unit" else "target_hp_pct"
        return Condition(type=ctype, comparison="gte" if direction.lower() == "more" else "lte", value=pct), rest

    m = _COND_SANITY_ANY.match(text)
    if m:
        who, n, direction, rest = m.groups()
        n = int(n)
        if not -45 <= n <= 45:
            raise TriggerParseError(text, "Sanity only goes from -45 to 45, so the number must be in that range.")
        ctype = "caster_sanity" if who.lower() == "this unit" else "target_sanity"
        cmp_ = "lte" if direction.lower().endswith("less") else "gte"
        return Condition(type=ctype, comparison=cmp_, value=n), rest

    m = _COND_AFFINITY.match(text)
    if m:
        return Condition(type="target_affinity", status_name=m.group(1).lower()), m.group(2)

    m = _COND_NEGATIVE.match(text)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= len(TARGET_STATUSES):
            raise TriggerParseError(text, f"The number of negative effects must be between 1 and {len(TARGET_STATUSES)}.")
        return Condition(type="target_negative_count", value=n), m.group(2)

    m = _COND_DEFENDING.match(text)
    if m:
        return Condition(type="target_defending"), m.group(1)

    m = _COND_TARGET_STATUS_ANY.match(text)
    if m:
        status = m.group(1).strip().lower()
        return Condition(type="target_status", status_name=status, min_potency=1), m.group(2)

    m = _COND_TARGET_STATUS_N.match(text)
    if m:
        n, status = int(m.group(1)), m.group(2).strip().lower()
        return _threshold_condition("target_status", status, n, m.group(3)), m.group(4)

    m = _COND_MORALE.match(text)
    if m:
        who, state, rest = m.groups()
        ctype = "caster_morale" if who.lower() == "this unit" else "target_morale"
        key = "panic" if state.lower().startswith("panic") else "low_morale"
        return Condition(type=ctype, status_name=key), rest

    m = _COND_CASTER_SANITY.match(text)
    if m:
        return Condition(type="caster_sanity", comparison="gte", value=int(m.group(1))), m.group(2)

    m = _COND_AT_N_THING_COMMA.match(text)
    if m:
        n, thing, rest = int(m.group(1)), m.group(2).strip().lower(), m.group(3)
        basis = None
        known = set(SELF_BUFF_STATUSES) | set(TARGET_STATUSES) | set(_active_custom)
        thing, basis = _split_basis(thing, known)
    else:
        m = _COND_AT_N_THING.match(text)
        if m:
            n, thing, basis, rest = int(m.group(1)), m.group(2).strip().lower(), m.group(3), m.group(4)
    if m:
        if thing == "speed":
            return Condition(type="caster_speed_at_least", value=n), rest
        thing = "_".join(thing.split())
        if thing in SELF_BUFF_STATUSES or thing in TARGET_STATUSES or thing in _active_custom:
            return _threshold_condition("caster_status", thing, n, basis), rest
        raise TriggerParseError(
            text,
            f"'{thing}' isn't a tracked resource yet (only Speed and existing statuses "
            "are supported conditions this pass, custom Identity resources are queued separately)",
        )

    return Condition(type="always"), text


def _norm_status(raw: str) -> str:
    """'Slash Fragility' -> 'slash_fragility'."""
    return "_".join(raw.strip().lower().split())


def _parse_effect_text(text: str) -> tuple[str, int, str | None, int]:
    """Parses the effect half of a line. Returns
    (effect_type, effect_value, status_name, status_count).

    Every flat-effect pattern below is matched with fullmatch against
    the effect text (minus one trailing parenthetical annotation, see
    _TRAILING_PAREN_RE), not search. This is deliberate: with search, a
    formula phrase like "gain Coin Power based on Speed difference
    (Coin Power +1 for every 2 Speed difference; max 2)" would
    silently match the embedded "Coin Power +1" fragment and produce a
    flat, wrong effect with no warning. Requiring the whole phrase to
    match means anything with leftover, unrecognized text (a formula,
    or a second effect chained on with ';') falls through to an error
    instead of being partially, silently accepted.
    """
    text = text.strip().rstrip(".")

    core = _TRAILING_PAREN_RE.sub("", text).strip()

    for pattern, effect_type in (
        (_EFFECT_BONUS_POWER_PCT, "bonus_damage_pct"),
        (_EFFECT_BONUS_POWER, "bonus_power"),
        (_EFFECT_COIN_POWER, "bonus_coin_power"),
        (_EFFECT_CLASH_POWER, "bonus_clash_power"),
        (_EFFECT_FINAL_POWER, "bonus_final_power"),
        (_EFFECT_SANITY, "sanity_gain"),
        (_EFFECT_SANITY_LOSS, "sanity_loss"),
    ):
        m = pattern.fullmatch(core)
        if m:
            return effect_type, int(m.group(1)), None, 0

    m = _EFFECT_INFLICT_EXPLICIT.fullmatch(core)
    if m:
        status, potency, count = _norm_status(m.group(1)), int(m.group(2)), int(m.group(3))
        _check_inflictable(status, text)
        return "inflict_status", potency, status, count

    m = _EFFECT_INFLICT_COUNT.fullmatch(core)
    if m:
        n, status = int(m.group(1)), _norm_status(m.group(2))
        _check_inflictable(status, text)
        return "inflict_status", 1, status, n

    m = _EFFECT_INFLICT_BARE.fullmatch(core)
    if m:
        n, status = int(m.group(1)), _norm_status(m.group(2))
        _check_inflictable(status, text)
        return "inflict_status", n, status, 1

    m = _EFFECT_GAIN_EXPLICIT.fullmatch(core)
    if m:
        status, potency, count = _norm_status(m.group(1)), int(m.group(2)), int(m.group(3))
        _check_gainable(status, text)
        return "gain_status", potency, status, count

    m = _EFFECT_GAIN_COUNT.fullmatch(core)
    if m:
        n, status = int(m.group(1)), _norm_status(m.group(2))
        _check_gainable(status, text)
        return "gain_status", 0, status, n

    m = _EFFECT_GAIN_STATUS.fullmatch(core)
    if m:
        n, status = int(m.group(1)), _norm_status(m.group(2))
        _check_gainable(status, text)
        return "gain_status", n, status, 1

    if ";" in text:
        raise TriggerParseError(
            text,
            "this line chains more than one effect with ';' -- each effect needs its own line, "
            "repeating the same bracket tag (e.g. two separate ':Coin1: [On Hit] ...' lines)",
        )

    raise TriggerParseError(text, "effect phrase not recognized, see the format guide")


def _parse_trigger_lines(text: str, strict: bool = True) -> tuple[list[Trigger], set[str]]:
    """Parses a full multi-line trigger block, as pasted into the trigger
    modal. One line is one Trigger (or one skill-flag tag), blank lines
    are ignored.

    Line shape: '[optional :CoinN:] [Timing Tag] rest of the line'. The
    rest of the line is handed to the condition parser first (which
    strips off any recognized condition phrase and hands back whatever
    is left), then the effect parser. An optional trailing '(Hint N)'
    anywhere in the line sets that trigger's Hint tier explicitly, and
    always wins when present. Left off, an inflict_status trigger's
    tier is worked out automatically from its own potency*count (same
    scale an already-active status of that size would get, see
    magnitude_to_hint_tier in game/statuses.py); any other effect type
    defaults to 1. See docs/ENGINEERING_NOTES.md#conditions-parse-trigger-text-hint-tier for the full rationale.

    A single line only carries one effect. A Limbus tooltip line chaining
    several effects with ';' needs to become several lines instead, each
    repeating the same tag (this is a known simplification, not an
    oversight).

    Returns (triggers, flags) -- flags is the set of skill-metadata tags
    (clashable_counter, target_fixed, indiscriminate, unclashable) found
    on their own bracket-only lines, e.g. a line that is just
    '[Target Fixed]'.

    Raises TriggerParseError on the first malformed line, with that exact
    line attached, so the caller (the modal's on_submit) can show the
    person precisely which line to fix instead of the whole block.
    """
    triggers: list[Trigger] = []
    flags: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        tag_match = _TAG_RE.match(line)
        if not tag_match:
            raise TriggerParseError(line, "every line needs a bracket timing tag, e.g. '[On Use] ...'")

        coin_str, tag_text, rest = tag_match.groups()
        tag_key = tag_text.strip().lower()

        if not rest.strip() and tag_key in SKILL_FLAG_TAGS:
            if coin_str is not None:
                raise TriggerParseError(
                    line, f"[{tag_text}] is a skill-flag tag, it can't take a ':Coin{coin_str}:' prefix"
                )
            flags.add(SKILL_FLAG_TAGS[tag_key])
            continue

        if tag_key in UNSUPPORTED_TIMINGS:
            raise TriggerParseError(line, f"[{tag_text}] isn't usable yet, {UNSUPPORTED_TIMINGS[tag_key]}")

        if tag_key in SKILL_FLAG_TAGS:
            raise TriggerParseError(
                line, f"[{tag_text}] is a skill-flag tag, it belongs on its own line with no effect text after it"
            )

        if tag_key not in ALL_TIMING_LOOKUP:
            raise TriggerParseError(line, f"'[{tag_text}]' isn't a recognized timing tag")

        timing = ALL_TIMING_LOOKUP[tag_key]
        is_per_coin = tag_key in PER_COIN_TIMINGS

        coin_index = None
        if coin_str is not None:
            coin_index = int(coin_str)
            if not is_per_coin:
                raise TriggerParseError(
                    line,
                    f"':Coin{coin_str}:' is only valid on a per-coin timing tag "
                    f"({', '.join(f'[{k.title()}]' for k in PER_COIN_TIMINGS)}), not [{tag_text}]",
                )
        elif is_per_coin:
            raise TriggerParseError(
                line, f"[{tag_text}] is a per-coin tag, it needs a ':CoinN:' prefix, e.g. ':Coin1: [{tag_text}] ...'"
            )

        explicit_hint_tier = None
        hint_match = _HINT_RE.search(rest)
        if hint_match:
            explicit_hint_tier = int(hint_match.group(1))
            if not (1 <= explicit_hint_tier <= 3):
                raise TriggerParseError(line, "Hint tier must be 1, 2, or 3")
            rest = _HINT_RE.sub("", rest).strip()

        max_per_turn = 0
        per_turn = _PER_TURN_RE.search(rest)
        if per_turn:
            word = per_turn.group(0).lower()
            max_per_turn = int(per_turn.group(1)) if per_turn.group(1) else (2 if "twice" in word else 1)
            if max_per_turn < 1:
                raise TriggerParseError(line, "the per-turn limit must be at least 1")
            rest = _PER_TURN_RE.sub("", rest).strip()

        rest, note_max, note_max_percent, chance = _take_notes(rest, line, strict)
        rest = rest.strip()
        delayed = False
        if _NEXT_TURN_RE.search(rest):
            delayed = True
            rest = _NEXT_TURN_RE.sub("", rest).strip()

        if not rest.strip():
            raise TriggerParseError(line, "no effect text after the timing tag")

        condition, effect_text = _parse_condition_and_effect_text(rest)
        to_ally = False
        if _TO_ALLY_RE.search(effect_text):
            to_ally = True
            effect_text = _TO_ALLY_RE.sub("", effect_text)
        consume_status = None
        consume_amount = 0
        consume_match = _EFFECT_CONSUME.fullmatch(effect_text.strip())
        if consume_match:
            consume_amount = int(consume_match.group(1))
            consume_status = _norm_status(consume_match.group(2))
            if consume_amount < 1:
                raise TriggerParseError(line, "Consume needs an amount of at least 1")
            _check_gainable(consume_status, line)
            effect_text = consume_match.group(3)
        elif effect_text.strip().lower().startswith("consume"):
            raise TriggerParseError(
                line, "Consume lines look like 'Consume 3 Pulsation: Coin Power +2' (amount, resource, colon, payoff)"
            )
        scale_status = None
        scale_per = 0
        scale_basis = "potency"
        scale_target = False
        scale_max = 0
        heal_percent = False
        on_target = False
        heal_match = _EFFECT_HEAL.fullmatch(_TRAILING_PAREN_RE.sub("", effect_text).strip())
        scaled_match = _EFFECT_SCALED.fullmatch(_TRAILING_PAREN_RE.sub("", effect_text).strip())
        remove_match = _EFFECT_REMOVE.fullmatch(_TRAILING_PAREN_RE.sub("", effect_text).strip())
        if remove_match:
            removed = _norm_status(remove_match.group(1))
            if (
                removed not in TARGET_STATUSES and removed not in SELF_BUFF_STATUSES
                and removed not in _active_custom
            ):
                raise TriggerParseError(line, f"'{removed}' isn't a status that can be removed")
            effect_type, effect_value, status_name, status_count = "remove_status", 0, removed, 0
        elif heal_match:
            if int(heal_match.group(1)) < 1:
                raise TriggerParseError(line, "Heal needs an amount of at least 1")
            heal_percent = heal_match.group(2) is not None
            if heal_percent and int(heal_match.group(1)) > 100:
                raise TriggerParseError(line, "Heal can be at most 100% of max HP")
            effect_type, effect_value, status_name, status_count = "heal", int(heal_match.group(1)), None, 0
        elif _EFFECT_SHIELD.fullmatch(effect_text.strip().rstrip(".")):
            sm = _EFFECT_SHIELD.fullmatch(effect_text.strip().rstrip("."))
            heal_percent = sm.group(2) is not None
            if int(sm.group(1)) < 1 or (heal_percent and int(sm.group(1)) > 100):
                raise TriggerParseError(line, "Shield needs 1 or more, or 1 to 100% of max HP")
            effect_type, effect_value, status_name, status_count = "gain_shield", int(sm.group(1)), None, 0
        elif _EFFECT_LEVEL.fullmatch(effect_text.strip().rstrip(".")):
            lm = _EFFECT_LEVEL.fullmatch(effect_text.strip().rstrip("."))
            amount = int(lm.group(4)) * (1 if lm.group(3) == "+" else -1)
            if not 1 <= abs(amount) <= 5:
                raise TriggerParseError(line, "an Offense/Defense Level change is 1 to 5 steps")
            on_target = lm.group(1).lower() == "inflict"
            effect_type, effect_value, status_name, status_count = "level_mod", amount, lm.group(2).lower(), 0
        elif scaled_match:
            kind_word, n, percent, per, scale_name, basis, on_target = scaled_match.groups()
            scale_status = _norm_status(scale_name)
            scale_per = int(per)
            scale_basis = basis.lower()
            scale_target = on_target is not None
            if scale_per < 1:
                raise TriggerParseError(line, "the 'per' number must be at least 1")
            if scale_target:
                known = set(SELF_BUFF_STATUSES) | set(TARGET_STATUSES) | set(_active_custom)
                if scale_status not in known:
                    raise TriggerParseError(line, f"'{scale_status}' isn't a status the target can hold")
            else:
                _check_gainable(scale_status, line)
            if kind_word.lower() == "deal":
                effect_type = "bonus_damage_pct" if percent else "bonus_power"
            elif percent:
                raise TriggerParseError(line, "Coin Power is a flat number, it can't be a percent")
            else:
                effect_type = "bonus_coin_power"
            effect_value, status_name, status_count = int(n), None, 0
            if effect_type == "bonus_damage_pct" and not note_max:
                raise TriggerParseError(
                    line, "a percent that scales needs a cap so it can't run away, "
                    "e.g. 'Deal +5% damage per 6 Burn Count on the target (max 15%)'"
                )
            if note_max_percent and effect_type != "bonus_damage_pct" and strict:
                raise TriggerParseError(
                    line, "a % cap only belongs on a percent line, like 'Deal +5% damage per 6 Burn Count on the target (max 15%)'"
                )
            scale_max = note_max
        else:
            effect_type, effect_value, status_name, status_count = _parse_effect_text(effect_text)
            if note_max and strict:
                raise TriggerParseError(
                    line, "(max N) only works on a line that scales, like 'Coin Power +1 per 5 Poise Count (max 3)'"
                )

        if effect_type == "bonus_damage_pct":
            if not 1 <= effect_value <= MAX_DAMAGE_PCT:
                raise TriggerParseError(line, f"Deal +N% damage takes N from 1 to {MAX_DAMAGE_PCT} per line")
            if scale_max > MAX_DAMAGE_PCT:
                raise TriggerParseError(line, f"the cap on a percent line can be at most {MAX_DAMAGE_PCT}%")

        if strict and effect_type in POWER_EFFECTS:
            allowed = list(POWER_TIMINGS) + (["coin_start"] if effect_type in COIN_START_EFFECTS else [])
            if timing not in allowed:
                where = ", ".join(f"[{k.title()}]" for k, v in SKILL_LEVEL_TIMINGS.items() if v in POWER_TIMINGS)
                message = (
                    f"this effect changes the hit before the coins are tossed, so at [{tag_text}] it would do "
                    f"nothing. It works at {where}"
                )
                if effect_type not in COIN_START_EFFECTS:
                    message += " (this one covers the whole skill, so it has no per-coin timing)."
                else:
                    message += " and, with a :CoinN: prefix, at [Coin Start]."
                raise TriggerParseError(line, message)

        # A hand-typed "(Hint N)" always wins. Otherwise, a trigger that
        # inflicts a status gets its tier worked out the same way an
        # ALREADY-ACTIVE status of that same potency/count would be
        # (magnitude_to_hint_tier, game/statuses.py) -- so an admin
        # doesn't have to guess a number by hand, and can't forget to
        # flag something dangerous. Floored at 1, never 0: hint_tier's
        # valid range is 1-3, same as the old flat default this
        # replaces. Any other effect type (bonus_power, sanity_gain,
        # gain_status -- none of them "inflict danger onto an opponent"
        # the way a status does) keeps the old flat default of 1.
        if explicit_hint_tier is not None:
            hint_tier = explicit_hint_tier
        elif effect_type == "inflict_status":
            hint_tier = max(1, magnitude_to_hint_tier(effect_value * status_count, status_name))
        else:
            hint_tier = 1

        if to_ally and effect_type not in ("heal", "gain_status", "sanity_gain", "remove_status", "gain_shield") and not (
            effect_type == "level_mod" and not on_target
        ):
            raise TriggerParseError(
                line, "'to the ally' only works with Heal, Gain, Remove, Gain Sanity, Shield and Gain Level effects"
            )
        if delayed and effect_type not in POST_HIT_EFFECTS:
            raise TriggerParseError(line, "'next turn' only works on effects that land after the hit (Gain, Inflict, Heal, Shield, Sanity, Level)")
        if delayed and consume_status:
            raise TriggerParseError(line, "'next turn' can't be combined with Consume")

        triggers.append(Trigger(
            condition=condition,
            effect_type=effect_type,
            timing=timing,
            coin_index=coin_index,
            effect_value=effect_value,
            status_name=status_name,
            status_count=status_count,
            hint_tier=hint_tier,
            raw_text=line,
            consume_status=consume_status,
            consume_amount=consume_amount,
            scale_status=scale_status,
            scale_per=scale_per,
            scale_basis=scale_basis,
            scale_target=scale_target,
            scale_max=scale_max,
            heal_percent=heal_percent,
            to_ally=to_ally,
            max_per_turn=max_per_turn,
            chance=chance,
            delayed=delayed,
            on_target=on_target,
        ))

    return triggers, flags


def parse_trigger_text(
    text: str, custom_statuses: dict[str, str] | None = None, strict: bool = True,
) -> tuple[list[Trigger], set[str]]:
    """Parses a trigger block (see _parse_trigger_lines above for the full format). `custom_statuses` maps the slug of each player-made status the owner has to its kind ("buff" or "debuff"), which is what lets lines like 'Gain +2 Pulsation Count' pass. `strict` (the default) refuses notes and effect/timing pairs that would silently do nothing; the only caller that turns it off is the battle-time rebuild of an already saved passive, so passives saved before these checks keep loading."""
    _active_custom.clear()
    _active_custom.update(custom_statuses or {})
    try:
        return _parse_trigger_lines(text, strict)
    finally:
        _active_custom.clear()
