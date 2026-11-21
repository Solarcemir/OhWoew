#!/usr/bin/env python3
"""Round-robin duplicate arena, in the tournament's format.

Every pair of bots plays duplicate freezeout pairs: the same seed twice, with the
seats swapped, so both bots get both sets of cards. Winning both matches of a
pair is a win, one each is a draw.

  python tools/arena.py my_bots/ours sparring/*              # every bot vs every bot
  python tools/arena.py my_bots/ours sparring/* --focus ours  # only pairings involving 'ours'
  python tools/arena.py A B C --pairs 20 --workers 6 --seed 1

Not part of the submission (only my_bots/<team>/ is collected).
"""

import argparse
import itertools
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if os.name == "nt":  # Python on Windows can't start without these (guard.safe_env drops them)
    os.environ.setdefault("BOT_ENV_ALLOW", "SYSTEMROOT,WINDIR")

from pokercore import structures  # noqa: E402
from pokercore.seats import BotSeat  # noqa: E402
from pokercore.seeds import derive_seed  # noqa: E402
from pokercore.table import MatchConfig, play_match  # noqa: E402


@dataclass
class Bot:
    name: str
    path: str
    hash: str = None
    source: str = "local"


class NullLog:
    def __init__(self):
        self.n = 0

    def emit(self, event_type, **data):
        self.n += 1
        return {"i": self.n, "type": event_type, **data}


def play_one(paths, names, seed, hands_cap, time_limit_ms, docker=False):
    """One freezeout match; returns {name: (place, final_stack, timeouts, errors)} and hands.
    docker=True runs every bot in the tournament sandbox (no network, read-only, half a core)."""
    bots = [Bot(n, p) for n, p in zip(names, paths)]
    os.environ["BOT_MATCH_SEED"] = str(seed)   # only the test-only sparring/supreme bot reads this
    config = MatchConfig(mode="freezeout", hands=hands_cap, starting_stack=10_000,
                         structure=structures.parse("standard", 10_000), seed=seed,
                         time_limit_ms=time_limit_ms)
    seats = [BotSeat(b, seed=derive_seed(seed, "bot", b.name), time_limit_ms=time_limit_ms,
                     use_docker=docker) for b in bots]
    result = play_match(seats, config, f"arena-{seed}", NullLog())
    out = {r["bot"]: (r["place"], r["final_stack"], r.get("timeouts", 0) or 0,
                      r.get("errors", 0) or 0) for r in result["results"]}
    return out, result["hands"]


def play_pair(a, b, seed, hands_cap, time_limit_ms, docker=False):
    """Duplicate pair: (a, b) then (b, a) on the same seed."""
    (na, pa), (nb, pb) = a, b
    r1, h1 = play_one([pa, pb], [na, nb], seed, hands_cap, time_limit_ms, docker)
    r2, h2 = play_one([pb, pa], [nb, na], seed, hands_cap, time_limit_ms, docker)
    return na, nb, seed, r1, r2, h1 + h2


