"""
TEST-ONLY "supreme" bot: it knows the whole board (flop, turn and river) before it
is dealt, but not the opponent's hole cards. A benchmark for how much a perfect
read of the run-out is worth. It is NOT a legal entry (it reads the deck).

How it knows: the engine shuffles each hand's deck with
random.Random(derive_seed(match_seed, "hand", hand_num)), deals 2 cards per seat,
then the board with no burns. The match seed comes in through BOT_MATCH_SEED
(tools/arena.py sets it; for play.py run: BOT_MATCH_SEED=7 python play.py ... --seed 7).
We skip the first four deck cards (both players' hole cards) and use only
deck[4:9], the board.
"""

import hashlib
import os
import random

import eval7

RANKS = "23456789TJQKA"
SUITS = "shdc"
DECK = [r + s for r in RANKS for s in SUITS]
_SEED = os.environ.get("BOT_MATCH_SEED")


def _derive(*parts):
    d = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(d[:8], "big") >> 1


def future_board(hand_num):
    if _SEED is None:
        return None
    cards = list(DECK)
    random.Random(_derive(int(_SEED), "hand", hand_num)).shuffle(cards)
    return cards[4:9]


def final_equity(hole, board):
    """Exact equity on the final board against every possible opponent hand."""
    known = set(hole) | set(board)
    rest = [c for c in DECK if c not in known]
    b = [eval7.Card(c) for c in board]
    mine = eval7.evaluate([eval7.Card(c) for c in hole] + b)
    rc = [eval7.Card(c) for c in rest]
    win = n = 0
    for i in range(len(rc)):
        for j in range(i + 1, len(rc)):
            v = eval7.evaluate([rc[i], rc[j]] + b)
            win += 1.0 if mine > v else (0.5 if mine == v else 0.0)
            n += 1
    return win / n


def _raise(gs, total):
    me_total = gs["your_stack"] + gs["your_bet_this_street"]
    total = int(max(total, gs["min_raise_to"]))
    if total >= me_total * 0.9:
        return {"action": "all_in"}
    return {"action": "raise", "amount": total}


def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    try:
        board = future_board(gs["hand_num"])
        if not board or not set(board).isdisjoint(gs["your_cards"]):
            return {"action": "check"} if gs["can_check"] else {"action": "fold"}
        eq = final_equity(gs["your_cards"], board)
        pot, owed = gs["pot"], gs["amount_owed"]
        need = owed / (pot + owed) if owed else 0
        street = gs["street"]
        if owed == 0:
            if eq >= 0.85:
                return _raise(gs, gs["your_bet_this_street"] + max(pot, gs["blinds"]["bb"] * 3))
            if eq >= 0.65:
                return _raise(gs, gs["your_bet_this_street"] + max(int(pot * 0.66), gs["blinds"]["bb"] * 2))
            if eq < 0.3 and street != "preflop" and random.random() < 0.25:
                return _raise(gs, gs["your_bet_this_street"] + int(pot * 0.66))      # a bluff
            return {"action": "check"}
        if eq >= 0.85:
            return _raise(gs, gs["current_bet"] + pot + owed)
        if eq >= max(0.55, need + 0.15):
            return {"action": "call"}
        return {"action": "fold"}
    except Exception:
        return {"action": "check"} if gs.get("can_check") else {"action": "fold"}
