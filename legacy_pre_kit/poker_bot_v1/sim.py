"""
Test harness for poker_bot.py. Put it next to poker_bot.py and run:

  python sim.py cash --opp all --hands 400          # heads-up vs each archetype
  python sim.py cash --opp tag+maniac+callstation   # one 4-handed table
  python sim.py tourney --opp tag+nit+maniac+callstation+random --n 30

It is a self-contained No-Limit Hold'em engine (side pots, min-raise rules),
so it works before you have the hackathon repo. Once you have the repo, the
useful parts are the opponent archetypes and the stats, and the way we feed
MODEL.observe_* exactly as the real event must.

Cash mode resets stacks every hand and (heads-up) plays every deal twice with
seats swapped ("duplicate"), which removes most card luck, so a few hundred
hands give a usable read of edge in bb/100.
"""
import argparse
import math
import random
import sys
import time

import poker_bot as pb
from poker_bot import FULL_DECK, evaluate, preflop_pct, _made_pct

STREET_NAMES = {0: "preflop", 1: "flop", 2: "turn", 3: "river"}


# ================================================================== engine
class Player:
    def __init__(self, name, fn, stack):
        self.name, self.fn, self.stack = name, fn, stack
        self.hole, self.folded, self.all_in = [], False, False
        self.bet = self.total = 0


