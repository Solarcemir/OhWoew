"""Round-robin of bots: every pair plays N freezeouts (seats alternate). Prints a win matrix and standings.
Usage (repo root, venv active; WORKERS=N parallel matches):
  python3 tools/round_robin.py 10 my_bots/ohwow rivals/rock rivals/bully ...
  python3 tools/round_robin.py 10 my_bots/ohwow rivals/*"""
import itertools, os, re, subprocess, sys, concurrent.futures as cf
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
n = int(sys.argv[1]); bots = [b.rstrip("/") for b in sys.argv[2:] if not os.path.basename(b.rstrip("/")).startswith("_")]
name = lambda b: os.path.basename(b)

def one(job):
    a, b, seed = job
    order = [a, b] if seed % 2 == 0 else [b, a]
    out = subprocess.run([sys.executable, "play.py", *order, "--seed", str(seed)], cwd=KIT,
                         capture_output=True, text=True, timeout=900).stdout
    m = re.search(r"1\. (\S+)", out)
    return a, b, (m.group(1) if m else "?")

jobs = [(a, b, s) for a, b in itertools.combinations(bots, 2) for s in range(1, n + 1)]
W = {(a, b): 0 for a in bots for b in bots}
with cf.ThreadPoolExecutor(int(os.environ.get("WORKERS", os.cpu_count() or 2))) as ex:
    for a, b, w in ex.map(one, jobs):
        if w == name(a): W[(a, b)] += 1
        elif w == name(b): W[(b, a)] += 1
tot = {b: sum(W[(b, o)] for o in bots if o != b) for b in bots}
print("\nWins out of %d (row beats column):" % n)
print(" " * 12 + "".join(f"{name(b)[:9]:>10}" for b in bots) + "     TOTAL")
for a in bots:
    print(f"{name(a)[:11]:<12}" + "".join(f"{('-' if a == b else W[(a, b)]):>10}" for b in bots) + f"{tot[a]:>10}")
games = n * (len(bots) - 1)
print("\nStandings (win rate over %d matches each):" % games)
for i, b in enumerate(sorted(bots, key=lambda x: -tot[x]), 1):
    print(f"{i:>2}. {name(b):<12} {tot[b]:>4}/{games}  {tot[b]/games:.0%}")
