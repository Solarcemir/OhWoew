"""
Algorithmic poker bot (pure Python stdlib, no LLMs). No-Limit Hold'em assumed.

Built to beat OTHER ALGORITHMS: they tend to be consistent, so we
  * learn per-opponent, per-situation response patterns (e.g. "folds river bets
    over half pot every time") and exploit them with EV-based betting;
  * learn what their bets really mean from showdowns (nit vs. bluffer);
  * accumulate range information across streets (incomplete information:
    we never see hands, so every action narrows a range);
  * play aggressively by default, but randomize sizes, frequencies and even
    our overall aggression level over time so we can't be modeled in return.

HOW TO HOOK INTO THE STARTER REPO
  - normalize(raw)         -> map repo state to the dict documented in decide()
  - to_repo_action(k, amt) -> map (kind, amount) to the repo's action type
  - Feed the model (this is where the edge comes from):
      MODEL.me = "<our player id>"
      MODEL.observe_hand_start(ids_in_hand)
      MODEL.observe_action(player, street, kind, to_call, pot, amount, bb)
      MODEL.observe_showdown(player, hole_cards, board)
    Without these the bot still plays (bet-size reads + neutral priors) but
    cannot exploit anyone.
"""
import random
import time
from collections import Counter

# Global settings. Change these at the event, not deep in the code.
CONFIG = {
    "time_budget": 0.15,   # seconds of equity simulation per decision (state["time_budget"] overrides)
    "safe_mode": True,     # decide() never raises, never returns an illegal action
}

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
def equity(hole, board, n_opp, tight=0.0, budget=0.15, max_iter=3000):
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
            sc = evaluate(h + full)
            if opp_best is None or sc > opp_best:
                opp_best = sc
        mine = evaluate(hole + full)
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
    PRIOR = 8   # pseudo-observations so early reads aren't wild
    K_CTX = 3   # smaller prior for per-situation stats -> deterministic bots
                # are recognized after a handful of samples

    def __init__(self):
        self.hands = self.vpip_n = self.bets = self.calls = 0
        self.faced = self.folded = 0
        self.vs_me = {}   # (street, raise depth, our bet bucket) -> [folds, times faced]
        self.shown = {}   # their bet bucket -> [sum hand strength, n] at showdown

    def add(self, o):
        for k in ("hands", "vpip_n", "bets", "calls", "faced", "folded"):
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
                       amount=None, bb=2):
        """kind in {'fold','check','call','raise'}; street 0 = preflop.
        to_call: chips the actor had to call. pot: pot BEFORE their action
        (including the bet they faced). amount: chips they added."""
        if street != self.cur_street:
            self.cur_street, self.n_raises = street, 0
        if kind == "raise":
            self.last_raiser = player
            self.last_depth = min(self.n_raises, 2)
            self.n_raises += 1
            if amount is not None and pot:
                self.last_frac = amount / max(pot, 1)
                self.last_bet[player] = self.last_frac
        if player == self.me:
            return
        profs = (self._p(player), self.table)
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
    def observe_showdown(self, player, hole, board):
        """Call when an opponent's cards are revealed. Teaches us what their
        BETS mean (only counted if they bet this hand)."""
        if player == self.me or player not in self.last_bet:
            return
        hole = [parse_card(c) for c in hole]
        board = [parse_card(c) for c in board]
        pct = _made_pct(hole, board) if board else preflop_pct(hole)
        key = _bucket(self.last_bet[player])
        for p in (self._p(player), self.table):
            rec = p.shown.setdefault(key, [0.0, 0])
            rec[0] += pct
            rec[1] += 1

    # ---- reading the model -------------------------------------------
    def view(self, ids=None):
        prof = self._combined(ids)
        return {"fold": prof.fold_to_bet(), "aggr": prof.aggression(),
                "vpip": prof.vpip()}

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

    def hand_tight(self, ids):
        vals = [self.hand_t.get(i, 0.0) for i in ids] if ids else list(self.hand_t.values())
        return max(vals) if vals else 0.0


MODEL = OpponentModel()


