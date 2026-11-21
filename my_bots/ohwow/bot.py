"""
ohwow: heads-up No-Limit Hold'em bot for the IEEE uOttawa x LARUS hackathon kit.

Pure Python plus eval7 (all allowed in the sandbox). No network, files or threads.

Layout of this file:
  1. cards, hand evaluator, preflop strength table, equity by Monte Carlo (eval7 when available)
  2. opponent model: learns per-situation fold/call patterns and what the opponent's
     bets really mean, fed from the kit's match_action_log
  3. _decide_core: the betting logic (EV-based bet sizing, bluffs, pot control, stack depth,
     position, heads-up preflop ranges, short-stack push/fold)
  4. _play: safety wrapper (never raises, always returns a legal action)
  5. decide(game_state): the kit entry point: converts the kit's state and action formats
"""
import random
import time
from collections import Counter

# Global settings. Change these at the event, not deep in the code.
CONFIG = {
    "time_budget": 0.45,   # seconds of equity simulation per decision (kit limit is 2s on half a core)
    "heads_up": True,      # the tournament is heads-up: wider preflop ranges, push/fold chart
    "safe_mode": True,     # decide() never raises, never returns an illegal action
}

try:                       # eval7 is installed in the tournament sandbox
    import eval7
except ImportError:        # pure-Python fallback keeps the bot working anywhere
    eval7 = None

SUITS = "shdc"
FULL_DECK = [(r, s) for r in range(2, 15) for s in SUITS]
_RANK_MAP = {c: i + 2 for i, c in enumerate("23456789TJQKA")}
_SUIT_MAP = {"♠": "s", "♥": "h", "♦": "d", "♣": "c"}


# ================================================================== cards
def parse_card(c):
    """Accepts 'As', 'Td', '10h', 'A♠', (14,'s') or ('A','s')."""
    if isinstance(c, tuple):
        return (c[0], c[1]) if isinstance(c[0], int) else (_RANK_MAP[c[0]], c[1])
    c = str(c).strip()
    rank = "T" if c[:-1] == "10" else c[:-1].upper()
    return (_RANK_MAP[rank], _SUIT_MAP.get(c[-1], c[-1].lower()))


# =============================================================== evaluator
_E7 = {}


def _score(cards):
    """Comparable hand score (higher wins): eval7 if present, else evaluate()."""
    if eval7 is None:
        return evaluate(cards)
    if not _E7:
        for r in range(2, 15):
            for s in "shdc":
                _E7[(r, s)] = eval7.Card("23456789TJQKA"[r - 2] + s)
    return eval7.evaluate([_E7[c] for c in cards])


def _straight_high(ranks):
    rs = set(ranks)
    if 14 in rs:
        rs.add(1)
    for hi in range(14, 4, -1):
        if all(r in rs for r in range(hi - 4, hi + 1)):
            return hi
    return 0


def evaluate(cards):
    """Best 5-card hand from 5-7 cards -> comparable tuple (higher wins)."""
    cnt = Counter(r for r, _ in cards)
    by_suit = {}
    for r, s in cards:
        by_suit.setdefault(s, []).append(r)
    flush = None
    for rs in by_suit.values():
        if len(rs) >= 5:
            flush = sorted(rs, reverse=True)
    if flush:
        sf = _straight_high(flush)
        if sf:
            return (8, sf)
    groups = sorted(cnt.items(), key=lambda x: (x[1], x[0]), reverse=True)
    top, top_n = groups[0]
    if top_n == 4:
        return (7, top, max(r for r in cnt if r != top))
    if top_n == 3 and len(groups) > 1 and groups[1][1] >= 2:
        return (6, top, groups[1][0])
    if flush:
        return (5, *flush[:5])
    sh = _straight_high(cnt)
    if sh:
        return (4, sh)
    if top_n == 3:
        return (3, top, *sorted((r for r in cnt if r != top), reverse=True)[:2])
    if top_n == 2 and len(groups) > 1 and groups[1][1] == 2:
        pairs = sorted((r for r, c in cnt.items() if c == 2), reverse=True)[:2]
        return (2, *pairs, max(r for r in cnt if r not in pairs))
    if top_n == 2:
        return (1, top, *sorted((r for r in cnt if r != top), reverse=True)[:3])
    return (0, *sorted(cnt, reverse=True)[:5])


# ========================================================= hand strength %
def _chen(hi, lo, suited):
    pts = {14: 10, 13: 8, 12: 7, 11: 6}.get(hi, hi / 2)
    if hi == lo:
        return max(5.0, pts * 2)
    if suited:
        pts += 2
    gap = hi - lo - 1
    pts -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and hi < 12:
        pts += 1
    return pts


def _build_preflop_table():
    classes = []
    for hi in range(2, 15):
        for lo in range(2, hi + 1):
            if hi == lo:
                classes.append((hi, lo, False, 6, _chen(hi, lo, False)))
            else:
                classes.append((hi, lo, True, 4, _chen(hi, lo, True)))
                classes.append((hi, lo, False, 12, _chen(hi, lo, False)))
    classes.sort(key=lambda x: x[4])  # weakest first
    total = sum(c[3] for c in classes)
    table, cum = {}, 0
    for hi, lo, suited, w, _ in classes:
        cum += w
        table[(hi, lo, suited)] = (cum - w / 2) / total  # 1.0 = best hand
    return table


PREFLOP_PCT = _build_preflop_table()


