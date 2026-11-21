"""Baseline policy adapters.

Every policy has the same interface, independent of this package's internals:

    policy.reset(first_observation, info)
    action = policy.act(observation)        # JSON action dict

`run(policy, env, seed, config)` drives one episode. The four baselines required by the case
are RandomExplorer, Reconciler, FlagAll and Oracle (evaluator only). Silence and NaiveReconciler
are extra diagnostics.
"""
import random

from . import analysis
from .env import Environment

ID_FIELD = {"ledger": "row_id", "invoices": "invoice_id", "payments": "payment_id", "fx": "key"}


def submit(episode_id, findings):
    return {"action": "submit", "findings": {"episode_id": episode_id, "findings": findings[:30]}}


class Silence:
    name = "silence"

    def reset(self, obs, info):
        self.eid = info["episode_id"]

    def act(self, obs):
        return submit(self.eid, [])


class Fetcher:
    """Reads whole tables page by page, then submits decide(data)."""
    tables = ("invoices", "payments", "ledger", "fx")

    def reset(self, obs, info):
        self.eid, self.manifest = info["episode_id"], obs["manifest"]
        self.data = {t: [] for t in self.tables}
        self.i, self.cursor, self.waiting = 0, None, False

    def act(self, obs):
        if self.waiting:
            self.waiting = False
            res = obs.get("result")
            if obs.get("error") or not res:
                self.i, self.cursor = self.i + 1, None
            else:
                self.data[self.tables[self.i]] += res["records"]
                self.cursor = res["next_cursor"]
                if self.cursor is None:
                    self.i += 1
        if self.i < len(self.tables):
            self.waiting = True
            return {"action": "list_records", "table": self.tables[self.i], "cursor": self.cursor, "limit": 50}
        return submit(self.eid, self.decide())

    def decide(self):
        return []

    def honest(self, naive=False):
        d = self.data
        return analysis.detect_all(self.manifest, d.get("invoices", []), d.get("payments", []),
                                   d.get("ledger", []), d.get("fx", []), naive=naive)


class Reconciler(Fetcher):
    """Simple reconciliation: read every table once, apply the public policy rules, submit."""
    name = "reconciler"

    def decide(self):
        return self.honest()


class NaiveReconciler(Fetcher):
    """Diagnostic: same rules, but reads dates as printed and ignores timezone offsets."""
    name = "naive_reconciler"

    def decide(self):
        return self.honest(naive=True)


class FlagAll(Fetcher):
    """Attack: accuse as many records as allowed, of every type, with unproven findings."""
    name = "flag_all"
    tables = ("invoices", "payments")

    def decide(self):
        out = []
        for n, r in enumerate(self.data["invoices"][:22]):
            t = ("duplicate_invoice", "fx_error", "period_error")[n % 3]
            out.append({"type": t, "affected_ids": [r["invoice_id"]],
                        "claimed_amount_minor": r["total_minor"], "currency": r["currency"],
                        "evidence_ids": [r["doc_id"]], "explanation": "suspicious"})
        for r in self.data["payments"][:8]:
            out.append({"type": "payment_allocation", "affected_ids": [r["payment_id"]],
                        "claimed_amount_minor": r["amount_minor"], "currency": "KZT",
                        "evidence_ids": [r["doc_id"]], "explanation": "suspicious"})
        return out


class RandomExplorer:
    """Random actions with a fixed seed, then an empty submission."""
    name = "random_explorer"

    def __init__(self, seed=0):
        self.seed = seed

    def reset(self, obs, info):
        self.rng = random.Random(f"explorer|{self.seed}")
        self.eid, self.left, self.last = info["episode_id"], None, None
        self.left = self.rng.randint(5, 25)
        self.ids = {"ledger": ["L00001"], "invoices": ["INV-00000"], "payments": ["PAY-00000"], "fx": ["x"]}

    def act(self, obs):
        if self.last and obs.get("result") and obs["result"].get("records"):
            self.ids[self.last] = [r[ID_FIELD[self.last]] for r in obs["result"]["records"]]
        self.last = None
        if self.left <= 0:
            return submit(self.eid, [])
        self.left -= 1
        rng = self.rng
        kind, t = rng.choice(["list", "get", "doc", "calc"]), rng.choice(list(self.ids))
        if kind == "list":
            self.last = t
            return {"action": "list_records", "table": t, "limit": rng.randint(1, 60)}
        if kind == "get":
            return {"action": "get_record", "table": t, "id": rng.choice(self.ids[t])}
        if kind == "doc":
            return {"action": "get_document", "doc_id": "A-000000"}
        return {"action": "calculator", "expression": f"{rng.randint(1, 99)}*{rng.randint(1, 99)}/7"}


class Oracle:
    """Evaluator-only: submits the closed truth with evidence. Checks the reward maximum."""
    name = "oracle"

    def __init__(self, env):
        self.env = env

    def reset(self, obs, info):
        pass

    def act(self, obs):
        return {"action": "submit", "findings": self.env._oracle_findings()}


BASELINES = ("oracle", "reconciler", "random_explorer", "flag_all", "silence")
POLICIES = BASELINES


def make(name, env, policy_seed=0):
    from . import attacks
    table = {"reconciler": Reconciler, "naive_reconciler": NaiveReconciler, "flag_all": FlagAll,
             "silence": Silence, **attacks.ATTACKS}
    if name == "oracle":
        return Oracle(env)
    if name == "random_explorer":
        return RandomExplorer(policy_seed)
    return table[name]()


def run(policy, env, seed, config=None):
    obs, info = env.reset(seed, config)
    policy.reset(obs, info)
    for _ in range(1000):
        obs, _, term, trunc, _ = env.step(policy.act(obs))
        if term or trunc:
            break
    return env.final_report()


def run_policy(name, seed, config=None, mode="train", policy_seed=0):
    env = Environment(mode=mode)
    report = run(make(name, env, policy_seed), env, seed, config)
    return {"report": report, "trajectory": env.trajectory, "env": env}