# ============================================================ reading bets
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
    if a > 0.6 and v > 0.35:        # loose-aggressive: bets mean less
        t *= 0.65
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

    # --- what do their actions say about their hand? (range narrowing) ---
    t = _tightness(to_call, pot, bb, pre, view)
    if to_call > 0:
        lt, w = MODEL.learned_tight(ids, _bucket(to_call / max(pot - to_call, 1)))
        if lt is not None:                     # showdown evidence beats heuristics
            t = (1 - w) * t + w * lt
    if not (pre and to_call <= bb):
        hist = MODEL.hand_tight(ids)           # earlier streets still count
        t = 1 - (1 - t) * (1 - (0.6 if to_call > 0 else 0.3) * hist)
    t = min(0.92, t)
    if n_opp > 1:
        t *= 0.7
    eq = equity(hole, board, n_opp, tight=t, budget=state.get("time_budget", CONFIG["time_budget"]))
    eq = min(1.0, max(0.0, eq + random.gauss(0, 0.02)))   # decision noise

    def fold_info(frac):
        base = view["fold"] * (0.75 + 0.5 * min(frac, 1.5))
        p, n = MODEL.ctx_fold(ids, sidx, depth, _bucket(frac), base)
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

    # ---- short stack: push / fold ----
    if stack <= 10 * bb:
        need = 0.50 + 0.04 * (n_opp - 1) - 0.08 * (view["fold"] - 0.4) - 0.03 * k
        pot_odds = to_call / (pot + to_call) if to_call else 0.0
        if to_call == 0:
            return ("raise", int(stack)) if (eq > need and can_raise) else ("check", 0)
        if eq > max(pot_odds + 0.05, need - 0.05):
            return ("raise", int(stack)) if (eq > need + 0.1 and can_raise) else ("call", 0)
        return ("fold", 0)

    # ---- preflop, unopened / limped: open-raise by hand strength ----
    if pre and to_call <= bb:
        pct = preflop_pct(hole)
        th = min(0.8, max(0.40, 0.65 - 0.3 * (view["fold"] - 0.4))) - 0.06 * k \
            + 0.04 * (n_opp - 1) - 0.08 * ip        # late seat opens wider
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
    elif view["aggr"] > 0.6 and view["vpip"] > 0.35:
        margin -= 0.02      # maniac: call down lighter
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


def decide(state):
    """Public entry point: same contract as _decide_core, but never raises and
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


# ======================================================= repo integration
def normalize(raw):
    """TODO: map the starter repo's state object to the dict decide() expects."""
    raise NotImplementedError("Fill this in using the repo's state class")


def to_repo_action(kind, amount):
    """TODO: convert (kind, amount) into the repo's action type."""
    raise NotImplementedError("Fill this in using the repo's action class")


def decide_for_repo(raw):
    kind, amount = decide(normalize(raw))
    return to_repo_action(kind, amount)


