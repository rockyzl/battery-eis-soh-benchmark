"""Fail-closed subprocess supervisor for the KIT F1 B-only engine."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import os
import platform
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, TextIO, cast, get_type_hints

from battery_eis_soh.kit_f1_b_authorization import (
    BINDING_KEYS,
    consume_authorization,
    create_exclusive_json,
    create_linked_exclusive_json,
    peek_authorization_nonce,
    sha256_bytes,
    validate_consumed_authorization,
    validate_consumption_receipt,
)
from battery_eis_soh.kit_f1_b_training import (
    ARTIFACT_NAMES,
    FALSE_FLAGS,
    execute_b_only_analysis,
)

ENGINE_MODULE = "battery_eis_soh.kit_f1_b_training"
ENGINE_CALLABLE = "execute_b_only_analysis"
ENGINE_PARAMETERS = (
    "eoc_zip",
    "output_dir",
    "config_path",
    "authorization_path",
    "authorization_receipt",
    "runtime_bindings",
    "progress",
)
ENGINE_HINTS: dict[str, object] = {
    "eoc_zip": Path,
    "output_dir": Path,
    "config_path": Path,
    "authorization_path": Path,
    "authorization_receipt": Path,
    "runtime_bindings": dict[str, str],
    "progress": Callable[[dict[str, object]], None],
    "return": dict[str, object],
}
PARENT_ARTIFACTS = (
    "start_manifest.json",
    "telemetry.jsonl",
    "lifecycle.jsonl",
    "soft_drain.request.json",
    "terminal.json",
    "terminal.recovery.json",
    "final_manifest.json",
)


class BOnlyWorkerError(RuntimeError):
    """The isolated B-only runtime contract failed closed."""


class SoftDrainRequested(BOnlyWorkerError):
    """The worker observed a supervisor drain request at a fit boundary."""


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(path: Path) -> str:
    before = path.lstat()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise BOnlyWorkerError("hash input changed during secure open")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _regular(path: Path, label: str) -> Path:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise BOnlyWorkerError(f"{label} path contains a symlink")
    metadata = absolute.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise BOnlyWorkerError(f"{label} must be a regular file")
    return absolute


def _existing_directory(path: Path, label: str) -> Path:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise BOnlyWorkerError(f"{label} contains a non-directory or symlink component")
    return absolute


def validate_engine_contract() -> dict[str, str]:
    module = importlib.import_module(ENGINE_MODULE)
    source = _regular(Path(str(module.__file__)), "B-only engine source")
    function = getattr(module, ENGINE_CALLABLE)
    parameters = tuple(inspect.signature(function).parameters.values())
    if tuple(parameter.name for parameter in parameters) != ENGINE_PARAMETERS:
        raise BOnlyWorkerError("B-only engine parameter contract drifted")
    if any(parameter.kind is not inspect.Parameter.KEYWORD_ONLY for parameter in parameters):
        raise BOnlyWorkerError("B-only engine parameters must be keyword-only")
    if get_type_hints(function) != ENGINE_HINTS:
        raise BOnlyWorkerError("B-only engine type contract drifted")
    return {
        "module": ENGINE_MODULE,
        "callable": ENGINE_CALLABLE,
        "signature": str(inspect.signature(function)),
        "module_sha256": _sha256(source),
    }


def _atomic(path: Path, value: object) -> None:
    try:
        create_exclusive_json(path, value)
    except FileExistsError as error:
        raise BOnlyWorkerError(f"immutable runtime artifact exists: {path.name}") from error


def _atomic_terminal(path: Path, value: object) -> None:
    try:
        create_linked_exclusive_json(path, value)
    except FileExistsError as error:
        raise BOnlyWorkerError(f"immutable terminal artifact exists: {path.name}") from error


def _append(path: Path, value: object) -> None:
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(_canonical(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _source_tree_hash(root: Path, runner: Path, config: Path, schema: Path) -> str:
    candidates = [
        *sorted((root / "src/battery_eis_soh").glob("*.py")),
        runner,
        config,
        schema,
        root / "pyproject.toml",
        root / "uv.lock",
    ]
    rows: list[dict[str, str]] = []
    for candidate in candidates:
        regular = _regular(candidate, "source-tree input")
        rows.append(
            {
                "path": regular.relative_to(root).as_posix(),
                "sha256": _sha256(regular),
            }
        )
    return sha256_bytes(_canonical(sorted(rows, key=lambda row: row["path"])).encode())


def _child_environment() -> dict[str, str]:
    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
    }


def _receipt_path(authorization_path: Path, nonce: str) -> Path:
    absolute = authorization_path.expanduser().absolute()
    return absolute.with_name(f".{absolute.name}.{nonce}.consumed.json")


def prepare_run_bindings(
    *,
    eoc_zip: Path,
    output_dir: Path,
    config_path: Path,
    authorization_path: Path,
    runner_path: Path,
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str], dict[str, object]]:
    """Prepare exact runtime bindings without claiming output or consuming authorization."""
    eoc = _regular(eoc_zip, "EOC archive")
    config = _regular(config_path, "B-only config")
    schema = _regular(config.with_name(f"{config.stem}.schema.json"), "B-only schema")
    runner = _regular(runner_path, "B-only runner")
    root = config.parent.parent
    worker = _regular(Path(__file__), "B-only worker source")
    loader_module = importlib.import_module("battery_eis_soh.kit_v2")
    loader = _regular(Path(str(loader_module.__file__)), "capacity loader source")
    lockfile = _regular(root / "uv.lock", "dependency lockfile")
    contract = validate_engine_contract()
    output = output_dir.expanduser().absolute()
    nonce = peek_authorization_nonce(authorization_path)
    receipt = _receipt_path(authorization_path, nonce)
    argv = (
        sys.executable,
        str(runner),
        "--worker",
        "--eoc-zip",
        str(eoc),
        "--output-dir",
        str(output),
        "--config-path",
        str(config),
        "--authorization-receipt",
        str(receipt),
        "--authorization-path",
        str(authorization_path.expanduser().absolute()),
        "--expected-engine-sha256",
        contract["module_sha256"],
    )
    environment = _child_environment()
    cwd = root.absolute()
    bindings = {
        "eoc_sha256": _sha256(eoc),
        "config_sha256": _sha256(config),
        "schema_sha256": _sha256(schema),
        "engine_sha256": contract["module_sha256"],
        "worker_sha256": _sha256(worker),
        "runner_sha256": _sha256(runner),
        "loader_sha256": _sha256(loader),
        "lockfile_sha256": _sha256(lockfile),
        "source_tree_sha256": _source_tree_hash(root, runner, config, schema),
        "command_sha256": sha256_bytes(_canonical(argv).encode()),
        "cwd_sha256": sha256_bytes(str(cwd).encode()),
        "output_sha256": sha256_bytes(str(output).encode()),
        "environment_sha256": sha256_bytes(_canonical(environment).encode()),
    }
    if set(bindings) != BINDING_KEYS:
        raise BOnlyWorkerError("runtime binding set drifted")
    metadata: dict[str, object] = {
        "eoc": str(eoc),
        "config": str(config),
        "schema": str(schema),
        "runner": str(runner),
        "output": str(output),
        "cwd": str(cwd),
        "authorization_receipt": str(receipt),
        "authorization_nonce": nonce,
        "engine": contract,
    }
    return bindings, argv, environment, metadata


class WorkerProgress:
    """Thread-safe progress/heartbeat channel that honors soft drain at fit boundaries."""

    def __init__(self, cancellation: Mapping[str, bool], interval_s: float = 5.0) -> None:
        self.cancellation = cancellation
        self.interval_s = interval_s
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def _emit(self, payload: dict[str, object]) -> None:
        with self.lock:
            print(_canonical(payload), flush=True)

    def _heartbeat(self) -> None:
        while not self.stop_event.wait(self.interval_s):
            self._emit({"stage": "heartbeat", "at_unix_s": time.time()})

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=max(1.0, self.interval_s * 2))

    def __call__(self, payload: dict[str, object]) -> None:
        if self.cancellation.get("requested", False) and payload.get("stage") in {
            "fit_start",
            "fit_complete",
        }:
            raise SoftDrainRequested("soft drain observed at a fit boundary")
        self._emit(payload)


def _worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--eoc-zip", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--authorization-receipt", type=Path, required=True)
    parser.add_argument("--authorization-path", type=Path, required=True)
    parser.add_argument("--expected-engine-sha256", required=True)
    return parser


def worker_main(argv: Sequence[str] | None = None) -> int:
    args = _worker_parser().parse_args(argv)
    contract = validate_engine_contract()
    if contract["module_sha256"] != args.expected_engine_sha256:
        raise BOnlyWorkerError("B-only engine hash changed after supervisor preflight")
    bindings, expected_argv, expected_environment, _ = prepare_run_bindings(
        eoc_zip=args.eoc_zip,
        output_dir=args.output_dir,
        config_path=args.config_path,
        authorization_path=args.authorization_path,
        runner_path=Path(sys.argv[0]),
    )
    if (sys.executable, *sys.argv) != expected_argv:
        raise BOnlyWorkerError("child command differs from the authorized command")
    observed_environment = {key: os.environ.get(key, "") for key in expected_environment}
    if observed_environment != expected_environment:
        raise BOnlyWorkerError("child environment differs from the authorized environment")
    validate_consumed_authorization(
        args.authorization_path,
        args.authorization_receipt,
        expected_bindings=bindings,
        output_dir=args.output_dir.expanduser().absolute(),
    )
    cancellation = {"requested": False}

    def request_drain(_signal: int, _frame: object) -> None:
        cancellation["requested"] = True

    prior = signal.signal(signal.SIGUSR1, request_drain)
    progress = WorkerProgress(cancellation)
    progress.start()
    try:
        result = execute_b_only_analysis(
            eoc_zip=args.eoc_zip,
            output_dir=args.output_dir,
            config_path=args.config_path,
            authorization_path=args.authorization_path,
            authorization_receipt=args.authorization_receipt,
            runtime_bindings=bindings,
            progress=progress,
        )
        progress({"stage": "worker_success", "result": result})
        return 0
    except SoftDrainRequested as error:
        print(
            _canonical({"stage": "soft_drain_cancelled", "error_type": type(error).__name__}),
            flush=True,
        )
        return 124
    except Exception as error:  # noqa: BLE001 - subprocess boundary fails closed
        print(
            _canonical({"stage": "worker_failure", "error_type": type(error).__name__}), flush=True
        )
        return 2
    finally:
        progress.stop()
        signal.signal(signal.SIGUSR1, prior)


def _rss_bytes(pid: int) -> int:
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return 0
    for line in status.read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0


def runtime_action(
    *,
    elapsed_s: float,
    rss_bytes: int,
    free_bytes: int,
    limits: Mapping[str, int | float],
    soft_sent: bool,
) -> str | None:
    """Return the deterministic next resource-governance action."""
    if free_bytes < int(limits["minimum_free_bytes"]):
        return "disk_floor_breached"
    if rss_bytes >= int(limits["hard_rss_bytes"]):
        return "hard_rss_exceeded"
    if elapsed_s >= float(limits["hard_stop_seconds"]):
        return "hard_stop_exceeded"
    if elapsed_s >= float(limits["soft_drain_seconds"]) and not soft_sent:
        return "soft_drain"
    return None


def _validate_success(output: Path, result: dict[str, object]) -> dict[str, str]:
    expected = set(ARTIFACT_NAMES)
    present = {path.name for path in output.iterdir() if path.name not in PARENT_ARTIFACTS}
    if present != expected:
        raise BOnlyWorkerError(f"B-only scientific artifact set drifted: {sorted(present)}")
    if result.get("artifact_paths") != list(ARTIFACT_NAMES):
        raise BOnlyWorkerError("worker result artifact allowlist drifted")
    if any(result.get(key) is not False for key in FALSE_FLAGS):
        raise BOnlyWorkerError("worker result attempts a prohibited claim")
    summary = json.loads((output / "engine_summary.json").read_text())
    if summary.get("view") != "B" or any(key.startswith("o2_") for key in summary):
        raise BOnlyWorkerError("B-only summary contains prohibited view/task state")
    if summary.get("planned_fits") != summary.get("completed_fits"):
        raise BOnlyWorkerError("partial B-only fit set cannot succeed")
    return {name: _sha256(output / name) for name in ARTIFACT_NAMES}


def _record_worker_line(
    line: str,
    *,
    output: Path,
    state: dict[str, object],
) -> dict[str, object]:
    if len(line.encode()) > 65_536:
        raise BOnlyWorkerError("worker output line exceeds 64 KiB")
    try:
        event = json.loads(line)
    except json.JSONDecodeError as error:
        raise BOnlyWorkerError("non-JSON worker output") from error
    if not isinstance(event, dict):
        raise BOnlyWorkerError("worker output event must be an object")
    _append(output / "lifecycle.jsonl", {"event": "worker", **event})
    stage = event.get("stage")
    if stage in {"worker_success", "worker_failure", "soft_drain_cancelled"}:
        prior_count = state.get("terminal_event_count", 0)
        if not isinstance(prior_count, int):
            raise BOnlyWorkerError("terminal worker event counter is invalid")
        count = prior_count + 1
        state["terminal_event_count"] = count
        if count != 1:
            raise BOnlyWorkerError("multiple terminal worker events are prohibited")
        if stage == "worker_success":
            candidate = event.get("result")
            if not isinstance(candidate, dict):
                raise BOnlyWorkerError("worker success event lacks a result object")
            state["result"] = candidate
        else:
            state["failure"] = str(event.get("error_type", stage))
    return event


def _drain_worker_stdout(
    stream: IO[str],
    *,
    output: Path,
    state: dict[str, object],
    max_lines: int = 1_000,
    max_bytes: int = 1_048_576,
    max_seconds: float = 2.0,
) -> None:
    started = time.monotonic()
    lines = 0
    consumed = 0
    while True:
        if lines >= max_lines or consumed >= max_bytes or time.monotonic() - started > max_seconds:
            raise BOnlyWorkerError("bounded worker stdout drain exceeded its limit")
        line = stream.readline()
        if not line:
            return
        lines += 1
        consumed += len(line.encode())
        _record_worker_line(line, output=output, state=state)


def _write_terminal(
    output: Path, *, now: Callable[[], float], failure: str, exit_code: int | None
) -> None:
    terminal = output / "terminal.json"
    if terminal.exists():
        recovery = output / "terminal.recovery.json"
        if recovery.exists():
            return
        _atomic_terminal(
            recovery,
            {
                **FALSE_FLAGS,
                "status": "failed",
                "worker_exit_code": exit_code,
                "failure": failure,
                "recovery_for_existing_terminal": True,
                "ended_at_unix_s": now(),
            },
        )
        return
    _atomic_terminal(
        terminal,
        {
            **FALSE_FLAGS,
            "status": "failed",
            "worker_exit_code": exit_code,
            "failure": failure,
            "ended_at_unix_s": now(),
        },
    )


def supervise(
    *,
    eoc_zip: Path,
    output_dir: Path,
    config_path: Path,
    authorization_path: Path,
    runner_path: Path,
    now: Callable[[], float] = time.time,
) -> int:
    claimed = False
    output = output_dir.expanduser().absolute()
    process: subprocess.Popen[str] | None = None
    try:
        bindings, argv, environment, metadata = prepare_run_bindings(
            eoc_zip=eoc_zip,
            output_dir=output,
            config_path=config_path,
            authorization_path=authorization_path,
            runner_path=runner_path,
        )
        config = _regular(config_path, "B-only config")
        config_payload = json.loads(config.read_text())
        limits = config_payload["resources"]
        _existing_directory(output.parent, "output parent")
        output.mkdir(mode=0o700, exist_ok=False)
        claimed = True
        receipt = consume_authorization(
            authorization_path,
            expected_bindings=bindings,
            output_dir=output,
            now_unix_s=now(),
        )
        if str(receipt.absolute()) != metadata["authorization_receipt"]:
            raise BOnlyWorkerError("authorization receipt path drifted")
        receipt_payload = validate_consumption_receipt(receipt)
        start = now()
        _atomic(
            output / "start_manifest.json",
            {
                **FALSE_FLAGS,
                "run_kind": "KIT_F1_B_CAPACITY_SEQUENCE_V1",
                "started_at_unix_s": start,
                "bindings": bindings,
                "authorization": {
                    "authorization_id": receipt_payload["authorization_id"],
                    "authorization_sha256": receipt_payload["authorization_sha256"],
                    "nonce": receipt_payload["nonce"],
                    "receipt_sha256": _sha256(receipt),
                },
                "command": list(argv),
                "cwd": metadata["cwd"],
                "output": metadata["output"],
                "environment": environment,
                "engine": metadata["engine"],
                "python": sys.version,
                "platform": platform.platform(),
                "runtime_versions": {
                    package: importlib.metadata.version(package)
                    for package in ("numpy", "scikit-learn", "jsonschema", "threadpoolctl")
                },
                "artifact_allowlist": list(ARTIFACT_NAMES),
            },
        )
        _append(output / "lifecycle.jsonl", {"event": "reserved", "at_unix_s": start})
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
            env=environment,
            cwd=str(metadata["cwd"]),
        )
        if process.stdout is None:
            raise BOnlyWorkerError("worker stdout pipe unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        last_heartbeat = time.monotonic()
        worker_state: dict[str, object] = {
            "result": None,
            "failure": None,
            "terminal_event_count": 0,
        }
        failure: str | None = None
        soft_sent = False
        try:
            while process.poll() is None:
                elapsed = now() - start
                rss = _rss_bytes(process.pid)
                free = shutil.disk_usage(output).free
                _append(
                    output / "telemetry.jsonl",
                    {
                        "at_unix_s": now(),
                        "elapsed_s": elapsed,
                        "rss_bytes": rss,
                        "free_bytes": free,
                        "soft_drain_sent": soft_sent,
                    },
                )
                action = runtime_action(
                    elapsed_s=elapsed,
                    rss_bytes=rss,
                    free_bytes=free,
                    limits=limits,
                    soft_sent=soft_sent,
                )
                if action == "soft_drain":
                    _atomic(output / "soft_drain.request.json", {"requested_at_unix_s": now()})
                    os.killpg(process.pid, signal.SIGUSR1)
                    soft_sent = True
                    _append(
                        output / "lifecycle.jsonl", {"event": "soft_drain_sent", "at_unix_s": now()}
                    )
                elif action is not None:
                    failure = action
                    os.killpg(process.pid, signal.SIGKILL)
                    break
                for key, _ in selector.select(timeout=1.0):
                    line = cast(TextIO, key.fileobj).readline()
                    if not line:
                        continue
                    _record_worker_line(line, output=output, state=worker_state)
                    last_heartbeat = time.monotonic()
                    state_failure = worker_state.get("failure")
                    if isinstance(state_failure, str):
                        failure = state_failure
                if time.monotonic() - last_heartbeat > 30.0:
                    failure = "stale_heartbeat"
                    os.killpg(process.pid, signal.SIGKILL)
                    break
            process.wait(timeout=10)
            _drain_worker_stdout(
                process.stdout,
                output=output,
                state=worker_state,
            )
        finally:
            selector.close()
        state_failure = worker_state.get("failure")
        if isinstance(state_failure, str):
            failure = state_failure
        candidate_result = worker_state.get("result")
        result = candidate_result if isinstance(candidate_result, dict) else None
        success = (
            process.returncode == 0 and result is not None and failure is None and not soft_sent
        )
        if not success and failure is None:
            failure = "worker_exit_nonzero" if process.returncode else "missing_worker_result"
        hashes: dict[str, str] = {}
        if success and result is not None:
            hashes = _validate_success(output, result)
        if not success:
            _write_terminal(
                output,
                now=now,
                failure=str(failure),
                exit_code=process.returncode,
            )
            return 3
        _atomic(
            output / "final_manifest.json",
            {
                **FALSE_FLAGS,
                "status": "validated",
                "scientific_artifact_sha256": hashes,
            },
        )
        _atomic_terminal(
            output / "terminal.json",
            {
                **FALSE_FLAGS,
                "status": "completed",
                "worker_exit_code": process.returncode,
                "failure": None,
                "final_manifest_sha256": _sha256(output / "final_manifest.json"),
                "ended_at_unix_s": now(),
            },
        )
        return 0
    except Exception as error:  # noqa: BLE001 - all post-claim failures need evidence
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if claimed:
            try:
                _write_terminal(
                    output,
                    now=now,
                    failure=f"{type(error).__name__}: {error}",
                    exit_code=None if process is None else process.returncode,
                )
            except (OSError, ValueError, BOnlyWorkerError):
                pass
        return 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervised KIT F1 B-only development runner")
    parser.add_argument("--eoc-zip", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--authorization-path", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if argv is not None and "--worker" in argv:
        return worker_main(argv)
    if argv is None and "--worker" in sys.argv[1:]:
        return worker_main(sys.argv[1:])
    args = _parser().parse_args(argv)
    root = args.config_path.expanduser().absolute().parent.parent
    return supervise(
        eoc_zip=args.eoc_zip,
        output_dir=args.output_dir,
        config_path=args.config_path,
        authorization_path=args.authorization_path,
        runner_path=root / "scripts/run_kit_f1_b.py",
    )


if __name__ == "__main__":
    raise SystemExit(main())
