"""Generates the rival archetype bots (rivals/<name>/bot.py) from a shared header."""
import os, textwrap
HEADER = '''import random
import eval7

_D = [eval7.Card(r + s) for r in "23456789TJQKA" for s in "shdc"]


def eqty(gs, n=120):
    h = [eval7.Card(c) for c in gs["your_cards"]]
    b = [eval7.Card(c) for c in gs["community_cards"]]
    k = set(map(str, h + b))
    d = [c for c in _D if str(c) not in k]
    w = 0.0
    for _ in range(n):
        random.shuffle(d)
        o = d[:2]
        run = b + d[2:2 + 5 - len(b)]
        x, y = eval7.evaluate(h + run), eval7.evaluate(o + run)
        w += 1.0 if x > y else 0.5 if x == y else 0.0
    return w / n


def bet(gs, frac):
    """Raise/bet to `frac` of the pot on top of what we owe (all-in if it covers us)."""
    pot, owed = gs["pot"], gs["amount_owed"]
    to = max(gs["min_raise_to"], gs["current_bet"] + int(frac * (pot + owed)))
    if to >= gs["your_stack"] + gs["your_bet_this_street"]:
        return {"action": "all_in"}
    return {"action": "raise", "amount": to}


def shove(gs):
    return {"action": "all_in"}


def passive(gs):
    return {"action": "check"} if gs["can_check"] else {"action": "fold"}


def call(gs):
    return {"action": "check"} if gs["can_check"] else {"action": "call"}


def info(gs):
    pot, owed = gs["pot"], gs["amount_owed"]
    return (gs["street"] == "preflop", owed, pot, owed / (pot + owed) if owed else 0.0,
            gs["blinds"]["bb"], gs["your_stack"])


def is_72(gs):
    r = sorted(c[0] for c in gs["your_cards"])
    return r == ["2", "7"] and gs["your_cards"][0][1] != gs["your_cards"][1][1]

'''
BOTS = {}

BOTS["rock"] = '''
# DEFENSIVE: tight-passive. Plays only strong hands, value bets, folds to pressure, never bluffs.
def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    e = eqty(gs)
    pre, owed, pot, odds, bb, stack = info(gs)
    if pre:
        if e > 0.66:
            return bet(gs, 0.8)
        if e > 0.58 and owed <= 3 * bb:
            return call(gs)
        return passive(gs) if not owed or owed <= bb and e > 0.5 else passive(gs)
    if e > 0.80:
        return bet(gs, 0.7)
    if e > 0.68 and gs["can_check"]:
        return bet(gs, 0.4)
    if gs["can_check"]:
        return {"action": "check"}
    return call(gs) if e > odds + 0.14 else {"action": "fold"}
'''

BOTS["bully"] = '''
# AGGRESSIVE: raises almost every hand, fires every street, re-raises constantly, ignores hand strength.
def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    pre, owed, pot, odds, bb, stack = info(gs)
    if pre:
        if owed > 0.4 * stack:                    # big re-raise: need a real hand
            return call(gs) if eqty(gs) > 0.6 else {"action": "fold"}
        return bet(gs, 1.0 if owed > bb else 0.0) if random.random() < 0.92 else call(gs)
    if gs["can_check"]:
        return bet(gs, 0.75) if random.random() < 0.8 else {"action": "check"}
    e = eqty(gs, 80)
    if e > 0.55 and random.random() < 0.5:
        return bet(gs, 1.0)
    if owed >= 0.5 * stack:
        return call(gs) if e > odds + 0.05 else {"action": "fold"}
    return call(gs) if (e > 0.3 or random.random() < 0.4) else {"action": "fold"}
'''

BOTS["station"] = '''
# CALLING STATION: calls with anything that has a pulse, almost never raises or folds.
def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    e = eqty(gs, 100)
    pre, owed, pot, odds, bb, stack = info(gs)
    if e > 0.88:
        return bet(gs, 0.6)
    if gs["can_check"]:
        return {"action": "check"}
    return call(gs) if e > 0.28 or owed <= bb else {"action": "fold"}
'''

BOTS["bluffer"] = '''
# PRIZE "biggest bluffer": barrels big with air, value-bets strong hands, gives up to heavy resistance.
def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    e = eqty(gs)
    pre, owed, pot, odds, bb, stack = info(gs)
    if pre:
        if e > 0.7:
            return bet(gs, 1.0)
        if not owed or owed <= bb:
            return bet(gs, 1.2) if random.random() < 0.6 else call(gs)
        return call(gs) if e > 0.5 else {"action": "fold"}
    if gs["can_check"]:
        if e > 0.65:
            return bet(gs, 0.7)
        if e < 0.40 and random.random() < 0.55:
            return bet(gs, random.choice([0.9, 1.3, 2.0]))      # big bluff
        return {"action": "check"}
    if e > 0.75:
        return bet(gs, 1.0)
    if e < 0.35 and random.random() < 0.15:
        return bet(gs, 2.2)                                     # bluff re-raise
    return call(gs) if e > odds + 0.08 else {"action": "fold"}
'''

BOTS["sevendeuce"] = '''
# PRIZE "most won with 7-2": always plays 7-2 offsuit as if it were pocket aces and fires every street;
# everything else is a plain tight-aggressive game.
def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    pre, owed, pot, odds, bb, stack = info(gs)
    if is_72(gs):
        if pre:
            return bet(gs, 1.5)
        if owed and owed >= 0.6 * stack:
            return call(gs) if eqty(gs, 80) > odds else {"action": "fold"}
        return bet(gs, 0.85)
    e = eqty(gs)
    if pre:
        if e > 0.62:
            return bet(gs, 0.9)
        if owed <= bb and e > 0.5:
            return call(gs)
        return passive(gs) if not owed else (call(gs) if e > 0.55 and owed <= 4 * bb else {"action": "fold"})
    if gs["can_check"]:
        return bet(gs, 0.6) if e > 0.6 else {"action": "check"}
    return call(gs) if e > odds + 0.06 else {"action": "fold"}
'''

