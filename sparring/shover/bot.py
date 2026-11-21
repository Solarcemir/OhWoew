"""Sparring bot: all-in every hand."""


def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    return {"action": "all_in"}
