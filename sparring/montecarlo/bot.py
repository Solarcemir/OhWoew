"""Monte Carlo equity + pot odds heads-up bot."""
import random
import time

import eval7

_DECK = [eval7.Card(r + s) for r in "23456789TJQKA" for s in "cdhs"]
_BUDGET = 0.55  # seconds of simulation per decision (limit is 2s on half a core)


def _equity(hole, board, deadline, max_iter=4000):
    h = [eval7.Card(c) for c in hole]
    b = [eval7.Card(c) for c in board]
    known = set(h + b)
    rest = [c for c in _DECK if c not in known]
    need = 5 - len(b)
    wins = 0.0
    n = 0
    while n < max_iter:
        if (n & 63) == 0 and n >= 200 and time.time() > deadline:
            break
        s = random.sample(rest, 2 + need)
        full = b + s[2:]
        mine = eval7.evaluate(h + full)
        theirs = eval7.evaluate(s[:2] + full)
        if mine > theirs:
            wins += 1
        elif mine == theirs:
            wins += 0.5
        n += 1
    return wins / max(n, 1)


def _raise(gs, target):
    stack_total = gs["your_stack"] + gs["your_bet_this_street"]
    target = max(int(target), gs["min_raise_to"])
    if target >= stack_total * 0.85:
        return {"action": "all_in"}
    return {"action": "raise", "amount": target}


def decide(gs):
    if gs.get("type") == "warmup":
        _equity(["As", "Kd"], [], time.time() + 0.2, 200)
        return {"action": "check"}

    try:
        return _decide(gs)
    except Exception:
        return {"action": "check"} if gs.get("can_check") else {"action": "fold"}


def _decide(gs):
    t0 = time.time()
    hole, board = gs["your_cards"], gs["community_cards"]
    street = gs["street"]
    pot = gs["pot"]
    owed = gs["amount_owed"]
    bb = gs["blinds"]["bb"]
    cur = gs["current_bet"]
    mybet = gs["your_bet_this_street"]
    stack = gs["your_stack"]

    eq = _equity(hole, board, t0 + _BUDGET)
    pot_before = max(pot - owed, 1)

    if owed <= 0:
        # Nothing to call: value bet strong hands, occasional semi-bluff, else check.
        if street == "preflop":
            # BB with option
            if eq > 0.62:
                return _raise(gs, cur + 3 * bb)
            return {"action": "check"}
        if eq > 0.78:
            return _raise(gs, cur + 0.75 * pot)
        if eq > 0.62:
            return _raise(gs, cur + 0.55 * pot)
        if street != "river" and 0.28 < eq < 0.5 and random.random() < 0.15:
            return _raise(gs, cur + 0.6 * pot)
        return {"action": "check"}

    # Facing a bet. Equity vs random overstates our hand against a betting range,
    # so discount in proportion to how big the bet is relative to the pot.
    frac = min(owed / pot_before, 1.5)
    discount = 0.0 if street == "preflop" and owed <= bb else 0.06 * min(frac, 1.0) + 0.02
    if street == "preflop" and owed > bb:
        discount = 0.04 + 0.03 * min(owed / (4 * bb), 2.0)
    eff = eq - discount
    pot_odds = owed / (pot + owed)
    all_in_call = owed >= stack

    if eff > 0.80 or (eff > 0.72 and not all_in_call and frac < 0.8):
        return _raise(gs, cur + max(0.75 * pot, 2 * bb))
    if eff > pot_odds + 0.02:
        return {"action": "call"}
    if street == "preflop" and owed <= bb and eq > 0.44:  # SB completing is cheap
        return {"action": "call"}
    return {"action": "fold"}
