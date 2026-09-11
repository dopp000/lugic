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
- **Attack Weight (multi-target splash)**: fully built and confirmed
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
  ephemeral embeds, chunked if long. Tuning: `COIN_FACE_DELAY` /
  `COIN_DETAIL_DELAY` near the top of `combat()`.
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

## Status Effects: Current State

- **Data model is solid**: `StatusInstance(name, potency, count)`, proper
  stacking (`apply_status` adds potency+count onto existing), decay
  (`decay_after_trigger` ticks count down by 1), universal potency cap at 99
  (canon's own cap; Charge additionally caps its own Count at 20, a separate
  ceiling). Resistance applies to infliction the same asymmetric way it
  applies to damage (`apply_resistance`, a real Limbus-style formula, not a
  flat percentage; see below).
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
- No non-Sin-damage debuffs exist at all (Power Down, Bind, Fragile,
  Paralyze, Curse, etc.) — this is now the actual biggest remaining status
  gap, not the five Sin statuses.
- Charge has no consumption payoff (see above) — smaller, but still open.
- No turn-based expiry independent of triggering (a status that should wear
  off after N turns even if its trigger never fires isn't modeled — every
  status here only ever decays by actually being triggered).

## Character System: Current State

`/character create/edit/resistance/view/list/delete/say` covers a **stat
sheet only**: HP, Speed (flat or min/max range, replacing the old flat-speed
field once set), a single flat Power value, resistances (comma-separated
multi-set: `resistance_types:slash,burn values:20,-10`, covers the 3 damage
types + 5 status types), avatar, and `say` (webhook RP). No Sanity field:
intentional, Sanity always starts at 0 per battle. No Trait/faction tags, no
self-buff starting values.

