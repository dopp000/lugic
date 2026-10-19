# Worked examples: from a Limbus passive to trigger lines

Every example below was run through the real parser (`parse_passive` for
passives, `parse_trigger_text` for skill lines) before it went in this file, so
you can paste the lines as they are. The ideas are described in plain words, not
copied from the game. Replace names, numbers and tags with your own.

## How to convert a passive

1. **Split the text into "when", "if" and "what".** "At the start of combat, if
   HP is low, gain a Shield" is when = Combat Start, if = HP is low, what = Shield.
2. **Pick the timing.** A passive can only use the timings in `/help`, Trigger
   Syntax: Turn Start, Combat Start, the Ally timings, On Evade, the four attack
   timings (Before Use, On Use, Clash Start, Before Attack) and Self Is Hit, Self
   On Stagger, Self On Kill, Self On Death. Clash Win, On Hit, Attack End and the
   other skill-level timings only work on a skill.
3. **Write the condition, then a comma, then the effect.** One line per rule, up to
   3 lines per passive.
4. **Add a limit if the game text has one**: `(once per turn)`, `(3 times per
   turn)`, `(once per Encounter)`, `(30% chance)`. A scaled line needs
   `(max N)` or `(max N%)`.
5. **Save it** with `/charadmin passive` (admin), and give it a Passive Point price.
6. **If the text needs a resource the bot does not have** (Ammo, Pulsation), make
   it first with `/character addstatus`.


## Simple start-of-fight passives

### Steady start

At the start of combat, recover a little HP and Sanity.

```
[Combat Start] Heal 5
[Combat Start] Gain 6 Sanity
```

Both fire once when the battle begins.

### Poise engine

Every clash the owner enters builds Poise, once a turn.

```
[Clash Start] Gain 1 Poise (once per turn)
```

Poise is a built-in resource, so no custom status is needed. `(once per turn)` is the limit note. A passive cannot use `Clash Win` (see the last section).

### Evasive momentum

Dodging an attack grants Haste.

```
[On Evade] Gain 1 Haste
```

`On Evade` fires after the owner's Evade skill beats an attacker.


## Bonuses to the owner's attacks

### Faster hits harder

Deal extra damage if you are faster than the target.

```
[Before Attack] If this unit's Speed is faster than the target's, Deal +10% damage
```

Passives can use `Before Use`, `On Use`, `Clash Start` and `Before Attack`, which is how a passive touches every skill. Percent damage joins the same pile as Damage Up and Offense Level.

### Desperate power

At half HP or less, coins hit harder.

```
[Before Use] If this unit's HP is 50% or less, Coin Power +1
```

Coin Power only works at the four attack timings.

### Exploit weakness

Extra damage against a target weak to the skill's damage type.

```
[Before Attack] If the target is Weak to this skill's damage type, Deal +15% damage
```

Weak means the target's resistance to that type is above x1.0. Fatal (x1.5 or more) has its own condition.

### Poise scaling

Coin Power grows with Poise, with a cap.

```
[Before Use] Coin Power +1 per 5 Poise Count (max 2)
```

A scaled line must carry `(max N)`.

### Burn payoff

More damage for every Burn Count on the target, capped.

```
[On Use] Deal +2% damage per 1 Burn Count on the target (max 20%)
```

A scaled percent needs `(max N%)`.

### Ammo spender

Spend one Ammo for a stronger coin.

```
[Before Use] Consume 1 Ammo: Coin Power +1
```

Ammo is not built in. Create it first as a buff with `/character addstatus`. If the owner has no Ammo, the line does nothing.


## Support passives: picking an ally

### Heal the weakest

Each turn, heal the ally with the lowest HP.

```
[Ally Turn Start] Heal 5 to the ally with the lowest HP (once per turn)
```

`Ally Turn Start` fires for every ally's turn start, so the `(once per turn)` limit keeps it to one heal.

### Speed to the quickest

Give Haste to the fastest ally at the start of each turn.

