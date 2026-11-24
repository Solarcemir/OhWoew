#!/usr/bin/env python3
"""Play heads-up against our bot in the browser, on the kit's own engine and table.

  python tools/play_web.py                         # opens http://127.0.0.1:8800
  python tools/play_web.py --bot sparring/our_v17 --port 8800 --no-browser

The match is the tournament format (freezeout, standard rising blinds, 10,000 chips).
You sit in a human seat; the bot runs exactly as in the tournament (its own process).
The browser never receives the bot's hole cards before a showdown.
"""

import argparse
import json
import os
import queue
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if os.name == "nt":  # Python on Windows can't start without these (guard.safe_env drops them)
    os.environ.setdefault("BOT_ENV_ALLOW", "SYSTEMROOT,WINDIR")

from pokercore import structures  # noqa: E402
from pokercore.seats import BotSeat, Seat  # noqa: E402
from pokercore.seeds import derive_seed, new_seed  # noqa: E402
from pokercore.table import MatchConfig, play_match  # noqa: E402

HUMAN, BOT = "you", "ohwow"
WEB = Path(__file__).resolve().parent / "web" / "index.html"


@dataclass
class BotSpec:
    name: str
    path: str
    hash: str = None
    source: str = "local"


class Abort(Exception):
    pass


class HumanSeat(Seat):
    kind = "human"

    def __init__(self, game):
        super().__init__(HUMAN, 10 ** 9)
        self.game = game

    def request_action(self, state):
        g = self.game
        with g.lock:
            g.pending = state
        while True:
            try:
                a = g.actions.get(timeout=0.5)
            except queue.Empty:
                if g.aborted:
                    raise Abort()
                continue
            break
        with g.lock:
            g.pending = None
        if a is None:
            raise Abort()
        return a


class Game:
    """One match in a background thread; it is also the table's event log."""

    def __init__(self, bot_path):
        self.lock = threading.Lock()
        self.events = []
        self.pending = None
        self.actions = queue.Queue()
        self.aborted = False
        self.over = False
        self.seed = new_seed()
        self.bot_path = bot_path
        threading.Thread(target=self._run, daemon=True).start()

    def emit(self, event_type, **data):
        with self.lock:
            event = {"i": len(self.events), "type": event_type, **data}
            self.events.append(event)
        return event

    def close(self):
        pass

    def _run(self):
        seats = [HumanSeat(self), BotSeat(BotSpec(BOT, self.bot_path), seed=derive_seed(self.seed, "bot", BOT))]
        config = MatchConfig(mode="freezeout", hands=2000, starting_stack=10_000,
                             structure=structures.parse("standard", 10_000), seed=self.seed)
        try:
            play_match(seats, config, "web", self)
        except Abort:
            pass
        except Exception as e:  # noqa: BLE001
            self.emit("error", message=repr(e))
        finally:
            self.over = True

    def abort(self):
        self.aborted = True
        self.actions.put(None)

    # what the browser may see
    def view(self, since):
        with self.lock:
            evs = self.events[since:]
            pending = self.pending
        out = []
        for e in evs:
            t = e["type"]
            if t == "hand_start":
                e = {**e, "hole": {HUMAN: e["hole"].get(HUMAN)}}         # never the bot's cards
            elif t == "decision":
                e = {"i": e["i"], "type": t, "hand": e["hand"], "bot": e["bot"], "street": e["street"],
                     "applied": e["applied"]}
            elif t == "match_start":
                e = {"i": e["i"], "type": t, "seed": e.get("seed")}
            elif t == "match_end":
                e = {"i": e["i"], "type": t, "hands": e.get("hands"),
                     "results": [{"bot": r["bot"], "place": r["place"], "final_stack": r["final_stack"]}
                                 for r in e.get("results", [])]}
            out.append(e)
        p = None
        if pending is not None:
            keys = ("hand_num", "street", "your_cards", "community_cards", "pot", "amount_owed", "can_check",
                    "min_raise_to", "current_bet", "your_stack", "your_bet_this_street", "blinds")
            p = {k: pending.get(k) for k in keys}
            p["players"] = [{"bot_id": q.get("bot_id"), "stack": q.get("stack"), "bet": q.get("bet_this_street")}
                            for q in pending.get("players", [])]
        return {"game": self.seed, "events": out, "pending": p, "over": self.over, "next": since + len(out)}


GAME = {"g": None, "bot": None}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, WEB.read_bytes(), "text/html; charset=utf-8")
        if self.path.startswith("/api/state"):
            since = 0
            if "since=" in self.path:
                try:
                    since = int(self.path.split("since=")[1].split("&")[0])
                except ValueError:
                    since = 0
            return self._send(200, GAME["g"].view(since))
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        g = GAME["g"]
        if self.path == "/api/action":
            if body.get("game") != g.seed:
                return self._send(409, {"error": "that action is for another match"})
            if g.pending is None:
                return self._send(409, {"error": "not your turn"})
            a = {"action": body.get("action", "fold")}
            if a["action"] == "raise":
                try:
                    a["amount"] = int(body.get("amount", 0))
                except (TypeError, ValueError):
                    a["amount"] = 0
            g.actions.put(a)
            return self._send(200, {"ok": True})
        if self.path == "/api/new":
            g.abort()
            GAME["g"] = Game(GAME["bot"])
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bot", default=str(ROOT / "sparring" / "our_v17"), help="bot folder (default: our_v17)")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    GAME["bot"] = args.bot
    GAME["g"] = Game(args.bot)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"ohwow table running at {url}  (bot: {args.bot})  Ctrl+C to stop", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
