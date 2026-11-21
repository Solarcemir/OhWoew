#!/usr/bin/env python3
"""Fast Monte Carlo of the event from measured pairing outcomes.

Step 1 (slow, once): tools/arena.py ... --pairs 8 --json results.json
          records, for every pairing, how many duplicate pairs ended 2-0, 1-1, 0-2.
Step 2 (fast, this script): simulate thousands of tournaments by drawing each
          qualifier pairing's result (2-0 / 1-1 / 0-2) from those frequencies,
          then the top-4 knockout (1v4, 2v3, final; a 1-1 is replayed up to 3 times,
          then a coin flip).

  python tools/tourney_mc.py results.json --sims 20000
  python tools/tourney_mc.py results.json --sims 20000 --field 33 --candidates our_bot,our_v4

--field N: instead of the measured bots as they are, each simulated event has the
candidate plus N opponents drawn with replacement from the non-candidate bots
(a rough stand-in for an unknown field of ~34 teams). Each candidate is simulated
in its own events (only one of our versions is entered).
Laplace smoothing (+0.5 per outcome) keeps unseen outcomes possible.
"""

import argparse
import json
import random
from collections import defaultdict


def load(paths):
    """Merge one or more arena --json files (counts for the same pairing are added)."""
    names, counts = [], defaultdict(lambda: [0, 0, 0])
    for path in paths:
        d = json.load(open(path))
        for x in d["names"]:
            if x not in names:
                names.append(x)
        for key, v in d["wdl"].items():
            a, b = key.split("|")
            for i in range(3):
                counts[(a, b)][i] += v[i]
    probs = {}
    for (a, b), (w, dr, l) in counts.items():
        tot = w + dr + l + 1.5
        probs[(a, b)] = ((w + 0.5) / tot, (dr + 0.5) / tot, (l + 0.5) / tot)
    fit_ratings(dict(counts))
    return names, probs


RATINGS = {}     # filled by fit_ratings(): Bradley-Terry strength per bot
RHO = 0.4        # share of duplicate pairs decided by the cards (always 1-1), fitted from data


def fit_ratings(counts, iters=300):
    """Bradley-Terry strengths from game counts (pair win = 2 games, draw = 1 each), and
    RHO, the card-decided share, so unmeasured pairings can be predicted."""
    global RHO
    wins = defaultdict(float)
    games = defaultdict(float)
    bots = set()
    for (a, b), (w, d, l) in counts.items():
        wins[(a, b)] += 2 * w + d
        games[(a, b)] += 2 * (w + d + l)
        bots |= {a, b}
    r = {x: 1.0 for x in bots}
    for _ in range(iters):
        new = {}
        for x in bots:
            num = sum(v for (a, b), v in wins.items() if a == x) + 0.5
            den = sum(g / (r[x] + r[b]) for (a, b), g in games.items() if a == x) + 1.0 / (r[x] + 1.0)
            new[x] = num / den
        s = sum(new.values()) / len(new)
        r = {x: v / s for x, v in new.items()}
    # draws beyond what independent games would give = card-decided pairs
    excess, n = 0.0, 0
    for (a, b), (w, d, l) in counts.items():
        tot = w + d + l
        if tot >= 4 and a < b:
            q = (2 * w + d) / (2 * tot)
            indep = 2 * q * (1 - q)
            if indep < 0.95:
                excess += max(0.0, (d / tot - indep) / (1 - indep))
                n += 1
    RHO = min(0.8, max(0.0, excess / n)) if n else 0.4
    RATINGS.clear()
    RATINGS.update(r)


def predicted(a, b):
    """(P 2-0, P 1-1, P 0-2) for an unmeasured pairing, from ratings and RHO."""
    if a not in RATINGS or b not in RATINGS:
        return (1 / 3, 1 / 3, 1 / 3)
    q = RATINGS[a] / (RATINGS[a] + RATINGS[b])
    qq = min(1.0, max(0.0, (q - 0.5 * RHO) / (1 - RHO)))
    w, d, l = qq * qq, 2 * qq * (1 - qq), (1 - qq) * (1 - qq)
    return ((1 - RHO) * w, RHO + (1 - RHO) * d, (1 - RHO) * l)


def pair(rng, probs, a, b):
    """Games won by (a, b) in one duplicate pair."""
    p = probs.get((a, b))
    if p is None:
        q = probs.get((b, a))
        p = (q[2], q[1], q[0]) if q else predicted(a, b)
    x = rng.random()
    if x < p[0]:
        return 2, 0
    if x < p[0] + p[1]:
        return 1, 1
    return 0, 2


def ko(rng, probs, a, b):
    for _ in range(3):
        ga, gb = pair(rng, probs, a, b)
        if ga != gb:
            return a if ga > gb else b
    return rng.choice([a, b])


