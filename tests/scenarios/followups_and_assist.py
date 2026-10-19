import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, asyncio, random; sys.path.insert(0,ROOT)
import warnings; warnings.simplefilter("ignore")
from unittest.mock import AsyncMock, MagicMock
import cogs.battle as cb
from game.battle import Battle, Fighter
from game.skills import Skill
from game.conditions import parse_trigger_text, TriggerParseError, Trigger
from game.passives import build_passive_skill, parse_passive

def P(t): return parse_trigger_text(t)[0][0]
# parsing
t=P("[Ally On Hit] Use Slash Combo as a follow-up attack (once per turn)"); assert t.effect_type=="followup_attack" and t.status_name=="slash combo" and t.max_per_turn==1
assert P("[Ally On Kill] Use Strike as a followup attack (once per Encounter)").max_per_encounter==1
for bad in ["[Ally On Hit] Use Strike as a follow-up attack","[Turn Start] Use Strike as a follow-up attack (once per turn)","[Ally On Hit] Take the hit for the ally","[Ally Before Getting Hit] Deal +3 damage","[Ally Before Getting Hit] Take the hit for the ally next turn"]:
    try: P(bad); print("NOT REJECTED",bad)
    except TriggerParseError as e: pass
assert P("[Ally Before Getting Hit] Take the hit for the ally (once per turn)").effect_type=="assist_defense"
assert P("[Ally Before Getting Hit] If the ally has the lowest HP, Take the hit for the ally (once per turn)").condition.type=="ally_rank"
trs,err=parse_passive(["[Ally Before Getting Hit] Take the hit for the ally (once per turn)"]); assert err is None,err
assert Trigger.from_dict(t.to_dict()).status_name=="slash combo"

def F(n,side,hp=500,power=10,skills=()):
    f=Fighter(name=n,side=side,hp=hp,max_hp=hp,power=power); f.slot_speeds=[10,10,10]; f.skill_slots=3
    for s in skills: f.skills[s.name.lower()]=s
    return f
def S(name,coins=2,base=5,coin=2,**kw): return Skill(name=name,base_power=base,coin_power=coin,coins=coins,**kw)

async def run(setup, passives, label):
    hero,buddy,foe1,foe2=setup()
    for f,ps in passives.items(): f.passive_skills=[build_passive_skill({"name":"P","kind":"passive","lines":ps})]
    b=Battle(channel_id=1,fighters=[hero,buddy,foe1,foe2]); b.started=True
    return b

def make():
    strike=S("Strike")
    hero=F("Hero","a",skills=[S("Slash")]); buddy=F("Buddy","a",skills=[strike,S("Wait")])
    foe1=F("Foe1","b",skills=[S("Hit")]); foe2=F("Foe2","b",skills=[S("Hit")])
    return hero,buddy,foe1,foe2

async def scenario(kind):
    hero,buddy,foe1,foe2=make()
    if kind=="followup":
        buddy.passive_skills=[build_passive_skill({"name":"P","kind":"passive","lines":["[Ally On Hit] Use Strike as a follow-up attack (once per turn)"]})]
        # Hero hits Foe1 unopposed; others attack nobody relevant
        assert hero.declare_in_slot(1,hero.skills["slash"],foe1,1)
        assert buddy.declare_in_slot(1,buddy.skills["wait"],foe2,1)
        assert foe1.declare_in_slot(1,foe1.skills["hit"],buddy,1)
        assert foe2.declare_in_slot(1,foe2.skills["hit"],buddy,1)
    else:
        buddy.passive_skills=[build_passive_skill({"name":"P","kind":"passive","lines":["[Ally Before Getting Hit] Take the hit for the ally (once per turn)"]})]
        assert hero.declare_in_slot(1,hero.skills["slash"],foe2,1)
        assert buddy.declare_in_slot(1,buddy.skills["wait"],foe2,1)
        assert foe1.declare_in_slot(1,foe1.skills["hit"],hero,1)   # unopposed attack on Hero? Hero attacks foe2, so foe1->hero is solo
        assert foe2.declare_in_slot(1,foe2.skills["hit"],buddy,1)
    b=Battle(channel_id=1,fighters=[hero,buddy,foe1,foe2]); b.started=True
    cog=cb.BattleCog(MagicMock()); cog.battles[1]=b
    inter=MagicMock(); inter.channel_id=1; inter.response.defer=AsyncMock(); inter.response.send_message=AsyncMock()
    msg=MagicMock(); msg.edit=AsyncMock(); inter.followup.send=AsyncMock(return_value=msg); inter.followup.edit_message=AsyncMock()
    inter.channel.send=AsyncMock(return_value=MagicMock(edit=AsyncMock(),delete=AsyncMock())); inter.channel.fetch_message=AsyncMock()
    texts=[]
    async def edit(embed=None,**k): texts.append(embed.description if embed else "")
    msg.edit=edit
    import asyncio as aio
    real=aio.sleep
    async def nosleep(*a,**k): return None
    cb.asyncio.sleep=nosleep
    await cb.BattleCog.combat.callback(cog,inter)
    cb.asyncio.sleep=real
    return b,texts,inter

async def main():
    random.seed(3)
    b,texts,inter=await scenario("followup")
    last=texts[-1] if texts else ""
    print("FOLLOWUP last render:\n", last[-900:])
    assert "**Buddy** hits **Foe1**" in last, "no follow-up"
    heroline=last.index("**Hero** hits **Foe1**"); assert last.index("**Buddy** hits **Foe1**")>heroline
    b,texts,inter=await scenario("assist")
    last=texts[-1]
    print("ASSIST last render:\n", last[-900:])
    hero=b.get_fighter("Hero"); buddy=b.get_fighter("Buddy"); assert hero.hp==500, hero.hp; assert "**Foe1** hits **Buddy**" in last
asyncio.run(main())
print("smoke ok")

# build_followup_units rules
def mk():
    hero,buddy,foe1,foe2=make(); foe3=F("Foe3","b")
    b=Battle(channel_id=2,fighters=[hero,buddy,foe1,foe2,foe3]); return b,buddy,foe1,foe2,foe3
b,buddy,foe1,foe2,foe3=mk()
buddy.followup_queue=[(1,"strike",foe1)]; units=cb.build_followup_units(b); assert len(units)==1 and units[0][1]["target"] is foe1 and units[0][1]["extra_targets"]==[]
foe1.hp=0; buddy.followup_queue=[(1,"strike",foe1)]; assert cb.build_followup_units(b)==[]   # cancelled
buddy.skills["strike"]=S("Strike",attack_weight=2)
buddy.followup_queue=[(1,"strike",foe1)]; u=cb.build_followup_units(b); assert len(u)==1 and u[0][1]["target"] in (foe2,foe3) and len(u[0][1]["extra_targets"])==1
foe1.hp=500; buddy.followup_queue=[(1,"strike",foe1)]; u=cb.build_followup_units(b); assert u[0][1]["target"] is foe1 and len(u[0][1]["extra_targets"])==1
buddy.followup_queue=[(1,"nothing",foe1)]; assert cb.build_followup_units(b)==[]
buddy.skills["strike"]=S("Strike",tags={"guard"}); buddy.followup_queue=[(1,"strike",foe1)]; assert cb.build_followup_units(b)==[]
buddy.skills["strike"]=S("Strike"); buddy.hp=0; buddy.followup_queue=[(1,"strike",foe1)]; assert cb.build_followup_units(b)==[]
print("followup rules ok")
