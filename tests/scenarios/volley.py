import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, asyncio, random, re; sys.path.insert(0,ROOT)
import warnings; warnings.simplefilter("ignore")
from unittest.mock import AsyncMock, MagicMock
import cogs.battle as cb
from game.battle import Battle, Fighter
from game.skills import Skill
from game.conditions import parse_trigger_text

def F(n,side,hp=500,skills=()):
    f=Fighter(name=n,side=side,hp=hp,max_hp=hp,power=10); f.slot_speeds=[10,10,10]; f.skill_slots=3
    for s in skills: f.skills[s.name.lower()]=s
    return f
lines="\n".join(f":Coin{i}: [On Hit] Inflict +1 Burn Count" for i in range(1,5))
trs,flags=parse_trigger_text("[Unfocused Volley]\n"+lines); assert "unfocused_volley" in flags,flags
volley=Skill(name="Volley",base_power=10,coin_power=0,coins=4,triggers=trs,tags=set(flags))

REC=[]
_orig_apply=cb.apply_trigger_effects
def _wrap(triggers, caster, target, ally=None):
    for t in triggers:
        if t.effect_type=="inflict_status" and t.status_name=="burn" and target is not None and caster.name=="Hero":
            REC.append((t.coin_index,target.name))
    return _orig_apply(triggers, caster, target, ally=ally)
cb.apply_trigger_effects=_wrap

async def go(seed):
    REC.clear()
    random.seed(seed)
    hero=F("Hero","a",skills=[volley]); f1=F("Foe1","b"); f2=F("Foe2","b"); f3=F("Foe3","b")
    for f in (f1,f2,f3): f.skills["hit"]=Skill(name="Hit",base_power=5,coin_power=0,coins=1)
    assert hero.declare_in_slot(1,volley,f1,1)
    f2.skills["big"]=Skill(name="Big",base_power=40,coin_power=0,coins=1)
    f1.declare_in_slot(1,f1.skills["hit"],f2,1); f2.declare_in_slot(1,f2.skills["big"],f1,1); f3.declare_in_slot(1,f3.skills["hit"],f1,1)
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
    # capture the full log for the volley note
    captured=[]
    orig=cb.BattleCog.combat.callback
    await orig(cog,inter)
    cb.asyncio.sleep=real
    return b,hero,f1,f2,f3,texts

async def main():
    seen_split=False
    for seed in range(12):
        b,hero,f1,f2,f3,texts=await go(seed)
        by_coin=dict(REC)
        assert sorted(by_coin)==[1,2,3,4], (seed,REC)      # one burn per coin, each exactly once
        assert by_coin[1]=="Foe1", (seed,REC)              # coin 1 always the main target
        if len(set(by_coin.values()))>1: seen_split=True
        # damage: Foe-side clashes/hits add to hp loss, so compare with the log note instead
        full="\n".join(texts)
        note=re.search(r"Unfocused Volley: (.*)",full)
        assert note, "no volley note in log"
        picks=dict((int(a),c) for a,c in re.findall(r"coin (\d+) -> (\w+)",note.group(1)))
        assert picks==by_coin,(picks,by_coin)
        last=[t for t in texts if "Unfocused Volley:" in t][-1]
        for name,dmg in re.findall(r"↳ \*\*(\w+)\*\* takes (\d+)",last):
            coins=sum(1 for v in by_coin.values() if v==name)
            assert int(dmg)==10*coins,(name,dmg,coins)
    assert seen_split
    print("volley distribution ok")
asyncio.run(main())
print("smoke ok")
