#!/usr/bin/env python3
"""Where does a bot win and lose chips? Reads play.py --log files.

  python tools/analyze.py our_bot logs/*.jsonl [--worst 8]
"""

import json
import sys
from collections import defaultdict


def main():
    bot = sys.argv[1]
    worst = int(sys.argv[sys.argv.index("--worst") + 1]) if "--worst" in sys.argv else 8
    files = [f for f in sys.argv[2:] if f.endswith(".jsonl")]
    cat = defaultdict(lambda: [0, 0])          # category -> [hands, chips]
    hands = []
    for fn in files:
        cur = {}
        for line in open(fn):
            e = json.loads(line)
            t = e["type"]
            if t == "hand_start":
                cur = {"file": fn, "hand": e["hand"], "hole": e.get("hole", {}), "dec": [], "sd": False,
                       "bb": e["blinds"]["bb"], "stacks": e.get("stacks", {})}
            elif t == "decision" and cur:
                st = e["state"]
                cur["dec"].append((e["bot"], e["street"], st.get("your_cards"), st.get("community_cards"),
                                   st.get("pot"), st.get("amount_owed"), e["applied"], e.get("debug")))
            elif t == "showdown" and cur:
                cur["sd"] = True
            elif t == "hand_end" and cur:
                d = (e.get("deltas") or {}).get(bot)
                if d is None:
                    continue
                cur["delta"] = d
                cur["board"] = e.get("board")
                last = max((x[1] for x in cur["dec"]), key=["preflop", "flop", "turn", "river"].index, default="preflop")
                mine = [x for x in cur["dec"] if x[0] == bot]
                folded = any(x[6]["action"] == "fold" for x in mine)
                opp_folded = any(x[6]["action"] == "fold" for x in cur["dec"] if x[0] != bot)
                how = "showdown" if cur["sd"] else ("we fold" if folded else ("opp folds" if opp_folded else "other"))
                key = f"{last:<7} {how}"
                cat[key][0] += 1
                cat[key][1] += d
                hands.append(cur)
                cur = {}
    print(f"{len(hands)} hands from {len(files)} files\n")
    print(f"{'last street / ending':<24}{'hands':>7}{'chips':>10}{'per hand':>10}")
    for k, (n, c) in sorted(cat.items(), key=lambda kv: kv[1][1]):
        print(f"{k:<24}{n:>7}{c:>10}{c / n:>10.0f}")
    print(f"\nWorst {worst} hands:")
    for hnd in sorted(hands, key=lambda h: h["delta"])[:worst]:
        print(f"\n{hnd['file'].split('/')[-1].split(chr(92))[-1]} hand {hnd['hand']}  delta {hnd['delta']}  bb {hnd['bb']}  "
              f"holes {hnd['hole']}  board {hnd['board']}")
        for b, stt, cards, board, pot, owed, ap, dbg in hnd["dec"]:
            tag = "  >" if b == bot else "   "
            amt = f" {ap.get('amount')}" if ap.get("amount") else ""
            print(f"{tag} {stt:<7} {b:<12} pot {pot:<6} owed {owed:<6} {ap['action']}{amt}  {json.dumps(dbg) if b == bot and dbg else ''}")


if __name__ == "__main__":
    main()
