"""HTTP regressions for uploaded agents and their real isolated execution.

Registration/HTTP validation tests do not need a WASI runtime. Execution tests
skip only when the optional host runtime is unavailable; the Windows x64 bundle
ships it. No external service or network connection is used by these tests.
"""
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from proofledger.live import LiveServer
from proofledger.live_runner import MAX_SOURCE_BYTES


EXAMPLES = Path(__file__).resolve().parents[1] / "proofledger" / "examples"
FILENAMES = ("baseline_silence.py", "audit_duplicates.py", "audit_allocation.py")


@pytest.fixture
def upload_server():
    server = LiveServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.fixture
def sandbox_runtime():
    from proofledger.upload_runtime import runtime_available

    if not runtime_available():
        pytest.skip("WASI execution runtime is not installed on this host")


def http(base, path, body=None, *, method=None):
    data = None if body is None else json.dumps(body, ensure_ascii=True).encode("utf-8")
    request = Request(base + path, data=data, method=method,
                      headers={"Content-Type": "application/json"})
    try:
        response = urlopen(request, timeout=15)
    except HTTPError as error:
        return error.code, error.read(), error.headers
    with response:
        return response.status, response.read(), response.headers


def json_http(base, path, body=None, *, method=None):
    code, raw, headers = http(base, path, body, method=method)
    return code, json.loads(raw), headers


def upload(base, filename, source=None):
    if source is None:
        source = (EXAMPLES / filename).read_text(encoding="utf-8")
    code, agent, _ = json_http(base, "/api/agents", {"filename": filename, "source": source})
    assert code == 201, agent
    return agent


def start(base, agent_id, **config):
    code, response, _ = json_http(base, "/api/runs", {
        "agent_id": agent_id, "level": "L5", "seed": 7, "mode": "closed", **config})
    assert code == 201, response
    return response["run_id"]


def poll(base, run_id, *, timeout=75):
    events, cursor = [], 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, snapshot, _ = json_http(base, f"/api/runs/{run_id}?after={cursor}")
        assert code == 200, snapshot
        events.extend(snapshot["events"])
        cursor = snapshot["last_seq"]
        if snapshot["status"] != "running":
            return snapshot, events
        # Neither final evaluation nor the final findings may be published
        # while a closed run is still active.
        assert snapshot["report"] is None
        assert snapshot["findings"] == []
        time.sleep(0.025)
    pytest.fail(f"Uploaded run {run_id} did not finish within {timeout} seconds")


def test_examples_are_downloadable_and_source_paths_are_not(upload_server):
    _, base = upload_server
    code, metadata, _ = json_http(base, "/api/meta")
    assert code == 200
    entries = {item["filename"]: item for item in metadata["examples"]}
    assert set(FILENAMES) <= set(entries)
    for filename in FILENAMES:
        code, source, headers = http(base, entries[filename]["url"])
        assert code == 200
        assert source == (EXAMPLES / filename).read_bytes()
        assert filename in headers["Content-Disposition"]
        assert "attachment" in headers["Content-Disposition"]
    for path in ("/api/examples/../env.py", "/api/examples/%2e%2e/env.py",
                 "/api/examples/missing.py", "/api/examples/audit_duplicates.py/extra"):
        assert http(base, path)[0] == 404


def test_identical_upload_reuses_identity_without_exposing_source(upload_server):
    _, base = upload_server
    original = (EXAMPLES / "baseline_silence.py").read_text(encoding="utf-8")
    first = upload(base, "same.py", original)
    repeated = upload(base, "same.py", original)
    assert repeated == first
    changed = upload(base, "same.py", original + "\n# changed source\n")
    assert changed["id"] != first["id"]
    _, metadata, _ = json_http(base, "/api/meta")
    agents = metadata["agents"]
    assert sum(agent["id"] == first["id"] for agent in agents) == 1
    assert {first["id"], changed["id"]} <= {agent["id"] for agent in agents}
    assert all("source" not in agent for agent in agents)


VALID_SOURCE = "class Agent:\n def reset(self, observation, info): pass\n def act(self, observation): return {}\n"


@pytest.mark.parametrize("payload", [
    [],
    {"filename": "agent.py", "source": ""},
    {"filename": "agent.py", "source": []},
    {"filename": "../agent.py", "source": VALID_SOURCE},
    {"filename": "agent.txt", "source": VALID_SOURCE},
    {"filename": "agent.py", "source": VALID_SOURCE, "episode_path": "private"},
    {"filename": "agent.py", "source": "class Agent(:\n pass"},
    {"filename": "agent.py", "source": "class WrongName:\n pass\n"},
    {"filename": "agent.py", "source": "class Agent:\n def reset(self, observation, info): pass\n"},
    {"filename": "agent.py", "source": "class Agent:\n def act(self, observation): return {}\n"},
    {"filename": "agent.py", "source": "#" + "x" * MAX_SOURCE_BYTES},
])
def test_upload_rejects_invalid_file_or_missing_contract(upload_server, payload):
    _, base = upload_server
    code, error, _ = json_http(base, "/api/agents", payload)
    assert code == 400
    assert isinstance(error.get("error"), str) and error["error"]
    _, metadata, _ = json_http(base, "/api/meta")
    assert [agent["id"] for agent in metadata["agents"]] == ["reconciler"]


