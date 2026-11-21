"""
Naive equity bot for sparring.

Strategy: estimate the chance of winning against a random hand with a
Monte Carlo simulation (eval7), then play by simple thresholds:
  - can check:  bet when strong, otherwise check
  - facing bet: raise when very strong, call when equity beats pot odds
                with a small margin, otherwise fold
No opponent modelling. Any error folds or checks, so the bot never crashes.
"""

import random

import eval7

RANKS = "23456789TJQKA"
SUITS = "cdhs"
DECK = [r + s for r in RANKS for s in SUITS]

SIMS = 300          # Monte Carlo samples per decision (well inside the 2 s limit)
BET_EQUITY = 0.62   # bet/raise threshold when we can check
RAISE_EQUITY = 0.80 # re-raise threshold when facing a bet
CALL_MARGIN = 0.05  # extra equity above pot odds needed to call


def _equity(hole, board, sims=SIMS):
    """Share of the pot won against one random opponent hand (ties count half)."""
    known = set(hole + board)
    remaining = [c for c in DECK if c not in known]
    need = 5 - len(board)
    my_cards = [eval7.Card(c) for c in hole]
    board_cards = [eval7.Card(c) for c in board]
    score = 0.0
    for _ in range(sims):
        draw = random.sample(remaining, need + 2)
        opp = [eval7.Card(draw[0]), eval7.Card(draw[1])]
        runout = [eval7.Card(c) for c in draw[2:]]
        mine = eval7.evaluate(my_cards + board_cards + runout)
        theirs = eval7.evaluate(opp + board_cards + runout)
        if mine > theirs:
            score += 1.0
        elif mine == theirs:
            score += 0.5
    return score / sims


def _raise_to(gs, extra_fraction):
    """Total bet for this street: current bet plus a fraction of the pot, legal and capped."""
    pot = gs["pot"]
    bb = gs["blinds"]["bb"]
    increment = max(bb, int(pot * extra_fraction))
    target = gs["current_bet"] + increment
    target = max(target, gs["min_raise_to"])
    max_total = gs["your_bet_this_street"] + gs["your_stack"]
    if target >= max_total:
        return {"action": "all_in"}
    return {"action": "raise", "amount": target}


def decide(game_state: dict) -> dict:
    if game_state.get("type") == "warmup":
        return {"action": "check"}

    try:
        hole = list(game_state["your_cards"])
        board = list(game_state["community_cards"])
        owed = game_state["amount_owed"]
        pot = game_state["pot"]

        equity = _equity(hole, board)
        debug = {"equity": round(equity, 3)}

        if game_state["can_check"]:
            if equity >= BET_EQUITY:
                action = _raise_to(game_state, 0.6)
            else:
                action = {"action": "check"}
        else:
            if equity >= RAISE_EQUITY:
                action = _raise_to(game_state, 0.7)
            else:
                pot_odds = owed / (pot + owed) if (pot + owed) > 0 else 0.0
                if equity >= pot_odds + CALL_MARGIN:
                    action = {"action": "call"}
                else:
                    action = {"action": "fold"}

        action["debug"] = debug
        return action
    except Exception:
        # Never crash: check when free, otherwise fold.
        try:
            if game_state.get("can_check"):
                return {"action": "check"}
        except Exception:
            pass
        return {"action": "fold"}
