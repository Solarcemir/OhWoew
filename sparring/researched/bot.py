"""
Heads-up NL Hold'em bot: equity-based, range-aware, with opponent modelling.

Ideas taken from the poker-AI literature (Libratus/Pluribus-style practice,
scaled down to 2 s on half a core):
  - Monte Carlo equity (eval7) against a *range*, not a random hand: when the
    opponent bets, its range is narrowed to the top slice of hands on this board.
  - Pot-odds calls with a margin; value bets sized to the pot; balanced
    semi-bluffs (draws) so we are not pure value.
  - Opponent model from match_action_log: fold-to-bet, aggression, VPIP.
    Calling stations get value only; over-folders get bluffed more.
  - Stack-depth awareness: push/fold when short (blinds rise fast), no
    spewing with marginal hands deep.
"""

import random
import time

import eval7

_CARDS = {}


def _card(s):
    c = _CARDS.get(s)
    if c is None:
        c = eval7.Card(s)
        _CARDS[s] = c
    return c


_DECK = [_card(r + s) for r in "23456789TJQKA" for s in "shdc"]
_RANKV = {r: i + 2 for i, r in enumerate("23456789TJQKA")}

# ---- opponent statistics (live for one match) ------------------------------
_STATS = {
    "hands": set(), "last_hand": -1,
    "opp_id": None,
    "vpip_n": 0, "vpip_d": 0,          # voluntarily put chips in preflop
    "fold_bet_n": 0, "fold_bet_d": 0,  # folds when facing our bet/raise (postflop)
    "fold_pre_n": 0, "fold_pre_d": 0,  # folds to our preflop raise
    "agg_n": 0, "agg_d": 0,            # postflop raises+bets / (bets+raises+calls)
    "bluff_seen": 0,
}


def _update_stats(gs):
    me = gs["your_bot_id"]
    log = gs.get("match_action_log") or []
    cur = gs.get("hand_num", 0)
    S = _STATS
    by_hand = {}
    for e in log:
        h = e.get("hand_num")
        if h is None or h >= cur or h in S["hands"]:
            continue
        by_hand.setdefault(h, []).append(e)
    for h, es in by_hand.items():
        # only count hands whose log is complete enough (starts with blinds)
        S["hands"].add(h)
        opp_vpip = False
        opp_pre_seen = False
        last_aggr_by = None
        last_aggr_street = None
        for e in es:
            a = e.get("action")
            st = e.get("street")
            who_me = e.get("bot_id") == me
            if a in ("small_blind", "big_blind", "ante", "show"):
                continue
            if not who_me:
                S["opp_id"] = e.get("bot_id")
            if st == "preflop":
                if not who_me:
                    opp_pre_seen = True
                    if a in ("call", "raise", "all_in"):
                        opp_vpip = True
                    if a == "fold" and last_aggr_by == "me":
                        S["fold_pre_n"] += 1
                        S["fold_pre_d"] += 1
                    elif a in ("call", "raise", "all_in") and last_aggr_by == "me":
                        S["fold_pre_d"] += 1
            else:
                if not who_me:
                    if last_aggr_by == "me" and last_aggr_street == st:
                        S["fold_bet_d"] += 1
                        if a == "fold":
                            S["fold_bet_n"] += 1
                    if a in ("raise", "all_in", "call"):
                        S["agg_d"] += 1
                        if a in ("raise", "all_in"):
                            S["agg_n"] += 1
                    elif a == "check":
                        S["agg_d"] += 0
            if a in ("raise", "all_in"):
                last_aggr_by = "me" if who_me else "opp"
                last_aggr_street = st
        if opp_pre_seen:
            S["vpip_d"] += 1
            if opp_vpip:
                S["vpip_n"] += 1
    S["last_hand"] = cur


def _rate(n, d, prior, weight=8.0):
    return (n + prior * weight) / (d + weight)


def _opp_fold_rate():
    S = _STATS
    return _rate(S["fold_bet_n"], S["fold_bet_d"], 0.40)


def _opp_agg():
    S = _STATS
    return _rate(S["agg_n"], S["agg_d"], 0.35)


