import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, asyncio, random, re; sys.path.insert(0,ROOT)
import warnings; warnings.simplefilter("ignore")
from unittest.mock import AsyncMock, MagicMock
import cogs.battle as cb
from game.battle import Battle, Fighter
from game.skills import Skill
from game.conditions import parse_trigger_text, TriggerContext, evaluate_condition, adjacent_allies, TriggerParseError
from game.passives import parse_passive
from game.build_points import check_build

def F(n,side,hp=500):
    f=Fighter(name=n,side=side,hp=hp,max_hp=hp,power=10); f.slot_speeds=[10,10,10]; f.skill_slots=3
    return f
def passive(text):
    trs,_=parse_trigger_text(text)
    return Skill(name="P",base_power=0,coin_power=0,coins=1,triggers=trs,tags={"passive"})

# adjacency helper
a,b,c,d=F("A","a"),F("B","a"),F("C","a"),F("D","a"); e=F("E","b")
bt=Battle(channel_id=1,fighters=[a,e,b,c,d])
assert [x.name for x in adjacent_allies(a,bt)]==["B"]
assert [x.name for x in adjacent_allies(b,bt)]==["A","C"]
assert [x.name for x in adjacent_allies(d,bt)]==["C"]
c.hp=0
assert [x.name for x in adjacent_allies(b,bt)]==["A","D"]  # downed C leaves the line
c.hp=500
# conditions
for txt,exp in [("If an ally is adjacent,",True),("If 2+ allies are adjacent,",True)]:
    trs,_=parse_trigger_text(f"[Turn Start] {txt} Gain 1 Sanity")
    ctx=TriggerContext(caster=b,target=None,battle=bt)
    assert evaluate_condition(trs[0].condition,ctx)==exp
trs,_=parse_trigger_text("[Turn Start] If 2+ allies are adjacent, Gain 1 Sanity")
assert not evaluate_condition(trs[0].condition,TriggerContext(caster=a,target=None,battle=bt))
try: parse_trigger_text("[Turn Start] If 3+ allies are adjacent, Gain 1 Sanity"); raise SystemExit("no error")
except TriggerParseError: pass
# adjacent target effect applies to each neighbour, round trip through save/load
trs,_=parse_trigger_text("[Combat Start] Gain 8 Shield to the adjacent allies")
assert trs[0].to_adjacent
from game.conditions import Trigger
t2=Trigger.from_dict(trs[0].to_dict()); assert t2.to_adjacent
sk=Skill(name="P",base_power=0,coin_power=0,coins=1,triggers=trs,tags={"passive"})
b.passive_skills=[sk]
bt.started=False; cb.fire_passive_triggers(bt)
print("shields:", a.shield,b.shield,c.shield,d.shield)
assert (a.shield,b.shield,c.shield,d.shield)==(8,0,8,0)
# passive parse + heal
tr,err=parse_passive(["[Turn Start] Heal 5 to the adjacent allies"]); assert not err,err
tr,err=parse_passive(["[Turn Start] Deal +5 damage to the adjacent allies"]); assert err
print("adjacency ok")

# failed hit / kill via fire_failed_triggers
trs,_=parse_trigger_text("[Failed Hit] Gain 3 Sanity\n[Failed Kill] Gain 7 Sanity")
sk=Skill(name="S",base_power=5,coin_power=0,coins=1,triggers=trs)
ok=F("Atk","a"); tg=F("Tgt","b")
bt2=Battle(channel_id=1,fighters=[ok,tg]); ctx=TriggerContext(caster=ok,target=tg,battle=bt2)
cb.fire_failed_triggers(ok,tg,sk,ctx,True,0); assert ok.sanity==3
cb.fire_failed_triggers(ok,tg,sk,ctx,False,0); assert ok.sanity==6
cb.fire_failed_triggers(ok,tg,sk,ctx,False,10); assert ok.sanity==13   # alive
tg.hp=0; cb.fire_failed_triggers(ok,tg,sk,ctx,False,10); assert ok.sanity==13  # killed
g=Skill(name="G",base_power=5,coin_power=0,coins=1,triggers=trs,tags={"guard"})
cb.fire_failed_triggers(ok,tg,g,ctx,True,0); assert ok.sanity==13
print("failed units ok")

# full combat: failed hit when evaded/0 damage and failed kill when survived
async def go(seed,atk_power,hp):
    random.seed(seed)
    hero=F("Hero","a"); foe=F("Foe","b",hp=hp)
    s=Skill(name="Strike",base_power=atk_power,coin_power=0,coins=1,triggers=parse_trigger_text("[Failed Hit] Gain 3 Sanity\n[Failed Kill] Gain 7 Sanity")[0])
    hero.skills["strike"]=s
    foe.skills["idle"]=Skill(name="Idle",base_power=1,coin_power=0,coins=1)
    assert hero.declare_in_slot(1,s,foe,1)
    foe.skill_slots=1; hero.skill_slots=1
    assert foe.declare_in_slot(1,foe.skills["idle"],hero,1)
    b=Battle(channel_id=1,fighters=[hero,foe]); b.started=True
    cog=cb.BattleCog(MagicMock()); cog.battles[1]=b
    inter=MagicMock(); inter.channel_id=1; inter.response.defer=AsyncMock(); inter.response.send_message=AsyncMock()
    msg=MagicMock(); msg.edit=AsyncMock(); inter.followup.send=AsyncMock(return_value=msg)
    inter.channel.send=AsyncMock(return_value=MagicMock(edit=AsyncMock(),delete=AsyncMock()))
    real=cb.asyncio.sleep
    async def nosleep(*a,**k): return None
    cb.asyncio.sleep=nosleep
    await cb.BattleCog.combat.callback(cog,inter)
    cb.asyncio.sleep=real
    return hero,foe
async def main():
    hero,foe=await go(1,50,500)
    # unopposed hit that landed and target survived: failed kill +7 on top of normal sanity from coin
    print("survive sanity",hero.sanity, foe.hp)
    print("hero win?", foe.hp, hero.hp)
    h2,f2=await go(1,50,5)
    print("kill sanity",h2.sanity,f2.hp)
asyncio.run(main())
