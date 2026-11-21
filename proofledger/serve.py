"""Evaluator process: the environment runs here, the agent talks to it only through JSON lines.

python -m proofledger.serve --mode closed --log evaluator_log.jsonl [--allow-oracle]

Requests (one JSON object per line on stdin):
  {"op": "reset", "seed": 7, "config": {...}}  -> {"ok": true, "observation": ..., "info": ...}
  {"op": "step", "action": {...}}              -> {"ok": true, "observation", "reward", "terminated", "truncated", "info"}
  {"op": "oracle"}                             -> oracle findings, only with --allow-oracle (evaluator's own runs)
  {"op": "close"}
Truth, seeds and diagnostics stay in this process. When an episode ends, the evaluator report is
appended to --log (the evaluator journal); in closed mode it is never sent to the agent.
RemoteEnvironment is a drop-in client with the same reset/step methods as Environment.
"""
import argparse
import json
import subprocess
import sys

from .env import Environment


def serve(mode="closed", log=None, allow_oracle=False, inp=sys.stdin, out=sys.stdout):
    env, logged = Environment(mode), None
    for line in inp:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError("request must be an object")
            op = req.get("op")
            if op == "reset":
                obs, info = env.reset(req.get("seed"), req.get("config"))
                logged = None
                resp = {"ok": True, "observation": obs, "info": info}
            elif op == "step":
                obs, r, term, trunc, info = env.step(req.get("action"))
                resp = {"ok": True, "observation": obs, "reward": r, "terminated": term,
                        "truncated": trunc, "info": info}
                rep = env.final_report()
                if rep is not None and logged != info.get("episode_id") and log:
                    with open(log, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"episode_id": info["episode_id"], **rep}) + "\n")
                    logged = info.get("episode_id")
            elif op == "oracle" and allow_oracle:
                resp = {"ok": True, "findings": env._oracle_findings()}
            elif op == "close":
                out.write(json.dumps({"ok": True}) + "\n")
                out.flush()
                return
            else:
                resp = {"ok": False, "error": "bad_request"}
        except (ValueError, TypeError, KeyError, RuntimeError) as e:
            resp = {"ok": False, "error": "bad_request", "message": type(e).__name__}
        out.write(json.dumps(resp) + "\n")
        out.flush()


class RemoteEnvironment:
    """Client: same reset/step contract, environment and truth live in a child process."""

    def __init__(self, mode="closed", log=None, allow_oracle=False):
        cmd = [sys.executable, "-m", "proofledger.serve", "--mode", mode]
        if log:
            cmd += ["--log", str(log)]
        if allow_oracle:
            cmd.append("--allow-oracle")
        self._p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def _call(self, req):
        self._p.stdin.write(json.dumps(req) + "\n")
        self._p.stdin.flush()
        resp = json.loads(self._p.stdout.readline())
        if not resp.get("ok"):
            raise RuntimeError(resp)
        return resp

    def reset(self, seed, config=None):
        r = self._call({"op": "reset", "seed": seed, "config": config})
        return r["observation"], r["info"]

    def step(self, action):
        r = self._call({"op": "step", "action": action})
        return r["observation"], r["reward"], r["terminated"], r["truncated"], r["info"]

    def _oracle_findings(self):
        return self._call({"op": "oracle"})["findings"]

    def close(self):
        if self._p.poll() is None:
            try:
                self._call({"op": "close"})
            except (RuntimeError, ValueError, BrokenPipeError):
                pass
            self._p.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="closed", choices=["train", "closed"])
    ap.add_argument("--log", default=None)
    ap.add_argument("--allow-oracle", action="store_true")
    a = ap.parse_args(argv)
    serve(a.mode, a.log, a.allow_oracle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
