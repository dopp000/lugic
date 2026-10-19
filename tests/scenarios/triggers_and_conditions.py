import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys; sys.path.insert(0,ROOT)
from game.conditions import *
from game.battle import Fighter
from game.skills import Skill
import dataclasses
def parse(t): return parse_trigger_text(t)
cases=[
"[On Use] If this unit's HP is 50% or less, Deal +3 damage",
"[On Use] If the target's HP is 30% or more, Deal +3 damage",
"[On Use] If the target's Sanity is -10 or less, Deal +3 damage",
"[On Use] If this unit's Sanity is -5 or less, Deal +3 damage",
"[On Use] If this unit's Sanity is 20+, Deal +3 damage",
"[On Use] If this unit's Speed is 3+ higher than the target's, Deal +3 damage",
"[On Use] If this unit's Speed is 2+ lower than the target's, Deal +3 damage",
"[On Use] If the target is Weak to this skill's damage type, Deal +3 damage",
"[On Use] If the target is Fatal to the skill's damage type, Deal +3 damage",
"[On Use] If the target has 3+ negative effects, Deal +3 damage",
"[On Use] If the target is using a Defense Skill, Deal +3 damage",
]
for c in cases:
    r=parse(c); t=r[0]; t=t[0] if isinstance(t,list) else t
    print(t.condition.type,t.condition.comparison,t.condition.value,t.condition.status_name)
for bad in ["[On Use] If this unit's HP is 150% or less, Deal +3 damage","[On Use] If the target has 0+ negative effects, Deal +3 damage"]:
    try: parse(bad); print("NOT REJECTED",bad)
    except TriggerParseError as e: print("rejected:",e.reason)

from types import SimpleNamespace as NS
def F(**k):
    d=dict(hp=100,max_hp=100,sanity=0,resistances={"slash":1.0,"blunt":1.0,"pierce":1.0},declared_actions={},side="a",statuses={})
    d.update(k)
    f=NS(**d); f.get_status=lambda n,f=f: f.statuses.get(n); return f
def ev(text,caster,target,skill=None,**ctx):
    t=parse(text)[0]; t=t[0] if isinstance(t,list) else t
    return evaluate_condition(t.condition, TriggerContext(caster=caster,target=target,battle=None,**ctx), skill)
c=F(hp=40,sanity=-20); t=F(hp=80,sanity=-12,side="b",resistances={"slash":1.5,"blunt":1.25,"pierce":0.5})
assert ev(cases[0],c,t) and not ev(cases[0],t,c)
assert ev(cases[1],c,t) and ev(cases[2],c,t) and ev(cases[3],c,t) and not ev(cases[4],c,t)
assert ev(cases[5],c,t,caster_slot_speed=8,target_slot_speed=5) and not ev(cases[5],c,t,caster_slot_speed=7,target_slot_speed=5)
assert ev(cases[6],c,t,caster_slot_speed=3,target_slot_speed=5) and not ev(cases[6],c,t,caster_slot_speed=4,target_slot_speed=5)
sl=NS(damage_type="slash"); bl=NS(damage_type="blunt"); pi=NS(damage_type="pierce")
assert ev(cases[7],c,t,sl) and ev(cases[7],c,t,bl) and not ev(cases[7],c,t,pi) and not ev(cases[7],c,t)
assert ev(cases[8],c,t,sl) and not ev(cases[8],c,t,bl)
t.statuses={"burn":NS(count=2,potency=1),"bleed":NS(count=0,potency=0),"rupture":NS(count=1,potency=1),"sinking":NS(count=1,potency=1)}
assert ev(cases[9],c,t); del t.statuses["sinking"]; assert not ev(cases[9],c,t)
assert not ev(cases[10],c,t)
t.declared_actions={1:NS(skill=NS(tags=["guard"]))}; assert ev(cases[10],c,t)
t.declared_actions={1:NS(skill=NS(tags=["slash"]))}; assert not ev(cases[10],c,t)
print("eval ok")