# ============================================================== self-test
if __name__ == "__main__":
    from collections import Counter
    fast = {"time_budget": 0.02}

    assert evaluate([(14,'s'),(13,'s'),(12,'s'),(11,'s'),(10,'s'),(2,'d'),(3,'c')])[0] == 8
    assert evaluate([(14,'s'),(2,'d'),(3,'c'),(4,'h'),(5,'s'),(9,'d'),(9,'c')])[:2] == (4, 5)
    assert preflop_pct([(14,'s'),(14,'h')]) > 0.99 > preflop_pct([(7,'c'),(2,'d')])
    print("evaluator + preflop table OK")

    def freq(state, n=80):
        c = Counter(decide({**state, **fast})[0] for _ in range(n))
        return {k: round(v / n, 2) for k, v in sorted(c.items())}

    MODEL.me = "me"

    # --- 1. pattern learning: 'A' calls flop bets but folds every river bet ---
    for _ in range(25):
        MODEL.observe_hand_start(["me", "A"])
        MODEL.observe_action("me", 1, "raise", to_call=0, pot=40, amount=25)
        MODEL.observe_action("A", 1, "call", to_call=25, pot=65)
        MODEL.observe_action("me", 3, "raise", to_call=0, pot=100, amount=65)
        MODEL.observe_action("A", 3, "fold", to_call=65, pot=165)
    flop_air = {"hole": ["7c", "2d"], "board": ["Ks", "Qh", "9d"], "pot": 40,
                "to_call": 0, "stack": 300, "big_blind": 2, "opponents": 1,
                "opponent_ids": ["A"]}
    river_air = {**flop_air, "board": ["Ks", "Qh", "9d", "4c", "3s"], "pot": 100}
    print("\n72o, checked to us, opp 'A' (calls flop, folds river):")
    print("  flop :", freq(flop_air))
    print("  river:", freq(river_air))

    # --- 2. showdown learning: nit's bets are strong, maniac's are air ---
    for _ in range(15):
        for who, cards in (("nit", ["As", "Ah"]), ("maniac", ["9h", "4d"])):
            MODEL.observe_hand_start(["me", who])
            MODEL.observe_action(who, 1, "raise", to_call=0, pot=40, amount=30)
            MODEL.observe_showdown(who, cards, ["Ks", "7c", "2d"])
    MODEL.observe_hand_start(["me", "nit", "maniac"])   # fresh hand: clear per-hand reads
    face = {"hole": ["Ac", "Qd"], "board": ["Ks", "7c", "2d"], "pot": 100,
            "to_call": 50, "stack": 300, "big_blind": 2, "opponents": 1}
    print("\nAce-high facing a 1/2-pot bet:")
    print("  from 'nit'   :", freq({**face, "opponent_ids": ["nit"]}))
    print("  from 'maniac':", freq({**face, "opponent_ids": ["maniac"]}))

    # --- 3. preflop aggression ---
    print("\nAA preflop:", freq({"hole": ["As", "Ah"], "board": [], "pot": 3,
          "to_call": 2, "stack": 200, "big_blind": 2, "opponents": 2}))
    print("K9o unopened:", freq({"hole": ["Kc", "9d"], "board": [], "pot": 3,
          "to_call": 2, "stack": 200, "big_blind": 2, "opponents": 1}))

    # --- 4. robustness fuzz + timing ---
    import itertools
    deck = [f"{r}{s}" for r in "23456789TJQKA" for s in "shdc"]
    t0 = time.perf_counter()
    for _ in range(300):
        cards = random.sample(deck, 7)
        nb = random.choice([0, 3, 4, 5])
        pot = random.randint(3, 400)
        st = {"hole": cards[:2], "board": cards[2:2 + nb], "pot": pot,
              "to_call": random.choice([0, 0, 2, pot // 3, pot // 2, pot]),
              "stack": random.choice([15, 60, 200, 1000]),
              "big_blind": 2, "opponents": random.randint(1, 5), **fast}
        assert decide(st)[0] in ("fold", "check", "call", "raise")
    # hostile states: zero stacks, bad numbers, junk keys must never crash or go illegal
    odd = []
    for _ in range(400):
        cards = random.sample(deck, 7)
        odd.append({"hole": cards[:2], "board": cards[2:2 + random.choice([0, 3, 4, 5])],
                    "pot": random.choice([0, 1, 3, 500]),
                    "to_call": random.choice([0, 1, 50, 10 ** 6]),
                    "stack": random.choice([0, 1, 5, 40, 10 ** 5]),
                    "big_blind": random.choice([1, 2, 100]),
                    "opponents": random.choice([0, 1, 8]),
                    "min_raise": random.choice([0, 1, 10 ** 7]),
                    "time_budget": random.choice([0, 0.001, 0.02])})
    odd += [{"hole": ["As", "Ah"]}, {}, {"hole": ["zz", "??"], "pot": 5, "to_call": 1, "stack": 9}]
    for st in odd:
        kind, amt = decide(st)
        tc, stk = max(0, st.get("to_call", 0)), max(0, st.get("stack", 0))
        assert kind in ("fold", "check", "call", "raise"), (st, kind)
        assert not (kind == "check" and tc > 0), (st, kind)
        assert not (kind == "fold" and tc == 0), (st, kind)
        assert not (kind == "call" and tc == 0), (st, kind)
        if kind == "raise":
            assert 0 < amt <= stk and stk > tc, (st, kind, amt)
    print("hostile-input fuzz OK: %d states, always a legal action" % len(odd))
    print("\nfuzz OK: 300 random states, %.1f ms/decision at 0.02s budget"
          % ((time.perf_counter() - t0) / 300 * 1000))
