"""Sparring bot: the classic hackathon bot. Monte Carlo equity against a random hand,
value-bets strong hands, calls by pot odds, occasional bluff."""
import random
import eval7

DECK = [eval7.Card(r + s) for r in "23456789TJQKA" for s in "shdc"]


def equity(hole, board, n=200):
    hole = [eval7.Card(c) for c in hole]
    board = [eval7.Card(c) for c in board]
    known = set(map(str, hole + board))
    deck = [c for c in DECK if str(c) not in known]
    wins = 0.0
    for _ in range(n):
        random.shuffle(deck)
        opp = deck[:2]
        run = board + deck[2:2 + 5 - len(board)]
        a, b = eval7.evaluate(hole + run), eval7.evaluate(opp + run)
        wins += 1.0 if a > b else 0.5 if a == b else 0.0
    return wins / n


def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    eq = equity(gs["your_cards"], gs["community_cards"])
    pot, owed, bb = gs["pot"], gs["amount_owed"], gs["blinds"]["bb"]
    odds = owed / (pot + owed) if owed else 0.0
    def raise_to(frac):
        return {"action": "raise", "amount": max(gs["min_raise_to"], gs["current_bet"] + int(frac * (pot + owed)))}
    if eq > 0.72:
        return raise_to(0.75)
    if gs["can_check"]:
        if eq > 0.58:
            return raise_to(0.55)
        if eq < 0.35 and random.random() < 0.10:
            return raise_to(0.6)
        return {"action": "check"}
    if eq > odds + 0.06:
        return {"action": "call"}
    return {"action": "fold"}
