"""Environment with a JSON-compatible, Gymnasium-style contract (no Env subclass needed).

reset(seed, config) -> (obs, info)
step(action)        -> (obs, reward, terminated, truncated, info)

Actions are flat dicts: {"action": "list_records", "table", "cursor", "limit"},
{"action": "get_record", "table", "id"}, {"action": "get_document", "doc_id"},
{"action": "calculator", "expression"}, {"action": "submit", "findings": {...}}.
"""
import copy
import json
import random
import time
from pathlib import Path

from . import matcher
from .calculator import CalcError, calculate
from .generator import VERSION, generate_episode, read_episode

MAX_STEPS = 100
TIME_LIMIT_S = 60.0
PAGE_MAX = 50
TABLES = {"ledger": "row_id", "invoices": "invoice_id", "payments": "payment_id", "fx": "key"}

TASK = (
    "You are auditing one fictional company for one reporting period. Investigate the records "
    "with list_records, get_record, get_document and calculator, then call submit once with "
    "findings.json {episode_id, findings:[{type, affected_ids, claimed_amount_minor, currency, "
    "evidence_ids, explanation}]}. Allowed finding types: duplicate_invoice, payment_allocation, "
    "fx_error, period_error. Finding conventions are in policy.finding_conventions. Limits: 30 "
    "findings, 10 evidence ids, 1000 characters per explanation, 100 steps including submit.")


_SCHEMAS = Path(__file__).parent / "schemas"


