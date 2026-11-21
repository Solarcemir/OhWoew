"""
GTO-style heads-up No-Limit Hold'em bot.  No opponent modelling at all.

  * <= 13 big blinds effective: Nash push/fold (SB shove range / BB call range
    interpolated by stack depth, widened a little for antes).
  * Deeper preflop: SB opens 2.5x with a mixed range (some limps, some bluffs),
    BB defends by pot odds (MDF), 3-bets a polarised range, 4-bet/shoves a
    value range plus ace-blocker bluffs.
  * Postflop: value bets and bluffs in a fixed ratio so the opponent is
    indifferent to calling (bluff share = s / (1 + 2s) for a bet of s * pot);
    defends against bets with the minimum-defence-frequency fraction of the
    range; raises for value with a small balanced bluff share.
  * All mixing uses `random` (seeded per match).
"""

import json
import os
import random

import eval7

# ---------------------------------------------------------------- data ------
_RANKS = "23456789TJQKA"
_PCT = {}


def _load():
    d = os.environ.get("BOT_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    try:
        with open(os.path.join(d, "preflop_pct.json")) as f:
            _PCT.update(json.load(f))
    except Exception:
        pass


_load()

# stack (bb) -> % of hands.  Approximate heads-up Nash push/fold charts.
_PUSH = [(2, 1.0), (4, 1.0), (5, .92), (6, .84), (7, .76), (8, .68), (9, .62), (10, .57),
         (12, .48), (14, .41), (16, .36), (20, .29)]
_CALL = [(2, .85), (3, .80), (4, .70), (5, .62), (6, .56), (7, .51), (8, .47), (9, .44),
         (10, .41), (12, .36), (14, .32), (16, .29), (20, .25)]
_PF_CUTOFF = 13.0


def _interp(tab, x):
    if x <= tab[0][0]:
        return tab[0][1]
    for (x0, y0), (x1, y1) in zip(tab, tab[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return tab[-1][1]


def _key(cards):
    a, b = cards[0], cards[1]
    ra, rb = _RANKS.index(a[0]), _RANKS.index(b[0])
    if ra < rb:
        a, b, ra, rb = b, a, rb, ra
    if ra == rb:
        return a[0] + b[0]
    return a[0] + b[0] + ("s" if a[1] == b[1] else "o")


def _pct(cards):
    """Percentile of the hand (0 = best, 1 = worst) among the 169 starting hands."""
    k = _key(cards)
    if k in _PCT:
        return _PCT[k]
    ra, rb = _RANKS.index(k[0]), _RANKS.index(k[1])  # fallback heuristic
    s = ra + rb + (6 if ra == rb else 0) + (2 if k.endswith("s") else 0) - (abs(ra - rb) * 0.5)
    return max(0.0, min(1.0, 1 - s / 30.0))


# ------------------------------------------------------------ helpers -------
def _is_raise(a):
    return a.get("action") in ("raise", "all_in")


def _raise_to(gs, amount):
    """Raise (or shove if it commits most of the stack)."""
    stack_total = gs["your_stack"] + gs["your_bet_this_street"]
    amount = max(int(amount), gs["min_raise_to"])
    if amount >= 0.55 * stack_total or amount >= stack_total:
        return {"action": "all_in"}
    return {"action": "raise", "amount": amount}


def _opp(gs):
    me = gs["seat_to_act"]
    for p in gs["players"]:
        if p["seat"] != me:
            return p
    return None


# ------------------------------------------------------------ preflop -------
def _preflop(gs):
    bb = gs["blinds"]["bb"]
    ante = gs["blinds"].get("ante", 0) or 0
    pct = _pct(gs["your_cards"])
    owed = gs["amount_owed"]
    pot = gs["pot"]
    opp = _opp(gs)
    my_total = gs["your_stack"] + gs["your_bet_this_street"]
    opp_total = (opp["stack"] + opp["bet_this_street"]) if opp else my_total
    eff = min(my_total, opp_total)
    eff_bb = eff / float(bb)
    n_raises = sum(1 for a in gs["action_log"] if a.get("street") == "preflop" and _is_raise(a))
    is_sb = gs["seat_to_act"] == gs["sb_seat"]
    widen = 1.0 + 0.5 * ante / float(bb)
    req = owed / float(pot + owed) if owed > 0 else 0.0   # equity needed to call

    # ---- Nash push/fold ----
    if eff_bb <= _PF_CUTOFF:
        push = min(1.0, _interp(_PUSH, eff_bb) * widen)
        call = min(1.0, _interp(_CALL, eff_bb) * widen)
        if n_raises == 0:
            if is_sb:
                return {"action": "all_in"} if pct <= push else {"action": "fold"}
            return {"action": "check"} if gs["can_check"] else (
                {"action": "call"} if pct <= call else {"action": "fold"})
        # facing a raise / shove
        if owed >= gs["your_stack"]:                       # call for everything
            return {"action": "call"} if (pct <= call or req < 0.22 and pct <= 0.6) else {"action": "fold"}
        if pct <= call * 0.55:
            return {"action": "all_in"}
        return {"action": "call"} if pct <= call else {"action": "fold"}

    # ---- deeper stacks ----
    if n_raises == 0:
        if is_sb:
            if pct <= 0.62 or (pct > 0.9 and random.random() < 0.12):      # raise + a few bluffs
                return _raise_to(gs, 2.5 * bb)
            if pct <= 0.88:                                                # limp the middle
                return {"action": "call"}
            return {"action": "fold"}
        # BB after a limp
        if pct <= 0.22 or (pct > 0.85 and random.random() < 0.10):
            return _raise_to(gs, 3.5 * bb)
        return {"action": "check"}

    d = max(0.12, min(0.78, 1.25 * (1 - req) - 0.2))
    if n_raises == 1:
        cur = gs["current_bet"]
        three_bet = pct <= 0.09 or (pct > d and pct <= d + 0.15 and random.random() < 0.18)
        if three_bet:
            return _raise_to(gs, 3.2 * cur)
        return {"action": "call"} if pct <= d else {"action": "fold"}

    # facing a 3-bet or more
    d2 = max(0.07, min(0.5, 1.1 * (1 - req) - 0.38))
    has_ace = any(c[0] == "A" for c in gs["your_cards"])
    suited = gs["your_cards"][0][1] == gs["your_cards"][1][1]
    if pct <= 0.04 or (has_ace and suited and pct > d2 and random.random() < 0.25):
        return {"action": "all_in"}
    return {"action": "call"} if pct <= d2 else {"action": "fold"}


# ----------------------------------------------------------- postflop -------
def _strength_and_equity(gs, narrow, n=260):
    """Return (strength 0..1 blending current percentile and drawing equity,
    equity vs the (optionally narrowed) opponent range)."""
    hero = [eval7.Card(c) for c in gs["your_cards"]]
    board = [eval7.Card(c) for c in gs["community_cards"]]
    used = set(hero + board)
    deck = [c for c in eval7.Deck().cards if c not in used]
    need = 5 - len(board)
    hs = eval7.evaluate(hero + board)

    pool = []
    beat = 0
    for _ in range(n * 2):
        o = random.sample(deck, 2)
        os_ = eval7.evaluate(o + board)
        pool.append((os_, o))
        if hs > os_:
            beat += 1
        elif hs == os_:
            beat += 0.5
    p_now = beat / len(pool)

    rng = pool
    if narrow:
        pool.sort(key=lambda t: -t[0])
        rng = pool[: max(20, int(len(pool) * narrow))]

    def equity(opps):
        w = 0.0
        m = 0
        for _, o in opps[:n]:
            rest = [c for c in deck if c not in o]
            run = random.sample(rest, need) if need else []
            a = eval7.evaluate(hero + board + run)
            b = eval7.evaluate(o + board + run)
            w += 1 if a > b else (0.5 if a == b else 0)
            m += 1
        return w / max(1, m)

    e_rand = equity(pool)
    e_rng = e_rand if not narrow else equity(rng)
    if len(board) == 5:
        strength = p_now
    else:
        strength = 0.5 * p_now + 0.5 * e_rand
    return strength, e_rng


def _postflop(gs):
    pot = gs["pot"]
    owed = gs["amount_owed"]
    street = gs["street"]
    cur = gs["current_bet"]
    my_bet = gs["your_bet_this_street"]
    stack_total = gs["your_stack"] + my_bet
    opp = _opp(gs)
    opp_stack = opp["stack"] if opp else 10 ** 9

    if owed <= 0:                                      # we may bet
        strength, _ = _strength_and_equity(gs, 0.0)
        spr_small = min(gs["your_stack"], opp_stack) <= 0.9 * pot
        size = 0.66
        alpha = size / (1 + 2 * size)                  # bluff share of the betting range
        p_value = 0.30
        bluff_prob = alpha / (1 - alpha) * p_value / 0.32
        if street == "river":
            bluff_prob *= 1.0
        if strength >= 0.70:
            if random.random() < 0.85:                 # keep ~15% in the checking range
                big = strength >= 0.9 and random.random() < 0.35
                return _raise_to(gs, pot * (1.0 if big else size))
            return {"action": "check"}
        if strength < 0.32 and random.random() < bluff_prob and not spr_small:
            return _raise_to(gs, pot * size)
        if 0.52 <= strength < 0.70 and random.random() < 0.25:   # thin value, small size
            return _raise_to(gs, pot * 0.33)
        return {"action": "check"}

    # facing a bet or raise
    pot_before = max(1, pot - owed)
    mdf = pot_before / float(pot_before + owed)       # minimum defence frequency
    req = owed / float(pot + owed)                    # equity needed
    narrow = 0.55 if gs["action_log"] and sum(
        1 for a in gs["action_log"] if a.get("street") == street and _is_raise(a)) <= 1 else 0.35
    strength, e_rng = _strength_and_equity(gs, narrow)
    allin = owed >= gs["your_stack"]

    if not allin and strength >= 0.88 and random.random() < 0.6:     # value raise
        return _raise_to(gs, cur + max(owed, 0.8 * pot))
    if (not allin and strength < 0.25 and street != "river" and random.random() < 0.08
            and gs["your_stack"] > 2 * owed):                         # balanced bluff-raise
        return _raise_to(gs, cur + max(owed, 0.8 * pot))

    cont = strength >= 1 - mdf * 0.95
    if street == "river" or allin:
        cont = cont and e_rng >= req * 0.8
    elif e_rng < req * 0.6:
        cont = False
    if e_rng >= req * 1.25 + 0.04:                    # clearly profitable
        cont = True
    return {"action": "call"} if cont else {"action": "fold"}


# -------------------------------------------------------------- entry -------
def decide(game_state: dict) -> dict:
    if game_state.get("type") == "warmup":
        return {"action": "check"}
    try:
        if game_state["street"] == "preflop":
            act = _preflop(game_state)
        else:
            act = _postflop(game_state)
    except Exception:
        act = {"action": "check"} if game_state.get("can_check") else {"action": "fold"}
    if act["action"] == "check" and not game_state.get("can_check"):
        act = {"action": "fold"}
    return act
