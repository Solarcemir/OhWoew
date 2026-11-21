#!/usr/bin/env python3
"""The four measured prizes, computed from play.py --log files, following the README rules.

  python tools/prizes.py logs/*.jsonl

- Biggest bluffer: chips won on hands where the bot bet or raised after the flop
  with under 40% equity against a random hand (its two cards and the board at that
  moment) and the opponent folded; per 1,000 hands played (needs 500 hands).
- Most won off 7-2: chips won on hands won holding seven-two, per seven-two dealt
  (losses don't count; needs 30 seven-two hands).
- Biggest comeback: the lowest share of the chips in play (after a hand) in a match
  the bot went on to win.
- Lowest winning showdown: the weakest five-card hand to win at showdown, with at
  least 1,000 chips taken, a bet on the river, and no split pot.
"""

import json
import random
import sys
from collections import defaultdict

import eval7

DECK = [r + s for r in "23456789TJQKA" for s in "shdc"]


def eq_vs_random(hole, board, n=400, rng=random.Random(1)):
    known = set(hole) | set(board)
    rest = [c for c in DECK if c not in known]
    h = [eval7.Card(c) for c in hole]
    b = [eval7.Card(c) for c in board]
    need = 5 - len(board)
    win = 0.0
    for _ in range(n):
        s = rng.sample(rest, 2 + need)
        full = b + [eval7.Card(c) for c in s[2:]]
        mv = eval7.evaluate(h + full)
        ov = eval7.evaluate([eval7.Card(s[0]), eval7.Card(s[1])] + full)
        win += 1 if mv > ov else (0.5 if mv == ov else 0)
    return win / n


def main():
    files = [f for f in sys.argv[1:] if f.endswith(".jsonl")]
    hands = defaultdict(int)
    bluff = defaultdict(int)
    s72_dealt = defaultdict(int)
    s72_won = defaultdict(int)
    comeback = {}
    lowest = {}
    for fn in files:
        low_share = defaultdict(lambda: 1.0)
        cur = None
        winner = None
        for line in open(fn):
            e = json.loads(line)
            t = e["type"]
            if t == "hand_start":
                cur = {"hole": e.get("hole", {}), "bluffs": defaultdict(bool), "river_bet": False,
                       "folded": set(), "showdown": None}
                for b, cards in cur["hole"].items():
                    hands[b] += 1
                    if sorted(c[0] for c in cards) == ["2", "7"]:
                        s72_dealt[b] += 1
            elif t == "decision" and cur:
                a = e["applied"]["action"]
                st = e["state"]
                if a == "fold":
                    cur["folded"].add(e["bot"])
                if e["street"] != "preflop" and a in ("raise", "all_in"):
                    if e["street"] == "river":
                        cur["river_bet"] = True
                    if not cur["bluffs"][e["bot"]]:
                        if eq_vs_random(st["your_cards"], st["community_cards"]) < 0.40:
                            cur["bluffs"][e["bot"]] = True
            elif t == "showdown" and cur:
                cur["showdown"] = e
            elif t == "hand_end" and cur:
                deltas = e.get("deltas") or {}
                stacks = e.get("stacks") or {}
                total = sum(stacks.values()) or 1
                for b, s in stacks.items():
                    low_share[b] = min(low_share[b], s / total)
                for b, d in deltas.items():
                    if d <= 0:
                        continue
                    others_folded = any(x in cur["folded"] for x in deltas if x != b)
                    if cur["bluffs"][b] and others_folded:
                        bluff[b] += d
                    if b in cur["hole"] and sorted(c[0] for c in cur["hole"][b]) == ["2", "7"]:
                        s72_won[b] += d
                    sd = cur["showdown"]
                    if sd and d >= 1000 and cur["river_bet"] and sum(1 for x in deltas.values() if x > 0) == 1:
                        cards = sd["reveal"][b] + sd["board"]
                        v = eval7.evaluate([eval7.Card(c) for c in cards])
                        if b not in lowest or v < lowest[b][0]:
                            lowest[b] = (v, eval7.handtype(v), " ".join(sd["reveal"][b]), " ".join(sd["board"]))
                cur = None
            elif t == "elimination":
                pass
            elif t == "match_end":
                res = e.get("results") or []
                for r in res:
                    if r.get("place") == 1:
                        winner = r.get("bot")
        if winner:
            share = low_share[winner]
            if winner not in comeback or share < comeback[winner]:
                comeback[winner] = share
    print(f"{len(files)} matches\n")
    print(f"{'bot':<14}{'hands':>7}{'bluff/1000':>11}{'72 dealt':>9}{'72 won/hand':>12}{'comeback':>10}  lowest winning showdown")
    for b in sorted(hands, key=lambda x: -hands[x]):
        h = hands[b]
        lw = lowest.get(b)
        print(f"{b:<14}{h:>7}{bluff[b] * 1000 / max(1, h):>11.0f}{s72_dealt[b]:>9}"
              f"{s72_won[b] / max(1, s72_dealt[b]):>12.0f}"
              f"{(f'{comeback[b]:.1%}' if b in comeback else '-'):>10}  "
              f"{(f'{lw[1]} ({lw[2]} on {lw[3]})' if lw else '-')}")


if __name__ == "__main__":
    main()
