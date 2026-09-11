import random
from dataclasses import dataclass, field, replace

from game.conditions import Trigger, TriggerContext, evaluate_condition


@dataclass
class Skill:
    """A single skill definition: just the numbers, no Discord code."""

    name: str
    base_power: int
    coin_power: int
    coins: int
    damage_type: str = "blunt"  # "slash", "blunt", or "pierce"

    # Per-coin status effects, one entry per coin (aligned by index). See docs/ENGINEERING_NOTES.md#skills-comment-17.
    coin_statuses: list[str | None] = field(default_factory=list)
    coin_status_potencies: list[int] = field(default_factory=list)
    coin_status_counts: list[int] = field(default_factory=list)

    # Skill-level Conditional Triggers, independent of the per-coin status system above. See docs/ENGINEERING_NOTES.md#skills-comment-28.
    triggers: list[Trigger] = field(default_factory=list)

    # Skill-level metadata flags parsed alongside triggers (target_fixed, unclashable, indiscriminate, clashable_counter). See docs/ENGINEERING_NOTES.md#skills-comment-35.
    tags: set[str] = field(default_factory=set)

    # Total number of enemy slots this skill hits when it resolves as an
    # UNOPPOSED attack (1 = a normal single-target skill, no splash).
    # Only ever matters for the solo/unopposed path -- Clashes are
    # completely untouched by this. See declare()'s attack_weight
    # target-selection and the splash-damage handling in combat()'s
    # solo branch, both in cogs/battle.py.
    attack_weight: int = 1

    def __post_init__(self):
        # Pads status lists up to `coins` entries if a Skill gets built
        # without explicitly passing them (quick construction, tests),
        # so nothing that indexes into these lists breaks.
        while len(self.coin_statuses) < self.coins:
            self.coin_statuses.append(None)
        while len(self.coin_status_potencies) < self.coins:
            self.coin_status_potencies.append(0)
        while len(self.coin_status_counts) < self.coins:
            self.coin_status_counts.append(0)


@dataclass
class CoinResult:
    """The outcome of a single coin toss within a skill's resolution."""

    heads: bool
    power_after: int
    damage_dealt: int

    # Per-coin Triggers (on_hit / heads_hit / tails_hit, matched by coin_index) that fired on this specific coin, already filtered to ones... See docs/ENGINEERING_NOTES.md#skills-comment-62.
    fired_triggers: list[Trigger] = field(default_factory=list)

    # True if this coin Crit -- the caster held Poise (count > 0) at the moment this coin resolved, independent of Heads/Tails (see the... See docs/ENGINEERING_NOTES.md#skills-comment-69.
    is_crit: bool = False
    crit_bonus_damage: int = 0

    # True if the DEFENDER dodged this coin -- set externally by
    # apply_incoming_hit in cogs/battle.py after a successful [Evade]
    # (find_eligible_evade), not by anything in this file.
    is_evaded: bool = False


@dataclass
class SkillResult:
    """The full outcome of resolving one skill: every coin, plus the total."""

    skill: Skill
    coin_results: list[CoinResult]

    @property
    def total_damage(self) -> int:
        return sum(c.damage_dealt for c in self.coin_results)

    @property
    def final_power(self) -> int:
        """The Power reached after the last coin. This is what Clashes compare."""
        return self.coin_results[-1].power_after

    def log(self) -> str:
        """A human-readable breakdown, useful for both testing and Discord output."""
        lines = [f"**{self.skill.name}** (Base {self.skill.base_power}, +{self.skill.coin_power} Coin Power, {self.skill.coins} coins)"]
        for i, c in enumerate(self.coin_results, start=1):
            face = "Heads" if c.heads else "Tails"
            status_note = ""
            if i - 1 < len(self.skill.coin_statuses) and self.skill.coin_statuses[i - 1]:
                name = self.skill.coin_statuses[i - 1]
                potency = self.skill.coin_status_potencies[i - 1]
                count = self.skill.coin_status_counts[i - 1]
                status_note = f", inflicts {name} {potency}/{count}"
            lines.append(f"  Coin {i}: {face}, Power {c.power_after}, {c.damage_dealt} damage{status_note}")
        lines.append(f"  **Total: {self.total_damage} damage**")
        return "\n".join(lines)


