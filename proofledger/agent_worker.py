"""JSON-lines adapter for the bundled Reconciler, never an arbitrary-code loader.

The trusted runner owns the episode and seed. This process receives only public
observations and chooses actions. Process separation here is not an OS sandbox.
"""
import json
import sys

from .policies import Reconciler

MAX_LINE = 2 * 1024 * 1024


def serve(inp=None, out=None):
    inp, out = inp or sys.stdin, out or sys.stdout
    policy, ready = Reconciler(), False
    while True:
        line = inp.readline(MAX_LINE + 1)
        if not line:
            return 0
        if len(line) > MAX_LINE:
            return 2
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            operation = request.get("op")
            if operation == "reset":
                observation, info = request.get("observation"), request.get("info")
                if not isinstance(observation, dict) or not isinstance(info, dict):
                    raise ValueError("invalid reset")
                policy.reset(observation, info)
                ready = True
                response = {"ok": True}
            elif operation == "act" and ready:
                observation = request.get("observation")
                if not isinstance(observation, dict):
                    raise ValueError("invalid observation")
                response = {"ok": True, "action": policy.act(observation)}
            elif operation == "close":
                return 0
            else:
                raise ValueError("unsupported operation")
        except (ValueError, TypeError, KeyError, IndexError):
            response = {"ok": False, "error": "invalid_agent_request"}
        out.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
        out.flush()


if __name__ == "__main__":
    raise SystemExit(serve())