def preflop_pct(hole):
    """Strength percentile of a starting hand (1.0 = AA, ~0 = 72o)."""
    (r1, s1), (r2, s2) = hole
    hi, lo = max(r1, r2), min(r1, r2)
    return PREFLOP_PCT[(hi, lo, hi != lo and s1 == s2)]


def _flush_draw(hand, board):
    cnt = Counter(s for _, s in hand + board)
    return any(n == 4 and any(c[1] == s for c in hand) for s, n in cnt.items())


def _made_pct(hand, board):
    """Rough strength percentile of a made hand on the current board."""
    ev = evaluate(hand + board)
    cat = ev[0]
    if cat == 1:
        pr = ev[1]
        brs = sorted((r for r, _ in board), reverse=True)
        if not any(r == pr for r, _ in hand):
            p = 0.2  # pair is on the board, not ours
        elif pr > brs[0]:
            p = 0.75
        elif pr == brs[0]:
            p = 0.65
        elif len(brs) > 1 and pr == brs[1]:
            p = 0.5
        else:
            p = 0.38
    else:
        p = {0: 0.2, 2: 0.8, 3: 0.88, 4: 0.93, 5: 0.94, 6: 0.97}.get(cat, 0.995)
    if len(board) < 5 and cat < 4 and _flush_draw(hand, board):
        p = max(p, 0.6)  # betting range includes draws
    return p


def _accept(pct, tight):
    """Prob. that an opponent hand of strength `pct` fits a range narrowed by
    `tight` (0 = any two cards, ~0.9 = mostly the top of the range). Weak hands
    keep a small chance so bluffs stay in the range."""
    return max(0.03, pct ** (2.5 * tight))