class Environment:
    """Gymnasium-style environment (JSON values, no numpy needed).

    Follows gymnasium.Env conventions: reset(seed=..., options=...) -> (obs, info);
    step(action) -> (obs, reward, terminated, truncated, info); metadata, render_mode, render(),
    close(), unwrapped, spec. action_space / observation_space are JSON Schemas (dicts);
    proofledger.gym_adapter wraps this class as a real gymnasium.Env when gymnasium is installed.
    """
    metadata = {"render_modes": ["ansi"], "render_fps": 4}
    spec = None

    def __init__(self, mode="train", clock=time.monotonic, render_mode=None):
        if mode not in ("train", "closed"):
            raise ValueError("mode must be 'train' or 'closed'")
        if render_mode not in (None, "ansi"):
            raise ValueError("render_mode must be None or 'ansi'")
        self.mode, self._clock, self.render_mode = mode, clock, render_mode
        self._ep, self._seed_rng = None, None
        self.action_space = json.loads((_SCHEMAS / "action.schema.json").read_text(encoding="utf-8"))
        self.observation_space = json.loads((_SCHEMAS / "observation.schema.json").read_text(encoding="utf-8"))

    @property
    def unwrapped(self):
        return self

    # ------------------------------------------------------------------ reset
    def reset(self, seed=None, config=None, *, options=None):
        """Start an episode. Call as reset(seed, config) (case contract) or
        reset(seed=..., options=...) (Gymnasium). An integer seed is fully reproducible; seed=None
        continues a seed sequence derived from the last integer seed (or from entropy if none).
        config/options {"episode_path": dir} loads an episode from files (evaluator use)."""
        started = self._clock()
        from .levels import expand
        config = options if config is None else config
        if seed is None:
            if self._seed_rng is None:
                self._seed_rng = random.Random()
            seed = self._seed_rng.randrange(2 ** 62)
        else:
            self._seed_rng = random.Random(f"next-seed|{seed}")
        if config and "episode_path" in config:
            ep = read_episode(config["episode_path"])
            if "truth" not in ep:
                raise ValueError("episode_path needs truth.json for scoring")
        else:
            ep = generate_episode(seed, expand(config, seed))
        self._ep = ep
        self._issues = matcher.scoring_issues(ep)  # evaluator side only
        self._evidence_ids = matcher.public_evidence_ids(ep)
        fx = [dict(r, key=f"{r['date']}|{r['source_currency']}|{r['target_currency']}") for r in ep["fx"]]
        self._tables = {
            "ledger": sorted(ep["ledger"], key=lambda r: r["row_id"]),
            "invoices": sorted(ep["invoices"], key=lambda r: r["invoice_id"]),
            "payments": sorted(ep["payments"], key=lambda r: r["payment_id"]),
            "fx": sorted(fx, key=lambda r: r["key"]),
        }
        self._index = {t: {r[TABLES[t]]: r for r in rows} for t, rows in self._tables.items()}
        self._docs = {r["doc_id"]: (kind, r) for kind, rows in
                      (("invoice", ep["invoices"]), ("payment", ep["payments"])) for r in rows}
        self._steps, self._done, self._terminated, self._truncated = 0, False, False, False
        self._report, self._t0 = None, started
        self.trajectory = []
        obs = {"task": TASK, "manifest": copy.deepcopy(ep["manifest"]),
               "policy": copy.deepcopy(ep["policy"]), "result": None, "error": None,
               "steps_remaining": MAX_STEPS}
        info = {"episode_id": ep["manifest"]["episode_id"], "version": VERSION}
        self._log(None, obs, 0.0, False, False)
        return obs, info

    # ------------------------------------------------------------------- step
    def step(self, action):
        if self._ep is None:
            raise RuntimeError("call reset() first")
        eid = self._ep["manifest"]["episode_id"]
        if self._done:
            obs = {"result": None, "error": {"code": "episode_done"}, "steps_remaining": 0}
            return obs, 0.0, self._terminated, self._truncated, {"episode_id": eid}
        # Enforce the budget before accepting another action, even if an earlier
        # unexpected exception interrupted the normal end-of-step path.
        if self._steps >= MAX_STEPS:
            return self._truncate("step_limit", action, self._steps)
        self._steps += 1
        remaining = MAX_STEPS - self._steps

        is_submit = isinstance(action, dict) and action.get("action") == "submit"
        if self._clock() - self._t0 > TIME_LIMIT_S:
            # A late submit uses one action slot, but never counts as inspection.
            inspected = self._steps - 1 if is_submit else self._steps
            return self._truncate("time_limit", action, inspected)

        if is_submit:
            return self._submit(action, remaining)

        result, error = self._dispatch(action)
        if self._steps >= MAX_STEPS:
            return self._truncate("step_limit", action, self._steps)
        obs = {"result": result, "error": error, "steps_remaining": remaining}
        info = {"episode_id": eid, "step": self._steps}
        self._log(action, obs, 0.0, False, False)
        return obs, 0.0, False, False, info

    # ---------------------------------------------------------------- helpers
    def _err(self, code, msg=None):
        e = {"code": code}
        if msg:
            e["message"] = msg
        return None, e

    def _dispatch(self, a):
        if not isinstance(a, dict):
            return self._err("invalid_action")
        name = a.get("action")
        if name == "list_records":
            t, cur, lim = a.get("table"), a.get("cursor"), a.get("limit", PAGE_MAX)
            if not isinstance(t, str) or t not in TABLES:
                return self._err("unknown_table")
            if not isinstance(lim, int) or isinstance(lim, bool) or lim < 1:
                return self._err("invalid_limit")
            rows = self._tables[t]
            try:
                off = 0 if cur is None else int(cur)
            except (TypeError, ValueError, OverflowError):
                return self._err("invalid_cursor")
            if off < 0 or off > len(rows):
                return self._err("invalid_cursor")
            page = rows[off:off + min(lim, PAGE_MAX)]
            nxt = off + len(page)
            return {"records": copy.deepcopy(page),
                    "next_cursor": str(nxt) if nxt < len(rows) else None}, None
        if name == "get_record":
            t, i = a.get("table"), a.get("id")
            if not isinstance(t, str) or t not in TABLES:
                return self._err("unknown_table")
            rec = self._index[t].get(i) if isinstance(i, str) else None
            return ({"record": copy.deepcopy(rec)}, None) if rec else self._err("unknown_id")
        if name == "get_document":
            d = a.get("doc_id")
            hit = self._docs.get(d) if isinstance(d, str) else None
            if not hit:
                return self._err("unknown_id")
            return {"doc_type": hit[0], "document": copy.deepcopy(hit[1])}, None
        if name == "calculator":
            try:
                return {"value": calculate(a.get("expression"))}, None
            except CalcError as e:
                return self._err(e.code)
        return self._err("unknown_action")

    def _finish(self, action, obs, reward, terminated, truncated, report):
        self._done, self._terminated, self._truncated = True, terminated, truncated
        self._report = report
        shown = reward if self.mode == "train" else 0.0
        info = {"episode_id": self._ep["manifest"]["episode_id"], "step": self._steps}
        if self.mode == "train":
            info["terminal"] = {k: report[k] for k in ("reward", "f1", "tp", "fp", "fn")}
        self._log(action, obs, shown, terminated, truncated)
        return obs, shown, terminated, truncated, info

    def _submit(self, action, remaining):
        inspected = self._steps - 1
        metrics, err = matcher.score_submission(self._ep, action.get("findings"), inspected, self._evidence_ids)
        reward = metrics.pop("return")
        report = {**metrics, "reward": reward, "error": err, "steps": self._steps,
                  "inspection_steps": inspected}
        if err:
            e = {"code": "invalid_submission"}
            if self.mode == "train":
                e["message"] = err
            obs = {"result": None, "error": e, "steps_remaining": remaining}
        else:
            obs = {"result": {"submitted": True}, "error": None, "steps_remaining": remaining}
        return self._finish(action, obs, reward, True, False, report)

    def _truncate(self, reason, action, inspection_steps):
        ev = matcher.evaluate(self._issues, [], self._evidence_ids)
        reward = matcher.terminal_reward(ev["f1"], inspection_steps)
        report = {**ev, "reward": reward, "error": reason, "steps": self._steps,
                  "inspection_steps": inspection_steps}
        obs = {"result": None, "error": {"code": "truncated", "reason": reason},
               "steps_remaining": max(0, MAX_STEPS - self._steps)}
        return self._finish(action, obs, reward, False, True, report)

    def _log(self, action, obs, reward, terminated, truncated):
        self.trajectory.append({
            "episode_id": self._ep["manifest"]["episode_id"], "step": self._steps,
            "action": copy.deepcopy(action), "observation": copy.deepcopy(obs),
            "reward": reward, "terminated": terminated, "truncated": truncated})

    # ------------------------------------------------------- render / close
    def render(self):
        """ansi mode: a short text status of the current episode (no closed data)."""
        if self.render_mode != "ansi" or self._ep is None:
            return None
        last = self.trajectory[-1] if self.trajectory else None
        m = self._ep["manifest"]
        state = "terminated" if self._terminated else "truncated" if self._truncated else "running"
        lines = [f"ProofLedger {m['episode_id']} period {m['period_start']}..{m['period_end']} tz {m['timezone']}",
                 f"step {self._steps}/{MAX_STEPS} {state}"]
        if last and last["action"] is not None:
            lines.append(f"last action: {json.dumps(last['action'])[:120]}")
            err = last["observation"].get("error")
            lines.append("last result: " + (f"error {err['code']}" if err else "ok"))
        return "\n".join(lines) + "\n"

    def close(self):
        """Nothing to release; safe to call more than once."""
        return None

    # -------------------------------------------------------- evaluator side
    def final_report(self):
        """Evaluator-only diagnostics; None until the episode has ended."""
        return copy.deepcopy(self._report)

    def _oracle_findings(self):
        """Evaluator-only: correct findings with evidence, read from the closed truth."""
        return {"episode_id": self._ep["manifest"]["episode_id"], "findings": [
            {"type": i["type"], "affected_ids": list(i["affected_ids"]),
             "claimed_amount_minor": i["expected_amount_minor"], "currency": i["currency"],
             "evidence_ids": list(i["evidence_sets"][0]), "explanation": "oracle"}
            for i in self._issues]}
