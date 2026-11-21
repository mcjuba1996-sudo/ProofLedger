"""Live runner integration: real policy subprocess, public stream and HTTP bounds."""
import io
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from proofledger.agent_worker import serve
from proofledger.live import LiveServer
from proofledger.live_runner import LiveRun, PolicyProcess, RunCancelled, RunError, RunTimeout, RunManager, validate_config


def finish(run, timeout=10):
    run.join(timeout)
    value = run.snapshot()
    assert value["status"] != "running", value
    return value


@pytest.mark.parametrize("mode", ["demo", "closed"])
def test_live_reconciler_matches_and_exports(mode):
    manager = RunManager()
    try:
        run = manager.create({"level": "L4", "seed": 7, "mode": mode})
        snapshot = finish(run)
        assert snapshot["status"] == "completed", snapshot
        assert snapshot["report"]["f1"] == 1
        assert snapshot["findings"]
        assert snapshot["events"][0]["seq"] == 1
        assert snapshot["events"][0]["kind"] == "reset"
        assert [row["seq"] for row in snapshot["events"]] == list(range(1, snapshot["last_seq"] + 1))
        assert len(run.snapshot(after=2)["events"]) == snapshot["last_seq"] - 2
        assert snapshot["elapsed_ms"] > 0
        assert run._policy.process.poll() is not None
        assert run.export_report()["report"]["f1"] == 1
        rows = [json.loads(line) for line in run.export_trajectory().splitlines()]
        assert rows[-1]["terminated"]
        assert len(rows) == snapshot["last_seq"]
        assert all(event["reward"] == 0 for event in snapshot["events"][:-1])
        if mode == "closed":
            assert all(event["reward"] == 0 for event in snapshot["events"])
        observations = json.dumps([row["observation"] for row in rows])
        for forbidden in ('"truth"', '"provenance"', '"seed"', '"expected_amount_minor"', '"evidence_sets"'):
            assert forbidden not in observations
    finally:
        manager.close()


class WaitingPolicy:
    def __init__(self, cancelled, deadline):
        self.cancelled, self.closed = cancelled, False

    def call(self, request):
        while not self.cancelled.wait(0.005):
            pass
        raise RunCancelled("cancelled")

    def close(self):
        self.closed = True


class FailingPolicy:
    def __init__(self, cancelled, deadline):
        self.closed = False

    def call(self, request):
        raise RunError("deliberate protocol error")

    def close(self):
        self.closed = True


def test_cancel_busy_run_and_cleanup():
    manager = RunManager(policy_factory=WaitingPolicy)
    try:
        run = manager.create({})
        with pytest.raises(RunError):
            manager.create({})
        deadline = time.monotonic() + 5
        while run._policy is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert run.snapshot()["report"] is None
        assert run.snapshot()["findings"] == []
        assert run.cancel()["status"] == "cancelled"
        assert finish(run)["report"] is None
        assert run._policy.closed
    finally:
        manager.close()


@pytest.mark.parametrize("mode", ["demo", "closed"])
def test_external_watchdog_scores_empty_answer_and_stops_nonresponsive_policy(mode):
    manager = RunManager(policy_factory=WaitingPolicy, run_limit=0.3)
    try:
        run = manager.create({"level": "L4", "seed": 7, "mode": mode})
        snapshot = finish(run)
        assert snapshot["status"] == "completed"
        assert snapshot["report"]["error"] == "time_limit"
        assert snapshot["report"]["inspection_steps"] == 0
        assert snapshot["report"]["f1"] == 0
        assert snapshot["report"]["fn"] > 0
        assert snapshot["findings"] == []
        final = snapshot["events"][-1]
        assert final["truncated"] and not final["terminated"]
        assert final["step"] == 1 and final["action"] is None
        assert final["observation"]["error"]["reason"] == "time_limit"
        assert final["reward"] == (0 if mode == "closed" else -1)
        assert json.loads(run.export_trajectory().splitlines()[-1])["truncated"]
        assert run._policy.closed
    finally:
        manager.close()