# ================================================================= equity
def equity(hole, board, n_opp, tight=0.0, budget=0.15, max_iter=12000):
    """Win prob. (ties = half) vs n_opp opponents whose hands are sampled from
    a range narrowed by `tight`."""
    known = set(hole) | set(board)
    deck = [c for c in FULL_DECK if c not in known]
    need = 5 - len(board)
    n_opp = max(1, min(n_opp, (len(deck) - need) // 2))
    pre = not board
    score, n = 0.0, 0
    end = time.perf_counter() + budget
    min_iter = 60 if budget >= 0.05 else 12
    while n < max_iter and (n < min_iter or time.perf_counter() < end):
        random.shuffle(deck)
        full = board + deck[:need]
        p = need
        opp_best = None
        for i in range(n_opp):
            tries = max(1, min(12, (len(deck) - p) // (2 * (n_opp - i))))
            best_h, best_pct = None, -1.0
            for _ in range(tries):
                h = deck[p:p + 2]
                p += 2
                if tight <= 0:
                    best_h = h
                    break
                pct = preflop_pct(h) if pre else _made_pct(h, board)
                if random.random() < _accept(pct, tight):
                    best_h = h
                    break
                if pct > best_pct:           # nothing fit: keep the strongest seen
                    best_h, best_pct = h, pct
            h = best_h
            sc = _score(h + full)
            if opp_best is None or sc > opp_best:
                opp_best = sc
        mine = _score(hole + full)
        score += 1.0 if mine > opp_best else 0.5 if mine == opp_best else 0.0
        n += 1
    return score / n


# ============================================================ opponent model
STYLE = {"agg": 1.15}   # our aggression level; re-rolled every ~25 hands
NEUTRAL = {"fold": 0.4, "aggr": 0.45, "vpip": 0.35}


def _reroll_style():
    STYLE["agg"] = random.uniform(1.05, 1.30)


def _bucket(frac):
    """Bet size bucket: 0 small (<0.4 pot), 1 medium, 2 large (>=0.8 pot)."""
    return 0 if frac < 0.4 else 1 if frac < 0.8 else 2


class Profile:
    PRIOR = 5   # pseudo-observations so early reads aren't wild (matches can be short)
    K_CTX = 3   # smaller prior for per-situation stats -> deterministic bots
                # are recognized after a handful of samples

    def __init__(self):
        self.hands = self.vpip_n = self.bets = self.calls = 0
        self.allins = 0     # times they shoved all-in preflop
        self.faced = self.folded = 0
        self.vs_me = {}   # (street, raise depth, our bet bucket) -> [folds, times faced]
        self.shown = {}   # their bet bucket -> [sum hand strength, n] at showdown
        self.rr_n = 0     # times they RE-RAISED one of our raises/bets
        self.rr_shown = [0.0, 0]   # hand strength they showed after re-raising

    def add(self, o):
        self.rr_shown[0] += o.rr_shown[0]
        self.rr_shown[1] += o.rr_shown[1]
        for k in ("hands", "vpip_n", "bets", "calls", "faced", "folded", "allins", "rr_n"):
            setattr(self, k, getattr(self, k) + getattr(o, k))
        for mine, theirs in ((self.vs_me, o.vs_me), (self.shown, o.shown)):
            for key, v in theirs.items():
                a = mine.setdefault(key, [0, 0])
                a[0] += v[0]
                a[1] += v[1]

    def fold_to_bet(self):
        return (self.folded + self.PRIOR * 0.40) / (self.faced + self.PRIOR)

    def aggression(self):
        return (self.bets + self.PRIOR * 0.45) / (self.bets + self.calls + self.PRIOR)

    def vpip(self):
        return min(1.0, (self.vpip_n + self.PRIOR * 0.35) / (self.hands + self.PRIOR))

    def shove_rate(self):
        """Share of hands they shove all-in preflop (they act first in ~half of them,
        so a bot that shoves every time it opens scores ~1.0). Smoothed for early reads."""
        return min(1.0, self.allins / (0.5 * self.hands + 4.0))

    def shover(self):
        """True once they have shoved all-in preflop in most of the hands seen."""
        return self.allins >= 2 and self.allins / max(self.hands, 1) > 0.7


def _never_raise(fn):
    """Opponent tracking is a bonus: a bad input from the repo must not crash a move."""
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:
            return None
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


class OpponentModel:
    def __init__(self):
        self.me = None
        self.players = {}
        self.table = Profile()     # everyone combined (fallback)
        self.hand_no = 0
        self._reset_hand()

    def _reset_hand(self):
        self.hand_t = {}           # player -> cumulative range tightness this hand
        self.last_bet = {}         # player -> their last bet size (fraction of pot)
        self.last_raiser = None
        self.last_frac = 0.5
        self.cur_street = -1
        self.n_raises = 0          # voluntary raises so far on the current street
        self.rr_hand = set()       # players who re-raised this hand
        self.last_depth = 0        # raises that preceded the last raise

    def _p(self, name):
        return self.players.setdefault(name, Profile())

    def _combined(self, ids):
        profs = [self.players[i] for i in (ids or []) if i in self.players]
        if not profs:
            return self.table
        prof = Profile()
        for p in profs:
            prof.add(p)
        return prof

    # ---- feeding the model -------------------------------------------
    @_never_raise
    def observe_hand_start(self, player_ids):
        self._reset_hand()
        self.hand_no += 1
        if self.hand_no % 25 == 1:
            _reroll_style()        # drift our own style so adaptive bots can't pin us
        for pid in player_ids:
            if pid != self.me:
                self._p(pid).hands += 1
                self.table.hands += 1

    @_never_raise
    def observe_action(self, player, street, kind, to_call=0, pot=None,
                       amount=None, bb=2, record=True):
        """kind in {'fold','check','call','raise'}; street 0 = preflop.
        to_call: chips the actor had to call. pot: pot BEFORE their action
        (including the bet they faced). amount: chips they added."""
        if street != self.cur_street:
            self.cur_street, self.n_raises = street, 0
        if kind == "raise":
            if self.n_raises >= 1 and player != self.me:
                self.rr_hand.add(player)
                if record and self.last_raiser == self.me:
                    for p in (self._p(player), self.table):
                        p.rr_n += 1
            self.last_raiser = player
            self.last_depth = min(self.n_raises, 2)
            self.n_raises += 1
            if amount is not None and pot:
                self.last_frac = amount / max(pot, 1)
                self.last_bet[player] = self.last_frac
        if player == self.me:
            return
        profs = (self._p(player), self.table)
        if record:          # record=False: replaying the live hand, stats come later
            for p in profs:
                if kind == "raise":
                    p.bets += 1
                elif kind == "call":
                    p.calls += 1
                if street == 0 and kind in ("call", "raise") and to_call > 0:
                    p.vpip_n += 1
                if to_call > 0:
                    p.faced += 1
                    if kind == "fold":
                        p.folded += 1
            # how do they respond to OUR aggression, per street and size?
            if to_call > 0 and self.last_raiser == self.me:
                key = (street, self.last_depth, _bucket(self.last_frac))
                for p in profs:
                    rec = p.vs_me.setdefault(key, [0, 0])
                    rec[1] += 1
                    rec[0] += int(kind == "fold")
        # accumulate range narrowing across streets (incomplete information)
        if pot and kind in ("raise", "call"):
            if kind == "raise" and amount:
                inc = _tightness(amount, pot + amount, bb, street == 0, NEUTRAL)
            else:
                inc = 0.4 * _tightness(to_call, pot, bb, street == 0, NEUTRAL)
            old = self.hand_t.get(player, 0.0)
            self.hand_t[player] = 1 - (1 - old) * (1 - inc)

    @_never_raise
    def observe_allin(self, player):
        """Opponent shoved all-in preflop (completed hands only)."""
        if player != self.me:
            self._p(player).allins += 1
            self.table.allins += 1

    @_never_raise
    def observe_showdown(self, player, hole, board):
        """Call when an opponent's cards are revealed. Teaches us what their
        BETS mean (only counted if they bet this hand)."""
        if player == self.me or player not in self.last_bet:
            return
        hole = [parse_card(c) for c in hole]
        board = [parse_card(c) for c in board]
        pct = _made_pct(hole, board) if board else preflop_pct(hole)
        if player in self.rr_hand:
            for p in (self._p(player), self.table):
                p.rr_shown[0] += pct
                p.rr_shown[1] += 1
        key = _bucket(self.last_bet[player])
        for p in (self._p(player), self.table):
            rec = p.shown.setdefault(key, [0.0, 0])
            rec[0] += pct
            rec[1] += 1

    # ---- reading the model -------------------------------------------
    def view(self, ids=None):
        prof = self._combined(ids)
        return {"fold": prof.fold_to_bet(), "aggr": prof.aggression(),
                "vpip": prof.vpip(), "shover": prof.shover(),
                "shove_rate": prof.shove_rate()}

    def ctx_fold(self, ids, street, depth, bucket, base):
        """P(fold) to OUR bet/raise in this context. `depth` = raises that
        preceded ours (0 = opening bet, 1 = raise over a bet, 2 = re-raise war).
        Folding to an open says nothing about folding to a 5-bet, so stats are
        never pooled across depths. Estimates shrink size-bucket -> street+depth
        -> general fold rate. Returns (prob, street_depth_samples)."""
        prof = self._combined(ids)
        K = Profile.K_CTX
        f_s = n_s = 0
        for (st, d, _b), (f, n) in prof.vs_me.items():
            if st == street and d == depth:
                f_s += f
                n_s += n
        rate = (f_s + K * base) / (n_s + K)
        f, n = prof.vs_me.get((street, depth, bucket), (0, 0))
        return (f + K * rate) / (n + K), n_s

    def learned_tight(self, ids, bucket):
        """(tightness, weight) learned from showdowns, or (None, 0). Uses the
        bet-size bucket if it has data, else pools all their shown bets."""
        shown = self._combined(ids).shown
        s, n = shown.get(bucket, (0.0, 0))
        if n < 4:
            s = sum(v[0] for v in shown.values())
            n = sum(v[1] for v in shown.values())
        if n < 4:
            return None, 0.0
        return min(0.9, max(0.0, (s / n - 0.35) * 2.0)), min(0.85, n / 15)

    def rr_read(self, ids):
        """How to read THEIR re-raises: (tightness|None, weight, rr_freq|None).
        Showdowns of re-raises teach how strong they are; how often they re-raise
        our bets (vs how often they faced them) says how bluffy they are."""
        prof = self._combined(ids)
        s, n = prof.rr_shown
        lt, w = (None, 0.0)
        if n >= 2:
            lt, w = min(0.9, max(0.0, (s / n - 0.35) * 2.0)), min(0.85, n / 6)
        faced = sum(v[1] for v in prof.vs_me.values())
        freq = prof.rr_n / faced if faced >= 8 else None
        return lt, w, freq

    def hand_tight(self, ids):
        vals = [self.hand_t.get(i, 0.0) for i in ids] if ids else list(self.hand_t.values())
        return max(vals) if vals else 0.0


MODEL = OpponentModel()


# ============================================================ reading bets
def _loose_agg(view):
    """0 = normal opponent .. 1 = plays almost every hand and raises almost every
    time (maniac / shover). Scales how little their bets should worry us."""
    loose = min(1.0, max(0.0, (view["vpip"] - 0.35) / 0.4))
    aggro = min(1.0, max(0.0, (view["aggr"] - 0.45) / 0.35))
    return min(loose, aggro)


def _tightness(to_call, pot, bb, pre, view):
    """Heuristic: how much a bet narrows the bettor's range (0 = not at all)."""
    if to_call <= 0:
        return 0.0
    if pre:
        x = to_call / bb
        if x <= 1.0:
            return 0.0
        t = min(0.85, 0.2 * (x - 1))
    else:
        t = min(0.85, 0.9 * to_call / max(pot - to_call, 1))
    a, v = view["aggr"], view["vpip"]
    loose_agg = _loose_agg(view)
    if loose_agg > 0:               # loose-aggressive: their bets mean much less
        t *= 1.0 - 0.7 * loose_agg
    elif a < 0.35 and v < 0.25:     # tight-passive: bets mean a lot
        t *= 1.25
    return min(0.9, t)


# ================================================================== decide
def _decide_core(state):
    """
    state (plain dict):
      hole, board       lists of cards (any format parse_card accepts)
      pot               total pot including bets already made this street
      to_call           chips we must add to call (0 = we can check)
      stack             our remaining chips
      big_blind         big blind size
      opponents         number of opponents still in the hand
      min_raise         (optional) min chips we must add to make a legal raise
      opponent_ids      (optional) ids of opponents in hand -> per-player reads
      pf_aggressor      (optional) True if we raised preflop (enables c-bets)
      effective_stack   (optional) most chips that can change hands: min(our stack,
                        biggest stack among opponents still in the hand). Default = ours.
      position          (optional) 0.0 = first to act after the flop (worst seat)
                        .. 1.0 = button / last to act (best). Default 0.5 = unknown.
      raises            (optional) voluntary raises so far this street (blinds
                        excluded); estimated from to_call if absent
      time_budget       (optional) seconds for equity simulation, default 0.15
    returns (kind, amount): kind in {'fold','check','call','raise'}
      amount = TOTAL chips we add to the pot with this action (call part
      included), only meaningful for 'raise'. Equal to stack means all-in.
    """
    hole = [parse_card(c) for c in state["hole"]]
    board = [parse_card(c) for c in state.get("board", [])]
    pot = max(state["pot"], 1)
    to_call, stack = state["to_call"], state["stack"]
    bb = state.get("big_blind", 2)
    n_opp = max(1, state.get("opponents", 1))
    min_raise = state.get("min_raise", max(bb, 2 * to_call))
    ids = state.get("opponent_ids")
    pre = not board
    can_raise = stack > to_call
    view = MODEL.view(ids)
    k = (STYLE["agg"] - 1.0) / 0.15            # aggression scale (~0.3 .. 2)
    ip = 2 * state.get("position", 0.5) - 1    # -1 worst seat .. +1 button
    eff = max(1, min(stack, state.get("effective_stack", stack)))
    spr = eff / pot                            # stack-to-pot ratio: <2 shallow, >8 deep
    sidx = {0: 0, 3: 1, 4: 2, 5: 3}.get(len(board), 1)
    raises = state.get("raises")
    if raises is None:                          # estimate from the bet we face
        raises = 0 if (to_call == 0 or (pre and to_call <= bb)) else \
            (1 if (not pre or to_call < 6 * bb) else 2)
    depth = min(raises, 2)                      # depth of OUR next bet/raise
    barrels = int(state.get("barrels") or 0)    # they already called our bet(s) this hand

    # --- what do their actions say about their hand? (range narrowing) ---
    t = _tightness(to_call, pot, bb, pre, view)
    if to_call > 0:
        lt, w = MODEL.learned_tight(ids, _bucket(to_call / max(pot - to_call, 1)))
        if lt is not None:                     # showdown evidence beats heuristics
            t = (1 - w) * t + w * lt
    if to_call > 0 and raises >= 2 and not (pre and to_call <= bb):   # facing a RE-raise
        lt_rr, w_rr, rr_freq = MODEL.rr_read(ids)
        if lt_rr is not None:
            t = (1 - w_rr) * t + w_rr * lt_rr          # what their re-raises have shown
        if rr_freq is not None:
            if rr_freq > 0.30:
                t *= 0.55          # re-raises us constantly: mostly pressure/bluffs
            elif rr_freq < 0.08:
                t = min(0.9, t * 1.25 + 0.05)   # rarely re-raises: it's the nuts
    if barrels and not pre:
        # someone who calls our flop and turn bets is not folding to a third:
        # their range is "pairs and better", not air.
        t = 1 - (1 - t) * (1 - 0.12 * barrels)
    if not (pre and to_call <= bb):
        hist = MODEL.hand_tight(ids)           # earlier streets still count
        t = 1 - (1 - t) * (1 - (0.6 if to_call > 0 else 0.3) * hist)
    t = min(0.92, t)
    if CONFIG["heads_up"] and pre and eff <= 12 * bb:
        t = min(t, 0.4)        # short stacks shove wide: don't assume a premium range
    shover = bool(view.get("shover")) and pre and eff > 12 * bb
    if shover:
        t = 0.0                # an opponent that shoves every hand holds a random hand
    # a bot that shoves a WIDE part of its range (30-60% of hands) holds a wide range,
    # not "a big bet = premium": price the call against the range it really shoves
    wide_shove = (pre and not shover and eff > 12 * bb and to_call >= 0.3 * stack
                  and view.get("shove_rate", 0.0) > 0.18)
    if wide_shove:
        t = min(t, max(0.0, 1.0 - 1.15 * view["shove_rate"]))
    if n_opp > 1:
        t *= 0.7
    eq = equity(hole, board, n_opp, tight=t, budget=state.get("time_budget", CONFIG["time_budget"]))
    eq = min(1.0, max(0.0, eq + random.gauss(0, 0.02)))   # decision noise

    def fold_info(frac):
        base = view["fold"] * (0.75 + 0.5 * min(frac, 1.5))
        p, n = MODEL.ctx_fold(ids, sidx, depth, _bucket(frac), base)
        if barrels and n < 6:            # little data on this spot: assume the second/third
            p *= 0.6 ** barrels          # barrel folds far less than the first bet did
        return min(0.92, max(0.02, p)), n

    def fold_at(frac, n=n_opp):                # P(all opponents fold)
        return fold_info(frac)[0] ** n

    def chips(x, jitter=True):
        if jitter:
            x *= random.uniform(0.9, 1.15)     # vary sizing: don't leak strength
        x = min(stack, max(x, min_raise, bb))
        # commitment: a bet that covers the effective stack, or leaves too little
        # behind to fold (<30% of the resulting pot), is a shove either way.
        if x >= eff or stack - x < 0.3 * (pot + x):
            return int(stack)
        return int(x)

    if wide_shove:
        # Every call risks the whole match, folding only costs a blind: when deep, wait
        # for a hand clearly ahead of their (wide) range instead of calling at break-even.
        po = to_call / (pot + to_call)
        deep = min(1.0, max(0.0, (eff / bb - 20.0) / 60.0))
        return ("call", 0) if eq > po + 0.035 + 0.10 * deep else ("fold", 0)

    # ---- exploit: opponent shoves all-in every hand. Every call risks the whole
    # match, so wait for a premium hand (win chance ~70%+) and fold the rest.
    if shover and (to_call >= 0.3 * stack or 0 < to_call <= bb):
        # folding costs a blind, a call risks the match: when deep, wait for AA-QQ class hands
        need = 0.68 + 0.12 * min(1.0, max(0.0, (eff / bb - 15.0) / 35.0))
        if eq > need:
            return ("raise", int(stack)) if (can_raise and to_call < stack) else ("call", 0)
        return ("fold", 0) if to_call > 0 else ("check", 0)

    # ---- heads-up, first in preflop with a short stack: push chart ----
    if CONFIG["heads_up"] and pre and 0 < to_call <= bb and stack <= 12 * bb and can_raise:
        frac = min(0.95, max(0.40, 1.05 - 0.05 * stack / bb))   # share of hands to shove
        return ("raise", int(stack)) if preflop_pct(hole) >= 1 - frac else ("fold", 0)

    # ---- short stack: push / fold ----
    if stack <= 10 * bb:
        need = 0.50 + 0.04 * (n_opp - 1) - 0.08 * (view["fold"] - 0.4) - 0.03 * k
        pot_odds = to_call / (pot + to_call) if to_call else 0.0
        if to_call == 0:
            return ("raise", int(stack)) if (eq > need and can_raise) else ("check", 0)
        if CONFIG["heads_up"]:                      # price decides, not a fixed bar
            if eq > pot_odds + 0.03:
                return ("raise", int(stack)) if (eq > 0.62 and can_raise) else ("call", 0)
            return ("fold", 0)
        if eq > max(pot_odds + 0.05, need - 0.05):
            return ("raise", int(stack)) if (eq > need + 0.1 and can_raise) else ("call", 0)
        return ("fold", 0)

    # ---- preflop, unopened / limped: open-raise by hand strength ----
    if pre and to_call <= bb:
        pct = preflop_pct(hole)
        th = min(0.8, max(0.40, 0.65 - 0.3 * (view["fold"] - 0.4))) - 0.06 * k \
            + 0.04 * (n_opp - 1) - 0.08 * ip        # late seat opens wider
        if CONFIG["heads_up"]:
            th -= 0.30 + 0.05 * ip              # heads-up: the button opens ~80% of hands
        steal = pct > 0.25 and n_opp <= 2 and \
            random.random() < max(0.0, 0.06 + 0.04 * k + 0.04 * ip)
        if can_raise and (pct >= th or steal):
            return ("raise", chips((3.0 if stack > 30 * bb else 2.3) * bb))
        if to_call == 0:
            return ("check", 0)
        return ("call", 0) if pct >= 0.40 else ("fold", 0)

    # ---- checked to us: bet by EV, counting fold equity from learned patterns ----
    if to_call == 0:
        if not can_raise:
            return ("check", 0)
        if spr <= 1.5 and eq > 0.5 and random.random() < 0.65:
            return ("raise", int(stack))       # shallow: nothing to play with, get it in
        ev_check = eq * pot
        gain, size = 0.0, None
        fracs = [0.33, 0.66, 1.0] + ([spr] if 1.0 < spr <= 2.5 else [])   # shove option
        for frac in fracs:
            b = max(bb, min(frac * pot, eff))
            if eq < 0.42 and b >= 0.6 * eff:
                _pf, _nc = fold_info(b / pot)      # a huge bluff needs MEASURED folding,
                if not (_nc >= 6 and _pf >= 0.7):  # not a size-based guess
                    continue
            fe = fold_at(b / pot)
            eq_called = eq * (0.95 if eq >= 0.5 else 0.85)   # callers are stronger
            ev = fe * pot + (1 - fe) * (eq_called * (pot + 2 * b) - b)
            if ev - ev_check > gain:
                gain, size = ev - ev_check, b
        cbet = state.get("pf_aggressor", False) and len(board) == 3 and n_opp <= 2
        if size is not None and gain > max(0.015, 0.04 - 0.02 * k) * pot:
            p = min(0.97, 0.5 + 3 * gain / pot + 0.08 * k + (0.15 if cbet else 0)
                    + 0.07 * ip)                  # act last -> bet/bluff more
            if eq < 0.4:                       # pure bluff: capped frequency...
                pf, nc = fold_info(size / pot)
                if nc >= 8 and pf >= 0.8:      # ...unless they reliably fold
                    p = max(p, 0.95)
                else:
                    p = min(p, min(0.6, 0.35 + 0.15 * k))
                if size >= 0.5 * eff and spr > 4 and not (nc >= 8 and pf >= 0.8):
                    p = min(p, 0.15)           # deep: huge bluffs risk our stack
            if barrels >= 2 and eq < 0.5:      # they called two bets already: stop bluffing
                _pf, _nc = fold_info(size / pot)
                if not (_nc >= 8 and _pf >= 0.8):
                    p = min(p, 0.08)
            if eq < 0.82 and spr > 1.5 and size > 0.66 * pot:
                size = 0.66 * pot              # medium hands: don't build a huge pot,
                p *= 0.9                       # callers hold real hands
            if eq > 0.85 and n_opp == 1 and len(board) < 5 and random.random() < 0.2:
                p = 0.0                        # slow-play monsters sometimes
            if 0.52 < eq < 0.74 and spr > 3 and len(board) in (3, 4):
                size = min(size, 0.5 * pot)    # pot control: good-not-great hands bet
                p *= 0.8                       # small and a bit less often, so one
                                               # mistake can't cost the whole stack
            if random.random() < p:
                return ("raise", chips(size))
        # keep some bluffs in our river betting range so value bets get paid
        if (len(board) == 5 and n_opp == 1 and eq < 0.3 and fold_at(0.66) > 0.22
                and random.random() < 0.06 + 0.04 * k):
            return ("raise", chips(0.66 * pot))
        return ("check", 0)

    # ---- facing a bet ----
    pot_odds = to_call / (pot + to_call)
    if to_call >= stack:
        return ("call", 0) if eq > pot_odds + 0.03 else ("fold", 0)

    ev_call = eq * (pot + to_call) - to_call
    if can_raise:
        gain, size = 0.0, None
        for frac in (0.7, 1.2):
            a = chips(to_call + frac * (pot + to_call), jitter=False)
            p_fold, n_ctx = fold_info(frac)
            # trust measured behavior vs. our raises; otherwise assume a bettor
            # whose range has narrowed folds less
            fe = p_fold if n_ctx >= 5 else 0.8 * (1 - t) * p_fold
            # when they call a raise they keep the best part of their range
            eq_c = eq * (0.9 if eq >= 0.5 else 0.85) - 0.2 * t
            ev_called = eq_c * (pot + a - to_call) - (1 - eq_c) * a
            ev_raise = fe * pot + (1 - fe) * ev_called
            if ev_raise - max(ev_call, 0.0) > gain:
                gain, size = ev_raise - max(ev_call, 0.0), a
        if size is not None and gain > max(0.02, 0.05 - 0.02 * k) * pot:
            p = min(0.97, 0.55 + 3 * gain / pot + 0.08 * k)
            if eq < 0.4:
                pf, nc = fold_info(size / (pot + to_call))
                if nc >= 8 and pf >= 0.8:
                    p = max(p, 0.9)
                else:
                    p = min(p, min(0.6, 0.30 + 0.15 * k))
            if 0.52 < eq < 0.72 and spr > 3 and to_call > 0.4 * pot:
                p *= 0.5                       # pot control: don't bloat a pot with a
                                               # medium hand facing a big bet
            if random.random() < p:
                return ("raise", chips(size, jitter=False))

    margin = 0.03 - 0.01 * k - (0.015 * ip if not pre else 0.0)  # in position: call lighter
    if not pre and spr > 2:                    # risking a big chunk of a deep stack:
        margin += 0.08 * min(1.0, to_call / stack)   # their range is strong, be pickier
    if view["aggr"] < 0.35 and view["vpip"] < 0.25:
        margin += 0.04      # tight-passive bettor: respect them
    else:
        margin -= 0.04 * _loose_agg(view)   # maniac: call down lighter
    return ("call", 0) if eq > pot_odds + margin else ("fold", 0)


# ================================================== safety: fallback + validation
def _fast_decide(state):
    """No-simulation fallback. Used if the main logic errors or there is no time:
    a mediocre move beats a timeout or crash."""
    try:
        hole = [parse_card(c) for c in state["hole"]]
        board = [parse_card(c) for c in state.get("board", [])]
        p = preflop_pct(hole) if not board else _made_pct(hole, board)
    except Exception:
        p = 0.4
    to_call = state.get("to_call", 0)
    pot = max(state.get("pot", 1), 1)
    stack = state.get("stack", 0)
    bb = state.get("big_blind", 2)
    if to_call <= 0:
        if p > 0.78 and stack > 0:
            return ("raise", int(min(stack, max(bb, 0.5 * pot))))
        return ("check", 0)
    if p > 0.92 and stack > to_call:
        return ("raise", int(min(stack, to_call + 0.7 * (pot + to_call))))
    return ("call", 0) if p > to_call / (pot + to_call) + 0.15 else ("fold", 0)


def _sanitize(state, kind, amount):
    """Guarantee a legal action: never fold for free, never check into a bet,
    never raise below the minimum or above the stack."""
    to_call = max(0, int(state.get("to_call", 0)))
    stack = max(0, int(state.get("stack", 0)))
    if kind not in ("fold", "check", "call", "raise"):
        kind = "fold" if to_call else "check"
    if kind == "raise":
        if stack <= to_call:                       # cannot raise: call or check
            return ("call", 0) if to_call else ("check", 0)
        try:
            amt = int(round(float(amount)))
        except (TypeError, ValueError):
            amt = 0
        lo = min(stack, max(int(state.get("min_raise", 0)), to_call + 1))
        return ("raise", max(lo, min(stack, amt)))
    if kind == "fold" and to_call == 0:
        return ("check", 0)
    if kind == "check" and to_call > 0:
        return ("fold", 0)
    if kind == "call" and to_call == 0:
        return ("check", 0)
    return (kind, 0)


def _play(state):
    """Safe entry point: same contract as _decide_core, but never raises and
    always returns a legal action. Set CONFIG["safe_mode"] = False to debug."""
    if not CONFIG["safe_mode"]:
        return _decide_core(state)
    try:
        if state.get("time_budget", CONFIG["time_budget"]) <= 0:
            out = _fast_decide(state)
        else:
            out = _decide_core(state)
    except Exception:
        try:
            out = _fast_decide(state)
        except Exception:
            out = ("fold", 0)
    try:
        return _sanitize(state, *out)
    except Exception:
        return ("check", 0) if not state.get("to_call") else ("fold", 0)


# ============================================================ kit adapter
_KIT = {"synced": -1, "boards": {}}
_STREETS = {"preflop": 0, "flop": 1, "turn": 2, "river": 3}


def _replay(entries, bb, record):
    """Feed one hand's log entries to MODEL, rebuilding pot and amount-to-call
    for each action. record=False replays the live hand (hand state only)."""
    bets, pot, cur = {}, 0, None
    for e in entries:
        act, seat, amt = e.get("action"), e.get("seat"), e.get("amount") or 0
        st = e.get("street")
        if act == "show" or st == "showdown":
            continue
        if st != cur:
            bets, cur = {}, st
        if act == "ante":
            pot += amt
            continue
        if act in ("small_blind", "big_blind"):
            bets[seat] = bets.get(seat, 0) + amt
            pot += amt
            continue
        top = max(bets.values()) if bets else 0
        mine = bets.get(seat, 0)
        owed = max(0, top - mine)
        if act in ("fold", "check"):
            kind, added = act, 0
        elif act == "call":
            kind, added = "call", amt
        elif act in ("raise", "all_in"):
            added = max(0, amt - mine)
            kind = "raise" if amt > top else "call"
        else:
            continue
        MODEL.observe_action(e.get("bot_id"), _STREETS.get(st, 0), kind, to_call=owed,
                             pot=pot, amount=added, bb=bb, record=record)
        if record and act == "all_in" and st == "preflop" and kind == "raise":
            MODEL.observe_allin(e.get("bot_id"))
        pot += added
        bets[seat] = mine + added


def _sync(gs, bb):
    """Learn from hands finished since our last decision (match_action_log), then
    rebuild this hand's state from action_log."""
    hand = gs.get("hand_num", 0)
    MODEL.me = gs.get("your_bot_id")
    by_hand = {}
    for e in gs.get("match_action_log") or []:
        h = e.get("hand_num")
        if h is not None and _KIT["synced"] < h < hand:
            by_hand.setdefault(h, []).append(e)
    for h in sorted(by_hand):
        ents = by_hand[h]
        if ents[0].get("action") not in ("ante", "small_blind", "big_blind"):
            continue                       # window cut this hand in half: skip it
        hand_bb = next((x["amount"] for x in ents if x.get("action") == "big_blind"), bb)
        MODEL.observe_hand_start(sorted({x["bot_id"] for x in ents if x.get("bot_id")}))
        _replay(ents, hand_bb or bb, True)
        for x in ents:
            if x.get("action") == "show" and x.get("bot_id") != MODEL.me:
                MODEL.observe_showdown(x["bot_id"], x.get("cards") or [],
                                       _KIT["boards"].get(h, []))
    _KIT["synced"] = max(_KIT["synced"], hand - 1)
    for h in [h for h in _KIT["boards"] if h < hand - 40]:
        del _KIT["boards"][h]
    _KIT["boards"][hand] = list(gs.get("community_cards") or [])
    MODEL._reset_hand()
    _replay(gs.get("action_log") or [], bb, False)


def _to_internal(gs):
    me = gs["seat_to_act"]
    players = gs.get("players") or []
    opp = [p for p in players if p.get("seat") != me and not p.get("is_folded")
           and p.get("state") != "busted"]
    opp_all = [p for p in players if p.get("seat") != me]
    stack, bet = gs["your_stack"], gs.get("your_bet_this_street", 0)
    opp_stack = max([p.get("stack", 0) + p.get("bet_this_street", 0) for p in opp_all] or [stack])
    street = gs.get("street", "preflop")
    log = gs.get("action_log") or []
    raises = sum(1 for e in log if e.get("street") == street and e.get("action") in ("raise", "all_in"))
    pre_raises = [e for e in log if e.get("street") == "preflop" and e.get("action") in ("raise", "all_in")]
    barrels = 0                      # earlier POSTFLOP streets where we bet and they called
    for i, e in enumerate(log):
        if (e.get("street") not in ("preflop", street) and e.get("seat") == me
                and e.get("action") in ("raise", "all_in")):
            nxt = [x for x in log[i + 1:] if x.get("street") == e.get("street")]
            if nxt and nxt[0].get("seat") != me and nxt[0].get("action") == "call":
                barrels += 1
    return {
        "barrels": barrels,
        "hole": gs["your_cards"],
        "board": gs.get("community_cards") or [],
        "pot": gs["pot"],
        "to_call": min(gs.get("amount_owed", 0), stack),
        "stack": stack,
        "big_blind": gs["blinds"]["bb"],
        "opponents": max(1, len(opp)),
        "opponent_ids": [p["bot_id"] for p in opp_all],
        "min_raise": max(1, gs.get("min_raise_to", 0) - bet),
        "pf_aggressor": bool(pre_raises) and pre_raises[-1].get("seat") == me,
        "position": 1.0 if gs.get("button_seat") == me else 0.0,
        "effective_stack": max(1, min(stack, opp_stack - bet)),
        "raises": raises,
    }


def _to_kit(gs, kind, amount):
    if kind == "fold":
        return {"action": "fold"}
    if kind == "check":
        return {"action": "check"} if gs.get("can_check") else {"action": "call"}
    if kind == "call":
        return {"action": "call"} if not gs.get("can_check") else {"action": "check"}
    stack, bet = gs["your_stack"], gs.get("your_bet_this_street", 0)
    if amount >= stack:
        return {"action": "all_in"}
    return {"action": "raise", "amount": int(max(bet + amount, gs.get("min_raise_to", 0)))}


def _last_resort(gs):
    """If even the adapter fails: never fold for free, call only cheap bets."""
    if gs.get("can_check"):
        return {"action": "check"}
    if gs.get("amount_owed", 1 << 30) <= 0.25 * max(gs.get("pot", 0), 1):
        return {"action": "call"}
    return {"action": "fold"}


def decide(game_state):
    """Kit entry point. See the kit README ("What your bot sees")."""
    try:
        if game_state.get("type") == "warmup":
            _score([(14, "s"), (13, "s"), (12, "s"), (11, "s"), (10, "s"), (2, "d"), (3, "c")])
            equity([(14, "s"), (14, "h")], [], 1, budget=0.02)
            return {"action": "check"}
        try:
            _sync(game_state, game_state["blinds"]["bb"])
        except Exception:
            pass                           # tracking is a bonus, never a reason to fail
        state = _to_internal(game_state)
        kind, amount = _play(state)
        return _to_kit(game_state, kind, amount)
    except Exception:
        return _last_resort(game_state)
