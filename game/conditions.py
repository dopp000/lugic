import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
]

EFFECT_TYPES = ["bonus_power", "bonus_coin_power", "inflict_status", "gain_status", "sanity_gain"]

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
    # to. See the Evasion-resource docstring on resolve_skill in
    # game/skills.py, and the Counter-resource docstring on
    # apply_incoming_hit in cogs/battle.py, for the actual mechanics
    # these fire off of. caster on the context is the defender (the one
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
}

# The only statuses a caster can hold on themselves as a self-buff
# resource right now. "Gain N <Status>" and "At N+ <Status>" (when the
# thing named isn't Speed) both check against this list, anything else
# is an unmodeled custom resource (Strider, Assist Defense, Deathrite,
# named Identity resources, etc) and gets rejected with a clear message,
# matching this pass's scope. Evasion works like Poise (a count-based
# stack consumed one-per-coin), read off the DEFENDER instead of the
# attacker -- see the Evasion-resource docstring on resolve_skill in
# game/skills.py.
#
# NOTE: "counter" is deliberately NOT in this list anymore -- Counter
# used to be a status/resource here, but it's now a Skill-level
# mechanic instead (the [Counter] skill flag, see SKILL_FLAG_TAGS
# above, plus find_eligible_counter/apply_counter_redirects in
# cogs/battle.py). A skill, not a stat a caster holds.
SELF_BUFF_STATUSES = ["poise", "charge", "evasion"]

# Target-facing statuses, mirrors statuses.py's INFLICTABLE_STATUSES.
# Duplicated here rather than imported so this module doesn't need to
# import statuses.py just for one list.
TARGET_STATUSES = ["burn", "bleed", "tremor", "rupture", "sinking"]


@dataclass
class Condition:
    """One check against battle state. type is one of CONDITION_TYPES.

    always -- no fields used, evaluates True unconditionally. Used for
    a trigger that is pure timing (e.g. "[Clash Win] Inflict +2 Rupture
    Count" has nothing to check beyond "did Clash Win happen").
    target_status / caster_status -- status_name/min_potency/min_count.
    caster_status's min_count defaults to 0 (presence-only) unless the
    phrase gave an explicit threshold ("At 5+ Charge" sets min_potency
    only, count stays 0).
    target_hp_pct / caster_sanity -- comparison/value. target_hp_pct has
    no recognized phrase in the parser yet (not in this pass's locked
    condition scope, only reachable by constructing a Condition
    directly), kept here so evaluate_condition and the dataclass shape
    stay ready for it once/if it gets a phrase added.
    speed_faster -- no fields, reads caster_slot_speed/target_slot_speed
    off the TriggerContext directly.
    caster_speed_at_least -- value only, checked against
    caster_slot_speed.
    first_hit_of_round -- no fields, reads is_first_hit_of_round.
    """
    type: str
    status_name: str | None = None
    min_potency: int = 0
    min_count: int = 0
    comparison: str = "gte"
    value: int = 0


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
    is_first_hit_of_round: bool = False
    caster_slot_speed: int = 0
    target_slot_speed: int = 0


