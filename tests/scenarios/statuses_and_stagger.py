import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys; sys.path.insert(0,ROOT)
import warnings; warnings.simplefilter("ignore")
from game.battle import Battle, Fighter
from game.conditions import *
from game.skills import Skill, resolve_triggers
from game.passives import build_passive_skill, parse_passive
from cogs.battle import apply_trigger_effects, fire_hit_ally_reactions, fire_passive_triggers
def P(t): return parse_trigger_text(t)[0][0]

# parsing
for text,et in [("[Turn Start] HP cannot drop below 1 this turn","hp_floor"),("[Turn Start] Nullify the next lethal damage","nullify_lethal"),
  ("[Turn Start] Cannot be Staggered this turn","stagger_immune"),("[Self Is Hit] Recover from Stagger","remove_stagger"),
  ("[Turn Start] Nullify lethal damage (once per Encounter)","nullify_lethal")]:
    try: t=P(text)
    except TriggerParseError as e:
        # Self Is Hit is passive only for skills? parse alone should be fine
        raise
    assert t.effect_type==et,(text,t.effect_type)
assert P("[Turn Start] Nullify lethal damage (once per Encounter)").max_per_encounter==1
assert P("[Turn Start] Gain 2 Sanity (2 times per Encounter)").max_per_encounter==2
assert P("[Turn Start] Gain 2 Sanity (twice per battle)").max_per_encounter==2
for bad in ["[Turn Start] HP cannot drop below 0 this turn","[Turn Start] Gain 2 Sanity (once per scene)","[Turn Start] Nullify lethal damage to the ally"]:
    try: P(bad); print("NOT REJECTED",bad)
    except TriggerParseError as e: pass
t=P("[Turn Start] Gain 2 Sanity (once per Encounter)"); assert Trigger.from_dict(t.to_dict()).max_per_encounter==1
old=t.to_dict(); del old["max_per_encounter"]; assert Trigger.from_dict(old).max_per_encounter==0

def F(name,side,hp=50):
    f=Fighter(name=name,side=side,hp=hp,max_hp=100); f.slot_speeds=[5]; f.stagger_thresholds=[0.5]; f.stagger_tiers_enabled=[True]; return f
# take_damage
f=F("a","a",30); f.hp_floor=1; assert f.take_damage(100)==(0,29) and f.hp==1 and f.drain_survival_notes()
f=F("a","a",30); f.hp_floor=40; assert f.take_damage(5)==(0,0) and f.hp==30   # never raises
f=F("a","a",30); f.nullify_lethal=1; assert f.take_damage(100)==(0,0) and f.hp==30 and f.nullify_lethal==0
assert f.take_damage(100)==(0,30) and f.hp==0
f=F("a","a",30); f.nullify_lethal=1; assert f.take_damage(10)==(0,10) and f.nullify_lethal==1   # non lethal keeps charge
f=F("a","a",30); f.shield=10; f.nullify_lethal=1; assert f.take_damage(100)[1]==0 and f.shield==0 and f.hp==30
# stagger
f=F("a","a",60); f.stagger_immune=True; f.hp=10; f.check_stagger(1); assert f.current_stagger_tier==0
f.stagger_immune=False; f.check_stagger(1); assert f.current_stagger_tier==1

# effects through apply_trigger_effects
me=F("me","a"); foe=F("foe","b"); b=Battle(channel_id=1,fighters=[me,foe]); b.started=True
sk=Skill(name="p",base_power=0,coin_power=0,coins=0,triggers=[P("[Turn Start] HP cannot drop below 1 this turn"),P("[Turn Start] Nullify the next lethal damage"),P("[Turn Start] Cannot be Staggered this turn")])
_,post=resolve_triggers(sk,TriggerContext(caster=me,target=None,battle=b),"turn_start")
print(apply_trigger_effects(post,me,None)); assert me.hp_floor==1 and me.nullify_lethal==1 and me.stagger_immune
me.current_stagger_tier=1
_,post=resolve_triggers(Skill(name="r",base_power=0,coin_power=0,coins=0,triggers=[P("[Turn Start] Recover from Stagger")]),TriggerContext(caster=me,target=None,battle=b),"turn_start")
apply_trigger_effects(post,me,None); assert me.current_stagger_tier==0

# encounter limit
me2=F("m2","a"); b2=Battle(channel_id=3,fighters=[me2,foe]); b2.started=True
sk=Skill(name="e",base_power=0,coin_power=0,coins=0,triggers=[P("[Turn Start] Gain 3 Sanity (once per Encounter)")])
fired=[]
for i in range(3):
    me2.trigger_uses={}   # a new round clears per turn counts
    _,post=resolve_triggers(sk,TriggerContext(caster=me2,target=None,battle=b2),"turn_start"); fired.append(len(post))
assert fired==[1,0,0],fired

# self on death
sk=build_passive_skill({"name":"Last","kind":"passive","lines":["[Self On Death] Inflict 3 Burn"]}); assert sk is not None
v=F("v","a",0); v.passive_skills=[sk]; k=F("k","b"); b3=Battle(channel_id=4,fighters=[v,k]); b3.started=True
log=fire_hit_ally_reactions(b3,k,v,10,False,0,False); print(log)
assert k.get_status("burn") is not None
# passive timing accepted via parse_passive
trs,err=parse_passive(["[Self On Death] Inflict 3 Burn"]); assert err is None
# skill refuses it
print("all ok")
