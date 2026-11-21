#!/usr/bin/env python3
"""Build the precomputed tables our bot ships in data/.

  python tools/build_tables.py my_bots/our_bot/data [--sims 1500]

Writes:
  eqmat.npy     169x169 float32: preflop all-in equity of hand class i vs class j
  classes.json  the 169 class names in index order (e.g. "AA", "AKs", "AKo")
  pushfold.json heads-up Nash push/fold strategies, solved here by fictitious play:
                {"stacks": [...BB...], "ante0": {"push": [[...169...]...], "call": [...]},
                 "ante10": {...}}   (ante10 = an ante of 0.1 BB per player)

Card removal between the two hands is respected in the equity matrix but ignored
in the Nash solve (standard simplification).
"""

import json
import random
import sys
from multiprocessing import Pool
from pathlib import Path

import eval7
import numpy as np

RANKS = "AKQJT98765432"
SUITS = "shdc"


def classes():
    out = []
    for i, r1 in enumerate(RANKS):
        for j, r2 in enumerate(RANKS):
            if i == j:
                out.append(r1 + r2)
            elif i < j:
                out.append(r1 + r2 + "s")
            else:
                out.append(r2 + r1 + "o")
    # keep a stable, de-duplicated order
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res


def combos(cls):
    r1, r2 = cls[0], cls[1]
    if len(cls) == 2:
        return [(r1 + a, r2 + b) for x, a in enumerate(SUITS) for b in SUITS[x + 1:]]
    if cls[2] == "s":
        return [(r1 + s, r2 + s) for s in SUITS]
    return [(r1 + a, r2 + b) for a in SUITS for b in SUITS if a != b]


def weight(cls):
    return 6 if len(cls) == 2 else (4 if cls[2] == "s" else 12)


CLASSES = classes()
DECK = [r + s for r in RANKS for s in SUITS]
CARD = {c: eval7.Card(c) for c in DECK}


def row(args):
    i, sims, seed = args
    rng = random.Random(seed)
    ci = combos(CLASSES[i])
    out = np.zeros(len(CLASSES), dtype=np.float64)
    for j in range(i + 1, len(CLASSES)):
        cj = combos(CLASSES[j])
        pairs = [(a, b) for a in ci for b in cj if not set(a) & set(b)]
        if not pairs:
            out[j] = 0.5
            continue
        score = 0.0
        for _ in range(sims):
            a, b = rng.choice(pairs)
            used = set(a) | set(b)
            board = []
            while len(board) < 5:
                c = DECK[rng.randrange(52)]
                if c not in used:
                    used.add(c)
                    board.append(c)
            bc = [CARD[c] for c in board]
            va = eval7.evaluate([CARD[a[0]], CARD[a[1]]] + bc)
            vb = eval7.evaluate([CARD[b[0]], CARD[b[1]]] + bc)
            score += 1.0 if va > vb else (0.5 if va == vb else 0.0)
        out[j] = score / sims
    return i, out


def solve_pushfold(E, w, S, ante, iters=3000):
    """Fictitious play for SB push/fold vs BB call/fold. S = effective stack in BB."""
    n = len(w)
    p = np.ones(n)            # SB push prob
    c = np.full(n, 0.5)       # BB call prob
    risk = S + ante           # what each player loses/wins when called (antes are dead money)
    for t in range(1, iters + 1):
        # BB best response to p
        pw = w * p
        if pw.sum() > 0:
            ev_call = (pw[:, None] * (2 * (1 - E) - 1) * risk).sum(axis=0) / pw.sum()
        else:
            ev_call = np.full(n, -np.inf)
        c_br = (ev_call > -(1 + ante)).astype(float)
        # SB best response to c
        ev_push = ((w * c)[None, :] * (2 * E - 1) * risk + (w * (1 - c))[None, :] * (1 + ante)).sum(axis=1) / w.sum()
        p_br = (ev_push > -(0.5 + ante)).astype(float)
        p += (p_br - p) / (t + 1)
        c += (c_br - c) / (t + 1)
    return p, c


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "my_bots/our_bot/data")
    sims = int(sys.argv[sys.argv.index("--sims") + 1]) if "--sims" in sys.argv else 1500
    out.mkdir(parents=True, exist_ok=True)
    n = len(CLASSES)
    assert n == 169
    E = np.full((n, n), 0.5)
    with Pool(24) as pool:
        for i, r in pool.imap_unordered(row, [(i, sims, 1000 + i) for i in range(n)]):
            E[i, i + 1:] = r[i + 1:]
            print(f"row {i} done", flush=True) if i % 20 == 0 else None
    for i in range(n):
        for j in range(i):
            E[i, j] = 1 - E[j, i]
    np.save(out / "eqmat.npy", E.astype(np.float32))
    (out / "classes.json").write_text(json.dumps(CLASSES))

    w = np.array([weight(c) for c in CLASSES], dtype=float)
    stacks = [x / 2 for x in range(2, 61)]          # 1.0 .. 30.0 BB
    pf = {"stacks": stacks}
    for key, ante in (("ante0", 0.0), ("ante10", 0.1)):
        pushes, calls = [], []
        for S in stacks:
            p, c = solve_pushfold(E, w, S, ante)
            pushes.append([round(float(x), 3) for x in p])
            calls.append([round(float(x), 3) for x in c])
        pf[key] = {"push": pushes, "call": calls}
        for S in (5, 10, 15, 20):
            k = stacks.index(S)
            print(f"{key} S={S}: push {np.dot(w, np.array(pushes[k]) > 0.5) / w.sum():.0%}, "
                  f"call {np.dot(w, np.array(calls[k]) > 0.5) / w.sum():.0%}")
    (out / "pushfold.json").write_text(json.dumps(pf))
    # percentile rank of each class by equity vs a random hand (combo-weighted)
    vs_rand = (E * w[None, :]).sum(axis=1) / w.sum()
    print("AA vs random", round(float(vs_rand[CLASSES.index('AA')]), 3),
          "72o vs random", round(float(vs_rand[CLASSES.index('72o')]), 3))


if __name__ == "__main__":
    main()
