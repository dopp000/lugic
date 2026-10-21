"""The /startingoff walkthrough and the first-use tips. Pure rules and storage, no Discord code.

Each step asks the player to do one thing. After every command the cog calls
`handle_event`, which checks whether the current step is now true (by looking at
what really exists: saved characters, the channel's battle) or whether the right
command was just run. Steps are done in order. When one finishes, `compose_message`
builds the private reply that says what to do next.

Tips are separate: the first time someone runs a command that has a tip, they get
it once. A player can turn tips off.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Callable

TUTORIAL_PATH = "data/tutorial.json"

DEFAULT_CHAR = "YourCharacter"
DEFAULT_SKILL = "Quick Jab"
DEFAULT_FOE = "Dummy"


@dataclass
class Event:
    """Something a player just did. kind is "command", "skill_saved" or "start"."""
    kind: str
    user_id: int
    channel_id: int | None = None
    command: str = ""
    args: dict = field(default_factory=dict)


@dataclass
class Progress:
    started: bool = False
    finished: bool = False
    tips: bool = True
    pitched: bool = False
    char: str | None = None
    done: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    seen_tips: list = field(default_factory=list)


class World:
    """What the steps can look at. The cog gives a live one, tests give a fake."""

    def characters(self, user_id: int) -> list:
        raise NotImplementedError

    def battle(self, channel_id: int | None):
        raise NotImplementedError


def _norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def _owned_names(world: World, user_id: int) -> set[str]:
    return {_norm(c.name) for c in world.characters(user_id)}


def _owns(world: World, user_id: int, name) -> bool:
    return bool(name) and _norm(name) in _owned_names(world, user_id)


def _my_fighters(world: World, event: Event) -> list:
    battle = world.battle(event.channel_id)
    if battle is None:
        return []
    owned = _owned_names(world, event.user_id)
    return [f for f in battle.fighters if f.owner_id == event.user_id and _norm(f.name) in owned]


# ---- the steps -------------------------------------------------------------

def _has_character(ev, w, p):
    return bool(w.characters(ev.user_id))


def _viewed_sheet(ev, w, p):
    return ev.kind == "command" and ev.command == "character view" and _owns(w, ev.user_id, ev.args.get("name"))


def _has_skill(ev, w, p):
    return any(c.skills for c in w.characters(ev.user_id))


def _checked_bp(ev, w, p):
    return ev.kind == "command" and ev.command == "character bp" and _owns(w, ev.user_id, ev.args.get("name"))


def _started_battle(ev, w, p):
    return ev.kind == "command" and ev.command in ("battle create", "practice start", "practice bot") and w.battle(ev.channel_id) is not None


def _joined_battle(ev, w, p):
    return bool(_my_fighters(w, ev))


def _has_opponent(ev, w, p):
    mine = _my_fighters(w, ev)
    battle = w.battle(ev.channel_id)
    if not mine or battle is None:
        return False
    return any(f.side != mine[0].side and f.skills for f in battle.fighters)


def _all_declared(ev, w, p):
    mine = _my_fighters(w, ev)
    battle = w.battle(ev.channel_id)
    if not mine or battle is None:
        return False
    sides = {f.side for f in battle.fighters}
    return len(sides) >= 2 and mine[0].has_declared() and battle.all_declared()


def _ran_round(ev, w, p):
    battle = w.battle(ev.channel_id)
    return ev.kind == "command" and ev.command == "battle combat" and battle is not None and bool(battle.history)


def _ended_battle(ev, w, p):
    return ev.kind == "command" and ev.command in ("battle end", "practice stop") and w.battle(ev.channel_id) is None


@dataclass(frozen=True)
class Step:
    id: str
    part: str
    title: str
    instruction: str
    done_text: str
    satisfied: Callable


PART_1 = "Your character"
PART_2 = "A practice fight"

STEPS = (
    Step(
        "create", PART_1, "Make a character",
        "Run `/character create` and pick a name. You can attach an image to use as the avatar.",
        "Your character is saved.",
        _has_character,
    ),
    Step(
        "view", PART_1, "Look at your sheet",
        "Run `/character view name:{char}`. It shows your stats, resistances and Build Points. "
        "Only you see it unless you add `public:True`.",
        "That's your character sheet.",
        _viewed_sheet,
    ),
    Step(
        "skill", PART_1, "Teach it a skill",
        "Run `/character addskill name:{char}`. A form opens. Fill it in like this:\n"
        "- Skill Name: `Quick Jab`\n"
        "- Base Power, Coin Power, Coins, Damage Type: `4, 2, 2, Blunt`\n"
        "- Per-coin statuses: `none`\n"
        "Leave the rest empty, submit it, then press **Confirm** within a minute.",
        "{char} learned a skill.",
        _has_skill,
    ),
    Step(
        "bp", PART_1, "Check your Build Points",
        "Run `/character bp name:{char}`. Build Points limit how strong a character can be. "
        "A character that breaks the rules can't join a battle.",
        "Now you know where your points stand.",
        _checked_bp,
    ),
    Step(
        "battle", PART_2, "Start a battle",
        "Go to a channel set aside for practice, since each channel holds one battle, then run `/battle create`. "
        "In a hurry? `/practice start` (a Dummy) or `/practice bot` (a random bot that matches your level) "
        "sets up the whole fight for you, and skips ahead to picking moves.",
        "The battle is open.",
        _started_battle,
    ),
    Step(
        "join", PART_2, "Join it",
        "Run `/battle addfighter side:A character_name:{char}`.",
        "{char} is on Side A.",
        _joined_battle,
    ),
    Step(
        "foe", PART_2, "Add something to hit",
        "Run `/battle addfighter side:B name:{foe}`. Then give it a skill with `/battle addskill fighter:{foe}`. "
        "The same form opens, so use the same values.",
        "{foe} is ready to fight back.",
        _has_opponent,
    ),
    Step(
        "declare", PART_2, "Pick your moves",
        "Every fighter picks a move before a round runs. "
        "Run `/battle declare fighter:{char} slot:1 skill_name:{skill} target:{foe} target_slot:1`, "
        "then press **Confirm** within a minute to lock it in. {foe} needs a move too, so do the same for it, aimed at {char}. "
        "(In a `/practice` fight the bot already picked for it.)",
        "Everyone has picked a move.",
        _all_declared,
    ),
    Step(
        "combat", PART_2, "Run the round",
        "Run `/battle combat`. The bot rolls the coins and posts what happened.",
        "That was your first round.",
        _ran_round,
    ),
    Step(
        "end", PART_2, "Clean up",
        "Run `/battle end` so the channel is free again.",
        "The channel is clear.",
        _ended_battle,
    ),
)

STEP_BY_ID = {s.id: s for s in STEPS}

FINALE = (
    "That's the basics. A few more to try: `/character say` to speak as your character, "
    "`/practice start` or `/practice bot` for another solo fight, `/request stats` to ask an admin for stat changes, "
    "`/roll 1d20+4` for dice, and `/help` for the guidebook. "
    "Run `/startingoff` any time to see this again."
)

INTRO = (
    "I'll ask you to do a few things. Each time you do one, I'll tell you what to try next. "
    "Everything I say here is private to you."
)

PITCH = "New here? `/startingoff` is a quick walkthrough."


# ---- tips ------------------------------------------------------------------

# Commands that open a form answer with a modal, so a tip can't follow them.
TIPS = {
    "character create": "Stats and Build Points are set by admins. Use `/request stats` to ask for a change and `/character bp` to see your points.",
    "character view": "Add `public:True` to show the sheet to everyone in the channel.",
    "character bp": "A character that breaks the Build Point rules can't join a battle until it is fixed.",
    "character say": "This posts as your character. Only the owner or an admin can speak as them.",
    "request stats": "An admin answers it. See your requests with `/request mine`, or withdraw one with `/request cancel`.",
    "battle create": "Each channel holds one battle. Add fighters with `/battle addfighter` and finish with `/battle end`.",
    "battle addfighter": "`character_name` loads a saved character. `name` makes a one-off fighter for this battle only.",
    "battle declare": "`slot` is your own skill slot and `target_slot` is the enemy slot you aim at. Cancel one with `/battle undeclare`.",
    "battle combat": "`/battle log` shows an earlier round again. Only you see it.",
    "battle mystatus": "Only you see this.",
    "roll": "Write it like `1d20+4+2`.",
}


def _tip_applies(ev: Event, world: World) -> bool:
    """Skip the tip when the command probably did not work, so it is saved for a real first use."""
    if ev.command in ("character create", "character view", "character bp"):
        return _owns(world, ev.user_id, ev.args.get("name"))
    if ev.command == "battle create":
        return world.battle(ev.channel_id) is not None
    return True


# ---- running the steps -----------------------------------------------------

def current_step(progress: Progress) -> Step | None:
    for step in STEPS:
        if step.id not in progress.done and step.id not in progress.skipped:
            return step
    return None


def step_number(step: Step) -> int:
    return [s.id for s in STEPS].index(step.id) + 1


def _pick_character(progress: Progress, world: World, user_id: int, args: dict | None = None):
    owned = world.characters(user_id)
    if not owned:
        return None
    if args and args.get("name"):
        for c in owned:
            if _norm(c.name) == _norm(args["name"]):
                return c
    if progress.char:
        for c in owned:
            if _norm(c.name) == _norm(progress.char):
                return c
    return sorted(owned, key=lambda c: c.name.lower())[0]


def names_for(progress: Progress, world: World, user_id: int, channel_id: int | None = None) -> dict:
    """The names to fill into step text, taken from what the player really has."""
    char = _pick_character(progress, world, user_id)
    skill = DEFAULT_SKILL
    if char is not None and char.skills:
        skill = next(iter(char.skills.values())).name
    foe = DEFAULT_FOE
    battle = world.battle(channel_id)
    if battle is not None and char is not None:
        mine = [f for f in battle.fighters if _norm(f.name) == _norm(char.name)]
        if mine:
            others = [f for f in battle.fighters if f.side != mine[0].side]
            if others:
                foe = others[0].name
    return {"char": char.name if char is not None else DEFAULT_CHAR, "skill": skill, "foe": foe}


def render(text: str, names: dict) -> str:
    return text.format(**names)


@dataclass
class Update:
    completed: list = field(default_factory=list)
    finished_now: bool = False
    tip: str | None = None
    pitch: bool = False
    changed: bool = False


def advance(progress: Progress, event: Event, world: World) -> list:
    """Marks steps done, in order, for as long as the next one is true. Returns the steps just finished."""
    completed = []
    while True:
        step = current_step(progress)
        if step is None:
            break
        if not step.satisfied(event, world, progress):
            break
        progress.done.append(step.id)
        completed.append(step)
        if step.id == "create":
            picked = _pick_character(progress, world, event.user_id, event.args if event.command == "character create" else None)
            if picked is not None:
                progress.char = picked.name
    if progress.started and not progress.finished and current_step(progress) is None:
        progress.finished = True
    return completed


def handle_event(progress: Progress, event: Event, world: World) -> Update:
    update = Update()
    if event.kind == "command" and progress.tips and event.command in TIPS:
        if event.command not in progress.seen_tips and _tip_applies(event, world):
            progress.seen_tips.append(event.command)
            update.tip = TIPS[event.command]
            update.changed = True
    if event.kind == "command" and progress.tips and not progress.started and not progress.pitched:
        progress.pitched = True
        update.pitch = True
        update.changed = True
    if progress.started and not progress.finished:
        was_finished = progress.finished
        update.completed = advance(progress, event, world)
        update.finished_now = progress.finished and not was_finished
        if update.completed:
            update.changed = True
    return update


def start(progress: Progress, event: Event, world: World) -> list:
    """Begins (or resumes) the walkthrough. Steps that are already true count as done."""
    progress.started = True
    progress.pitched = True
    progress.finished = False
    return advance(progress, event, world)


def restart(progress: Progress, event: Event, world: World) -> list:
    progress.done = []
    progress.skipped = []
    progress.finished = False
    progress.char = None
    return start(progress, event, world)


def skip(progress: Progress, event: Event, world: World) -> list:
    step = current_step(progress)
    if step is None:
        return []
    progress.skipped.append(step.id)
    return advance(progress, event, world)


def step_block(progress: Progress, step: Step, world: World, user_id: int, channel_id: int | None) -> str:
    names = names_for(progress, world, user_id, channel_id)
    return f"**Step {step_number(step)} of {len(STEPS)}: {step.title}**\n{render(step.instruction, names)}"


def compose_message(update: Update, progress: Progress, world: World, event: Event) -> str | None:
    """The private reply to send after a command, or None when there is nothing to say."""
    names = names_for(progress, world, event.user_id, event.channel_id)
    lines = []
    for step in update.completed:
        lines.append(f"✅ {render(step.done_text, names)}")
    if update.completed:
        if update.finished_now:
            lines.append(FINALE)
        else:
            nxt = current_step(progress)
            if nxt is not None:
                lines.append(step_block(progress, nxt, world, event.user_id, event.channel_id))
    if update.tip:
        lines.append(f"Tip: {update.tip}")
    if update.pitch:
        lines.append(PITCH)
    return "\n".join(lines) if lines else None


# ---- saving ----------------------------------------------------------------

class TutorialStore:
    """Every player's progress in one small file, written safely so a crash can't half-save it."""

    def __init__(self, path: str = TUTORIAL_PATH):
        self.path = path
        self._players: dict[str, Progress] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                raw = json.load(f)
            for key, value in raw.items():
                self._players[key] = Progress(**{k: v for k, v in value.items() if k in Progress.__dataclass_fields__})
        except (ValueError, TypeError, AttributeError):
            # Keep the unreadable file for a look and start fresh rather than crash the bot.
            os.replace(self.path, self.path + ".bad")
            self._players = {}

    def get(self, user_id: int) -> Progress:
        return self._players.setdefault(str(user_id), Progress())

    def save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        data = {key: vars(p) for key, p in self._players.items()}
        with open(self.path + ".tmp", "w") as f:
            json.dump(data, f, indent=2)
        os.replace(self.path + ".tmp", self.path)
