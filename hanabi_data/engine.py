"""The deterministic rules engine: replays a GameRecord's events (docs/representation.md §6) and
rejects anything the rules of §4 don't allow.

It works for both kinds of record: full information (every identity known) and one seated player's
view (that player's cards in hand are unknown). With unknown cards it checks everything it can see.

    engine = Engine(rules, view)
    for event in record["events"]:
        engine.apply(event)          # raises InvalidGame

`positions(record)` walks a record and stops before every action, which is where decision records
are taken (decision.py).
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterator, List, Optional, Set, Tuple

from .rules import (COPIES, MAX_CLUES, MAX_STRIKES, RANKS, End, Identity, InvalidGame, Rules,
                    parse_identity)

ACTIONS = ("play", "discard", "clue")


class Engine:
    def __init__(self, rules: Rules, view: Optional[int] = None):
        n = rules.players
        if view is not None and not 0 <= view < n:
            raise InvalidGame(f"view {view} with {n} players")
        self.rules, self.view = rules, view
        self.hands: List[List[int]] = [[] for _ in range(n)]  # slot order: index 0 is slot 1 (newest)
        self.ids: Dict[int, Optional[Identity]] = {}  # every drawn card; None = unknown in this view
        self.next_card = 0
        self.stacks = [0] * rules.suits
        self.discards: List[int] = []  # in order, including misplays
        self.clues, self.strikes = MAX_CLUES, 0
        self.turn = 0  # actions taken so far; the UI's "Turn N" is turn + 1
        self.active = rules.starting_player
        self.max_score = rules.max_score  # the highest score still reachable
        self.dealt = False

        # Per card: turn drawn (0 = deal), turns of clues that touched it, clue knowledge.
        self.drawn_t: Dict[int, int] = {}
        self.touched_t: Dict[int, List[int]] = {}
        self.pos_suit: Dict[int, Optional[int]] = {}
        self.pos_rank: Dict[int, Optional[int]] = {}
        self.neg_suits: Dict[int, Set[int]] = {}
        self.neg_ranks: Dict[int, Set[int]] = {}

        # Public history with absolute seats: the deal, then one entry per action. Clue entries also
        # carry `missed` (the untouched cards), which the review tool uses.
        self.history: List[dict] = []
        # One summary per action, for filtering metadata: misplay, max_drop (the reachable max
        # score fell), knowable (a misplay the actor could know was unplayable), ended (the rules
        # ended the game right after it).
        self.actions: List[dict] = []

        self.end: Optional[dict] = None  # {"condition", "seat"} once an `end` event is applied
        self.rule_end: Optional[Tuple[int, Optional[int]]] = None  # the rules have ended the game
        self.end_possible = False  # partial view only: the rules may have ended it; can't tell
        self.pending_draw: Optional[int] = None  # seat that must draw next
        self._end_turn: Optional[int] = None  # without All or Nothing: turn on which the game ends
        self._cannot_play: Set[int] = set()  # without All or Nothing: hands after their last turn
        self._revealed: Counter = Counter()  # identity -> copies seen so far

    # -------------------------------------------------------------------------------------------
    # Derived state

    @property
    def score(self) -> int:
        return sum(self.stacks)

    @property
    def deck_left(self) -> int:
        return self.rules.deck_size - self.next_card

    @property
    def over(self) -> bool:
        return self.end is not None or self.rule_end is not None

    def public_count(self) -> Counter:
        """Copies of each identity on the stacks or in the discard pile."""
        count = Counter()
        for s, height in enumerate(self.stacks):
            for r in range(1, height + 1):
                count[(s, r)] += 1
        for cid in self.discards:
            count[self.ids[cid]] += 1
        return count

    def gone(self) -> List[Identity]:
        """Identities with every copy on the stacks or in the discard pile, in suit/rank order."""
        return sorted(k for k, v in self.public_count().items() if v >= COPIES[k[1]])

    def know(self, cid: int) -> Tuple[List[int], List[int]]:
        """What a card's holder knows from clues alone: allowed suit indices, allowed ranks."""
        suits = [s for s in range(self.rules.suits)
                 if self.pos_suit[cid] in (None, s) and s not in self.neg_suits[cid]]
        ranks = [r for r in RANKS if self.pos_rank[cid] in (None, r) and r not in self.neg_ranks[cid]]
        return suits, ranks

    def playable(self, ident: Identity) -> bool:
        return self.stacks[ident[0]] == ident[1] - 1

    def reachable_max(self) -> int:
        """hanab.live GetMaxScore: each suit counts up to below its first rank with every copy discarded."""
        discarded = Counter(self.ids[c] for c in self.discards)
        total = 0
        for s in range(self.rules.suits):
            for r in RANKS:
                if discarded[(s, r)] >= COPIES[r]:
                    break
                total += 1
        return total

    # -------------------------------------------------------------------------------------------
    # Events

    def apply(self, ev: dict) -> None:
        kind = ev.get("e")
        if self.end is not None:
            raise InvalidGame(f"{kind} event after the game ended")
        if kind == "draw":
            self._draw(ev)
        elif kind in ACTIONS:
            self._start_action(ev)
            getattr(self, "_" + kind)(ev)
        elif kind == "end":
            self._end(ev)
        else:
            raise InvalidGame(f"unknown event {ev!r}")

    def _start_action(self, ev: dict) -> None:
        if not self.dealt:
            raise InvalidGame(f"{ev['e']} before the deal is complete")
        if self.pending_draw is not None:
            raise InvalidGame(f"{ev['e']} while seat {self.pending_draw} still has to draw")
        if self.rule_end is not None:
            raise InvalidGame(f"{ev['e']} on turn {self.turn + 1} after the game ended (condition {self.rule_end[0]})")
        if ev.get("by") != self.active:
            raise InvalidGame(f"turn {self.turn + 1}: seat {ev.get('by')} acted, but it is seat {self.active}'s turn")

    def _reveal(self, cid: int, ident: Optional[Identity]) -> None:
        """Record a card identity this view has just learned (None: still unknown)."""
        known = self.ids.get(cid)
        if ident is None or known == ident:
            self.ids.setdefault(cid, ident)
            return
        if known is not None:
            raise InvalidGame(f"card {cid} was {known}, now {ident}")
        self.ids[cid] = ident
        self._revealed[ident] += 1
        if self._revealed[ident] > COPIES[ident[1]]:
            raise InvalidGame(f"more than {COPIES[ident[1]]} copies of {ident}")

    def _take_from_hand(self, ev: dict) -> Tuple[int, Identity, int]:
        by, cid = ev["by"], ev.get("card")
        if cid not in self.hands[by]:
            raise InvalidGame(f"turn {self.turn + 1}: card {cid} is not in seat {by}'s hand")
        if ev.get("id") is None:
            raise InvalidGame(f"turn {self.turn + 1}: {ev['e']} of card {cid} without its identity")
        ident = parse_identity(ev["id"], self.rules.suits)
        self._reveal(cid, ident)
        slot = self.hands[by].index(cid) + 1
        return cid, ident, slot

    def _play(self, ev: dict) -> None:
        cid, ident, slot = self._take_from_hand(ev)
        ok = self.playable(ident)
        if "ok" in ev and ev["ok"] != ok:
            raise InvalidGame(f"turn {self.turn + 1}: play of {ev['id']} recorded ok={ev['ok']}, rules say {ok}")
        knowable = False if ok else self._knowably_unplayable(cid)
        self.hands[ev["by"]].remove(cid)
        if ok:
            self.stacks[ident[0]] += 1
            if ident[1] == 5:
                self.clues = min(self.clues + 1, MAX_CLUES)
        else:
            self.strikes += 1
            self.discards.append(cid)
        self._card_left(ev, {"e": "play", "card": cid, "slot": slot, "ok": ok},
                        misplay=not ok, knowable=knowable)

    def _discard(self, ev: dict) -> None:
        if self.clues >= MAX_CLUES:
            raise InvalidGame(f"turn {self.turn + 1}: discard at {MAX_CLUES} clues")
        cid, _, slot = self._take_from_hand(ev)
        self.hands[ev["by"]].remove(cid)
        self.clues += 1
        self.discards.append(cid)
        self._card_left(ev, {"e": "discard", "card": cid, "slot": slot}, misplay=False, knowable=False)

    def _card_left(self, ev: dict, entry: dict, misplay: bool, knowable: bool) -> None:
        old_max = self.max_score
        self.max_score = min(self.max_score, self.reachable_max())
        self.history.append({"t": self.turn + 1, "by": ev["by"], **entry, "drew": None})
        self.actions.append({"t": self.turn + 1, "by": ev["by"], "e": ev["e"], "misplay": misplay,
                             "max_drop": self.max_score < old_max, "knowable": knowable, "ended": False})
        if self.next_card < self.rules.deck_size:
            self.pending_draw = ev["by"]
        else:
            self._finish_action(ev["by"])

    def _clue(self, ev: dict) -> None:
        by, to, kind, value = ev["by"], ev.get("to"), ev.get("kind"), ev.get("value")
        t = self.turn + 1
        if not isinstance(to, int) or not 0 <= to < self.rules.players or to == by:
            raise InvalidGame(f"turn {t}: clue from seat {by} to seat {to}")
        if self.clues < 1:
            raise InvalidGame(f"turn {t}: clue with no clue tokens")
        if kind == "color" and isinstance(value, str) and len(value) == 1 and value in self.rules.letters:
            matches = lambda ident: ident[0] == self.rules.letters.index(value)
        elif kind == "rank" and value in RANKS:
            matches = lambda ident: ident[1] == value
        else:
            raise InvalidGame(f"turn {t}: bad clue {kind} {value!r}")
        hand, touched = self.hands[to], ev.get("touched")
        if not touched or touched != sorted(set(touched)) or not set(touched) <= set(hand):
            raise InvalidGame(f"turn {t}: touched {touched} must be a non-empty, ascending subset of "
                              f"seat {to}'s hand {sorted(hand)}")
        hit = set(touched)
        for cid in hand:
            if self.ids[cid] is not None and matches(self.ids[cid]) != (cid in hit):
                raise InvalidGame(f"turn {t}: the clue {'touches' if cid not in hit else 'misses'} card {cid} "
                                  f"according to the rules, but the record says otherwise")
        self.clues -= 1
        v = self.rules.letters.index(value) if kind == "color" else value
        for cid in hand:
            if cid in hit:
                self.touched_t[cid].append(t)
                (self.pos_suit if kind == "color" else self.pos_rank)[cid] = v
            else:
                (self.neg_suits if kind == "color" else self.neg_ranks)[cid].add(v)
        self.history.append({"t": t, "by": by, "e": "clue", "to": to, "kind": kind, "value": value,
                             "touched": [c for c in hand if c in hit], "missed": [c for c in hand if c not in hit]})
        self.actions.append({"t": t, "by": by, "e": "clue", "misplay": False, "max_drop": False,
                             "knowable": False, "ended": False})
        self._finish_action(by)

    def _draw(self, ev: dict) -> None:
        seat, cid = ev.get("seat"), ev.get("card")
        n, size = self.rules.players, self.rules.hand_size
        if self.dealt:
            if self.pending_draw is None or seat != self.pending_draw:
                raise InvalidGame(f"turn {self.turn + 1}: unexpected draw by seat {seat}")
        elif seat != self.next_card // size:
            raise InvalidGame(f"deal: card {cid} went to seat {seat}, expected seat {self.next_card // size}")
        if cid != self.next_card:
            raise InvalidGame(f"draw of card {cid}; the next card in the deck is {self.next_card}")
        ident = parse_identity(ev.get("id"), self.rules.suits)
        hidden = self.view is not None and seat == self.view
        if hidden != (ident is None):
            raise InvalidGame(f"draw of card {cid} by seat {seat}: identity must be "
                              f"{'hidden' if hidden else 'known'} in view {self.view}")
        self._reveal(cid, ident)
        self.next_card += 1
        self.hands[seat].insert(0, cid)
        self.drawn_t[cid] = self.turn + 1 if self.dealt else 0
        self.touched_t[cid] = []
        self.pos_suit[cid] = self.pos_rank[cid] = None
        self.neg_suits[cid], self.neg_ranks[cid] = set(), set()

        if not self.dealt:
            if self.next_card == n * size:
                self.dealt = True
                self.history.append({"t": 0, "e": "deal", "hands": {s: list(self.hands[s]) for s in range(n)}})
            return
        self.history[-1]["drew"] = cid
        self.pending_draw = None
        if self.next_card == self.rules.deck_size and not self.rules.all_or_nothing:
            self._end_turn = self.turn + n + 1  # game_player.go DrawCard: everyone gets one more turn
        self._finish_action(seat)

    def _finish_action(self, by: int) -> None:
        """command_action.go after an action: mark a finished player's hand, advance the turn, CheckEnd."""
        n = self.rules.players
        if self._end_turn is not None and self._end_turn != self.turn + n + 1:
            self._cannot_play.update(self.hands[by])
        self.turn += 1
        self.active = (self.active + 1) % n
        self.rule_end, self.end_possible = self._check_end()
        if self.rule_end is not None:
            self.actions[-1]["ended"] = True

    def _check_end(self) -> Tuple[Optional[Tuple[int, Optional[int]]], bool]:
        """game.go CheckEnd, in the same order. Returns ((condition, seat) or None, end_possible)."""
        r = self.rules
        if self.strikes >= MAX_STRIKES:
            return (End.STRIKEOUT, None), False
        if r.speedrun and self.max_score < r.max_score:
            return (End.SPEEDRUN_FAIL, None), False
        if r.all_or_nothing and self.max_score < r.max_score:
            return (End.ALL_OR_NOTHING_FAIL, None), False
        if r.all_or_nothing and not self.hands[self.active] and self.clues < 1:
            return (End.ALL_OR_NOTHING_SOFTLOCK, self.active), False
        if self._end_turn is not None and self.turn == self._end_turn:
            return (End.NORMAL, None), False
        if self.score == self.max_score:
            return (End.NORMAL, None), False
        dead = self._all_dead()
        if dead is None:
            return None, True
        return ((End.NORMAL, None), False) if dead else (None, False)

    def _all_dead(self) -> Optional[bool]:
        """True if no remaining card can ever be played. None if this view can't tell (unknown cards
        whose players have had their last turn)."""
        discarded = Counter(self.ids[c] for c in self.discards)
        blocked = Counter(self.ids[c] for c in self._cannot_play if self.ids[c] is not None)
        unknown_blocked = sum(1 for c in self._cannot_play if self.ids[c] is None)
        unsure = False
        for s, height in enumerate(self.stacks):
            if height == 5:
                continue
            need = (s, height + 1)
            left = COPIES[need[1]] - discarded[need] - blocked[need]
            if left > unknown_blocked:
                return False
            if left > 0:
                unsure = True
        return None if unsure else True

    def _end(self, ev: dict) -> None:
        cond, seat = ev.get("condition"), ev.get("seat")
        if self.pending_draw is not None:
            raise InvalidGame(f"end while seat {self.pending_draw} still has to draw")
        if self.rule_end is not None:
            if (cond, seat) != self.rule_end:
                raise InvalidGame(f"end {cond} (seat {seat}); the rules say {self.rule_end}")
        elif not (cond in End.EXTERNAL or (self.end_possible and cond == End.NORMAL)):
            raise InvalidGame(f"end condition {cond} on turn {self.turn + 1} is not explained by the rules")
        self.end = {"condition": cond, "seat": seat}

    def _knowably_unplayable(self, cid: int) -> bool:
        """No identity allowed by the card's clue knowledge and not publicly gone is playable now."""
        suits, ranks = self.know(cid)
        gone = set(self.gone())
        return not any(self.playable((s, r)) for s in suits for r in ranks if (s, r) not in gone)


def engine_for(record: dict) -> Engine:
    rules = Rules.from_record(record["options"], len(record["players"]))
    return Engine(rules, record.get("view"))


def replay(record: dict) -> Engine:
    """Apply every event; returns the engine at the end of the record."""
    engine = engine_for(record)
    for ev in record["events"]:
        engine.apply(ev)
    return engine


def positions(record: dict) -> Iterator[Tuple[Engine, Optional[dict]]]:
    """Yield (engine, next action event) before every action, then (engine, None) at the end.

    The engine is the same object each time, advanced in place: use it before asking for the next
    position.
    """
    engine = engine_for(record)
    for ev in record["events"]:
        if ev.get("e") in ACTIONS:
            yield engine, ev
        engine.apply(ev)
    yield engine, None