**Persistent Stagger config now exists**: `Character.stagger_thresholds` /
`stagger_tiers_enabled` (both `None` by default, meaning "use `Fighter`'s
own defaults"), applied in `Fighter.from_character()`. This closes what used
to be a listed gap ("no Stagger threshold customization on Character").

**The one real gap worth prioritizing**: Characters still carry **no Skills
at all**. Skills only exist on the in-battle `Fighter`, built fresh per
battle via `Fighter.from_character()`. Nothing persists between battles:
every fight means re-running `/battle addskill` for every skill on every
fighter from scratch. A saved "loadout" concept tying skills to a Character
would make persistent characters actually feel persistent, and would
compound well with any Skill Rank/Deck system built later. Note this is
**separate** from the `/battle skills`/`removeskill` commands below, which
manage an in-battle `Fighter`'s skill list, not a saved `Character`'s.

The flat `Power` field on Character looks orphaned now that Skills carry
their own independent Base Power/Coin Power; worth confirming whether it's
still consumed anywhere at battle time or is dead weight from before the
Skill system existed.

**Skill management is partially built now, not fully absent.**
`/battle skills` lists everything a fighter currently knows, and
`/battle removeskill` removes one by name — both closed since the last
README pass. There is still no `/battle editskill`, so fixing a typo on an
already-added skill means removing and re-adding it, not surgical edit.

## Command Reference

### `/battle` group (`cogs/battle.py`)

| Command | Notes |
| --- | --- |
| `create` | Starts a battle (Spar / Standard / Fatal). One per channel. Battle type is currently cosmetic only, no mechanical branching (no permanent-death enforcement for Fatal, etc. — though see Elimination in Combat Features, which applies regardless of battle type). |
| `addfighter` | From a saved `/character` or as a one-off. |
| `addskill` | `AddSkillModal`: one popup, packed comma-separated stats (Discord caps a modal at 5 fields), per-coin statuses, Trigger text box, `attack_weight_input`. 1-coin enforcement on `[Guard]`/`[Evade]`/`[Clashable Guard]`. |
| `skills` | Lists everything a fighter currently knows. |
| `removeskill` | Removes one named skill from a fighter's known skills. No `editskill` yet — remove and re-add for a typo fix. |
| `declare` | Locks a skill into a slot aimed at a target's slot. No scouting. Also where Attack Weight splash candidates get picked (see Combat Features). Blocks targeting an eliminated fighter, same as it already blocked an eliminated caster from acting. |
| `undeclare` | Clears one declared slot. |
| `removefighter` | Owner or admin (`ADMIN_ROLE_ID`) via `_can_manage_fighter`. |
| `setstatus` | Admin/testing tool: HP, Sanity, Speed (min+max), resistances, Power, Stagger thresholds/enabled-tiers, one status, whichever fields are passed. Refuses to touch an eliminated fighter at all. |
| `combat` | Resolves the round. See Animated Combat Phase. Also where Turn-End status ticks (Burn/Sinking/Charge) and Elimination checks run, once per round. |
| `end` | Ends the battle. |

No `/battle status`; the synced embed (`build_battle_embed`, kept current
via `sync_battle_message`) already shows everything it did. No skip/pass
action exists; every declared slot needs a real skill, and combat won't run
until `all_declared()` is true, with no admin override to force it. No
visible round/turn counter for players. **Elimination is built** (0 HP →
permanent flag, removed from roster, blocked from acting/being targeted,
see Combat Features) — this used to be listed as entirely absent, no longer
true. Still no Skill Slot loss, no Backup/Substitute/Retreat system beyond
that flag.

### `/character` group (`cogs/character.py`)

| Command | Notes |
| --- | --- |
| `create` | New saved character, owned by whoever ran it. |
| `list` | Lists your own saved characters. |
| `view` | Owner or admin; `public:True` posts to channel. |
| `edit` | Owner-or-admin. `speed_min`/`speed_max` must be given together, no implicit flat-speed shortcut anymore. Also where `stagger_thresholds`/`stagger_tiers_enabled` get set, applied at battle-start via `Fighter.from_character()`. |
| `resistance` | Owner-or-admin. Multi-set via positionally-aligned `resistance_types`/`values`. |
| `delete` | Owner-or-admin. |
| `say` | Owner-or-admin. Speaks via webhook (name + avatar). |

### `/roll`

Plain dice roller (`1d20+4+2` style), not tied to battle state.

## Trigger Syntax (`game/conditions.py`)

One line per Trigger, pasted into `/battle addskill`'s popup. Blank lines
ignored.

```
[<Timing>] <optional condition,> <effect>
:Coin<N>: [<Timing>] <optional condition,> <effect>     # per-coin timings only
[<Flag>]                                                 # skill-metadata flag, own line
```

**Skill-level timings** (no `:CoinN:` prefix): `Turn Start`, `Combat Start`,
`Turn End`, `Before Use`, `On Use`, `Clash Start`, `Clash Win`, `Clash Lose`,
`Before Attack`, `On Unopposed Attack`, `Attack End`, `On Evade`, `Before
Getting Hit`, `On Stagger`, `On Kill`, `On Crit Kill`, `Hit After Clash
Lose`, `Tremor Burst`. The last four (`On Kill`/`On Crit Kill`/`Hit After
Clash Lose`/`Tremor Burst`) were added after the last README pass — check
`SKILL_LEVEL_TIMINGS` in `game/conditions.py` directly if this list and the
code ever drift, the code is the source of truth.

**Per-coin timings** (need `:CoinN:` prefix): `Coin Start`, `On Hit`, `Heads
Hit`, `Tails Hit`, `Hit After Clash Win`, `Current Coin Attack End`, `Heads
Attack End`, `Tails Attack End`, `On Crit`, `On Crit - Heads Hit`, `On Crit -
Tails Hit`.

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
Counter`, `Guard`, `Clashable Guard`, `Evade`. All eight are enforced,
including the newest, `Evade` (added when Evade was rebuilt from a status
into a declared skill — see Combat Features). Note: `Indiscriminate` is
currently parsed and stored, but `/battle declare` still hard-blocks
same-side targeting, so it doesn't yet actually let a skill hit allies.
`[Guard]`/`[Evade]`/`[Clashable Guard]` are enforced to exactly 1 coin at
`/battle addskill` time.

## Known Gaps

- Counter/Clashable Counter/Clashable Guard's animated reveal (coin faces,
  power ramp) uses the same generic `animate_damage`/`format_skill_result`
  path as a normal attack; no distinct visual treatment for "this was an
  interception," you have to read the header/log text to tell. Purely
  cosmetic, not a logic gap — see **Currently In Progress** below for the
  actual open logic work on this system.
- `Combat Start`/`Turn Start` fire via a full sweep of a fighter's entire
  known skill list each round (`fire_passive_triggers`), not tied to what's
  declared. `On Evade` works the same way (`fire_evade_triggers`). `Before
  Getting Hit` is different: fires only on the one `[Counter]` skill that
  actually redirected and landed, not a sweep. Deliberate, not a gap.
- Animated combat reveal pacing (`COIN_FACE_DELAY`/`COIN_DETAIL_DELAY`) is
  untuned beyond "verified it works"; a busy round can run long.
- Staggered units can still act freely: nothing currently blocks a
  Staggered fighter from declaring/using Skills or Counters next turn, and
  their resistances don't flip to a Fatal-style override; only the flat
  tier-multiplier applies to damage they take.

## Currently In Progress

The defense-skill cascade (Guard/Evade/Counter/Clashable variants
interacting with each other within one round) is mid-rebuild, actively
being worked on across recent sessions. Read this section before touching
`apply_counter_redirects`, the Pass 0 block, or anything in `combat()`'s
solo/clash dispatch — it's the one area of the codebase currently in a
known-incomplete state, not a stable baseline to build on top of casually.

**Locked in and confirmed correct** (see Combat Features above for the full
mechanical writeup): Evade is a declared, slot-specific, 1-coin skill now,
not a resource. Guard/Evade are strictly slot-specific, never steal or get
stolen from. Clashable Guard/Clashable Counter are matched against their
*exact* declared target via Pass 0, not "any incoming attack," fixed after
finding the old fallback ignored the declared target entirely. A used-up
defense skill correctly won't fire twice within the same round.

**The one piece left, not yet built**: the "used-up non-clashable defense
falls through to a still-unused Clashable" rule. Confirmed design (locked
in, not still being decided): if an attack lands on a slot whose `[Evade]`/
`[Guard]` already fired earlier this same round, it doesn't auto-hit —
it falls through to whichever of that *same fighter's* Clashable defenses
(`[Clashable Guard]`/`[Clashable Counter]`) is still unused, and that
clashes instead. Never a teammate's Clashable, only the same fighter's own.
Once any defense skill (clashable or not) has fired once, it's spent for
the round, full stop — no double-activation in either direction.

**Why this isn't built yet, concretely**: `apply_counter_redirects`
historically ran as a single static pre-pass, deciding everything before
the round's resolution loop even starts. Pass 0 already fixed this for
Clashable Guard/Counter's own *primary* eligibility (matching against
runtime-live `evade_used_slots` state now works correctly). What's still
missing is the fall-through redirect itself — reacting mid-round to "this
slot's own defense already fired, try the next one" requires the
clash-resolution body currently tangled inside the animation loop's shared
closures (`render`, `animate_faces`, `animate_power`, `animate_damage`) to
get extracted into its own reusable function first, so it can be invoked
a second time, mid-loop, for a redirected fall-through target without
duplicating that whole block. This extraction is flagged as real surgery,
deliberately not rushed into the same diff as anything else — do it as its
own careful, tested step.

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
- **E.G.O → Supermoves.** Setting is an MHA x Project Moon crossover, so
  E.G.O Skills become Supermoves. No E.G.O resource system, Sanity-cost
  formula, or Overclocking is planned to be ported as-is; needs its own
  design pass when actually built.
- **Corrosion**: not implemented, not planned near-term.
- **No Skill Deck / pull system.** `/battle declare` picking a known skill
  directly is the intended design, not a placeholder.
- **Evade is a single win/lose roll per attack, not a persistent dodge
  state**, unlike canon (where a clean Evade can stay active). Evade is a
  declared, slot-specific, 1-coin Skill now (rebuilt from a Count-based
  resource — see Combat Features and **Currently In Progress**); each
  incoming hit against its watched slot gets exactly one coin toss decided
  by the defender's own `heads_chance()`, win or lose, no persistence.
  Deliberate, not an oversight.
- **Resistance uses the real canon formula, asymmetric around Normal.** A
  weakness (`resistance_pct` negative) scales damage linearly and fully; an
  actual resistance (positive) is only half as effective per point as an
  equivalent weakness (`apply_resistance` in `game/resistances.py`, checked
  against the wiki's worked example: 20 resistance reduces damage 10%, not
  20%). No finite resistance value ever reaches true 0 damage; canon's
  "Immune" tier is a separate discrete `x0`, not modeled here. A genuine
  zero-damage case would need an explicit override.

**Actually on the roadmap, not built yet, rough priority order:**

1. **The defense-skill cascade's fall-through rule.** See **Currently In
   Progress** above — the one remaining piece of the Evade/Guard/Counter
   rebuild, blocked on extracting the clash-resolution body out of the
   animation loop first. Highest priority since it's actively mid-build,
   not a cold-start item.
2. **Character skill loadouts.** Let a saved Character carry a skill set
   that auto-loads via `Fighter.from_character()`, so battles stop requiring
   `/battle addskill` from scratch every time. Compounds well with a future
   Skill Rank/Deck system.
3. **`[Failed ...]` trigger prefix**: fires when a conditional (e.g. a Kill)
   would have activated but didn't. Not present.
4. **`[Ally ...]` trigger prefix**: scopes an existing timing to allies
   only, for support effects. `Indiscriminate` is targeting-only; this would
   be trigger-scoping instead. Not present.
5. **Non-Sin debuffs** (Power Down, Bind, Fragile, Paralyze, Curse, etc.) —
   now the actual biggest status-system gap, since all 5 Sin-damage statuses
   have real payoffs (see Status Effects above).
6. **Charge's consumption payoff.** Its own Count decay is wired; nothing
   converts it into Coin Power/Haste/whatever its actual effect should be
   yet.
7. **Panic / Low Morale.** -30 SP (Low Morale) / -45 SP (Panic) thresholds.
   Panic default: target can't act that Turn. Both must be turn-limited by
   design: never a permanent stat change from one trigger, must expire and
   be reapplied. Needs a customization layer similar to canon's [Panic Type
   Changing
   Effects](https://limbuscompany.wiki.gg/wiki/Status_Effects), for a
   Sinking-user inflicting it, and for a character with their own version of
   the behavior.
8. **Parts / Core.** For a future multi-part NPC, each skill slot is a
   distinct body part with its own resistances, damage to a Part also drains
   the shared Core HP (matches canon Focused Encounters). Not built; no NPC
   needing it exists yet.
9. **A worked-examples guide**: converting a real Limbus kit passive into
   this bot's Trigger syntax. The Trigger Syntax section documents the
   grammar; there's no "here's a real passive → here's the Trigger lines"
   guide yet. Worth writing once there's a backlog of real characters.

**Confirmed already true, not a gap:** Sanity can never be set nonzero at
battle start through any normal command: `Fighter.sanity` defaults to 0 and
`/battle addfighter` has no Sanity parameter. The only way Sanity differs
from 0 at Round 1 start is a passive that explicitly says so (via
`fire_passive_triggers`), never a flat override. `/battle setstatus` remains
a deliberate admin/testing escape hatch.

Emoji IDs for Shield, Panic, and Low Morale (Panic/Low Morale share one icon)
are filled in (`STATUS_EMOJI_IDS`, `game/emojis.py`) even though Panic/Low
Morale's own mechanics aren't built yet. `guard` and `tremor_burst` both
have real emoji IDs filled in too now (neither is `None` anymore) — both
mechanics are fully built (see Combat Features / Status Effects above);
`tremor_burst`'s own inline comment in `game/emojis.py` claiming "not wired
to a mechanic yet" is itself stale and worth fixing next time that file is
touched.

## Character Creation & Progression (Level 1)

Design notes for the leveling system: a Level 1 character is an MHA
hero-university student with no hero license yet. None of this is enforced
by the bot as commands/validation right now; it's the target ruleset
`/character create`/`edit` should eventually check against.

**Two separate Build Point pools**: Stat Points and Skill Points, spent
independently. Stat Points cover HP, Speed range (max 8 wide), and Stagger
(both threshold placement and whether all 3 tiers are kept).

**Stagger tier trade-off**: all 3 checkpoints are active by default. Tier 1
is permanent, can never be paid off. Tiers 2/3 can be stripped with Build
Points: removing Tier 3 means never eating its 2.5x penalty again; removing
both leaves only Tier 1 in any fight. Duration is the other half: Tier 1
clears end of the same Turn; Tiers 2/3, if kept, persist through one extra
Turn. Paying to strip a tier is "spend upfront to guarantee only the mild,
same-turn version."

**Sanity, in combat** (matches actual code in `game/battle.py`/
`cogs/battle.py`; if these ever drift, the code is the bug):

| Event | Change |
| --- | --- |
| Win a Clash | +2 SP per coin in the winning skill |
| Unopposed attack | +2 SP per coin that flips Heads, +0 for Tails |
| Lose a Clash | -3 SP flat, ignores the floor (can push positive to negative) |
| Turn end, positive | -4 SP, floored at 0 |
| Turn end, negative | +2 SP, capped at 0 (recovery slower than falling) |

**Character creation rules:**

- No single stat above 40% of total BP pool.
- Power + Speed combined ≤ 60% of BP pool.
- Power + HP combined ≤ 60% of BP pool.
- Max 2 Status Effect Archetypes.
- Max 2 Defense Skill Archetypes.
- Status resistance refund capped at 6 BP total.
- Stagger Tier 1 can never be removed.

**Pacing**: 4–6 Turns minimum for a spar-weight fight, longer for anything
serious.

**Point economy**: Build Points earned via literacy training (weekly caps)
and event participation, not handed out freely.

**Still open, not decided**: how Power itself should scale against level and
desired damage output; how much Power a mid-battle skill should trade for
carrying a status effect, and how that scales; the Build Point (or
Sanity/self-debuff/limited-use) cost of a Supermove and what real drawbacks
should gate a strong one; the cost of attaching extra skill tags or
amplifications onto a base skill. No concrete numbers yet; needs a
dedicated design pass rather than inventing figures ad hoc.

## Project Structure

```
├── main.py            # Bot entry point; loads cogs, starts connection, syncs command tree on_ready
├── cogs/               # Discord-facing commands, grouped by feature
│   ├── battle.py        # /battle group -- largest file, combat engine glue lives here
│   ├── character.py     # /character group -- persistent character CRUD
│   └── roll.py          # /roll -- standalone dice roller
├── game/               # Game logic, no Discord imports here
│   ├── battle.py         # Fighter, Battle, DeclaredAction, BATTLE_TYPES
│   ├── skills.py          # Skill, SkillResult, resolve_skill, resolve_round_clash, resolve_triggers
│   ├── conditions.py      # Trigger, TriggerContext, parse_trigger_text -- the trigger syntax parser
│   ├── character.py       # Character (persistent, JSON-backed), separate from per-battle Fighter
│   ├── statuses.py        # StatusInstance, apply_status, decay_after_trigger
│   ├── resistances.py     # DAMAGE_TYPES, ALL_RESISTANCE_TYPES, apply_resistance
│   ├── emojis.py          # Every custom Discord emoji ID this bot uses
│   └── colors.py          # Embed color helpers
├── data/characters/    # One JSON file per saved Character. Gitignored on
│                         purpose -- personal data, stays local, never in repo.
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