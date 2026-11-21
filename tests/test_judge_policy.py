"""A closed judge and a WASI guest exchange only public observations and actions."""
from contextlib import closing
import threading
import time

import pytest

import judge_adapter
from proofledger.live_runner import PolicyProcess, RUNTIME_START_LIMIT_S, RUN_LIMIT_S
from proofledger.upload_runtime import runtime_available


# This fixture has one page of invoices, one company timezone and one duplicate.
DUPLICATE_POLICY = """
class Agent:
    def reset(self, observation, info):
        self.episode_id = info["episode_id"]

    def act(self, observation):
        if "manifest" in observation:
            return {"name": "list_records", "args": {"table": "invoices", "limit": 50, "cursor": None}}
        seen, findings = {}, []
        for invoice in observation["result"]["records"]:
            key = (invoice["entity_id"], invoice["source_invoice_number"])
            if key in seen:
                original = seen[key]
                findings.append({
                    "type": "duplicate_invoice", "affected_ids": [original["invoice_id"], invoice["invoice_id"]],
                    "claimed_amount_minor": invoice["total_minor"], "currency": invoice["currency"],
                    "evidence_ids": [original["doc_id"], invoice["doc_id"]],
                    "explanation": "Both records carry the same primary invoice number for this entity.",
                })
            seen[key] = invoice
        return {"name": "submit", "args": {"findings": {"episode_id": self.episode_id, "findings": findings}}}
"""


@pytest.mark.skipif(not runtime_available(), reason="WASI policy runtime is unavailable")
def test_closed_judge_scores_separate_wasi_policy(tmp_path):
    config = {"duplicate_invoice": 1, "n_invoices": 10, "recurring_entities": 0,
              "mixed_timezones": False, "boundary_distractors": 0}
    judge_adapter.generate_episode(7, config, tmp_path)
    judge = judge_adapter.make_env()
    deadline = time.monotonic() + RUN_LIMIT_S
    with closing(PolicyProcess(threading.Event(), deadline, uploaded=True)) as policy:
        policy.call({"op": "ready"}, timeout=RUNTIME_START_LIMIT_S)
        policy.call({"op": "load", "source": DUPLICATE_POLICY})
        obs, info = judge.reset(7, {"mode": "closed", "episode_dir": str(tmp_path)})
        policy.call({"op": "reset", "observation": obs, "info": info})
        request = policy.call({"op": "act", "observation": obs})["action"]
        assert request["name"] == "list_records"
        obs, reward, done, truncated, _ = judge.step(request)
        assert (reward, done, truncated) == (0, False, False)
        assert obs["error"] is None and obs["result"]["next_cursor"] is None
        submit = policy.call({"op": "act", "observation": obs})["action"]
        assert submit["name"] == "submit"
        obs, reward, done, truncated, _ = judge.step(submit)
        assert (obs["result"], reward, done, truncated) == ({"accepted": True}, 0, True, False)

    score = judge_adapter.score_episode(str(tmp_path), submit["args"]["findings"], inspection_steps=1)
    assert score == {"tp": 1, "fp": 0, "fn": 0, "precision": 1, "recall": 1,
                     "f1": 1, "return": 0.998}
