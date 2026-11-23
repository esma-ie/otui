"""A stand-in for julia/worker.jl that speaks the same protocol, for unit tests."""

import json
import sys

PREFIX = "@@OTUI "


def reply(d):
    print(PREFIX + json.dumps(d), flush=True)


print("some package banner on stdout", flush=True)
print("a warning on stderr", file=sys.stderr, flush=True)
reply({"id": 0, "ok": True, "event": "ready"})
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    rid, cmd = req.get("id"), req.get("cmd")
    if cmd == "quit":
        reply({"id": rid, "ok": True})
        break
    if cmd == "ping":
        reply({"id": rid, "ok": True})
    elif cmd == "echo":
        print("noise between replies", flush=True)
        reply({"id": rid, "ok": True, "args": req})
    elif cmd == "crash":
        sys.exit(3)
    else:
        reply({"id": rid, "ok": False, "error": f"unknown command: {cmd}"})