def flip_coin(heads_chance: int = 50) -> bool:
    """Rolls a percentage-based coin flip. See docs/ENGINEERING_NOTES.md#skills-flip-coin for the full rationale."""
    roll = random.randint(1, 100)
    return roll <= heads_chance


# Poise/Crit tuning: each point of Poise Potency held is a 5% chance
# PER COIN to crit (not a guaranteed crit while any Poise remains), and
# a crit multiplies that coin's current Power by 1.2x rather than
# adding a flat bonus equal to Potency. Count only decays on an actual
# crit (a coin that rolls the chance and succeeds), not merely on a
# coin tossed while Poise is held.
POISE_CRIT_CHANCE_PER_POTENCY = 5
POISE_CRIT_DAMAGE_MULTIPLIER = 1.2


def resolve_skill(
    skill: Skill,
    heads_chance: int = 50,
    context: TriggerContext | None = None,
    extra_coin_timings: tuple[str, ...] = (),
) -> SkillResult:
    """Resolves a skill's coins one at a time, in sequence. See docs/ENGINEERING_NOTES.md#skills-resolve-skill for the full rationale."""
    power = skill.base_power
    coin_power = skill.coin_power
    results: list[CoinResult] = []

    poise_remaining = 0
    poise_potency = 0
    if context is not None and context.caster is not None:
        poise = context.caster.get_status("poise")
        if poise is not None:
            poise_remaining = poise.count
            poise_potency = poise.potency

    for i in range(skill.coins):
        coin_index = i + 1
        fired_triggers: list[Trigger] = []

        if context is not None:
            coin_start_fired = [
                t for t in skill.triggers
                if t.coin_index == coin_index
                and t.timing == "coin_start"
                and evaluate_condition(t.condition, context)
            ]
            power += sum(t.effect_value for t in coin_start_fired if t.effect_type == "bonus_power")
            coin_power += sum(t.effect_value for t in coin_start_fired if t.effect_type == "bonus_coin_power")
            fired_triggers.extend(
                t for t in coin_start_fired if t.effect_type in ("inflict_status", "sanity_gain")
            )

        heads = flip_coin(heads_chance)
        if heads:
            power += coin_power

        # Evade no longer lives here -- it's a declared Skill of its
        # own now (the [Evade] flag), resolved by find_eligible_evade
        # in cogs/battle.py BEFORE apply_incoming_hit ever calls into
        # this function, not as a per-coin resource check on the
        # attacker's own resolve_skill call. is_evaded on a CoinResult
        # is still a real field, a successful Evade marks every coin
        # of the incoming attack as evaded from the outside, it's just
        # never SET from inside this function anymore.
        is_evaded = False

        is_crit = False
        crit_bonus_damage = 0
        if not is_evaded and poise_remaining > 0:
            crit_chance = min(100, poise_potency * POISE_CRIT_CHANCE_PER_POTENCY)
            if flip_coin(crit_chance):
                is_crit = True
                crit_bonus_damage = round(power * (POISE_CRIT_DAMAGE_MULTIPLIER - 1))
                poise_remaining -= 1

        if context is not None and not is_evaded:
            post_toss_timings = (
                "on_hit", "heads_hit", "tails_hit",
                "current_coin_attack_end", "heads_attack_end", "tails_attack_end",
                "on_crit", "on_crit_heads_hit", "on_crit_tails_hit",
                *extra_coin_timings,
            )
            for t in skill.triggers:
                if t.coin_index != coin_index or t.timing not in post_toss_timings:
                    continue
                if t.timing in ("heads_hit", "heads_attack_end") and not heads:
                    continue
                if t.timing in ("tails_hit", "tails_attack_end") and heads:
                    continue
                if t.timing in ("on_crit", "on_crit_heads_hit", "on_crit_tails_hit") and not is_crit:
                    continue
                if t.timing == "on_crit_heads_hit" and not heads:
                    continue
                if t.timing == "on_crit_tails_hit" and heads:
                    continue
                if t.effect_type not in ("inflict_status", "sanity_gain"):
                    continue
                if evaluate_condition(t.condition, context):
                    fired_triggers.append(t)

        results.append(CoinResult(
            heads=heads, power_after=power, damage_dealt=(0 if is_evaded else power),
            fired_triggers=fired_triggers,
            is_crit=is_crit, crit_bonus_damage=crit_bonus_damage,
            is_evaded=is_evaded,
        ))

    return SkillResult(skill=skill, coin_results=results)


