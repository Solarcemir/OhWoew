#!/usr/bin/env python3
"""Answer the current relay decision and/or wait for the next one (see tools/relay.py)."""

import json
import sys
import time
from pathlib import Path


def wait_pending(d, after_id, timeout):
    end = time.time() + timeout
    while time.time() < end:
        p = d / "pending.json"
        if p.exists():
            try:
                s = json.loads(p.read_text())
                if s.get("id", 0) > after_id:
                    return s
            except Exception:
                pass
        time.sleep(0.2)
    return None


def main():
    d = Path(sys.argv[1])
    cmd = sys.argv[2]
    last = 0
    if cmd == "answer":
        cur = json.loads((d / "pending.json").read_text())
        a = json.loads(sys.argv[3])
        a["id"] = cur["id"]
        (d / "answer.json").write_text(json.dumps(a))
        last = cur["id"]
    s = wait_pending(d, last, float(sys.argv[4]) if len(sys.argv) > 4 else 120)
    print(json.dumps(s) if s else "NO PENDING DECISION (match over?)")


if __name__ == "__main__":
    main()