BOTS["comeback"] = '''
# PRIZE "comeback": survives short stacks - when behind it shoves any decent equity hand
# (fold equity + variance), when ahead it plays small-ball and avoids big pots.
def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    e = eqty(gs)
    pre, owed, pot, odds, bb, stack = info(gs)
    behind = stack < 4000
    ahead = stack > 14000
    if behind:
        if stack < 15 * bb or e > 0.7:
            if e > 0.5 or (pre and not owed and random.random() < 0.5):
                return shove(gs)
        if gs["can_check"]:
            return bet(gs, 0.6) if e > 0.6 else {"action": "check"}
        return call(gs) if e > odds + 0.02 else {"action": "fold"}
    if ahead:
        if gs["can_check"]:
            return bet(gs, 0.3) if e > 0.55 else {"action": "check"}
        return call(gs) if e > odds + 0.12 else {"action": "fold"}
    if gs["can_check"]:
        return bet(gs, 0.55) if e > 0.6 else {"action": "check"}
    return call(gs) if e > odds + 0.06 else {"action": "fold"}
'''

BOTS["limper"] = '''
# TRAPPER / LIMPER: limps and min-raises, small bets, check-raises strong hands, hates big pots without a hand.
def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    e = eqty(gs)
    pre, owed, pot, odds, bb, stack = info(gs)
    if pre:
        if e > 0.75:
            return {"action": "raise", "amount": gs["min_raise_to"]}
        if owed <= bb and e > 0.4:
            return call(gs)
        return call(gs) if e > 0.58 and owed <= 3 * bb else passive(gs)
    if gs["can_check"]:
        if e > 0.78 and random.random() < 0.6:
            return {"action": "check"}                           # slow-play / trap
        return bet(gs, 0.33) if e > 0.5 else {"action": "check"}
    if e > 0.82:
        return bet(gs, 1.5)                                       # check-raise
    return call(gs) if e > odds + 0.08 else {"action": "fold"}
'''

BOTS["adaptive"] = '''
# ADAPTIVE: counts how often the opponent bets/raises vs checks/calls (across hands) and counters:
# vs maniacs it traps and calls down; vs passive players it bluffs and bets thin.
_SEEN = set()
_ST = {"bets": 0, "pass": 0}


def _learn(gs):
    me = gs["seat_to_act"]
    for i, a in enumerate(gs.get("action_log", [])):
        key = (gs.get("hand_num"), i)
        if key in _SEEN or a.get("seat") == me:
            continue
        _SEEN.add(key)
        if a["action"] in ("raise", "all_in"):
            _ST["bets"] += 1
        elif a["action"] in ("check", "call"):
            _ST["pass"] += 1


def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    _learn(gs)
    n = _ST["bets"] + _ST["pass"]
    agg = (_ST["bets"] + 3) / (n + 8.0)                          # opponent aggression estimate
    e = eqty(gs)
    pre, owed, pot, odds, bb, stack = info(gs)
    maniac = agg > 0.5
    passive_opp = agg < 0.25
    if pre:
        if e > (0.58 if maniac else 0.64):
            return bet(gs, 0.9)
        if owed <= bb and e > 0.42:
            return call(gs)
        return call(gs) if (maniac and e > 0.5 and owed < 0.3 * stack) else (
            call(gs) if e > 0.6 and owed <= 4 * bb else passive(gs))
    if gs["can_check"]:
        if passive_opp and e < 0.45 and random.random() < 0.45:
            return bet(gs, 0.6)                                   # steal from a passive player
        if maniac and e > 0.7 and random.random() < 0.5:
            return {"action": "check"}                            # trap
        return bet(gs, 0.6) if e > (0.55 if passive_opp else 0.62) else {"action": "check"}
    margin = -0.03 if maniac else (0.12 if passive_opp else 0.05)  # passive bets are strong
    if e > 0.8:
        return bet(gs, 1.0)
    return call(gs) if e > odds + margin else {"action": "fold"}
'''

BOTS["balanced"] = '''
# BALANCED / "GTO-ish": bets with value and with a matching share of bluffs, mixes sizes and frequencies.
def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    e = eqty(gs)
    pre, owed, pot, odds, bb, stack = info(gs)
    r = random.random()
    if pre:
        if e > 0.68 or (e > 0.5 and r < 0.45):
            return bet(gs, 0.75 + 0.5 * random.random()) if owed < 0.3 * stack else call(gs)
        if owed <= bb and e > 0.4:
            return call(gs)
        return call(gs) if e > 0.56 and owed <= 3 * bb else passive(gs)
    if gs["can_check"]:
        size = random.choice([0.33, 0.66, 1.0])
        if e > 0.65 or (e < 0.30 and r < 0.30):                  # value + ~1:2 bluffs
            return bet(gs, size)
        if 0.5 < e <= 0.65 and r < 0.35:
            return bet(gs, 0.33)
        return {"action": "check"}
    if e > 0.82:
        return bet(gs, 1.0)
    if e < 0.28 and r < 0.08:
        return bet(gs, 1.0)                                       # rare bluff-raise
    return call(gs) if e > odds + 0.04 + 0.05 * r else {"action": "fold"}
'''

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for name, body in BOTS.items():
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "bot.py"), "w") as f:
        f.write('"""Rival archetype: ' + name + ' (test opponent; generated by rivals/_gen/gen.py)."""\n' + HEADER + body.lstrip("\n"))
print("wrote", ", ".join(BOTS))