def resolve_triggers(
    skill: Skill, context: TriggerContext, timing: str
) -> tuple[Skill, list[Trigger]]:
    """Evaluates every skill-level trigger matching `timing` against the current context. See docs/ENGINEERING_NOTES.md#skills-resolve-triggers for the full rationale."""
    fired = [
        t for t in skill.triggers
        if t.timing == timing and evaluate_condition(t.condition, context)
    ]

    bonus_power = sum(t.effect_value for t in fired if t.effect_type == "bonus_power")
    bonus_coin_power = sum(t.effect_value for t in fired if t.effect_type == "bonus_coin_power")

    if bonus_power or bonus_coin_power:
        skill = replace(
            skill,
            base_power=skill.base_power + bonus_power,
            coin_power=skill.coin_power + bonus_coin_power,
        )

    post_hit = [t for t in fired if t.effect_type in ("inflict_status", "sanity_gain", "gain_status")]
    return skill, post_hit


def resolve_clash(result_a: SkillResult, result_b: SkillResult) -> SkillResult | None:
    """Compares two SkillResults by final_power. See docs/ENGINEERING_NOTES.md#skills-resolve-clash for the full rationale."""
    if result_a.final_power > result_b.final_power:
        return result_a
    elif result_b.final_power > result_a.final_power:
        return result_b
    else:
        return None


@dataclass
class PairwiseClashOutcome:
    """The result of the earlier per-coin-index clash model. Superseded by
    the round-based attrition model below, kept for reference.
    """
    winner: str  # "a" or "b"
    result_a: SkillResult
    result_b: SkillResult
    a_survived: list[bool]
    b_survived: list[bool]
    rerolls: int

    def winner_result(self) -> SkillResult:
        return self.result_a if self.winner == "a" else self.result_b

    def loser_result(self) -> SkillResult:
        return self.result_b if self.winner == "a" else self.result_a

    def winner_survived(self) -> list[bool]:
        return self.a_survived if self.winner == "a" else self.b_survived

    def total_damage(self) -> int:
        result = self.winner_result()
        survived = self.winner_survived()
        return sum(c.damage_dealt for c, s in zip(result.coin_results, survived) if s)


def resolve_pairwise_clash(skill_a: Skill, skill_b: Skill, max_rerolls: int = 50) -> PairwiseClashOutcome:
    """The earlier per-coin-index clash model. Superseded by
    resolve_round_clash below, kept here for reference.
    """
    rerolls = 0
    while True:
        result_a = resolve_skill(skill_a)
        result_b = resolve_skill(skill_b)

        len_a = len(result_a.coin_results)
        len_b = len(result_b.coin_results)
        max_len = max(len_a, len_b)

        a_survived = [True] * len_a
        b_survived = [True] * len_b

        for i in range(max_len):
            if i >= len_a or i >= len_b:
                continue
            power_a = result_a.coin_results[i].power_after
            power_b = result_b.coin_results[i].power_after
            if power_a > power_b:
                b_survived[i] = False
            elif power_b > power_a:
                a_survived[i] = False

        a_total = sum(c.power_after for c, s in zip(result_a.coin_results, a_survived) if s)
        b_total = sum(c.power_after for c, s in zip(result_b.coin_results, b_survived) if s)

        if a_total == b_total:
            rerolls += 1
            if rerolls >= max_rerolls:
                raise RuntimeError(
                    f"Clash failed to resolve after {max_rerolls} rerolls, "
                    "something is almost certainly wrong with the input skills."
                )
            continue

        winner = "a" if a_total > b_total else "b"
        return PairwiseClashOutcome(
            winner=winner,
            result_a=result_a,
            result_b=result_b,
            a_survived=a_survived,
            b_survived=b_survived,
            rerolls=rerolls,
        )


