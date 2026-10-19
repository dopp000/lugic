import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, asyncio; sys.path.insert(0,ROOT)
from game.battle import Battle, Fighter
from game.conditions import *
from game.passives import parse_passive, build_passive_skill
from game.trigger_text import describe_trigger
from cogs.battle import fire_passive_triggers, apply_trigger_effects
import inspect

def mk(name,side,hp,mhp,speeds,sanity=0):
    f=Fighter(name=name,side=side,hp=hp,max_hp=mhp,sanity=sanity); f.slot_speeds=list(speeds); return f
me=mk("Me","a",50,100,[5]); a1=mk("Fast","a",90,100,[9,3]); a2=mk("Hurt","a",20,150,[4],sanity=-10); foe=mk("Foe","b",100,100,[6])
b=Battle(channel_id=1,fighters=[me,a1,a2,foe]); b.started=True

def trig(t):
    r=parse_trigger_text(t)[0]; return r
tests={
 "[Turn Start] Heal 5 to the ally with the lowest HP": ("Hurt",),
 "[Turn Start] Heal 5 to the ally with the fastest Speed": ("Fast",),
 "[Turn Start] Heal 5 to the ally with the highest Max HP": ("Hurt",),
 "[Turn Start] Gain 3 Sanity to the ally with the lowest SP": ("Hurt",),
 "[Turn Start] Heal 5 to the ally with the earliest deployment": ("Fast",),
 "[Turn Start] Heal 5 to the ally with the lowest HP percent": ("Hurt",),
}
for text,(who,) in tests.items():
    t=trig(text)[0]
    assert t.ally_select and t.to_ally, text
    ctx=TriggerContext(caster=me,target=None,battle=b)
    got=select_ally(me,b,t.ally_select); assert got.name==who,(text,got.name)
    print("ok",text,"->",describe_trigger(t))
# status selector
a1.statuses["charge"]=__import__("game.statuses",fromlist=["StatusInstance"]).StatusInstance(name="charge",potency=1,count=4) if False else None
# condition: ally_rank
t=trig("[Ally Turn Start] If the ally has the lowest HP, Heal 5 to the ally")[0]
assert evaluate_condition(t.condition,TriggerContext(caster=me,target=None,battle=b,ally=a2))
assert not evaluate_condition(t.condition,TriggerContext(caster=me,target=None,battle=b,ally=a1))
assert not evaluate_condition(t.condition,TriggerContext(caster=me,target=None,battle=b))
# bind + apply
from game.skills import resolve_triggers, Skill
sk=Skill(name="p",base_power=0,coin_power=0,coins=0,triggers=trig("[Turn Start] Heal 5 to the ally with the lowest HP"))
_,post=resolve_triggers(sk,TriggerContext(caster=me,target=None,battle=b),"turn_start")
assert post[0].selected_ally is a2
log=apply_trigger_effects(post,me,None); print(log); assert a2.hp==25 and a1.hp==90 and me.hp==50
# with explicit ally argument, selector still wins
_,post=resolve_triggers(sk,TriggerContext(caster=me,target=None,battle=b,ally=a1),"turn_start")
apply_trigger_effects(post,me,None,ally=a1); assert a2.hp==30 and a1.hp==90
# no allies
solo=Battle(channel_id=2,fighters=[me,foe]); _,post=resolve_triggers(sk,TriggerContext(caster=me,target=None,battle=solo),"turn_start")
assert apply_trigger_effects(post,me,None)==[] and me.hp==50
# round trip
t0=trig("[Turn Start] Heal 5 to the ally with the lowest HP")[0]
assert Trigger.from_dict(t0.to_dict()).ally_select=="hp|min"
old=t0.to_dict(); del old["ally_select"]; assert Trigger.from_dict(old).ally_select is None
# bad
for bad in ["[Turn Start] Heal 5 to the ally with the shiniest hat","[Turn Start] Deal +3 damage to the ally with the lowest HP","[Ally On Hit] If the ally has the best hat, Heal 3"]:
    try: trig(bad); print("NOT REJECTED",bad)
    except TriggerParseError as e: print("rejected:",e.reason[:80])
print("all ok")
# passive sweep end to end
me.passive_skills=[build_passive_skill("Cover","[Turn Start] Heal 4 to the ally with the lowest HP",[],"support") ] if False else me.passive_skills
import game.passives as gp
print([n for n in dir(gp) if 'build' in n or 'parse' in n])
sk=build_passive_skill({"name":"Cover","kind":"support","lines":["[Turn Start] Heal 4 to the ally with the lowest HP","[Ally Is Hit] If the ally has the lowest HP, Gain 2 Sanity to the ally"]})
assert sk is not None
me.passive_skills=[sk]
b.started=True
a2.hp=25
log=fire_passive_triggers(b); print(log); assert a2.hp==29
print("passive sweep ok")