@pytest.mark.parametrize("filename,tp,fn,steps,f1,reward", [
    ("baseline_silence.py", 0, 10, 1, 0.0, -1.0),
    ("audit_duplicates.py", 3, 7, 3, 6 / 13, -0.080923076923),
    ("audit_allocation.py", 2, 8, 10, 1 / 3, -0.351333333333),
])
def test_uploaded_examples_run_and_export_real_scores(
        upload_server, sandbox_runtime, filename, tp, fn, steps, f1, reward):
    _, base = upload_server
    agent = upload(base, filename)
    run_id = start(base, agent["id"])
    snapshot, events = poll(base, run_id)
    assert snapshot["status"] == "completed", snapshot
    assert snapshot["agent"] == agent["id"]
    assert snapshot["agent_name"] == filename
    assert snapshot["config"]["agent_id"] == agent["id"]
    report = snapshot["report"]
    assert (report["tp"], report["fp"], report["fn"], report["steps"]) == (tp, 0, fn, steps)
    assert report["f1"] == pytest.approx(f1)
    assert report["reward"] == pytest.approx(reward)
    assert report["error"] is None
    assert len(snapshot["findings"]) == tp
    assert [event["seq"] for event in events] == list(range(1, snapshot["last_seq"] + 1))
    assert [event["step"] for event in events] == list(range(steps + 1))
    assert all(event["reward"] == 0 for event in events)
    assert events[-1]["terminated"] and not events[-1]["truncated"]

    code, exported, headers = json_http(base, f"/api/runs/{run_id}/report")
    assert code == 200 and "attachment" in headers["Content-Disposition"]
    assert exported["run_id"] == run_id
    assert exported["agent"] == agent["id"] and exported["agent_name"] == filename
    assert exported["report"] == report
    assert exported["config"] == snapshot["config"]
    assert exported["status"] == "completed" and exported["elapsed_ms"] > 0

    code, raw, headers = http(base, f"/api/runs/{run_id}/trajectory")
    assert code == 200 and "attachment" in headers["Content-Disposition"]
    trajectory = [json.loads(line) for line in raw.splitlines()]
    assert len(trajectory) == steps + 1
    assert all(row["agent"] == agent["id"] and row["agent_name"] == filename for row in trajectory)
    assert trajectory[-1]["terminated"] and not trajectory[-1]["truncated"]
    observations = json.dumps([row["observation"] for row in trajectory])
    for forbidden in ('"truth"', '"provenance"', '"seed"', '"expected_amount_minor"', '"evidence_sets"'):
        assert forbidden not in observations


EXCEPTION_SOURCE = """class Agent:
    def reset(self, observation, info):
        pass
    def act(self, observation):
        raise RuntimeError('deliberate uploaded-agent failure')
"""


def test_judge_exception_does_not_block_next_run(upload_server, sandbox_runtime):
    _, base = upload_server
    agent = upload(base, "throws.py", EXCEPTION_SOURCE)
    snapshot, _ = poll(base, start(base, agent["id"]))
    assert snapshot["status"] == "failed", snapshot
    assert snapshot["report"] is None
    assert snapshot["error"]
    code, report, _ = json_http(base, f"/api/runs/{snapshot['run_id']}/report")
    assert code == 200 and report["status"] == "failed"
    assert report["agent"] == agent["id"] and report["agent_name"] == "throws.py"
    next_snapshot, _ = poll(base, start(base, "reconciler"))
    assert next_snapshot["status"] == "completed", next_snapshot
    assert next_snapshot["report"]["f1"] == 1


WAITING_SOURCE = """class Agent:
    def reset(self, observation, info):
        self.first = True
    def act(self, observation):
        if self.first:
            self.first = False
            return {'action': 'list_records', 'table': 'invoices', 'limit': 1}
        while True:
            pass
"""


def test_judge_code_can_be_cancelled_and_next_run_starts(upload_server, sandbox_runtime):
    _, base = upload_server
    agent = upload(base, "waiting.py", WAITING_SOURCE)
    run_id = start(base, agent["id"])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _, snapshot, _ = json_http(base, f"/api/runs/{run_id}")
        assert snapshot["status"] == "running", snapshot
        if snapshot["last_seq"] >= 2:
            break
        time.sleep(0.025)
    else:
        pytest.fail("Uploaded agent did not execute its first action")
    assert http(base, f"/api/runs/{run_id}/report")[0] == 409
    code, cancelled, _ = json_http(base, f"/api/runs/{run_id}/cancel", {}, method="POST")
    assert code == 200 and cancelled["status"] == "cancelled"
    finished, _ = poll(base, run_id)
    assert finished["report"] is None and finished["agent"] == agent["id"]
    following, _ = poll(base, start(base, "reconciler"))
    assert following["status"] == "completed", following
    assert following["report"]["f1"] == 1
