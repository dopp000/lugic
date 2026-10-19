import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, asyncio, random; sys.path.insert(0,ROOT)
import warnings; warnings.simplefilter("ignore")
from unittest.mock import AsyncMock, MagicMock
import cogs.battle as cb
from game.battle import Battle, Fighter, Part
from game.skills import Skill

def F(n,side,hp=500):
    f=Fighter(name=n,side=side,hp=hp,max_hp=hp,power=10); f.slot_speeds=[10,10,10]; f.skill_slots=3
    return f
# unit: thresholds default
b=F("Boss","b",hp=1000); b.parts[2]=Part(name="Arm",hp=100,max_hp=100)
print(b.stagger_thresholds,b.stagger_tiers_enabled)
assert b.check_part_stagger(2,1)==0
b.parts[2].hp=50
t=b.check_part_stagger(2,1); print("tier",t)
assert t>=1 and b.parts[2].stagger_tier==t
assert b.slot_disabled(2) and not b.slot_disabled(1)
assert b.stagger_tier_for_slot(2)==t and b.stagger_tier_for_slot(1)==0 and b.stagger_tier_for_slot(None)==0
b.current_stagger_tier=3; assert b.stagger_tier_for_slot(1)==3 and b.stagger_tier_for_slot(2)==3
b.current_stagger_tier=0
# expiry
b.clear_expired_stagger(1)
if t==1: assert b.parts[2].stagger_tier==0
else:
    assert b.parts[2].stagger_tier==t; b.clear_expired_stagger(2); assert b.parts[2].stagger_tier==0
# stagger_immune
b.parts[2].hp=1; b.stagger_immune=True; assert b.check_part_stagger(2,5)==0
# no part
assert b.check_part_stagger(1,1)==0
print("units ok")

# hit multiplier through apply_incoming_hit
from game.skills import resolve_skill
atk=F("Atk","a"); tgt=F("Tgt","b",hp=1000); tgt.parts[1]=Part(name="Arm",hp=100,max_hp=100)
sk=Skill(name="S",base_power=20,coin_power=0,coins=1)
res=resolve_skill(sk,50)
d0,_,_=cb.apply_incoming_hit(sk,res,tgt,atk,1)
tgt.parts[1].stagger_tier=1
d1,log,_=cb.apply_incoming_hit(sk,res,tgt,atk,1)
d2,_,_=cb.apply_incoming_hit(sk,res,tgt,atk,2)  # other slot, no part
print(d0,d1,d2); assert d1==round(d0*1.5) and d2==d0
print("multiplier ok")

# full combat: hit the part below threshold, check stagger line and disabled slot
async def go():
    random.seed(3)
    hero=F("Hero","a"); boss=F("Boss","b",hp=2000); boss.parts[1]=Part(name="Arm",hp=100,max_hp=100)
    hero.skill_slots=1; boss.skill_slots=2
    s=Skill(name="Strike",base_power=60,coin_power=0,coins=1)
    hero.skills["strike"]=s
    boss.skills["idle"]=Skill(name="Idle",base_power=1,coin_power=0,coins=1)
    assert hero.declare_in_slot(1,s,boss,1)
    assert boss.declare_in_slot(2,boss.skills["idle"],hero,1)
    bt=Battle(channel_id=1,fighters=[hero,boss]); bt.started=True
    cog=cb.BattleCog(MagicMock()); cog.battles[1]=bt
    inter=MagicMock(); inter.channel_id=1; inter.response.defer=AsyncMock(); inter.response.send_message=AsyncMock()
    texts=[]
    async def edit(embed=None,**k): texts.append((embed.description or "") + "\n".join(f.value for f in embed.fields) if embed else "")
    msg=MagicMock(); msg.edit=edit; inter.followup.send=AsyncMock(return_value=msg)
    inter.channel.send=AsyncMock(return_value=MagicMock(edit=AsyncMock(),delete=AsyncMock()))
    real=cb.asyncio.sleep
    async def nosleep(*a,**k): return None
    cb.asyncio.sleep=nosleep
    await cb.BattleCog.combat.callback(cog,inter)
    cb.asyncio.sleep=real
    return boss,"\n".join(texts)
REC=[]
_o=cb.part_stagger_step
def _w(*a,**k):
    r=_o(*a,**k); REC.append(r); return r
cb.part_stagger_step=_w
boss,full=asyncio.run(go())
print(REC)
assert REC and REC[0][0] and 'Staggered (Tier' in REC[0][1][0]

print(boss.parts[1], boss.hp)
assert boss.parts[1].broken or boss.parts[1].stagger_tier>0 or boss.parts[1].hp>0
assert "Arm" in full
print(full[-1500:])
print("combat ok")