def test_per_action_timeout_is_also_scored_as_truncation():
    class TimedOutPolicy(FailingPolicy):
        def call(self, request):
            raise RunTimeout("action timeout")

    manager = RunManager(policy_factory=TimedOutPolicy)
    try:
        run = manager.create({"level": "L4", "seed": 7, "mode": "closed"})
        snapshot = finish(run)
        assert snapshot["status"] == "completed"
        assert snapshot["report"]["error"] == "time_limit"
        assert snapshot["report"]["f1"] == 0
        assert snapshot["events"][-1]["truncated"]
        assert snapshot["events"][-1]["reward"] == 0
        assert run._policy.closed
    finally:
        manager.close()


def test_failed_policy_and_bounded_history():
    manager = RunManager(policy_factory=FailingPolicy, max_runs=2)
    try:
        first = manager.create({})
        assert finish(first)["status"] == "failed"
        assert first._policy.closed
        second = manager.create({})
        finish(second)
        third = manager.create({})
        finish(third)
        assert manager.get(first.run_id) is None
        assert manager.get(second.run_id) is second
    finally:
        manager.close()


@pytest.mark.parametrize("config", [None, [], {"level": []}, {"level": "L6"}, {"seed": True},
                                     {"seed": -1}, {"seed": 2**53}, {"seed": 0.5}, {"mode": []},
                                     {"mode": "train"}, {"episode_path": "/private"}, {"script": "x.py"}])
def test_config_rejects_untrusted_or_unbounded_values(config):
    with pytest.raises(ValueError):
        validate_config(config)


def test_agent_worker_refuses_non_policy_operations():
    output = io.StringIO()
    serve(io.StringIO('null\n{"op":"eval","code":"1+1"}\n{"op":"close"}\n'), output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(rows) == 2 and all(row["ok"] is False for row in rows)


@pytest.fixture
def http_server(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text('<html lang="ru">ProofLedger</html>', encoding="utf-8")
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    server = LiveServer(("127.0.0.1", 0), web_root=web)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(base, path, body=None, headers=None, method=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})}, method=method)
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as response:
        return response.code, response.read(), response.headers
    with response:
        return response.status, response.read(), response.headers


def test_http_full_run_download_and_static_bounds(http_server):
    server, base = http_server
    code, body, _ = request(base, "/api/meta")
    assert code == 200 and json.loads(body)["agent"]["id"] == "reconciler"
    assert request(base, "/")[0] == 200
    for route in ("/../secret.txt", "/%2e%2e/secret.txt", "/proofledger/env.py", "/api/runs/nope"):
        assert request(base, route)[0] == 404
    code, body, _ = request(base, "/api/runs", {"seed": 4, "level": "L3", "mode": "closed"})
    assert code == 201
    run_id = json.loads(body)["run_id"]
    finish(server.manager.get(run_id))
    code, body, _ = request(base, f"/api/runs/{run_id}?after=1")
    status = json.loads(body)
    assert code == 200 and status["status"] == "completed"
    assert status["events"][0]["seq"] == 2
    assert request(base, f"/api/runs/{run_id}?after=-1")[0] == 400
    code, body, headers = request(base, f"/api/runs/{run_id}/report")
    assert code == 200 and json.loads(body)["report"]["f1"] == 1
    assert "attachment" in headers["Content-Disposition"]
    code, body, _ = request(base, f"/api/runs/{run_id}/trajectory")
    assert code == 200 and json.loads(body.splitlines()[-1])["terminated"]
    assert request(base, f"/api/runs/{run_id}/cancel", {}, method="POST")[0] == 200


def test_http_rejects_wrong_origin_and_malformed_requests(http_server):
    _, base = http_server
    assert request(base, "/api/runs", {}, {"Origin": "https://unrelated.example"})[0] == 403
    assert request(base, "/api/meta", headers={"Host": "unrelated.example"})[0] == 403
    assert request(base, "/api/runs", [])[0] == 400
    assert request(base, "/api/runs", {"extra": "x"})[0] == 400
    assert request(base, "/api/runs", {"seed": "x"})[0] == 400
    req = Request(base + "/api/runs", data=b"{}", headers={"Content-Type": "text/plain"})
    with pytest.raises(HTTPError) as error:
        urlopen(req, timeout=5)
    assert error.value.code == 400
