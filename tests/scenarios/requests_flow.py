import os as _os
ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys, os, asyncio, tempfile, copy
sys.path.insert(0,ROOT); os.chdir(tempfile.mkdtemp())
from unittest.mock import AsyncMock, MagicMock, patch
import game.build_requests as br
from game.character import Character, save_character, load_character
from game.build_points import check_build
c=Character(name="Tester", owner_id=7)
print(c.hp, c.power, c.pp_total, c.tags)
save_character(c)
# pure
for kind,params in [
 ("stats",{"hp":110}),("stats",{"power":12,"hp":160}),("stats",{}),
 ("rank",{"status":"bleed","rank":2}),("rank",{"status":"bleed","rank":5}),
 ("archetype",{"archetype":"guard"}),("archetype",{"archetype":"counter"}),
 ("tags",{"tags":["N Corp","n corp","Dawn Office"]}),
 ("passive",{"name":"P","kind":"passive","lines":["[Combat Start] Heal 5"],"cost":2,"note":""}),
]:
    print(kind,params,"->",br.try_change(c,kind,params)[:2])
c.pp_total=3
print("passive w/ pp", br.try_change(c,"passive",{"name":"P","kind":"passive","lines":["[Combat Start] Heal 5"],"cost":2,"note":""})[:2])
print("bad line", br.try_change(c,"passive",{"name":"P","kind":"passive","lines":["[Clash Win] Heal 5"],"cost":2,"note":""})[:2])
print("followup missing", br.try_change(c,"passive",{"name":"P","kind":"passive","lines":["[Ally On Hit] Use Pounce as a follow-up attack (once per turn)"],"cost":2,"note":""})[:2])
# store
r=br.create_request(7,"Tester","rank",{"status":"bleed","rank":2},"x"*500)
print(r.id,len(r.reason),br.open_count(7),br.has_open_duplicate(7,"tester","rank"),br.get_request(1).status)
r.status="denied"; br.save_request(r); print(br.list_requests(status="open"), br.describe_request(r))
# cog flow
import cogs.build_requests as cb
bot=MagicMock(); cog=cb.RequestCog.__new__(cb.RequestCog); cog.bot=bot
def mk(admin, uid):
    i=MagicMock(); i.user.id=uid; i.client=bot; i.response.send_message=AsyncMock(); i.response.edit_message=AsyncMock(); i.response.send_modal=AsyncMock()
    i.message=MagicMock(); return i
chan=MagicMock(); chan.id=99; sent=MagicMock(); sent.id=555; chan.send=AsyncMock(return_value=sent); bot.get_channel.return_value=chan
br.set_review_channel(99)
async def run():
    i=mk(False,7)
    with patch.object(cb,"is_admin",lambda x: x is adm):
        await cb.RequestCog.rank.callback(cog,i,"Tester",MagicMock(value="bleed"),2,"story")
        print("submit:", i.response.send_message.call_args.args[0])
        await cb.RequestCog.rank.callback(cog,i,"Tester",MagicMock(value="bleed"),3,"dup")
        print("dup:", i.response.send_message.call_args.args[0])
        rid=br.list_requests(status="open")[0].id
        # non admin tries button
        btn=cb.ReviewButton("approve",rid); ni=mk(False,7)
        await btn.callback(ni); print("nonadmin:", ni.response.send_message.call_args.args[0])
        bot.fetch_user=AsyncMock(); u=MagicMock(); u.send=AsyncMock(); bot.get_user.return_value=u
        await btn.callback(adm);
        print("approved:", load_character("Tester").status_ranks, br.get_request(rid).status, u.send.call_args.args[0])
        await btn.callback(adm); print("again:", adm.response.send_message.call_args.args[0])
        # deny flow
        i2=mk(False,7); await cb.RequestCog.tags.callback(cog,i2,"Tester","why","A","B")
        rid2=br.list_requests(status="open")[0].id
        m=cb.DenyModal(rid2); m.note._value="not now"
        await m.on_submit(adm); print("denied:", br.get_request(rid2).status, br.get_request(rid2).decision_note, u.send.call_args.args[0])
        # passive approve w/ cost modal
        c=load_character("Tester"); c.pp_total=4; save_character(c)
        i3=mk(False,7)
        pcb=cb.RequestCog.passive.callback
        await pcb(cog,i3,"Tester","Calm",MagicMock(value="passive"),1,"[Combat Start] Heal 5","story")
        rid3=br.list_requests(status="open")[0].id
        cm=cb.CostModal(rid3); cm.cost._value="3"; await cm.on_submit(adm)
        print("passive:", load_character("Tester").passives, br.get_request(rid3).status)
        # limit
        for k in range(4):
            ii=mk(False,7); await cb.RequestCog.tags.callback(cog,ii,"Tester","w",f"t{k}")
            print(k, ii.response.send_message.call_args.args[0][:70])
adm=mk(True,1)
asyncio.run(run())