# ---- preflop hand strength -------------------------------------------------
_PRE_CACHE = {}


def _hand_key(c1, c2):
    r1, r2 = c1[0], c2[0]
    if _RANKV[r1] < _RANKV[r2]:
        r1, r2 = r2, r1
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if c1[1] == c2[1] else "o")


def _equity_random(hole, board, n, deadline):
    """MC equity vs a uniformly random hand."""
    hero = [_card(c) for c in hole]
    bd = [_card(c) for c in board]
    known = set(hero + bd)
    rest = [c for c in _DECK if c not in known]
    need = 5 - len(bd)
    wins = 0.0
    done = 0
    for i in range(n):
        if (i & 15) == 15 and time.perf_counter() > deadline:
            break
        s = random.sample(rest, 2 + need)
        full = bd + s[2:]
        a = eval7.evaluate(hero + full)
        b = eval7.evaluate(s[:2] + full)
        if a > b:
            wins += 1
        elif a == b:
            wins += 0.5
        done += 1
    return wins / done if done else 0.5


def _pre_equity(hole):
    k = _hand_key(*hole)
    v = _PRE_CACHE.get(k)
    if v is None:
        # computed once per hand class (<=169 times), ~250 samples is enough
        v = _equity_random(hole, [], 400, time.perf_counter() + 0.25)
        _PRE_CACHE[k] = v
    return v


# ---- postflop equity vs a range --------------------------------------------
def _equity_vs_range(hole, board, keep, n, deadline):
    """
    Equity against the top `keep` fraction of opponent hands ranked by their
    strength on the current board (keep=1 -> random hand).
    """
    hero = [_card(c) for c in hole]
    bd = [_card(c) for c in board]
    known = set(hero + bd)
    rest = [c for c in _DECK if c not in known]
    need = 5 - len(bd)
    if keep >= 0.99:
        return _equity_random(hole, board, n, deadline)
    # sample opponent holdings and rank them on the current board
    m = 200
    cand = []
    for _ in range(m):
        h = random.sample(rest, 2)
        cand.append((eval7.evaluate(h + bd), h))
        if time.perf_counter() > deadline:
            break
    cand.sort(key=lambda x: -x[0])
    top = cand[:max(6, int(len(cand) * keep))]
    wins = 0.0
    done = 0
    for i in range(n):
        if (i & 15) == 15 and time.perf_counter() > deadline:
            break
        _, h = top[random.randrange(len(top))]
        rem = [c for c in rest if c is not h[0] and c is not h[1]]
        runout = random.sample(rem, need) if need else []
        full = bd + runout
        a = eval7.evaluate(hero + full)
        b = eval7.evaluate(h + full)
        if a > b:
            wins += 1
        elif a == b:
            wins += 0.5
        done += 1
    return wins / done if done else 0.5


def _draw_potential(hole, board):
    """Rough flag: flush draw or open-ended/gutshot draw on flop/turn."""
    if len(board) >= 5:
        return 0
    cards = list(hole) + list(board)
    suits = {}
    for c in cards:
        suits[c[1]] = suits.get(c[1], 0) + 1
    fd = any(v == 4 and sum(1 for c in hole if c[1] == s) >= 1 for s, v in suits.items())
    vals = sorted({_RANKV[c[0]] for c in cards} | ({1} if any(c[0] == "A" for c in cards) else set()))
    best = 0
    for lo in range(1, 11):
        win = set(range(lo, lo + 5))
        have = len(win & set(vals))
        # need at least one hole card in the window
        hv = {_RANKV[c[0]] for c in hole} | ({1} if any(c[0] == "A" for c in hole) else set())
        if have == 4 and (win & hv):
            best = 1
    return (2 if fd else 0) + best


# ---- helpers ---------------------------------------------------------------
def _raise_to(gs, target):
    target = int(max(target, gs["min_raise_to"]))
    mx = gs["your_stack"] + gs["your_bet_this_street"]
    if target >= mx:
        return {"action": "all_in"}
    return {"action": "raise", "amount": target}


