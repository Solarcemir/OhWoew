"""Sparring bot: raises about 80% of the time whatever it holds."""
import random


def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    if random.random() < 0.8:
        top = gs["current_bet"] + int(gs["pot"] * random.uniform(0.6, 1.4))
        return {"action": "raise", "amount": max(gs["min_raise_to"], top)}
    return {"action": "check"} if gs["can_check"] else {"action": "call"}
