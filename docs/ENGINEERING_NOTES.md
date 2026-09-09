# Engineering Notes

Detailed rationale extracted from code comments/docstrings, kept here so the source stays readable. Code keeps a short summary plus a link back to the matching heading below.

## cogs/battle.py :: _can_manage_fighter
<a id="battle-can-manage-fighter"></a>

True if whoever's invoking this is either the fighter's own linked
    owner, or holds the server's admin role (ADMIN_ROLE_ID). Used for
    destructive fighter-management actions like /battle removefighter,
    where "your own fighter, or an admin" is the right bar -- same
    pattern /character edit/delete already use, just role-based instead
    of Discord's built-in manage_guild permission, since that's what was
    asked for here specifically.

## cogs/battle.py :: _parse_status_tokens
<a id="battle-parse-status-tokens"></a>

Parses the comma-separated per-coin status string ('none' or
    'Name:Potency:Count' per coin) into three aligned lists. Returns
    (coin_statuses, potencies, counts, error) -- error is None on
    success, or a user-facing message on failure (ignore the lists in
    that case). Pulled out of addskill's old body so the new popup's
    on_submit can share the exact same parsing/error text.

## cogs/battle.py :: AddSkillModal
<a id="battle-addskillmodal"></a>

The full skill-creation popup: everything /battle addskill used to
    collect as 6 required slash-command options (base_power, coin_power,
    coins, damage_type, status_input) PLUS the separate trigger modal is
    now just this one popup. addskill itself only takes `fighter` --
    who's learning it -- and opens this immediately.

    Discord caps a modal at 5 components. To fit within that, Base
    Power / Coin Power / Coins / Damage Type are packed into one
    comma-separated line ("5, 5, 3, Blunt") and parsed by hand in
    on_submit below, same pattern status_input already used for
    per-coin data -- there wasn't room to give each its own box.

## cogs/battle.py :: _status_hint_tier
<a id="battle-status-hint-tier"></a>

The worse of a fighter's currently active statuses, mapped to a
    tier by magnitude (potency * count). Rupture gets a flat +1 on top
    of its magnitude tier, capped at 3, since its automatic on-hit
    trigger makes any active Rupture a live threat regardless of size.

## cogs/battle.py :: _skill_hint_tier
<a id="battle-skill-hint-tier"></a>

The highest hint_tier among the fighter's known skills whose
    trigger condition currently reads true against at least one living
    enemy. Speculative, this doesn't require the skill to actually be
    declared, it's meant to warn "this fighter COULD hit hard right now
    if they use this."

## cogs/battle.py :: compute_hint_tier
<a id="battle-compute-hint-tier"></a>

The tier to show on this fighter's Hint line: the higher of their
    live-triggerable skill danger and their active status danger.
    Returns None if there's nothing worth flagging.

## cogs/battle.py :: apply_incoming_hit
<a id="battle-apply-incoming-hit"></a>

Applies each landed coin's damage/status, and collects whichever
    per-coin Triggers (on_hit/heads_hit/tails_hit) fired on that coin
    (see CoinResult.fired_triggers), so the caller can hand them to
    apply_trigger_effects alongside the skill-level ones. `result` here
    is always the coins that actually landed -- attrition rounds during
    a clash never reach this function, only the final toss does -- so
    every coin iterated below is a real hit.

    `caster` is needed so a Crit coin (see CoinResult.is_crit) can
    consume 1 count off the caster's real Poise stack here -- resolve_skill
    only computed is_crit against a local copy, it never touches the
    Fighter object (see its docstring in game/skills.py). Crit bonus
    damage is folded into the SAME resistance check as the coin's
    normal damage (still a hit of that skill's damage_type, just a
    harder one), unlike Rupture's bonus below, which is the TARGET's
    own reaction and deliberately bypasses resistance entirely.

    Returns evade_count too: how many of this result's coins had
    is_evaded set (computed in resolve_skill, see its Evasion docstring).
    An evaded coin is skipped entirely -- no resistance check, no
    Rupture, no coin status, doesn't add to total_damage -- and just
    logs the dodge, decaying 1 count off the target's real Evasion
    stack per dodge.

    skip_evasion=True bypasses the target's Evasion check entirely
    (every coin lands as if they had none) -- used specifically for a
    Counter skill's retaliation strike against the original attacker,
    since Counter is explicitly meant to bypass whatever defensive
    skills its target has active, not just deal normal damage to them.

    Note: the OLD Counter status/resource mechanic (flat retaliation
    damage whenever the target held a "counter" status) has been
    removed entirely -- Counter is now a Skill-level mechanic (see the
    [Counter]/[Clashable Counter] flags and find_eligible_counter /
    find_eligible_clashable_counter / apply_counter_redirects below),
    not something baked into every single hit here.

    Stagger multiplier: if `target` is ALREADY Stagger'd (from an
    earlier hit, current_stagger_tier > 0) when this hit lands, the
    summed total_damage gets multiplied by STAGGER_MULTIPLIERS for
    that tier, applied once to the total rather than per-coin (avoids
    compounding rounding error across coins). Deliberately checked
    BEFORE this hit's own Stagger state update -- the hit that FIRST
    triggers Stagger does NOT get the bonus itself, only hits landing
    WHILE already Stagger'd do. The caller is responsible for calling
    target.check_stagger(...) AFTER this function returns and damage
    has actually been applied via take_damage, so that ordering holds.

## cogs/battle.py :: fire_evade_triggers
<a id="battle-fire-evade-triggers"></a>

Fires [On Evade] once per coin the defender actually evaded this
    hit (evade_count, from apply_incoming_hit above), swept across ALL
    of the defender's own known skills -- same passive-sweep pattern as
    fire_passive_triggers uses for [Combat Start]/[Turn Start], since
    the defender isn't the one whose skill is being resolved right now,
    they're reacting to someone else's attack, so there's no single
    "current skill" of theirs to check triggers against.

    caster on the context is the defender (the one who reacted), target
    is the attacker (so a condition like "if attacker has Rupture" or
    "if attacker has Fragile" reads naturally off the existing
    target_status condition type). Called once per evaded coin rather
    than once per hit, so a trigger like "[On Evade] Gain 2 Sanity"
    correctly stacks if a multi-coin skill gets partially evaded.

## cogs/battle.py :: find_eligible_counter
<a id="battle-find-eligible-counter"></a>

