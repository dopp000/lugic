"""/practice: solo fights for learning, against a Dummy or a random bot, both run by the bot.

`/practice start` and `/practice bot` build an ordinary battle in the channel (the player on
Side A, the opponent on Side B) and register it here. The player then uses the real
/battle declare and /battle combat. The opponent has no owner, so nobody can declare for it:
this cog declares its moves at the start of every round, using the rules in game/practice.py.

`/practice bot` makes the opponent from a real unsaved Character (game/practice_bot.py) with the
same Build Points, Skill Points and Passive Points as the player, so it follows the same rules.
The player can add skills and passives to it by hand (`addskill`, `addpassive`), checked
against the bot's own points exactly like a character's.
"""
import copy
import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from cogs.battle import AddSkillModal, _is_admin, build_battle_embed, sync_battle_message
from cogs.character import build_character_embed
from game import practice as pr
from game import practice_bot as pb
from game.battle import Battle, Fighter
from game.build_points import STARTING_BP, passive_points_spent
from game.character import Character, list_characters_by_owner, load_character
from game.passives import MAX_PASSIVES, build_passive_skill, followup_problem, parse_passive

log = logging.getLogger(__name__)


@dataclass
class Session:
    battle: Battle
    user_id: int
    foe_name: str
    show_plan: bool
    max_moves: int | None  # how many of its slots the opponent uses each round (None: all)
    round: int  # the round the opponent last picked moves for
    bot_char: Character | None = None  # the Character behind a /practice bot opponent
    finished: bool = False
    nag_round: int = 0  # the round we last said the opponent has nothing to play


@dataclass
class Picked:
    fighter: Fighter | None = None
    character: Character | None = None  # None for a Trainee
    lent: str | None = None  # name of a skill lent for this fight
    trainee: bool = False
    problem: str | None = None


SETUP_CHOICES = [
    app_commands.Choice(name="Random", value="random"),
    app_commands.Choice(name="None, I'll add them myself", value="manual"),
]