@dataclass
class ClashRound:
    """One round of clash attrition: both sides tossed all their currently remaining coins fresh, and whichever side's final Power was lower... See docs/ENGINEERING_NOTES.md#skills-clashround for the full rationale."""
    result_a: SkillResult
    result_b: SkillResult
    coins_a_before: int
    coins_b_before: int
    loser: str | None  # "a", "b", or None on a tie (nobody loses a coin this round)


@dataclass
class ClashOutcome:
    """The full outcome of a round-based attrition clash: every attrition round that happened, plus the winner's final one-sided damage toss (a... See docs/ENGINEERING_NOTES.md#skills-clashoutcome for the full rationale."""
    winner: str  # "a" or "b"
    rounds: list[ClashRound]
    winner_final_result: SkillResult

    # [Before Attack] triggers that fired for the winner specifically, right before their final decisive toss (see resolve_round_clash below... See docs/ENGINEERING_NOTES.md#skills-comment-350.
    winner_before_attack_post_hit: list[Trigger] = field(default_factory=list)

    def total_damage(self) -> int:
        return self.winner_final_result.total_damage


def resolve_round_clash(
    skill_a: Skill,
    skill_b: Skill,
    heads_chance_a: int = 50,
    heads_chance_b: int = 50,
    context_a: TriggerContext | None = None,
    context_b: TriggerContext | None = None,
    max_rounds: int = 100,
) -> ClashOutcome:
    """Resolves a clash via round-by-round coin attrition. See docs/ENGINEERING_NOTES.md#skills-resolve-round-clash for the full rationale."""
    coins_a = skill_a.coins
    coins_b = skill_b.coins
    rounds: list[ClashRound] = []
    round_count = 0

    while coins_a > 0 and coins_b > 0:
        round_count += 1
        if round_count > max_rounds:
            raise RuntimeError(
                f"Clash failed to resolve after {max_rounds} rounds, "
                "something is almost certainly wrong with the input skills."
            )

        before_a, before_b = coins_a, coins_b
        temp_a = replace(skill_a, coins=coins_a)
        temp_b = replace(skill_b, coins=coins_b)
        result_a = resolve_skill(temp_a, heads_chance_a, context_a)
        result_b = resolve_skill(temp_b, heads_chance_b, context_b)

        if result_a.final_power > result_b.final_power:
            loser = "b"
            coins_b -= 1
        elif result_b.final_power > result_a.final_power:
            loser = "a"
            coins_a -= 1
        else:
            loser = None

        rounds.append(ClashRound(
            result_a=result_a, result_b=result_b,
            coins_a_before=before_a, coins_b_before=before_b,
            loser=loser,
        ))

    winner = "a" if coins_a > 0 else "b"
    winner_skill = skill_a if winner == "a" else skill_b
    winner_remaining = coins_a if winner == "a" else coins_b
    winner_heads_chance = heads_chance_a if winner == "a" else heads_chance_b
    winner_context = context_a if winner == "a" else context_b

    # [Before Attack] fires HERE, for the winner only, right before their one real damage-dealing toss -- see the docstring above for why this... See docs/ENGINEERING_NOTES.md#skills-comment-415.
    before_attack_post_hit: list[Trigger] = []
    if winner_context is not None:
        winner_skill, before_attack_post_hit = resolve_triggers(winner_skill, winner_context, "before_attack")

    final_skill = replace(
        winner_skill,
        coins=winner_remaining,
        coin_statuses=winner_skill.coin_statuses[:winner_remaining],
        coin_status_potencies=winner_skill.coin_status_potencies[:winner_remaining],
        coin_status_counts=winner_skill.coin_status_counts[:winner_remaining],
    )
    # hit_after_clash_win only ever applies to THIS toss -- the winner's one real damage-dealing toss -- never to the attrition rounds above... See docs/ENGINEERING_NOTES.md#skills-comment-435.
    final_result = resolve_skill(
        final_skill, winner_heads_chance, winner_context, extra_coin_timings=("hit_after_clash_win",)
    )

    return ClashOutcome(
        winner=winner, rounds=rounds, winner_final_result=final_result,
        winner_before_attack_post_hit=before_attack_post_hit,
    )