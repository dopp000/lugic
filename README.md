# Limbattle Bot

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
- **Evasion**: holding Evasion dodges incoming coins one at a time (consumed
  per dodge), firing `[On Evade]` via `fire_evade_triggers`.
- **Counter / Clashable Counter**: two Skill-flag mechanics, not a
  status/resource. `[Counter]`: reactive, fires against any incoming
  unopposed attack on the holder if the Counter skill's own slot speed beats
  the attacker's, fully redirects the attack and strikes back, bypassing the
  attacker's Evasion (`skip_evasion` on `apply_incoming_hit`). Single-use per
  round. `[Clashable Counter]`: resolves in normal speed order; if unopposed,
  scans the holder's other slots for an unopposed attack and forces a real
  Clash against it instead, or fizzles. Also single-use per round. Both reset
  via `Fighter.clear_declaration`. See `apply_counter_redirects`.
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
- **Guard / Shield / Clashable Guard**: third defense-skill type alongside
  Evade/Counter. `[Guard]`: never enters a Clash even if mutually targeted;
  always self-resolves, converting Final Power into Shield HP for the caster.
  Shield drains before HP (1:1, no reduction) and clears every round, with
  no persistence. `[Clashable Guard]`: reactive, same interception shape as
  Clashable Counter, but its clash outcome replaces damage entirely: winning
  raises the loser's enabled Stagger thresholds by
  `GUARD_STAGGER_THRESHOLD_RAISE` (10pp/tier); losing still takes the
  winner's damage, cut by `GUARD_LOSE_DAMAGE_REDUCTION_PCT` (25%). Both
  placeholder defaults.