```
[Turn Start] Gain 1 Haste to the ally with the fastest Speed
```

The picked ally is never the owner, and the lowest-ordered unit wins ties.

### Sanity top-up

Give Sanity to the ally with the lowest Sanity.

```
[Combat Start] Gain 5 Sanity to the ally with the lowest Sanity
```

Other selectors: slowest Speed, highest or lowest HP, HP percent, Max HP, most or least of a status, earliest or latest deployed.

### Conditional ally buff

If the ally with the highest Max HP is hit, give it a Shield.

```
[Ally Is Hit] If the ally has the highest Max HP, Gain 8 Shield to the ally (once per turn)
```

The condition and the target use the same selector.


## Factions and group tags

### Squad bonus

With three or more squad mates on the field, attacks deal more.

```
[Before Use] If 3+ units on your side have the Dawn Office tag, Deal +10% damage
```

Tags are set by an admin with `/charadmin tags`, at most 3 per character. The count includes the owner and only living units.

### Rival hunter

Extra Coin Power against a rival group.

```
[Before Attack] If the target has the N Corp tag, Coin Power +1
```

A misspelled tag never matches and gives no error, so copy it exactly.

### Last stand

When two or more allies have fallen, shield yourself.

```
[Turn Start] If 2+ allies are defeated, Gain 10 Shield
```


## Survival and death

### Second wind

Survive one killing blow per battle.

```
[Combat Start] Nullify the next lethal damage
```

Lasts until used or the battle ends.

### Hold the line

At low HP, HP cannot fall below 1 this turn.

```
[Turn Start] If this unit's HP is 25% or less, HP cannot drop below 1 this turn (once per Encounter)
```

`(once per Encounter)` resets each battle.

### Unshakable

Cannot be Staggered this turn when badly hurt.

```
[Turn Start] If this unit's HP is 30% or less, Cannot be Staggered this turn
```

### Dying curse

When killed, burn the killer.

```
[Self On Death] Inflict 3 Burn
```

The killer is the target. Burn, Poison and Bleed deaths do not fire it.


## Follow-ups and Assist

### Join the attack

When an ally hits an enemy, use your own attack on that enemy.

```
[Ally On Hit] Use Pounce as a follow-up attack (once per turn)
```

The owner must have a normal attack skill named Pounce (no defense tag, not a Supermove). A follow-up needs a per-turn or per-Encounter limit.

### Bodyguard

Take the hit meant for an ally.

```
[Ally Before Getting Hit] Take the hit for the ally (once per turn)
```

Only for unopposed attacks, resolved as a normal hit on the bodyguard. Clashes are never redirected.


## Chances, shields and delays

### Lucky Poise

A one in three chance to gain Poise whenever a clash starts.

```
[Clash Start] Gain 2 Poise (30% chance)
```

The chance is rolled each time the line would fire. A miss spends nothing.

### Opening Shield

Start the fight with a Shield worth 15% of Max HP.

```
[Combat Start] Gain 15% max HP Shield
```

### Delayed Haste

Gain Haste next turn.

```
[Turn Start] Gain 2 Haste next turn
```

Next-turn effects apply when the next round opens.


## Lines that belong on a skill, not a passive

These timings fire only when the skill is the one being used.

### Poise on a clash win

```
[Clash Win] Gain 1 Poise (once per turn)
```

Put it on a skill instead of a passive.

### Fragile after a kill

```
[On Kill] Inflict 1 Fragile
```

Skill-level timings such as Clash Win, On Kill, Attack End and On Hit live on skills.

### Lucky Fragile

```
[Clash Win] Inflict 1 Fragile (30% chance)
```

The chance is rolled each time the line would fire.


## What to leave to the GM

Some identities are whole mini games: skill swapping states, shared counters
across the field, Prescript-style subsystems and Sin Resonance rules. The cheap
approach is a custom status that holds the number, with the passive text as
flavor and the GM deciding the rest. Not built: Reuse Coin, Sin Resonance,
Part-specific Stagger, adjacency conditions.