def winner(res):
    """The bot with place 1, or the bigger stack if the hand cap was hit."""
    return max(res, key=lambda n: (res[n][0] == 1, res[n][1]))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("bots", nargs="+", help="bot folders")
    p.add_argument("--pairs", type=int, default=10, help="duplicate pairs per pairing (default 10)")
    p.add_argument("--seed", type=int, default=12345, help="root seed (same seed = same deals)")
    p.add_argument("--focus", help="only play pairings that include one of these bot names (comma-separated)")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    p.add_argument("--hands", type=int, default=1000, help="safety cap per match")
    p.add_argument("--time-limit-ms", type=int, default=2000)
    p.add_argument("--json", help="also write the per-pairing W-D-L counts here (for tools/tourney_mc.py)")
    p.add_argument("--docker", action="store_true",
                   help="run every bot in the tournament sandbox (make sandbox first); use for untrusted bots")
    args = p.parse_args()

    bots = []
    for path in args.bots:
        bp = Path(path)
        if not (bp / "bot.py").exists():
            continue
        bots.append((bp.resolve().name, str(bp)))
    names = [n for n, _ in bots]
    if len(set(names)) != len(names):
        sys.exit("two bots have the same folder name")
    focus = set(args.focus.split(",")) if args.focus else set()
    pairings = [(a, b) for a, b in itertools.combinations(bots, 2)
                if not focus or a[0] in focus or b[0] in focus]
    jobs = [(a, b, derive_seed(args.seed, "pair", k)) for a, b in pairings for k in range(args.pairs)]
    print(f"{len(bots)} bots, {len(pairings)} pairings x {args.pairs} duplicate pairs = "
          f"{len(jobs) * 2} matches on {args.workers} workers")

    wdl = defaultdict(lambda: [0, 0, 0])           # (name, opp) -> [wins, draws, losses]
    matches_won = defaultdict(int)
    mw = defaultdict(lambda: [0, 0])               # (name, opp) -> [matches won, matches]
    hands_played = defaultdict(int)
    problems = defaultdict(lambda: [0, 0])          # name -> [timeouts, errors]
    t0, done = time.time(), 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(play_pair, a, b, s, args.hands, args.time_limit_ms, args.docker) for a, b, s in jobs]
        for f in as_completed(futs):
            try:
                na, nb, seed, r1, r2, hands = f.result()
            except Exception as e:  # noqa: BLE001
                print(f"  match failed: {e!r}")
                continue
            w1, w2 = winner(r1), winner(r2)
            for r in (r1, r2):
                for n, (_, _, to, er) in r.items():
                    problems[n][0] += to
                    problems[n][1] += er
            matches_won[w1] += 1
            matches_won[w2] += 1
            for w in (w1, w2):
                mw[(na, nb)][1] += 1
                mw[(nb, na)][1] += 1
                mw[(w, nb if w == na else na)][0] += 1
            hands_played[na] += hands
            hands_played[nb] += hands
            a_wins = (w1 == na) + (w2 == na)
            res_a = {2: 0, 1: 1, 0: 2}[a_wins]       # 0 win, 1 draw, 2 loss (for a)
            wdl[(na, nb)][res_a] += 1
            wdl[(nb, na)][2 - res_a] += 1
            done += 1
            if done % max(1, len(jobs) // 10) == 0:
                print(f"  {done}/{len(jobs)} pairs, {time.time() - t0:.0f}s")

    print(f"\nDone in {time.time() - t0:.0f}s\n")
    order = sorted(names, key=lambda n: -sum(v[0] + 0.5 * v[1] for (a, _), v in wdl.items() if a == n))
    width = max(len(n) for n in names) + 2
    print("Pairing record (W-D-L) of the row bot against the column bot:")
    print(" " * width + "".join(f"{n[:10]:>12}" for n in order))
    for a in order:
        row = []
        for b in order:
            v = wdl.get((a, b))
            cell = "-" if a == b or not v else f"{v[0]}-{v[1]}-{v[2]}"
            row.append(f"{cell:>12}")
        print(f"{a:<{width}}" + "".join(row))
    print("\nMatch win % of the row bot against the column bot (± 95% interval):")
    print(" " * width + "".join(f"{n[:10]:>12}" for n in order))
    for a in order:
        row = []
        for b in order:
            won, tot = mw.get((a, b), (0, 0))
            if a == b or not tot:
                row.append(f"{'-':>12}")
            else:
                pct = won / tot
                ci = 1.96 * (pct * (1 - pct) / tot) ** 0.5
                row.append(f"{f'{pct:.0%}±{ci:.0%}':>12}")
        print(f"{a:<{width}}" + "".join(row))
    if args.json:
        import json
        with open(args.json, "w") as fh:
            json.dump({"names": names, "pairs_per_pairing": args.pairs,
                       "wdl": {f"{a}|{b}": v for (a, b), v in wdl.items()}}, fh)
        print(f"\nwrote {args.json}")
    print("\nStandings (pair win = 1, draw = 0.5):")
    for n in order:
        rec = [sum(v[i] for (a, _), v in wdl.items() if a == n) for i in range(3)]
        played = sum(rec)
        pts = rec[0] + 0.5 * rec[1]
        to, er = problems[n]
        flags = f"  timeouts {to}, errors {er}" if to or er else ""
        tot = sum(v[1] for (a, _), v in mw.items() if a == n)
        pct = matches_won[n] / tot if tot else 0
        ci = 1.96 * (pct * (1 - pct) / tot) ** 0.5 if tot else 0
        print(f"  {n:<{width}} {pts:6.1f} / {played:<4} W{rec[0]} D{rec[1]} L{rec[2]}"
              f"  matches won {matches_won[n]}/{tot} ({pct:.0%}±{ci:.0%})"
              f"  avg hands/match {hands_played[n] / max(1, tot):.0f}{flags}")


if __name__ == "__main__":
    main()