def evaluate_condition(condition: Condition, context: TriggerContext) -> bool:
    """Pure evaluation, no side effects. Safe to call speculatively (e.g.
    for Hint display) against a target the caster hasn't actually
    declared on yet.
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

    if condition.type == "first_hit_of_round":
        return context.is_first_hit_of_round

    if condition.type == "speed_faster":
        if context.target is None:
            return False
        return context.caster_slot_speed > context.target_slot_speed

    if condition.type == "caster_speed_at_least":
        return context.caster_slot_speed >= condition.value

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

    hint_tier (1-3) is hand-set by whoever built the skill, written as
    a trailing "(Hint N)" on the line. Defaults to 1 if omitted.

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

_COND_SPEED_FASTER = re.compile(r"if this unit'?s speed is faster than the target'?s,?\s*(.*)", re.IGNORECASE)
_COND_TARGET_STATUS_ANY = re.compile(r"if (?:the )?target has any ([a-z]+),?\s*(.*)", re.IGNORECASE)
_COND_TARGET_STATUS_N = re.compile(r"if (?:the )?target has (\d+)\+ ([a-z]+),?\s*(.*)", re.IGNORECASE)
_COND_CASTER_SANITY = re.compile(r"if this unit'?s sanity is (\d+)\+,?\s*(.*)", re.IGNORECASE)
_COND_AT_N_THING = re.compile(r"at (\d+)\+ ([a-z]+),?\s*(.*)", re.IGNORECASE)

_EFFECT_BONUS_POWER_PCT = re.compile(r"deal \+(\d+)%\s*damage", re.IGNORECASE)
_EFFECT_BONUS_POWER = re.compile(r"deal \+(\d+)\s*damage", re.IGNORECASE)
_EFFECT_COIN_POWER = re.compile(r"coin power \+(\d+)", re.IGNORECASE)
_EFFECT_SANITY = re.compile(r"gain (\d+) sanity", re.IGNORECASE)
_EFFECT_INFLICT_EXPLICIT = re.compile(
    r"inflict ([a-z ]+?) potency:?\s*(\d+),?\s*count:?\s*(\d+)", re.IGNORECASE
)
_EFFECT_INFLICT_COUNT = re.compile(r"inflict \+(\d+) ([a-z ]+?) count", re.IGNORECASE)
_EFFECT_INFLICT_BARE = re.compile(r"inflict (\d+) ([a-z ]+)", re.IGNORECASE)
_EFFECT_GAIN_STATUS = re.compile(r"gain (\d+) ([a-z ]+)", re.IGNORECASE)


def _parse_condition_and_effect_text(text: str) -> tuple[Condition, str]:
    """Splits a line's remaining text (after the bracket tag) into a
    Condition and whatever's left over to hand to the effect parser.
    Returns Condition(type='always') with the full text unchanged if no
    condition phrase is recognized, since plenty of valid lines are pure
    effects with no gate beyond their timing tag.
    """
    m = _COND_SPEED_FASTER.match(text)
    if m:
        return Condition(type="speed_faster"), m.group(1)

    m = _COND_TARGET_STATUS_ANY.match(text)
    if m:
        status = m.group(1).strip().lower()
        return Condition(type="target_status", status_name=status, min_potency=1), m.group(2)

    m = _COND_TARGET_STATUS_N.match(text)
    if m:
        n, status = int(m.group(1)), m.group(2).strip().lower()
        return Condition(type="target_status", status_name=status, min_potency=n), m.group(3)

    m = _COND_CASTER_SANITY.match(text)
    if m:
        return Condition(type="caster_sanity", comparison="gte", value=int(m.group(1))), m.group(2)

    m = _COND_AT_N_THING.match(text)
    if m:
        n, thing = int(m.group(1)), m.group(2).strip().lower()
        if thing == "speed":
            return Condition(type="caster_speed_at_least", value=n), m.group(3)
        if thing in SELF_BUFF_STATUSES or thing in TARGET_STATUSES:
            return Condition(type="caster_status", status_name=thing, min_potency=n), m.group(3)
        raise TriggerParseError(
            text,
            f"'{thing}' isn't a tracked resource yet (only Speed and existing statuses "
            "are supported conditions this pass, custom Identity resources are queued separately)",
        )

    return Condition(type="always"), text


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

    if _EFFECT_BONUS_POWER_PCT.search(text):
        raise TriggerParseError(
            text,
            "percent-based or formula-based damage scaling isn't supported yet, only flat "
            "'deal +N damage' (queued separately, see the handoff doc's bigger-additions list)",
        )

    core = _TRAILING_PAREN_RE.sub("", text).strip()

    for pattern, effect_type in (
        (_EFFECT_BONUS_POWER, "bonus_power"),
        (_EFFECT_COIN_POWER, "bonus_coin_power"),
        (_EFFECT_SANITY, "sanity_gain"),
    ):
        m = pattern.fullmatch(core)
        if m:
            return effect_type, int(m.group(1)), None, 0

    m = _EFFECT_INFLICT_EXPLICIT.fullmatch(core)
    if m:
        status, potency, count = m.group(1).strip().lower(), int(m.group(2)), int(m.group(3))
        return "inflict_status", potency, status, count

    m = _EFFECT_INFLICT_COUNT.fullmatch(core)
    if m:
        n, status = int(m.group(1)), m.group(2).strip().lower()
        return "inflict_status", 1, status, n

    m = _EFFECT_INFLICT_BARE.fullmatch(core)
    if m:
        n, status = int(m.group(1)), m.group(2).strip().lower()
        return "inflict_status", n, status, 1

    m = _EFFECT_GAIN_STATUS.fullmatch(core)
    if m:
        n, status = int(m.group(1)), m.group(2).strip().lower()
        if status not in SELF_BUFF_STATUSES:
            raise TriggerParseError(
                text,
                f"'{status}' isn't a self-buff resource the engine tracks yet "
                f"(only {', '.join(s.capitalize() for s in SELF_BUFF_STATUSES)} exist right now, "
                "custom resources like Strider/Assist Defense/Deathrite are queued separately)",
            )
        return "gain_status", n, status, 1

    if ";" in text:
        raise TriggerParseError(
            text,
            "this line chains more than one effect with ';' -- each effect needs its own line, "
            "repeating the same bracket tag (e.g. two separate ':Coin1: [On Hit] ...' lines)",
        )

    raise TriggerParseError(text, "effect phrase not recognized, see the format guide")


def parse_trigger_text(text: str) -> tuple[list[Trigger], set[str]]:
    """Parses a full multi-line trigger block, as pasted into the trigger
    modal. One line is one Trigger (or one skill-flag tag), blank lines
    are ignored.

    Line shape: '[optional :CoinN:] [Timing Tag] rest of the line'. The
    rest of the line is handed to the condition parser first (which
    strips off any recognized condition phrase and hands back whatever
    is left), then the effect parser. An optional trailing '(Hint N)'
    anywhere in the line sets that trigger's Hint tier, default 1 if
    omitted.

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

        hint_tier = 1
        hint_match = _HINT_RE.search(rest)
        if hint_match:
            hint_tier = int(hint_match.group(1))
            if not (1 <= hint_tier <= 3):
                raise TriggerParseError(line, "Hint tier must be 1, 2, or 3")
            rest = _HINT_RE.sub("", rest).strip()

        if not rest.strip():
            raise TriggerParseError(line, "no effect text after the timing tag")

        condition, effect_text = _parse_condition_and_effect_text(rest)
        effect_type, effect_value, status_name, status_count = _parse_effect_text(effect_text)

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
        ))

    return triggers, flags