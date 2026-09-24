"""Game rules and constants (docs/representation.md §4).

Reference: the hanab.live server at commit c1d970b (`server/src/game.go`, `game_player.go`,
`command_action.go`, `constants.go`). Only the plain variants are supported: "No Variant" (5 suits)
and "6 Suits". Anything else raises `Unsupported`, so a bulk run can count and skip those games.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

SUIT_LETTERS = "RYGBPT"  # suit index order; the first five are the same in 5- and 6-suit games
COPIES = {1: 3, 2: 2, 3: 2, 4: 2, 5: 1}
RANKS = (1, 2, 3, 4, 5)
MAX_CLUES = 8
MAX_STRIKES = 3
HAND_SIZE = {2: 5, 3: 5, 4: 4, 5: 4, 6: 3}  # constants.go DefaultNumCardsPerHand
VARIANTS = {"No Variant": 5, "6 Suits": 6}  # variant name -> number of suits
DEFAULT_VARIANT = "No Variant"

# Options that change the rules in ways the engine does not implement.
UNSUPPORTED_OPTIONS = ("cardCycle", "deckPlays", "emptyClues", "detrimentalCharacters")

Identity = Tuple[int, int]  # (suit index, rank)


class End:
    """End conditions (constants.go). The game's recorded score is 0 unless the condition is NORMAL."""
    NORMAL = 1
    STRIKEOUT = 2
    TIMEOUT = 3
    TERMINATED_BY_PLAYER = 4
    SPEEDRUN_FAIL = 5
    IDLE_TIMEOUT = 6
    ALL_OR_NOTHING_FAIL = 8
    ALL_OR_NOTHING_SOFTLOCK = 9
    TERMINATED_BY_VOTE = 10

    # Set by the server from outside the rules (a timer, a player, a vote); may happen on any turn.
    EXTERNAL = frozenset({TIMEOUT, TERMINATED_BY_PLAYER, IDLE_TIMEOUT, TERMINATED_BY_VOTE})


class Unsupported(ValueError):
    """The game uses a variant or option the engine does not implement."""


class InvalidGame(ValueError):
    """The record breaks the rules or is internally inconsistent."""


def identity_str(ident: Identity) -> str:
    return f"{SUIT_LETTERS[ident[0]]}{ident[1]}"


def parse_identity(s: Optional[str], num_suits: int) -> Optional[Identity]:
    if s is None:
        return None
    if not isinstance(s, str) or len(s) != 2 or s[0] not in SUIT_LETTERS[:num_suits] or s[1] not in "12345":
        raise InvalidGame(f"bad card identity {s!r}")
    return SUIT_LETTERS.index(s[0]), int(s[1])


@dataclass(frozen=True)
class Rules:
    """The settings the engine needs. GameRecord `options` is `to_record()` of this."""
    variant: str
    players: int
    hand_size: int
    all_or_nothing: bool = False
    speedrun: bool = False
    starting_player: int = 0

    def __post_init__(self):
        if self.variant not in VARIANTS:
            raise Unsupported(f"variant {self.variant!r}")
        if self.players not in HAND_SIZE:
            raise Unsupported(f"{self.players} players")
        if not 0 <= self.starting_player < self.players:
            raise InvalidGame(f"starting player {self.starting_player} with {self.players} players")

    @property
    def suits(self) -> int:
        return VARIANTS[self.variant]

    @property
    def letters(self) -> str:
        return SUIT_LETTERS[: self.suits]

    @property
    def max_score(self) -> int:
        return 5 * self.suits

    @property
    def deck_size(self) -> int:
        return sum(COPIES.values()) * self.suits

    @classmethod
    def from_site_options(cls, options: dict, players: int) -> "Rules":
        """From an export's `options` (only non-default settings, variant under "variant") or a live
        `init` message's `options` (every setting, variant under "variantName")."""
        options = options or {}
        bad = [k for k in UNSUPPORTED_OPTIONS if options.get(k)]
        if bad:
            raise Unsupported(f"options {bad}")
        if players not in HAND_SIZE:
            raise Unsupported(f"{players} players")
        hand = HAND_SIZE[players] + bool(options.get("oneExtraCard")) - bool(options.get("oneLessCard"))
        return cls(
            variant=options.get("variant", options.get("variantName", DEFAULT_VARIANT)),
            players=players,
            hand_size=hand,
            all_or_nothing=bool(options.get("allOrNothing")),
            speedrun=bool(options.get("speedrun")),
            starting_player=options.get("startingPlayer", 0),
        )

    def to_record(self) -> dict:
        return {"variant": self.variant, "suits": self.suits, "hand_size": self.hand_size,
                "all_or_nothing": self.all_or_nothing, "speedrun": self.speedrun,
                "starting_player": self.starting_player}

    @classmethod
    def from_record(cls, options: dict, players: int) -> "Rules":
        rules = cls(variant=options["variant"], players=players, hand_size=options["hand_size"],
                    all_or_nothing=options["all_or_nothing"], speedrun=options.get("speedrun", False),
                    starting_player=options.get("starting_player", 0))
        if options.get("suits", rules.suits) != rules.suits:
            raise InvalidGame(f"options.suits {options['suits']} does not match variant {rules.variant!r}")
        return rules
