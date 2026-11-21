"""Play N freezeouts per opponent (alternating seats) with the kit's play.py.
Usage (from the repo root, venv active; WORKERS=N sets parallel matches, default = CPU cores):
  python tools/run_matches.py my_bots/ohwow 30 sparring/tag sparring/shover bots/caller"""
import re, subprocess, sys, concurrent.futures as cf
import os
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
me, n, opps = sys.argv[1], int(sys.argv[2]), sys.argv[3:]

def one(args):
    opp, seed = args
    order = [me, opp] if seed % 2 == 0 else [opp, me]
    out = subprocess.run([sys.executable, "play.py", *order, "--seed", str(seed)], cwd=KIT,
                         capture_output=True, text=True, timeout=900).stdout
    won = re.search(r"1\. (\S+)", out)
    hands = re.search(r"(\d+) hands", out)
    think = re.findall(r"avg think (\d+) ms", out)
    err = re.search(r"(timeout|error|crash)[^\n]*", out, re.I)
    return opp, won.group(1) if won else "?", int(hands.group(1)) if hands else 0, think, err.group(0) if err else ""

jobs = [(o, s) for o in opps for s in range(1, n + 1)]
res = {}
WORKERS = int(os.environ.get("WORKERS", max(1, (os.cpu_count() or 2))))
with cf.ThreadPoolExecutor(WORKERS) as ex:
    for opp, winner, hands, think, err in ex.map(one, jobs):
        r = res.setdefault(opp, {"w": 0, "n": 0, "hands": 0, "err": set(), "think": []})
        r["n"] += 1; r["w"] += winner.startswith(me.rstrip("/").split("/")[-1]); r["hands"] += hands
        r["think"] += [int(x) for x in think]; 
        if err: r["err"].add(err)
def wilson(w, n, z=1.96):
    if n == 0: return 0.0, 0.0
    p = w / n; d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d; h = z * ((p*(1-p)/n + z*z/(4*n*n)) ** 0.5) / d
    return c - h, c + h
for o, r in res.items():
    lo, hi = wilson(r["w"], r["n"])
    print(f"{o:<22} win rate {r['w']/r['n']:.0%}  95% CI [{lo:.0%}, {hi:.0%}]  |  wins {r['w']}/{r['n']}  avg hands {r['hands']/r['n']:.0f}  max avg-think {max(r['think'] or [0])} ms  {sorted(r['err'])[:2]}")
