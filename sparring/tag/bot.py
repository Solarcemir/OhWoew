"""Sparring bot: tight-aggressive by simple thresholds (deterministic-ish)."""
import eval7

R = "23456789TJQKA"


def pre_score(c):
    a, b = sorted((R.index(c[0][0]), R.index(c[1][0])), reverse=True)
    return a + b + (4 if a == b else 0) + (2 if c[0][1] == c[1][1] else 0) - (2 if a - b > 3 else 0)


def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    pot, owed = gs["pot"], gs["amount_owed"]
    board = gs["community_cards"]
    if not board:
        s = pre_score(gs["your_cards"])
        if s >= 20:
            return {"action": "raise", "amount": max(gs["min_raise_to"], gs["blinds"]["bb"] * 3)}
        if s >= 14 and owed <= gs["blinds"]["bb"] * 3:
            return {"action": "call"} if not gs["can_check"] else {"action": "check"}
        return {"action": "check"} if gs["can_check"] else {"action": "fold"}
    hand = [eval7.Card(x) for x in gs["your_cards"] + board]
    t = eval7.handtype(eval7.evaluate(hand))
    strong = t not in ("High Card", "Pair")
    pair = t == "Pair"
    if strong:
        return {"action": "raise", "amount": max(gs["min_raise_to"], gs["current_bet"] + int(pot * 0.7))}
    if pair:
        if gs["can_check"]:
            return {"action": "raise", "amount": max(gs["min_raise_to"], int(pot * 0.5))}
        return {"action": "call"} if owed <= pot * 0.6 else {"action": "fold"}
    return {"action": "check"} if gs["can_check"] else {"action": "fold"}
