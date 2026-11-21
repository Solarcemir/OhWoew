"""Play N freezeouts per opponent (alternating seats) with the kit's play.py.
Usage (from the repo root, venv active):
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
with cf.ThreadPoolExecutor(4) as ex:
    for opp, winner, hands, think, err in ex.map(one, jobs):
        r = res.setdefault(opp, {"w": 0, "n": 0, "hands": 0, "err": set(), "think": []})
        r["n"] += 1; r["w"] += winner.startswith(me.rstrip("/").split("/")[-1]); r["hands"] += hands
        r["think"] += [int(x) for x in think]; 
        if err: r["err"].add(err)
for o, r in res.items():
    print(f"{o:<22} wins {r['w']}/{r['n']}  avg hands {r['hands']/r['n']:.0f}  max avg-think {max(r['think'] or [0])} ms  {sorted(r['err'])[:2]}")
