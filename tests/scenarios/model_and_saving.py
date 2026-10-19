import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys; sys.path.insert(0,ROOT)
import warnings; warnings.simplefilter("ignore")
from game.battle import Battle, Fighter
from game.character import Character
from game.conditions import *
from game.trigger_text import describe_trigger
def P(t): return parse_trigger_text(t)[0][0]
def F(n,side,hp=50,tags=()):
    f=Fighter(name=n,side=side,hp=hp,max_hp=100); f.tags=list(tags); return f
a=F("a","x",tags=["n corp"]); b=F("b","x",tags=["n corp","dawn office"]); c=F("c","x",50,["n corp"]); d=F("d","y",tags=["dawn office"])
bt=Battle(channel_id=1,fighters=[a,b,c,d])
def ev(text,caster,target=None):
    t=P(text); return evaluate_condition(t.condition,TriggerContext(caster=caster,target=target,battle=bt))
T="[Turn Start] If 3+ units on your side have the N  Corp tag, Gain 2 Sanity"
assert ev(T,a); c.hp=0; assert not ev(T,a); c.hp=50
assert ev("[Turn Start] If 2+ units on your side have the Dawn Office tag, Gain 2 Sanity",a) is False
assert ev("[On Use] If the target has the Dawn Office tag, Deal +3 damage",a,d) and not ev("[On Use] If the target has the Dawn Office tag, Deal +3 damage",a,c)
D="[Turn Start] If 1+ allies are defeated, Gain 2 Sanity"
assert not ev(D,a); b.hp=0; assert ev(D,a); b.hp=50; c.eliminated=True; assert ev(D,a); c.eliminated=False
d.hp=0; assert not ev(D,a)   # enemy down does not count
print(describe_trigger(P(T)), "|", describe_trigger(P(D)))
# model
ch=Character.from_dict({**Character(name="Z",owner_id=1).to_dict()}) if hasattr(Character,"from_dict") else None
ch.tags=["N Corp","Dawn Office"]; assert Character.from_dict(ch.to_dict()).tags==["N Corp","Dawn Office"]
dd=ch.to_dict(); del dd["tags"]; assert Character.from_dict(dd).tags==[]
assert Fighter.from_character(ch,"x").tags==["n corp","dawn office"]
import cogs.character
print("all ok")
