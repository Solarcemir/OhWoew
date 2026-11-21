"""
our_bot: an adaptive heads-up NLHE bot with two personalities.

  SOLID  - every choice is an expected-value comparison that uses what we have
           learned about this opponent: how often it folds to each bet size, how
           wide it plays preflop, how often its bets are bluffs (from showdowns).
           Short stacks use a heads-up Nash push/fold table we solved offline.
  BAIT / TRAP - in the cheap early levels we bet with an obvious size tell
           (small = weak, big = strong). If the opponent starts attacking our
           small bets (it "learned" the tell), we flip it: small bets become our
           strong hands, big bets become bluffs. If it never adapts, we drop the
           act and play SOLID.

Data (loaded once at import, from data/ next to this file):
  eqmat.npy     169x169 preflop all-in equity between starting-hand classes
  classes.json  names of the 169 classes
  pushfold.json heads-up Nash push/fold strategies by stack depth (built by tools/build_tables.py)
"""

import json
import os
import random
import time

import eval7
import numpy as np

# ── data ──────────────────────────────────────────────────────────────────────
_DIR = os.environ.get("BOT_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
if not os.path.isdir(_DIR):
    _DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
E = np.load(os.path.join(_DIR, "eqmat.npy")).astype(np.float64)
with open(os.path.join(_DIR, "classes.json")) as f:
    CLASSES = json.load(f)
with open(os.path.join(_DIR, "pushfold.json")) as f:
    PF = json.load(f)
CIDX = {c: i for i, c in enumerate(CLASSES)}
W = np.array([6.0 if len(c) == 2 else (4.0 if c[2] == "s" else 12.0) for c in CLASSES])
VS_RAND = (E * W[None, :]).sum(axis=1) / W.sum()
_ORDER = np.argsort(-VS_RAND)
PCT = np.zeros(169)                       # share of combos at least as strong as this class
_cum = 0.0
for _i in _ORDER:
    _cum += W[_i]
    PCT[_i] = _cum / W.sum()
PF_STACKS = PF["stacks"]
PF_PUSH = {k: np.array(PF[k]["push"]) for k in ("ante0", "ante10")}
PF_CALL = {k: np.array(PF[k]["call"]) for k in ("ante0", "ante10")}
PF_PUSH_FRAC = {k: (PF_PUSH[k] > 0.5).astype(float) @ W / W.sum() for k in PF_PUSH}
PF_CALL_FRAC = {k: (PF_CALL[k] > 0.5).astype(float) @ W / W.sum() for k in PF_CALL}

RANKS = "AKQJT98765432"
SUITS = "shdc"
DECK_STR = [r + s for r in RANKS for s in SUITS]
DECK = [eval7.Card(c) for c in DECK_STR]
CARD_I = {c: i for i, c in enumerate(DECK_STR)}


def _cls_of(c1, c2):
    r1, r2 = c1[0], c2[0]
    if RANKS.index(r1) > RANKS.index(r2):
        r1, r2 = r2, r1
    if r1 == r2:
        return CIDX[r1 + r2]
    return CIDX[r1 + r2 + ("s" if c1[1] == c2[1] else "o")]


CLS52 = np.zeros((52, 52), dtype=np.int16)
for _a in range(52):
    for _b in range(52):
        if _a != _b:
            CLS52[_a, _b] = _cls_of(DECK_STR[_a], DECK_STR[_b])

RAISE_BLUFF_SCALE = 0.5   # postflop raises are bluffs half as often as bets, until showdowns say otherwise
START_MODE = "solid"     # "bait" starts with the size tell; arena tests showed it costs chips vs AI-written bots
POSTS = ("small_blind", "big_blind", "ante")
AGGR = ("raise", "all_in")
BUCKETS = ("S", "M", "L", "O")
FOLD_PRIOR = {"S": 0.28, "M": 0.40, "L": 0.48, "O": 0.55}
SIZES = {"S": 0.33, "M": 0.66, "L": 1.0, "O": 1.5}


def bucket(ratio):
    if ratio < 0.45:
        return "S"
    if ratio < 0.85:
        return "M"
    if ratio < 1.25:
        return "L"
    return "O"


def range_eq(h, frac):
    """Preflop all-in equity of class h against the top `frac` of hands."""
    frac = min(max(frac, 0.02), 1.0)
    w = W * (PCT <= frac + 1e-9)
    if w.sum() == 0:
        w = W * (PCT <= PCT[_ORDER[0]] + 1e-9)
    return float((E[h] * w).sum() / w.sum())


def blend(count, n, prior, k):
    return (count + prior * k) / (n + k)


# ── opponent model (lives for one match: the process restarts every match) ──
class Model:
    def __init__(self):
        self.done = set()
        self.boards = {}
        self.me = None
        self.opp = None
        # preflop
        self.sb_n = self.sb_fold = self.sb_limp = self.sb_raise = self.sb_shove = 0
        self.vs_open_n = self.vs_open_fold = self.vs_open_call = self.vs_open_raise = 0
        self.vs_shove_n = self.vs_shove_call = 0
        self.vs_3b_n = self.vs_3b_fold = 0
        # postflop, response to our bets by size bucket
        # key (barrel, bucket): barrel=True when the opponent already called one of our
        # postflop bets this hand (it folds much less after that)
        self.resp = {(k, b): [0, 0, 0] for k in (False, True) for b in BUCKETS}   # fold, call, raise
        self.resp_log = []                               # (hand, bucket, response)
        self.bet_opps = self.bets = 0                    # opp betting when checked to
        self.sd_bets = self.sd_bluffs = 0                # opp postflop bets seen at showdown
        self.sd_calls = self.sd_weak_calls = 0
        self.size_sd = []                                # (bet|raise, size/pot, weak at showdown)
        self.hands = 0
        # mode control
        self.mode = START_MODE
        self.mode_switch_hand = 0
        self.stack_at = {}

    # rates
    def fold_rate(self, b, barrel=False):
        f, c, r = self.resp[(barrel, b)]
        prior = FOLD_PRIOR[b] * (0.6 if barrel else 1.0)
        if barrel:                       # shrink toward what we know from first bets, scaled down
            f0, c0, r0 = self.resp[(False, b)]
            if f0 + c0 + r0 >= 4:
                prior = min(prior, blend(f0, f0 + c0 + r0, FOLD_PRIOR[b], 4) * 0.6)
        return blend(f, f + c + r, prior, 4)

    def open_frac(self):              # how wide the opponent opens as SB
        return blend(self.sb_raise + self.sb_limp, self.sb_n, 0.75, 6)

    def raise_frac(self):
        return blend(self.sb_raise, self.sb_n, 0.6, 6)

    def bluff_frac(self):
        return blend(self.sd_bluffs, self.sd_bets, 0.25, 4)

    def size_bluff(self, kind, ratio):
        """How often bets of this exact size (+-0.06 pot) were weak at showdown. Many bots
        use fixed sizes (e.g. 0.6 pot for bluffs and medium hands, 0.75 pot only with
        monsters), so the size is a tell. Raises default to half the bluff rate of bets."""
        base = self.bluff_frac() * (1.0 if kind == "bet" else RAISE_BLUFF_SCALE)
        recs = [w for k, r, w in self.size_sd if k == kind and abs(r - ratio) <= 0.06]
        return blend(sum(recs), len(recs), base, 3)

    def fold_to_open(self):
        return blend(self.vs_open_fold, self.vs_open_n, 0.35, 6)

    def threebet_rate(self):
        return blend(self.vs_open_raise, self.vs_open_n, 0.10, 6)

    def call_vs_shove(self, prior):
        return blend(self.vs_shove_call, self.vs_shove_n, prior, 5)

    def update(self, gs):
        self.me = gs.get("your_bot_id")
        for p in gs.get("players") or []:
            if p.get("bot_id") != self.me:
                self.opp = p.get("bot_id")
        hn = gs.get("hand_num", 0)
        if hn not in self.stack_at:
            for p in gs.get("players") or []:
                if p.get("bot_id") == self.me:
                    self.stack_at[hn] = p.get("stack", 0) + p.get("bet_this_street", 0)
        board = gs.get("community_cards") or []
        if len(board) >= len(self.boards.get(hn, [])):
            self.boards[hn] = list(board)
        by_hand = {}
        for e in gs.get("match_action_log") or []:
            h = e.get("hand_num")
            if h is None or h >= hn or h in self.done:
                continue
            by_hand.setdefault(h, []).append(e)
        for h in sorted(by_hand):
            self.done.add(h)
            try:
                self._process(h, by_hand[h])
            except Exception:
                pass

    def _process(self, h, entries):
        me, opp = self.me, self.opp
        if opp is None:
            return
        self.hands += 1
        street = None
        contrib = {}
        pot = 0
        sb = None
        pre_vol = []
        opp_post_aggr = False
        opp_called_our_bet = False
        last_our_bet_bucket = None
        shows = {}
        opp_sizes = []
        for e in entries:
            a, who, amt, st = e.get("action"), e.get("bot_id"), e.get("amount") or 0, e.get("street")
            if a == "show":
                shows[who] = e.get("cards")
                continue
            if st != street:
                street = st
                contrib = {}
            if a == "ante":
                pot += amt
                continue
            if a == "small_blind":
                sb = who
            if a in ("small_blind", "big_blind"):
                contrib[who] = contrib.get(who, 0) + amt
                pot += amt
                continue
            mine, theirs = contrib.get(me, 0), contrib.get(opp, 0)
            owed_opp = max(0, mine - theirs)
            if who == opp:
                if st == "preflop":
                    pre_vol.append((who, a))
                    first_opp = sum(1 for x in pre_vol if x[0] == opp) == 1
                    my_prev = [x for x in pre_vol[:-1] if x[0] == me]
                    if sb == opp and first_opp and not my_prev:
                        self.sb_n += 1
                        if a == "fold":
                            self.sb_fold += 1
                        elif a == "call":
                            self.sb_limp += 1
                        elif a in AGGR:
                            self.sb_raise += 1
                            if a == "all_in":
                                self.sb_shove += 1
                    if my_prev and my_prev[-1][1] == "all_in":
                        self.vs_shove_n += 1
                        if a in ("call", "all_in"):
                            self.vs_shove_call += 1
                    elif sb == me and first_opp and len(my_prev) == 1 and my_prev[0][1] == "raise":
                        self.vs_open_n += 1
                        if a == "fold":
                            self.vs_open_fold += 1
                        elif a == "call":
                            self.vs_open_call += 1
                        elif a in AGGR:
                            self.vs_open_raise += 1
                    elif len(my_prev) >= 2 and my_prev[-1][1] == "raise":
                        self.vs_3b_n += 1
                        if a == "fold":
                            self.vs_3b_fold += 1
                else:
                    if owed_opp > 0 and last_our_bet_bucket is not None:
                        r = {"fold": 0, "call": 1}.get(a, 2 if a in AGGR else 1)
                        self.resp[(opp_called_our_bet, last_our_bet_bucket)][r] += 1
                        self.resp_log.append((h, last_our_bet_bucket, r))
                        if r == 1:
                            opp_called_our_bet = True
                        last_our_bet_bucket = None
                    elif owed_opp == 0:
                        self.bet_opps += 1
                        if a in AGGR:
                            self.bets += 1
                    if a in AGGR:
                        opp_post_aggr = True
                        # remember the exact size, to learn later what this size means (size tells)
                        opp_sizes.append((st, "raise" if owed_opp > 0 else "bet",
                                          max(0, amt - theirs) / max(1, pot)))
            elif who == me:
                if st == "preflop":
                    pre_vol.append((who, a))
                elif a in AGGR:
                    added = max(0, amt - mine)
                    before = pot
                    owed = max(0, amt - theirs)
                    last_our_bet_bucket = "O" if a == "all_in" and owed > 1.25 * max(1, before) \
                        else bucket(owed / max(1, before))
            # apply chips
            if a == "call":
                contrib[who] = contrib.get(who, 0) + amt
                pot += amt
            elif a in AGGR:
                prev = contrib.get(who, 0)
                contrib[who] = max(prev, amt)
                pot += max(0, amt - prev)
        if opp in shows and shows[opp]:
            board = self.boards.get(h, [])
            nb = {"flop": 3, "turn": 4, "river": 5}
            for st_, kind, ratio in opp_sizes:
                if len(board) >= nb.get(st_, 9):
                    weak = made_level(shows[opp], board[:nb[st_]]) < 1
                    self.size_sd.append((kind, ratio, weak))
            if len(board) >= 3:
                lvl = made_level(shows[opp], board)
                if opp_post_aggr:
                    self.sd_bets += 1
                    if lvl < 1:
                        self.sd_bluffs += 1
                if opp_called_our_bet:
                    self.sd_calls += 1
                    if lvl < 1:
                        self.sd_weak_calls += 1


def made_level(hole, board):
    """0 nothing, 0.5 draw, 1 pair using a hole card, 2 two pair or better."""
    cards = [eval7.Card(c) for c in list(hole) + list(board)]
    t = eval7.evaluate(cards) >> 24
    branks = [c[0] for c in board]
    board_paired = len(set(branks)) < len(branks)
    if t >= 2:
        if t == 2 and board_paired and not any(c[0] in branks for c in hole):
            return 1 if max(RANKS.index(c[0]) for c in hole) < 4 else 0.5
        return 2
    if t == 1:
        if any(c[0] in branks for c in hole) or hole[0][0] == hole[1][0]:
            return 1
        return 0
    if len(board) < 5:
        suits = [c[1] for c in list(hole) + list(board)]
        if max(suits.count(s) for s in SUITS) >= 4:
            return 0.5
    return 0


M = Model()


# ── helpers ───────────────────────────────────────────────────────────────────
def safe(gs):
    return {"action": "check"} if gs.get("can_check") else {"action": "fold"}


def raise_to(gs, total, tag=None, dbg=None):
    me_total = gs["your_stack"] + gs["your_bet_this_street"]
    total = int(max(total, gs["min_raise_to"]))
    if total >= me_total * 0.92:
        return {"action": "all_in", "debug": dbg}
    return {"action": "raise", "amount": total, "debug": dbg}


def opp_player(gs):
    for p in gs.get("players") or []:
        if p.get("bot_id") != gs.get("your_bot_id"):
            return p
    return None


def pf_index(S):
    k = int(round(S * 2)) - 2
    return min(max(k, 0), len(PF_STACKS) - 1)


# ── equity vs a weighted opponent range (postflop) ───────────────────────────
def opp_aggr_sizes(gs, opp_id):
    """The opponent's postflop bets/raises this hand as (bet|raise, chips added / pot before)."""
    out = []
    street, contrib, pot = None, {}, 0
    for e in gs.get("action_log", []):
        a, who, amt, st = e.get("action"), e.get("bot_id"), e.get("amount") or 0, e.get("street")
        if st != street:
            street, contrib = st, {}
        if a == "ante":
            pot += amt
            continue
        if a in ("small_blind", "big_blind", "call"):
            contrib[who] = contrib.get(who, 0) + amt
            pot += amt
            continue
        if a in AGGR:
            prev = contrib.get(who, 0)
            others = max([v for k, v in contrib.items() if k != who] or [0])
            if who == opp_id and st != "preflop":
                out.append(("raise" if others > prev else "bet", max(0, amt - prev) / max(1, pot)))
            contrib[who] = max(prev, amt)
            pot += max(0, amt - prev)
    return out


def postflop_samples(gs, budget):
    hole = gs["your_cards"]
    board = gs["community_cards"]
    known = set(CARD_I[c] for c in hole + board)
    rest = [i for i in range(52) if i not in known]
    my = [DECK[CARD_I[c]] for c in hole]
    bd = [DECK[CARD_I[c]] for c in board]
    need = 5 - len(board)
    # preflop range of the opponent
    pre = [a for a in gs.get("action_log", []) if a.get("street") == "preflop" and a.get("action") not in POSTS]
    opp_id = M.opp
    opp_pre = [a["action"] for a in pre if a.get("bot_id") == opp_id]
    n_raises = sum(1 for a in pre if a.get("action") in AGGR)
    if opp_pre and opp_pre[-1] in AGGR:
        pre_frac = 0.14 if n_raises >= 2 else max(0.35, M.raise_frac())
    elif "call" in opp_pre:
        pre_frac = 0.55 if n_raises >= 1 else max(0.5, M.open_frac())
    else:
        pre_frac = 1.0
    # postflop actions of the opponent this hand (aggression shapes its range)
    post = [a for a in gs.get("action_log", []) if a.get("street") != "preflop" and a.get("bot_id") == opp_id]
    n_bets = sum(1 for a in post if a.get("action") in AGGR)
    n_calls = sum(1 for a in post if a.get("action") == "call")
    n_checks = sum(1 for a in post if a.get("action") == "check")
    # one weight table per opponent bet/raise this hand, from what THAT size has meant at showdown
    lw_list = []
    for kind, ratio in opp_aggr_sizes(gs, opp_id)[-3:]:
        bl = min(1.0, max(0.06, M.size_bluff(kind, ratio) * 1.6))
        if kind == "bet":
            lw_list.append({0: bl, 0.5: max(bl, 0.6), 1: 0.8, 2: 1.0})
        else:                       # raises: honest by default (most bots raise only for value)
            lw_list.append({0: bl, 0.5: max(bl, 0.4), 1: 0.6, 2: 1.0})
    lvl_w_call = {0: 0.3, 0.5: 1.0, 1: 1.0, 2: 1.0}
    lvl_w_check = {0: 1.0, 0.5: 1.0, 1: 1.0, 2: 0.75}
    branks = [c[0] for c in board]
    board_paired = len(set(branks)) < len(branks)
    bsuits = [c[1] for c in board]
    bval = 0
    out = []
    t_end = time.perf_counter() + budget
    n = 0
    rnd = random.random
    while n < 3000:
        if (n & 63) == 0 and time.perf_counter() > t_end:
            break
        n += 1
        pick = random.sample(rest, 2 + need)
        o1, o2 = pick[0], pick[1]
        cls = CLS52[o1, o2]
        w = 1.0 if PCT[cls] <= pre_frac else 0.12
        oc = [DECK[o1], DECK[o2]]
        # opponent's strength on the current board
        ov = eval7.evaluate(oc + bd)
        t = ov >> 24
        s1, s2 = DECK_STR[o1], DECK_STR[o2]
        if t >= 2:
            lvl = 2 if not (t == 2 and board_paired and s1[0] not in branks and s2[0] not in branks) else 1
        elif t == 1:
            lvl = 1 if (s1[0] in branks or s2[0] in branks or s1[0] == s2[0]) else 0
        else:
            lvl = 0
            if need:
                for s in SUITS:
                    if (s1[1] == s) + (s2[1] == s) + bsuits.count(s) >= 4:
                        lvl = 0.5
                        break
        for lw in lw_list:
            w *= lw[lvl]
        if n_calls:
            w *= lvl_w_call[lvl] ** min(n_calls, 2)
        if n_checks:
            w *= lvl_w_check[lvl]
        full = bd + [DECK[i] for i in pick[2:]]
        mv = eval7.evaluate(my + full)
        vv = eval7.evaluate(oc + full)
        win = 1.0 if mv > vv else (0.5 if mv == vv else 0.0)
        out.append((lvl + (ov & 0xFFFFFF) / 0x1000000 * 0.5, w, win))
    return out


def equity_of(samples, fold_share=0.0):
    """Equity vs the samples, after removing the weakest `fold_share` of the weight."""
    if not samples:
        return 0.5
    tot = sum(s[1] for s in samples)
    if fold_share <= 0:
        return sum(s[1] * s[2] for s in samples) / tot
    cut = tot * fold_share
    acc = 0.0
    num = den = 0.0
    for sc, w, win in sorted(samples, key=lambda s: s[0]):
        if acc + w <= cut:
            acc += w
            continue
        keep = w if acc >= cut else (acc + w - cut)
        acc += w
        num += keep * win
        den += keep
    return num / den if den > 0 else 0.0


# ── preflop ───────────────────────────────────────────────────────────────────
def preflop(gs):
    bb = gs["blinds"]["bb"]
    ante = gs["blinds"].get("ante", 0) or 0
    akey = "ante10" if ante > 0 else "ante0"
    a_bb = ante / bb
    me_total = gs["your_stack"] + gs["your_bet_this_street"]
    opp = opp_player(gs)
    opp_total = (opp["stack"] + opp["bet_this_street"]) if opp else me_total
    S = min(me_total, opp_total) / bb
    h = _cls_of(*gs["your_cards"])
    name = CLASSES[h]
    owed = gs["amount_owed"]
    pot = gs["pot"]
    i_sb = gs.get("seat_to_act") == gs.get("sb_seat")
    log = [a for a in gs.get("action_log", []) if a.get("street") == "preflop" and a.get("action") not in POSTS]
    n_raises = sum(1 for a in log if a.get("action") in AGGR)
    opp_allin = bool(opp and (opp.get("is_all_in") or opp.get("stack", 1) == 0))
    k = pf_index(S)
    dbg = {"S": round(S, 1), "h": name, "mode": M.mode}

    # facing an all-in (or a bet that commits us): pure pot odds vs their range
    if owed > 0 and (opp_allin or owed >= 0.45 * me_total):
        if i_sb or n_raises >= 2:
            rng = 0.16 if n_raises >= 2 else 0.2
        else:
            rng = blend(M.sb_shove, M.sb_n, PF_PUSH_FRAC[akey][k], 6) if S <= 20 else 0.12
        eq = range_eq(h, rng)
        need = owed / (pot + owed)
        dbg.update(eq=round(eq, 3), need=round(need, 3), rng=round(rng, 2))
        return {"action": "call", "debug": dbg} if eq >= need else {"action": "fold", "debug": dbg}

    # SB first to act
    if i_sb and not log:
        if S <= 13:
            c_nash = PF_CALL_FRAC[akey][k]
            c = M.call_vs_shove(c_nash)
            eq_c = range_eq(h, c)
            ev_push = (1 - c) * (1 + a_bb) + c * (2 * eq_c - 1) * (S + a_bb)
            ev_fold = -(0.5 + a_bb)
            dbg.update(ev_push=round(ev_push, 2), c=round(c, 2))
            if ev_push > ev_fold or PF_PUSH[akey][k][h] > random.random() and ev_push > ev_fold - 0.15:
                return {"action": "all_in", "debug": dbg}
            return {"action": "fold", "debug": dbg}
        fto = M.fold_to_open()
        tb = M.threebet_rate()
        width = 0.80
        if fto > 0.5:
            width = 1.0
        elif fto > 0.4:
            width = 0.9
        if tb > 0.25:
            width = min(width, 0.65)
        if name in ("72o", "72s") and S > 25:
            width = 1.0                                 # the 7-2 prize: cheap when blinds are low
        size = 2.5 if S > 25 else 2.2
        if PCT[h] <= width:
            return raise_to(gs, size * bb, dbg=dbg)
        if gs["can_check"]:
            return {"action": "check", "debug": dbg}
        if PCT[h] <= width + 0.1 and owed <= 0.5 * bb:
            return {"action": "call", "debug": dbg}
        return {"action": "fold", "debug": dbg}

    # BB after a limp
    if owed == 0:
        width = 0.35 if M.open_frac() - M.raise_frac() < 0.3 else 0.45
        if S <= 13 and PCT[h] <= PF_PUSH_FRAC[akey][k] * 0.6:
            return {"action": "all_in", "debug": dbg}
        if PCT[h] <= width and random.random() < 0.85:
            return raise_to(gs, gs["current_bet"] + 3 * bb, dbg=dbg)
        return {"action": "check", "debug": dbg}

    # facing a raise
    need = owed / (pot + owed)
    if n_raises == 1:                          # BB vs an open
        rng = max(0.3, M.raise_frac())
        eq = range_eq(h, rng)
        dbg.update(eq=round(eq, 3), need=round(need, 3), rng=round(rng, 2))
        if S <= 25:
            if eq >= 0.57 or (S <= 13 and PF_CALL[akey][k][h] > 0.5 and eq >= 0.5):
                return {"action": "all_in", "debug": dbg}
        elif eq >= 0.62 or (eq >= 0.5 and M.fold_to_open() > 0.0 and random.random() < 0.08):
            return raise_to(gs, gs["current_bet"] * 3, dbg=dbg)
        if eq * 0.9 * (pot + owed) - owed > 0 or eq >= need + 0.08:
            return {"action": "call", "debug": dbg}
        return {"action": "fold", "debug": dbg}

    # facing a 3-bet or more
    rng = 0.15 if n_raises == 2 else 0.07
    eq = range_eq(h, rng)
    dbg.update(eq=round(eq, 3), need=round(need, 3), rng=rng)
    if eq >= 0.56:
        return {"action": "all_in", "debug": dbg}
    if eq >= need + 0.05 and owed < 0.3 * me_total:
        return {"action": "call", "debug": dbg}
    return {"action": "fold", "debug": dbg}


# ── mode control (bait → trap or solid) ───────────────────────────────────────
FORCE_MODE = None          # set to "solid" to switch off the bait/trap personality (for testing)


def update_mode(gs):
    hn = gs.get("hand_num", 0)
    if FORCE_MODE:
        M.mode = FORCE_MODE
        return
    small = [(h, r) for h, b, r in M.resp_log if b == "S"]
    if M.mode == "solid":
        # reactive trap: the opponent attacks our small bets, so our small bets become our strong hands
        recent = [r for h, r in small[-12:]]
        if len(recent) >= 5 and sum(1 for r in recent if r == 2) / len(recent) >= 0.3 \
                and hn - M.mode_switch_hand > 10:
            M.mode, M.mode_switch_hand = "trap", hn
        return
    if M.mode == "bait":
        early = [r for h, r in small if h < 20]
        late = [r for h, r in small if h >= 20]
        if len(late) >= 4 and len(early) >= 3:
            e_att = sum(1 for r in early if r != 0) / len(early)
            l_att = sum(1 for r in late if r != 0) / len(late)
            l_raise = sum(1 for r in late if r == 2) / len(late)
            if l_att - e_att >= 0.25 or l_raise >= 0.3:
                M.mode, M.mode_switch_hand = "trap", hn
                return
        if hn >= 45:
            M.mode, M.mode_switch_hand = "solid", hn
    elif M.mode == "trap":
        start = M.stack_at.get(M.mode_switch_hand)
        now = M.stack_at.get(hn)
        if start and now is not None and now < start * 0.8:
            M.mode, M.mode_switch_hand = "solid", hn
        if hn - M.mode_switch_hand > 80:
            M.mode, M.mode_switch_hand = "solid", hn


# ── postflop ──────────────────────────────────────────────────────────────────
def postflop(gs):
    tl = (gs.get("time_limit_ms") or 2000) / 1000.0
    samples = postflop_samples(gs, min(0.35, tl * 0.18))
    eq = equity_of(samples)
    pot = gs["pot"]
    owed = gs["amount_owed"]
    me_total = gs["your_stack"] + gs["your_bet_this_street"]
    street = gs["street"]
    in_pos = gs.get("seat_to_act") == gs.get("button_seat")
    dbg = {"eq": round(eq, 3), "mode": M.mode, "n": len(samples)}
    hole = gs["your_cards"]
    is72 = sorted(c[0] for c in hole) == ["2", "7"]
    barrel = any(a.get("bot_id") == M.opp and a.get("action") == "call" and a.get("street") != "preflop"
                 for a in gs.get("action_log", []))
    dbg["barrel"] = barrel
    opp_raised_post = any(a.get("bot_id") == M.opp and a.get("action") in AGGR and a.get("street") != "preflop"
                          for a in gs.get("action_log", []))
    fold_scale = 0.5 if opp_raised_post else 1.0

    if owed > 0:
        need = owed / (pot + owed)
        bl = M.bluff_frac()
        raise_th = 0.80 - (0.06 if bl > 0.35 else 0)
        dbg.update(need=round(need, 3))
        opp = opp_player(gs)
        opp_allin = bool(opp and (opp.get("is_all_in") or opp.get("stack", 1) == 0))
        if eq >= raise_th and not opp_allin:
            if M.mode == "trap" or random.random() < 0.85:
                return raise_to(gs, gs["current_bet"] + 0.9 * (pot + owed), dbg=dbg)
        margin = 0.0 if street == "river" else 0.03
        if eq >= need + margin:
            return {"action": "call", "debug": dbg}
        return {"action": "fold", "debug": dbg}

    # checked to us / first to act: compare EV of checking and of each bet size
    real = 0.95 if in_pos else 0.85
    ev_check = eq * pot * (1.0 if street == "river" else real)
    best = ("check", ev_check, 0.0)
    evs = {"check": round(ev_check, 1)}
    for b in BUCKETS:
        bet = SIZES[b] * pot
        if bet > me_total:
            bet = me_total
        f = M.fold_rate(b, barrel) * fold_scale
        eq_c = equity_of(samples, f)
        ev = f * pot + (1 - f) * (eq_c * (pot + 2 * bet) - bet)
        if street != "river":
            ev = f * pot + (1 - f) * (eq_c * (pot + 2 * bet) * real - bet)
        evs[b] = round(ev, 1)
        if ev > best[1]:
            best = (b, ev, bet)
    dbg["evs"] = evs

    # bait / trap override of the size (the "story" we tell the opponent)
    choice = best[0]
    if M.mode == "bait":
        if choice != "check" or eq >= 0.72:
            choice = "L" if eq >= 0.62 else "S"
            if eq < 0.62 and evs.get("S", -1e9) < ev_check - 0.15 * pot:
                choice = "check"
    elif M.mode == "trap":
        if eq >= 0.70:
            choice = "S"                                  # strong hand, small "weak-looking" bet
        elif choice != "check" and eq < 0.5:
            choice = "L" if M.fold_rate("L", barrel) >= 0.45 else choice
    else:
        # a little mixing so the sizes don't give our hand away
        if choice != "check" and random.random() < 0.15:
            alt = sorted(BUCKETS, key=lambda b: -evs[b])[1]
            if evs[alt] > best[1] - 0.08 * pot:
                choice = alt
    if is72 and choice == "check" and M.fold_rate("M", barrel) > 0.4 and random.random() < 0.6:
        choice = "M"                                      # the 7-2 prize
    dbg["choice"] = choice
    if choice == "check":
        return {"action": "check", "debug": dbg}
    bet = min(SIZES[choice] * pot, me_total)
    if me_total - bet < 0.3 * (pot + 2 * bet):
        return {"action": "all_in", "debug": dbg}
    return raise_to(gs, gs["your_bet_this_street"] + max(bet, gs["blinds"]["bb"]), dbg=dbg)


# ── entry point ───────────────────────────────────────────────────────────────
def decide(game_state: dict) -> dict:
    if game_state.get("type") == "warmup":
        try:
            postflop_samples({"your_cards": ["As", "Kd"], "community_cards": ["2c", "7h", "9s"],
                              "action_log": []}, 0.05)
        except Exception:
            pass
        return {"action": "check"}
    try:
        M.update(game_state)
    except Exception:
        pass
    try:
        update_mode(game_state)
    except Exception:
        pass
    try:
        if game_state.get("street") == "preflop":
            act = preflop(game_state)
        else:
            act = postflop(game_state)
        if act.get("action") == "check" and game_state.get("amount_owed", 0) > 0:
            act = {"action": "fold", "debug": act.get("debug")}
        if act.get("debug") is None:
            act.pop("debug", None)
        return act
    except Exception as e:  # noqa: BLE001
        out = safe(game_state)
        out["debug"] = {"error": repr(e)[:200]}
        return out
