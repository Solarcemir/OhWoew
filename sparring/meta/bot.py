"""Meta bot: exploits equity-vs-random + pot-odds bots.

Such bots fold when equity < pot odds, call/raise when above, and rarely bluff.
So: steal and c-bet a lot, size bets so their pot-odds fold threshold is hit,
discount our equity when they show aggression (their bets are value-heavy), and
track their fold rate to scale bluffing.
"""
import random
import time

import eval7

_deck_cache = {}
S = {"bets_faced": 0, "folds": 0, "seen": set(), "last_hand": -1, "we_bet": False,
     "opp_bets": 0, "opp_opps": 0}
RV = "23456789TJQKA"


def _equity(hole, board, n, deadline):
    h = [eval7.Card(c) for c in hole]
    b = [eval7.Card(c) for c in board]
    used = set(hole) | set(board)
    deck = [eval7.Card(r + s) for r in RV for s in "cdhs" if (r + s) not in used]
    w = t = 0.0
    for i in range(n):
        if i % 20 == 0 and time.time() > deadline:
            break
        random.shuffle(deck)
        opp = deck[:2]
        rest = deck[2:2 + 5 - len(b)]
        full = b + rest
        a = eval7.evaluate(h + full)
        o = eval7.evaluate(opp + full)
        w += 1 if a > o else 0.5 if a == o else 0
        t += 1
    return w / t if t else 0.5


def _pre_strength(cards):
    r = sorted((RV.index(c[0]) for c in cards), reverse=True)
    suited = cards[0][1] == cards[1][1]
    hi, lo = r
    s = hi * 1.0 + lo * 0.5
    if hi == lo:
        s = 14 + hi * 2.2
    if suited:
        s += 2
    gap = hi - lo
    if hi != lo and gap <= 2:
        s += 1.5 - gap * 0.5
    return s  # roughly 5 (72o) .. 40 (AA)


def _track(gs):
    me = gs["your_bot_id"]
    hn = gs["hand_num"]
    if hn != S["last_hand"]:
        S["last_hand"] = hn
    # Learn from the match log: how often opp folds when we bet/raise.
    log = gs.get("match_action_log") or []
    # Simple pass: for each entry by opp following one of our raises on same hand/street.
    for i in range(1, len(log)):
        p, e = log[i - 1], log[i]
        if p.get("hand_num") != e.get("hand_num") or p.get("street") != e.get("street"):
            continue
        if p.get("bot_id") == me and p.get("action") in ("raise", "all_in") and e.get("bot_id") != me \
                and e.get("action") in ("fold", "call", "raise", "all_in"):
            k = (p["hand_num"], p["street"], i)
            if k in S["seen"] or e.get("street") == "preflop":
                continue
            S["seen"].add(k)
            S["bets_faced"] += 1
            if e["action"] == "fold":
                S["folds"] += 1


def _fold_rate():
    # prior 45% fold, blended with observation
    return (S["folds"] + 4.5) / (S["bets_faced"] + 10)


def _raise(gs, to):
    to = int(max(gs["min_raise_to"], to))
    if to >= gs["your_stack"] + gs["your_bet_this_street"]:
        return {"action": "all_in"}
    return {"action": "raise", "amount": to}


def decide(gs):
    if gs.get("type") == "warmup":
        _equity(["As", "Kd"], [], 50, time.time() + 1)
        return {"action": "check"}
    start = time.time()
    _track(gs)
    pot, owed = gs["pot"], gs["amount_owed"]
    stack, bb = gs["your_stack"], gs["blinds"]["bb"]
    cb, mine = gs["current_bet"], gs["your_bet_this_street"]
    street = gs["street"]
    me = gs["seat_to_act"]
    on_button = gs["button_seat"] == me
    fr = _fold_rate()
    eff = stack + mine
    for p in gs["players"]:
        if p["seat"] != me and p["state"] != "busted":
            eff = min(eff, p["stack"] + p["bet_this_street"])
    short = eff <= 12 * bb

    if street == "preflop":
        s = _pre_strength(gs["your_cards"])
        facing_raise = cb > bb
        if short:
            # push/fold-ish
            thr = 22 if owed == 0 or not facing_raise else 29
            if s >= thr or (on_button and s >= 17):
                return {"action": "all_in"}
            return {"action": "check"} if gs["can_check"] else {"action": "fold"}
        if not facing_raise:
            # open (button) / iso-raise: raise wide, they fold a lot
            if on_button or gs["can_check"] is False:
                if s >= 14 or random.random() < 0.55:
                    return _raise(gs, bb * (2.5 + random.random() * 0.5))
                return {"action": "fold"} if not gs["can_check"] else {"action": "check"}
            # BB checked option
            if s >= 22 or random.random() < 0.25:
                return _raise(gs, bb * 3)
            return {"action": "check"}
        # facing a raise: their raises are value-heavy
        if s >= 33:
            return _raise(gs, cb * 3) if cb < eff * 0.3 else {"action": "all_in"}
        if s >= 24 and owed <= eff * 0.12:
            return {"action": "call"}
        if s >= 20 and owed <= bb * 3:
            return {"action": "call"}
        return {"action": "fold"}

    # postflop
    board = gs["your_cards"], gs["community_cards"]
    deadline = start + 1.2
    eq = _equity(gs["your_cards"], gs["community_cards"], 600 if street != "river" else 900, deadline)

    if owed == 0:
        # they checked / we act first: bet for value and bluff when they fold a lot
        bluff_p = min(0.6, max(0.0, (fr - 0.30) * 1.4))
        if eq >= 0.62:
            return _raise(gs, mine + pot * (0.6 + random.random() * 0.2))
        if eq >= 0.50 and random.random() < 0.5:
            return _raise(gs, mine + pot * 0.45)
        if eq < 0.42 and random.random() < bluff_p and eff > pot * 0.5:
            return _raise(gs, mine + pot * 0.65)
        return {"action": "check"}

    # facing a bet: their bet range is stronger than random -> discount equity
    pot_before = pot - owed
    odds = owed / (pot + owed)
    bet_frac = owed / max(1, pot_before)
    adj = eq - 0.06 - min(0.10, bet_frac * 0.06)
    if street == "river":
        adj -= 0.03
    if adj >= 0.80 or (adj >= 0.70 and owed < pot * 0.5):
        return _raise(gs, cb + pot * 0.9)
    # light bluff-raise against capped-looking small bets
    if bet_frac < 0.4 and eq < 0.35 and random.random() < 0.12 and fr > 0.4 and street != "river":
        return _raise(gs, cb + pot)
    if adj > odds + 0.02:
        return {"action": "call"}
    return {"action": "fold"}
