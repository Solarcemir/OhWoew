#!/usr/bin/env python3
"""Relay for sparring/claude_live: hands each decision to a human or LLM through files.

  python tools/relay.py <dir>          # serve on 127.0.0.1:8765
  python tools/relay_ctl.py <dir> next             # wait for and print the next decision
  python tools/relay_ctl.py <dir> answer '{"action":"call"}'   # answer it, then print the next one

Files in <dir>: pending.json (the current decision, with an "id"), answer.json.
"""

import json
import socket
import sys
import time
from pathlib import Path


def main():
    d = Path(sys.argv[1])
    d.mkdir(parents=True, exist_ok=True)
    for f in ("pending.json", "answer.json"):
        (d / f).unlink(missing_ok=True)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 8765))
    srv.listen(4)
    print("relay listening on 127.0.0.1:8765", flush=True)
    n = 0
    while True:
        conn, _ = srv.accept()
        with conn:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                continue
            n += 1
            state = json.loads(buf.decode())
            state["id"] = n
            (d / "pending.json").write_text(json.dumps(state))
            ans = None
            while ans is None:
                p = d / "answer.json"
                if p.exists():
                    try:
                        a = json.loads(p.read_text())
                        if a.get("id") == n:
                            ans = a
                            try:
                                p.unlink()
                            except PermissionError:
                                pass
                    except Exception:
                        pass
                time.sleep(0.1)
            ans.pop("id", None)
            conn.sendall((json.dumps(ans) + "\n").encode())
            for _ in range(50):          # Windows: the reader may hold the file for a moment
                try:
                    (d / "pending.json").unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.05)


if __name__ == "__main__":
    main()
