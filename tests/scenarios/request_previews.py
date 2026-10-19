import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, os, asyncio, tempfile; sys.path.insert(0,ROOT); os.chdir(tempfile.mkdtemp())
import warnings; warnings.simplefilter("ignore")
from unittest.mock import AsyncMock, MagicMock, patch
from game.character import Character, save_character, load_character
from game.passives import parse_passive, tag_warnings
import game.build_requests as br
import cogs.build_requests as cb
a=Character(name="Alpha",owner_id=7); a.tags=["N Corp"]; a.pp_total=3; save_character(a)
b=Character(name="Beta",owner_id=7); b.pp_total=3; save_character(b)
print(br.known_tags())
tr,_=parse_passive(["[Before Use] If 2+ units on your side have the N Cop tag, Deal +10% damage","[Before Attack] If the target has the N Corp tag, Coin Power +1"])
w=tag_warnings(tr,br.known_tags()); print(w); assert len(w)==1 and "n corp" in w[0].lower()
# cost preview
c=load_character("Beta")
s,e,ch=br.try_change(c,"stats",{"hp":120}); print(br.cost_preview(c,ch))
s,e,ch=br.try_change(load_character("Alpha"),"passive",{"name":"X","kind":"passive","lines":["[Combat Start] Heal 5"],"cost":2,"note":""}); print(br.cost_preview(load_character("Alpha"),ch))
# submit shows preview + warning
bot=MagicMock(); cog=cb.RequestCog.__new__(cb.RequestCog); cog.bot=bot; bot.get_channel.return_value=None
def mk(uid):
    i=MagicMock(); i.user.id=uid; i.client=bot; i.response.send_message=AsyncMock(); return i
async def run():
    with patch.object(cb,"is_admin",lambda x: False):
        i=mk(7)
        await cb.RequestCog.passive.callback(cog,i,"Beta","Typo",MagicMock(value="passive"),2,"[Before Use] If 2+ units on your side have the N Cop tag, Deal +10% damage","why")
        print(i.response.send_message.call_args.args[0])
        i2=mk(7); await cb.RequestCog.mine.callback(cog,i2,"alpha"); print(i2.response.send_message.call_args.args[0])
        i3=mk(7); await cb.RequestCog.mine.callback(cog,i3,"Beta"); print(i3.response.send_message.call_args.args[0])
    with patch.object(cb,"is_admin",lambda x: True):
        i4=mk(1); await cb.RequestCog.list_open.callback(cog,i4,"Beta",False); print(i4.response.send_message.call_args.args[0])
        i5=mk(1); await cb.RequestCog.list_open.callback(cog,i5,"Alpha",True); print(i5.response.send_message.call_args.args[0])
        emb=cb.request_embed(br.get_request(1)); print([ (f.name,f.value) for f in emb.fields])
asyncio.run(run())