- **Offset**: two plain `[Guard]` skills mutually targeting each other fully
  cancel: no coins, no Shield, no triggers, just a log line. `[Clashable
  Guard]`, `[Counter]`, `[Clashable Counter]` are excluded from Offset
  (matches canon's exception clause exactly).
- **Attack Weight (multi-target splash)**: engine-level implementation is
  live, wiring into `/battle addskill` needs verification (see **Known
  Gaps**). At `declare()` time, `(attack_weight - 1)` extra enemy slots are
  auto-picked as splash candidates: any living enemy is eligible, fastest
  Speed first, deduped to exactly **one representative slot per distinct
  Fighter** (their own fastest eligible slot) since HP/Shield/status all live
  on the Fighter, not the slot. The primary `(target_fighter, target_slot)`
  is excluded from candidate selection, but the primary target's *other*
  slots can still be picked; hitting the same enemy via primary + splash is
  intentional ("Attack Weight reaches them twice") but must **not** double
  damage or status; a same-Fighter splash is a logged no-op. In `combat()`,
  splash targets reuse the already-computed hit (no re-tossed coins, no
  extra Poise consumption, no re-fired per-coin Triggers) but each splash
  target gets its own resistance, Stagger check, Evasion check (whole-splash
  dodge, not per-coin), and its own independent per-coin Rupture/status
  accrual walked the same way `apply_incoming_hit` does for the primary
  target. `[Clashable Guard]`/`[Clashable Counter]` are excluded from splash
  interactions entirely (same exception Offset gets). Plain `[Counter]`
  holders are excluded from splash candidacy in `apply_counter_redirects`
  Pass 1 if they're also the primary defender, so one incoming attack never
  draws two retaliations.
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

## Status Effects: Current State

- **Data model is solid**: `StatusInstance(name, potency, count)`, proper
  stacking (`apply_status` adds potency+count onto existing), decay
  (`decay_after_trigger` ticks count down by 1). Resistance applies to
  infliction the same asymmetric way it applies to damage (`apply_resistance`
  (a real Limbus-style formula, not a flat percentage; see below).
- **Self-buff resources wired**: Poise (Crit), Evasion (dodge). Charge exists
  as a resource but nothing consumes it yet (no Coin Power scaling, no Haste
  conversion).
- **Rupture** is the only target-facing status with a real payoff: bonus
  damage on hit, decays by 1 count per trigger.
- **Not built, biggest remaining gap**: Burn, Bleed, Tremor, Sinking can be
  inflicted (stored as potency/count) but do nothing. Canon behavior still
  missing: Burn ticks fixed damage at Turn End; Bleed ticks per coin tossed;
  Tremor raises Stagger Threshold on "Tremor Burst"; Sinking drains fixed SP
  per hit taken. A large fraction of real Limbus Identity passives reference
  these statuses conditionally, so this blocks porting real kits more than
  any other single gap.
- No non-Sin-damage debuffs exist at all (Power Down, Bind, Fragile,
  Paralyze, Curse, etc.).
- No potency cap (canon caps at 99) or turn-based expiry independent of
  triggering.

## Character System: Current State

`/character create/edit/resistance/view/list/delete/say` covers a **stat
sheet only**: HP, Speed (flat or min/max range, replacing the old flat-speed
field once set), a single flat Power value, resistances (comma-separated
multi-set: `resistance_types:slash,burn values:20,-10`, covers the 3 damage
types + 5 status types), avatar, and `say` (webhook RP). No Sanity field:
intentional, Sanity always starts at 0 per battle. No Stagger threshold
customization, no Trait/faction tags, no self-buff starting values.

**The one real gap worth prioritizing**: Characters carry **no Skills at
all**. Skills only exist on the in-battle `Fighter`, built fresh per battle
via `Fighter.from_character()`. Nothing persists between battles: every
fight means re-running `/battle addskill` for every skill on every fighter
from scratch. A saved "loadout" concept tying skills to a Character would
make persistent characters actually feel persistent, and would compound well
with any Skill Rank/Deck system built later.

The flat `Power` field on Character looks orphaned now that Skills carry
their own independent Base Power/Coin Power; worth confirming whether it's
still consumed anywhere at battle time or is dead weight from before the
Skill system existed.

No skill editing/removal/listing commands exist post-creation, so a typo means
`/battle end` and starting over, or manual data surgery.

## Command Reference

### `/battle` group (`cogs/battle.py`)

| Command | Notes |
| --- | --- |
| `create` | Starts a battle (Spar / Standard / Fatal). One per channel. Battle type is currently cosmetic only, no mechanical branching (no permanent-death enforcement for Fatal, etc.). |
| `addfighter` | From a saved `/character` or as a one-off. |
| `addskill` | `AddSkillModal`: one popup, packed comma-separated stats (Discord caps a modal at 5 fields), per-coin statuses, Trigger text box. **Verify**: does this modal actually expose `attack_weight`? Suspected gap, see Known Gaps. |
| `declare` | Locks a skill into a slot aimed at a target's slot. No scouting. Also where Attack Weight splash candidates get picked (see Combat Features). |
| `undeclare` | Clears one declared slot. |
| `removefighter` | Owner or admin (`ADMIN_ROLE_ID`) via `_can_manage_fighter`. |
| `setstatus` | Admin/testing tool: HP, Sanity, Speed (min+max), resistances, Power, Stagger thresholds/enabled-tiers, one status, whichever fields are passed. |
| `combat` | Resolves the round. See Animated Combat Phase. |
| `end` | Ends the battle. |

No `/battle status`; the synced embed (`build_battle_embed`, kept current
via `sync_battle_message`) already shows everything it did. No skip/pass
action exists; every declared slot needs a real skill, and combat won't run
until `all_declared()` is true, with no admin override to force it. No
visible round/turn counter for players. No death/removal handling beyond
presumably `is_alive()` gating future rounds, no Sinner-Death consequence,
no Skill Slot loss, no Backup/Substitute/Retreat system.

### `/character` group (`cogs/character.py`)

| Command | Notes |
| --- | --- |
| `create` | New saved character, owned by whoever ran it. |
| `list` | Lists your own saved characters. |
| `view` | Owner or admin; `public:True` posts to channel. |
| `edit` | Owner-or-admin. `speed_min`/`speed_max` must be given together, no implicit flat-speed shortcut anymore. |
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
Getting Hit`, `On Stagger`.

**Per-coin timings** (need `:CoinN:` prefix): `Coin Start`, `On Hit`, `Heads
Hit`, `Tails Hit`, `Hit After Clash Win`, `Current Coin Attack End`, `Heads
Attack End`, `Tails Attack End`, `On Crit`, `On Crit - Heads Hit`, `On Crit -
Tails Hit`.

`UNSUPPORTED_TIMINGS` is currently empty: every recognized timing has real
dispatch. New timing names without wired dispatch should go here with a
reason string; that's the "parsed but not built yet" convention. `Before
Getting Hit` fires on a `[Counter]` skill right after it redirects and lands
its retaliation strike.

**Self-buff resources** (`SELF_BUFF_STATUSES`): `Poise`, `Charge`,
`Evasion`, the only things `Gain N <X>` / `At N+ <X>` recognize as a
caster-held resource; anything else is rejected with a clear message.
`Counter` used to be in this list; it's a skill-flag now, not a status.

**Skill-flag tags** (`SKILL_FLAG_TAGS`, own line, never `:CoinN:`-prefixed):
`Target Fixed`, `Unclashable`, `Indiscriminate`, `Counter`, `Clashable
Counter`, `Guard`, `Clashable Guard`. All seven are enforced. Note:
`Indiscriminate` is currently parsed and stored, but `/battle declare` still
hard-blocks same-side targeting, so it doesn't yet actually let a skill hit
allies.

## Known Gaps

- **`attack_weight` modal wiring: unconfirmed, needs a direct check.** The
  full splash engine (candidate selection, dedup, per-coin status/Rupture
  parity, primary-target exclusion, Counter-double-retaliation prevention)
  is built and tested in `cogs/battle.py`. What's unverified is whether
  `AddSkillModal`/`Skill(...)` construction actually exposes a way to set
  `attack_weight` above its default at skill-creation time. If it doesn't,
  none of the splash engine is reachable from actual play yet; check
  `AddSkillModal`'s field list and the `Skill(...)` call it makes before
  assuming Attack Weight is usable in a real battle.
- Counter redirect / Clashable Counter interception visuals.
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
- **Evade doesn't stay active after a clean dodge**, unlike canon. Evasion is
  a flat Count-based resource (`Gain N Evasion`) that depletes per dodge,
  deliberate, not an oversight.
- **Resistance uses the real canon formula, asymmetric around Normal.** A
  weakness (`resistance_pct` negative) scales damage linearly and fully; an
  actual resistance (positive) is only half as effective per point as an
  equivalent weakness (`apply_resistance` in `game/resistances.py`, checked
  against the wiki's worked example: 20 resistance reduces damage 10%, not
  20%). No finite resistance value ever reaches true 0 damage; canon's
  "Immune" tier is a separate discrete `x0`, not modeled here. A genuine
  zero-damage case would need an explicit override.

**Built, but needs a wiring check before it counts as "done":**

- **Attack Weight / multi-target splash.** Engine-level logic is real and
  tested (see Combat Features + Known Gaps above); this moved out of
  "not built" territory this cycle. The open question is purely whether
  `/battle addskill` lets a player actually set `attack_weight` on a skill.
  **Resolve this first** before treating Attack Weight as shippable.

**Actually on the roadmap, not built yet, rough priority order:**

1. **Status payoffs: Burn, Bleed, Tremor, Sinking.** Highest-impact gap,
   blocks the most real Limbus passive content from porting cleanly (see
   Status Effects section above for exact expected behavior per status).
2. **Character skill loadouts.** Let a saved Character carry a skill set
   that auto-loads via `Fighter.from_character()`, so battles stop requiring
   `/battle addskill` from scratch every time. Compounds well with a future
   Skill Rank/Deck system.
3. **Kill-triggered timings**: `[On Kill]`, `[On Crit Kill]`, `[On Crit Kill
   Against Enemy]`, not in `conditions.py` at all yet.
4. **`[Failed ...]` trigger prefix**: fires when a conditional (e.g. a Kill)
   would have activated but didn't. Not present.
5. **`[Ally ...]` trigger prefix**: scopes an existing timing to allies
   only, for support effects. `Indiscriminate` is targeting-only; this would
   be trigger-scoping instead. Not present.
6. **Panic / Low Morale.** -30 SP (Low Morale) / -45 SP (Panic) thresholds.
   Panic default: target can't act that Turn. Both must be turn-limited by
   design: never a permanent stat change from one trigger, must expire and
   be reapplied. Needs a customization layer similar to canon's [Panic Type
   Changing
   Effects](https://limbuscompany.wiki.gg/wiki/Status_Effects), for a
   Sinking-user inflicting it, and for a character with their own version of
   the behavior.
7. **Parts / Core.** For a future multi-part NPC, each skill slot is a
   distinct body part with its own resistances, damage to a Part also drains
   the shared Core HP (matches canon Focused Encounters). Not built; no NPC
   needing it exists yet.
8. **A worked-examples guide**: converting a real Limbus kit passive into
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
are filled in (`STATUS_EMOJI_IDS`, `game/emojis.py`) even though those
mechanics aren't built. `guard` is still `None`, same pattern as
`tremor_burst`; fill in once Guard's icon is uploaded (Guard mechanics
themselves are already built; this is purely a missing emoji asset).

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
   git clone https://github.com/dopp000/custom-limbus-dnd-bot.git
   cd custom-limbus-dnd-bot
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
