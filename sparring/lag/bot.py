"""Sparring bot: loose-aggressive. Opens and bets a lot with any hand, folds to
resistance when it has nothing."""
import random
import eval7


def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    pot, owed, board = gs["pot"], gs["amount_owed"], gs["community_cards"]
    bb = gs["blinds"]["bb"]
    def raise_to(frac):
        return {"action": "raise", "amount": max(gs["min_raise_to"], gs["current_bet"] + int(frac * (pot + owed)))}
    if not board:
        r = random.random()
        if owed > bb * 4:                       # facing a big raise
            hi = {c[0] for c in gs["your_cards"]} & set("AKQJ")
            return {"action": "call"} if hi and random.random() < 0.6 else {"action": "fold"}
        if r < 0.65:
            return raise_to(1.0) if owed else {"action": "raise", "amount": max(gs["min_raise_to"], int(bb * 2.5))}
        return {"action": "check"} if gs["can_check"] else {"action": "call"}
    t = eval7.handtype(eval7.evaluate([eval7.Card(c) for c in gs["your_cards"] + board]))
    made = t != "High Card"
    if gs["can_check"]:
        return raise_to(random.choice([0.5, 0.75, 1.0])) if (made or random.random() < 0.55) else {"action": "check"}
    if made and random.random() < 0.8:
        return {"action": "call"} if random.random() < 0.7 else raise_to(0.8)
    if owed <= pot * 0.35 and random.random() < 0.5:
        return {"action": "call"}
    return {"action": "fold"}
