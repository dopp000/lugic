# Lugic

A Discord bot for running Limbus Company–style coin-flip combat, built around a
custom ruleset. Owner-operated project, developed in a GitHub Codespace.

> **Notes to self, kept here so I don't have to re-figure things out from
> scratch every time I come back to this.** `git log --oneline -15` plus this
> file is usually enough to remember where things stand. When something
> structural changes (a new command, a new mechanic, a bug whose fix isn't
> obvious from the diff alone) worth jotting it down here while it's fresh,
> since by next session I've usually forgotten the "why."

## Combat Features

- **Coin-based skill resolution**: sequential coin tosses. Heads permanently
  raises Power for every hit after it (including its own); Tails hits at
  current Power.
- **Sanity-driven coin odds** (`heads_chance = 50 + sanity`). Clash win/loss
  and unopposed-hit both feed the Sanity economy; Sanity drifts toward 0 each
  round.
- **Round-based Clash attrition**: both sides re-toss all remaining coins each
  round; lower Power loses one coin permanently; repeats until one side hits
  zero coins. Winner makes one fresh final toss, the only toss that deals
  damage.
- **No scouting**: `/battle declare` never reveals what's incoming. Whether an
  action becomes a real Clash is only decided at `/battle combat` time, and
  only if both sides' declared actions target each other's exact slot back.
  Otherwise it resolves unopposed.
- **Clash-steal**: a faster ally can take over an existing ally-vs-enemy clash
  slot, with DM approval from the displaced ally.
- **Conditional Triggers**: bracket-tag natural-language syntax (e.g.
  `[On Use] If this unit's Sanity is 45+, Coin Power +1`), parsed by
  `game/conditions.py`, entered via the `/battle addskill` popup. See
  **Trigger Syntax** below. Most worth re-reading before touching
  `game/conditions.py`, `game/skills.py`, or the trigger-dispatch chain in
  `cogs/battle.py`'s `combat()`.
- **Hint Tier**: every Trigger carries a danger rating, 1 (mild) to 3
  (highly dangerous). Can be hand-set via a trailing `(Hint N)` on the
  line, which always wins when present; left off, a Trigger that inflicts
  a status gets its tier worked out automatically from its own
  potency \* count, the same scale an already-active status of that size
  would be judged by (`magnitude_to_hint_tier`, `game/statuses.py`) --
  so nobody has to remember to manually flag a dangerous passive, and
  an inflict-type effect never silently defaults to looking mild. Any
  other effect type (bonus power, Sanity gain, a self-buff) still
  defaults to 1. Shown as a small icon next to each Trigger
  in a skill listing (`/battle skills`, `/character skills`), and as a
  single worst-of-the-fighter icon under their name on the battle embed
  (`compute_hint_tier`, `cogs/battle.py`). A tier 2+ Trigger that actually
  fires also gets called out in the live fight log itself: bolded,
  capitalized, and wrapped in that tier's icon on both sides
  (`_warn_wrap`), so a genuinely dangerous passive stands out from routine
  tier-1 ticks instead of reading the same as them. Tier 1 lines are left
  as plain text.
- **Poise-break Crit**: holding Poise crits coins for bonus damage, consuming
  one Poise count per crit until it runs out.
- **Evade**: a declared Skill via `/battle declare`, not a self-buff resource
  anymore (rebuilt from the ground up; see **Debugging Notes** for why).
  `[Evade]` is fixed to exactly 1 coin, enforced at `/battle addskill` time
  alongside Guard/Clashable Guard. Strictly slot-specific: only reacts to
  the exact `(attacker, attacker_slot)` it was declared against, no speed
  comparison, never steals or gets stolen from. `find_eligible_evade` checks
  eligibility (declared, untapped this round via `Fighter.evade_used_slots`,
  target matches exactly); `resolve_evade` rolls the ONE deciding coin
  against the defender's own `heads_chance()`, called *before*
  `apply_incoming_hit` at every hit-resolution call site (solo, Clash-loser,
  and Attack Weight splash). Whole-hit dodge for this pass, not per-coin: a
  win fully negates the incoming hit, a loss dodges nothing. Marks its slot
  used in `evade_used_slots` on every roll regardless of outcome; reset each
  round via `Fighter.clear_declaration`. `[On Evade]` fires via
  `fire_evade_triggers` only on an actual successful dodge.
