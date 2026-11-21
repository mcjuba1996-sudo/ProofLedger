"""Trusted local evaluator and registry of uploaded auditor agents."""
import copy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unicodedata
import uuid

from .env import Environment, MAX_STEPS
from .levels import LEVEL_IDS
from .upload_runtime import PINNED_VERSION, runtime_available, validate_source

APP_VERSION = "0.6.2"
RUN_LIMIT_S = 60.0
ACTION_LIMIT_S = 10.0
RUNTIME_START_LIMIT_S = 45.0
MAX_RUNS = 20
MAX_AGENTS = 64
MAX_SOURCE_BYTES = 64 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
MAX_AGENT_OUTPUT = 2 * 1024 * 1024


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    source: str = ""

    def summary(self):
        return {"id": self.id, "name": self.name}


BUILTIN = AgentSpec("reconciler", "Reconciler")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("Non-finite number")
    return value


class RunError(Exception):
    pass


class RunCancelled(RunError):
    pass


class RunTimeout(RunError):
    pass


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Параметры запуска должны быть объектом JSON.")
    if set(config) - {"level", "seed", "mode", "agent_id"}:
        raise ValueError("Допустимые параметры: level, seed, mode, agent_id.")
    level, seed, mode = config.get("level", "L4"), config.get("seed", 7), config.get("mode", "demo")
    if not isinstance(level, str) or level not in LEVEL_IDS:
        raise ValueError("Выберите уровень L1-L5.")
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError("Seed должен быть целым числом от 0 до 2147483647.")
    if mode not in ("demo", "closed"):
        raise ValueError("Допустимые режимы: demo, closed.")
    agent_id = config.get("agent_id", BUILTIN.id)
    if not isinstance(agent_id, str) or not 1 <= len(agent_id) <= 80:
        raise ValueError("Неверный идентификатор агента.")
    return {"level": level, "seed": seed, "mode": mode, "agent_id": agent_id}


class PolicyProcess:
    """Bounded JSON-lines calls to a trusted worker implementation."""

    def __init__(self, cancelled, deadline, action_limit=ACTION_LIMIT_S, *, uploaded=False):
        self.cancelled, self.deadline, self.action_limit = cancelled, deadline, action_limit
        self._responses = queue.Queue(maxsize=1)
        self._requests = queue.Queue(maxsize=1)
        self._reader_stopped = threading.Event()
        self._uploaded = uploaded
        self._close_lock = threading.Lock()
        root = Path(__file__).resolve().parent.parent
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        worker = "proofledger.upload_runtime" if uploaded else "proofledger.agent_worker"
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", worker],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, **options)
        self._reader = threading.Thread(target=self._read, name="proofledger-agent-output", daemon=True)
        self._writer = threading.Thread(target=self._write, name="proofledger-agent-input", daemon=True)
        self._reader.start()
        self._writer.start()

    def _write(self):
        # Guest input can back up when it stops reading. Only this thread touches
        # the pipe so the calling thread can keep enforcing the action deadline.
        try:
            while True:
                request = self._requests.get()
                if request is None or self._reader_stopped.is_set():
                    break
                self.process.stdin.write(json.dumps(request, ensure_ascii=True, allow_nan=False) + "\n")
                self.process.stdin.flush()
        except (OSError, ValueError):
            self._deliver(RunError("Не удалось передать наблюдение агенту."))

    def _deliver(self, response):
        while not self._reader_stopped.is_set():
            try:
                self._responses.put(response, timeout=0.05)
                return
            except queue.Full:
                continue

    def _read(self):
        output_size = 0
        try:
            while True:
                line = self.process.stdout.readline(MAX_RESPONSE + 1)
                if not line:
                    break
                if len(line) > MAX_RESPONSE:
                    self._deliver(RunError("Ответ агента превышает допустимый размер."))
                    return
                if self._uploaded:
                    output_size += len(line.encode("utf-8"))
                    if output_size > MAX_AGENT_OUTPUT:
                        self._deliver(RunError("Ответы агента за эпизод превысили 2 МиБ."))
                        return
                try:
                    response = json.loads(line, parse_float=_finite_float, parse_constant=_finite_float)
                except (ValueError, RecursionError):
                    self._deliver(RunError("Агент вернул некорректный JSON."))
                    return
                self._deliver(response)
                if self._reader_stopped.is_set():
                    return
        except (OSError, ValueError):
            pass
        finally:
            self._deliver(RunError("Процесс агента завершился без ответа."))

    def call(self, request, *, timeout=None):
        if self.cancelled.is_set():
            raise RunCancelled("Запуск остановлен пользователем.")
        if time.monotonic() >= self.deadline:
            raise RunTimeout("Превышен лимит времени запуска.")
        until = min(self.deadline, time.monotonic() + (self.action_limit if timeout is None else timeout))
        try:
            self._requests.put_nowait(request)
        except queue.Full:
            raise RunError("Агент нарушил порядок запросов и ответов.") from None
        while True:
            if self.cancelled.is_set():
                raise RunCancelled("Запуск остановлен пользователем.")
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise RunTimeout("Агент не ответил за отведённое время.")
            try:
                response = self._responses.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue
            if isinstance(response, Exception):
                raise response
            if isinstance(response, dict) and response.get("ok") is False:
                error = response.get("error")
                if isinstance(error, str) and error.strip():
                    raise RunError("Ошибка агента: " + error.strip()[:300])
            if not isinstance(response, dict) or response.get("ok") is not True:
                raise RunError("Агент отклонил запрос или нарушил протокол.")
            return response

    def close(self):
        with self._close_lock:
            self._reader_stopped.set()
            try:
                self._requests.put_nowait(None)
            except queue.Full:
                pass
            if self.process.poll() is None:
                self.process.kill()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (self.process.stdin, self.process.stdout):
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
        if threading.current_thread() is not self._reader:
            self._reader.join(timeout=1)
        self._writer.join(timeout=1)


