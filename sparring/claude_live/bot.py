"""TEST-ONLY: a seat played live by an LLM (Claude in a Claude Code session).

Each decision is sent to tools/relay.py on 127.0.0.1:8765 and the reply is played.
Uses a socket, so it would fail the validator: it is never an entry.
Run with a long time limit, e.g. play.py ... --time-limit-ms 600000
"""

import json
import socket

POSTS = ("small_blind", "big_blind", "ante")


def _compact(gs):
    me = gs.get("your_bot_id")
    opp = next((p for p in gs.get("players", []) if p.get("bot_id") != me), {})
    pos = "SB/button" if gs.get("seat_to_act") == gs.get("sb_seat") else "BB"

    def who(e):
        return "me" if e.get("bot_id") == me else "opp"

    this_hand = [f"{e['street'][:4]}:{who(e)} {e['action']}{(' ' + str(e['amount'])) if e.get('amount') else ''}"
                 for e in gs.get("action_log", []) if e.get("action") not in ("ante",)]
    shows = [f"h{e['hand_num']} opp showed {' '.join(e['cards'])}"
             for e in gs.get("match_action_log", []) if e.get("action") == "show" and e.get("bot_id") != me]
    return {
        "hand": gs.get("hand_num"), "street": gs.get("street"), "pos": pos,
        "cards": gs.get("your_cards"), "board": gs.get("community_cards"),
        "pot": gs.get("pot"), "owed": gs.get("amount_owed"), "can_check": gs.get("can_check"),
        "min_raise_to": gs.get("min_raise_to"), "my_bet_street": gs.get("your_bet_this_street"),
        "my_stack": gs.get("your_stack"), "opp_stack": opp.get("stack"), "opp_bet_street": opp.get("bet_this_street"),
        "blinds": gs.get("blinds"), "this_hand": this_hand, "recent_opp_showdowns": shows[-3:],
    }


def decide(gs):
    if gs.get("type") == "warmup":
        return {"action": "check"}
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=900) as s:
            s.sendall((json.dumps(_compact(gs)) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode())
    except Exception as e:  # noqa: BLE001
        return {"action": "check" if gs.get("can_check") else "fold", "debug": {"relay_error": repr(e)}}