Counter is a reactive Skill-level mechanic, not a status: any
    skill flagged [Counter] that `defender` has DECLARED this round (in
    ANY of their slots -- it doesn't matter which one) is a standing
    threat against every incoming unopposed attack, all round, until it
    fires once. Eligible if defender hasn't already used their Counter
    this round (counter_used_this_round, reset every round in
    Fighter.clear_declaration) AND the declared Counter skill's OWN
    slot speed beats the attacker's slot speed. Returns the first
    eligible (slot, skill) found in declared_actions order, or None.

    Doesn't mark it used -- the caller (apply_counter_redirects) only
    commits that once it actually claims this specific incoming attack,
    since which attack claims a defender's single Counter charge
    depends on speed-priority resolution order across the whole round,
    not just this one comparison.

## cogs/battle.py :: find_eligible_clashable_counter
<a id="battle-find-eligible-clashable-counter"></a>

Same idea as find_eligible_counter, for [Clashable Counter]. Not
    speed-gated the way Counter is -- just needs to be declared this
    round and not yet used (clashable_counter_used_this_round).

## cogs/battle.py :: find_eligible_clashable_guard
<a id="battle-find-eligible-clashable-guard"></a>

Same shape as find_eligible_clashable_counter, for
    [Clashable Guard]. Not speed-gated, just needs to be declared this
    round and not yet used (clashable_guard_used_this_round).

## cogs/battle.py :: apply_counter_redirects
<a id="battle-apply-counter-redirects"></a>

Runs once, right after `units` is built and speed-sorted, BEFORE
    any resolution happens -- transforms the list to reflect Counter and
    Clashable Counter interceptions, so the main resolution loop further
    down never needs to know either mechanic exists; it just sees
    ordinary ('solo', entry) / ('clash', entry_a, entry_b) tuples,
    exactly the shape it already handles.

    Only ever touches 'solo' units -- a mutual Clash already means both
    sides are actively fighting back, so neither reactive mechanic
    applies there. Processes in the SAME speed order the main loop will
    use, since both mechanics are single-use per round and which
    incoming attack claims that single use depends on order.

    Pass 1 -- Counter: for every solo unit (attacker -> defender,
    would otherwise be unopposed), check find_eligible_counter against
    the defender. If eligible, this unit is entirely replaced: instead
    of the attacker's skill hitting the defender, the defender's OWN
    Counter skill now attacks the attacker back (skip_evasion=True on
    the eventual apply_incoming_hit call, since Counter explicitly
    bypasses the target's defenses) -- represented here as a normal
    'solo' unit with caster/target swapped and 'is_counter_retaliation'
    tagged on the entry so the main loop knows to skip evasion and use
    different flavor text. The attacker's original action never
    resolves at all: it was redirected, not merely blocked.

    Pass 2 -- Clashable Counter: for a fighter with one declared, if
    THEIR OWN action is (still, after pass 1) a solo unit -- i.e. its
    own declared target never clashed back -- it does NOT just resolve
    as a normal unopposed hit. Instead, scan every OTHER solo unit for
    one where someone else is unopposedly attacking any of this
    fighter's OTHER slots. If found, both units are consumed and
    replaced with a single real 'clash' unit between the Clashable
    Counter skill and that intercepted attacker's skill. If nothing to
    intercept, this fighter's own action simply doesn't resolve at all
    this round (it "does not activate" -- no consumption, no effect).

    Pass 3 -- Clashable Guard: identical shape to Pass 2 (same
    unopposed-own-action / scan-for-something-to-intercept / fizzle-if-
    nothing-found logic, own single-use flag), just tagged
    'is_clashable_guard_intercept' instead of
    'is_clashable_counter_intercept' -- the clash branch in combat()
    checks that tag (well, the skill's own .tags directly) to apply the
    Guard reward/mitigation instead of normal damage, see there.

## cogs/battle.py :: fire_passive_triggers
<a id="battle-fire-passive-triggers"></a>

The persistent Fighter-level buff store this engine was missing:
    fires [Combat Start] (once per battle) and [Turn Start] (every
    round) against EVERY skill a living fighter knows, not just
    whichever one they happened to declare this round. This is what
    lets a passive like "[Combat Start] Gain 3 Charge" sitting on a
    skill that never gets used still actually do something.

    There's no per-fighter "turn" separate from the shared round
    structure in this engine, so [Turn Start] is mapped onto "start of
    this round's Combat Phase" -- a documented simplification, not a
    real per-turn system.

    Each fighter's own skills are evaluated with target=None (there's
    no specific enemy at this moment), so any trigger whose condition
    actually depends on a target (target_status, speed_faster, ...)
    correctly never fires here -- see evaluate_condition's handling of
    a missing target in game/conditions.py. Only effect types that make
    sense with no live coin toss in progress actually do anything:
    sanity_gain and gain_status. inflict_status is skipped by
    apply_trigger_effects (no target to inflict onto); bonus_power/
    bonus_coin_power are evaluated but have nothing to apply to
    (there's no skill resolution in progress right now), so writing one
    against these timings is simply a no-op.

    Returns the combined log lines from every fighter, in fighter order.

## cogs/battle.py :: _resolve_pre_roll_chain
<a id="battle-resolve-pre-roll-chain"></a>

Chains resolve_triggers across every pre-roll timing in `timings`,
    in order, folding each stage's bonus_power/bonus_coin_power into the
    skill before the next stage evaluates against it (so e.g. a [Clash
    Start] Power buff is visible to [On Use]'s own condition check), and
    merging every stage's post-hit triggers into one list for the caller
    to apply once the hit actually lands.

    combat_start/turn_start are NOT in `timings` anymore -- they're
    fired once per fighter per round, across ALL of that fighter's
    known skills, by fire_passive_triggers above, before this function
    ever runs. This function only ever sees the skill actually declared
    this round, so it no longer needs a `battle` param to check
    battle.started against.

## cogs/battle.py :: apply_trigger_effects
<a id="battle-apply-trigger-effects"></a>

Applies the post-hit effects (inflict_status, sanity_gain,
    gain_status) from whichever triggers fired AND actually landed a
    hit. Pre-roll effects (bonus_power/bonus_coin_power) are already
    baked into the skill by resolve_triggers before this ever runs, so
    there's nothing to do for those here.

    target is optional: passive timings that don't reference an enemy
    (Combat Start, Turn Start) call this with target=None, since there's
    nobody to inflict a status onto at that moment -- an inflict_status
    trigger written against one of those timings is simply skipped
    rather than crashing (writing one there is a modeling mistake on
    the skill author's part, not something the engine can resolve).

    gain_status was previously parsed by parse_trigger_text but never
    actually applied anywhere -- a self-buff trigger (e.g. "[Combat
    Start] Gain 3 Charge") would silently do nothing. Fixed here: it
    lands on the CASTER's own statuses dict, same layering as
    inflict_status but with no resistance applied (Poise/Charge are
    self-buffs, not something an opponent resists -- see the note on
    INFLICTABLE_STATUSES in game/statuses.py).

## cogs/battle.py :: CombatLogView
<a id="battle-combatlogview"></a>

One button, "Full Log" -- anyone can click it to see the FULL
    breakdown of everything that happened this Combat Phase (every
    attrition round, every coin's face, every Trigger that fired,
    across every action), as an ephemeral reply to whoever clicked.
    Replaces the old per-action CombatRevealView (one button per
    action) with a single consolidated log, per the design decision to
    have one place to review the whole phase rather than action by
    action.

    Discord caps a single embed description at 4096 chars and a single
    message at 10 embeds -- with enough actions in one round the full
    log can genuinely blow past even that, so entries are packed into
    as many embeds as fit (up to 10) and anything beyond that is noted
    rather than silently dropped.

## cogs/battle.py :: BattleCog.combat.render
<a id="battle-battlecog-combat-render"></a>

Re-renders the ONE shared combat_message: every locked
            (finished) line, plus whatever the current unit's live
            animation looks like right now. Called constantly during
            animation -- this is genuinely a lot of message edits for a
            busy round (every coin face, every round, every unit), which
            is an intentional trade-off for the full-fidelity animation
            the user asked for over a faster but less spectacle-driven
            reveal.

## cogs/battle.py :: BattleCog.combat.animate_faces
<a id="battle-battlecog-combat-animate-faces"></a>

Phase one: reveals each coin's face one at a time, rolling
            icon first, mirroring a real Limbus clash flipping its coins
            in sequence. Returns the finished face row.

## cogs/battle.py :: BattleCog.combat.animate_power
<a id="battle-battlecog-combat-animate-power"></a>

Phase two for an ATTRITION ROUND toss: once every coin's
            face is showing, reveal each coin's running Power one at a
            time (this is a round toss, nobody actually takes damage
            yet -- only Power is being compared to decide who loses a
            coin this round).

## cogs/battle.py :: BattleCog.combat.animate_damage
<a id="battle-battlecog-combat-animate-damage"></a>

Phase two for the FINAL DECISIVE toss -- "once all the
            coins break, it goes through the animation again for the
            damage output one by one per coin". hit_log is the flat log
            apply_incoming_hit already produced for this exact hit
            (every line prefixed "Coin N:", covering resistance,
            Crit/Rupture/status/Counter notes, and dodges) -- this
            never recomputes anything, it just reveals those
            already-applied lines grouped by coin, one coin at a time.

## cogs/battle.py :: comment near line 23
<a id="battle-comment-23"></a>

Clashable Guard tuning knobs -- winning replaces normal clash damage
with raising the loser's Stagger thresholds by this many percentage
points per enabled tier (makes them easier to Stagger later, not an
instant Stagger); losing still takes the winner's damage, just cut
by this percent. Both easy to retune here if they feel off in play.

## cogs/battle.py :: comment near line 31
<a id="battle-comment-31"></a>

Offset: when a Clash forms between two skills that are BOTH tagged as
one of these defense-type tags, the Clash cancels outright -- no
resolve_round_clash, no damage, no Sanity change, no Triggers on
either side -- instead of computing a winner. Deliberately narrow
(exactly these four tags, not anything vaguely "defensive"), per
design decision: two units both trying to block/redirect the same
exchange simply fail to connect with each other, they don't fight.

## cogs/battle.py :: comment near line 40
<a id="battle-comment-40"></a>

Magnitude (potency * count) thresholds for the status-based half of a
fighter's Hint tier, checked highest first. Rupture then gets a flat
+1 on top of whatever tier its own magnitude lands on, since its
automatic on-hit trigger makes it a real threat even at low numbers,
not because it's inherently worse than the others at equal magnitude.

## cogs/battle.py :: comment near line 272
<a id="battle-comment-272"></a>

Discord silently rejects an ENTIRE modal with a 400 (which the caller
only ever sees as a generic "The application did not respond") if any
single TextInput's label is over 45 chars or placeholder is over 100
-- this bit us for real once (AddSkillModal's status_input label was
46 chars, triggers_input's placeholder was 119). This check runs once
at import time and fails loudly and immediately if it ever regresses,
instead of silently again at some future /battle addskill call.

## cogs/battle.py :: comment near line 867
<a id="battle-comment-867"></a>

Every pre-roll (pre-toss) skill-level timing, in firing order, for a
side that's about to enter a Clash. combat_start/turn_start used to
live in this list too, but only ever fired for whatever skill was
actually declared that round -- see fire_passive_triggers below,
which now covers ALL of a fighter's known skills instead and is
called once per fighter per round, before this per-entry chain ever
runs. Keeping them here as well would double-fire them for any skill
that happens to be both declared AND carries one of those tags.

[Before Attack] is deliberately NOT in this list. It used to be
bundled in here alongside [On Use], which meant it fired for BOTH
sides before attrition even started -- wrong, since the loser never
actually attacks. It's now evaluated inside resolve_round_clash
itself, for the winner only, immediately before their final decisive
toss (see that function's docstring in game/skills.py). [Clash Start]
stays here though: it's genuinely a "the clash begins" moment for
both sides, before ANY toss (attrition or final), which is exactly
what this pre-roll window represents.

## cogs/battle.py :: comment near line 887
<a id="battle-comment-887"></a>

Same idea for a side making an unopposed attack. [Before Attack]
stays here for the solo path -- there's no attrition to distinguish
it from, the single toss IS the attack, so firing it at the same
pre-roll moment as [On Use] is already correct.

## cogs/battle.py :: comment near line 1171
<a id="battle-comment-1171"></a>

Parsed and validated up front, before anything gets mutated --
same "all-or-nothing" principle as the rest of this command:
a bad resistance entry shouldn't leave hp/sanity/etc already
applied while resistances silently fail.

## cogs/battle.py :: comment near line 1345
<a id="battle-comment-1345"></a>

The ONLY response for this interaction -- everything about the
skill (name, stats, damage type, statuses, triggers) is now
collected in one popup instead of 6 required slash-command
options plus a second modal. See AddSkillModal above.

## cogs/battle.py :: comment near line 1523
<a id="battle-comment-1523"></a>

Deliberately no preview of the target's own skill here, ever --
you don't get to see what an enemy is bringing before you
commit, regardless of Speed. Whether this ends up a real Clash
is also genuinely unknown at declare time: it only becomes one
if the target's own action in target_slot targets this exact
(caster, slot) back (see combat()'s mutual-match logic). If it
doesn't, this resolves as an unopposed attack instead -- and
you won't know which until /battle combat actually runs.

## cogs/battle.py :: comment near line 1604
<a id="battle-comment-1604"></a>

This whole command can take a while now (animating every
attrition round coin-by-coin), so acknowledge the interaction
immediately and do everything else as followups -- the entire
animation lives inside ONE message that gets edited repeatedly
from here until the phase is done.

## cogs/battle.py :: comment near line 1611
<a id="battle-comment-1611"></a>

Fires [Combat Start] (first round of the battle only) and
[Turn Start] (every round) against every living fighter's
FULL skill list, not just whatever they declared -- see
fire_passive_triggers's docstring above. Deliberately happens
before any clash/unopposed resolution below, and before
battle.started flips to True at the end of this method.

## cogs/battle.py :: comment near line 1637
<a id="battle-comment-1637"></a>

An Unclashable OR (plain, non-Clashable) Guard skill on
EITHER side forces this to resolve unopposed, even if both
sides' target/target_slot would otherwise mutually match.
Guard doesn't fight in the traditional sense -- it always
resolves as its own standalone "solo" unit that converts
Final Power into Shield HP for the caster instead of
dealing damage (see the solo branch further down).
Clashable Guard is deliberately NOT excluded here -- if
its own declared target genuinely clashes back, that's
allowed to form a normal 'clash' unit (the clash branch
then special-cases the Clashable Guard reward instead of
normal damage, see there for how).

## cogs/battle.py :: comment near line 1671
<a id="battle-comment-1671"></a>

[Guard] must always resolve before anything else, regardless
of its own slot's Speed -- it's raising a defensive Shield,
not racing to land a hit, and a fast attacker beating a slow
Guard to the punch would mean the Shield never exists in time
to matter. A unit is treated as Guard-priority if EITHER side
carries the tag (a clash unit can only reach this point if
one side is [Clashable Guard], since plain [Unclashable]/
[Guard] already blocks normal pairing -- see the matching
loop above).

## cogs/battle.py :: comment near line 1690
<a id="battle-comment-1690"></a>

Transforms `units` to reflect Counter / Clashable Counter
interceptions -- see apply_counter_redirects's docstring
above. Must run AFTER sorting (it depends on speed-priority
order) and BEFORE any resolution below, since it can replace
or merge units entirely.

## cogs/battle.py :: comment near line 1697
<a id="battle-comment-1697"></a>

locked_lines holds everything PERMANENTLY decided so far this
Combat Phase: the passive-trigger block (if any), then one
one-line summary per unit as it finishes animating. It's
re-rendered on every single edit below alongside whatever
unit is currently mid-animation, so earlier results are never
lost while later ones are still resolving -- this is what
makes the whole phase feel like one continuous message rather
than N separate ones (the design choice made for this
rewrite: one message for the entire phase, not one per unit).

## cogs/battle.py :: comment near line 1732
<a id="battle-comment-1732"></a>

Coin-by-coin animation is pure PRESENTATION over results that
are already fully computed the instant resolve_skill/
resolve_round_clash/apply_incoming_hit run below -- same
principle the old "rolling" flavor message used, just carried
all the way through instead of stopping after one flourish.
Nothing here recomputes damage, crits, evasion, resistance,
or triggers; it only controls the PACING of revealing numbers
that already exist.

## cogs/battle.py :: comment near line 1802
<a id="battle-comment-1802"></a>

Full pre-roll chain for both sides -- see
PRE_ROLL_CLASH_TIMINGS / _resolve_pre_roll_chain above
for firing order and the documented [Clash Start] /
[Before Attack] scope collapse.

## cogs/battle.py :: comment near line 1839
<a id="battle-comment-1839"></a>

[Clash Win] and [Attack End] only fire for the winner --
the loser never actually lands a hit, so neither an
on-hit-family Trigger nor an "attack end" one makes
sense for them (resolve_triggers is only ever called
with the loser's own skill+context at "clash_lose").
[Turn End] is different: it fires for BOTH sides, since
it's about that fighter's own turn ending, not about
whether they landed a hit.

## cogs/battle.py :: comment near line 1853
<a id="battle-comment-1853"></a>

Clashable Guard replaces normal clash damage with its
own reward/mitigation, on EITHER side -- see
GUARD_STAGGER_THRESHOLD_RAISE / GUARD_LOSE_DAMAGE_REDUCTION_PCT
near the top of this function's module for the tuning
knobs. Winning with Clashable Guard deals NO damage at
all -- the reward is raising the loser's Stagger
thresholds (making them easier to Stagger later), not
a hit. Losing WITH a Clashable Guard skill still takes
the winner's damage, just reduced.

## cogs/battle.py :: comment near line 1904
<a id="battle-comment-1904"></a>

[On Evade] is the LOSER's own reaction -- they're the
one who just got hit by the winner's final toss, see
fire_evade_triggers for why this needs its own call
rather than folding into apply_trigger_effects.

## cogs/battle.py :: comment near line 2015
<a id="battle-comment-2015"></a>

Full pre-roll chain -- see PRE_ROLL_SOLO_TIMINGS /
_resolve_pre_roll_chain above. A Counter retaliation
still goes through this same chain, since it's the
defender's own skill resolving normally -- the only
thing special about it is skip_evasion below. Guard
goes through it too -- it's still a skill being used,
its coins still get tossed normally, only what happens
with the RESULT differs (Shield instead of damage).

## cogs/battle.py :: comment near line 2034
<a id="battle-comment-2034"></a>

Guard never deals damage -- its Final Power (the
Power reached after the last coin, same value a
Clash would compare) becomes Shield HP for the
CASTER instead. No apply_incoming_hit call at all:
there's no hit landing on anyone, target is just
whatever slot the player had to fill in to declare
it (declare() still requires one), functionally
vestigial here.

## cogs/battle.py :: comment near line 2075
<a id="battle-comment-2075"></a>

[On Evade] is the TARGET's own reaction to this
unopposed attack -- doesn't apply to a Counter
retaliation, which explicitly bypasses it
(skip_evasion=True already means evade_count is 0).

## cogs/battle.py :: comment near line 2154
<a id="battle-comment-2154"></a>

Whatever [Combat Start] triggers were going to fire this battle
already fired above (or didn't, if nobody had one declared) --
this flips permanently so they never fire again in later rounds
of the same battle.

## cogs/battle.py :: comment near line 2173
<a id="battle-comment-2173"></a>

Once the whole phase is done, post the updated battle status
as a FRESH message in the channel (not just a silent edit of
whatever the old tracked message was, which may be scrolled
far above the animation that just happened) -- and start
tracking THIS new message going forward, so future declares/
addfighter/etc. edit the freshest copy instead of an old one
buried above a wall of combat animation.

## game/battle.py :: DeclaredAction
<a id="battle-declaredaction"></a>

One skill+target pairing occupying one of a fighter's skill slots,
    explicitly aimed at one specific slot on the target.

    slot is the CASTER's own slot number (1-based) this action lives in.
    target_slot is which of the TARGET's slots this action is aimed at.
    A real Clash only happens if the target's own action in target_slot
    points back at (this caster, slot) -- see combat() in cogs/battle.py.

    target and target_slot are deliberately mutable (not frozen), since a
    speed-priority clash steal can silently redirect an already-declared
    action's target onto a different enemy after the fact, without the
    original caster knowing beforehand. See Battle.find_mutual_clash_partner
    and StealApprovalView in cogs/battle.py.

## game/battle.py :: Fighter.roll_slot_speeds
<a id="battle-fighter-roll-slot-speeds"></a>

Rerolls every skill slot's own Speed within [speed_min, speed_max].
        Called on creation and again at the start of every round -- slot
        speed is independent of whatever skill later gets assigned to it.

## game/battle.py :: Fighter.slot_speed
<a id="battle-fighter-slot-speed"></a>

1-based slot lookup. Returns 0 for an out-of-range slot rather
        than raising, since this gets called from places that only
        loosely validate slot numbers first.

## game/battle.py :: Fighter.take_damage
<a id="battle-fighter-take-damage"></a>

Consumes Shield first (1:1, no reduction of its own -- Shield
        isn't a resistance, it's a literal overhead HP pool), then
        spills whatever's left into regular HP. Returns
        (shield_absorbed, hp_damage) so the caller can log the split if
        it wants to (see combat() in cogs/battle.py).

## game/battle.py :: Fighter.check_stagger
<a id="battle-fighter-check-stagger"></a>

Checks current HP% against every ENABLED Stagger threshold and
        updates current_stagger_tier / stagger_clears_end_of_round if the
        deepest currently-qualifying tier is at least as deep as this
        fighter's existing tier (refreshing duration either way, even if
        the tier itself doesn't change -- taking more damage while
        already Stagger'd at that depth still resets the clock). Call
        this AFTER applying a hit's damage via take_damage, not before.

        Each tier's threshold is checked independently (hp_pct <=
        threshold), not as a sequential crossing -- so disabling Tier 2
        doesn't block Tier 3 from triggering on its own if HP drops low
        enough, it just means the milder Tier 2 penalty is skipped for
        HP in that in-between range (Tier 1 still applies there instead,
        since its own threshold is still satisfied).

        Returns the tier now active (0 if none, unchanged from before if
        nothing new qualifies).

## game/battle.py :: Fighter.clear_expired_stagger
<a id="battle-fighter-clear-expired-stagger"></a>

Called once, at the end of a round's combat() resolution
        (right before battle.round_number increments), for every living
        fighter. Clears this fighter's Stagger if its stored expiry
        round has been reached.

## game/battle.py :: Fighter.declare_in_slot
<a id="battle-fighter-declare-in-slot"></a>

Fills (or overwrites/moves) one specific skill slot.

        Returns False only if the slot number itself is out of range.
        Unlike the old declare(), this deliberately allows re-declaring
        an already-filled slot -- that's how "move a skill to a
        different slot" and "swap which skill is in this slot" both
        work: undeclare the old one (or just overwrite it here) and
        declare_in_slot the new one.

        extra_target_slots is only ever non-empty for an [Attack Weight]
        skill (validated by the caller, /battle declare in
        cogs/battle.py, before this is ever called) -- see
        DeclaredAction.extra_target_slots for what it actually does.

## game/battle.py :: Battle.find_mutual_clash_partner
<a id="battle-battle-find-mutual-clash-partner"></a>

Checks whether target's target_slot is currently locked in a
        real mutual clash with some other fighter: that fighter's own
        declared action targets exactly target's target_slot, AND
        target's action in target_slot targets exactly that fighter's
        slot back.

        Returns (fighter, slot) of that clash partner, or None if
        target_slot isn't currently in a genuine mutual clash (nothing
        declared there yet, or only a one-sided declare so far).

        Used to detect a clash-steal situation: if a third fighter also
        wants target's target_slot, and this returns a partner on that
        third fighter's own side, the partner is the ally who'd have to
        approve giving up the clash. See StealApprovalView in
        cogs/battle.py.

## game/battle.py :: comment near line 10
<a id="battle-comment-10"></a>

Clash win is +2 SP PER COIN in the winner's winning skill (variable,
not flat -- see SANITY_PER_COIN_CLASH_WIN below, applied against
outcome.winner_final_result.skill.coins by the caller in
cogs/battle.py). Clash loss and the unopposed-Heads bonus stay flat.

## game/battle.py :: comment near line 20
<a id="battle-comment-20"></a>

Default Stagger thresholds as HP% (Tier 1 = mildest/first crossed as
HP drops, Tier 3 = harshest/last crossed), and the incoming-damage
multiplier each tier applies WHILE active. Both are per-character
customizable (Fighter.stagger_thresholds / stagger_tiers_enabled),
these are just the defaults a Fighter starts with. See
Fighter.check_stagger below for the actual detection/duration logic,
and STAGGER_MULTIPLIERS' use in apply_incoming_hit (cogs/battle.py)
for where the multiplier actually gets applied.

## game/battle.py :: comment near line 50
<a id="battle-comment-50"></a>

Additional target-side slots this same action also "reaches",
beyond the primary target_slot -- only ever populated for a skill
tagged [Attack Weight] (see SKILL_FLAG_TAGS in game/conditions.py).
This is still ONE action with ONE coin toss and ONE damage result
(see apply_incoming_hit / resolve_round_clash, neither of which
loop per reached slot) -- extra_target_slots exists purely so the
Clash-matching loop in combat() (cogs/battle.py) can also pair
this action against a defender declared in any of these slots,
not just target_slot. It does NOT multiply damage: whichever
single reached slot actually forms the real Clash (target_slot
takes priority if eligible, see the matching loop) is the whole
story -- winning that Clash cancels the entire attack, and if
nothing clashes it, it resolves as one ordinary unopposed hit
exactly like a non-Attack-Weight action, since damage already
lands on the target Fighter's whole HP/Shield pool, never per-slot.

## game/battle.py :: comment near line 81
<a id="battle-comment-81"></a>

Discord user ID of whoever controls this fighter. Set from
Character.owner_id for saved characters, or from whoever ran
/battle addfighter for a one-off NPC. Used to DM this fighter's
controller for things they need to privately approve, like a
clash-steal request, since ephemeral replies only reach whoever
is actually running the current slash command.

## game/battle.py :: comment near line 89
<a id="battle-comment-89"></a>

The range each of this fighter's skill slots rolls its own Speed
from, once per round (e.g. 4-7). Defaults to a constant range equal
to `speed`, so a fighter with no explicit range set just behaves
like every slot has the same fixed speed, matching the old
single-speed behavior. Real per-character ranges (set via
/character edit) are pulled in by from_character below.

## game/battle.py :: comment near line 98
<a id="battle-comment-98"></a>

One rolled speed per skill slot, independent of whatever skill (if
any) ends up assigned to that slot. Rolled fresh at Fighter creation
and at the start of every round. This is what determines Clash/
unopposed resolution order now, not the flat `speed` stat above.

## game/battle.py :: comment near line 113
<a id="battle-comment-113"></a>

Round-scoped single-use flags for the two Counter-family Skill
flags ([Counter] and [Clashable Counter], see SKILL_FLAG_TAGS in
game/conditions.py). Both reset every round in clear_declaration
below. See find_eligible_counter / find_eligible_clashable_counter
/ apply_counter_redirects in cogs/battle.py for how these get set.

## game/battle.py :: comment near line 122
<a id="battle-comment-122"></a>

Stagger. stagger_thresholds is 3 HP% values (Tier 1/2/3, in that
order -- descending, Tier 1 is the highest/mildest/first crossed).
stagger_tiers_enabled marks which of the 3 actually exist for this
character -- Tier 1 (index 0) should never be set False (nothing
enforces that here, it's a character-creation rule, see the
Stagger tier trade-off in the README). current_stagger_tier is 0
when not Stagger'd, else 1/2/3. stagger_clears_end_of_round is the
LAST battle.round_number this Stagger is still active for --
cleared once that round's combat() finishes, see the clearing
logic in combat() itself.

## game/battle.py :: comment near line 137
<a id="battle-comment-137"></a>

Shield HP from a [Guard] skill: an overhead pool consumed BEFORE
regular HP, not a resistance or a damage-avoidance mechanic --
incoming damage is computed exactly the same way whether or not
Shield exists, take_damage below just drains this first. Clears
every round in clear_declaration (Guard doesn't carry over -- a
fresh Guard each round is the only way to keep it topped up).

## game/battle.py :: comment near line 166
<a id="battle-comment-166"></a>

If the saved character has its own speed range (set via
/character edit), it takes over from the flat speed default
that __post_init__ already applied above, and slots are
rerolled against the real range. /battle addfighter's own
speed_min/speed_max params still override this afterward if
the host passes them, since that runs after this returns.

## game/conditions.py :: Condition
<a id="conditions-condition"></a>

One check against battle state. type is one of CONDITION_TYPES.

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

## game/conditions.py :: TriggerContext
<a id="conditions-triggercontext"></a>

Everything a Condition might need.

    caster_slot_speed/target_slot_speed are the rolled Speed of the
    specific slots involved in this resolution, not a fighter-wide
    value, since each slot rolls its own Speed independently. The
    caller (combat() in cogs/battle.py) is responsible for passing the
    right slot's value in here, this module has no notion of slots.

    target can be None for triggers that never reference one
    (caster_sanity, caster_status, first_hit_of_round, most Combat/Turn
    Start/End triggers) -- any target-dependent condition just
    evaluates False rather than raising if target is missing.

## game/conditions.py :: evaluate_condition
<a id="conditions-evaluate-condition"></a>

Pure evaluation, no side effects. Safe to call speculatively (e.g.
    for Hint display) against a target the caster hasn't actually
    declared on yet.

## game/conditions.py :: Trigger
<a id="conditions-trigger"></a>

A Condition plus what happens if it's true, plus when to check it.

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

## game/conditions.py :: TriggerParseError
<a id="conditions-triggerparseerror"></a>

Raised on a malformed trigger line. Carries the offending line so
    the caller can show exactly which one failed, instead of a generic
    error against the whole block.

## game/conditions.py :: _parse_condition_and_effect_text
<a id="conditions-parse-condition-and-effect-text"></a>

Splits a line's remaining text (after the bracket tag) into a
    Condition and whatever's left over to hand to the effect parser.
    Returns Condition(type='always') with the full text unchanged if no
    condition phrase is recognized, since plenty of valid lines are pure
    effects with no gate beyond their timing tag.

## game/conditions.py :: _parse_effect_text
<a id="conditions-parse-effect-text"></a>

Parses the effect half of a line. Returns
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

## game/conditions.py :: parse_trigger_text
<a id="conditions-parse-trigger-text"></a>

Parses a full multi-line trigger block, as pasted into the trigger
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

## game/conditions.py :: comment near line 36
<a id="conditions-comment-36"></a>

[On Evade] and [Before Getting Hit] both live here, not in
PER_COIN_TIMINGS, even though they're each about a single
incoming coin. Both are evaluated as a passive sweep across ALL
of the DEFENDER's own known skills (same pattern fire_passive_
triggers uses for [Combat Start]/[Turn Start]), since the
defender isn't the one whose skill is being resolved when they
get attacked, so there's no coin_index of theirs to attach either
to. See the Evasion-resource docstring on resolve_skill in
game/skills.py, and the Counter-resource docstring on
apply_incoming_hit in cogs/battle.py, for the actual mechanics
these fire off of. caster on the context is the defender (the one
reacting), target is the attacker, so a condition like "if
attacker has Rupture" still reads naturally off target_status.

## game/conditions.py :: comment near line 51
<a id="conditions-comment-51"></a>

Fires on the ATTACKER's own skill, right after this hit's damage
is applied, if the TARGET is now Stagger'd -- either freshly
triggered by this exact hit, or already Stagger'd from an earlier
hit and still qualifying. Evaluated with caster=attacker,
target=defender, same as every other skill-level timing (not a
defender-side passive sweep like on_evade/before_getting_hit
above, since this is genuinely about the ATTACKING skill's own
resolution). See Fighter.check_stagger in game/battle.py and
STAGGER_MULTIPLIERS in cogs/battle.py for the actual mechanic.

## game/conditions.py :: comment near line 74
<a id="conditions-comment-74"></a>

A coin Crits if the caster is currently holding Poise (see the
Poise-break rule documented on resolve_skill in game/skills.py) --
independent of that coin's own face, which is why [On Crit -
Heads Hit]/[On Crit - Tails Hit] exist as their own sub-timings
rather than being implied by [Heads Hit]/[Tails Hit].

## game/conditions.py :: comment near line 84
<a id="conditions-comment-84"></a>

Recognized by name, but nothing in the engine backs them yet. Parsed so
the error can name the specific tag and the missing system, instead of
a generic "unknown tag". Empty for now -- Counter and Evade (the last
two items that lived here) are both built now; kept as a dict, not
removed entirely, so a future unbuilt timing has somewhere to go.

## game/conditions.py :: comment near line 105
<a id="conditions-comment-105"></a>

Gates whether /battle declare will accept extra target slots for
this skill (see DeclaredAction.extra_target_slots in
game/battle.py, and the Clash-matching loop in combat(),
cogs/battle.py). A skill without this tag can only ever be aimed
at a single target_slot, same as before this existed.

## game/conditions.py :: comment near line 113
<a id="conditions-comment-113"></a>

The only statuses a caster can hold on themselves as a self-buff
resource right now. "Gain N <Status>" and "At N+ <Status>" (when the
thing named isn't Speed) both check against this list, anything else
is an unmodeled custom resource (Strider, Assist Defense, Deathrite,
named Identity resources, etc) and gets rejected with a clear message,
matching this pass's scope. Evasion works like Poise (a count-based
stack consumed one-per-coin), read off the DEFENDER instead of the
attacker -- see the Evasion-resource docstring on resolve_skill in
game/skills.py.

NOTE: "counter" is deliberately NOT in this list anymore -- Counter
used to be a status/resource here, but it's now a Skill-level
mechanic instead (the [Counter] skill flag, see SKILL_FLAG_TAGS
above, plus find_eligible_counter/apply_counter_redirects in
cogs/battle.py). A skill, not a stat a caster holds.

## game/conditions.py :: comment near line 226
<a id="conditions-comment-226"></a>

Strips exactly one trailing parenthetical "annotation" off an effect
phrase before matching it against the flat-effect patterns below, so
notes like "(once per turn)" or "(max 2)" don't block an otherwise
well-formed line. It is NOT applied before the ';' chained-effect
check further down in _parse_effect_text, so a real formula clause
living inside that same parenthetical (e.g. "for every 2 Speed
difference; max 2") still correctly fails to parse instead of being
silently accepted as something else.

## game/skills.py :: flip_coin
<a id="skills-flip-coin"></a>

Rolls a percentage-based coin flip. heads_chance is 0-100, the
    percent chance of landing Heads. Defaults to a fair 50/50 so every
    existing caller that doesn't pass this stays behaves exactly as
    before.

## game/skills.py :: resolve_skill
<a id="skills-resolve-skill"></a>

Resolves a skill's coins one at a time, in sequence.

    Every coin lands a hit at whatever Power has been built up so far.
    A Heads permanently raises Power (by coin_power) for every hit after it,
    including its own. A Tails deals a hit too, just without raising Power.

    heads_chance is this skill's OWN caster's Sanity-driven odds (see
    Fighter.heads_chance in game/battle.py), defaulting to a fair 50/50.

    context is optional (attrition rounds inside resolve_round_clash pass
    None, since those tosses never actually deal damage and their
    per-coin triggers would never be applied anyway). When provided,
    each coin now fires TWO passes of its own per-coin Triggers (matched
    by coin_index):

      1. [Coin Start], BEFORE this coin is tossed. Its bonus_power/
         bonus_coin_power effects are applied immediately -- bonus_power
         adds straight onto the running Power for this coin (and every
         coin after it, same as a Heads would), bonus_coin_power raises
         coin_power itself from this coin onward. Any inflict_status/
         sanity_gain effect on a [Coin Start] trigger is collected into
         that coin's fired_triggers same as the hit-timings below (a
         self-buff on use of this specific coin, not gated on it
         actually landing since it hasn't tossed yet -- but every coin
         in this engine deals a hit regardless, so this is a moot
         distinction in practice).

      2. AFTER the toss: [On Hit] (always), [Heads Hit]/[Tails Hit]
         (gated on that face), [Current Coin Attack End] (always),
         [Heads Attack End]/[Tails Attack End] (gated on that face),
         plus whatever's in extra_coin_timings -- currently only
         [Hit After Clash Win], passed by resolve_round_clash ONLY for
         the winner's final decisive toss, never for attrition rounds
         or a solo unopposed attack.

    Matching, condition-true triggers land in that CoinResult.fired_
    triggers for the caller (apply_incoming_hit in cogs/battle.py) to
    apply. Evaluating here (not later) matters because a trigger's
    condition might depend on state a later coin's own effect changes
    (e.g. a status this same skill just inflicted).

    Poise-break / Crit rule: if context is given and context.caster
    currently holds Poise (a StatusInstance with count > 0 -- see
    SELF_BUFF_STATUSES in game/conditions.py), each coin in this
    resolution checks that stack IN ORDER: as long as count remains,
    that coin Crits, consuming exactly 1 count and dealing bonus damage
    equal to Poise's potency (read fresh at the start of this call, not
    re-read from the Fighter mid-resolution, since skills.py never
    mutates a Fighter -- the real consumption happens in
    apply_incoming_hit, which trusts these per-coin is_crit flags to
    know how many counts to actually decay off the caster afterward).
    A Crit is independent of that coin's own face -- [On Crit] fires on
    ANY crit, [On Crit - Heads Hit]/[On Crit - Tails Hit] additionally
    gate on which face it landed on, mirroring how [Heads Hit]/[Tails
    Hit] relate to [On Hit].

    Evasion rule: mirrors Poise-break exactly, but read off the
    DEFENDER (context.target) instead of the attacker, since it's a
    reaction to being hit, not something the attacker's own skill does.
    If context.target currently holds Evasion (count > 0), each coin in
    this resolution checks that stack IN ORDER: as long as count
    remains, that coin is dodged, consuming exactly 1 count. An evaded
    coin's damage_dealt is 0 and it never Crits and never fires its own
    on_hit-family triggers -- nothing actually landed, so none of that
    makes sense. Power still climbs normally on a Heads regardless
    (Evasion blocks damage, it doesn't change who wins a Clash). [Coin
    Start] still fires even on a coin that ends up evaded, since that's
    a pre-toss self-buff on the ATTACKER's own skill, decided before
    evasion is even checked. [On Evade] itself is NOT fired from in
    here -- see SKILL_LEVEL_TIMINGS["on evade"] in game/conditions.py
    and fire_evade_triggers in cogs/battle.py, since it's swept across
    the DEFENDER's own known skills, not tied to this skill's coins at
    all. The real Evasion stack is consumed by the caller
    (apply_incoming_hit), same deferred-mutation pattern as Poise.

## game/skills.py :: resolve_triggers
<a id="skills-resolve-triggers"></a>

Evaluates every skill-level trigger matching `timing` against the
    current context. Per-coin timings (on_hit/heads_hit/tails_hit/etc.)
    are never handled here -- those only ever fire from inside
    resolve_skill, one coin at a time, since they need to know that
    coin's own heads/tails result.

    Pre-roll effects (bonus_power, bonus_coin_power) are baked into a
    modified copy of the skill immediately, since they have to apply
    before coins are ever tossed -- this is why it returns a (possibly
    new) Skill rather than mutating in place. Post-hit effects
    (inflict_status, sanity_gain) are NOT applied here, they're only
    evaluated and returned as "fired", so the caller can apply them
    alongside normal hit resolution (see apply_trigger_effects in
    cogs/battle.py) only if the skill actually lands -- a losing side of
    a clash never hits, so its post-hit triggers should never fire even
    though its pre-roll bonuses still legitimately affected the clash
    math.

## game/skills.py :: resolve_clash
<a id="skills-resolve-clash"></a>

Compares two SkillResults by final_power.

    Returns the winning SkillResult (whose total_damage should be applied to
    the loser), or None on a tie. You decide how ties get handled at the
    call site, since that is a rules decision, not a math one.

    Superseded by resolve_round_clash below for actual gameplay, kept
    here since it's simple and still useful for quick comparisons.

## game/skills.py :: ClashRound
<a id="skills-clashround"></a>

One round of clash attrition: both sides tossed all their currently
    remaining coins fresh, and whichever side's final Power was lower
    loses exactly one coin (permanently) heading into the next round.

## game/skills.py :: ClashOutcome
<a id="skills-clashoutcome"></a>

The full outcome of a round-based attrition clash: every attrition
    round that happened, plus the winner's final one-sided damage toss
    (a completely fresh roll using whatever coins they had left).

## game/skills.py :: resolve_round_clash
<a id="skills-resolve-round-clash"></a>

Resolves a clash via round-by-round coin attrition.

    Each round, both sides toss ALL of their currently-remaining coins
    fresh (not carrying over previous rolls), building Power the normal
    sequential way. Whichever side's final Power is lower this round
    permanently loses one coin. A tie means neither side loses a coin,
    but the round still counts, both sides simply re-toss again next
    round with unchanged coin counts.

    This repeats until one side's coin count hits zero, at which point
    the OTHER side wins the clash outright. The winner then makes one
    final, completely fresh toss using whatever coins they have left,
    exactly like an unopposed attack, and that toss is what actually
    deals damage. The attrition rounds themselves never deal damage,
    they only decide who wins and how many coins the winner has left.

    The winner's per-coin status lists are sliced down to match however
    many coins survived, keeping the FIRST N entries (front-loading
    status onto early coin slots is a real strategic choice, since those
    are the ones most likely to make it through attrition intact).

    heads_chance_a/heads_chance_b are each side's OWN Sanity-driven odds
    (Fighter.heads_chance()), applied to every toss on that side, both
    during attrition and on the final damage toss.

    context_a/context_b are each side's TriggerContext, passed through
    to resolve_skill so per-coin triggers can be evaluated. They're
    threaded through the attrition-round tosses too (harmless, those
    tosses never actually apply damage or fired_triggers), and the
    winner's own context is the one carried into their final decisive
    toss, since that's the only toss whose fired_triggers actually
    matter to the caller.

    [Before Attack] is evaluated right here, against the winner only,
    immediately before their final toss -- NOT bundled into whatever
    pre-roll chain the caller ran before calling this function at all
    (see PRE_ROLL_CLASH_TIMINGS in cogs/battle.py, which now only
    covers combat_start/turn_start/before_use/on_use/clash_start).
    That's the real distinction between [Clash Start] (both sides, the
    moment the clash begins, before even the attrition rounds) and
    [Before Attack] (winner only, the moment right before the hit that
    actually deals damage) -- they used to collapse into the same
    pre-toss window, which meant a loser's [Before Attack] trigger
    could fire even though they never land a hit. Now it can't: the
    loser's skill/context are never touched here.

    max_rounds is a safety valve against a true infinite loop (a tie
    every single round forever); hitting it is astronomically unlikely
    with real coin randomness.

## game/skills.py :: comment near line 17
<a id="skills-comment-17"></a>

Per-coin status effects, one entry per coin (aligned by index).
coin_statuses[i] is None if that coin inflicts nothing. Potency/
count are the RAW values before resistance, resistance gets applied
by the caller (apply_incoming_hit in cogs/battle.py) at hit time.
Status names are not restricted to a fixed list, this same shape
covers keyword statuses (Rupture, Bleed, ...) and non-keyword ones
(Fragile, Bind, Power Down, ...) equally.

## game/skills.py :: comment near line 28
<a id="skills-comment-28"></a>

Skill-level Conditional Triggers, independent of the per-coin
status system above. Evaluated once per resolution via
resolve_triggers below, not per coin, since a trigger's condition
(target's HP, caster's Sanity, etc.) doesn't change coin to coin
within a single resolution.

## game/skills.py :: comment near line 35
<a id="skills-comment-35"></a>

Skill-level metadata flags parsed alongside triggers (target_fixed,
unclashable, indiscriminate, clashable_counter). These aren't
Conditional Triggers themselves -- no condition, no effect, no
timing -- just static properties of the skill that other systems
(declare/clash targeting, etc.) check directly off the Skill.

## game/skills.py :: comment near line 62
<a id="skills-comment-62"></a>

Per-coin Triggers (on_hit / heads_hit / tails_hit, matched by
coin_index) that fired on this specific coin, already filtered to
ones whose condition evaluated true. The caller (apply_incoming_hit
in cogs/battle.py) is responsible for actually applying their
effects, same as skill-level post_hit triggers from resolve_triggers.

## game/skills.py :: comment near line 69
<a id="skills-comment-69"></a>

True if this coin Crit -- the caster held Poise (count > 0) at the
moment this coin resolved, independent of Heads/Tails (see the
Poise-break rule on resolve_skill below). crit_bonus_damage is the
extra damage that Crit deals (equal to the caster's Poise potency
at that moment), computed here but NOT yet added into damage_dealt
above -- same pattern as Rupture's bonus damage, which also isn't
baked into a coin's own damage_dealt, it's added by the caller
(apply_incoming_hit in cogs/battle.py) at hit-application time,
since that's also where the caster's real Poise stack actually
gets consumed (skills.py never mutates a Fighter).

## game/skills.py :: comment near line 82
<a id="skills-comment-82"></a>

True if the DEFENDER dodged this coin -- see the Evasion-resource
rule on resolve_skill below. damage_dealt above is already 0 for
an evaded coin (Power still climbed normally, only the hit itself
was negated), and this coin never crits and never fires its
on_hit-family triggers, since nothing actually landed. The real
Evasion stack is consumed by the caller (apply_incoming_hit in
cogs/battle.py), same deferred-mutation pattern as is_crit/Poise
above -- skills.py never touches a Fighter directly.

## game/skills.py :: comment near line 350
<a id="skills-comment-350"></a>

[Before Attack] triggers that fired for the winner specifically,
right before their final decisive toss (see resolve_round_clash
below for why this is separate from the general pre-roll chain
that runs before attrition even starts). Only inflict_status/
sanity_gain effects show up here -- bonus_power/bonus_coin_power
are already baked into winner_final_result by the time this is
populated, same pattern as every other post_hit list in this
engine. The caller (cogs/battle.py combat()) applies these
alongside clash_win/attack_end.

## game/skills.py :: comment near line 415
<a id="skills-comment-415"></a>

[Before Attack] fires HERE, for the winner only, right before
their one real damage-dealing toss -- see the docstring above for
why this is deliberately separate from whatever pre-roll chain
the caller already ran on skill_a/skill_b before this function
was even called. bonus_power/bonus_coin_power get baked into
winner_skill immediately via resolve_triggers (affecting only
this final toss, never the attrition rounds already resolved
above), inflict_status/sanity_gain triggers are collected for the
caller to apply.

## game/skills.py :: comment near line 435
<a id="skills-comment-435"></a>

hit_after_clash_win only ever applies to THIS toss -- the winner's
one real damage-dealing toss -- never to the attrition rounds
above (those pass no extra_coin_timings, matching their plain
resolve_skill calls) and never to a solo unopposed attack (that
path in cogs/battle.py calls resolve_skill directly with no
extra_coin_timings either).

## game/statuses.py :: StatusInstance
<a id="statuses-statusinstance"></a>

One status effect currently active on a fighter.

    Potency = damage/magnitude dealt each time it triggers.
    Count = how many triggers remain before it's fully gone.

## game/statuses.py :: decay_after_trigger
<a id="statuses-decay-after-trigger"></a>

Called when a status's trigger condition fires (e.g. Rupture
    triggers when its owner is hit). The caller is responsible for
    actually dealing current.potency as damage; this function only
    handles what happens to the stack afterward.

    Decrements Count by 1. If Count reaches 0, the stack is fully
    consumed: Potency resets to 0 too, rather than lingering at 0 count
    with stale Potency.

## game/statuses.py :: apply_status
<a id="statuses-apply-status"></a>

Layers a new application on top of whatever is currently there.

    If this application coincides with a trigger (e.g. a hit that both
    triggers existing Rupture AND inflicts new Rupture at once), call
    decay_after_trigger() FIRST and pass its result in as `current`, so
    the new stack layers on top of the already-decayed one, not the
    stale pre-trigger one.

    A stack can never have Count > 0 with Potency <= 0 (a status with
    nothing to deal doesn't make sense), so if the combined Potency would
    be zero or less (e.g. a "no potency, +N count" effect applied to an
    empty stack), it floors to 1.

## game/statuses.py :: comment near line 3
<a id="statuses-comment-3"></a>

The 5 target-facing statuses that can be inflicted on an opponent and
resisted. Poise and Charge are deliberately excluded: they're self-buff
resources a fighter builds for themselves, not something dealt TO a
target, so "resisting" them doesn't make sense.

## game/resistances.py :: apply_resistance
<a id="resistances-apply-resistance"></a>

Reduces a value by a resistance percentage, using Limbus
    Company's own asymmetric formula instead of a flat linear
    reduction: a WEAKNESS (resistance_pct negative, meaning MORE damage
    taken) applies at full face value, but an actual RESISTANCE
    (resistance_pct positive) is only HALF as effective as its face
    value suggests. This matches the real game's philosophy that being
    weak to something hurts fully, but resisting something only helps
    partially -- resistance and weakness are not mirror images of each
    other.

    Concretely: -50 resistance_pct (50% weakness) still means 1.5x
    damage, same as a naive linear formula would give. But +50
    resistance_pct (nominally "50% resistance") only actually reduces
    damage by 25%, not 50% -- it now takes +200 resistance_pct to reach
    a full 100% reduction (0 damage), not +100. So once resistance_pct
    is positive, it's no longer literally "the percent damage reduced";
    it's better read as "how much resistance is being applied," with
    the real reduction being half that.

    resistance_pct still isn't clamped going in on either side. The
    result is still floored at 0, since resistance should never turn
    damage into healing.