- **Counter / Clashable Counter**: two Skill-flag mechanics, not a
  status/resource. `[Counter]`: reactive, not slot-specific, fires against
  any incoming unopposed attack on the holder if the Counter skill's own
  slot speed beats the attacker's, fully redirects the attack and strikes
  back, bypassing the attacker's Evade (`skip_evade` on `resolve_evade`).
  Single-use per round (`counter_used_this_round`). Still resolved by
  `apply_counter_redirects`, which now handles **only** plain Counter (its
  one remaining pass). `[Clashable Counter]`: no longer decided by that
  same pre-pass. It's checked live, exact-target-matched against the
  mutual-pairing block, before `apply_counter_redirects` even runs (see
  **Pass 0**, next bullet) — an unmatched Clashable Counter idles safely
  (no damage, doesn't mark itself used) rather than acting as a live
  attack. Both reset via `Fighter.clear_declaration`.
- **Guard / Shield / Clashable Guard**: third defense-skill type alongside
  Evade/Counter. `[Guard]`: never enters a Clash even if mutually targeted;
  always self-resolves, converting Final Power into Shield HP for the caster.
  Shield drains before HP (1:1, no reduction) and clears every round, with
  no persistence. `[Clashable Guard]`: same live, exact-target Pass 0
  interception as Clashable Counter (see below); its clash outcome replaces
  damage entirely: winning raises the loser's enabled Stagger thresholds by
  `GUARD_STAGGER_THRESHOLD_RAISE` (10pp/tier); losing checks the loser's own
  Evade first (a Clash loser's hit isn't exempt from Evade), and only if
  that fails still takes the winner's damage, cut by
  `GUARD_LOSE_DAMAGE_REDUCTION_PCT` (25%). Both placeholder defaults.
- **Pass 0 — exact-target Clashable interception** (`cogs/battle.py`,
  `combat()`, runs before `apply_counter_redirects`): matches a declared
  `[Clashable Guard]`/`[Clashable Counter]` against the *exact*
  `(target, target_slot)` it declared, mirroring the same target/slot
  matching the ordinary mutual-pairing block already uses. Replaced two
  confirmed real bugs (see **Debugging Notes**): a copy-pasted
  `clashable_guard_used_this_round` check inside
  `find_eligible_clashable_counter` that let using one block the other, and
  a much bigger targeting bug where the old fallback (`apply_counter_redirects`
  Passes 2/3, now removed) just grabbed *any* solo attack aimed at the
  Clashable-defense holder, ignoring what it had actually declared as its
  target — so protecting an ally with a Clashable Counter never worked as
  declared. Both verified fixed with real objects, not just read-through.
  Same restrictions as before: solo-only (never converts an already-mutual
  Clash), never steals a teammate's incoming attack, single-use per round
  (own `clashable_guard_used_this_round` / `clashable_counter_used_this_round`
  flag, now correctly independent of each other).
- **Fall-through** (`cogs/battle.py`, `combat()`): the defense-cascade
  rule Pass 0 didn't cover — any plain attack that finds no live `[Evade]`
  waiting for it may redirect into an ad-hoc Clash against the *same
  target Fighter's* still-idle `[Clashable Guard]`/`[Clashable Counter]`
  instead of landing as a normal hit. Implementation: any Pass-0-unmatched
  Clashable solo unit is pulled out of the main speed-ordered list up
  front into `deferred_clashable_by_fighter` (keyed by `id(fighter)`,
  `Fighter` isn't hashable) rather than resolving its idle turn at its own
  natural speed position; the main loop's plain-attack branch checks
  `find_eligible_evade` first (a side-effect-free peek, not a commit), and
  only if that comes back empty does it check this dict for the target
  fighter, picking the highest-Speed entry if more than one is available
  (tie-break: higher Speed wins). Required extracting the whole Clash
  resolution body out of the animation loop's shared closures
  (`render`/`animate_faces`/`animate_damage`/`animate_attrition_round`) into its own
  callable, `resolve_clash_unit(entry_a, entry_b)`, so it can be invoked a
  second time, mid-loop, for a redirected target without duplicating that
  block — the surgery earlier notes on this repo had flagged as too risky
  to rush. Whatever's left unclaimed once the whole loop finishes still
  gets its real "nothing to intercept" idle turn rendered at the very end.
  Verified with real objects: a redirected attack that should lose still
  loses (damage lands on the original target, not the interceptor), a
  redirect that should win protects the target fully, the Speed tie-break
  picks correctly between an idle Guard and an idle Counter, Evade still
  takes priority when it's actually eligible, and a normal Clash/plain hit
  with nothing to redirect is unaffected (regression-checked).

- **Stagger**: up to 3 HP% thresholds per fighter
  (`Fighter.stagger_thresholds`, default 55/40/25%), checked via
  `Fighter.check_stagger()` right after damage lands. Each enabled tier is
  checked independently; the deepest tier reached becomes active. A disabled
  tier falls back to the shallowest still-enabled one. While staggered, the
  next incoming hit is multiplied (`STAGGER_MULTIPLIERS`: 1.5/2.0/2.5x),
  checked before this hit's own Stagger update, so the triggering hit itself
  gets no bonus. Tier 1 clears end of the triggering round; Tier 2/3 persist
  one extra round. `[On Stagger]` fires on the attacker's skill after damage
  lands if the target is now staggered. Settable per fighter via
  `/battle setstatus`; Tier 1 can never be disabled. Not yet wired into a
  Build Point purchase system.
- **Offset**: two plain `[Guard]` skills mutually targeting each other fully
  cancel: no coins, no Shield, no triggers, just a log line. `[Clashable
  Guard]`, `[Counter]`, `[Clashable Counter]` are excluded from Offset
  (matches canon's exception clause exactly).
- **Attack Weight (multi-target splash)**: Supermove only for players (admins and exempt characters can use it anywhere); fully built and confirmed
  reachable from real play — `AddSkillModal` exposes `attack_weight_input`,
  parsed and passed into `Skill(attack_weight=...)`, so this is no longer a
  "needs verification" item. At `declare()` time, `(attack_weight - 1)`
  extra enemy slots are auto-picked as splash candidates: any living enemy
  is eligible, fastest Speed first, deduped to exactly **one representative
  slot per distinct Fighter** (their own fastest eligible slot) since
  HP/Shield/status all live on the Fighter, not the slot. The primary
  `(target_fighter, target_slot)` is excluded from candidate selection, but
  the primary target's *other* slots can still be picked; hitting the same
  enemy via primary + splash is intentional ("Attack Weight reaches them
  twice") but must **not** double damage or status; a same-Fighter splash is
  a logged no-op. In `combat()`, splash targets reuse the already-computed
  hit (no re-tossed coins, no extra Poise consumption, no re-fired per-coin
  Triggers) but each splash target gets its own resistance, Stagger check,
  Evade check (`resolve_evade`, whole-hit dodge, not per-coin), and its own
  independent per-coin Rupture/status accrual walked the same way
  `apply_incoming_hit` does for the primary target. `[Clashable
  Guard]`/`[Clashable Counter]` are excluded from splash interactions
  entirely (same exception Offset gets). Plain `[Counter]` holders are
  excluded from splash candidacy in `apply_counter_redirects` if they're
  also the primary defender, so one incoming attack never draws two
  retaliations.
- **Animated Combat Phase**: `/battle combat` plays the round as one
  continuously-edited message with coin-by-coin face reveals per Clash
  attrition round, then a face-reveal + real-damage reveal pass for the
  winner's final toss (pulled from `apply_incoming_hit`'s actual log, not an
  approximation). Each unit locks a permanent one-line summary as it
  finishes. `CombatLogView`'s **Full Log** button shows the full breakdown as
  ephemeral embeds, chunked if long. Tuning: `MIN_EDIT_GAP` near the top of
  `combat()` (the least time between two edits of the combat message).
- **Proelium Fatale GIF**: any `fatal`-type battle shows the Proelium Fatale
  GIF (`BATTLE_TYPES["fatal"]["image"]`), on creation and every embed sync.
- **Elimination**: 0 HP sets `Fighter.eliminated`, a permanent one-way flag
  for the rest of that battle — no revival path, matching canon's Sinner
  Death being final within an encounter. Checked at the very end of
  `combat()`'s resolution loop (`newly_eliminated = [f for f in
  battle.fighters if not f.is_alive() and not f.eliminated]`), announced
  in-round (`💀 Victim has been eliminated.`), removed from their side's
  roster in the battle embed and shown in a combined "Eliminated" section
  instead, and blocked from acting (`declare`) or being targeted (`declare`,
  target-side too) or touched by `/battle setstatus`. Does not remove Skill
  Slots or do anything beyond this — no Backup/Substitute/Retreat system
  exists to hand the fight off to.

### Parts / Core

A multi-part enemy is one Fighter whose skill slots can each be a **Part**
(`Part` in `game/battle.py`, `Fighter.parts`, keyed by slot). Admin-only
`/battle addpart fighter slot name hp [resistances]`, `removepart`,
`repairpart`. Attackers need no new input: the `target_slot` they already
pick in `/battle declare` is the Part. A hit on that slot uses
`Fighter.resistances_for_slot` (the Part's own resistances, else the fighter's),
and after damage lands `Fighter.hit_part` drains the Part's HP while the
fighter's own HP is the shared Core. At 0 HP a Part breaks: `/battle declare`
refuses that slot until `repairpart`. A broken Part still drains the Core when
hit. A break lands from the next declaration on, an attack already declared
that round still resolves. Attack Weight splash hits use the same Part
logic. Each Part has its own Stagger: when a hit takes the Part's HP percent under one of
the fighter's enabled thresholds (same numbers and durations as the Core's, Tier 1 clears at
round end, Tier 2/3 a round later) the Part is Staggered, its slot is refused by `/battle
declare`, and hits aimed at it use the deeper of the Part's and the Core's Stagger
multiplier. A newly Staggered Part fires the attacker's `[On Stagger]` line, but not the
`[Ally On Stagger]` reactions. `Recover from Stagger` and `repairpart` clear it. Part names, HP and
Stagger show under the fighter in the battle embed. See
`docs/ENGINEERING_NOTES.md#parts-and-core`.

## Status Effects: Current State

- **Data model is solid**: `StatusInstance(name, potency, count)`, proper
  stacking (`apply_status` adds potency+count onto existing), decay
  (`decay_after_trigger` ticks count down by 1), universal potency cap at 99
  (canon's own cap; Charge additionally caps its own Count at 20, a separate
  ceiling). Resistance applies to infliction the same way it applies to
  damage: a flat decimal multiplier (`apply_resistance`; see below).
- **Self-buff resources**: `SELF_BUFF_STATUSES` is just `["poise",
  "charge"]` now. Poise drives Crit. Charge's own Count decay is wired
  (`apply_turn_end_status_ticks`), but nothing *consumes* Charge into an
  actual payoff yet (no Coin Power scaling, no Haste conversion) — still a
  gap. Counter and Evade both used to live in this list; both are declared
  Skills now, not resources (see Combat Features).
- **All 5 `INFLICTABLE_STATUSES` (`burn`, `bleed`, `tremor`, `rupture`,
  `sinking`) have real payoffs now** — this was the single biggest gap as of
  the last README pass, and it's closed:
  - **Rupture**: bonus damage added directly to an incoming hit (bypasses
    resistance), decays 1 count per trigger. Built earliest, unchanged.
  - **Burn**: fixed Potency damage at Turn End (`apply_turn_end_status_ticks`,
    called once per round in `combat()` before the round closes), through
    the normal Shield-first `take_damage` path, decays 1 count per tick.
  - **Sinking**: fixed Sanity loss equal to Potency at Turn End, same
    function. No Count to decay — persists at whatever Potency it's at until
    changed some other way, matching its own spec (doesn't wear off on its
    own).
  - **Tremor**: `fire_tremor_burst` — fires `[Tremor Burst]` on an
    attacker's skill against a target currently holding Tremor (both the
    Trigger line's own generic effect, if any, AND the built-in payload:
    raises the target's *enabled* Stagger thresholds by Tremor's own
    Potency as percentage points, then decays Tremor 1 count).
  - **Bleed**: `apply_bleed_self_damage` — damages the *bleeding fighter
    themselves*, fixed Potency **per coin** in whatever non-Defense skill
    they just landed (Clash-decisive toss or solo unopposed toss; `guard`/
    `clashable_guard`/`counter`/`clashable_counter`-tagged skills don't
    count as "attacking" for this, per `DEFENSE_TAGS`), decays 1 count per
    *use*, not per coin.
- **Kill-triggered timings are built**: `[On Kill]` fires on the attacker's
  skill if this hit just reduced the target to 0 HP (checked via
  `fire_kill_triggers`, called after `take_damage`); `[On Crit Kill]` also
  fires if any coin in that same hit was a Crit. Both were listed as "not in
  `conditions.py` at all" in the last README pass — that's now wrong, fixed.
- **Non-Sin debuffs built** (wiki numbers, no resistances, all settable via
  `/battle setstatus`):
  - Potency-based, last the rest of the round, wiped at turn end:
    **Bind** (Speed -X) and **Power Down** (all coins lose X Final Power).
    Write them as `:Coin1: [On Hit] Inflict Bind Potency 3, Count 1`.
  - Count-based, same lifetime: **Fragile** and **Slash/Pierce/Blunt
    Fragility** (+10% damage taken per Count, max 10), **Damage Down**
    (-10% damage dealt per Count, max 10) and **Paralyze** (the first X coins
    of the caster's skills deal 0). Write them as
    `:Coin1: [On Hit] Inflict +3 Fragile Count`.
  - **Poison** (Count): at turn end take fixed damage equal to Count, then
    Count is halved. Lasts across turns until it reaches 0.
  - **Immobilized**: the fighter cannot declare and any declared actions are
    dropped at combat start. It is removed at the end of the round it blocked.
    If it lands mid-combat it carries into next round.
  See `docs/ENGINEERING_NOTES.md#statuses-one-turn-debuffs`.
- **Custom statuses and resources** (e.g. a "Pulsation" counter): a
  character can own up to 10, made with `/character addstatus`
  (name, buff or debuff, persistent or turn-only, optional emoji ID of an
  emoji the bot can see, description). Anyone who owns the character can
  make one, every create and remove is posted to the log channel for admins,
  and `/character removestatus` / `/character statuses` manage them. Use
  them in trigger lines: `[Turn Start] Gain +2 Pulsation Count`,
  `Gain Pulsation Potency 2, Count 3`, `Inflict +2 Death Mark Count`,
  `At 5+ Pulsation, ...` as a condition (checks Count, add the word Potency to check Potency instead: `At 5+ Pulsation Potency, ...`). Buffs are gained by the owner, debuffs
  are inflicted on enemies. A turn-only status is wiped at every turn end.
  Admins can place one with `/battle setstatus custom_status`. They do
  nothing by themselves until a skill spends them, see Consume below.
  See `docs/ENGINEERING_NOTES.md#custom-statuses`.
- **Consume triggers** spend a self buff (Charge, Poise or a custom buff)
  for a payoff: `[On Use] Consume 3 Pulsation: Coin Power +2`,
  `:Coin1: [Coin Start] Consume 2 Pulsation: Deal +4 damage`,
  `:Coin2: [On Hit] Consume 1 Charge: Inflict +2 Burn Count`,
  `[Turn Start] Consume 2 Pulsation: Gain +1 Charge Count`. The payoff is any
  normal effect. If the fighter has less Count than the cost, the trigger
  simply does not fire and nothing is spent. Only Count is spent, Potency
  stays. `At N+ X,` and `If the target has N+ X,` check Count by default; write
  `At N+ X Potency,` to check Potency. Skills saved before this change keep
  their old Potency check until they are re-added.
- **Permanent Potency and scaling** (the Pulsation style). Two settings when
  making a status with `/character addstatus`: `potency_every` (every N Count
  spent in total this battle adds +1 Potency, 0 = off, Limbus Pulsation is 10)
  and `keep_potency` (the status stays at 0 Count while it has Potency).
  Payoffs can scale off Potency or Count:
  `[On Use] Coin Power +1 per 3 Pulsation Potency`,
  `[On Use] Deal +2 damage per 5 Pulsation Count`. Scaling works on Coin
  Power and Deal damage only, and it reads the value after any cost on the
  same line was paid. `(max N)` caps a scaled bonus, and `on the target`
  reads the target's status instead of the caster's. A scaled percent must
  have a cap: `[On Use] Deal +5% damage per 6 Burn Count on the target (max
  15%)`. The spent total resets every battle.
  See `docs/ENGINEERING_NOTES.md#consume-triggers`.
- **Panic and Low Morale**: Sanity -30 or lower is Low Morale, which does
  nothing on its own. Sanity -45 is Panic: at turn end the fighter becomes
  Immobilized for the next turn, and Sanity resets to 0 once that turn has
  passed. Characters build their own version with conditions usable in any
  trigger line: `If this unit is Low Morale,`, `If this unit is Panicked,`
  and the same for `If the target is ...`. Low Morale is also true while
  Panicked. Example:
  `:Coin1: [On Hit] If this unit is Panicked, Inflict +3 Sinking Count`.
  See `docs/ENGINEERING_NOTES.md#panic-and-low-morale`.
- **Healing (self only for now)**: `Heal 8 HP` and `Heal 10% max HP` work in
  any trigger line and any timing, e.g. `[Turn Start] Heal 5% max HP` or
  `:Coin1: [On Hit] Consume 2 Pulsation: Heal 6 HP`. Healing stops at max HP
  and does not work on a downed or eliminated fighter. Healing an ally or a
  target waits for the `[Ally ...]` trigger prefix, because `/battle declare`
  only allows targeting enemies. Heal boosts and HP Healing Down are not
  built. See `docs/ENGINEERING_NOTES.md#healing`.
- **Self buffs** (Count-based, last the rest of the round, wiped at turn
  end, gained like any self buff, e.g. `[Turn Start] Gain +2 Haste Count`):
  **Power Up** (+Count Final Power on every coin), **Haste** (+Count Speed on
  every slot), **Damage Up** (+10% damage dealt per Count, max 10) and
  **Protection** (-10% damage taken per Count, max 10). Damage Up,
  Protection, Fragile and Damage Down all add into one multiplier applied
  once to the whole hit, floored at 0.
- **Duality skills and ally effects** (first half of the `[Ally ...]` work;
  `[Duality]` replaces `[Support]`, and old `[Support]` text and saved skills
  still work as Duality): give a skill the `[Duality]` flag (on its own line).
  Aimed at an enemy it is a normal attack (any number of coins, Clashes unless
  it is also `[Unclashable]`). Aimed at an ally or yourself with
  `/battle declare` nobody takes damage and nothing Clashes, but the coins
  still flip and its `[Before Use]`, `[On Use]`, coin-timing and `[Attack End]`
  lines fire (fastest slot first, listed with the passive triggers). Write
  `If the target is an ally,` / `If the target is an enemy,` to split the two
  modes, and end a line with `(2 times per turn)` or `(once per turn)` to cap
  how often it fires each round. Add `to the ally` at the end of a line
  to put the effect on the declared ally: `[On Use] Heal 8 HP to the ally`,
  `[On Use] Gain +2 Haste Count to the ally`, `[On Use] Gain 3 Sanity to the
  ally`, `[On Use] Remove Bleed to the ally`. Only Heal, Gain, Gain Sanity and
  the new `Remove <status>` can go to an ally. Lines without it affect the
  caster, and a Consume cost is always paid by the caster. Passives that react
  to allies (`[Ally On Hit]` and friends) are the next patch.
  See `docs/ENGINEERING_NOTES.md#ally-support`.
- **Supermoves (E.G.O)**: a normal attack skill with `[Supermove]` on its own
  line. The bot prices it from its expected total damage (`game/supermoves.py`,
  every number in one place): Sanity cost about damage / 6 (min 4, max 30), paid
  when it is declared and needed in hand to declare it, refunded on undeclare.
  Hard hitters also drain Sanity at every turn end afterwards (40+ damage:
  5 x 2 turns, 80+: 8 x 3, 130+: 10 x 3), before the Panic check. Every reuse
  of the same Supermove in a battle adds 50% to both cost and drain. Cannot be
  combined with Duality, Guard, Evade or Counter. `/battle mystatus` shows a
  running drain and the uses so far. See `docs/ENGINEERING_NOTES.md#supermoves`.
- **Ally reaction timings** (second half of `[Ally ...]`): put these on a
  passive and they fire when one of the owner's allies (same side, not the
  owner) does or suffers something: `[Ally On Hit]`, `[Ally On Crit]`,
  `[Ally On Kill]` (the ally defeated an enemy), `[Ally Is Hit]`,
  `[Ally On Stagger]` (the ally was newly Staggered), `[Ally Turn Start]` and
  `[Ally Combat Start]` (once per living ally). Effects land on the owner by
  default. `to the ally` puts a Heal, Gain, Gain Sanity or Remove on the ally
  who triggered it, and an Inflict goes to the enemy involved. Examples:
  `[Ally On Hit] Gain +1 Charge Count`, `[Ally Is Hit] Heal 4 HP to the ally`,
  `[Ally On Kill] Gain +2 Haste Count`. They fire once per landed hit, not per
  coin, and not for an evaded or zero-damage hit. Attack Weight splash hits fire
  them too, once per splash target. See `docs/ENGINEERING_NOTES.md#ally-reactions`.
- **Build Points (first part)**: `game/build_points.py` holds every number. A
  character has a BP pool (30 at the start, more from `/charadmin grantbp`),
  and its level is just a label on the pool (45 / 63 / 84 / 108). HP (100 to
  160), Power (6 to 12, 0 / 1 / 2 / 4 / 4 / 6 / 8 BP per step), Speed range (1 to 8, at least 2 wide,
  priced by its average) and Stagger tier removal (5 / 7 / 12) are paid from the
  pool. No single stat may use more than 40% of the pool, and Power + Speed or
  Power + HP at most 60%. Power also caps a skill: Base Power at most
  floor(2 + 0.4 x Power), Coin Power at most floor(1 + 0.2 x Power); the skill
  form rejects anything higher (admins are exempt). Only admins change HP,
  Speed, Power and Stagger. A character that breaks the rules cannot join a
  battle (an admin can still add it, with a note), `/charadmin recheck` DMs
  every owner what to fix, and `/charadmin exempt` is for NPCs. Supermoves
  (up to 3) cost BP too: Low 10 / Mid 16 / High 22 by damage, plus 1 to 6 for
  demanding timings, minus an admin-set drawback (`/charadmin drawback`). Only
  a Supermove can use the strongest tags (Turn Start, Clash Win, Unclashable
  and so on), only admins add Supermoves for now, and they skip the Power
  ceiling but cannot hit for more than about 200. Status resistances (Burn,
  Bleed, Tremor, Rupture, Sinking: 0.5x to 2.0x) are priced, with at most 18 BP
  banked from vulnerabilities; Slash/Blunt/Pierce are all 1.0x or one each of
  0.5 / 1.0 / 1.5. Passive Points (admin-granted, max 2 per event) pay for identity passives (always-on rules kept off the skill list, reacting at Turn/Combat Start, On Evade, Ally reactions and the passive-only Self Is Hit / Self On Stagger / Self On Kill / Self On Death, and shaping every skill they use at Before Use / On Use / Clash Start / Before Attack, which is how a passive adds Deal +10% damage) and for new unique statuses (1 to 3 PP). Non-keyword effects (Haste, Fragile, Bind, Protection, Damage Up/Down, Power Up/Down, Paralyze) on normal skills cost ceil(size / 2) BP each, at most 3 BP per kit, paid from the pool and shown in `/character bp`. Defense archetypes (max 2) gate Guard / Evade / Counter style skills, with a lowest Speed 4 or removed Stagger Tier 2 prerequisite. Status ranks (max 2 statuses, Rank 1 to 5) limit which statuses a normal skill may use and how strong (budget for Bleed/Tremor/Poise/Charge, Potency cap for Burn/Sinking/Rupture), old skills grandfathered. Skill Points (granted by admins, start at 0) pay for extra trigger lines: the first line on a skill is free, each extra costs 1 SP, old skills are grandfathered. Offense/Defense Level (0 to 5, 4% damage per step, 0 / 1 / 2 /
  4 / 4 / 6 BP) change damage dealt and taken. In a clash the higher Level adds the
  Level gap to its Power every round (`/charadmin exempt level:` and `/battle
  setstatus level:` set it for NPCs). Not built yet: the player request and
  approval flow (every build change is admin-only for now). See
  `docs/ENGINEERING_NOTES.md#build-points`.
- Still missing as real statuses: Clash Power Down, Plus/Minus Coin
  Drop/Boost, HP Healing Down, Curse. Offense/Defense Level Up/Down and
  Clash Power already exist as one-turn trigger effects (see Trigger Syntax),
  just not as statuses with their own icon.
- No turn-based expiry independent of triggering (a status that should wear
  off after N turns even if its trigger never fires isn't modeled — every
  status here only ever decays by actually being triggered).

## Character System: Current State

`/character create/edit/resistance/view/list/delete/say` covers the **stat
sheet** (the Build Point rules around it are in Character Creation &
Progression below): HP, Speed (flat or min/max range, replacing the old flat-speed
field once set), a single flat Power value, resistances (comma-separated
multi-set: `resistance_types:slash,burn values:1.25,0.75`, covers the 3
damage types + 5 status types, 1.0 = Normal, see Combat Features), avatar,
and `say` (webhook RP). No Sanity field: intentional, Sanity always starts at
0 per battle. Up to 3 group tags per character (`/charadmin tags`, admin
only) that passive conditions can count. No self-buff starting values.

**Persistent Stagger config now exists**: `Character.stagger_thresholds` /
`stagger_tiers_enabled` (both `None` by default, meaning "use `Fighter`'s
own defaults"), applied in `Fighter.from_character()`. This closes what used
to be a listed gap ("no Stagger threshold customization on Character").

**Characters now carry Skills.** `/character addskill`/`skills`/`removeskill`
teach, list, and remove skills on a saved `Character`, using the same popup
form as `/battle addskill`. `Fighter.from_character()` deep-copies a
character's saved skills into the Fighter at battle start, so a skill only
has to be taught once, ever — fighting no longer means rebuilding a kit from
scratch each time. This used to be the biggest listed gap ("no Skill
loadouts"); it's closed now. `/battle addskill` still exists and is explicitly
**temporary, battle-only** — useful for a one-off fighter or testing a skill
before committing it to a character permanently. The two skill lists are
separate: a `Character`'s saved skills vs. an in-battle `Fighter`'s current
skills (which starts as a copy of the character's, but `/battle
addskill`/`removeskill` only change that battle's copy, not the saved
character).

The flat `Power` field on Character looks orphaned now that Skills carry
their own independent Base Power/Coin Power; worth confirming whether it's
still consumed anywhere at battle time or is dead weight from before the
Skill system existed.

**Skill management is fully built now, not partially.**
`/battle mystatus` privately shows a fighter's exact statuses, Potency, Count and
Sanity to its owner (the battle embed only shows hint tiers).
`/battle skills` lists everything a fighter currently knows, `/battle
removeskill` removes one by name, and `/battle editskill` opens the same
popup pre-filled with the skill's current values -- fixing a typo no longer
means removing and re-adding the whole thing. `/character` has the matching
three. All closed since the last README pass.

## Command Reference

### `/battle` group (`cogs/battle.py`)

| Command | Notes |
| --- | --- |
| `create` | Starts a battle (Spar / Standard / Fatal). One per channel. Battle type is currently cosmetic only, no mechanical branching (no permanent-death enforcement for Fatal, etc. — though see Elimination in Combat Features, which applies regardless of battle type). |
| `addfighter` | From a saved `/character` or as a one-off. |
| `addnpc` | Admin. Adds a ready-made Fodder / Elite / Boss fighter (`game/npc_tiers.py`) with HP, Speed, Power, Level, slots and Stagger preset, optional `name`/`hp`/`level`. Battle only, never saved, no Build Point checks. |
| `addskill` | Owner-or-admin (`_can_manage_fighter`). `AddSkillModal`: one popup, packed comma-separated stats (Discord caps a modal at 5 fields), per-coin statuses, Trigger text box, `attack_weight_input`. 1-coin enforcement on `[Guard]`/`[Evade]`/`[Clashable Guard]`. **Temporary, battle-only** — teaches the in-battle `Fighter`, not the saved `Character`. Use `/character addskill` to keep a skill permanently. |
| `skills` | Lists everything a fighter currently knows (this battle only — see `/character skills` for a character's permanent list). |
| `removeskill` | Removes one named skill from a fighter's known skills, for this battle only. `skill_name` autocompletes from that fighter's own known skills. |
| `editskill` | Owner or admin, same as `removeskill`. Opens the same `AddSkillModal` popup as `addskill`, but pre-filled with the skill's current values (`EditSkillModal`) -- renaming is handled (the old name's entry is removed, not left behind alongside the new one), and the admin log channel gets a before/after instead of just the end result. `skill_name` autocompletes the same way as `removeskill`. Battle-only, same as `addskill`. |
| `declare` | Owner-or-admin (`_can_manage_fighter`). Locks a skill into a slot aimed at a target's slot. No scouting. Also where Attack Weight splash candidates get picked (see Combat Features). Blocks targeting an eliminated fighter, same as it already blocked an eliminated caster from acting. |
| `undeclare` | Owner-or-admin. Clears one declared slot. |
| `removefighter` | Owner or admin (`ADMIN_ROLE_ID`) via `_can_manage_fighter`. |
| `setstatus` | Admin-only (`ADMIN_ROLE_ID`) via `_is_admin`, enforced in code; also hidden from the Discord command list by default for anyone without Manage Server (`default_permissions`), though a server whose admin role doesn't carry that permission should re-scope it via Server Settings → Integrations. Sets HP, Sanity, Speed (min+max), resistances, Power, Stagger thresholds/enabled-tiers, one status, whichever fields are passed. Refuses to touch an eliminated fighter at all. |
| `addpart` / `removepart` / `repairpart` | Admin-only. Parts / Core for a multi-part enemy, see Combat Features. `addpart fighter slot name hp [resistances]` makes a skill slot a body Part; `repairpart` restores HP and un-breaks it. |
| `mystatus` | Private view of your own fighter's exact statuses and numbers. |
| `log` | Re-read the recap and full log of a recent round (the last 5 are kept, `round` autocompletes). Only you see it. |
| `combat` | Resolves the round. See Animated Combat Phase. Also where Turn-End status ticks (Burn/Sinking/Charge) and Elimination checks run, once per round. |
| `end` | Ends the battle. |

**Friendlier skill errors and previews.** A bad trigger line now says which line it is, what is
wrong, and the closest valid thing: `[On Hitt]` gets "Did you mean [On Hit]", a misspelled status
gets the nearest status name, `Deal 3 damage` gets "Closest valid effect: Deal +N damage", and
a condition without its comma or an unknown condition lists the supported ones (`friendly_error_text`
in `game/trigger_text.py`). The skill preview used by `/battle addskill`, `editskill`, `/battle skills`
and `/character skills` now shows an "Expected damage: about X (lowest Y, highest Z)" line and
writes each trigger in plain words (`Coin 1, On Hit: if the target has 3+ Burn Count -> Deal +3
damage`) instead of internal names. A wrong damage type gets a "Did you mean" too. The
expected-damage line is a calculation, not a simulated roll, and counts flat Deal / Coin Power
bonuses as always firing. See `docs/ENGINEERING_NOTES.md#friendlier-errors-and-previews`.

**Round ping.** After a round resolves and the fresh battle status is posted, the bot sends
one public message mentioning the owners of every fighter that can act in the new round, with
their fighters in brackets (`build_round_ping`). Fighters with no linked owner, Staggered,
Immobilized (including Panicked) or eliminated fighters are left out, and nothing is sent
when a side has nobody left standing. Not sent when a battle is first created. Other ping
ideas (a waiting list, a ready-to-resolve notice) are not built.

**Round recap and `/battle log`.** Every Combat Phase ends with a "Round N Recap" field on
the results message: which fights happened, and for each fighter whose numbers changed,
HP, Sanity, newly gained Stagger, a lost Shield, Parts that broke, statuses gained,
changed or lost, and who went down. It is built by comparing a snapshot taken when
`/battle combat` starts against the state after Turn End (`game/recap.py`), so it needs
no hooks inside the resolution code. One-turn debuffs that expire at Turn End do not
show up in it. The recap and the Full Log entries of the last 5 rounds are kept on
`Battle.history`, so `/battle log` still works after the Full Log button times out.
See `docs/ENGINEERING_NOTES.md#round-recap-and-log`.

**Autocomplete.** Name and number options suggest values as you type: your own fighters
(`fighter` on declare, undeclare, addskill, removeskill, editskill, removefighter),
any fighter on skills, setstatus and the Parts commands, enemies (or allies for a
`[Duality]` skill) for `target`, your fighter's own free slots for `slot`, the
target's slots with Part names and HP for `target_slot`, saved characters for
`addfighter` and every `/character` `name` (your own, or everyone's for an admin).
See `docs/ENGINEERING_NOTES.md#autocomplete`.

No `/battle status`; the synced embed (`build_battle_embed`, kept current
via `sync_battle_message`) already shows everything it did. No skip/pass
action exists; every declared slot needs a real skill, and combat won't run
until `all_declared()` is true, with no admin override to force it. The
round number IS shown to players, as the synced embed's footer text
("Round N") -- this used to be listed as missing, no longer true (it's
easy to miss since a Discord footer is small and gray, but it's there).
**Elimination is built** (0 HP →
permanent flag, removed from roster, blocked from acting/being targeted,
see Combat Features) — this used to be listed as entirely absent, no longer
true. Still no Skill Slot loss, no Backup/Substitute/Retreat system beyond
that flag.

### `/character` group (`cogs/character.py`)

| Command | Notes |
| --- | --- |
| `create` | New saved character, owned by whoever ran it. |
| `import` | Makes a new character from a code copied off the character builder website (see **Character Builder Website**). Skills are added at once; anything that costs points becomes one `build` request for the admins. Never overwrites an existing character. |
| `list` | Lists your own saved characters. |
| `view` | Owner or admin; `public:True` posts to channel. |
| `edit` | Owner-or-admin for name, avatar and resistances. HP, Speed, Power, Stagger, resistances and Offense/Defense Level are admin-only (they cost Build Points) and a build that breaks the rules is rejected before anything is saved. `speed_min`/`speed_max` must be given together, no implicit flat-speed shortcut anymore. Also where `stagger_thresholds`/`stagger_tiers_enabled` get set, applied at battle-start via `Fighter.from_character()`. |
| `bp` | Owner-or-admin. Level, BP spent and available, per-stat breakdown, anything breaking the rules, and the last few BP changes. |
| `resistance` | Owner-or-admin. Multi-set via positionally-aligned `resistance_types`/`values`. |
| `addskill` | Owner-or-admin. Same `AddSkillModal` popup as `/battle addskill`, but teaches the skill to the saved `Character` permanently, and it's then picked up automatically next time this character joins a battle, via `Fighter.from_character()`. |
| `skills` | Owner-or-admin. Lists everything this character permanently knows. |
| `removeskill` | Owner-or-admin. Removes one named skill from the character's permanent skill list. `skill_name` autocompletes from this character's own known skills. |
| `editskill` | Owner-or-admin. Same `EditSkillModal` popup as `/battle editskill`, pre-filled with the skill's current values, but saves to the permanent `Character` (via the same `on_saved=save_character` wiring `addskill` already uses). `skill_name` autocompletes the same way as `removeskill`. |
| `delete` | Owner-or-admin. |
| `say` | Owner-or-admin. Speaks via webhook (name + avatar). |

### `/request` group (`cogs/build_requests.py`)

Players cannot change anything that costs points, so they ask. A request stores one change as data
(`game/build_requests.py`), is checked on a copy of the character with the normal Build Point rules (so an impossible
one is turned away at once), and is posted with Approve and Deny buttons in the review channel. Approving checks it
again against the character as it is now, saves it and DMs the player. Denying asks for an optional reason.

| Command | Notes |
| --- | --- |
| `stats` | HP, Speed range, Power, Offense/Defense Level, Stagger tiers to remove, resistances. A reason is required. |
| `rank` | A status rank 0 to 5. |
| `archetype` | Take or drop a defense archetype. |
| `tags` | Up to 3 group tags (no tags clears them). |
| `passive` | An identity passive with 1 to 3 lines and a suggested PP price. On approval the admin confirms the final price. |
| `mine` / `cancel` | See your requests and their answers (optionally for one character), withdraw an open one. |
| `channel` | Admin. Sets where new requests are posted (default: the current channel). |
| `list` / `review` | Admin. Open requests (filter by character, or include answered ones), and re-posting one with its buttons. |

Filing a request shows the budget before and after (`BP 16/30 -> 18/30`, plus PP for a passive), the review message shows the same,
and a passive that counts a tag nobody has gets a warning with a "did you mean" hint. The same warning shows in the skill form
and after `/charadmin passive`. A player can have 3 open requests, and one open request per kind and character. Skills and custom statuses are not
requests: owners add those themselves and the Build Point checks still apply. Requests are stored in `data/requests.json`.

A fifth kind, `build`, cannot be filed with a command. `/character import` makes it: one request that holds a whole new character's stats, ranks,
defense types, tags, passives and skills. `game/build_requests.py` applies it as the same single changes in a fixed order (`build_steps`), so every
existing check runs unchanged, and `cogs/build_check.py` teaches the skills through the real skill popup. Approving re-checks all of it. A passive
needs Passive Points that an admin gives out, so an admin adds them to the character first, otherwise the approval says so and changes nothing.

### `/charadmin` group (`cogs/character.py`)

The admin-only build commands. They are their own group because Discord allows at most 25 subcommands in one group, and `/character` was already at that limit.

| Command | Notes |
| --- | --- |
| `rank` | Admin. Sets a status rank 0 to 5 (Burn, Bleed, Tremor, Rupture, Sinking, Poise, Charge; at most 2 owned; costs 1 / 2 / 4 / 7 / 11 BP). Checked on a copy first, DMs the owner. |
| `archetype` | Admin. Gives or removes a defense archetype (Guard 3 / Evade 4 / Counter 5 / Clashable Guard 6 / Clashable Counter 7 BP, at most 2, with Speed and Stagger prerequisites). Checked on a copy first, DMs the owner. |
| `grantpp` | Admin. Gives Passive Points for a GM-approved moment (max 2 per grant, reason required), logs it and DMs the owner. |
| `passive` | Admin. Gives an identity passive (name, Passive or Support, PP cost 1 to 6, up to 3 trigger lines). Kept off the skill list. |
| `removepassive` | Admin. Removes a passive and frees its PP. |
| `tags` | Admin. Sets up to 3 group tags (like `N Corp`) that passive conditions can count. |
| `grantsp` | Admin. Adds (or removes) Skill Points like `grantbp`, logs the reason and DMs the owner. |
| `grantbp` | Admin. Adds (or removes, with a negative number) BP, logs the reason, and DMs the owner. Warns if the build is now over budget. |
| `exempt` | Admin. Skips every Build Point check for a character (NPCs and bosses). Optional `level` fixes the Level it fights at (0 clears it). |
| `recheck` | Admin. Checks every saved character against the rules and DMs each owner what to fix. |

### `/roll`

Plain dice roller (`1d20+4+2` style), not tied to battle state.

### `/startingoff`

A private walkthrough for new players (`cogs/tutorial.py`, rules and saved progress in `game/tutorial.py`).
It has 10 steps in two parts: your character (create, view, add a skill, check Build Points) and a practice fight
(create a battle, join, add a Dummy with a skill, declare both moves, run the round, end). The bot shows one step at
a time. After every command it checks whether the step is now true: by looking at what really exists (saved
characters, the channel's battle) or at which command just ran. When a step finishes, the bot sends a private
follow-up with the next step. Buttons skip a step, start over, or turn tips on and off.

The same listener sends a one-time tip the first time someone uses a command that has one (`TIPS` in
`game/tutorial.py`), and a single pointer to `/startingoff` the first time anyone uses any command. A tip is not used
up by a command that failed (for example a name that does not exist). Progress is saved per person in
`data/tutorial.json`. Commands that open a form (`addskill`) cannot be followed by a message, so the skill steps
finish when the form's Confirm button is pressed, and a locked-in move finishes the declare step the same way
(`skill_saved` and `declare_locked` events dispatched from `cogs/battle.py`).

### `/practice`

A solo fight for learning (`cogs/practice.py`, rules in `game/practice.py`). `/practice start` builds an ordinary
`Battle` in the channel with the player on Side A and a **Dummy** on Side B, and registers it in the cog. The player
still uses the real `/battle declare` and `/battle combat`. The Dummy is an owner-less `Fighter`, so nobody can declare
for it; the cog does it. Options: `character` (one of your own; blank uses your only one, or a ready-made **Trainee**
with a lent starter skill if you have none), `tier` (Easy: 60 HP and one attack a round, or Tougher: 150 HP and three
attacks a round) and `show_plan` (on by default).

At the start of every round `plan_moves` gives each of the Dummy's acting slots a random skill, a random living enemy
and an enemy slot (different slots when there is room), and `apply_plan` declares them. The cog posts that plan to the
channel so the player can answer a move on purpose: a Clash happens when the player's slot aims at the Dummy slot that
aims back at it. Turn `show_plan` off to guess. The channel message is sent with mentions switched off, because
character names are free text.

When the Dummy plans is decided by `_check`, which runs after every `/battle combat` (through
`on_app_command_completion`) and when a move is locked in (the `declare_locked` event). It is safe to run any number
of times: it only plans when the Dummy can act and has not declared. It also ends the fight when a side is down
(`outcome`: won, lost or draw) and says so once. A Staggered or Immobilized Dummy sits the round out, and combat does
not wait for it (`Battle.all_declared` already skips fighters that cannot act). Sessions live in memory like battles do,
and a session is dropped as soon as its battle is gone, so `/battle end` needs no hook. `/practice start` replaces a
finished fight; `/practice stop` clears the channel (the person who started it, or an admin). The walkthrough counts
`/practice start` and `/practice bot` as its battle step and `/practice stop` as its cleanup step.

**`/practice bot`** swaps the Dummy for a random bot (`game/practice_bot.py`, no Discord imports). `generate_bot` makes
an ordinary unsaved `Character` with the Build Points, Skill Points and Passive Points it is given, so the cog can copy
the player's own pools (or use `level`, `skill_points` and `passive_points`) and the bot follows the same rules. By default
`spend` caps its Build Points at what the player has spent (`spent_of`, or `starting_spent` for a Trainee), so a new player
meets a new build; giving `level` drops the cap and the bot spends its whole pool. Stats
are bought one random step at a time and a step is kept only if `check_build` still accepts the build. Skills are made
to fit the bot's Power, status ranks and Skill Points, and passives to fit its Passive Points. The fight uses
`Fighter.from_character` with `owner_id` set to None, so only the cog declares for it. Options: `character`, `level`
(1 to 5, blank means the bot spends only as many Build Points as the player has), `skill_points`, `passive_points`, `skills` and `passives` (Random, or None to add
them by hand), `attacks` (1 to 3 moves a round, blank means every slot) and `show_plan`.

A bot started with no skills or passives is set up after it exists. `/practice addskill` opens the normal skill form
with the bot's `Character` as its target, so the same Skill Point, status rank, Power and archetype limits apply
(a practice flag swaps the admin hints for ones that make sense here, and keeps the skill out of the admin log).
`/practice addpassive` takes the same options as `/charadmin passive` (apart from the flavour text, which a throwaway practice opponent does not need) and is checked against the bot's Passive Points.
`/practice removeskill`, `/practice removepassive` and `/practice sheet` do what they say. Only whoever started the
fight, or an admin, can change the bot (admins skip the Power, rank and archetype checks, as they do for characters; the Build Point and Skill Point checks still apply). After any change the
live fighter is rebuilt from the `Character` (`_sync_bot`) and `_check` plans again. Limits worth knowing: the bot only
plays attack skills (no Guard, Evade, Counter or Supermoves), so a Level 4 or 5 bot cannot spend every Build Point and
its summary says how many are left; a bot with no skills makes `/battle combat` refuse until it has one, and the cog
says so once per round.

### `/help`

Player-facing guidebook, not a dev reference (that's this README).
Drill-down menu: pick a category button, then a topic from a dropdown,
then read the full explanation; Back/Home navigate without re-running the
command. Ephemeral, ~5 minute timeout on the buttons. Content lives in
`game/help_content.py`, written in plain language on purpose, kept
separate from this file. **Five categories exist so far**: **Skill Tags**
(one dropdown entry per flag, including Duality and Supermove), **Damage Math** (the basic
hit formula, resistance, Stagger's multiplier, Rupture vs. Crit, Attack
Weight splash, Parts and Core) and **Status Effects** (21 topics: Potency and Count, the
five Sin statuses, Poise, Charge, every debuff and buff, Low Morale and
Panic, custom statuses; each entry key is the status key so its icon comes
from `STATUS_EMOJI_IDS` automatically) and **Trigger Syntax** (10 topics: line
shape, skill and coin timings, conditions, number effects, Inflict/Gain/Remove,
Consume and scaling, allies, hint tiers, worked examples) and **Commands** (9 topics: the order of a battle, battle
setup, declaring and fighting, battle skills, admin tools, characters,
character skills and statuses, `/character say`, `/roll` and `/help`). A new
category is a new entry list in `game/help_content.py` plus one new line in
`HELP_CATEGORIES`, no changes needed to `cogs/help.py` itself.
The Trigger Syntax and Commands text is hand-written, so when a timing,
condition, effect phrase or slash command is added or renamed it has to be
updated there too.

Help pages show icons inline: `decorate_text()` in `game/emojis.py` puts a
status, stat or damage-type icon in front of its first mention in each page
(skipping anything inside `code`, and any name with no emoji ID yet). Dropdown
descriptions can't show custom emoji, so they stay plain.

Category buttons (`Skill Tags`, `Damage Math`, `Status Effects`, `Trigger Syntax`, `Commands`) and five of
the ten skill tags (`Target Fixed`, `Unclashable`, `Indiscriminate`, `Supermove`) have no
custom emoji yet (`HELP_CATEGORY_EMOJI_IDS` / `SKILL_TAG_EMOJI_IDS` in
`game/emojis.py`, both `None` placeholders) — same gradual-fill
convention as every other icon in that file. The other five tags already
reuse an existing uploaded icon (Counter/Clashable Counter/Guard/
Clashable Guard/Evade all have one from their own mechanic). Unlike
everywhere else that convention is used, a `None` here is never passed
into a Button/SelectOption's own `emoji=` kwarg — that field needs a real
emoji object, not display text, so `cogs/help.py` only uses the
plain-text-safe `emoji_tag()` output in embed bodies, and builds its own
`discord.PartialEmoji` directly from the ID dicts (skipping the kwarg
entirely while still `None`) wherever a real icon slot is needed.

## Trigger Syntax (`game/conditions.py`)

One line per Trigger, pasted into the `addskill` popup (`/battle addskill`
for a one-off battle fighter, `/character addskill` to teach it permanently
— both use the same form). Blank lines ignored.

```
[<Timing>] <optional condition,> <effect>
:Coin<N>: [<Timing>] <optional condition,> <effect>     # per-coin timings only
[<Flag>]                                                 # skill-metadata flag, own line
```

**Skill-level timings** (no `:CoinN:` prefix): `Turn Start`, `Combat Start`,
`Turn End`, `Before Use`, `On Use`, `Clash Start`, `Clash Win`, `Clash Lose`,
`Before Attack`, `On Unopposed Attack`, `Attack End`, `On Evade`, `Before
Getting Hit`, `On Stagger`, `On Kill`, `On Crit Kill`, `Failed Hit` (evaded or 0
damage), `Failed Kill` (landed, target still up), `Hit After Clash
Lose`, `Tremor Burst`. Check `SKILL_LEVEL_TIMINGS` in `game/conditions.py`
directly if this list and the code ever drift, the code is the source of truth.

**Reaction timings** (skill-level, swept over the owner's skills and
passives): `Ally On Hit`, `Ally On Crit`, `Ally On Kill`, `Ally Is Hit`, `Ally On
Stagger`, `Ally Turn Start`, `Ally Combat Start`, `Ally Before Getting Hit` (fires
before an unopposed attack on an ally, used with Assist Defense). Passive-only:
`Self Is Hit`, `Self On Stagger`, `Self On Kill`, `Self On Death` (fires when a landed
hit kills the owner, with the killer as the target. A Burn, Poison or Bleed
death fires it too, with no target, so lines aimed at the target do nothing and
lines on the owner or its allies still work).

**Per-coin timings** (need `:CoinN:` prefix): `Coin Start`, `On Hit`, `Heads
Hit`, `Tails Hit`, `Hit After Clash Win`, `Current Coin Attack End`, `Heads
Attack End`, `Tails Attack End`, `On Crit`, `On Crit - Heads Hit`, `On Crit -
Tails Hit`.

**Effects and notes.** `Deal +N damage`, `Deal +N% damage` (1 to 50 per line,
`MAX_DAMAGE_PCT`), `Coin Power +N`, `Gain N Sanity`, `Lose N Sanity`, Heal,
Inflict, Gain, Remove, `Consume`. The three power effects (`POWER_EFFECTS` in
`game/conditions.py`) change the hit itself, so the engine only reads them at
`Before Use`, `On Use`, `Clash Start` and `Before Attack` (`POWER_TIMINGS`),
plus `Coin Start` for the two flat ones. At any other timing they never did
anything, so the parser now refuses the line and says where it does work. A
percent is not its own multiplier: it joins the same additive pile as Damage
Up and Offense Level (`_damage_mods`). The only trailing notes understood are
`(Hint N)`, `(once per turn)` / `(N times per turn)`, `(once per Encounter)` /
`(N times per Encounter)` (resets each battle), `(N% chance)` (1 to 99) and
`(max N)` / `(max N%)` on a scaled line. Any other bracket note is refused,
because the old parser dropped it silently and the player believed it was
enforced. `pay_cost` checks the limits and rolls the chance each time the line
would fire; a miss spends nothing. Passives saved before these checks are read
back with `strict=False`, so they keep loading.

**Conditions** (`evaluate_condition`): target or caster status, HP percent,
Sanity, Speed (`faster`, `at least N`, `diff`), Morale, ally or enemy target,
first hit of the round, Weak or Fatal affinity (needs the skill, uses
fighter-wide resistances), count of negative statuses, target is defending,
ally rank, `If an ally is adjacent,` / `If 2+ allies are adjacent,` (neighbours in
deployment order), `If N+ units on your side have the X tag,`, `If the target has the X
tag,` and `If N+ allies are defeated,`. The full list with examples is in
`/help` and `CONDITION_EXAMPLES` in `game/trigger_text.py`.

**More effects:** `Clash Power +N`, `Final Power +N`, `Gain N Shield`, `Gain N%
max HP Shield`, `Gain Offense Level +N`, `Inflict Defense Level -N`, a trailing
`next turn`, and the ally forms `... to the ally with the lowest HP` /
`If the ally has the fastest Speed,` (selectors: Speed, HP, HP percent, Max HP,
Sanity, most or least of a status, earliest or latest deployed; never the
caster), or `to the adjacent allies` for each living neighbour. Survival effects keep a fighter up (HP floor, survive one lethal hit,
no Stagger this turn, recover from Stagger). Follow-ups name an owned plain
attack skill and need a per-turn or per-Encounter limit; Assist Defense takes
the hit for an ally (unopposed attacks only, as a normal hit). Exact wording
for each is in `/help` and `docs/ENGINEERING_NOTES.md`.

`UNSUPPORTED_TIMINGS` is currently empty: every recognized timing has real
dispatch. New timing names without wired dispatch should go here with a
reason string; that's the "parsed but not built yet" convention. `Before
Getting Hit` fires on a `[Counter]` skill right after it redirects and lands
its retaliation strike. `Tremor Burst` carries its own built-in payload
(raises the target's Stagger thresholds by Tremor's own Potency) on top of
whatever generic effect its own Trigger line writes — see **Status
Effects** above.

**Self-buff resources** (`SELF_BUFF_STATUSES`): just `Poise`, `Charge` now
— the only things `Gain N <X>` / `At N+ <X>` recognize as a caster-held
resource; anything else is rejected with a clear message. Both `Counter`
and `Evasion`/`Evade` used to be in this list; both are skill-flags now, not
statuses. `Gain N Evasion` is no longer valid trigger text for this reason —
it now falls through to the same "not a tracked resource" rejection any
other unrecognized name would get.

**Skill-flag tags** (`SKILL_FLAG_TAGS`, own line, never `:CoinN:`-prefixed):
`Target Fixed`, `Unclashable`, `Indiscriminate`, `Counter`, `Clashable
Counter`, `Guard`, `Clashable Guard`, `Evade`, `Duality`, `Supermove`,
`Unfocused Volley` (coin 1 hits the main target, other coins pick random living
enemies; unopposed only). All are enforced, including `Evade` (added when Evade was rebuilt from a status
into a declared skill, see Combat Features). Note: `Indiscriminate` makes
`/battle declare` pick a random enemy slot, but same-side targeting is still
blocked, so it doesn't yet let a skill hit allies.
`[Guard]`/`[Evade]`/`[Clashable Guard]` are enforced to exactly 1 coin at
`/battle addskill` time.

## Known Gaps

- Counter/Clashable Counter/Clashable Guard's animated reveal (coin faces,
  power ramp) uses the same generic `animate_damage`/`format_skill_result`
  path as a normal attack; no distinct visual treatment for "this was an
  interception," you have to read the header/log text to tell. Purely
  cosmetic, not a logic gap.
- `Combat Start`/`Turn Start` fire via a full sweep of a fighter's entire
  known skill list each round (`fire_passive_triggers`), not tied to what's
  declared. `On Evade` works the same way (`fire_evade_triggers`). `Before
  Getting Hit` is different: fires only on the one `[Counter]` skill that
  actually redirected and landed, not a sweep. Deliberate, not a gap.
- Combat reveal pacing is set by `MIN_EDIT_GAP` (Discord allows about 5 edits
  per 5 seconds on one message). It was checked in a mocked run, not on a real
  server, so the feel is untuned; a busy round still takes a while.
- A Staggered fighter cannot declare (`/battle declare` blocks them), and
  `/battle combat` no longer waits for them (`Fighter.can_act` is false for a
  downed, Staggered or Immobilized fighter, and `Battle.all_declared` skips
  them). Their resistances still don't flip to a Fatal-style override; only
  the flat tier-multiplier applies to damage they take.

## Design Divergences From Canon Limbus, and Where This Is Headed

Cross-referenced against the [Limbus wiki's Battles
page](https://limbuscompany.wiki.gg/wiki/Battles), deliberately skipping
Deployment Order, Offense/Defense Levels, Unbreakable/Excision Coins,
Durante, and Backups, none of those fit what this bot is for.

**Deliberately different from canon, not gaps:**

- **Sin Affinity → Status Resistance.** Canon stacks Sin Affinity +
  Physical Type additively. This server uses only the five status-resistance
  types (Burn/Bleed/Tremor/Rupture/Sinking, `ALL_RESISTANCE_TYPES` in
  `game/resistances.py`), one resistance value per hit, never two stacked.
- **E.G.O → Supermoves.** Built, see Status Effects above. Setting is an MHA x Project Moon crossover, so
  E.G.O Skills are Supermoves. No E.G.O resource system or Overclocking is
  ported. Still open: the exact numbers (they are first guesses, tune them in
  `game/supermoves.py`) and Build Point costs for owning one.
- **Corrosion**: not implemented, not planned near-term.
- **No Skill Deck / pull system.** `/battle declare` picking a known skill
  directly is the intended design, not a placeholder.
- **Evade is a single win/lose roll per attack, not a persistent dodge
  state**, unlike canon (where a clean Evade can stay active). Evade is a
  declared, slot-specific, 1-coin Skill now (rebuilt from a Count-based
  resource — see Combat Features); each
  incoming hit against its watched slot gets exactly one coin toss decided
  by the defender's own `heads_chance()`, win or lose, no persistence.
  Deliberate, not an oversight.
- **Resistance is a flat decimal multiplier, not canon's asymmetric
  formula.** Every resistance value is read directly as "how much damage
  this type/status deals to this fighter": 1.0x is Normal, 0.75x takes 25%
  less, 1.25x takes 25% more (`apply_resistance` in `game/resistances.py`
  is a straight `value * multiplier`, no special-casing between resist and
  weak). Values are stepped in fixed 0.25 increments (`RESISTANCE_STEP`),
  enforced by `parse_resistance_multiplier` at every command that sets one.
  0x is a real, reachable Immune, unlike the old percent system where no
  finite value ever reached true 0 damage. Characters saved before this
  switch get their old percent value one-time converted to the closest
  0.25 step (`migrate_percent_to_multiplier`) the first time they're
  loaded, so they don't silently change behavior.

**Actually on the roadmap, not built yet, rough priority order:**

1. **`[Failed ...]` trigger prefix**: done as the timings `[Failed Hit]` and
   `[Failed Kill]`.
2. **`[Ally ...]` trigger prefix**: done (support skills, `to the ally`
   effects and ally reaction timings, also for Attack Weight splash hits).
3. **More non-Sin statuses**: Offense/Defense Level and Clash Power exist as
   one-turn trigger effects. Still open: Clash Power Down, Coin Drop/Boost,
   HP Healing Down and Curse as real statuses. Bind, Fragile, type Fragility,
   Power Down, Damage Down, Paralyze, Poison and Immobilized are done.
4. **Charge's consumption payoff**: done. Charge can be spent with a
   `Consume` trigger, see Status Effects above.
5. **Panic / Low Morale**: done, see Status Effects above. Still open: a
   customization layer like canon's Panic Type Changing Effects is just
   trigger lines written against the new `If this unit is Low Morale,` and
   `If this unit is Panicked,` conditions.
6. **Parts / Core**: done, see Combat Features. Part-specific Stagger is done. Still open: auto-breaking a Part's declared attack mid-round, and storing Parts on a saved NPC character.
7. **A worked-examples guide**: done, see `docs/WORKED_EXAMPLES.md`. Every
   example in it was run through the parser.

**Confirmed already true, not a gap:** Sanity can never be set nonzero at
battle start through any normal command: `Fighter.sanity` defaults to 0 and
`/battle addfighter` has no Sanity parameter. The only way Sanity differs
from 0 at Round 1 start is a passive that explicitly says so (via
`fire_passive_triggers`), never a flat override. `/battle setstatus` remains
a deliberate admin/testing escape hatch.

Emoji IDs for Shield, Panic, and Low Morale (Panic/Low Morale share one icon)
are filled in (`STATUS_EMOJI_IDS`, `game/emojis.py`) even though Panic/Low
Morale's own mechanics aren't built yet. `guard`, `shield`, `clashable_guard`,
and `tremor_burst` all have real emoji IDs filled in too (none are `None`
anymore) — all four mechanics are fully built (see Combat Features / Status
Effects above). Their stale "not wired to a mechanic yet" inline comments in
`game/emojis.py`, flagged here as worth fixing next time that file got
touched, got fixed the next time it actually was (adding `/help`'s category
icons).

## Character Creation & Progression (Level 1)

A Level 1 character is an MHA hero-university student with no hero license
yet. The rules below are enforced by the bot (`game/build_points.py`,
`game/supermoves.py`; `/character view` shows the budget). Only admins change
anything that costs points for now; the player request flow is not built.

**Three pools.** Build Points (BP, start 30) pay for stats. Skill Points (SP,
start 0, granted by admins) pay for extra trigger lines (1 SP each after the
first line per skill). Passive Points (PP, granted by admins for story moments,
at most 2 per grant) pay for identity passives (price 1 to 6) and new unique
statuses. A build over any pool is blocked from battle until fixed.

**Levels** raise the BP pool: Level 1 at 30, 2 at 45, 3 at 63, 4 at 84, 5 at
108. The higher level in a clash adds the gap to its Power each round.

**Stats:** HP 100 to 160 and Power 6 to 12 (each step 0 / 1 / 2 / 4 / 4 / 6 / 8
BP in total), Speed 1 to 8 with a range at least 2 wide, Offense and Defense
Level 0 to 5 (4% damage per step). Caps: no single stat above 40% of the pool,
Power + Speed and Power + HP each at most 60%. Skill Power is capped by
Power: Max Base = floor(2 + 0.4 x Power), Max Coin = floor(1 + 0.2 x Power).

**Stagger tier trade-off**: all 3 checkpoints are active by default. Tier 1
is permanent, can never be paid off. Tiers 2/3 can be stripped with Build
Points (5 and 7): removing Tier 3 means never eating its 2.5x penalty again;
removing both leaves only Tier 1 in any fight. Duration is the other half:
Tier 1 clears end of the same Turn; Tiers 2/3, if kept, persist through one
extra Turn.

**Resistances** are part of the build (admin only): a worse resistance
refunds BP, a better one costs BP, and the total refund is capped at 18.

**Status ranks and archetypes.** A character owns up to 2 ranked statuses
(Burn, Bleed, Tremor, Rupture, Sinking, Poise, Charge) at Rank 1 to 5, which
caps what a normal skill can apply. Up to 2 defense archetypes (Guard,
Evade, Counter, Clashable Guard, Clashable Counter); Evade and the counters
need a lowest Speed of 4, the clashable ones need Stagger Tier 2 removed.
Non-keyword effects (Bind, Fragile, Protection and so on) cost
ceil(size / 2) BP each, at most 3 BP for a whole kit. Supermoves are priced
by expected damage and offset by admin-set drawbacks. Skills saved before a
rule existed are grandfathered; only new or edited skills are checked.

**Sanity, in combat** (matches actual code in `game/battle.py`/
`cogs/battle.py`; if these ever drift, the code is the bug):

| Event | Change |
| --- | --- |
| Win a Clash | +2 SP per coin in the winning skill |
| Unopposed attack | +2 SP per coin that flips Heads, +0 for Tails |
| Lose a Clash | -3 SP flat, ignores the floor (can push positive to negative) |
| Turn end, positive | -4 SP, floored at 0 |
| Turn end, negative | +2 SP, capped at 0 (recovery slower than falling) |

**Pacing**: 4–6 Turns minimum for a spar-weight fight, longer for anything
serious.

**Point economy**: Build Points earned via literacy training (weekly caps)
and event participation, not handed out freely.

**Still open**: the player request and approval flow, and Reuse Coin (the
bot has no such mechanic).

## Character Builder Website (`docs/builder/`)

A static web page (plain HTML, CSS and JavaScript, no build step, nothing sent anywhere) where a player sets up a whole character and sees
the Build Point cost as they go, then copies a code and pastes it into `/character import`. It is the "identity card" side of making a
character: name, portrait link, stats, resistances, status ranks, defense types, tags, passives and skills.

- **The code** is the character as JSON, compressed and written as text (`LUGIC1Z.` then base64url; `LUGIC1.` is the same without compression for a
  browser that cannot compress). `game/character_import.py` reads it, caps its size (it cannot unpack into more than 60 KB), and checks the shape.
  The site and the bot never talk to each other, so there is nothing to host on the bot's side.
- **What the bot does with it** (`cogs/character_import.py`): the whole build is checked first with the bot's own rules, on a copy, and a build
  that would fail creates nothing and says why. A build that only has skills creates the character at once. Anything else creates the character
  with the starting stats and no skills, and files one `build` request; approving it applies everything. It only makes new characters.
- **Rules on the site** (`docs/builder/engine.js`) are a port of `check_build` and of the skill popup's Power, rank and defense-type rules, with the
  same wording. The prices come from `docs/builder/rules.js`, which is written by `python -m game.builder_rules` from the constants in
  `game/build_points.py`, so changing a price means running that one command. The site cannot know whether a trigger line parses or what it costs;
  the bot checks those on import and the player is told then.
- **Putting it online (GitHub Pages):** the repository must be public on a free plan. On GitHub open Settings, Pages, set the source to the `main`
  branch and the `/docs` folder. The page is then at `https://<owner>.github.io/<repo>/builder/` (`docs/index.html` forwards the root to it, and
  `docs/.nojekyll` stops GitHub from converting the Markdown files). To have the bot tell players where it is, add
  `CHARACTER_SITE_URL=https://<owner>.github.io/<repo>/builder/` to `.env`.
- **Trying it locally:** run `python -m http.server` in `docs/` and open `/builder/`. (Opening `index.html` straight from disk works, but the icons below only load over http.)
- **Look:** one font family everywhere, the code box included: the wiki's own. The wiki sets its status names, skill names and descriptions in **Mikodacs** (by gluk, SIL OFL) and the rest of its page in **Nunito**, so the page asks for Mikodacs first, then Nunito, then the system font (`--font` in `style.css`). Nunito ships in `docs/builder/fonts/` (two `.woff2` files and the licence). **Mikodacs does not ship yet**: the file could not be fetched from the build sandbox, so put it at `docs/builder/fonts/mikodacs.ttf` with its licence file next to it (see `docs/builder/fonts/README.txt`); until it is there the whole page shows Nunito and nothing breaks. The wiki sets Mikodacs 10% larger than the text round it, so the font face has `size-adjust: 110%`. Bold text is white (`--bold`), not cream; names that carry a tone colour keep it. There is no page header: the tab bar starts the page and carries the stripe of the seven Sin colours, and "Load a code" and "Start over" sit under the code in the summary card. The colours are variables at the top of `style.css` and are the ones the Limbus wiki uses, read from the inline styles of wiki pages: positive `#fac400`, negative `#e20000`, neutral `#9f6a3a`, text cream `#eecea4`, skill bar `#810000`, `[On Use]` `#26cfff`, every other timing green `#94f140`, `[Clash Win]` `#ff7e00`, `[Clash Lose]` `#e30000`. The whole page uses them (buttons, tabs, panels, tags), not only the skill preview. Each Sin has a dark and a light colour for the name banner and the frame ring (Envy is a guess: the sampled wiki pages had no Envy skill). A stripe of the seven Sin colours sits under the header. Change the palette in `:root`; `WikiLook` in `tests/test_builder_site.py` checks the values.
- **Tabs:** every part of the form is its own tab: Identity, Your points, Stats, Resistances, Status ranks, Defense types, Group tags, Skills, Passives, Status effects. Click one, use the arrow keys, Home and End, or the Back and Next buttons under each part. The page remembers the tab you were on (browser session) and `#sec-skills` in the address opens that tab. A red dot on Skills or Passives means something there needs fixing. On a phone the summary card moves below the form (the bar at the bottom jumps to it).
- **Skills:** each skill has a live preview laid out like a skill on the Limbus wiki: the name banner in the Sin's colours, the coins, the damage type, `[On Use]`-style tags coloured the wiki's way (blue On Use, orange Clash Win, red Clash Lose, green for every other timing, brown underlined skill-type tags), status words coloured by whether the effect is positive (yellow), negative (red) or neutral (brown) with their icon, and a numbered row per coin (a `:Coin2:` line lands on coin 2). `preview.js` makes it, and `tests/preview_check.js` plus `tests/test_builder_site.py` check it. "Add from an example" and "Copy" make a skill quicker; the examples live in `SKILL_EXAMPLES` in `game/builder_rules.py`.
- **Rule lines are picked, not typed:** under each skill (and passive) a list shows its rule lines, and "Add a rule line" builds a new one in four steps: *When* it fires (all 43 timings, grouped, with a coin picker for the per-coin ones; passives only see the timings they react to), an optional *If* (18 conditions with their numbers, statuses and tags as inputs), *What it does* (30 effects: damage and power, statuses, Offense/Defense Level, Sanity, HP and Shield, survival, follow-ups) and *Extras* (aimed at an ally, wait until next turn, how often, chance). The page shows the finished line, or says why the choices do not fit (for example Deal +N% damage only works at Before Use, On Use, Clash Start and Before Attack). Typing the lines is still there under "Type or paste the lines as text instead". The choices come from `game/line_spec.py` (written into `rules.js`), the line is built by `docs/builder/lines.js`, and `tests/test_line_picker.py` feeds every combination to the bot's own parser both ways: a line the picker offers must parse, and a pair the picker refuses must be one the bot refuses. The skill tag buttons now include `[Unclashable]`, `[Indiscriminate]` and `[Unfocused Volley]`.
- **Coloured lists:** the picker's lists (when, only if, what it does, the status choices, the coin status list) are a small custom dropdown instead of the browser's own, so each choice can carry a colour, an icon and a hover box. Timings show in their tag colour (the wiki's: On Use blue, Clash Win orange, Clash Lose red, the rest green), statuses and effects show yellow when they help, red when they hurt and brown when they are neutral, and pointing at a choice (or moving to it with the arrow keys) shows its definition beside the list. It works with the keyboard (Enter, Space, arrows, Home, End, Page Up and Down, typing the first letters, Escape), opens upward when there is no room below, and is a plain tap list on a phone. `choice()` in `app.js` is the component; the colours are `.ch-*` and `.pv-*` in `style.css`.
- **Skill frames:** the emblem beside a skill uses a frame from `docs/builder/borders/`. By default the page picks it: the Sin comes from the status the skill leans on most (Burn Wrath, Bleed Lust, Tremor Sloth, Rupture Gluttony, Sinking Gloom, Poise Pride, Charge Envy; coin statuses weigh by potency and count, mentions in trigger lines by one each), a Guard, Evade or Counter tag gives the defense frame, and the tier 1 to 3 comes from the number of coins (1 or 2, 3, 4). A tier with no image yet uses the best tier below it (Sloth has two so far), a Sin with no image at all would get a plain ring in its colour, and a skill with no status and no defense tag gets a grey ring. **Skill border** (under the picture buttons) lets the player pick the frame by hand instead: a grid of every image (Defense 1, Wrath 1 to 3, Lust 1 to 3, Sloth 1 to 2, Gluttony 1 to 3, Gloom 1 to 3, Pride 1 to 3, Envy 1 to 3), a plain ring, and Automatic to go back. The banner and bar colours follow the picked Sin. Like the picture it is only in the preview, lives in this browser (`lugic-character-builder-frames-v1`, one slot per skill) and is **not** part of the code, because the bot has no field for it. Drop a new `<sin><tier>.png` (such as `wrath1.png`) in `borders/` and add its tier to `FRAME_TIERS` in `preview.js`; a test fails if the folder and the list disagree, and the picker grows by itself.
- **Skill pictures:** "Add a picture" under a skill's preview opens an adjuster as soon as the file is chosen: drag the picture to move it, scroll or use the slider to zoom, arrow keys and plus/minus work too. The picture sits in the frame's opening (clipped to its heptagon) and is shrunk to 640 px in the browser. It lives only in that browser (`localStorage`, one slot per skill, kept beside the saved build) and is **not** part of the code, so the bot never sees it; loading a code keeps the pictures of skills with the same name. `preview.js` holds the maths (`clampArt`, `artStyle`, tested in `tests/test_builder_site.py`); `app.js` holds the upload and the adjuster.
- **Passive preview and flavour text:** each passive shows a preview laid out like a passive on the wiki: a Support Passive label bar when it is one, the brown name plate, the price in Passive Points, the rule lines (timings and statuses coloured and pointable like everywhere else) and, when the player wrote one, a **flavour text** in the wiki's brown italics (`#a16a3b`) at the bottom, the line the wiki uses for the story behind a passive. The text box under the rule lines is optional (up to 400 letters; each line written is one paragraph, blank lines and spaces around lines are dropped). **The flavour text is part of the character, not only of the site:** it goes into the builder code (`flavour` on the passive, left out when empty, so codes of passives without one are unchanged and a longer story makes a longer code), `/character import` reads it (a text longer than 400 letters after tidying is refused, a non-text value is refused) and keeps it on the passive (`Character.passives[i]["flavour"]`, only present when there is one), the admin's review text shows it (`Flavour text: ...`), and it is saved with the build in the browser and comes back with a loaded code. `/charadmin passive` and `/request passive` have a new optional `flavour` option for the same thing, and the approved request stores it on the passive. **In Discord** the `/character` sheet shows each story in italics right under its passive's rule line (`passives_text` in `cogs/character.py`; markdown characters in the story are escaped so they cannot break the italics). The Passives field is limited to 1000 letters, so the rule lines are always written in full first and the room that is left is shared between the passives that have a story (a short story uses what it needs and leaves the rest for the others); a story that does not fit is cut with `…`. `/practice addpassive` has no flavour option, since the practice opponent is not kept. The cleaning is the same on both sides (`tidy_flavour`/`clean_flavour` in `game/passives.py`, `cleanFlavour` in `engine.js`, and the limit `passiveFlavour` in `rules.js`); a test feeds both the same texts. `passiveModel`, `flavourOf` and `renderPassive` in `preview.js` are the data and the drawing; tests are in `tests/test_builder_site.py` (site and parity) and `tests/test_passive_flavour.py` (bot).
- **Background:** `docs/builder/bg.jpg` is the page picture, already darkened and blurred (it is a 1600 px copy, so the page does not filter a big image while scrolling); a dark panel sits behind the form so text stays readable.
- **Status list and colour key:** the last tab lists every status in the same three short slots, so the pattern can be learned: **When** it acts, what it **Does**, **Then** what happens to it (for example Burn: Turn end, Take Potency damage, Count −1). Keyword statuses are one table (with a Sin chip and its icon); opposites sit side by side in a second one (Power Up and Power Down, Haste and Bind, Damage Up and Damage Down, Protection and Fragile, with the three Fragility types under Fragile) and are all gone at turn end; Paralyze, Poison, Immobilized and Panic are a third. On a phone each row becomes a small card with the same labels. Above them a colour key is three cards and nothing else: yellow (helps you), red (hurts you), brown (neither), as on the wiki. There are no Sin colour cards and no explanation lines; each keyword status keeps its small Sin chip. The short text is `_QUICK` in `game/builder_rules.py` (also `_PAIRS`, `_COLOR_KEY`, and `_GLOSSARY` for the hover text), and the bot's full `/help` wording is one fold-out at the end. `StatusShortForm` in `tests/test_builder_site.py` keeps each slot under 48 characters and fails if a number in the short form is not in the full text. Charge is the one neutral status in the list (a guess from earlier wiki reading, not seen on the sampled pages). The key also shows the resistance words in the wiki's colours: Fatal red, Weak pink, Normal cream, Endure grey, Ineffective dark grey.
- **Icons:** the page can show the bot's own emoji next to statuses, HP and Speed. Run `python -m game.emoji_icons` once from a machine with internet access. It downloads every emoji in `game/emojis.py` into `docs/builder/icons/` and writes `icons/index.json`; commit that folder. Without it the page shows text only. Run it again after uploading new emoji. `tests/test_emoji_icons.py` covers the collecting and saving.
- **Wiki images:** the pages the owner shared use the game's own icons (the seven Sin symbols, Slash, Pierce, Blunt, HP, Speed, coins, tiers, and an icon for every status). The page links them straight from the wiki, so nothing has to be downloaded: `rules.js` carries the list of addresses (`wikiImages`, made from `ASSETS` in `game/wiki_assets.py`). Each icon is tried in this order: a copy saved beside the page, the wiki's address, then the bot's emoji. A picture that fails to load falls through to the next, and if the wiki refuses even one, linking is switched off for that visit and the emoji are used. Images are asked for without a referrer, and the footer says the page fetches them (the wiki sees the visitor's address, nothing entered on the page). The linked icons are Project Moon's game art hosted by the wiki. If you would rather not depend on the wiki's servers, run `python -m game.wiki_assets` once from a machine with internet access: it saves each one as `docs/builder/wiki/<name>.png`, writes `wiki/index.json` and `wiki/CREDITS.txt`, and skips files it already has; commit that folder and the page uses the saved copies first (faster, works offline). The sandbox this was written in cannot reach wiki.gg, so the addresses were taken from the pages' image tags and tested here only with a stand-in server (both "wiki serves" and "wiki refuses" cases). `tests/test_wiki_assets.py` covers the list, the addresses, saving, and refusing a page that is not a picture.
- **Portrait:** by default the picture is shown whole, in its own shape. The **size** choice on the Identity tab (Small 40%, Medium 60%, Large 80%, Full 100% of the picture frame) only resizes it: nothing is cropped, zoomed, stretched or capped in height, and the frame has a little padding so even Full shows every corner. To really adjust the picture there is a second control under it: a **shape** (Original, Square, Tall 3:4, Wide 4:3, Banner 16:9) and a **Move and zoom** stage: drag the picture, scroll or use the slider, or use the arrow keys and plus/minus; the card shows the same thing as the stage. Reset puts the position and zoom back to the middle (pick Original to see the whole picture again). The maths is the same `clampArt`/`artStyle` the skill pictures use (with the opening's own shape passed in), so the picture can never be dragged off its frame, and with Original and zoom 1 it is exactly the whole picture. A new picture starts from the middle. The size, the shape and the placement are kept in this browser only (`lugic-character-builder-portrait-v1`, `...-portrait-fit-v1`), so none of it is in the code and the bot is not told. The Lv label sits under the picture, not on it.
- **Rule line colours:** in a rule line (passives, and the lines of a skill) only the words that help or hurt are coloured, the way the wiki does it: a status takes the colour of its tone (`Gain 2` stays plain, `Protection` is yellow), and a phrase with no status to carry it takes its own tone: `Heal 5 HP`, `Deal +10% damage`, `Coin Power +2`, `Gain 5 Sanity`, `Gain 5 Shield` and the other helpful effects are yellow, `Lose 5 Sanity` and `Inflict Defense Level -2` are red, and in a condition `negative effects` and `Low Morale` are red. Which effect has which tone is each effect's `tone` in `game/line_spec.py`; `preview.js` builds its patterns from the effect's own wording, so a new effect only needs its tone. Nothing else in a line is yellow. In the rule-line picker the same tones colour the choices (the effect list, the two conditions, and every status list), in the list and once chosen.
- **Definitions on hover:** the box is laid out like the wiki's own: black with a brown edge, the name in large type with its icon, then one `- line` for each thing it does, cream text. It shows for every status (in the skill preview, in the status lists, in the checking messages and notes, on the status chips and rank names), every timing tag, Slash, Pierce, Blunt, HP, Speed, Sanity, Offense Level, Defense Level and every skill-type tag such as `[Counter]`. It shows when the pointer rests on the word, when it has keyboard focus, or when it is tapped (Escape or a tap elsewhere closes it). A status uses the bot's own text for it (the same text as the status list, cut into one line per sentence, so the box and the bot cannot disagree), a timing uses the picker's help line, and the rest read `_GLOSSARY` in `game/builder_rules.py` (numbers from the bot's constants; `Glossary` in `tests/test_builder_site.py` checks each one against the bot's `/help` text). The hover box can also carry the wiki's italic brown flavour line (`flavour`), but the bot's statuses have none, so none is shown; the flavour text a player can add to a passive is shown in the passive's own preview and on the Discord sheet (see **Passive preview and flavour text**). `LugicPreview.definition()` in `preview.js` is the data, the box is `showTip()` in `app.js`; a word with no definition is not made pointable. **Running text is coloured too:** the checking messages, the notes and the picker's help run through `LugicPreview.renderProse`, which colours every timing the wiki's way (On Use blue, Clash Win orange, Clash Lose red, every other timing green, bracketed or not) and every status by whether it helps or hurts, and makes each one pointable. Only exact names are found (a lower-case word or a longer word such as Burning is left alone).
- **Tests:** `tests/test_builder_site.py` runs the site's engine under node and the bot's Python on 400 random builds and compares prices and
  problems exactly, checks codes both ways, that every "add a line" button parses, and that `rules.js` is current. `tests/test_character_import.py`
  covers the bot side. Not covered: how the page looks and behaves in other browsers, and a real Discord client.

## Project Structure

```
├── main.py            # Bot entry point; loads cogs, starts connection, syncs command tree on_ready
├── cogs/               # Discord-facing commands, grouped by feature
│   ├── battle.py        # /battle group -- largest file, combat engine glue lives here
│   ├── character.py     # /character group -- persistent character CRUD
│   ├── character_import.py  # /character import: makes a character from a builder site code
│   ├── build_check.py   # runs a whole build (stats, ranks, skills) through the bot's own rules
│   ├── help.py          # /help -- player-facing guidebook (drill-down menu)
│   ├── tutorial.py      # /startingoff walkthrough and first-use tips
│   ├── practice.py      # /practice: solo fight against a bot-controlled Dummy or random bot
│   └── roll.py          # /roll -- standalone dice roller
├── game/               # Game logic, no Discord imports here
│   ├── battle.py         # Fighter, Battle, DeclaredAction, BATTLE_TYPES
│   ├── skills.py          # Skill, SkillResult, resolve_skill, resolve_round_clash, resolve_triggers
│   ├── conditions.py      # Trigger, TriggerContext, parse_trigger_text -- the trigger syntax parser
│   ├── character.py       # Character (persistent, JSON-backed), separate from per-battle Fighter
│   ├── character_import.py # reads the builder site's code into a build request, no Discord imports
│   ├── builder_rules.py   # writes docs/builder/rules.js from the bot's rule constants
│   ├── line_spec.py       # the timings, conditions and effects the site's rule-line picker offers
│   ├── emoji_icons.py     # downloads the bot's emoji as images for the builder site (python -m game.emoji_icons)
│   ├── wiki_assets.py     # downloads the wiki's game icons for the builder site (python -m game.wiki_assets)
│   ├── statuses.py        # StatusInstance, apply_status, decay_after_trigger
│   ├── resistances.py     # DAMAGE_TYPES, ALL_RESISTANCE_TYPES, apply_resistance
│   ├── emojis.py          # Every custom Discord emoji ID this bot uses
│   ├── help_content.py    # /help's plain-language topic content, no Discord imports
│   ├── tutorial.py        # /startingoff steps, tips and saved progress, no Discord imports
│   ├── practice.py        # The Dummy: its skills, move picking, plan text, win/loss check, no Discord imports
│   ├── practice_bot.py    # Random practice bot: builds a legal Character for given BP, SP and PP, no Discord imports
│   └── colors.py          # Embed color helpers
├── data/characters/    # One JSON file per saved Character. Gitignored on
│                         purpose -- personal data, stays local, never in repo.
├── docs/builder/       # The character builder website (static, served by GitHub Pages)
├── requirements.txt
├── .env.example
└── .gitignore
```

## Setup

1. Clone and enter the repo:
   ```
   git clone https://github.com/dopp000/lugic.git
   cd lugic
   ```
2. Create and activate a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and paste in your bot token:
   ```
   cp .env.example .env
   ```
5. Run it:
   ```
   python main.py
   ```

Watch for `Synced N slash command(s)` in the output. `main.py` calls
`bot.tree.sync()` on every `on_ready`, so a signature change to a command is
live again as soon as the bot restarts.

## Debugging Notes Worth Keeping

- **Character data is private and gitignored (`data/characters/`).** Used to
  be tracked by accident, so `git add .` would sweep character saves into
  code commits. Fixed via `.gitignore` + `git rm --cached -r
  data/characters/` (stays on disk locally, just untracked going forward).
  Anything already pushed still lives in git history; would need
  `git filter-repo` + a force-push to actually scrub. Not done yet.
- **Discord modal field limits are a real, easy-to-hit trap**: a
  `discord.ui.TextInput` label over 45 chars or placeholder over 100 chars
  makes Discord reject the entire modal with a 400; surfaces to the caller
  only as a generic "The application did not respond," no exception unless
  the command body is wrapped in try/except. `_check_modal_field_limits()`
  runs once at import time against `AddSkillModal` to catch this at bot
  startup instead of silently at some future call. Run any new Modal
  subclass through the same check.
- **Attack Weight splash: the "one Fighter, one effect" rule.** Multiple
  passes were needed here because HP/Shield/status all live on the `Fighter`
  object, not on individual slots, but candidate selection and Counter
  redirection both originally iterated per-slot. Three separate bugs came
  from that mismatch: (1) a multi-slot enemy could be picked as more than
  one splash candidate, multiplying damage/status per cast, fixed by
  deduping candidates to one representative slot per distinct Fighter; (2)
  splash targets weren't getting per-coin Rupture/status infliction at all,
  fixed by mirroring `apply_incoming_hit`'s per-coin walk independently per
  splash target; (3) when the primary target's *other* slot got auto-picked
  as its own splash candidate, the hit and any Counter retaliation could
  double, fixed by treating a same-Fighter splash as a no-op (still logged
  as "reached," but no second effect) and excluding the primary defender
  from splash-side Counter interception in `apply_counter_redirects` Pass 1.
  The underlying rule to remember for any future multi-target work: **decide
  effects per distinct Fighter, never per slot**: slots are for targeting
  and Speed, not for how many times an effect can land.
- **Clashable Guard/Counter interception: two real bugs, only caught by
  reading the actual live code instead of trusting a prior session's
  summary.** (1) A copy-paste bug: `find_eligible_clashable_counter` checked
  `defender.clashable_guard_used_this_round` instead of its own
  `clashable_counter_used_this_round` flag, so using one blocked the other
  from ever firing, one direction only. (2) A much bigger targeting bug,
  found while tracing the first one: the old interception fallback
  (`apply_counter_redirects`'s since-removed Passes 2/3) matched a Clashable
  defense against *any* solo attack aimed at its holder — `if
  other[1]["target"] is caster`, checking only who the incoming attack was
  aimed at, never what the Clashable defense itself had declared as its own
  target. A Clashable Counter declared to protect an ally from a specific
  attack never actually intercepted that attack; it just grabbed whatever
  unopposed attack happened to exist instead. Fixed by moving Clashable
  Guard/Counter eligibility out of that static pre-pass entirely and into a
  new **Pass 0**, matching the *exact* `(target, target_slot)` a Clashable
  defense actually declared — same target/slot matching the ordinary
  mutual-pairing block already used, just extended to this asymmetric
  (declarer ≠ the one being attacked) case. Verified with real `Fighter`/
  `Skill` objects, not just read-through: the protect-an-ally scenario now
  correctly intercepts the declared attack, an unmatched Clashable defense
  idles safely instead of acting as a live attack, and a full regression
  pass (mutual clashing, Guard-vs-Guard Offset, Evade) stayed unchanged.
  **The lesson to repeat**: when a prior session's summary says a system
  "works," re-derive it from the actual live function bodies before
  building on top of it — both of these were confidently described as
  working in earlier notes, and neither was.
- **Fall-through's real trigger condition wasn't what the original phrasing
  implied.** "A used-up defense falls through to a still-unused Clashable"
  reads like it's about the *same* Evade being asked to react twice — but
  `[Evade]` watches one exact `(attacker, attacker_slot)` pair, declared at
  `/battle declare` time, and that pair only ever resolves once per round,
  so the same Evade can never literally be asked twice. The actual rule,
  confirmed by working through a concrete example: it's about a *separate*
  incoming attack that has no live defense of its own reacting to it at
  all (never had one, or its own watcher already fired for something else)
  — check the target Fighter's other still-idle Clashable defenses for a
  rescue. Also confirmed live, not upfront: whether an attacker dies
  mid-round (from an earlier, faster action) before their own turn comes
  up genuinely changes which redirect is correct, since a static pre-pass
  can't know that yet — this is what made the `resolve_clash_unit`
  extraction necessary rather than optional.

## Running 24/7

See `docs/DEPLOYMENT.md` for putting the bot on a server so it stays online, plus daily backups of `data/`.

## Running the tests

From the repo root:

```
python -m unittest discover tests
```

- `tests/test_limits.py` checks the things Discord rejects outright: every slash command description and name, the size of the battle display with many fighters, help text lengths, the bars, character name and file safety, and how often the combat message is edited.
- `tests/test_scenarios.py` runs each script in `tests/scenarios/` in its own process from an empty temp folder (so nothing is written to `data/`). They cover trigger parsing, passives, statuses and Stagger, follow-ups and Assist Defense, Unfocused Volley, player requests, death reactions, adjacency and `[Failed ...]`, Part Stagger, and the `/help` menus.
- `tests/test_character_import.py` covers reading builder codes and what `/character import` does with them (made at once, sent to the admins, refused, approved).
- `tests/test_builder_site.py` checks that the website's rules agree with the bot's. Its comparison tests need node 18 or newer and are skipped without it. After changing a price in `game/build_points.py`, or any file of the site (`style.css`, `rules.js`, `engine.js`, `preview.js`, `lines.js`, `app.js`), run `python -m game.builder_rules` or its freshness tests fail: the command rewrites `rules.js` and puts a fingerprint of each site file into its address in `docs/builder/index.html` (`app.js?v=e4495fce02`). Without it, right after a deploy a browser can hold the old `preview.js` (GitHub Pages lets browsers keep a file for 10 minutes) next to the new `app.js`, and the page breaks (no tabs, everything shown at once). The same page also draws every part of the form and every start-up step on its own, so one failure leaves the tabs and the other parts working, and shows a box at the top with what failed and a **Fetch fresh copies** button (it asks the server again for the page and its files, then reloads). `tests/test_builder_site.py` opens the real page in Chromium for this when Playwright for Node is installed (`tests/page_check.js`; skipped otherwise).
- Run them before pushing a change to `cogs/` or `game/`. A failing scenario prints its last output.
- `requirements.txt` pins `discord.py` to the version these tests were run against (2.7.1).
