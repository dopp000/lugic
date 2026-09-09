import random
import re
from dataclasses import dataclass, field

# Matches an optional sign followed by either "NdM" dice notation or a flat number.
# Examples it captures out of "1d20+4+2": ("", "1d20"), ("+", "4"), ("+", "2")
TERM_PATTERN = re.compile(r'([+-]?)(\d*d\d+|\d+)')

MAX_DICE = 100
MAX_SIDES = 1000


@dataclass
class DiceTerm:
    sign: int  # +1 or -1
    is_dice: bool
    count: int = 0
    sides: int = 0
    flat: int = 0
    rolls: list[int] = field(default_factory=list)

    def value(self) -> int:
        if self.is_dice:
            return self.sign * sum(self.rolls)
        return self.sign * self.flat

    def token(self) -> str:
        return f"{self.count}d{self.sides}" if self.is_dice else str(self.flat)

    def rolled_display(self) -> str:
        if not self.is_dice:
            return str(self.flat)
        if self.count == 1:
            return str(self.rolls[0])
        return "[" + ", ".join(str(r) for r in self.rolls) + "]"


@dataclass
class RollResult:
    expression: str
    terms: list[DiceTerm]

    @property
    def total(self) -> int:
        return sum(t.value() for t in self.terms)

    def breakdown(self) -> str:
        """Hepa-style: 'expression = substituted values = total'."""
        expr_parts = []
        val_parts = []
        for i, t in enumerate(self.terms):
            sign_char = "-" if t.sign < 0 else ("" if i == 0 else "+")
            spacer = "" if i == 0 else " "
            expr_parts.append(f"{spacer}{sign_char}{'' if i == 0 else ' '}{t.token()}".strip())
            val_parts.append(f"{spacer}{sign_char}{'' if i == 0 else ' '}{t.rolled_display()}".strip())
        expr_line = " ".join(expr_parts)
        val_line = " ".join(val_parts)
        return f"{expr_line} = {val_line} = {self.total}"


def roll(expression: str) -> RollResult:
    """Parses and rolls a dice expression like '1d20+4+2' or '2d6-1+3'.

    Raises ValueError if the expression has no valid terms, or if a dice
    term asks for too many dice or too many sides (guards against someone
    typing 999999d999999 and hanging the bot).
    """
    cleaned = expression.replace(" ", "")
    matches = TERM_PATTERN.findall(cleaned)
    if not matches:
        raise ValueError(f"No valid terms found in '{expression}'")

    terms: list[DiceTerm] = []
    for sign_str, term_str in matches:
        sign = -1 if sign_str == "-" else 1
        if "d" in term_str:
            count_str, sides_str = term_str.split("d")
            count = int(count_str) if count_str else 1
            sides = int(sides_str)
            if not (1 <= count <= MAX_DICE):
                raise ValueError(f"Dice count must be between 1 and {MAX_DICE}")
            if not (1 <= sides <= MAX_SIDES):
                raise ValueError(f"Dice sides must be between 1 and {MAX_SIDES}")
            rolls = [random.randint(1, sides) for _ in range(count)]
            terms.append(DiceTerm(sign=sign, is_dice=True, count=count, sides=sides, rolls=rolls))
        else:
            flat = int(term_str)
            terms.append(DiceTerm(sign=sign, is_dice=False, flat=flat))

    return RollResult(expression=cleaned, terms=terms)