class Hand:
    def __init__(self, players, button, bb, rng, budget=0.01):
        self.P, self.btn, self.bb, self.rng, self.budget = players, button, bb, rng, budget
        self.board, self.pf_raiser = [], None

    def post(self, p, amt):
        put = min(int(amt), p.stack)
        p.stack -= put
        p.bet += put
        p.total += put
        if p.stack == 0:
            p.all_in = True
        return put

    def pot(self):
        return sum(q.total for q in self.P)

    def state(self, p, street, to_call, last_raise, n_raises=0):
        others = [q for q in self.P if q is not p and not q.folded]
        return {
            "hole": list(p.hole), "board": list(self.board), "pot": self.pot(),
            "to_call": min(to_call, p.stack), "stack": p.stack, "big_blind": self.bb,
            "opponents": len(others), "opponent_ids": [q.name for q in others],
            "min_raise": min(p.stack, to_call + max(last_raise, self.bb)),
            "pf_aggressor": self.pf_raiser == p.name, "time_budget": self.budget,
            "raises": n_raises,
            "effective_stack": min(p.stack, max([q.stack + q.bet for q in others] + [0]) - 0) if others else p.stack,
            "position": ((self.P.index(p) - self.btn - 1) % len(self.P)) / max(len(self.P) - 1, 1),
        }

    def play(self):
        P, n, bb = self.P, len(self.P), self.bb
        total_before = sum(p.stack for p in P)
        for p in P:
            p.folded = p.all_in = False
            p.bet = p.total = 0
        deck = FULL_DECK[:]
        self.rng.shuffle(deck)
        for i, p in enumerate(P):
            p.hole = [deck[2 * i], deck[2 * i + 1]]
        runout = deck[2 * n:2 * n + 5]
        sb_i, bb_i = (self.btn, (self.btn + 1) % n) if n == 2 else \
                     ((self.btn + 1) % n, (self.btn + 2) % n)
        self.post(P[sb_i], bb // 2)
        self.post(P[bb_i], bb)
        pb.MODEL.observe_hand_start([p.name for p in P])

        self.round(0, self.btn if n == 2 else (bb_i + 1) % n, bb)
        for street, upto in ((1, 3), (2, 4), (3, 5)):
            if sum(not p.folded for p in P) <= 1:
                break
            self.board = runout[:upto]
            for p in P:
                p.bet = 0
            self.round(street, (self.btn + 1) % n, 0)
        self.board = runout if sum(not p.folded for p in P) > 1 else self.board
        self.award()
        assert sum(p.stack for p in P) == total_before, "chips not conserved"

    def round(self, street, start, current):
        P, n = self.P, len(self.P)
        order = [P[(start + i) % n] for i in range(n)]
        can = [p for p in order if not p.folded and not p.all_in]
        if len(can) == 0 or (len(can) == 1 and can[0].bet >= current):
            return
        need = {id(p) for p in can}
        last_raise, idx, n_raises = self.bb, 0, 0
        while need:
            p = order[idx % n]
            idx += 1
            if id(p) not in need:
                continue
            to_call = current - p.bet
            st = self.state(p, street, to_call, last_raise, n_raises)
            kind, amt = p.fn(st)
            pot_before = self.pot()
            capped_call = min(to_call, p.stack)

            if kind == "check" and to_call > 0:
                kind = "fold"
            if kind == "fold" and to_call == 0:
                kind = "check"
            if kind == "call" and to_call == 0:
                kind = "check"
            added = 0
            if kind == "raise":
                new_bet = p.bet + min(int(amt), p.stack)
                if new_bet <= current:
                    kind = "call" if to_call > 0 else "check"
                else:
                    new_bet = max(new_bet, min(current + last_raise, p.bet + p.stack))
                    added = self.post(p, new_bet - p.bet)
                    last_raise = max(last_raise, new_bet - current)
                    current = new_bet
                    n_raises += 1
                    need = {id(q) for q in P if not q.folded and not q.all_in}
                    if street == 0:
                        self.pf_raiser = p.name
            if kind == "call":
                added = self.post(p, capped_call)
            elif kind == "fold":
                p.folded = True
            need.discard(id(p))
            pb.MODEL.observe_action(p.name, street, kind, to_call=capped_call,
                                    pot=pot_before, amount=added, bb=self.bb)
            if sum(not q.folded for q in P) == 1:
                return

    def award(self):
        P = self.P
        live = [p for p in P if not p.folded]
        if len(live) == 1:
            live[0].stack += self.pot()
            return
        scores = {id(p): evaluate(p.hole + self.board) for p in live}
        for p in live:
            pb.MODEL.observe_showdown(p.name, p.hole, self.board)
        prev = 0
        for lv in sorted({p.total for p in P if p.total > 0}):
            part = sum(min(p.total, lv) - min(p.total, prev) for p in P)
            elig = [p for p in live if p.total >= lv] or live
            best = max(scores[id(p)] for p in elig)
            winners = [p for p in elig if scores[id(p)] == best]
            share, extra = divmod(part, len(winners))
            for j, w in enumerate(winners):
                w.stack += share + (1 if j < extra else 0)
            prev = lv


# ======================================================= opponent archetypes
def _pct(st):
    return preflop_pct(st["hole"]) if not st["board"] else _made_pct(st["hole"], st["board"])


def _raise(st, frac):
    return ("raise", int(st["to_call"] + frac * (st["pot"] + st["to_call"])))


def callstation(st):
    """Never folds, never raises."""
    return ("check", 0) if st["to_call"] == 0 else ("call", 0)


def nit(st):
    """Plays only premium hands, bets big when strong, folds otherwise."""
    p = _pct(st)
    if st["to_call"] == 0:
        return _raise(st, 0.75) if p >= 0.80 else ("check", 0)
    if p >= 0.88:
        return _raise(st, 1.0)
    return ("call", 0) if p >= 0.80 else ("fold", 0)


def maniac(st):
    """Raises ~80% of the time regardless of cards."""
    if random.random() < 0.8:
        return _raise(st, random.uniform(0.7, 1.5))
    return ("check", 0) if st["to_call"] == 0 else ("call", 0)


def allin(st):
    """Shoves every single hand."""
    return ("raise", st["stack"])


def rand(st):
    r = random.random()
    if st["to_call"] > 0 and r < 0.25:
        return ("fold", 0)
    if r < 0.8:
        return ("check", 0) if st["to_call"] == 0 else ("call", 0)
    return _raise(st, random.uniform(0.3, 1.5))


def tag(st):
    """Tight-aggressive, deterministic threshold bot (typical 'decent' repo bot)."""
    p, pre = _pct(st), not st["board"]
    if st["to_call"] == 0:
        if pre:
            return ("raise", 3 * st["big_blind"]) if p >= 0.62 else ("check", 0)
        return _raise(st, 0.6) if p >= 0.65 else ("check", 0)
    odds = st["to_call"] / (st["pot"] + st["to_call"])
    if p >= 0.85:
        return _raise(st, 0.8)
    return ("call", 0) if p >= max(0.45, odds + 0.2) else ("fold", 0)


def weaktight(st):
    """Small c-bets, calls small bets, folds to big ones. Easy to bully."""
    p, pre = _pct(st), not st["board"]
    if st["to_call"] == 0:
        if pre:
            return ("raise", 3 * st["big_blind"]) if p >= 0.55 else ("check", 0)
        return _raise(st, 0.4) if p >= 0.45 else ("check", 0)
    frac = st["to_call"] / max(st["pot"] - st["to_call"], 1)
    if frac > 0.6 and p < 0.75:
        return ("fold", 0)
    return ("call", 0) if (p >= 0.35 or frac < 0.3) else ("fold", 0)


OPPONENTS = {"callstation": callstation, "nit": nit, "maniac": maniac,
             "allin": allin, "random": rand, "tag": tag, "weaktight": weaktight}


# ===================================================================== hero
def make_hero():
    def hero(st):
        return pb.decide(st)
    return hero


def reset_model():
    pb.MODEL.__init__()
    pb.MODEL.me = "hero"
    pb._reroll_style()


def opp_players(names, stack):
    seen, out = {}, []
    for nm in names:
        seen[nm] = seen.get(nm, 0) + 1
        label = nm if names.count(nm) == 1 else f"{nm}{seen[nm]}"
        out.append(Player(label, OPPONENTS[nm], stack))
    return out


# ============================================================== cash mode
def play_cash(opp_names, hands, bb=2, stack_bb=100, budget=0.01, seed=0):
    reset_model()
    rng = random.Random(seed)
    stack = bb * stack_bb
    hero_fn = make_hero()
    samples = []
    for h in range(hands):
        if len(opp_names) == 1:                       # duplicate heads-up
            deck_seed, tot = rng.random(), 0
            for swap in (0, 1):
                hero = Player("hero", hero_fn, stack)
                opp = opp_players(opp_names, stack)[0]
                P = [hero, opp] if swap == 0 else [opp, hero]
                Hand(P, 0, bb, random.Random(deck_seed), budget).play()
                tot += hero.stack - stack
            samples.append(tot / 2 / bb)
        else:
            hero = Player("hero", hero_fn, stack)
            P = [hero] + opp_players(opp_names, stack)
            rng.shuffle(P)
            Hand(P, h % len(P), bb, random.Random(rng.random()), budget).play()
            samples.append((hero.stack - stack) / bb)
    return samples


def summarize_cash(samples):
    n = len(samples)
    mean = sum(samples) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in samples) / max(n - 1, 1))
    return mean * 100, 1.96 * sd / math.sqrt(n) * 100