def event(rng, probs, entrants):
    """entrants: list of (label, bot name). Returns ranked labels, top4, champion label."""
    score = defaultdict(int)
    n = len(entrants)
    for i in range(n):
        for j in range(i + 1, n):
            (li, bi), (lj, bj) = entrants[i], entrants[j]
            if bi == bj:                      # two copies of the same bot: a coin per game
                gi = rng.randint(0, 2)
                gj = 2 - gi
            else:
                gi, gj = pair(rng, probs, bi, bj)
            score[li] += gi
            score[lj] += gj
    order = sorted((l for l, _ in entrants), key=lambda l: (-score[l], rng.random()))
    bot = dict(entrants)
    t = order[:4]
    s1 = ko(rng, probs, bot[t[0]], bot[t[3]])
    s2 = ko(rng, probs, bot[t[1]], bot[t[2]])
    lab = {bot[x]: x for x in t}              # map winners back to labels (unique within top 4 mostly)
    champ = ko(rng, probs, s1, s2)
    return order, score, t, (lab.get(s1), lab.get(s2)), lab.get(champ)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results", nargs="+", help="one or more arena --json files")
    p.add_argument("--sims", type=int, default=20000)
    p.add_argument("--field", type=int, default=0, help="opponents drawn per event (0 = the measured bots as-is)")
    p.add_argument("--candidates", default="", help="our versions, comma-separated (each simulated separately with --field)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--real-prefix", default="x_", help="bots whose name starts with this form a separate pool (for --real-frac)")
    p.add_argument("--real-frac", type=float, default=None,
                   help="with --field: this share of the opponents is drawn from that pool, the rest from the others")
    p.add_argument("--ratings", action="store_true", help="print the fitted strength of every bot")
    args = p.parse_args()
    names, probs = load(args.results)
    rng = random.Random(args.seed)
    cands = [c for c in args.candidates.split(",") if c]

    stats = defaultdict(lambda: defaultdict(float))
    if args.ratings:
        print(f"card-decided share of duplicate pairs (RHO): {RHO:.2f}")
        for x in sorted(RATINGS, key=lambda x: -RATINGS[x]):
            print(f"  {x:<16}{RATINGS[x]:7.2f}")
        print()
    if args.field:
        pool = [x for x in names if x not in cands]
        real = [x for x in pool if x.startswith(args.real_prefix)]
        other = [x for x in pool if not x.startswith(args.real_prefix)]
        n_real = None
        if args.real_frac is not None and real and other:
            n_real = round(args.real_frac * args.field)
            print(f"mixed field: {n_real} real ({len(real)} distinct) + {args.field - n_real} others ({len(other)} distinct)")
        for c in cands:
            for _ in range(args.sims):
                if n_real is None:
                    opps = [rng.choice(pool) for _ in range(args.field)]
                else:
                    opps = [rng.choice(real) for _ in range(n_real)] + \
                           [rng.choice(other) for _ in range(args.field - n_real)]
                entrants = [("ME", c)] + [(f"o{k}_{o}", o) for k, o in enumerate(opps)]
                order, score, t, fin, champ = event(rng, probs, entrants)
                s = stats[c]
                s["pts"] += score["ME"]
                s["rank"] += order.index("ME") + 1
                s["top4"] += "ME" in t
                s["final"] += "ME" in fin
                s["champ"] += champ == "ME"
        n_opp = args.field
        print(f"{args.sims} simulated events per candidate; field = candidate + {n_opp} opponents drawn "
              f"from {len(pool)} measured bots; max qualifier points {2 * n_opp}\n")
        rows = cands
    else:
        entrants = [(x, x) for x in names]
        for _ in range(args.sims):
            order, score, t, fin, champ = event(rng, probs, entrants)
            for x in names:
                s = stats[x]
                s["pts"] += score[x]
                s["rank"] += order.index(x) + 1
                s["top4"] += x in t
                s["final"] += x in fin
                s["champ"] += champ == x
        print(f"{args.sims} simulated events with the {len(names)} measured bots; "
              f"max qualifier points {2 * (len(names) - 1)}\n")
        rows = sorted(names, key=lambda x: -stats[x]["top4"])
    S = args.sims
    print(f"{'bot':<14}{'mean pts':>9}{'mean rank':>10}{'P(top4)':>9}{'P(final)':>9}{'P(champ)':>9}")
    for x in rows:
        s = stats[x]
        print(f"{x:<14}{s['pts'] / S:>9.1f}{s['rank'] / S:>10.1f}{s['top4'] / S:>9.1%}"
              f"{s['final'] / S:>9.1%}{s['champ'] / S:>9.1%}")


if __name__ == "__main__":
    main()
