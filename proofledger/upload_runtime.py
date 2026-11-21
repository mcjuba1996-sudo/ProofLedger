"""Host for one uploaded policy. Only the packaged WASI interpreter executes submissions."""
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import struct
import sys
import tempfile
import tokenize


RUNTIME = Path(__file__).resolve().parent.parent / "runtime"
IMAGE_MODULES = Path("/opt/proofledger/wasmtime")
PINNED_VERSION = "50.0.1"
MEMORY_LIMIT = 128 * 1024 * 1024


def _bundled_engine():
    return (os.name == "nt" and struct.calcsize("P") == 8
            and platform.machine().lower() in {"amd64", "x86_64"})


def runtime_available():
    if not (RUNTIME / "python.wasm").is_file() or not (RUNTIME / "lib/python313.zip").is_file():
        return False
    if _bundled_engine():
        return (RUNTIME / "win_amd64/wasmtime/win32-x86_64/_wasmtime.dll").is_file()
    from importlib.metadata import PackageNotFoundError, version
    try:
        return importlib.util.find_spec("wasmtime") is not None and version("wasmtime") == PINNED_VERSION
    except PackageNotFoundError:
        return False


def validate_source(source):
    """Check the submission contract at registration without evaluating the module."""
    if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > 65536:
        raise ValueError("Выберите непустой файл Python размером до 64 КиБ.")
    try:
        # Bound the complexity of each statement before passing it to the host parser.
        tokens = 0
        for item in tokenize.generate_tokens(io.StringIO(source).readline):
            if item.type == tokenize.NEWLINE:
                tokens = 0
            else:
                tokens += 1
                if tokens > 512:
                    raise ValueError("Слишком сложное выражение в скрипте агента.")
        tree = ast.parse(source, filename="agent.py")
    except (SyntaxError, tokenize.TokenError, RecursionError) as error:
        line = getattr(error, "lineno", None)
        place = f" в строке {line}" if line else ""
        raise ValueError(f"Ошибка синтаксиса Python{place}.") from None
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Agent"]
    if len(classes) != 1:
        raise ValueError("Объявите один класс Agent в загружаемом файле.")
    methods = {node.name for node in classes[0].body if isinstance(node, ast.FunctionDef)}
    if not {"reset", "act"}.issubset(methods):
        raise ValueError("В классе Agent нужны методы reset(self, observation, info) и act(self, observation).")


def serve(*, precompile=False):
    if not runtime_available():
        raise RuntimeError(f"Установите wasmtime=={PINNED_VERSION} для исполнения загруженных агентов.")
    if _bundled_engine():
        sys.path.insert(0, str(RUNTIME / "win_amd64"))
    import wasmtime

    config = wasmtime.Config()
    image_runtime = sys.platform == "linux" and IMAGE_MODULES.is_dir()
    if image_runtime:
        # An explicit target disables build-host CPU feature detection. The image
        # can move between machines of the same architecture.
        config.target = f"{platform.machine()}-unknown-linux-gnu"
    else:
        try:
            config.cache = True
        except wasmtime.WasmtimeError:
            pass  # Source checkouts also run with a read-only cache.
    config.wasm_threads = False
    config.cranelift_opt_level = "none"
    config.memory_reservation = MEMORY_LIMIT
    config.memory_reservation_for_growth = 0
    engine = wasmtime.Engine(config)
    wasm = RUNTIME / "python.wasm"
    artifact = None
    if image_runtime:
        with wasm.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        artifact = IMAGE_MODULES / f"python-{PINNED_VERSION}-{digest}.cwasm"
    if precompile:
        if artifact is None:
            raise RuntimeError("Precompilation requires the Linux image runtime directory.")
        module = wasmtime.Module.from_file(engine, wasm)
        with tempfile.NamedTemporaryFile(dir=IMAGE_MODULES, delete=False) as output:
            output.write(module.serialize())
        os.chmod(output.name, 0o444)
        os.replace(output.name, artifact)
        return
    if artifact is not None and artifact.is_file():
        # Serialized modules contain machine code. Only load the immutable
        # interpreter created by the Docker build, outside the guest filesystem.
        module = wasmtime.Module.deserialize_file(engine, str(artifact))
    else:
        module = wasmtime.Module.from_file(engine, wasm)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    store = wasmtime.Store(engine)
    store.set_limits(memory_size=MEMORY_LIMIT, instances=1, memories=1, tables=1)
    wasi = wasmtime.WasiConfig()
    wasi.argv = ["python", "-B", "-S", "-P", "-u", "-c", (RUNTIME / "guest.py").read_text(encoding="utf-8")]
    wasi.env = [("PYTHONHOME", "/runtime"), ("PYTHONHASHSEED", "0"), ("PYTHONUTF8", "1")]
    # Mount only the shipped standard-library directory. No project, host credentials,
    # network capability or writable directory is provided to the guest.
    wasi.preopen_dir(str(RUNTIME / "lib"), "/runtime/lib", fs_mutable=False)
    wasi.inherit_stdin()
    wasi.inherit_stdout()
    wasi.inherit_stderr()
    store.set_wasi(wasi)
    instance = linker.instantiate(store, module)
    instance.exports(store)["_start"](store)


if __name__ == "__main__":
    try:
        serve(precompile=sys.argv[1:] == ["--precompile"])
    except Exception as error:
        message = str(error).splitlines()[0] if str(error) else type(error).__name__
        print(json.dumps({"ok": False, "error": "WASI: " + message[:220]}, ensure_ascii=True), flush=True)
        sys.exit(1)
