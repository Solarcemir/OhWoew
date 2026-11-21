#!/usr/bin/env python3
"""Monte Carlo of the whole event, in its announced format, repeated many times.

Qualifier: every bot plays every other bot once, as one duplicate pair (2 games,
same seed, seats swapped). Points = games won. The top 4 go to the final.
Final: knockout, 1st vs 4th and 2nd vs 3rd, then the final. Each round is a
duplicate pair; on a 1-1 another pair is played (up to 3), then a coin flip.
Ties in the qualifier table are broken at random (the official tiebreak is unknown).

  python tools/tournament.py my_bots/our_bot sparring/* --reps 10 --workers 20

Reports, per bot: mean qualifier points (out of 2*(N-1)), mean rank, P(top 4),
P(reach final) and P(champion), with the number of tournaments simulated.
"""

import argparse
import os
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402  (also sets BOT_ENV_ALLOW on Windows and line-buffers stdout)
from pokercore.seeds import derive_seed  # noqa: E402


def pair_job(a, b, seed):
    """One duplicate pair; returns (a_name, b_name, games won by a, games won by b)."""
    na, nb, _, r1, r2, _ = arena.play_pair(a, b, seed, 1000, 2000)
    w1, w2 = arena.winner(r1), arena.winner(r2)
    return na, nb, (w1 == na) + (w2 == na), (w1 == nb) + (w2 == nb)


def run_pairs(ex, jobs):
    """jobs: list of (key, a, b, seed). Returns {key: (ga, gb)}."""
    futs = {ex.submit(pair_job, a, b, s): k for k, a, b, s in jobs}
    out = {}
    for f, k in futs.items():
        try:
            _, _, ga, gb = f.result()
        except Exception as e:  # noqa: BLE001
            print(f"  pair failed: {e!r}")
            ga = gb = 1
        out[k] = (ga, gb)
    return out


def knockout(ex, reps_pairs, seed_root, stage):
    """reps_pairs: {rep: (botA, botB)}. Plays duplicate pairs until decided (max 3)."""
    winners = {}
    pending = dict(reps_pairs)
    for attempt in range(3):
        if not pending:
            break
        jobs = [(r, a, b, derive_seed(seed_root, r, stage, attempt)) for r, (a, b) in pending.items()]
        res = run_pairs(ex, jobs)
        nxt = {}
        for r, (ga, gb) in res.items():
            a, b = pending[r]
            if ga > gb:
                winners[r] = a
            elif gb > ga:
                winners[r] = b
            else:
                nxt[r] = (a, b)
        pending = nxt
    for r, (a, b) in pending.items():
        winners[r] = random.Random(derive_seed(seed_root, r, stage, "coin")).choice([a, b])
    return winners


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("bots", nargs="+")
    p.add_argument("--reps", type=int, default=10, help="tournaments to simulate")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    p.add_argument("--seed", type=int, default=2026)
    args = p.parse_args()

    bots = []
    for path in args.bots:
        bp = Path(path)
        if (bp / "bot.py").exists():
            bots.append((bp.resolve().name, str(bp)))
    names = [n for n, _ in bots]
    assert len(set(names)) == len(names), "two bots share a folder name"
    by_name = dict(bots)
    n = len(bots)
    print(f"{n} bots, {args.reps} tournaments: {n * (n - 1) // 2} qualifier pairs each "
          f"({n * (n - 1)} games), on {args.workers} workers")

    pts = defaultdict(list)
    ranks = defaultdict(list)
    top4 = defaultdict(int)
    finals = defaultdict(int)
    champs = defaultdict(int)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        # qualifier for every tournament at once
        jobs = []
        for r in range(args.reps):
            for i in range(n):
                for j in range(i + 1, n):
                    jobs.append(((r, i, j), bots[i], bots[j], derive_seed(args.seed, r, "q", names[i], names[j])))
        print(f"qualifier: {len(jobs)} pairs ...")
        res = run_pairs(ex, jobs)
        print(f"  done in {time.time() - t0:.0f}s")
        seeds_top = {}
        for r in range(args.reps):
            score = defaultdict(int)
            for (rr, i, j), (ga, gb) in res.items():
                if rr == r:
                    score[names[i]] += ga
                    score[names[j]] += gb
            rng = random.Random(derive_seed(args.seed, r, "tiebreak"))
            order = sorted(names, key=lambda x: (-score[x], rng.random()))
            for k, x in enumerate(order):
                pts[x].append(score[x])
                ranks[x].append(k + 1)
            seeds_top[r] = order[:4]
            for x in order[:4]:
                top4[x] += 1
        # final: semis then final
        semi1 = knockout(ex, {r: (by_name_t(t[0], by_name), by_name_t(t[3], by_name)) for r, t in seeds_top.items()},
                         args.seed, "semi1")
        semi2 = knockout(ex, {r: (by_name_t(t[1], by_name), by_name_t(t[2], by_name)) for r, t in seeds_top.items()},
                         args.seed, "semi2")
        for r in seeds_top:
            finals[semi1[r][0]] += 1
            finals[semi2[r][0]] += 1
        final = knockout(ex, {r: (semi1[r], semi2[r]) for r in seeds_top}, args.seed, "final")
        for r in seeds_top:
            champs[final[r][0]] += 1

    R = args.reps
    print(f"\nDone in {time.time() - t0:.0f}s. {R} simulated tournaments, {n} bots, "
          f"max qualifier points {2 * (n - 1)}.\n")
    print(f"{'bot':<14}{'mean pts':>9}{'mean rank':>10}{'P(top4)':>9}{'P(final)':>9}{'P(champ)':>9}")
    for x in sorted(names, key=lambda x: (-top4[x], -sum(pts[x]))):
        print(f"{x:<14}{sum(pts[x]) / R:>9.1f}{sum(ranks[x]) / R:>10.1f}{top4[x] / R:>9.0%}"
              f"{finals[x] / R:>9.0%}{champs[x] / R:>9.0%}")


def by_name_t(name, by_name):
    return (name, by_name[name])


if __name__ == "__main__":
    main()
