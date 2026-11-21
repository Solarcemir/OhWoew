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
    return names, probs


def pair(rng, probs, a, b):
    """Games won by (a, b) in one duplicate pair."""
    p = probs.get((a, b))
    if p is None:
        q = probs.get((b, a))
        p = (q[2], q[1], q[0]) if q else (1 / 3, 1 / 3, 1 / 3)
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
    args = p.parse_args()
    names, probs = load(args.results)
    rng = random.Random(args.seed)
    cands = [c for c in args.candidates.split(",") if c]

    stats = defaultdict(lambda: defaultdict(float))
    if args.field:
        pool = [x for x in names if x not in cands]
        for c in cands:
            for _ in range(args.sims):
                opps = [rng.choice(pool) for _ in range(args.field)]
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