# ========================================================== tournament mode
def play_tourney(opp_names, n_tourneys, start=1500, level_hands=15,
                 budget=0.01, seed=0, max_hands=1500):
    rng = random.Random(seed)
    hero_fn, results = make_hero(), []
    for _ in range(n_tourneys):
        reset_model()
        players = [Player("hero", hero_fn, start)] + opp_players(opp_names, start)
        rng.shuffle(players)
        alive, places, hand_no = players[:], {}, 0
        size = len(players)
        while len(alive) > 1 and hand_no < max_hands:
            bb = max(2, int(20 * 1.5 ** (hand_no // level_hands)) // 2 * 2)
            Hand(alive, hand_no % len(alive), bb, random.Random(rng.random()), budget).play()
            out = [p for p in alive if p.stack == 0]
            for p in out:
                places[p.name] = len(alive)
            alive = [p for p in alive if p.stack > 0]
            hand_no += 1
        for rank, p in enumerate(sorted(alive, key=lambda q: -q.stack), start=1):
            places[p.name] = rank
        results.append((places["hero"], hand_no))
    return results, size


# ====================================================================== CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["cash", "tourney"])
    ap.add_argument("--opp", default="all",
                    help="'all' | comma list (separate heads-up runs) | a+b+c (one table)")
    ap.add_argument("--hands", type=int, default=300)
    ap.add_argument("--n", type=int, default=20, help="tournaments to play")
    ap.add_argument("--budget", type=float, default=0.01, help="bot equity time per decision")
    ap.add_argument("--stack", type=int, default=100, help="cash stack in bb")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    if a.opp == "all":
        groups = [[k] for k in OPPONENTS]
    elif "+" in a.opp:
        groups = [a.opp.split("+")]
    else:
        groups = [[k] for k in a.opp.split(",")]

    t0 = time.time()
    if a.mode == "cash":
        print(f"CASH  {a.hands} hands, {a.stack}bb stacks, budget {a.budget}s\n")
        print(f"{'table':<38}{'bb/100':>9}{'  ±95%':>9}")
        for g in groups:
            mean, ci = summarize_cash(play_cash(g, a.hands, stack_bb=a.stack,
                                                budget=a.budget, seed=a.seed))
            flag = "" if abs(mean) > ci else "  (not significant)"
            print(f"hero vs {'+'.join(g):<30}{mean:>9.1f}{ci:>9.1f}{flag}")
    else:
        for g in groups:
            res, size = play_tourney(g, a.n, budget=a.budget, seed=a.seed)
            wins = sum(1 for pl, _ in res if pl == 1)
            avg = sum(pl for pl, _ in res) / len(res)
            hands = sum(h for _, h in res) / len(res)
            print(f"TOURNEY {size} players: hero vs {'+'.join(g)}")
            print(f"  wins {wins}/{len(res)} ({100 * wins / len(res):.0f}%; "
                  f"chance baseline {100 / size:.0f}%), avg place {avg:.2f} "
                  f"(baseline {(size + 1) / 2:.1f}), avg {hands:.0f} hands")
    print(f"\n({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
