"""Offline dashboard: python -m proofledger.live [--port 8765] [--no-browser]."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser

from .levels import LEVELS
from .live_runner import APP_VERSION, BUILTIN, MAX_SOURCE_BYTES, RunError, RunManager
from .upload_runtime import runtime_available

WEB = Path(__file__).parent / "web"
EXAMPLE_DIR = Path(__file__).parent / "examples"
EXAMPLES = {
    "baseline_silence.py": "Контрольный агент с пустым ответом",
    "audit_duplicates.py": "Аудитор дублирующихся счетов",
    "audit_allocation.py": "Аудитор распределения платежей",
}
MAX_BODY = 8192
# JSON permits six ASCII bytes per input character via Unicode escapes.
MAX_UPLOAD_BODY = 6 * MAX_SOURCE_BYTES + MAX_BODY
ASSETS = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
          "/app.js": ("app.js", "text/javascript"), "/styles.css": ("styles.css", "text/css"),
          "/scene.svg": ("scene.svg", "image/svg+xml"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
RUN_PATH = re.compile(r"/api/runs/([0-9a-f]{32})(?:/(report|trajectory|cancel))?")


def metadata(manager=None):
    return {"version": APP_VERSION, "agent": BUILTIN.summary(),
            "agents": manager.agents() if manager is not None else [BUILTIN.summary()],
            "examples": [{"filename": filename, "name": title, "url": "/api/examples/" + filename}
                         for filename, title in EXAMPLES.items()],
            "max_source_bytes": MAX_SOURCE_BYTES,
            "upload_runtime_available": runtime_available(),
            "levels": [{"id": item["id"], "title": item["title"], "description": item["goal"]}
                       for item in LEVELS], "default_level": "L4",
            "modes": ["demo", "closed"], "max_steps": 100, "time_limit_s": 60,
            "scope": "local_agent_upload"}


class LiveServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address=("127.0.0.1", 8765), *, manager=None, web_root=None):
        if address[0] != "127.0.0.1":
            raise ValueError("Live dashboard must bind to 127.0.0.1")
        self.manager = manager or RunManager()
        self.web_root = Path(web_root) if web_root is not None else WEB
        super().__init__(address, LiveHandler)

    def server_close(self):
        self.manager.close()
        super().server_close()


class LiveHandler(BaseHTTPRequestHandler):
    server_version = "ProofLedgerLocal/0.6"

    def log_message(self, format, *args):
        # Browser polling must not flood the launch terminal.
        pass

    def _send(self, status, body, content_type="application/json", filename=None):
        if not isinstance(body, bytes):
            # Preserve any escaped surrogate returned inside an agent's JSON string.
            body = body.encode("utf-8", errors="backslashreplace")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                         "object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _json(self, status, value, filename=None):
        self._send(status, json.dumps(value, ensure_ascii=False, allow_nan=False), filename=filename)

    def _error(self, status, message):
        self._json(status, {"error": message})

    def _allowed_request(self, mutate=False):
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "").lower()
        if host not in allowed_hosts:
            self._error(403, "Доступ разрешён только через локальный адрес сервера.")
            return False
        if mutate:
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{host}":
                self._error(403, "Запрос из другого источника отклонён.")
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self._error(403, "Запрос из другого источника отклонён.")
                return False
        return True

    def _discard_rejected_body(self):
        # Closing with unread request data can reset TCP before the client sees
        # the error response. Only drain short bodies with known framing.
        self.close_connection = True
        if self.headers.get("Transfer-Encoding"):
            return
        try:
            remaining = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if not 0 < remaining <= MAX_BODY:
            return
        previous = self.connection.gettimeout()
        budget = 0.25 if previous is None else min(0.25, previous)
        deadline = time.monotonic() + budget
        try:
            while remaining:
                wait = deadline - time.monotonic()
                if wait <= 0:
                    break
                self.connection.settimeout(wait)
                chunk = self.rfile.read1(remaining)
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.connection.settimeout(previous)

    def _body(self, limit=MAX_BODY):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Потоковая передача тела запроса не поддерживается.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Некорректный Content-Length.") from None
        if length < 0 or length > limit:
            raise ValueError(f"Слишком большой запрос (максимум {limit} байт).")
        if length == 0:
            return {}
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._discard_rejected_body()
            raise ValueError("Ожидается Content-Type: application/json.")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"),
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, RecursionError):
            raise ValueError("Некорректный JSON.") from None
        if not isinstance(value, dict):
            raise ValueError("Запрос должен быть объектом JSON.")
        return value

    def do_GET(self):
        if not self._allowed_request():
            return
        url = urlsplit(self.path)
        path = unquote(url.path)
        if path == "/api/meta":
            self._json(200, metadata(self.server.manager))
            return
        if path.startswith("/api/examples/"):
            filename = path[len("/api/examples/"):]
            if filename not in EXAMPLES:
                self._error(404, "Пример агента не найден.")
            else:
                self._send(200, (EXAMPLE_DIR / filename).read_bytes(), "text/plain", filename=filename)
            return
        match = RUN_PATH.fullmatch(path)
        if match:
            run_id, operation = match.groups()
            run = self.server.manager.get(run_id)
            if run is None:
                self._error(404, "Запуск не найден или удалён из истории.")
                return
            if operation is None:
                try:
                    query = parse_qs(url.query)
                    after = int(query.get("after", ["0"])[0])
                    if after < 0:
                        raise ValueError()
                except ValueError:
                    self._error(400, "Параметр after должен быть неотрицательным целым числом.")
                    return
                self._json(200, run.snapshot(after))
                return
            if operation in ("report", "trajectory"):
                if run.snapshot()["status"] == "running":
                    self._error(409, "Файл доступен после завершения запуска.")
                    return
                if operation == "report":
                    self._json(200, run.export_report(), filename=f"proofledger-{run_id[:8]}-report.json")
                else:
                    self._send(200, run.export_trajectory(), "application/x-ndjson",
                               filename=f"proofledger-{run_id[:8]}-trajectory.jsonl")
                return
        if path in ASSETS:
            name, mime = ASSETS[path]
            try:
                content = (self.server.web_root / name).read_bytes()
            except FileNotFoundError:
                self._error(404, "Файл интерфейса не найден.")
                return
            self._send(200, content, mime)
            return
        self._error(404, "Ресурс не найден.")

    def do_POST(self):
        if not self._allowed_request(mutate=True):
            self._discard_rejected_body()
            return
        path = unquote(urlsplit(self.path).path)
        try:
            body = self._body(MAX_UPLOAD_BODY if path == "/api/agents" else MAX_BODY)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        if path in ("/api/agents", "/api/runs"):
            try:
                if path == "/api/agents":
                    result = self.server.manager.register_agent(body)
                else:
                    result = {"run_id": self.server.manager.create(body).run_id}
            except ValueError as exc:
                self._error(400, str(exc))
                return
            except RunError as exc:
                self._error(409, str(exc))
                return
            self._json(201, result)
            return
        match = RUN_PATH.fullmatch(path)
        if match and match.group(2) == "cancel":
            run = self.server.manager.get(match.group(1))
            if run is None:
                self._error(404, "Запуск не найден.")
                return
            self._json(200, run.cancel())
            return
        self._error(404, "Ресурс не найден.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="ProofLedger: локальная проверка агентов-аудиторов")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Не открывать браузер автоматически")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        server = LiveServer(("127.0.0.1", args.port))
    except OSError as exc:
        print(f"Cannot start local server on port {args.port}: {exc}")
        return 1
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"ProofLedger Live {APP_VERSION}: {url}", flush=True)
    print("Local agent dashboard. Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        timer = threading.Timer(0.3, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
