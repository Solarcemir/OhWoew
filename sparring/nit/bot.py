"""Sparring bot: only plays premium hands; bets big when it has them."""
import random

R = "23456789TJQKA"


def strength(gs):
    c = gs["your_cards"]
    a, b = sorted((R.index(c[0][0]), R.index(c[1][0])), reverse=True)
    suited = c[0][1] == c[1][1]
    s = a + b + (3 if a == b else 0) + (1 if suited else 0) + (6 if a == b and a >= 8 else 0)
    board = gs["community_cards"]
    if board:
        ranks = [x[0] for x in board]
        if c[0][0] in ranks or c[1][0] in ranks or a == b:
            s += 14
    return s


def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    s = strength(gs)
    if s >= 30:
        return {"action": "raise", "amount": max(gs["min_raise_to"], gs["current_bet"] + gs["pot"])}
    if gs["can_check"]:
        return {"action": "check"}
    return {"action": "call"} if s >= 24 and gs["amount_owed"] <= gs["pot"] * 0.4 else {"action": "fold"}