class LiveRun:
    def __init__(self, config, *, agent=BUILTIN, policy_factory=PolicyProcess, run_limit=RUN_LIMIT_S):
        self.run_id = uuid.uuid4().hex
        self.config = validate_config(config)
        self.agent = agent
        self.status, self.error = "running", None
        self.report, self.findings = None, []
        self.events, self.trajectory = [], []
        self.started = time.monotonic()
        self.runtime_setup_ms = None
        self.finished = None
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._expired = threading.Event()
        self._env_ready = False
        self._policy, self._factory = None, policy_factory
        self._run_limit = run_limit
        self._thread = threading.Thread(target=self._execute, name=f"proofledger-run-{self.run_id[:8]}", daemon=True)
        self._watchdog = threading.Timer(run_limit, self._timeout)
        self._watchdog.daemon = True

    def start(self):
        self._watchdog.start()
        self._thread.start()

    def elapsed_ms(self):
        return round(((self.finished or time.monotonic()) - self.started) * 1000, 3)

    def snapshot(self, after=0):
        with self._lock:
            return copy.deepcopy({
                "run_id": self.run_id, "status": self.status, "config": self.config,
                "events": [event for event in self.events if event["seq"] > after],
                "last_seq": len(self.events), "elapsed_ms": self.elapsed_ms(),
                "runtime_setup_ms": self.runtime_setup_ms,
                "report": self.report, "findings": self.findings, "error": self.error,
                "agent": self.agent.id, "agent_name": self.agent.name,
            })

    def _append(self, row, kind):
        with self._lock:
            if self.status != "running":
                return
            self.trajectory.append(copy.deepcopy(row))
            self.events.append({"seq": len(self.events) + 1, "kind": kind,
                                **copy.deepcopy(row), "elapsed_ms": self.elapsed_ms()})

    def _finish(self, status, *, report=None, findings=None, error=None):
        with self._lock:
            if self.status != "running":
                return
            self.status, self.finished = status, time.monotonic()
            self.report, self.findings, self.error = copy.deepcopy(report), copy.deepcopy(findings or []), error
        self._watchdog.cancel()

    def _timeout(self):
        with self._lock:
            if self.status != "running":
                return
            self._expired.set()
            self._cancelled.set()
            if not self._env_ready:
                self._finish("failed", error="Превышен лимит времени подготовки эпизода.")
        # Only the run thread accesses Environment. The timer interrupts the
        # policy, then the run thread records a canonical empty-answer truncation.
        self._close_policy()

    def _close_policy(self):
        with self._lock:
            policy = self._policy
        if policy:
            policy.close()

    def cancel(self):
        self._cancelled.set()
        self._finish("cancelled", error="Запуск остановлен пользователем.")
        self._close_policy()
        return self.snapshot()

    def join(self, timeout=5):
        self._thread.join(timeout)

    def _finalize_timeout(self, env):
        with self._lock:
            if self.status != "running" or not self._env_ready:
                return
            # Observe the deadline at the next interaction boundary, just as
            # Environment.step does. There is no submitted answer. Reuse the
            # evaluator's common scorer/logging on its owning thread only.
            # Any in-flight step that crossed the deadline is not published.
            previous_step = self.events[-1]["step"] if self.events else 0
            env._steps = min(MAX_STEPS, previous_step + 1)
            env._truncate("time_limit", None, inspection_steps=previous_step)
            self._append(env.trajectory[-1], "step")
            self._finish("completed", report=env.final_report(), findings=[])

    def _execute(self):
        env = None
        try:
            deadline = self.started + self._run_limit
            if self.agent.source:
                setup_started = time.monotonic()
                policy = PolicyProcess(self._cancelled, deadline, uploaded=True)
                with self._lock:
                    self._policy = policy
                ready = policy.call({"op": "ready"}, timeout=RUNTIME_START_LIMIT_S)
                if ready.get("ready") is not True:
                    raise RunError("Изолированный исполнитель не подтвердил готовность.")
                with self._lock:
                    self.runtime_setup_ms = round((time.monotonic() - setup_started) * 1000, 3)
            env = Environment(mode="train" if self.config["mode"] == "demo" else "closed")
            observation, info = env.reset(self.config["seed"], {"level": self.config["level"]})
            with self._lock:
                if self._cancelled.is_set():
                    raise RunCancelled("Запуск остановлен пользователем.")
                self._env_ready = True
                self._append(env.trajectory[-1], "reset")
            if self.agent.source:
                policy.call({"op": "load", "source": self.agent.source})
            else:
                policy = self._factory(self._cancelled, deadline)
                with self._lock:
                    self._policy = policy
            policy.call({"op": "reset", "observation": observation, "info": info})
            submitted = []
            for _ in range(MAX_STEPS):
                response = policy.call({"op": "act", "observation": observation})
                action = response.get("action")
                if not isinstance(action, dict):
                    raise RunError("Агент должен вернуть действие в виде JSON-объекта.")
                if self._cancelled.is_set():
                    raise RunCancelled("Запуск остановлен пользователем.")
                observation, reward, terminated, truncated, _ = env.step(action)
                with self._lock:
                    if self._expired.is_set():
                        raise RunTimeout("Превышен лимит времени запуска.")
                    if self._cancelled.is_set():
                        raise RunCancelled("Запуск остановлен пользователем.")
                    self._append(env.trajectory[-1], "step")
                    if terminated or truncated:
                        if terminated and action.get("action") == "submit":
                            submission = action.get("findings")
                            if isinstance(submission, dict) and isinstance(submission.get("findings"), list):
                                submitted = submission["findings"]
                        report = env.final_report()
                        if report is None:
                            raise RunError("Оценщик не вернул итоговый отчёт.")
                        self._finish("completed", report=report, findings=submitted)
                        return
            raise RunError("Эпизод не завершился в пределах 100 шагов.")
        except RunTimeout:
            if self._env_ready:
                self._finalize_timeout(env)
            else:
                self._finish("failed", error="Истекло время подготовки изолированного исполнителя.")
        except RunCancelled as exc:
            if self._expired.is_set():
                self._finalize_timeout(env)
            else:
                self._finish("cancelled", error=str(exc))
        except RunError as exc:
            if self._expired.is_set():
                self._finalize_timeout(env)
            else:
                self._finish("failed", error=str(exc))
        except Exception as exc:
            self._finish("failed", error=f"Ошибка выполнения ({type(exc).__name__}).")
        finally:
            self._close_policy()
            if env:
                env.close()
            self._watchdog.cancel()

    def export_report(self):
        snapshot = self.snapshot(after=len(self.events))
        return {"version": APP_VERSION, "run_id": self.run_id, "agent": self.agent.id,
                "agent_name": self.agent.name,
                "config": snapshot["config"], "status": snapshot["status"],
                "runtime_setup_ms": snapshot["runtime_setup_ms"],
                "elapsed_ms": snapshot["elapsed_ms"], "report": snapshot["report"],
                "findings": snapshot["findings"], "error": snapshot["error"]}

    def export_trajectory(self):
        with self._lock:
            return "".join(json.dumps({**row, "agent": self.agent.id, "agent_name": self.agent.name},
                                      ensure_ascii=False, allow_nan=False) + "\n" for row in self.trajectory)