class PracticeCog(commands.GroupCog, name="practice"):
    """Fight an opponent run by the bot, on your own."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, Session] = {}

    # ---- helpers -----------------------------------------------------------

    def _battles(self) -> dict | None:
        cog = self.bot.get_cog("battle")
        return cog.battles if cog is not None else None

    def session_for(self, channel_id: int | None) -> Session | None:
        """The live session for a channel. It is dropped once its battle is gone or replaced (for example by /battle end)."""
        session = self.sessions.get(channel_id)
        if session is None:
            return None
        battles = self._battles()
        if battles is None or battles.get(channel_id) is not session.battle:
            del self.sessions[channel_id]
            return None
        return session

    async def _say(self, channel_id: int | None, channel, text: str):
        channel = channel or self.bot.get_channel(channel_id)
        if channel is None:
            return
        try:
            # Names are free text, so never let one ping anybody.
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("Could not post a practice message", exc_info=True)

    def _pick_fighter(self, user, name: str | None) -> Picked:
        owned = list_characters_by_owner(user.id)
        chosen = None
        if name:
            chosen = next((c for c in owned if c.name.lower() == name.lower()), None)
            if chosen is None:
                other = load_character(name)
                if other is not None:
                    return Picked(problem=f"{other.name} belongs to someone else. Pick one of your own characters.")
                return Picked(problem=f"No character named {name}. Pick a name from the list that shows as you type.")
        elif len(owned) == 1:
            chosen = owned[0]
        elif len(owned) > 1:
            names = ", ".join(sorted(c.name for c in owned)[:10])
            return Picked(problem=f"You have several characters ({names}). Pick one with the character option.")
        if chosen is None:
            return Picked(fighter=pr.build_trainee(user.id), lent=pr.starter_skill().name, trainee=True)
        fighter = Fighter.from_character(chosen, "A")
        lent = None
        if not fighter.skills:
            starter = pr.starter_skill()
            fighter.add_skill(starter)
            lent = starter.name
        return Picked(fighter=fighter, character=chosen, lent=lent)

    async def _can_open(self, interaction: discord.Interaction) -> bool:
        """True when this channel is free for a new practice fight (a finished one counts as free). Otherwise says why not."""
        battles = self._battles()
        if battles is None:
            await interaction.response.send_message("Battles aren't available right now.", ephemeral=True)
            return False
        old = self.session_for(interaction.channel_id)
        if old is not None and not old.finished:
            await interaction.response.send_message(
                "A practice fight is already running in this channel. `/practice stop` ends it.", ephemeral=True
            )
            return False
        if old is None and interaction.channel_id in battles:
            await interaction.response.send_message(
                "There's already a battle in this channel. Practice in another channel, or use /battle end first.",
                ephemeral=True,
            )
            return False
        return True

    async def _launch(
        self, interaction: discord.Interaction, player: Fighter, foe: Fighter, guide: str, *,
        show_plan: bool, max_moves: int | None, bot_char: Character | None = None,
    ):
        """Opens the battle, lets the opponent pick its first moves, and tells the player how it works. A finished fight in the channel is replaced."""
        channel_id = interaction.channel_id
        battle = Battle(channel_id=channel_id, battle_type="spar")
        battle.add_fighter(player)
        battle.add_fighter(foe)
        plan = pr.plan_moves(battle, foe, max_moves=max_moves)
        pr.apply_plan(foe, plan)
        self._battles()[channel_id] = battle
        self.sessions[channel_id] = Session(
            battle, interaction.user.id, foe.name, show_plan, max_moves, battle.round_number,
            bot_char=bot_char, nag_round=battle.round_number,
        )
        await interaction.response.send_message(embed=build_battle_embed(battle))
        message = await interaction.original_response()
        battle.message_id = message.id
        await interaction.followup.send(guide, ephemeral=True)
        await self._say(channel_id, interaction.channel, pr.describe_plan(plan, foe, show_plan))

    # ---- starting and stopping --------------------------------------------

    @app_commands.command(
        name="start",
        description="Fight a Dummy on your own, controlled by the bot, to learn how a round works",
    )
    @app_commands.describe(
        character="Which of your characters to use (blank: your only one, or a ready-made Trainee)",
        tier="How tough the Dummy is",
        show_plan="Show what the Dummy will do each round (on by default, turn off to guess)",
    )
    @app_commands.choices(tier=[app_commands.Choice(name=pr.TIER_NOTES[k], value=k) for k in pr.PRACTICE_TIERS])
    async def start(
        self,
        interaction: discord.Interaction,
        character: str | None = None,
        tier: str = pr.DEFAULT_TIER,
        show_plan: bool = True,
    ):
        if not await self._can_open(interaction):
            return
        picked = self._pick_fighter(interaction.user, character)
        if picked.problem:
            await interaction.response.send_message(picked.problem, ephemeral=True)
            return
        if tier not in pr.PRACTICE_TIERS:
            tier = pr.DEFAULT_TIER
        name = pr.DUMMY_NAME if picked.fighter.name.lower() != pr.DUMMY_NAME.lower() else f"Practice {pr.DUMMY_NAME}"
        dummy = pr.build_dummy(tier, name)
        guide = pr.start_text(picked.fighter, dummy, picked.lent, show_plan, picked.trainee)
        await self._launch(
            interaction, picked.fighter, dummy, guide, show_plan=show_plan, max_moves=pr.MOVES_PER_ROUND[tier],
        )

    @app_commands.command(
        name="bot",
        description="Fight a random bot built to match your level and points",
    )
    @app_commands.describe(
        character="Which of your characters to match (blank: your only one, or a Level 1 Trainee)",
        level="The bot's Level, 1 to 5 (blank: the same as yours)",
        skill_points="The bot's Skill Points (blank: the same as yours)",
        passive_points="The bot's Passive Points (blank: the same as yours)",
        skills="Random skills, or none so you add them yourself with /practice addskill",
        passives="Random passives, or none so you add them yourself with /practice addpassive",
        attacks="How many attacks the bot makes each round, 1 to 3 (blank: all its slots)",
        show_plan="Show what the bot will do each round (on by default, turn off to guess)",
    )
    @app_commands.choices(skills=SETUP_CHOICES, passives=SETUP_CHOICES)
    async def versus(
        self,
        interaction: discord.Interaction,
        character: str | None = None,
        level: app_commands.Range[int, 1, 5] | None = None,
        skill_points: app_commands.Range[int, 0, 30] | None = None,
        passive_points: app_commands.Range[int, 0, 12] | None = None,
        skills: str = "random",
        passives: str = "random",
        attacks: app_commands.Range[int, 1, 3] | None = None,
        show_plan: bool = True,
    ):
        if not await self._can_open(interaction):
            return
        picked = self._pick_fighter(interaction.user, character)
        if picked.problem:
            await interaction.response.send_message(picked.problem, ephemeral=True)
            return
        bp, sp, pp = pb.points_of(picked.character) if picked.character is not None else (STARTING_BP, 0, 0)
        if level is not None:
            bp = pb.bp_for_level(level)
        if skill_points is not None:
            sp = skill_points
        if passive_points is not None:
            pp = passive_points
        name = pb.BOT_NAME if picked.fighter.name.lower() != pb.BOT_NAME.lower() else f"Practice {pb.BOT_NAME}"
        manual = skills == "manual"
        char = pb.generate_bot(
            bp, sp, pp, name=name, owner_id=interaction.user.id, skills=not manual, passives=passives != "manual",
        )
        foe = Fighter.from_character(char, "B")
        foe.owner_id = None  # nobody declares for it, the cog does
        guide = pr.bot_start_text(
            picked.fighter, foe, pb.bot_text(char), picked.lent, show_plan, picked.trainee, manual,
        )
        await self._launch(
            interaction, picked.fighter, foe, guide, show_plan=show_plan, max_moves=attacks, bot_char=char,
        )

    @start.autocomplete("character")
    async def start_character_autocomplete(self, interaction: discord.Interaction, current: str):
        return self._own_characters(interaction, current)

    @versus.autocomplete("character")
    async def versus_character_autocomplete(self, interaction: discord.Interaction, current: str):
        return self._own_characters(interaction, current)

    def _own_characters(self, interaction: discord.Interaction, current: str):
        cur = current.lower()
        names = sorted(c.name for c in list_characters_by_owner(interaction.user.id) if cur in c.name.lower())
        return [app_commands.Choice(name=n[:100], value=n) for n in names][:25]

    @app_commands.command(name="stop", description="End your practice fight and clear this channel")
    async def stop(self, interaction: discord.Interaction):
        session = self.session_for(interaction.channel_id)
        if session is None:
            battles = self._battles()
            extra = " Use /battle end for a normal battle." if battles and interaction.channel_id in battles else ""
            await interaction.response.send_message(f"There's no practice fight in this channel.{extra}", ephemeral=True)
            return
        if not self._can_manage(interaction, session):
            await interaction.response.send_message(
                "Only whoever started this practice fight, or an admin, can stop it.", ephemeral=True
            )
            return
        del self._battles()[interaction.channel_id]
        self.sessions.pop(interaction.channel_id, None)
        await interaction.response.send_message("Practice over. The channel is clear.")

    def _can_manage(self, interaction: discord.Interaction, session: Session) -> bool:
        return interaction.user.id == session.user_id or _is_admin(interaction)

    # ---- setting up a bot by hand -----------------------------------------

    async def _bot_session(self, interaction: discord.Interaction, manage: bool = True) -> Session | None:
        """The practice bot's session, or None after telling the player why not."""
        session = self.session_for(interaction.channel_id)
        if session is None or session.bot_char is None:
            await interaction.response.send_message(
                "There's no practice bot in this channel. Start one with `/practice bot`.", ephemeral=True
            )
            return None
        if manage and not self._can_manage(interaction, session):
            await interaction.response.send_message(
                "Only whoever started this practice fight, or an admin, can change the bot.", ephemeral=True
            )
            return None
        return session

    def _sync_bot(self, session: Session):
        """Copies the bot Character's skills and passives onto its fighter in the battle. A move already picked with a skill that is gone is dropped, so the bot picks again."""
        char = session.bot_char
        live = session.battle.get_fighter(session.foe_name)
        if live is None:
            return
        live.skills = copy.deepcopy(char.skills)
        kinds = {k: cs.kind for k, cs in char.custom_statuses.items()}
        live.passive_skills = [s for s in (build_passive_skill(p, kinds) for p in char.passives) if s]
        if any(a.skill.name.lower() not in live.skills for a in live.declared_actions.values()):
            live.refund_all_supermoves()
            live.declared_actions = {}

    @app_commands.command(name="sheet", description="See the practice bot's full build")
    async def sheet(self, interaction: discord.Interaction):
        session = await self._bot_session(interaction, manage=False)
        if session is None:
            return
        await interaction.response.send_message(
            embed=build_character_embed(session.bot_char, "Practice bot"), ephemeral=True
        )

    @app_commands.command(name="addskill", description="Teach the practice bot a skill (the same form and rules as a character)")
    async def addskill(self, interaction: discord.Interaction):
        session = await self._bot_session(interaction)
        if session is None:
            return
        modal = AddSkillModal(session.bot_char, on_saved=lambda: self._sync_bot(session), practice=True)
        await interaction.response.send_modal(modal)

    @app_commands.command(name="removeskill", description="Take a skill away from the practice bot")
    @app_commands.describe(skill_name="Which skill to remove")
    async def removeskill(self, interaction: discord.Interaction, skill_name: str):
        session = await self._bot_session(interaction)
        if session is None:
            return
        if not session.bot_char.remove_skill(skill_name):
            await interaction.response.send_message(
                f"{session.bot_char.name} doesn't know a skill called {skill_name}.", ephemeral=True
            )
            return
        self._sync_bot(session)
        text = f"{session.bot_char.name} forgot {skill_name}."
        if not session.bot_char.skills:
            text += " It has no skills now, so the round can't run until you add one with `/practice addskill`."
        await interaction.response.send_message(text, ephemeral=True)
        await self._check(interaction.channel_id, interaction.channel)

    @removeskill.autocomplete("skill_name")
    async def removeskill_autocomplete(self, interaction: discord.Interaction, current: str):
        session = self.session_for(interaction.channel_id)
        if session is None or session.bot_char is None:
            return []
        cur = current.lower()
        return [
            app_commands.Choice(name=s.name[:100], value=s.name)
            for s in session.bot_char.skills.values() if cur in s.name.lower()
        ][:25]

    @app_commands.command(name="addpassive", description="Give the practice bot a passive (always-on rules), paid with its Passive Points")
    @app_commands.choices(kind=[
        app_commands.Choice(name="Passive", value="passive"),
        app_commands.Choice(name="Support Passive", value="support"),
    ])
    @app_commands.describe(
        passive_name="Name of the passive",
        kind="Passive or Support Passive (a label, they work the same)",
        cost="Passive Points it costs (1 to 6)",
        line_1="First rule, e.g. [On Use] If the target has any Burn, Deal +10% damage (timings: /help)",
        line_2="Second rule (optional)",
        line_3="Third rule (optional)",
    )
    async def addpassive(
        self, interaction: discord.Interaction, passive_name: str, kind: app_commands.Choice[str],
        cost: app_commands.Range[int, 1, 6], line_1: str, line_2: str | None = None, line_3: str | None = None,
    ):
        session = await self._bot_session(interaction)
        if session is None:
            return
        char = session.bot_char
        title = passive_name.strip()
        if not title or len(title) > 40:
            await interaction.response.send_message("Give the passive a name of 1 to 40 characters.", ephemeral=True)
            return
        if any(p["name"].lower() == title.lower() for p in char.passives):
            await interaction.response.send_message(f"{char.name} already has a passive called {title}.", ephemeral=True)
            return
        if len(char.passives) >= MAX_PASSIVES:
            await interaction.response.send_message(f"A character can have at most {MAX_PASSIVES} passives.", ephemeral=True)
            return
        lines = [l.strip() for l in (line_1, line_2, line_3) if l and l.strip()]
        kinds = {k: cs.kind for k, cs in char.custom_statuses.items()}
        parsed, error = parse_passive(lines, kinds)
        if error:
            await interaction.response.send_message(error[:1900], ephemeral=True)
            return
        problem = followup_problem(char, parsed)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        entry = {"name": title, "kind": kind.value, "lines": lines, "cost": cost, "note": "Practice bot"}
        trial = copy.deepcopy(char)
        trial.passives.append(entry)
        used = passive_points_spent(trial)
        if used > char.pp_total:
            await interaction.response.send_message(
                f"{char.name} has {char.pp_total} Passive Points and this would use {used}. "
                f"Pick a cheaper passive, remove one with `/practice removepassive`, or start again with more passive_points.",
                ephemeral=True,
            )
            return
        char.passives.append(entry)
        self._sync_bot(session)
        await interaction.response.send_message(
            f"{char.name} now has the {kind.name} **{title}** ({cost} PP). {used}/{char.pp_total} PP used.", ephemeral=True
        )

    @app_commands.command(name="removepassive", description="Take a passive away from the practice bot")
    @app_commands.describe(passive_name="Which passive to remove")
    async def removepassive(self, interaction: discord.Interaction, passive_name: str):
        session = await self._bot_session(interaction)
        if session is None:
            return
        char = session.bot_char
        keep = [p for p in char.passives if p["name"].lower() != passive_name.strip().lower()]
        if len(keep) == len(char.passives):
            await interaction.response.send_message(f"{char.name} has no passive called {passive_name}.", ephemeral=True)
            return
        char.passives = keep
        self._sync_bot(session)
        await interaction.response.send_message(
            f"Removed {passive_name} from {char.name}. {passive_points_spent(char)}/{char.pp_total} PP used.", ephemeral=True
        )

    @removepassive.autocomplete("passive_name")
    async def removepassive_autocomplete(self, interaction: discord.Interaction, current: str):
        session = self.session_for(interaction.channel_id)
        if session is None or session.bot_char is None:
            return []
        cur = current.lower()
        return [
            app_commands.Choice(name=p["name"][:100], value=p["name"])
            for p in session.bot_char.passives if cur in p["name"].lower()
        ][:25]

    # ---- between rounds ----------------------------------------------------

    async def _check(self, channel_id: int | None, channel):
        """Called after a round runs, when a move is locked in and when the bot gets a skill. Ends the fight if one side is down, and otherwise makes sure the opponent has picked its moves for the current round."""
        session = self.session_for(channel_id)
        if session is None or session.finished:
            return
        battle = session.battle
        new_round = battle.round_number != session.round
        session.round = battle.round_number
        result = pr.outcome(battle)
        if result is not None:
            session.finished = True
            await self._say(channel_id, channel, pr.end_text(result, battle.round_number - 1, session.foe_name))
            return
        foe = battle.get_fighter(session.foe_name)
        if foe is None:
            return
        # No await between the check and the declare, so two events cannot both plan one round.
        text = None
        if pr.needs_plan(foe):
            plan = pr.plan_moves(battle, foe, max_moves=session.max_moves)
            if plan:
                pr.apply_plan(foe, plan)
                text = pr.describe_plan(plan, foe, session.show_plan)
            elif session.nag_round != battle.round_number:
                session.nag_round = battle.round_number
                text = pr.describe_plan([], foe, session.show_plan)
        elif new_round and not foe.can_act():
            text = pr.describe_plan([], foe)
        if text is None:
            return
        await self._say(channel_id, channel, text)
        await sync_battle_message(self.bot, battle)

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        try:
            if command.qualified_name == "battle combat":
                await self._check(interaction.channel_id, interaction.channel)
        except Exception:
            log.exception("Practice could not follow up on a round")

    @commands.Cog.listener()
    async def on_declare_locked(self, interaction: discord.Interaction, fighter):
        try:
            await self._check(interaction.channel_id, interaction.channel)
        except Exception:
            log.exception("Practice could not follow up on a locked-in move")

    @commands.Cog.listener()
    async def on_skill_saved(self, interaction: discord.Interaction, fighter):
        try:
            session = self.session_for(interaction.channel_id)
            if session is not None and session.bot_char is fighter:
                await self._check(interaction.channel_id, interaction.channel)
        except Exception:
            log.exception("Practice could not follow up on a skill for the bot")


async def setup(bot: commands.Bot):
    await bot.add_cog(PracticeCog(bot))