def _opp_stack(gs):
    me = gs["your_bot_id"]
    for p in gs["players"]:
        if p.get("bot_id") != me:
            return p.get("stack", 0) + p.get("bet_this_street", 0)
    return gs["your_stack"]


def _count_raises_this_street(gs):
    n = 0
    for e in gs.get("action_log", []):
        if e.get("street") == gs["street"] and e.get("action") in ("raise", "all_in"):
            n += 1
    return n


def decide(gs):
    if gs.get("type") == "warmup":
        try:
            for r in "AKQJT98765432":
                _pre_equity([r + "s", r + "h"])
        except Exception:
            pass
        return {"action": "check"}
    try:
        return _decide(gs)
    except Exception:
        # safe fallback
        if gs.get("can_check"):
            return {"action": "check"}
        return {"action": "call"} if gs.get("amount_owed", 0) <= gs.get("pot", 0) * 0.2 else {"action": "fold"}


def _decide(gs):
    t0 = time.perf_counter()
    budget = min(0.9, max(0.2, gs.get("time_limit_ms", 2000) / 1000.0 * 0.4))
    deadline = t0 + budget
    try:
        if gs.get("hand_num", 0) != _STATS["last_hand"]:
            _update_stats(gs)
    except Exception:
        pass

    hole = gs["your_cards"]
    board = gs["community_cards"]
    street = gs["street"]
    pot = gs["pot"]
    owed = gs["amount_owed"]
    stack = gs["your_stack"]
    mybet = gs["your_bet_this_street"]
    cur = gs["current_bet"]
    bb = gs["blinds"]["bb"]
    can_check = gs["can_check"]
    opp_stack = _opp_stack(gs)
    eff = min(stack + mybet, opp_stack)
    eff_bb = eff / float(bb)
    nraises = _count_raises_this_street(gs)
    fold_rate = _opp_fold_rate()
    opp_agg = _opp_agg()
    in_position = (gs["seat_to_act"] == gs["button_seat"])  # button acts last postflop

    # ------------------------------------------------------------ preflop
    if street == "preflop":
        eq = _pre_equity(hole)
        k = _hand_key(*hole)
        is_pair = len(k) == 2
        facing_raise = owed > bb * 1.01 or (cur > bb)
        # Short stack: push/fold, Nash-like thresholds widen as stack shrinks.
        if eff_bb <= 12:
            thr = 0.50 + 0.012 * (eff_bb - 4) if eff_bb > 4 else 0.50
            thr = max(0.50, min(0.62, 0.62 - (12 - eff_bb) * 0.012))
            if facing_raise:
                # call-off needs a real hand; price = owed vs pot
                need = owed / float(pot + owed) if pot + owed else 0.5
                if eq >= max(need + 0.07, 0.56 if eff_bb > 6 else 0.52):
                    return {"action": "all_in"} if owed >= stack else {"action": "call"}
                return {"action": "fold"}
            if eq >= thr:
                return {"action": "all_in"}
            if can_check:
                return {"action": "check"}
            return {"action": "fold"} if eq < thr - 0.04 else {"action": "call"}

        if not facing_raise:
            # we are SB/button first to act (or BB facing a limp)
            if can_check:
                # BB option after limp: raise good hands
                if eq >= 0.60 or (eq >= 0.50 and random.random() < 0.35):
                    return _raise_to(gs, bb * 3 + (bb if random.random() < 0.3 else 0))
                return {"action": "check"}
            # open: heads-up button should open very wide
            open_thr = 0.455 + (0.02 if fold_rate < 0.3 else 0.0)
            if fold_rate > 0.55:
                open_thr -= 0.03
            if eq >= open_thr or random.random() < 0.10:
                size = 2.3 * bb if eq < 0.60 else 2.8 * bb
                return _raise_to(gs, size)
            return {"action": "call"} if owed <= bb * 0.51 and eq >= 0.44 else {"action": "fold"}

        # facing a raise
        ratio = cur / float(bb)
        need = owed / float(pot + owed) if pot + owed else 0.4
        # 3-bet / 4-bet for value and with some bluffs
        strong = eq >= (0.68 if nraises <= 1 else 0.75)
        if strong:
            if nraises >= 3 or cur * 2.2 >= eff:
                return {"action": "all_in"}
            return _raise_to(gs, cur * 3 if gs["seat_to_act"] != gs["button_seat"] else cur * 2.7)
        if nraises <= 1 and eq >= 0.52 and ratio <= 4.5 and random.random() < (0.12 + 0.25 * max(0.0, fold_rate - 0.4)):
            return _raise_to(gs, cur * 3)  # light 3-bet
        margin = 0.04 + 0.02 * min(nraises, 3)
        # tighter vs very large raises; stationary callers rarely raise light
        if eq >= need + margin and eq >= 0.46:
            if owed >= stack:
                return {"action": "all_in"} if eq >= 0.58 else {"action": "fold"}
            return {"action": "call"}
        return {"action": "fold"}

    # ------------------------------------------------------------ postflop
    # opponent range tightness given what it did this street
    keep = 1.0
    if owed > 0:
        frac = owed / float(max(1, pot - owed))  # bet as fraction of pot before it
        keep = max(0.12, 0.62 - 0.35 * min(frac, 1.5) - 0.06 * (nraises - 1))
        # an aggressive, rarely-folding bettor is less polarised than a nit
        keep += 0.10 * max(0.0, opp_agg - 0.4)
        keep = min(0.8, keep)
    n_samples = 3000
    eq = _equity_vs_range(hole, board, keep, n_samples, deadline)
    eq_rand = eq if keep >= 0.99 else None
    draws = _draw_potential(hole, board)

    spr = eff / float(max(1, pot))
    pot_after_call = pot + owed

    if owed == 0:
        # we act with option to bet
        value_thr = 0.66 if spr > 1.5 else 0.58
        if eq >= 0.85 and street != "river" and random.random() < 0.3:
            return {"action": "check"}  # slow-play a little with monsters
        if eq >= value_thr:
            frac = 0.75 if eq < 0.85 else 0.95
            if street == "river" and eq >= 0.85:
                frac = 1.0
            return _raise_to(gs, mybet + pot * frac)
        # thin value / protection
        if eq >= 0.55 and random.random() < 0.55:
            return _raise_to(gs, mybet + pot * 0.5)
        # semi-bluff with draws, pure bluff when opponent folds a lot
        bluff_p = 0.0
        if draws and street != "river":
            bluff_p = 0.40 + 0.5 * max(0.0, fold_rate - 0.35)
        elif street == "river" and eq < 0.30:
            bluff_p = 0.20 + 0.6 * max(0.0, fold_rate - 0.35)
        elif eq < 0.40:
            bluff_p = 0.12 + 0.5 * max(0.0, fold_rate - 0.35)
        if fold_rate < 0.25:
            bluff_p *= 0.2  # calling station: do not bluff
        if eff_bb < 15:
            bluff_p *= 0.5
        if random.random() < bluff_p:
            return _raise_to(gs, mybet + pot * (0.66 if street != "river" else 0.75))
        return {"action": "check"}

    # facing a bet
    need = owed / float(pot_after_call)
    if owed >= stack:  # call-off decision
        return {"action": "all_in"} if eq >= need + 0.05 else {"action": "fold"}
    # raise for value with very strong hands
    if eq >= 0.82 and nraises <= 2:
        return _raise_to(gs, cur + (pot + owed) * 0.8)
    # semi-bluff raise with strong draws
    if draws >= 2 and street == "flop" and nraises <= 1 and random.random() < 0.25 + 0.4 * max(0.0, fold_rate - 0.35):
        return _raise_to(gs, cur + (pot + owed) * 0.7)
    margin = 0.03 + (0.03 if street == "river" else 0.0)
    if eq >= need + margin:
        return {"action": "call"}
    # implied-odds call with strong draws vs small bets
    if draws >= 2 and street != "river" and need < 0.28 and eq >= need - 0.04:
        return {"action": "call"}
    # occasional bluff-raise vs a bettor who folds a lot
    if fold_rate > 0.55 and street != "river" and nraises <= 1 and random.random() < 0.10:
        return _raise_to(gs, cur + (pot + owed) * 0.8)
    return {"action": "fold"}
