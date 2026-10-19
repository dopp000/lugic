import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, asyncio, random, re; sys.path.insert(0,ROOT)
import warnings; warnings.simplefilter("ignore")
from unittest.mock import AsyncMock, MagicMock
import cogs.battle as cb
from game.battle import Battle, Fighter
from game.skills import Skill
from game.statuses import StatusInstance
from game.conditions import parse_trigger_text

def F(n,side,hp=500):
    f=Fighter(name=n,side=side,hp=hp,max_hp=hp,power=10); f.slot_speeds=[10,10,10]; f.skill_slots=3
    return f
def passive(text):
    trs,_=parse_trigger_text(text)
    return Skill(name="P",base_power=0,coin_power=0,coins=1,triggers=trs,tags={"passive"})

# 1. Burn death
a=F("Ally","a"); v=F("Victim","a",hp=2); e=F("Foe","b")
v.passive_skills=[passive("[Self On Death] Gain 5 Sanity to the ally with the lowest HP")]
a.hp=300
v.statuses["burn"]=StatusInstance(name="burn",potency=5,count=2)
b=Battle(channel_id=1,fighters=[a,v,e]);
log=cb.apply_turn_end_status_ticks(b)
assert not v.is_alive() and a.sanity==5, (v.hp,a.sanity,log)
assert v.death_reacted
log2=cb.apply_turn_end_status_ticks(b)
assert a.sanity==5   # no second fire
print("burn death ok")

# 2. Poison death then burn doesn't double
a=F("Ally","a"); v=F("Victim","a",hp=2); e=F("Foe","b")
v.passive_skills=[passive("[Self On Death] Gain 5 Sanity to the ally with the lowest HP")]
a.hp=300
v.statuses["poison"]=StatusInstance(name="poison",potency=1,count=5)
v.statuses["burn"]=StatusInstance(name="burn",potency=5,count=2)
b=Battle(channel_id=1,fighters=[a,v,e]); cb.apply_turn_end_status_ticks(b)
assert a.sanity==5,(a.sanity)
print("poison death ok")

# 3. Bleed death
a=F("Ally","a"); v=F("Victim","a",hp=2); e=F("Foe","b"); a.hp=300
v.passive_skills=[passive("[Self On Death] Gain 5 Sanity to the ally with the lowest HP")]
v.statuses["bleed"]=StatusInstance(name="bleed",potency=3,count=2)
b=Battle(channel_id=1,fighters=[a,v,e])
sk=Skill(name="Swing",base_power=5,coin_power=0,coins=2)
cb.apply_bleed_self_damage(v,sk,b)
assert not v.is_alive() and a.sanity==5
print("bleed death ok")

# 4. Indiscriminate follow-up fallback
o=F("Owner","a"); t1=F("T1","b"); t2=F("T2","b")
sk=Skill(name="Wild",base_power=5,coin_power=0,coins=1,tags={"indiscriminate"})
o.skills["wild"]=sk; t1.hp=0
o.followup_queue=[(1,"wild",t1)]
b=Battle(channel_id=1,fighters=[o,t1,t2])
u=cb.build_followup_units(b); assert len(u)==1 and u[0][1]["target"] is t2
plain=Skill(name="Plain",base_power=5,coin_power=0,coins=1); o.skills["plain"]=plain
o.followup_queue=[(1,"plain",t1)]
assert cb.build_followup_units(b)==[]
print("indiscriminate ok")

# 5. Volley per-coin conditions
lines="\n".join(f":Coin{i}: [On Hit] If the target's HP is 30% or less, Inflict +1 Burn Count" for i in range(1,5))
trs,flags=parse_trigger_text("[Unfocused Volley]\n"+lines)
volley=Skill(name="Volley",base_power=10,coin_power=0,coins=4,triggers=trs,tags=set(flags))
REC=[]
_orig=cb.apply_trigger_effects
def _wrap(triggers, caster, target, ally=None):
    for t in triggers:
        if t.effect_type=="inflict_status" and t.status_name=="burn" and target is not None and caster.name=="Hero":
            REC.append((t.coin_index,target.name))
    return _orig(triggers, caster, target, ally=ally)
cb.apply_trigger_effects=_wrap
async def go(seed):
    REC.clear(); random.seed(seed)
    hero=F("Hero","a"); f1=F("Foe1","b"); f2=F("Foe2","b",hp=500); f2.hp=100; f3=F("Foe3","b")
    hero.skills["volley"]=volley
    for f,bp in ((f1,3),(f2,8),(f3,5)): f.skills["hit"]=Skill(name="Hit",base_power=bp,coin_power=0,coins=1)
    assert hero.declare_in_slot(1,volley,f1,1)
    f1.declare_in_slot(1,f1.skills["hit"],f2,1); f2.declare_in_slot(1,f2.skills["hit"],f1,1); f3.declare_in_slot(1,f3.skills["hit"],f1,1)
    b=Battle(channel_id=1,fighters=[hero,f1,f2,f3]); b.started=True
    cog=cb.BattleCog(MagicMock()); cog.battles[1]=b
    inter=MagicMock(); inter.channel_id=1; inter.response.defer=AsyncMock(); inter.response.send_message=AsyncMock()
    texts=[]
    async def edit(embed=None,**k): texts.append(embed.description if embed else "")
    msg=MagicMock(); msg.edit=edit; inter.followup.send=AsyncMock(return_value=msg)
    inter.channel.send=AsyncMock(return_value=MagicMock(edit=AsyncMock(),delete=AsyncMock()))
    real=cb.asyncio.sleep
    async def nosleep(*a,**k): return None
    cb.asyncio.sleep=nosleep
    await cb.BattleCog.combat.callback(cog,inter)
    cb.asyncio.sleep=real
    return "\n".join(texts)
async def main():
    for seed in range(10):
        full=await go(seed)
        note=re.search(r"Unfocused Volley: (.*)",full)
        picks=dict((int(a),c) for a,c in re.findall(r"coin (\d+) -> (\w+)",note.group(1)))
        burns=dict(REC)
        expect={i:c for i,c in picks.items() if c=="Foe2"}
        assert burns==expect,(seed,picks,burns)
    print("volley per-coin conditions ok")
asyncio.run(main())