class RunManager:
    def __init__(self, *, policy_factory=PolicyProcess, run_limit=RUN_LIMIT_S, max_runs=MAX_RUNS):
        self._lock, self._runs = threading.Lock(), {}
        self._agents = {BUILTIN.id: BUILTIN}
        self.policy_factory, self.run_limit, self.max_runs = policy_factory, run_limit, max_runs
        self._closed = False

    def agents(self):
        with self._lock:
            return [agent.summary() for agent in self._agents.values()]

    def register_agent(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"filename", "source"}:
            raise ValueError("При загрузке передайте имя файла и текст скрипта.")
        name, source = payload["filename"], payload["source"]
        if (not isinstance(name, str) or not 3 < len(name) <= 128 or not name.lower().endswith(".py")
                or name.startswith(".") or any(c in "/\\:" or unicodedata.category(c).startswith("C")
                                               for c in name)):
            raise ValueError("Укажите имя файла .py без пути, до 128 символов.")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Файл агента пуст.")
        try:
            content = source.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Сохраните файл агента в кодировке UTF-8.") from None
        if len(content) > MAX_SOURCE_BYTES:
            raise ValueError("Максимальный размер одного агента — 64 КиБ.")
        agent_id = "upload-" + hashlib.sha256(name.encode("utf-8") + b"\x00" + content).hexdigest()
        with self._lock:
            if self._closed:
                raise RunError("Сервер останавливается.")
            if agent_id in self._agents:
                return self._agents[agent_id].summary()
            if len(self._agents) - 1 >= MAX_AGENTS:
                raise RunError(f"Достигнут лимит в {MAX_AGENTS} загруженных агентов за сеанс.")
            source = source.removeprefix("\ufeff")
            validate_source(source)
            agent = AgentSpec(agent_id, name, source)
            self._agents[agent_id] = agent
            return agent.summary()

    def create(self, config):
        config = validate_config(config)
        with self._lock:
            if self._closed:
                raise RunError("Сервер останавливается.")
            agent = self._agents.get(config["agent_id"])
            if agent is None:
                raise ValueError("Агент не найден. Выберите или загрузите файл заново.")
            if agent.source and not runtime_available():
                raise RunError("Исполнитель загруженных агентов недоступен. Распакуйте каталог runtime "
                               f"полностью. Для платформ кроме Windows x64 установите wasmtime=={PINNED_VERSION}.")
            if any(run.status == "running" for run in self._runs.values()):
                raise RunError("Уже выполняется запуск. Дождитесь завершения или остановите его.")
            while len(self._runs) >= self.max_runs:
                self._runs.pop(next(iter(self._runs)))
            run = LiveRun(config, agent=agent, policy_factory=self.policy_factory, run_limit=self.run_limit)
            self._runs[run.run_id] = run
            run.start()
            return run

    def get(self, run_id):
        with self._lock:
            return self._runs.get(run_id)

    def close(self):
        with self._lock:
            self._closed = True
            runs = list(self._runs.values())
        for run in runs:
            if run.status == "running":
                run.cancel()
        for run in runs:
            run.join(timeout=3)
