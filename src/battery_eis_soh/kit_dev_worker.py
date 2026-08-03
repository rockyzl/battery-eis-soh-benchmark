"""Fail-closed worker and parent supervisor for the KIT development engine.

The worker imports one hard-coded scientific callable.  The supervisor launches it with a fixed
argument grammar and owns all lifecycle, telemetry, terminal, and manifest records.  This module
does not authorize a scientific, protected-test, publication, or resume claim.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import inspect
import json
import math
import os
import platform
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast, get_type_hints

import numpy as np
from jsonschema import Draft202012Validator

from battery_eis_soh.kit_f1_b_authorization import sha256_bytes
from battery_eis_soh.kit_paired_authorization import (
    BINDING_KEYS as PAIRED_BINDING_KEYS,
)
from battery_eis_soh.kit_paired_authorization import RUN_KIND as PAIRED_RUN_KIND
from battery_eis_soh.kit_paired_authorization import (
    consume as consume_paired_authorization,
)
from battery_eis_soh.kit_paired_authorization import (
    peek_nonce as peek_paired_authorization_nonce,
)
from battery_eis_soh.kit_paired_authorization import (
    receipt_path as paired_authorization_receipt_path,
)
from battery_eis_soh.kit_paired_authorization import (
    validate_consumed as validate_consumed_paired_authorization,
)

ENGINE_MODULE = "battery_eis_soh.kit_dev_training"
ENGINE_CALLABLE = "execute_engineering_analysis"
ENGINE_PARAMETERS = ("eoc_zip", "eis_zip", "output_dir", "config_path", "progress")
ENGINE_TYPE_HINTS: dict[str, object] = {
    "eoc_zip": Path,
    "eis_zip": Path,
    "output_dir": Path,
    "config_path": Path,
    "progress": Callable[[dict[str, object]], None],
    "return": dict[str, object],
}
SCIENTIFIC_ARTIFACTS = (
    "acquisition_manifest.jsonl",
    "snapshot_manifest.jsonl",
    "fold_registry.json",
    "oof_predictions.jsonl",
    "paired_deltas.json",
    "retry_sensitivity.json",
    "engine_summary.json",
)
PARENT_ALWAYS_ARTIFACTS = (
    "start_manifest.json",
    "run_status.json",
    "lifecycle.jsonl",
    "telemetry.jsonl",
)
FINAL_MANIFEST = "final_manifest.json"
TERMINAL_ARTIFACTS = ("terminal.json", "failure.json")
FALSE_CLAIM_FLAGS: dict[str, bool] = {
    "scientific_result": False,
    "publication_ready": False,
    "resume_approved": False,
    "formal_model_result": False,
    "protected_test_result": False,
}
SCIENTIFIC_RESULT_FLAGS: dict[str, bool] = {
    **FALSE_CLAIM_FLAGS,
    "timestamp_semantics_provisional": True,
    "retry_semantics_provisional": True,
    "selection_performed": False,
}
SCIENTIFIC_FLAG_KEYS = frozenset(SCIENTIFIC_RESULT_FLAGS)
DEVELOPMENT_ERROR_CODES = frozenset(
    {
        "convergence_failed",
        "invalid_predictions",
        "invalid_input",
        "unknown_development_failure",
    }
)


class WorkerContractError(RuntimeError):
    """The frozen scientific worker contract is unavailable or has drifted."""


class SoftDrainCancellation(RuntimeError):
    """A soft-drain request was observed at a fit boundary."""


class SupervisorError(RuntimeError):
    """The parent supervisor cannot safely continue."""


def _allowlisted_development_error_code(value: object) -> str:
    """Return only a stable, non-sensitive development failure category."""
    return (
        value
        if isinstance(value, str) and value in DEVELOPMENT_ERROR_CODES
        else "unknown_development_failure"
    )


def _sanitize_worker_message(message: Mapping[str, object]) -> dict[str, object]:
    """Remove untrusted diagnostic detail from worker failure messages."""
    if message.get("stage") != "worker_failure":
        return dict(message)
    error_type = (
        "DevelopmentRunError"
        if message.get("error_type") == "DevelopmentRunError"
        else "UnknownDevelopmentError"
    )
    return {
        "stage": "worker_failure",
        "error_type": error_type,
        "error_code": _allowlisted_development_error_code(message.get("error_code")),
    }


@dataclass(frozen=True)
class SupervisorLimits:
    telemetry_interval_s: float = 5.0
    soft_drain_s: float = 420.0
    terminate_s: float = 450.0
    kill_s: float = 480.0
    stale_s: float = 30.0
    rss_soft_bytes: int = 1_600 * 1024 * 1024
    rss_hard_bytes: int = 2 * 1024 * 1024 * 1024
    cleanup_poll_s: float = 0.1
    cleanup_term_grace_s: float = 2.0
    cleanup_kill_grace_s: float = 2.0

    def validate(self) -> None:
        if not 0 < self.telemetry_interval_s <= 10:
            raise SupervisorError("telemetry interval must be in (0, 10] seconds")
        if not 0 < self.soft_drain_s < self.terminate_s < self.kill_s:
            raise SupervisorError("deadlines must satisfy 0 < soft < terminate < kill")
        if self.stale_s <= 0:
            raise SupervisorError("stale threshold must be positive")
        if not 0 < self.rss_soft_bytes < self.rss_hard_bytes:
            raise SupervisorError("RSS thresholds must satisfy 0 < soft < hard")
        if not 0 < self.cleanup_poll_s <= 1:
            raise SupervisorError("cleanup poll interval must be in (0, 1] seconds")
        if self.cleanup_term_grace_s <= 0 or self.cleanup_kill_grace_s <= 0:
            raise SupervisorError("cleanup grace periods must be positive")


@dataclass(frozen=True)
class ProcessTelemetry:
    cpu_seconds: float
    rss_bytes: int
    process_count: int


@dataclass(frozen=True)
class ParentEvidenceBinding:
    """Exact parent-owned bytes, plus hash-chain state when the file is an event log."""

    expected_bytes: bytes
    sha256: str
    sequence: int | None = None
    chain_head: str | None = None


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def time(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class ProcessHandle(Protocol):
    pid: int


class ProcessController(Protocol):
    def start(self, argv: Sequence[str]) -> ProcessHandle: ...

    def poll(self, process: ProcessHandle) -> int | None: ...

    def telemetry(self, process: ProcessHandle) -> ProcessTelemetry: ...

    def messages(self, process: ProcessHandle) -> list[dict[str, object]]: ...

    def send_group(self, process: ProcessHandle, signal_number: int) -> None: ...


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_regular_file(path: Path, label: str) -> Path:
    expanded = path.expanduser()
    try:
        metadata = expanded.lstat()
    except FileNotFoundError as error:
        raise SupervisorError(f"{label} does not exist") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SupervisorError(f"{label} must be a regular non-symlink file")
    return expanded.resolve(strict=True)


def _safe_existing_directory(path: Path, label: str) -> Path:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    try:
        for component in absolute.parts[1:]:
            current /= component
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise SupervisorError(f"{label} contains a symlink or non-directory component")
    except FileNotFoundError as error:
        raise SupervisorError(f"{label} does not exist") from error
    return absolute


def validate_engine_contract(module: ModuleType | None = None) -> dict[str, str]:
    """Validate and fingerprint the exact scientific callable used by the child."""
    loaded = importlib.import_module(ENGINE_MODULE) if module is None else module
    module_path_value = getattr(loaded, "__file__", None)
    if not isinstance(module_path_value, str):
        raise WorkerContractError("scientific engine module has no source file")
    module_path = _safe_regular_file(Path(module_path_value), "scientific engine module")
    function = getattr(loaded, ENGINE_CALLABLE, None)
    if not callable(function):
        raise WorkerContractError("scientific engine callable is missing")
    signature = inspect.signature(function)
    parameters = tuple(signature.parameters.values())
    if tuple(parameter.name for parameter in parameters) != ENGINE_PARAMETERS:
        raise WorkerContractError("scientific engine parameter names drifted")
    if any(parameter.kind is not inspect.Parameter.KEYWORD_ONLY for parameter in parameters):
        raise WorkerContractError("scientific engine parameters must be keyword-only")
    if any(parameter.default is not inspect.Parameter.empty for parameter in parameters):
        raise WorkerContractError("scientific engine parameters must all be required")
    try:
        type_hints = get_type_hints(function)
    except (NameError, TypeError) as error:
        raise WorkerContractError("scientific engine annotations cannot be resolved") from error
    if type_hints != ENGINE_TYPE_HINTS:
        raise WorkerContractError("scientific engine annotations drifted")
    return {
        "module": ENGINE_MODULE,
        "callable": ENGINE_CALLABLE,
        "signature": str(signature),
        "module_sha256": _sha256_file(module_path),
    }


def verify_engine_source(expected_sha256: str, module: ModuleType | None = None) -> None:
    if len(expected_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        raise WorkerContractError("expected engine SHA256 is malformed")
    if module is None:
        specification = importlib.util.find_spec(ENGINE_MODULE)
        origin = None if specification is None else specification.origin
        if origin is None:
            raise WorkerContractError("scientific engine source cannot be located")
        source = _safe_regular_file(Path(origin), "scientific engine source")
    else:
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str):
            raise WorkerContractError("scientific engine module has no source file")
        source = _safe_regular_file(Path(origin), "scientific engine source")
    if _sha256_file(source) != expected_sha256:
        raise WorkerContractError("scientific engine SHA256 mismatch")


def fixed_worker_argv(
    *,
    eoc_zip: Path,
    eis_zip: Path,
    output_dir: Path,
    config_path: Path,
    expected_engine_sha256: str,
    authorized_output_dir: Path | None = None,
    authorization_path: Path | None = None,
    authorization_receipt: Path | None = None,
    runner_path: Path | None = None,
) -> tuple[str, ...]:
    """Return the only child command shape the supervisor will execute."""
    base = (
        sys.executable,
        "-m",
        "battery_eis_soh.kit_dev_worker",
        "--worker",
        "--eoc-zip",
        str(eoc_zip),
        "--eis-zip",
        str(eis_zip),
        "--output-dir",
        str(output_dir),
        "--config-path",
        str(config_path),
    )
    auth_values = (
        authorized_output_dir,
        authorization_path,
        authorization_receipt,
        runner_path,
    )
    if all(value is None for value in auth_values):
        return (*base, "--expected-engine-sha256", expected_engine_sha256)
    if any(value is None for value in auth_values):
        raise WorkerContractError("paired authorization child arguments are incomplete")
    return (
        *base,
        "--authorized-output-dir",
        str(authorized_output_dir),
        "--authorization-path",
        str(authorization_path),
        "--authorization-receipt",
        str(authorization_receipt),
        "--runner-path",
        str(runner_path),
        "--expected-engine-sha256",
        expected_engine_sha256,
    )


def _source_tree_sha256(root: Path, runner: Path, config: Path, schema: Path) -> str:
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
        regular = _safe_regular_file(candidate, "paired source-tree input")
        rows.append(
            {
                "path": regular.relative_to(root).as_posix(),
                "sha256": _sha256_file(regular),
            }
        )
    return sha256_bytes(_canonical_json(sorted(rows, key=lambda row: row["path"])).encode())


def _paired_scientific_staging_path(output: Path) -> Path:
    absolute = output.expanduser().absolute()
    return absolute.parent / f".{absolute.name}.scientific-staging"


def prepare_paired_run_bindings(
    *,
    eoc_zip: Path,
    eis_zip: Path,
    output_dir: Path,
    config_path: Path,
    authorization_path: Path,
    runner_path: Path,
    expected_engine_sha256: str | None = None,
) -> tuple[dict[str, str], tuple[str, ...], dict[str, object]]:
    """Freeze the exact paired child command and all externally reviewed run identities."""
    eoc = _safe_regular_file(eoc_zip, "EOC archive")
    eis = _safe_regular_file(eis_zip, "EIS archive")
    config = _safe_regular_file(config_path, "paired config")
    schema = _safe_regular_file(
        config.with_name(f"{config.stem}.schema.json"), "paired config schema"
    )
    runner = _safe_regular_file(runner_path, "paired runner")
    root = config.parent.parent
    worker = _safe_regular_file(Path(__file__), "paired worker source")
    loader = _safe_regular_file(root / "src/battery_eis_soh/kit_v2.py", "paired loader source")
    lockfile = _safe_regular_file(root / "uv.lock", "dependency lockfile")
    engine_source = _safe_regular_file(
        root / "src/battery_eis_soh/kit_dev_training.py", "paired engine source"
    )
    observed_engine_sha256 = _sha256_file(engine_source)
    engine_sha256 = expected_engine_sha256 or observed_engine_sha256
    if engine_sha256 != observed_engine_sha256:
        raise WorkerContractError("paired engine hash changed before binding preparation")
    readiness = json.loads(config.read_text())["readiness"]
    readiness_path = _safe_regular_file(
        (config.parent / str(readiness["eis_readiness_artifact_path"])).resolve(),
        "paired readiness artifact",
    )
    paired_designs = readiness["paired_design_sha256_by_mode"]
    if not isinstance(paired_designs, dict):
        raise WorkerContractError("paired design identities are missing")
    output = output_dir.expanduser().absolute()
    staging = _paired_scientific_staging_path(output)
    authorization = _safe_regular_file(authorization_path, "paired authorization")
    nonce = peek_paired_authorization_nonce(authorization)
    receipt = paired_authorization_receipt_path(authorization, nonce)
    argv = fixed_worker_argv(
        eoc_zip=eoc,
        eis_zip=eis,
        output_dir=staging,
        authorized_output_dir=output,
        config_path=config,
        authorization_path=authorization,
        authorization_receipt=receipt,
        runner_path=runner,
        expected_engine_sha256=engine_sha256,
    )
    cwd = Path.cwd().absolute()
    bindings = {
        "eoc_sha256": _sha256_file(eoc),
        "eis_sha256": _sha256_file(eis),
        "config_sha256": _sha256_file(config),
        "schema_sha256": _sha256_file(schema),
        "readiness_sha256": _sha256_file(readiness_path),
        "engine_sha256": engine_sha256,
        "worker_sha256": _sha256_file(worker),
        "runner_sha256": _sha256_file(runner),
        "loader_sha256": _sha256_file(loader),
        "lockfile_sha256": _sha256_file(lockfile),
        "source_tree_sha256": _source_tree_sha256(root, runner, config, schema),
        "command_sha256": sha256_bytes(_canonical_json(argv).encode()),
        "cwd_sha256": sha256_bytes(str(cwd).encode()),
        "output_sha256": sha256_bytes(str(output).encode()),
        "paired_cohort_sha256": str(readiness["paired_cohort_identity_sha256"]),
        "paired_designs_sha256": sha256_bytes(_canonical_json(paired_designs).encode()),
        "trial_keys_sha256": str(readiness["planned_trial_keys_sha256"]),
    }
    if set(bindings) != PAIRED_BINDING_KEYS:
        raise WorkerContractError("paired runtime binding set drifted")
    metadata: dict[str, object] = {
        "authorization_nonce": nonce,
        "authorization_receipt": str(receipt),
        "cwd": str(cwd),
        "engine_sha256": engine_sha256,
        "output": str(output),
        "scientific_staging": str(staging),
    }
    return bindings, argv, metadata


def _parse_proc_stat(text: str) -> tuple[int, int, int, int]:
    """Return pgrp, utime, stime, and RSS from Linux proc stat text.

    The parenthesized ``comm`` field may contain whitespace and parentheses, so a plain split is
    not safe.  Linux places the final closing parenthesis immediately before the state field.
    """
    closing = text.rfind(")")
    if closing < 0:
        raise ValueError("malformed /proc stat record")
    fields = text[closing + 1 :].split()
    # fields starts at stat field 3 (state): pgrp=5, utime=14, stime=15, rss=24.
    if len(fields) < 22:
        raise ValueError("truncated /proc stat record")
    return int(fields[2]), int(fields[11]), int(fields[12]), int(fields[21])


class RealProcessController:
    """Linux process-group controller with read-only /proc telemetry."""

    def __init__(self) -> None:
        self._selector = selectors.DefaultSelector()
        self._buffers: dict[int, str] = {}

    def start(self, argv: Sequence[str]) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            shell=False,
            start_new_session=True,
            close_fds=True,
        )
        if process.stdout is None:  # pragma: no cover - defensive subprocess invariant
            raise SupervisorError("worker stdout pipe was not created")
        os.set_blocking(process.stdout.fileno(), False)
        self._selector.register(process.stdout, selectors.EVENT_READ, process.pid)
        self._buffers[process.pid] = ""
        return process

    def poll(self, process: ProcessHandle) -> int | None:
        if not isinstance(process, subprocess.Popen):
            raise SupervisorError("unexpected process handle")
        return process.poll()

    def telemetry(self, process: ProcessHandle) -> ProcessTelemetry:
        target_group = process.pid
        clock_ticks = os.sysconf("SC_CLK_TCK")
        page_size = os.sysconf("SC_PAGE_SIZE")
        cpu_ticks = 0
        rss_pages = 0
        count = 0
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                process_group, user_ticks, system_ticks, resident_pages = _parse_proc_stat(
                    (entry / "stat").read_text()
                )
                if process_group != target_group:
                    continue
                cpu_ticks += user_ticks + system_ticks
                rss_pages += max(0, resident_pages)
                count += 1
            except (FileNotFoundError, PermissionError, IndexError, ValueError):
                continue
        return ProcessTelemetry(cpu_ticks / clock_ticks, rss_pages * page_size, count)

    def messages(self, process: ProcessHandle) -> list[dict[str, object]]:
        if not isinstance(process, subprocess.Popen) or process.stdout is None:
            return []
        buffer = self._buffers.get(process.pid, "")
        for key, _ in self._selector.select(timeout=0):
            if key.data != process.pid:
                continue
            try:
                chunk = os.read(process.stdout.fileno(), 65_536).decode("utf-8", errors="replace")
            except BlockingIOError:
                chunk = ""
            buffer += chunk
        lines = buffer.split("\n")
        self._buffers[process.pid] = lines.pop()
        messages: list[dict[str, object]] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                value = {
                    "stage": "worker_output_rejected",
                    "line_sha256": hashlib.sha256(line.encode()).hexdigest(),
                }
            if isinstance(value, dict):
                messages.append(value)
        return messages

    def send_group(self, process: ProcessHandle, signal_number: int) -> None:
        try:
            os.killpg(process.pid, signal_number)
        except ProcessLookupError:
            return


class HashChainWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.previous_hash = "0" * 64
        self.sequence = 0
        self._expected_bytes = bytearray()
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def append(self, payload: Mapping[str, object]) -> None:
        event = {
            **FALSE_CLAIM_FLAGS,
            **payload,
            "sequence": self.sequence,
            "previous_hash": self.previous_hash,
        }
        event_hash = hashlib.sha256(_canonical_json(event).encode()).hexdigest()
        row = {**event, "event_hash": event_hash}
        line = (_canonical_json(row) + "\n").encode("utf-8")
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self._expected_bytes.extend(line)
        self.previous_hash = event_hash
        self.sequence += 1

    def binding(self) -> ParentEvidenceBinding:
        expected = bytes(self._expected_bytes)
        return ParentEvidenceBinding(
            expected_bytes=expected,
            sha256=hashlib.sha256(expected).hexdigest(),
            sequence=self.sequence,
            chain_head=self.previous_hash,
        )


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _binding(expected: bytes) -> ParentEvidenceBinding:
    return ParentEvidenceBinding(
        expected_bytes=expected, sha256=hashlib.sha256(expected).hexdigest()
    )


def _write_immutable_json(path: Path, payload: Mapping[str, object]) -> ParentEvidenceBinding:
    content = _json_bytes(payload)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return _binding(content)


def _write_atomic_json(path: Path, payload: Mapping[str, object]) -> ParentEvidenceBinding:
    content = _json_bytes(payload)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return _binding(content)


def _prepare_output(path: Path) -> Path:
    expanded = path.expanduser().absolute()
    parent = _safe_existing_directory(expanded.parent, "output parent")
    claimed = parent / expanded.name
    try:
        claimed.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as error:
        raise SupervisorError("output directory must not already exist") from error
    return claimed


def _prepare_scientific_staging(output: Path) -> Path:
    staging = _paired_scientific_staging_path(output)
    try:
        staging.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as error:
        raise SupervisorError("scientific staging path already exists") from error
    metadata = staging.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SupervisorError("scientific staging must be a real directory")
    if any(staging.iterdir()):
        raise SupervisorError("scientific staging must start empty")
    return staging


def _remove_staging(staging: Path) -> None:
    if staging.exists() and not staging.is_symlink():
        shutil.rmtree(staging)


def _require_false_claim_flags(payload: object, *, location: str) -> dict[str, object]:
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise SupervisorError(f"{location} must be a JSON object")
    typed = {str(key): value for key, value in payload.items()}
    for name in FALSE_CLAIM_FLAGS:
        if type(typed.get(name)) is not bool or typed[name] is not False:
            raise SupervisorError(f"{location} must contain exact false boolean {name}")
    return typed


def _require_scientific_result_flags(payload: object, *, location: str) -> dict[str, object]:
    typed = _require_false_claim_flags(payload, location=location)
    for name, expected in SCIENTIFIC_RESULT_FLAGS.items():
        if type(typed.get(name)) is not bool or typed[name] is not expected:
            raise SupervisorError(
                f"{location} must contain exact boolean {name}={str(expected).lower()}"
            )
    return typed


FieldValidator = Callable[[object, str], None]


def _expect_exact_fields(
    value: object,
    fields: Mapping[str, FieldValidator],
    location: str,
    *,
    scientific_flags: bool = False,
) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SupervisorError(f"{location} must be a JSON object")
    payload = {str(key): nested for key, nested in value.items()}
    expected = set(fields) | (set(SCIENTIFIC_FLAG_KEYS) if scientific_flags else set())
    if set(payload) != expected:
        missing = sorted(expected - set(payload))
        unknown = sorted(set(payload) - expected)
        raise SupervisorError(
            f"{location} fields do not match reviewed schema; missing={missing}, unknown={unknown}"
        )
    if scientific_flags:
        _require_scientific_result_flags(payload, location=location)
    for name, validator in fields.items():
        validator(payload[name], f"{location}.{name}")
    return payload


def _string(value: object, location: str) -> None:
    if not isinstance(value, str) or not value:
        raise SupervisorError(f"{location} must be a non-empty string")


def _nullable_string(value: object, location: str) -> None:
    if value is not None:
        _string(value, location)


def _exact_string(expected: str) -> FieldValidator:
    def validate(value: object, location: str) -> None:
        if value != expected:
            raise SupervisorError(f"{location} must equal {expected!r}")

    return validate


def _one_of_strings(allowed: frozenset[str]) -> FieldValidator:
    def validate(value: object, location: str) -> None:
        if not isinstance(value, str) or value not in allowed:
            raise SupervisorError(f"{location} is not an allowed string")

    return validate


def _integer(value: object, location: str) -> None:
    if type(value) is not int:
        raise SupervisorError(f"{location} must be an integer")


def _nonnegative_integer(value: object, location: str) -> None:
    if type(value) is not int:
        raise SupervisorError(f"{location} must be an integer")
    if value < 0:
        raise SupervisorError(f"{location} must be non-negative")


def _binary_integer(value: object, location: str) -> None:
    if type(value) is not int or value not in {0, 1}:
        raise SupervisorError(f"{location} must be integer 0 or 1")


def _finite_float(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SupervisorError(f"{location} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SupervisorError(f"{location} must be a finite number")
    return result


def _finite_number(value: object, location: str) -> None:
    _finite_float(value, location)


def _nullable_finite_number(value: object, location: str) -> None:
    if value is not None:
        _finite_number(value, location)


def _probability(value: object, location: str) -> None:
    if not 0.0 <= _finite_float(value, location) <= 1.0:
        raise SupervisorError(f"{location} must be within [0, 1]")


def _boolean(value: object, location: str) -> None:
    if type(value) is not bool:
        raise SupervisorError(f"{location} must be a boolean")


def _sha256(value: object, location: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SupervisorError(f"{location} must be a lowercase SHA-256 hex digest")


def _list_of(
    item_validator: FieldValidator,
    *,
    length: int | None = None,
) -> FieldValidator:
    def validate(value: object, location: str) -> None:
        if not isinstance(value, list):
            raise SupervisorError(f"{location} must be an array")
        if length is not None and len(value) != length:
            raise SupervisorError(f"{location} must contain exactly {length} items")
        for index, item in enumerate(value):
            item_validator(item, f"{location}[{index}]")

    return validate


def _string_integer_map(value: object, location: str) -> None:
    if not isinstance(value, dict) or not all(isinstance(key, str) and key for key in value):
        raise SupervisorError(f"{location} must be a string-keyed object")
    for key, nested in value.items():
        _nonnegative_integer(nested, f"{location}.{key}")


def _weight_row(value: object, location: str) -> None:
    _expect_exact_fields(
        value, {"snapshot_id": _string, "weight_mean_one": _finite_number}, location
    )


def _class_support(value: object, location: str) -> None:
    names = (
        "snapshots",
        "events",
        "non_events",
        "cells",
        "conditions",
        "positive_supporting_conditions",
        "negative_supporting_conditions",
    )
    _expect_exact_fields(value, {name: _nonnegative_integer for name in names}, location)


def _fold_support_row(value: object, location: str) -> None:
    def inner_row(nested: object, nested_location: str) -> None:
        fields = {"inner_fold": _nonnegative_integer}
        fields.update(
            {
                name: _nonnegative_integer
                for name in (
                    "snapshots",
                    "events",
                    "non_events",
                    "cells",
                    "conditions",
                    "positive_supporting_conditions",
                    "negative_supporting_conditions",
                )
            }
        )
        _expect_exact_fields(nested, fields, nested_location)

    _expect_exact_fields(
        value,
        {
            "outer_fold": _nonnegative_integer,
            "validation": _class_support,
            "inner_validation": _list_of(inner_row),
        },
        location,
    )


def _mode_support(value: object, location: str) -> None:
    _expect_exact_fields(
        value, {"overall": _class_support, "folds": _list_of(_fold_support_row)}, location
    )


def _two_modes(validator: FieldValidator) -> FieldValidator:
    def validate(value: object, location: str) -> None:
        _expect_exact_fields(value, {"cyclic": validator, "profile": validator}, location)

    return validate


def _paired_design(value: object, location: str) -> None:
    def inner_fold(nested: object, nested_location: str) -> None:
        _expect_exact_fields(
            nested,
            {
                "inner_fold": _nonnegative_integer,
                "training_weights": _list_of(_weight_row),
                "validation_weights": _list_of(_weight_row),
            },
            nested_location,
        )

    def outer_fold(nested: object, nested_location: str) -> None:
        _expect_exact_fields(
            nested,
            {
                "outer_fold": _nonnegative_integer,
                "training_weights": _list_of(_weight_row),
                "validation_weights": _list_of(_weight_row),
                "inner_folds": _list_of(inner_fold),
            },
            nested_location,
        )

    _expect_exact_fields(
        value,
        {
            "applies_identically_to_feature_views": _list_of(
                _one_of_strings(frozenset({"B", "D_CORE"})), length=2
            ),
            "applies_identically_to_acquisition_policies": _list_of(
                _one_of_strings(frozenset({"primary", "keep_last_sensitivity"})), length=2
            ),
            "ordered_cohort_snapshot_ids": _list_of(_string),
            "metric_weights": _list_of(_weight_row),
            "folds": _list_of(outer_fold),
            "paired_design_sha256": _sha256,
        },
        location,
    )


def _fold_registry_row(value: object, location: str) -> None:
    def inner(nested: object, nested_location: str) -> None:
        _expect_exact_fields(
            nested,
            {
                "train_snapshot_ids": _list_of(_string),
                "validation_snapshot_ids": _list_of(_string),
            },
            nested_location,
        )

    _expect_exact_fields(
        value,
        {
            "outer_fold": _nonnegative_integer,
            "train_snapshot_ids": _list_of(_string),
            "validation_snapshot_ids": _list_of(_string),
            "inner_folds": _list_of(inner),
        },
        location,
    )


def _metric_payload(value: object, location: str) -> None:
    _expect_exact_fields(
        value,
        {
            "metric_id": _exact_string("condition_balanced_pooled_pr_auc_v1"),
            "condition_balanced_pooled_pr_auc_v1": _probability,
            "condition_balanced_pooled_brier_v1": _finite_number,
            "snapshots": _nonnegative_integer,
            "events": _nonnegative_integer,
            "non_events": _nonnegative_integer,
            "cells": _nonnegative_integer,
            "conditions": _nonnegative_integer,
            "positive_supporting_conditions": _nonnegative_integer,
            "negative_supporting_conditions": _nonnegative_integer,
            "weighted_event_mass": _probability,
        },
        location,
    )


def _metric_row(value: object, location: str) -> None:
    _expect_exact_fields(
        value,
        {
            "mode": _one_of_strings(frozenset({"cyclic", "profile"})),
            "acquisition_policy": _one_of_strings(frozenset({"primary", "keep_last_sensitivity"})),
            "view": _one_of_strings(frozenset({"B", "D_CORE"})),
            "model_family": _one_of_strings(
                frozenset({"logistic_elastic_net", "hist_gradient_boosting"})
            ),
            "paired_design_sha256": _sha256,
            "calibrated": _metric_payload,
            "raw": _metric_payload,
        },
        location,
        scientific_flags=True,
    )


def _bootstrap_payload(value: object, location: str) -> None:
    _expect_exact_fields(
        value,
        {
            "draws": _nonnegative_integer,
            "seed": _nonnegative_integer,
            "valid_draws": _nonnegative_integer,
            "invalid_draws": _nonnegative_integer,
            "minimum_valid_draws": _nonnegative_integer,
            "interval_valid": _boolean,
            "pr_auc_delta_percentile_95": lambda nested, where: (
                _list_of(_finite_number, length=2)(nested, where) if nested is not None else None
            ),
            "brier_delta_percentile_95": lambda nested, where: (
                _list_of(_finite_number, length=2)(nested, where) if nested is not None else None
            ),
        },
        location,
    )


def _paired_delta_row(value: object, location: str) -> None:
    _expect_exact_fields(
        value,
        {
            "mode": _one_of_strings(frozenset({"cyclic", "profile"})),
            "model_family": _one_of_strings(
                frozenset({"logistic_elastic_net", "hist_gradient_boosting"})
            ),
            "acquisition_policy": _one_of_strings(frozenset({"primary", "keep_last_sensitivity"})),
            "same_snapshot_ids_and_weights": _boolean,
            "pr_auc_delta_d_core_minus_b": _finite_number,
            "brier_delta_d_core_minus_b": _finite_number,
            "bootstrap": _bootstrap_payload,
        },
        location,
        scientific_flags=True,
    )


def _retry_row(value: object, location: str) -> None:
    def stop_margins(nested: object, nested_location: str) -> None:
        _expect_exact_fields(
            nested,
            {
                "pr_auc_delta_absolute_change": _finite_number,
                "brier_delta_absolute_change": _finite_number,
                "stop_on_sign_reversal": _boolean,
            },
            nested_location,
        )

    _expect_exact_fields(
        value,
        {
            "mode": _one_of_strings(frozenset({"cyclic", "profile"})),
            "model_family": _one_of_strings(
                frozenset({"logistic_elastic_net", "hist_gradient_boosting"})
            ),
            "primary_policy": _exact_string("equal_finite_mean_per_frequency_v1"),
            "sensitivity_policy": _exact_string("keep_last_acquisition_v1"),
            "pr_auc_delta_sign_reversal": _boolean,
            "pr_auc_delta_absolute_change": _finite_number,
            "brier_delta_absolute_change": _finite_number,
            "stop_margins": stop_margins,
            "prohibit_eis_increment_conclusion": _boolean,
            "decision_role": _exact_string("diagnostic_stop_only_never_promotion"),
        },
        location,
        scientific_flags=True,
    )


def _load_json_object(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SupervisorError(f"invalid JSON artifact: {path.name}") from error


def _load_jsonl(path: Path) -> list[object]:
    parsed: list[object] = []
    rows = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise SupervisorError(f"blank JSONL row: {path.name}:{line_number}")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise SupervisorError(
                        f"invalid JSONL artifact: {path.name}:{line_number}"
                    ) from error
                parsed.append(value)
                rows += 1
    except UnicodeDecodeError as error:
        raise SupervisorError(f"invalid UTF-8 artifact: {path.name}") from error
    if rows == 0:
        raise SupervisorError(f"JSONL artifact must not be empty: {path.name}")
    return parsed


def _public_hierarchical_weights(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    """Independently reconstruct condition/cell/checkup mean-one weights from artifact rows."""
    conditions = {row["condition_id"] for row in rows}
    cells_by_condition: dict[object, set[object]] = {}
    checkups_by_cell: Counter[tuple[object, object]] = Counter()
    for row in rows:
        condition, cell = row["condition_id"], row["cell_id"]
        cells_by_condition.setdefault(condition, set()).add(cell)
        checkups_by_cell[(condition, cell)] += 1
    raw = {
        str(row["snapshot_id"]): 1.0
        / (
            len(conditions)
            * len(cells_by_condition[row["condition_id"]])
            * checkups_by_cell[(row["condition_id"], row["cell_id"])]
        )
        for row in rows
    }
    mean = math.fsum(raw.values()) / len(raw)
    return {key: value / mean for key, value in raw.items()}


def _public_weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    ordered_values = values[order]
    cumulative = np.cumsum(weights[order])
    index = int(np.searchsorted(cumulative, 0.5 * float(np.sum(weights)), side="left"))
    return float(ordered_values[min(index, len(ordered_values) - 1)])


def _public_regression_metric_packet(
    snapshot_rows: Sequence[dict[str, object]], prediction_rows: Sequence[dict[str, object]]
) -> dict[str, object]:
    snapshots = {str(row["snapshot_id"]): row for row in snapshot_rows}
    predictions = {str(row["snapshot_id"]): row for row in prediction_rows}
    if (
        not snapshots
        or len(snapshots) != len(snapshot_rows)
        or len(predictions) != len(prediction_rows)
        or set(snapshots) != set(predictions)
    ):
        raise SupervisorError("F1-v2 metric rows do not cover one exact mode cohort")
    ordered_ids = sorted(snapshots)
    weights_by_id = _public_hierarchical_weights([snapshots[value] for value in ordered_ids])
    targets = np.asarray(
        [float(cast("int | float", snapshots[value]["soh_loss_pp_h3"])) for value in ordered_ids]
    )
    predicted = np.asarray(
        [
            float(cast("int | float", predictions[value]["predicted_soh_loss_pp_h3"]))
            for value in ordered_ids
        ]
    )
    weights = np.asarray([weights_by_id[value] for value in ordered_ids])
    for value in ordered_ids:
        observed = float(cast("int | float", predictions[value]["observed_soh_loss_pp_h3"]))
        if not math.isclose(
            observed,
            float(cast("int | float", snapshots[value]["soh_loss_pp_h3"])),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise SupervisorError("F1-v2 metric prediction truth differs from snapshot target")
    errors = predicted - targets
    absolute = np.abs(errors)
    squared = errors**2
    target_mean = float(np.average(targets, weights=weights))
    denominator = float(np.sum(weights * (targets - target_mean) ** 2))
    r2 = None if denominator <= 1e-24 else 1.0 - float(np.sum(weights * squared)) / denominator
    return {
        "metric_id": "condition_balanced_mae_pp_v1",
        "condition_balanced_mae_pp_v1": float(np.average(absolute, weights=weights)),
        "condition_balanced_median_ae_pp_v1": _public_weighted_median(absolute, weights),
        "condition_balanced_rmse_pp_v1": math.sqrt(float(np.average(squared, weights=weights))),
        "condition_balanced_signed_bias_pp_v1": float(np.average(errors, weights=weights)),
        "condition_balanced_r2_v1": r2,
        "r2_role": "secondary_only",
        "snapshots": len(snapshot_rows),
        "cells": len({row["cell_id"] for row in snapshot_rows}),
        "conditions": len({row["condition_id"] for row in snapshot_rows}),
        "negative_loss_snapshots": int(np.sum(targets < 0.0)),
    }


def _public_paired_regression_bootstrap(
    snapshot_rows: Sequence[dict[str, object]],
    b_rows: Sequence[dict[str, object]],
    d_rows: Sequence[dict[str, object]],
) -> dict[str, object]:
    snapshots = {str(row["snapshot_id"]): row for row in snapshot_rows}
    b_by_id = {str(row["snapshot_id"]): row for row in b_rows}
    d_by_id = {str(row["snapshot_id"]): row for row in d_rows}
    if set(snapshots) != set(b_by_id) or set(snapshots) != set(d_by_id):
        raise SupervisorError("F1-v2 bootstrap B/D snapshot membership differs")
    ordered_ids = sorted(snapshots)
    targets = np.asarray(
        [float(cast("int | float", snapshots[value]["soh_loss_pp_h3"])) for value in ordered_ids]
    )
    b_values = np.asarray(
        [
            float(cast("int | float", b_by_id[value]["predicted_soh_loss_pp_h3"]))
            for value in ordered_ids
        ]
    )
    d_values = np.asarray(
        [
            float(cast("int | float", d_by_id[value]["predicted_soh_loss_pp_h3"]))
            for value in ordered_ids
        ]
    )
    condition_indices: dict[int, list[int]] = {}
    for index, value in enumerate(ordered_ids):
        condition = int(cast("int", snapshots[value]["condition_id"]))
        condition_indices.setdefault(condition, []).append(index)
    conditions = sorted(condition_indices)
    base_weights = _public_hierarchical_weights([snapshots[value] for value in ordered_ids])
    weight_values = np.asarray([base_weights[value] for value in ordered_ids])
    rng = np.random.default_rng(20260801)
    mae_deltas = np.empty(2000, dtype=np.float64)
    rmse_deltas = np.empty(2000, dtype=np.float64)
    for draw_index in range(2000):
        sampled = rng.choice(conditions, size=len(conditions), replace=True)
        sampled_indices = np.concatenate(
            [np.asarray(condition_indices[int(condition)], dtype=np.int64) for condition in sampled]
        )
        # A repeated condition is a distinct draw slot. Reusing its within-condition weights is
        # algebraically identical to relabelling the slot's condition/cell identities.
        weights = weight_values[sampled_indices]
        truth = targets[sampled_indices]
        b_error = b_values[sampled_indices] - truth
        d_error = d_values[sampled_indices] - truth
        mae_deltas[draw_index] = float(np.average(np.abs(d_error), weights=weights)) - float(
            np.average(np.abs(b_error), weights=weights)
        )
        rmse_deltas[draw_index] = math.sqrt(
            float(np.average(d_error**2, weights=weights))
        ) - math.sqrt(float(np.average(b_error**2, weights=weights)))
    return {
        "method": "whole_condition_paired_bootstrap_v1",
        "draws": 2000,
        "valid_draws": 2000,
        "minimum_valid_draws": 1900,
        "seed": 20260801,
        "interval_percent": 95,
        "interval_valid": True,
        "mae_delta_percentile_95": [
            float(value) for value in np.percentile(mae_deltas, [2.5, 97.5])
        ],
        "rmse_delta_percentile_95": [
            float(value) for value in np.percentile(rmse_deltas, [2.5, 97.5])
        ],
    }


def _assert_public_structure(observed: object, expected: object, label: str) -> None:
    if isinstance(expected, dict):
        if not isinstance(observed, dict) or set(observed) != set(expected):
            raise SupervisorError(f"F1-v2 {label} fields drifted")
        for key, value in expected.items():
            _assert_public_structure(observed[key], value, f"{label}.{key}")
    elif isinstance(expected, list):
        if not isinstance(observed, list) or len(observed) != len(expected):
            raise SupervisorError(f"F1-v2 {label} list drifted")
        for index, value in enumerate(expected):
            _assert_public_structure(observed[index], value, f"{label}[{index}]")
    elif isinstance(expected, float):
        if (
            not isinstance(observed, (int, float))
            or isinstance(observed, bool)
            or not math.isclose(float(observed), expected, rel_tol=1e-12, abs_tol=1e-12)
        ):
            raise SupervisorError(f"F1-v2 {label} numeric value drifted")
    elif observed != expected:
        raise SupervisorError(f"F1-v2 {label} value drifted")


def _public_metric_difference(
    d_packet: Mapping[str, object], b_packet: Mapping[str, object], key: str
) -> float:
    return float(cast("int | float", d_packet[key])) - float(cast("int | float", b_packet[key]))


def _assert_public_packet_matches(
    observed: object, expected: Mapping[str, object], label: str
) -> None:
    if not isinstance(observed, dict) or set(observed) != set(expected):
        raise SupervisorError(f"F1-v2 {label} fields drifted")
    for key, expected_value in expected.items():
        observed_value = observed[key]
        if isinstance(expected_value, float):
            if not isinstance(observed_value, (int, float)) or isinstance(observed_value, bool):
                raise SupervisorError(f"F1-v2 {label} numeric field drifted: {key}")
            if not math.isclose(
                float(observed_value), expected_value, rel_tol=1e-12, abs_tol=1e-12
            ):
                raise SupervisorError(f"F1-v2 {label} numeric field drifted: {key}")
        elif observed_value != expected_value:
            raise SupervisorError(f"F1-v2 {label} field drifted: {key}")


def _validate_scientific_staging(staging: Path) -> None:
    metadata = staging.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SupervisorError("scientific staging is not a real directory")
    entries = {path.name: path for path in staging.iterdir()}
    if set(entries) != set(SCIENTIFIC_ARTIFACTS):
        raise SupervisorError("scientific staging does not contain the exact artifact set")
    for name, path in entries.items():
        item = path.lstat()
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISREG(item.st_mode):
            raise SupervisorError(f"scientific artifact is not a regular file: {name}")

    acquisition_fields: dict[str, FieldValidator] = {
        "acquisition_id": _string,
        "cell_id": _string,
        "condition_id": _nonnegative_integer,
        "timestamp_s": _finite_number,
        "timestamp_origin": _integer,
        "duration_s": _finite_number,
        "sd_block_id": _integer,
        "source_archive": _string,
        "source_archive_sha256": _sha256,
        "source_member": _string,
        "source_line": _nonnegative_integer,
        "aligned_snapshot_id": _nullable_string,
        "within_60h_candidate_only": _boolean,
        "same_aligned_checkup_as_previous": _boolean,
        "within_60h_candidate_reason": _string,
        "eis_values": _list_of(_nullable_finite_number, length=54),
    }
    for index, row in enumerate(_load_jsonl(entries["acquisition_manifest.jsonl"])):
        _expect_exact_fields(
            row,
            acquisition_fields,
            f"acquisition_manifest.jsonl:{index + 1}",
            scientific_flags=True,
        )

    snapshot_fields: dict[str, FieldValidator] = {
        "snapshot_id": _string,
        "cell_id": _string,
        "condition_id": _nonnegative_integer,
        "mode": _one_of_strings(frozenset({"cyclic", "profile"})),
        "checkup_index": _nonnegative_integer,
        "timestamp_s": _finite_number,
        "q0_ah": _finite_number,
        "capacity_ah": _finite_number,
        "soh_percent": _finite_number,
        "future_soh_percent": _finite_number,
        "o2_label": _binary_integer,
        "b_values": _list_of(_finite_number, length=12),
        "primary_eis_values": _list_of(_nullable_finite_number, length=54),
        "keep_last_eis_values": _list_of(_nullable_finite_number, length=54),
        "acquisition_ids": _list_of(_string),
        "finite_acquisition_count_by_feature": _list_of(_nonnegative_integer, length=54),
        "keep_last_acquisition_id": _string,
    }
    for index, row in enumerate(_load_jsonl(entries["snapshot_manifest.jsonl"])):
        _expect_exact_fields(
            row,
            snapshot_fields,
            f"snapshot_manifest.jsonl:{index + 1}",
            scientific_flags=True,
        )

    def platt(value: object, location: str) -> None:
        _expect_exact_fields(
            value,
            {
                "slope": _finite_number,
                "intercept": _finite_number,
                "iterations": _nonnegative_integer,
            },
            location,
        )

    prediction_fields: dict[str, FieldValidator] = {
        "snapshot_id": _string,
        "cell_id": _string,
        "condition_id": _nonnegative_integer,
        "mode": _one_of_strings(frozenset({"cyclic", "profile"})),
        "acquisition_policy": _one_of_strings(frozenset({"primary", "keep_last_sensitivity"})),
        "view": _one_of_strings(frozenset({"B", "D_CORE"})),
        "model_family": _one_of_strings(
            frozenset({"logistic_elastic_net", "hist_gradient_boosting"})
        ),
        "paired_design_sha256": _sha256,
        "outer_fold": _nonnegative_integer,
        "label": _binary_integer,
        "raw_probability": _probability,
        "calibrated_probability": _probability,
        "outer_validation_weight_mean_one": _finite_number,
        "platt": platt,
    }
    for index, row in enumerate(_load_jsonl(entries["oof_predictions.jsonl"])):
        _expect_exact_fields(
            row,
            prediction_fields,
            f"oof_predictions.jsonl:{index + 1}",
            scientific_flags=True,
        )

    _expect_exact_fields(
        _load_json_object(entries["fold_registry.json"]),
        {
            "split_policy": _exact_string("condition_grouped_outer3_inner2_v1"),
            "support": _two_modes(_mode_support),
            "paired_design_by_mode": _two_modes(_paired_design),
            "modes": _two_modes(_list_of(_fold_registry_row)),
        },
        "fold_registry.json",
        scientific_flags=True,
    )
    _expect_exact_fields(
        _load_json_object(entries["paired_deltas.json"]),
        {"paired_deltas": _list_of(_paired_delta_row)},
        "paired_deltas.json",
        scientific_flags=True,
    )
    _expect_exact_fields(
        _load_json_object(entries["retry_sensitivity.json"]),
        {"results": _list_of(_retry_row)},
        "retry_sensitivity.json",
        scientific_flags=True,
    )

    def source_archives(value: object, location: str) -> None:
        _expect_exact_fields(value, {"eoc": _string, "eis": _string}, location)

    def materialization(value: object, location: str) -> None:
        def policy(nested: object, nested_location: str) -> None:
            payload = _expect_exact_fields(
                nested,
                {
                    "primary": _exact_string("equal_finite_mean_per_frequency_v1"),
                    "sensitivity": _exact_string("keep_last_acquisition_v1"),
                    "retry_window_seconds": _finite_number,
                    "retry_window_role": _exact_string("flag_only_never_group_or_drop"),
                    "timestamp_semantics_provisional": _boolean,
                },
                nested_location,
            )
            if payload["timestamp_semantics_provisional"] is not True:
                raise SupervisorError(
                    f"{nested_location}.timestamp_semantics_provisional must be true"
                )

        _expect_exact_fields(
            value,
            {
                "counts": _string_integer_map,
                "eligible_acquisitions": _nonnegative_integer,
                "within_60h_candidate_count": _nonnegative_integer,
                "policy": policy,
            },
            location,
        )

    summary = _expect_exact_fields(
        _load_json_object(entries["engine_summary.json"]),
        {
            "run_kind": _exact_string("KIT_O2_DEVELOPMENT_ROBUSTNESS_V1"),
            "outcome": _exact_string("O2_RAPID_LOSS_5PP_H3"),
            "source_archives": source_archives,
            "source_counts": _string_integer_map,
            "materialization": materialization,
            "snapshot_count": _nonnegative_integer,
            "planned_fits": _nonnegative_integer,
            "completed_fits": _nonnegative_integer,
            "candidate_role": _exact_string("fixed_robustness_only_no_selection"),
            "metrics": _list_of(_metric_row),
            "comparison_policy": _exact_string(
                "report_each_fixed_family_without_ranking_or_promotion"
            ),
            "config_sha256": _sha256,
            "artifact_paths": _list_of(_string, length=len(SCIENTIFIC_ARTIFACTS)),
        },
        "engine_summary.json",
        scientific_flags=True,
    )
    if summary["artifact_paths"] != list(SCIENTIFIC_ARTIFACTS):
        raise SupervisorError(
            "engine_summary artifact_paths must exactly match the ordered allowlist"
        )


def validate_kit_f1_v2_artifact_tree(
    root: Path,
    config_path: Path,
    *,
    require_terminal_tree: bool = False,
    enforce_frozen_official_counts: bool = True,
) -> None:
    """Public parent-side validator for staged or fully promoted KIT F1-v2 artifacts."""
    directory = root.expanduser().absolute()
    current_component = Path(directory.anchor)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    for component in directory.parts[1:]:
        current_component /= component
        component_metadata = current_component.lstat()
        attributes = int(getattr(component_metadata, "st_file_attributes", 0))
        if stat.S_ISLNK(component_metadata.st_mode) or bool(attributes & reparse_flag):
            raise SupervisorError("F1-v2 artifact root path contains a link or reparse point")
    metadata = directory.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SupervisorError("F1-v2 artifact root is not a real directory")
    expected = set(SCIENTIFIC_ARTIFACTS)
    if require_terminal_tree:
        expected.update(PARENT_ALWAYS_ARTIFACTS)
        expected.update({"terminal.json", FINAL_MANIFEST})
    entries = {path.name: path for path in directory.iterdir()}
    if set(entries) != expected:
        raise SupervisorError("F1-v2 artifact tree does not contain the exact expected paths")
    for name, path in entries.items():
        item = path.lstat()
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISREG(item.st_mode):
            raise SupervisorError(f"F1-v2 artifact is not a regular file: {name}")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    schema_ref = Path(str(config.get("$schema", "")))
    if schema_ref.is_absolute() or schema_ref.name != schema_ref.as_posix():
        raise SupervisorError("F1-v2 schema reference must be one local basename")
    schema_path = config_path.with_name(schema_ref.name)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if _sha256_file(schema_path) != config.get("artifact_schema_sha256"):
        raise SupervisorError("F1-v2 schema hash binding failed")
    config_errors = list(Draft202012Validator(schema).iter_errors(config))
    if config_errors:
        raise SupervisorError("F1-v2 config fails its closed schema")
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise SupervisorError("F1-v2 schema definitions are missing")
    definition_by_name = {
        "acquisition_manifest.jsonl": "acquisitionManifestRow",
        "snapshot_manifest.jsonl": "snapshotManifestRow",
        "oof_predictions.jsonl": "oofPredictionRow",
        "fold_registry.json": "foldRegistryArtifact",
        "paired_deltas.json": "pairedDeltasArtifact",
        "retry_sensitivity.json": "retrySensitivityArtifact",
        "engine_summary.json": "engineSummaryArtifact",
    }
    decoded: dict[str, list[dict[str, object]]] = {}
    for name, definition in definition_by_name.items():
        values = (
            _load_jsonl(entries[name])
            if name.endswith(".jsonl")
            else [_load_json_object(entries[name])]
        )
        wrapper = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": definitions,
            "$ref": f"#/$defs/{definition}",
        }
        validator = Draft202012Validator(wrapper)
        for row_number, value in enumerate(values, start=1):
            errors = list(validator.iter_errors(value))
            if errors:
                raise SupervisorError(f"F1-v2 closed-schema failure: {name}:{row_number}")
            if not isinstance(value, dict):
                raise SupervisorError(f"F1-v2 artifact row is not an object: {name}:{row_number}")
        decoded[name] = values  # type: ignore[assignment]

    acquisitions = decoded["acquisition_manifest.jsonl"]
    snapshots = decoded["snapshot_manifest.jsonl"]
    predictions = decoded["oof_predictions.jsonl"]
    folds = decoded["fold_registry.json"][0]
    paired = decoded["paired_deltas.json"][0]
    sensitivity = decoded["retry_sensitivity.json"][0]
    summary = decoded["engine_summary.json"][0]
    if any(
        row.get(key) is not False
        for rows in decoded.values()
        for row in rows
        for key in FALSE_CLAIM_FLAGS
    ):
        raise SupervisorError("F1-v2 artifact contains a non-false claim flag")
    if summary.get("config_sha256") != _sha256_file(config_path):
        raise SupervisorError("F1-v2 summary config hash binding failed")
    if summary.get("artifact_schema_sha256") != _sha256_file(schema_path):
        raise SupervisorError("F1-v2 summary schema hash binding failed")
    source_archives = summary.get("source_archives")
    if not isinstance(source_archives, dict) or any(
        not isinstance(source_archives.get(kind), dict)
        or source_archives[kind].get("sha256") != config["source"][f"{kind}_sha256"]
        or not source_archives[kind].get("path_name")
        for kind in ("eoc", "eis")
    ):
        raise SupervisorError("F1-v2 summary source-archive binding failed")
    expected_source_counts = {
        "real_cells": int(config["source"]["expected_real_cells"]),
        "capacity_endpoints": int(config["source"]["expected_capacity_endpoints"]),
        "valid_rt50_spectra": int(config["source"]["expected_valid_rt50_spectra"]),
    }
    source_counts = summary.get("source_counts")
    if not isinstance(source_counts, dict) or any(
        source_counts.get(key) != value for key, value in expected_source_counts.items()
    ):
        raise SupervisorError("F1-v2 summary source-count binding failed")

    snapshot_by_id = {str(row["snapshot_id"]): row for row in snapshots}
    if len(snapshot_by_id) != len(snapshots):
        raise SupervisorError("F1-v2 snapshot IDs are not unique")
    availability: dict[str, list[float]] = {}
    for row in acquisitions:
        aligned = row.get("aligned_snapshot_id")
        if aligned is None:
            continue
        snapshot = snapshot_by_id.get(str(aligned))
        if snapshot is None:
            raise SupervisorError("F1-v2 acquisition references unknown snapshot")
        available_at = float(cast("int | float", row["timestamp_s"])) + float(
            cast("int | float", row["duration_s"])
        )
        if available_at <= float(cast("int | float", snapshot["timestamp_s"])):
            raise SupervisorError("F1-v2 EIS is not available after current endpoint")
        availability.setdefault(str(aligned), []).append(available_at)
        next_row = snapshot_by_id.get(
            f"{snapshot['cell_id']}:checkup-{int(cast('int', snapshot['checkup_index'])) + 1}"
        )
        if next_row is not None and available_at >= float(
            cast("int | float", next_row["timestamp_s"])
        ):
            raise SupervisorError("F1-v2 EIS crosses next capacity boundary")
        h3_id = snapshot.get("h3_endpoint_snapshot_id")
        if h3_id is not None and available_at >= float(
            cast("int | float", snapshot_by_id[str(h3_id)]["timestamp_s"])
        ):
            raise SupervisorError("F1-v2 EIS crosses H3 target boundary")
    for snapshot_id, snapshot in snapshot_by_id.items():
        times = availability.get(snapshot_id, [])
        observed = snapshot.get("prediction_available_at_s")
        if (not times and observed is not None) or (
            times and (observed is None or float(cast("int | float", observed)) != max(times))
        ):
            raise SupervisorError("F1-v2 prediction availability is not completion-derived")

    if enforce_frozen_official_counts:
        expected_paired = int(config["readiness"]["expected_paired_snapshots"])
        eligible = {
            str(row["snapshot_id"])
            for row in snapshots
            if row["eligibility_status"] == "eligible_f1_and_d_core"
        }
        paired_identity_rows = [
            {
                "snapshot_id": row["snapshot_id"],
                "cell_id": row["cell_id"],
                "condition_id": row["condition_id"],
                "mode": row["mode"],
                "checkup_index": row["checkup_index"],
                "timestamp_s_hex": float(cast("int | float", row["timestamp_s"])).hex(),
                "h3_endpoint_snapshot_id": row["h3_endpoint_snapshot_id"],
                "h3_checkup_index": row["h3_checkup_index"],
                "soh_percent_hex": float(cast("int | float", row["soh_percent"])).hex(),
                "future_soh_percent_hex": float(
                    cast("int | float", row["future_soh_percent"])
                ).hex(),
            }
            for row in sorted(
                (
                    item
                    for item in snapshots
                    if item["eligibility_status"] == "eligible_f1_and_d_core"
                ),
                key=lambda item: (str(item["mode"]), str(item["snapshot_id"])),
            )
        ]
        paired_identity_hash = hashlib.sha256(
            _canonical_json(paired_identity_rows).encode("utf-8")
        ).hexdigest()
        if (
            len(snapshots) != 3999
            or len(acquisitions) != 3993
            or len(eligible) != expected_paired
            or len(predictions) != 23352
            or summary.get("planned_fits") != 54
            or summary.get("completed_fits") != 54
            or len(cast("list[object]", summary.get("metrics", []))) != 24
            or len(cast("list[object]", paired.get("paired_deltas", []))) != 12
            or len(cast("list[object]", sensitivity.get("results", []))) != 6
            or paired_identity_hash != config["readiness"]["paired_cohort_identity_sha256"]
        ):
            raise SupervisorError("F1-v2 frozen count reconciliation failed")
        observed_modes = {
            mode: sum(
                row["eligibility_status"] == "eligible_f1_and_d_core" and row["mode"] == mode
                for row in snapshots
            )
            for mode in ("calendar", "cyclic", "profile")
        }
        if observed_modes != {"calendar": 1134, "cyclic": 1190, "profile": 595}:
            raise SupervisorError("F1-v2 frozen mode counts drifted")
        if any(len(cast("list[object]", row["eis_values"])) != 54 for row in acquisitions):
            raise SupervisorError("F1-v2 acquisition feature width is not 54")
        acquisition_by_id = {str(row["acquisition_id"]): row for row in acquisitions}
        if len(acquisition_by_id) != len(acquisitions):
            raise SupervisorError("F1-v2 acquisition IDs are not unique")
        for row in snapshots:
            if row["eligibility_status"] != "eligible_f1_and_d_core":
                continue
            acquisition_ids = cast("list[str]", row["acquisition_ids"])
            acquisition_rows = [acquisition_by_id[value] for value in acquisition_ids]
            if not acquisition_rows or row["keep_last_acquisition_id"] not in acquisition_ids:
                raise SupervisorError("F1-v2 eligible snapshot acquisition lineage is incomplete")
            keep_last = acquisition_by_id[str(row["keep_last_acquisition_id"])]
            if row["keep_last_eis_values"] != keep_last["eis_values"]:
                raise SupervisorError("F1-v2 keep-last vector is not one whole acquisition")
            for column in range(54):
                finite: list[float] = []
                for acquisition_row in acquisition_rows:
                    feature_value = cast("list[float | None]", acquisition_row["eis_values"])[
                        column
                    ]
                    if feature_value is not None:
                        finite.append(float(feature_value))
                observed_count = cast("list[int]", row["finite_acquisition_count_by_feature"])[
                    column
                ]
                observed_value = cast("list[float | None]", row["primary_eis_values"])[column]
                if observed_count != len(finite):
                    raise SupervisorError("F1-v2 finite-acquisition count drifted")
                expected_value = math.fsum(finite) / len(finite) if finite else None
                if observed_value is None or expected_value is None:
                    if observed_value != expected_value:
                        raise SupervisorError("F1-v2 primary EIS mean drifted")
                elif not math.isclose(
                    float(observed_value), expected_value, rel_tol=1e-15, abs_tol=1e-12
                ):
                    raise SupervisorError("F1-v2 primary EIS mean drifted")
            if cast("int", row["h3_checkup_index"]) != cast("int", row["checkup_index"]) + 3:
                raise SupervisorError("F1-v2 H3 checkup arithmetic drifted")
            expected_h3_id = f"{row['cell_id']}:checkup-{cast('int', row['checkup_index']) + 3}"
            h3_id = row.get("h3_endpoint_snapshot_id")
            h3_row = snapshot_by_id.get(str(h3_id))
            if (
                h3_id != expected_h3_id
                or h3_row is None
                or h3_row["cell_id"] != row["cell_id"]
                or not math.isclose(
                    float(cast("int | float", row["future_soh_percent"])),
                    float(cast("int | float", h3_row["soh_percent"])),
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                raise SupervisorError("F1-v2 H3 target lineage drifted")
            current = float(cast("int | float", row["soh_percent"]))
            future = float(cast("int | float", row["future_soh_percent"]))
            loss = float(cast("int | float", row["soh_loss_pp_h3"]))
            if not math.isclose(current - future, loss, rel_tol=0.0, abs_tol=1e-9):
                raise SupervisorError("F1-v2 target arithmetic drifted")
        preprocessing = cast(
            "dict[str, list[dict[str, object]]]", folds.get("preprocessing_by_mode", {})
        )
        audit_rows = [row for rows in preprocessing.values() for row in rows]
        if len(audit_rows) != 54:
            raise SupervisorError("F1-v2 fold-local preprocessing evidence count is not 54")
        for row in audit_rows:
            expected_width = 54 if row["view"] == "D_CORE" else 0
            if len(cast("list[str]", row["retained_eis_feature_names"])) != expected_width:
                raise SupervisorError("F1-v2 retained EIS-channel evidence drifted")

        fold_modes = cast("dict[str, list[dict[str, object]]]", folds["modes"])
        for mode, fold_rows in fold_modes.items():
            mode_ids = {
                str(row["snapshot_id"])
                for row in snapshots
                if row["mode"] == mode and row["eligibility_status"] == "eligible_f1_and_d_core"
            }
            for fold in fold_rows:
                train_ids = set(cast("list[str]", fold["train_snapshot_ids"]))
                validation_ids = set(cast("list[str]", fold["validation_snapshot_ids"]))
                if train_ids & validation_ids or train_ids | validation_ids != mode_ids:
                    raise SupervisorError("F1-v2 outer fold does not partition the mode cohort")
                train_conditions = {snapshot_by_id[value]["condition_id"] for value in train_ids}
                validation_conditions = {
                    snapshot_by_id[value]["condition_id"] for value in validation_ids
                }
                train_cells = {snapshot_by_id[value]["cell_id"] for value in train_ids}
                validation_cells = {snapshot_by_id[value]["cell_id"] for value in validation_ids}
                if train_conditions & validation_conditions or train_cells & validation_cells:
                    raise SupervisorError("F1-v2 outer fold leaks condition or cell")
                for inner in cast("list[dict[str, object]]", fold["inner_folds"]):
                    inner_train = set(cast("list[str]", inner["train_snapshot_ids"]))
                    inner_validation = set(cast("list[str]", inner["validation_snapshot_ids"]))
                    if (
                        inner_train & inner_validation
                        or inner_train | inner_validation != train_ids
                        or (inner_train | inner_validation) & validation_ids
                    ):
                        raise SupervisorError("F1-v2 inner fold escapes outer training data")
                    inner_train_conditions = {
                        snapshot_by_id[value]["condition_id"] for value in inner_train
                    }
                    inner_validation_conditions = {
                        snapshot_by_id[value]["condition_id"] for value in inner_validation
                    }
                    inner_train_cells = {snapshot_by_id[value]["cell_id"] for value in inner_train}
                    inner_validation_cells = {
                        snapshot_by_id[value]["cell_id"] for value in inner_validation
                    }
                    if (
                        inner_train_conditions & inner_validation_conditions
                        or inner_train_cells & inner_validation_cells
                    ):
                        raise SupervisorError("F1-v2 inner fold leaks condition or cell")
        paired_designs = cast("dict[str, dict[str, object]]", folds["paired_design_by_mode"])
        observed_design_hashes: dict[str, str] = {}
        for mode, design in paired_designs.items():
            claimed = design["paired_design_sha256"]
            unhashed = {
                key: value for key, value in design.items() if key != "paired_design_sha256"
            }
            computed = hashlib.sha256(_canonical_json(unhashed).encode()).hexdigest()
            if claimed != computed:
                raise SupervisorError("F1-v2 paired-design hash does not recompute")
            observed_design_hashes[mode] = str(claimed)
        if observed_design_hashes != config["readiness"]["paired_design_sha256_by_mode"]:
            raise SupervisorError("F1-v2 paired-design hashes differ from frozen readiness")

        def expected_partition_weights(snapshot_ids: list[str]) -> dict[str, float]:
            conditions = {snapshot_by_id[value]["condition_id"] for value in snapshot_ids}
            cells_by_condition: dict[object, set[object]] = {}
            checkups_by_cell: Counter[tuple[object, object]] = Counter()
            for value in snapshot_ids:
                item = snapshot_by_id[value]
                condition = item["condition_id"]
                cell = item["cell_id"]
                cells_by_condition.setdefault(condition, set()).add(cell)
                checkups_by_cell[(condition, cell)] += 1
            raw = {
                value: 1.0
                / (
                    len(conditions)
                    * len(cells_by_condition[snapshot_by_id[value]["condition_id"]])
                    * checkups_by_cell[
                        (
                            snapshot_by_id[value]["condition_id"],
                            snapshot_by_id[value]["cell_id"],
                        )
                    ]
                )
                for value in snapshot_ids
            }
            mean = math.fsum(raw.values()) / len(raw)
            return {value: weight / mean for value, weight in raw.items()}

        def validate_weight_rows(
            rows: list[dict[str, object]], snapshot_ids: list[str], label: str
        ) -> dict[str, float]:
            observed = {
                str(row["snapshot_id"]): float(cast("int | float", row["weight_mean_one"]))
                for row in rows
            }
            expected_weights = expected_partition_weights(snapshot_ids)
            if (
                len(observed) != len(rows)
                or set(observed) != set(expected_weights)
                or any(
                    not math.isclose(
                        observed[value], expected_weights[value], rel_tol=1e-14, abs_tol=1e-14
                    )
                    for value in expected_weights
                )
            ):
                raise SupervisorError(f"F1-v2 {label} hierarchical weights drifted")
            return expected_weights

        validation_weights: dict[tuple[str, int], dict[str, float]] = {}
        for mode, fold_rows in fold_modes.items():
            design = paired_designs[mode]
            design_folds = {
                int(cast("int", row["outer_fold"])): row
                for row in cast("list[dict[str, object]]", design["folds"])
            }
            mode_snapshot_ids = [
                str(row["snapshot_id"])
                for row in snapshots
                if row["mode"] == mode and row["eligibility_status"] == "eligible_f1_and_d_core"
            ]
            validate_weight_rows(
                cast("list[dict[str, object]]", design["metric_weights"]),
                mode_snapshot_ids,
                f"{mode} metric",
            )
            for fold in fold_rows:
                outer_fold = int(cast("int", fold["outer_fold"]))
                design_fold = design_folds[outer_fold]
                outer_train_ids = list(cast("list[str]", fold["train_snapshot_ids"]))
                outer_validation_ids = list(cast("list[str]", fold["validation_snapshot_ids"]))
                validate_weight_rows(
                    cast("list[dict[str, object]]", design_fold["training_weights"]),
                    outer_train_ids,
                    f"{mode} outer training",
                )
                validation_weights[(mode, outer_fold)] = validate_weight_rows(
                    cast("list[dict[str, object]]", design_fold["validation_weights"]),
                    outer_validation_ids,
                    f"{mode} outer validation",
                )
                for inner, design_inner in zip(
                    cast("list[dict[str, object]]", fold["inner_folds"]),
                    cast("list[dict[str, object]]", design_fold["inner_folds"]),
                    strict=True,
                ):
                    validate_weight_rows(
                        cast("list[dict[str, object]]", design_inner["training_weights"]),
                        list(cast("list[str]", inner["train_snapshot_ids"])),
                        f"{mode} inner training",
                    )
                    validate_weight_rows(
                        cast("list[dict[str, object]]", design_inner["validation_weights"]),
                        list(cast("list[str]", inner["validation_snapshot_ids"])),
                        f"{mode} inner validation",
                    )

        prediction_identity_fields = (
            "task_id",
            "mode",
            "outer_fold",
            "acquisition_policy",
            "view",
            "model_role",
            "model_family",
            "snapshot_id",
        )
        prediction_identities = {
            tuple(row[field] for field in prediction_identity_fields) for row in predictions
        }
        if len(prediction_identities) != len(predictions):
            raise SupervisorError("F1-v2 prediction identities are not unique")
        expected_prediction_families = {
            ("baseline", "persistence_zero_loss", "B", "primary"): expected_paired,
            ("baseline", "recent_slope_h3", "B", "primary"): expected_paired,
            ("fitted_candidate", "elastic_net_regression", "B", "primary"): expected_paired,
            (
                "fitted_candidate",
                "hist_gradient_boosting_regressor",
                "B",
                "primary",
            ): expected_paired,
            (
                "fitted_candidate",
                "elastic_net_regression",
                "D_CORE",
                "primary",
            ): expected_paired,
            (
                "fitted_candidate",
                "hist_gradient_boosting_regressor",
                "D_CORE",
                "primary",
            ): expected_paired,
            (
                "fitted_candidate",
                "elastic_net_regression",
                "D_CORE",
                "keep_last_sensitivity",
            ): expected_paired,
            (
                "fitted_candidate",
                "hist_gradient_boosting_regressor",
                "D_CORE",
                "keep_last_sensitivity",
            ): expected_paired,
        }
        observed_prediction_families = Counter(
            (
                str(row["model_role"]),
                str(row["model_family"]),
                str(row["view"]),
                str(row["acquisition_policy"]),
            )
            for row in predictions
        )
        if observed_prediction_families != expected_prediction_families:
            raise SupervisorError("F1-v2 per-model prediction counts drifted")

        fixed = [row for row in predictions if row["model_role"] == "fitted_candidate"]
        baseline_rows = [row for row in predictions if row["model_role"] == "baseline"]
        if any(row.get("fit_id") is not None for row in baseline_rows):
            raise SupervisorError("F1-v2 baseline prediction unexpectedly carries a fit ID")
        for row in baseline_rows:
            family = row["model_family"]
            snapshot = snapshot_by_id[str(row["snapshot_id"])]
            if family == "persistence_zero_loss":
                expected_baseline = 0.0
            elif family == "recent_slope_h3":
                b_values = cast("list[int | float]", snapshot["b_values"])
                if len(b_values) <= 3:
                    raise SupervisorError("F1-v2 recent-slope baseline lacks B feature index 3")
                expected_baseline = 3.0 * float(b_values[3])
            else:  # pragma: no cover - family count contract rejects this first
                raise SupervisorError("F1-v2 baseline family is unknown")
            if not math.isclose(
                float(cast("int | float", row["predicted_soh_loss_pp_h3"])),
                expected_baseline,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise SupervisorError("F1-v2 baseline prediction semantic drifted")
        fit_rows: dict[str, list[dict[str, object]]] = {}
        observed_trial_by_fit: dict[str, tuple[object, object, int, object, object, object]] = {}
        for row in fixed:
            fit_id = str(row.get("fit_id"))
            expected_fit_id = (
                f"{row['task_id']}:{row['mode']}:{row['acquisition_policy']}:"
                f"{row['view']}:{row['model_family']}:outer-{row['outer_fold']}"
            )
            if fit_id != expected_fit_id:
                raise SupervisorError("F1-v2 fitted prediction fit ID drifted")
            trial = (
                row["task_id"],
                row["mode"],
                int(cast("int", row["outer_fold"])),
                row["acquisition_policy"],
                row["view"],
                row["model_family"],
            )
            prior_trial = observed_trial_by_fit.setdefault(fit_id, trial)
            if prior_trial != trial:
                raise SupervisorError("F1-v2 fit ID aliases more than one trial")
            fit_rows.setdefault(fit_id, []).append(row)

        workflows = (
            ("primary", "B"),
            ("primary", "D_CORE"),
            ("keep_last_sensitivity", "D_CORE"),
        )
        model_families = (
            "elastic_net_regression",
            "hist_gradient_boosting_regressor",
        )
        planned_trials: list[tuple[object, object, int, object, object, object]] = [
            (
                "F1_SOH_LOSS_PP_H3",
                mode,
                outer_fold,
                acquisition_policy,
                view,
                model_family,
            )
            for mode in ("calendar", "cyclic", "profile")
            for outer_fold in range(3)
            for acquisition_policy, view in workflows
            for model_family in model_families
        ]
        observed_trials = set(observed_trial_by_fit.values())
        if len(fit_rows) != 54 or observed_trials != set(planned_trials):
            raise SupervisorError("F1-v2 actual fitted trial identities drifted from frozen plan")
        validation_ids_by_fold = {
            (mode, int(cast("int", fold["outer_fold"]))): set(
                cast("list[str]", fold["validation_snapshot_ids"])
            )
            for mode, rows in fold_modes.items()
            for fold in rows
        }
        for fit_id, rows in fit_rows.items():
            trial = observed_trial_by_fit[fit_id]
            expected_ids = validation_ids_by_fold[(str(trial[1]), int(cast("int", trial[2])))]
            observed_ids = {str(row["snapshot_id"]) for row in rows}
            if len(observed_ids) != len(rows) or observed_ids != expected_ids:
                raise SupervisorError("F1-v2 fit OOF coverage differs from its outer fold")
        actual_trial_rows = [
            {
                "task_id": trial[0],
                "mode": trial[1],
                "outer_fold": trial[2],
                "acquisition_policy": trial[3],
                "view": trial[4],
                "model_family": trial[5],
            }
            for trial in planned_trials
            if trial in observed_trials
        ]
        actual_trial_digest = hashlib.sha256(
            _canonical_json(actual_trial_rows).encode("utf-8")
        ).hexdigest()
        if (
            len(actual_trial_rows) != 54
            or actual_trial_digest != config["readiness"]["planned_trial_keys_sha256"]
            or summary.get("planned_fits") != len(actual_trial_rows)
            or summary.get("completed_fits") != len(fit_rows)
        ):
            raise SupervisorError("F1-v2 actual fitted trial digest does not reconcile")

        metric_rows = cast("list[dict[str, object]]", summary["metrics"])
        metric_identity_fields = (
            "mode",
            "acquisition_policy",
            "view",
            "model_role",
            "model_family",
            "paired_design_sha256",
        )
        metric_by_identity = {
            tuple(row[field] for field in metric_identity_fields): row for row in metric_rows
        }
        if len(metric_by_identity) != len(metric_rows) or len(metric_rows) != 24:
            raise SupervisorError("F1-v2 metric identities are missing or duplicated")
        eligible_by_mode = {
            mode: [
                row
                for row in snapshots
                if row["mode"] == mode and row["eligibility_status"] == "eligible_f1_and_d_core"
            ]
            for mode in ("calendar", "cyclic", "profile")
        }
        for identity, metric_row in metric_by_identity.items():
            metric_mode, policy, view, role, family, design_hash = identity
            if design_hash != config["readiness"]["paired_design_sha256_by_mode"][str(metric_mode)]:
                raise SupervisorError("F1-v2 metric design identity drifted")
            group = [
                row
                for row in predictions
                if all(
                    row[field] == value
                    for field, value in zip(
                        metric_identity_fields,
                        (metric_mode, policy, view, role, family, design_hash),
                        strict=True,
                    )
                )
            ]
            packet = _public_regression_metric_packet(eligible_by_mode[str(metric_mode)], group)
            _assert_public_packet_matches(metric_row.get("metrics"), packet, "metric packet")

        delta_rows = cast("list[dict[str, object]]", paired["paired_deltas"])
        delta_by_identity = {
            (row["mode"], row["model_family"], row["acquisition_policy"]): row for row in delta_rows
        }
        expected_delta_identities = {
            (mode, family, policy)
            for mode in ("calendar", "cyclic", "profile")
            for family in model_families
            for policy in ("primary", "keep_last_sensitivity")
        }
        if (
            len(delta_by_identity) != len(delta_rows)
            or set(delta_by_identity) != expected_delta_identities
        ):
            raise SupervisorError("F1-v2 paired-delta identities are missing or duplicated")
        recomputed_deltas: dict[tuple[object, object, object], dict[str, object]] = {}
        for identity, delta_row in delta_by_identity.items():
            delta_mode, delta_family, delta_policy = identity
            b_rows = [
                row
                for row in predictions
                if row["mode"] == delta_mode
                and row["model_role"] == "fitted_candidate"
                and row["model_family"] == delta_family
                and row["view"] == "B"
                and row["acquisition_policy"] == "primary"
            ]
            d_rows = [
                row
                for row in predictions
                if row["mode"] == delta_mode
                and row["model_role"] == "fitted_candidate"
                and row["model_family"] == delta_family
                and row["view"] == "D_CORE"
                and row["acquisition_policy"] == delta_policy
            ]
            cohort = eligible_by_mode[str(delta_mode)]
            b_packet = _public_regression_metric_packet(cohort, b_rows)
            d_packet = _public_regression_metric_packet(cohort, d_rows)

            b_r2 = b_packet["condition_balanced_r2_v1"]
            d_r2 = d_packet["condition_balanced_r2_v1"]
            expected_delta: dict[str, object] = {
                "same_snapshot_ids_and_weights": True,
                "mae_delta_d_core_minus_b_pp": _public_metric_difference(
                    d_packet, b_packet, "condition_balanced_mae_pp_v1"
                ),
                "median_ae_delta_d_core_minus_b_pp": _public_metric_difference(
                    d_packet, b_packet, "condition_balanced_median_ae_pp_v1"
                ),
                "rmse_delta_d_core_minus_b_pp": _public_metric_difference(
                    d_packet, b_packet, "condition_balanced_rmse_pp_v1"
                ),
                "signed_bias_delta_d_core_minus_b_pp": _public_metric_difference(
                    d_packet, b_packet, "condition_balanced_signed_bias_pp_v1"
                ),
                "r2_delta_d_core_minus_b": (
                    None
                    if b_r2 is None or d_r2 is None
                    else float(cast("int | float", d_r2)) - float(cast("int | float", b_r2))
                ),
                "bootstrap": _public_paired_regression_bootstrap(cohort, b_rows, d_rows),
            }
            observed_delta = {key: delta_row.get(key) for key in expected_delta}
            _assert_public_structure(observed_delta, expected_delta, "paired delta")
            recomputed_deltas[identity] = expected_delta

        retry_rows = cast("list[dict[str, object]]", sensitivity["results"])
        retry_by_identity = {(row["mode"], row["model_family"]): row for row in retry_rows}
        expected_retry_identities = {
            (mode, family)
            for mode in ("calendar", "cyclic", "profile")
            for family in model_families
        }
        if (
            len(retry_by_identity) != len(retry_rows)
            or set(retry_by_identity) != expected_retry_identities
        ):
            raise SupervisorError("F1-v2 retry-sensitivity identities are missing or duplicated")
        for identity, retry_row in retry_by_identity.items():
            retry_mode, retry_family = identity
            primary_delta = float(
                cast(
                    "int | float",
                    recomputed_deltas[(retry_mode, retry_family, "primary")][
                        "mae_delta_d_core_minus_b_pp"
                    ],
                )
            )
            keep_last_delta = float(
                cast(
                    "int | float",
                    recomputed_deltas[(retry_mode, retry_family, "keep_last_sensitivity")][
                        "mae_delta_d_core_minus_b_pp"
                    ],
                )
            )
            sign_reversal = primary_delta * keep_last_delta < 0.0
            expected_retry = {
                "primary_policy": "equal_finite_mean_per_frequency_v1",
                "sensitivity_policy": "keep_last_acquisition_v1",
                "mae_delta_sign_reversal": sign_reversal,
                "mae_delta_absolute_change_pp": abs(keep_last_delta - primary_delta),
                "prohibit_eis_increment_conclusion": sign_reversal,
                "decision_role": "diagnostic_stop_only_never_promotion",
            }
            observed_retry = {key: retry_row.get(key) for key in expected_retry}
            _assert_public_structure(observed_retry, expected_retry, "retry sensitivity")

        lookup = {
            (
                row["mode"],
                row["acquisition_policy"],
                row["view"],
                row["model_family"],
                row["snapshot_id"],
            ): row
            for row in fixed
        }
        if len(lookup) != len(fixed):
            raise SupervisorError("F1-v2 fitted prediction identities are not unique")
        for row in fixed:
            if row["view"] != "D_CORE":
                continue
            b_row = lookup.get(
                (row["mode"], "primary", "B", row["model_family"], row["snapshot_id"])
            )
            if b_row is None or any(
                row[field] != b_row[field]
                for field in (
                    "paired_design_sha256",
                    "outer_fold",
                    "condition_id",
                    "cell_id",
                    "outer_validation_weight_mean_one",
                )
            ):
                raise SupervisorError("F1-v2 D_CORE/B-primary identity pairing failed")
        for row in predictions:
            expected_oof_weight = validation_weights[
                (str(row["mode"]), int(cast("int", row["outer_fold"])))
            ][str(row["snapshot_id"])]
            if not math.isclose(
                float(cast("int | float", row["outer_validation_weight_mean_one"])),
                expected_oof_weight,
                rel_tol=1e-14,
                abs_tol=1e-14,
            ):
                raise SupervisorError("F1-v2 OOF hierarchical weight drifted")
            observed_loss = float(cast("int | float", row["observed_soh_loss_pp_h3"]))
            current = float(cast("int | float", row["current_soh_percent"]))
            observed_future = float(cast("int | float", row["observed_future_soh_percent"]))
            predicted_loss = float(cast("int | float", row["predicted_soh_loss_pp_h3"]))
            predicted_future = float(cast("int | float", row["predicted_future_soh_percent"]))
            if not math.isclose(
                current - observed_future, observed_loss, rel_tol=0.0, abs_tol=1e-9
            ):
                raise SupervisorError("F1-v2 prediction truth arithmetic drifted")
            if not math.isclose(
                current - predicted_loss, predicted_future, rel_tol=0.0, abs_tol=1e-9
            ):
                raise SupervisorError("F1-v2 predicted-future arithmetic drifted")
        if any(row.get("task_id") != "F1_SOH_LOSS_PP_H3" for row in predictions):
            raise SupervisorError("F1-v2 OOF artifact contains O2 output")
        target_registry = cast("list[dict[str, object]]", summary["target_registry"])
        f1_registry, o2_registry = target_registry
        if (
            o2_registry["planned_fits"]
            or o2_registry["completed_fits"]
            or o2_registry["prediction_rows"]
        ):
            raise SupervisorError("F1-v2 O2 counts are nonzero")
        if f1_registry["prediction_rows"] != len(predictions):
            raise SupervisorError("F1-v2 prediction count does not reconcile")

    if require_terminal_tree:
        terminal = _load_json_object(entries["terminal.json"])
        run_status = _load_json_object(entries["run_status.json"])
        final_manifest = _load_json_object(entries[FINAL_MANIFEST])
        parent_json: dict[str, dict[str, object]] = {
            "start_manifest.json": cast(
                "dict[str, object]", _load_json_object(entries["start_manifest.json"])
            ),
            "run_status.json": cast("dict[str, object]", run_status),
            "terminal.json": cast("dict[str, object]", terminal),
            FINAL_MANIFEST: cast("dict[str, object]", final_manifest),
        }
        if any(
            payload.get(key) is not False
            for payload in parent_json.values()
            for key in FALSE_CLAIM_FLAGS
        ):
            raise SupervisorError("F1-v2 parent evidence contains a non-false claim flag")

        start_manifest = parent_json["start_manifest.json"]
        config_payload = cast("dict[str, object]", _load_json_object(config_path))
        readiness = cast("dict[str, object]", config_payload["readiness"])
        source = cast("dict[str, object]", config_payload["source"])
        engine = cast("dict[str, object]", start_manifest.get("engine"))
        authorization = cast("dict[str, object]", start_manifest.get("authorization"))
        bindings = cast("dict[str, object]", start_manifest.get("bindings"))
        inputs = cast("dict[str, object]", start_manifest.get("inputs"))
        engine_sha = engine.get("module_sha256") if isinstance(engine, dict) else None
        expected_argv_shape = [
            sys.executable,
            "-m",
            "battery_eis_soh.kit_dev_worker",
            "--worker",
            "--eoc-zip",
            "<EOC_PATH>",
            "--eis-zip",
            "<EIS_PATH>",
            "--output-dir",
            "<SCIENTIFIC_STAGING_PATH>",
            "--config-path",
            "<CONFIG_PATH>",
            "--authorized-output-dir",
            "<AUTHORIZED_OUTPUT_PATH>",
            "--authorization-path",
            "<AUTHORIZATION_PATH>",
            "--authorization-receipt",
            "<AUTHORIZATION_RECEIPT_PATH>",
            "--runner-path",
            "<RUNNER_PATH>",
            "--expected-engine-sha256",
            engine_sha,
        ]
        sha_fields = (
            "authorization_sha256",
            "nonce",
            "receipt_sha256",
        )
        sha_pattern = re.compile(r"^[0-9a-f]{64}$")
        if (
            start_manifest.get("run_kind") != PAIRED_RUN_KIND
            or not isinstance(authorization, dict)
            or not isinstance(authorization.get("authorization_id"), str)
            or not authorization["authorization_id"]
            or any(
                not isinstance(authorization.get(field), str)
                or sha_pattern.fullmatch(cast("str", authorization[field])) is None
                for field in sha_fields
            )
            or not isinstance(bindings, dict)
            or set(bindings) != PAIRED_BINDING_KEYS
            or any(
                not isinstance(value, str) or sha_pattern.fullmatch(value) is None
                for value in bindings.values()
            )
            or start_manifest.get("worker_argv_shape") != expected_argv_shape
            or not isinstance(engine_sha, str)
            or sha_pattern.fullmatch(engine_sha) is None
        ):
            raise SupervisorError("F1-v2 paired start authorization contract drifted")
        schema_path = config_path.with_name(f"{config_path.stem}.schema.json")
        start_paired_designs = cast("dict[str, object]", readiness["paired_design_sha256_by_mode"])
        exact_bindings = {
            "eoc_sha256": source["eoc_sha256"],
            "eis_sha256": source["eis_sha256"],
            "config_sha256": _sha256_file(config_path),
            "schema_sha256": _sha256_file(schema_path),
            "readiness_sha256": readiness["eis_readiness_artifact_sha256"],
            "engine_sha256": engine_sha,
            "paired_cohort_sha256": readiness["paired_cohort_identity_sha256"],
            "paired_designs_sha256": sha256_bytes(_canonical_json(start_paired_designs).encode()),
            "trial_keys_sha256": readiness["planned_trial_keys_sha256"],
        }
        if any(bindings.get(key) != value for key, value in exact_bindings.items()):
            raise SupervisorError("F1-v2 paired start bindings do not match frozen inputs")
        expected_input_hashes = {
            "eoc": source["eoc_sha256"],
            "eis": source["eis_sha256"],
            "config": _sha256_file(config_path),
        }
        if (
            not isinstance(inputs, dict)
            or set(inputs) != set(expected_input_hashes)
            or any(
                not isinstance(inputs.get(name), dict)
                or cast("dict[str, object]", inputs[name]).get("sha256") != expected_hash
                or not isinstance(cast("dict[str, object]", inputs[name]).get("basename"), str)
                or not cast("str", cast("dict[str, object]", inputs[name])["basename"])
                for name, expected_hash in expected_input_hashes.items()
            )
            or cast("dict[str, object]", inputs["config"]).get("basename") != config_path.name
        ):
            raise SupervisorError("F1-v2 paired start input identities drifted")

        def validate_public_chain(name: str) -> list[dict[str, object]]:
            rows = cast("list[dict[str, object]]", _load_jsonl(entries[name]))
            previous = "0" * 64
            for sequence, row in enumerate(rows):
                event_hash = row.get("event_hash")
                event = {key: value for key, value in row.items() if key != "event_hash"}
                if event.get("sequence") != sequence or event.get("previous_hash") != previous:
                    raise SupervisorError(f"F1-v2 parent hash chain is broken: {name}")
                expected_hash = hashlib.sha256(_canonical_json(event).encode()).hexdigest()
                if event_hash != expected_hash:
                    raise SupervisorError(f"F1-v2 parent event hash is invalid: {name}")
                previous = expected_hash
            return rows

        lifecycle_rows = validate_public_chain("lifecycle.jsonl")
        validate_public_chain("telemetry.jsonl")
        if (
            len(lifecycle_rows) < 2
            or lifecycle_rows[-2].get("event") != "worker_exited"
            or lifecycle_rows[-2].get("worker_exit_code") != 0
            or lifecycle_rows[-1].get("event") != "finalizing"
            or lifecycle_rows[-1].get("mapped_exit_code") != 0
        ):
            raise SupervisorError("F1-v2 lifecycle lacks a successful terminal sequence")
        if (
            not isinstance(terminal, dict)
            or terminal.get("status") != "completed"
            or terminal.get("exit_code") != 0
            or terminal != run_status
        ):
            raise SupervisorError("F1-v2 terminal tree is not a successful receipt")
        expected_hashed_paths = expected - {FINAL_MANIFEST}
        observed_hashes = {
            name: _sha256_file(entries[name]) for name in sorted(expected_hashed_paths)
        }
        observed_sizes = {
            name: entries[name].stat().st_size for name in sorted(expected_hashed_paths)
        }
        if (
            not isinstance(final_manifest, dict)
            or final_manifest.get("exit_code") != 0
            or final_manifest.get("artifact_validation_passed") is not True
            or final_manifest.get("validation_error") is not None
            or final_manifest.get("unexpected_entries") != []
            or final_manifest.get("artifacts_sha256") != observed_hashes
            or final_manifest.get("artifacts_size_bytes") != observed_sizes
        ):
            raise SupervisorError("F1-v2 final manifest does not reconcile artifact hashes")


def _promote_scientific_artifacts(staging: Path, output: Path) -> None:
    promoted: list[Path] = []
    try:
        for name in SCIENTIFIC_ARTIFACTS:
            destination = output / name
            if destination.exists() or destination.is_symlink():
                raise SupervisorError(f"scientific promotion collision: {name}")
            os.replace(staging / name, destination)
            promoted.append(destination)
        staging.rmdir()
    except Exception:
        for path in promoted:
            path.unlink(missing_ok=True)
        _remove_staging(staging)
        raise


def _platform_supported() -> bool:
    return (
        os.name == "posix"
        and platform.system() == "Linux"
        and Path("/proc").is_dir()
        and all(hasattr(signal, name) for name in ("SIGUSR1", "SIGTERM", "SIGKILL"))
    )


@dataclass
class Supervisor:
    eoc_zip: Path
    eis_zip: Path
    output_dir: Path
    config_path: Path
    authorization_path: Path | None = None
    runner_path: Path | None = None
    limits: SupervisorLimits = SupervisorLimits()
    clock: Clock = field(default_factory=SystemClock)
    controller: ProcessController | None = None
    engine_module_for_validation: ModuleType | None = None
    platform_check: Callable[[], bool] = _platform_supported

    def _status(self, status: str, **extra: object) -> dict[str, object]:
        return {**FALSE_CLAIM_FLAGS, "status": status, **extra}

    def run(self) -> int:
        self.limits.validate()
        if not self.platform_check():
            raise SupervisorError(
                "unsupported platform: Linux /proc and POSIX signals are required"
            )
        engine = validate_engine_contract(self.engine_module_for_validation)
        eoc = _safe_regular_file(self.eoc_zip, "EOC archive")
        eis = _safe_regular_file(self.eis_zip, "EIS archive")
        config = _safe_regular_file(self.config_path, "analysis config")
        production_path = self.controller is None and self.engine_module_for_validation is None
        paired_bindings: dict[str, str] | None = None
        authorization_metadata: dict[str, object] | None = None
        if production_path:
            if self.authorization_path is None or self.runner_path is None:
                raise SupervisorError("production paired run requires external authorization")
            paired_bindings, argv, authorization_metadata = prepare_paired_run_bindings(
                eoc_zip=eoc,
                eis_zip=eis,
                output_dir=self.output_dir,
                config_path=config,
                authorization_path=self.authorization_path,
                runner_path=self.runner_path,
                expected_engine_sha256=engine["module_sha256"],
            )
        output = _prepare_output(self.output_dir)
        staging = _prepare_scientific_staging(output)
        authorization_record: dict[str, object] | None = None
        if production_path:
            assert paired_bindings is not None
            assert authorization_metadata is not None
            assert self.authorization_path is not None
            receipt = consume_paired_authorization(
                self.authorization_path,
                expected_bindings=paired_bindings,
                output_dir=output,
                now_unix_s=self.clock.time(),
            )
            if str(receipt.absolute()) != authorization_metadata["authorization_receipt"]:
                raise SupervisorError("paired authorization receipt path drifted")
            receipt_payload = validate_consumed_paired_authorization(
                self.authorization_path,
                receipt,
                expected_bindings=paired_bindings,
                output_dir=output,
                now_unix_s=self.clock.time(),
            )
            authorization_record = {
                "authorization_id": receipt_payload["authorization_id"],
                "authorization_sha256": receipt_payload["authorization_sha256"],
                "nonce": receipt_payload["nonce"],
                "receipt_sha256": _sha256_file(receipt),
            }
        else:
            argv = fixed_worker_argv(
                eoc_zip=eoc,
                eis_zip=eis,
                output_dir=staging,
                config_path=config,
                expected_engine_sha256=engine["module_sha256"],
            )
        start_manifest = {
            **FALSE_CLAIM_FLAGS,
            "schema_version": 1,
            "run_kind": "KIT_F1_PAIRED_B_D_CORE_DEVELOPMENT_V2",
            "started_at_unix_s": self.clock.time(),
            "engine": engine,
            "authorization": authorization_record,
            "bindings": paired_bindings,
            "inputs": {
                "eoc": {"basename": eoc.name, "sha256": _sha256_file(eoc)},
                "eis": {"basename": eis.name, "sha256": _sha256_file(eis)},
                "config": {"basename": config.name, "sha256": _sha256_file(config)},
            },
            "worker_argv_shape": [
                sys.executable,
                "-m",
                "battery_eis_soh.kit_dev_worker",
                "--worker",
                "--eoc-zip",
                "<EOC_PATH>",
                "--eis-zip",
                "<EIS_PATH>",
                "--output-dir",
                "<SCIENTIFIC_STAGING_PATH>",
                "--config-path",
                "<CONFIG_PATH>",
                "--authorized-output-dir",
                "<AUTHORIZED_OUTPUT_PATH>",
                "--authorization-path",
                "<AUTHORIZATION_PATH>",
                "--authorization-receipt",
                "<AUTHORIZATION_RECEIPT_PATH>",
                "--runner-path",
                "<RUNNER_PATH>",
                "--expected-engine-sha256",
                engine["module_sha256"],
            ],
            "limits": asdict(self.limits),
            "scientific_artifact_allowlist": list(SCIENTIFIC_ARTIFACTS),
        }
        start_binding = _write_immutable_json(output / "start_manifest.json", start_manifest)
        lifecycle = HashChainWriter(output / "lifecycle.jsonl")
        telemetry_log = HashChainWriter(output / "telemetry.jsonl")
        lifecycle.append({"event": "supervisor_started", "at_unix_s": self.clock.time()})
        status_binding = _write_atomic_json(output / "run_status.json", self._status("starting"))
        controller = self.controller or RealProcessController()
        try:
            process = controller.start(argv)
        except Exception as error:  # noqa: BLE001 - process boundary must produce a terminal record
            _remove_staging(staging)
            return self._finalize_without_process(
                output,
                lifecycle,
                telemetry_log,
                error,
                start_binding,
                status_binding,
            )
        lifecycle.append(
            {"event": "worker_started", "at_unix_s": self.clock.time(), "pid": process.pid}
        )
        try:
            self._verify_parent_evidence(
                output,
                start_binding,
                status_binding,
                lifecycle.binding(),
                telemetry_log.binding(),
            )
        except Exception as error:  # noqa: BLE001 - parent evidence boundary must fail closed
            cleanup_ok = self._cleanup_process_group(controller, process, lifecycle)
            _remove_staging(staging)
            try:
                lifecycle.append(
                    {
                        "event": "parent_evidence_pre_status_failed",
                        "error_type": type(error).__name__,
                        "cleanup_ok": cleanup_ok,
                    }
                )
            except (OSError, ValueError):
                pass
            return self._finalize_failure_only(
                output,
                lifecycle,
                telemetry_log,
                3,
                None,
                "parent_evidence_integrity_failure",
                start_binding,
                status_binding,
                validation_error=f"{type(error).__name__}: {error}",
            )
        status_binding = _write_atomic_json(
            output / "run_status.json", self._status("running", pid=process.pid)
        )
        started = self.clock.monotonic()
        last_heartbeat = started
        completed_fits: int | None = None
        planned_fits: int | None = None
        soft_sent = False
        term_sent = False
        kill_sent = False
        forced_reason: str | None = None
        worker_error_code: str | None = None
        exit_code: int | None = None
        while exit_code is None:
            now = self.clock.monotonic()
            elapsed = max(0.0, now - started)
            messages = controller.messages(process)
            for message in messages:
                safe_message = _sanitize_worker_message(message)
                lifecycle.append(
                    {
                        "event": "worker_progress",
                        "at_unix_s": self.clock.time(),
                        "payload": safe_message,
                    }
                )
                completed_value = safe_message.get("completed_fits")
                planned_value = safe_message.get("planned_fits")
                if isinstance(completed_value, int):
                    completed_fits = completed_value
                if isinstance(planned_value, int):
                    planned_fits = planned_value
                if safe_message.get("stage") == "heartbeat":
                    last_heartbeat = now
                if safe_message.get("stage") == "worker_failure":
                    worker_error_code = _allowlisted_development_error_code(
                        safe_message.get("error_code")
                    )
            metrics = controller.telemetry(process)
            remaining_fits = (
                max(0, planned_fits - completed_fits)
                if planned_fits is not None and completed_fits is not None
                else None
            )
            telemetry_log.append(
                {
                    "at_unix_s": self.clock.time(),
                    "elapsed_s": elapsed,
                    "remaining_until_soft_drain_s": max(0.0, self.limits.soft_drain_s - elapsed),
                    "remaining_fits": remaining_fits,
                    "cpu_seconds": metrics.cpu_seconds,
                    "rss_bytes": metrics.rss_bytes,
                    "process_count": metrics.process_count,
                }
            )
            if metrics.rss_bytes >= self.limits.rss_hard_bytes and not kill_sent:
                if forced_reason is None:
                    forced_reason = "rss_hard_limit"
                controller.send_group(process, signal.SIGKILL)
                kill_sent = True
                term_sent = True
                lifecycle.append(
                    {"event": "kill_sent", "reason": forced_reason, "at_unix_s": self.clock.time()}
                )
            elif (
                (
                    metrics.rss_bytes >= self.limits.rss_soft_bytes
                    or elapsed >= self.limits.soft_drain_s
                )
                and not soft_sent
                and not term_sent
            ):
                controller.send_group(process, signal.SIGUSR1)
                soft_sent = True
                lifecycle.append({"event": "soft_drain_sent", "at_unix_s": self.clock.time()})
            if now - last_heartbeat > self.limits.stale_s and not term_sent:
                if forced_reason is None:
                    forced_reason = "stale_heartbeat"
                controller.send_group(process, signal.SIGTERM)
                term_sent = True
                lifecycle.append(
                    {"event": "term_sent", "reason": forced_reason, "at_unix_s": self.clock.time()}
                )
            if elapsed >= self.limits.terminate_s and not term_sent:
                if forced_reason is None:
                    forced_reason = "terminate_deadline"
                controller.send_group(process, signal.SIGTERM)
                term_sent = True
                lifecycle.append(
                    {"event": "term_sent", "reason": forced_reason, "at_unix_s": self.clock.time()}
                )
            if elapsed >= self.limits.kill_s and not kill_sent:
                if forced_reason is None:
                    forced_reason = "kill_deadline"
                controller.send_group(process, signal.SIGKILL)
                kill_sent = True
                lifecycle.append(
                    {"event": "kill_sent", "reason": forced_reason, "at_unix_s": self.clock.time()}
                )
            exit_code = controller.poll(process)
            if exit_code is None:
                self.clock.sleep(self.limits.telemetry_interval_s)
        cleanup_ok = self._cleanup_process_group(controller, process, lifecycle)
        if not cleanup_ok and forced_reason is None:
            forced_reason = "process_group_cleanup_failed"
        lifecycle_append_error: OSError | ValueError | None = None
        try:
            lifecycle.append(
                {
                    "event": "worker_exited",
                    "at_unix_s": self.clock.time(),
                    "worker_exit_code": exit_code,
                    "forced_reason": forced_reason,
                }
            )
        except (OSError, ValueError) as error:
            lifecycle_append_error = error
            if forced_reason is None:
                forced_reason = "parent_evidence_write_failure"
        mapped = (
            124 if forced_reason is not None else (exit_code if exit_code in {0, 2, 3, 124} else 2)
        )
        if lifecycle_append_error is not None:
            mapped = 3
        return self._finalize(
            output,
            staging,
            lifecycle,
            telemetry_log,
            mapped,
            exit_code,
            forced_reason or worker_error_code,
            start_binding,
            status_binding,
            config,
            self.engine_module_for_validation is None,
        )

    def _wait_for_empty_group(
        self,
        controller: ProcessController,
        process: ProcessHandle,
        grace_s: float,
    ) -> bool:
        deadline = self.clock.monotonic() + grace_s
        while self.clock.monotonic() <= deadline:
            if controller.telemetry(process).process_count == 0:
                return True
            self.clock.sleep(self.limits.cleanup_poll_s)
        return controller.telemetry(process).process_count == 0

    def _cleanup_process_group(
        self,
        controller: ProcessController,
        process: ProcessHandle,
        lifecycle: HashChainWriter,
    ) -> bool:
        remaining = controller.telemetry(process).process_count
        if remaining == 0:
            return True
        controller.send_group(process, signal.SIGTERM)
        lifecycle.append(
            {
                "event": "cleanup_term_sent",
                "at_unix_s": self.clock.time(),
                "remaining_processes": remaining,
            }
        )
        if self._wait_for_empty_group(controller, process, self.limits.cleanup_term_grace_s):
            return True
        controller.send_group(process, signal.SIGKILL)
        lifecycle.append(
            {
                "event": "cleanup_kill_sent",
                "at_unix_s": self.clock.time(),
                "remaining_processes": controller.telemetry(process).process_count,
            }
        )
        return self._wait_for_empty_group(controller, process, self.limits.cleanup_kill_grace_s)

    def _finalize_without_process(
        self,
        output: Path,
        lifecycle: HashChainWriter,
        telemetry: HashChainWriter,
        error: BaseException,
        start_binding: ParentEvidenceBinding,
        status_binding: ParentEvidenceBinding,
    ) -> int:
        lifecycle.append({"event": "worker_start_failed", "error_type": type(error).__name__})
        return self._finalize_failure_only(
            output,
            lifecycle,
            telemetry,
            3,
            None,
            "worker_start_failed",
            start_binding,
            status_binding,
        )

    def _finalize(
        self,
        output: Path,
        staging: Path,
        lifecycle: HashChainWriter,
        telemetry: HashChainWriter,
        mapped_exit: int,
        worker_exit: int | None,
        forced_reason: str | None,
        start_binding: ParentEvidenceBinding,
        status_binding: ParentEvidenceBinding,
        config_path: Path,
        use_f1_v2_validator: bool,
    ) -> int:
        try:
            self._verify_parent_evidence(
                output,
                start_binding,
                status_binding,
                lifecycle.binding(),
                telemetry.binding(),
            )
            if mapped_exit == 0:
                if use_f1_v2_validator:
                    validate_kit_f1_v2_artifact_tree(
                        staging,
                        config_path,
                        require_terminal_tree=False,
                        enforce_frozen_official_counts=True,
                    )
                else:
                    _validate_scientific_staging(staging)
                _promote_scientific_artifacts(staging, output)
            else:
                _remove_staging(staging)
            lifecycle.append(
                {
                    "event": "finalizing",
                    "mapped_exit_code": mapped_exit,
                    "validation_error": None,
                }
            )
            return self._write_terminal_and_manifest(
                output,
                mapped_exit,
                worker_exit,
                forced_reason,
                None,
                start_binding=start_binding,
                status_binding=status_binding,
                lifecycle_binding=lifecycle.binding(),
                telemetry_binding=telemetry.binding(),
                strict_parent_integrity=True,
            )
        except Exception as error:  # noqa: BLE001 - finalization boundary must fail closed
            for name in SCIENTIFIC_ARTIFACTS:
                (output / name).unlink(missing_ok=True)
            _remove_staging(staging)
            try:
                lifecycle.append(
                    {
                        "event": "finalization_failed",
                        "error_type": type(error).__name__,
                    }
                )
            except (OSError, ValueError):
                pass
            return self._finalize_failure_only(
                output,
                lifecycle,
                telemetry,
                3,
                worker_exit,
                forced_reason or "finalization_failure",
                start_binding,
                status_binding,
                validation_error=f"{type(error).__name__}: {error}",
            )

    def _verify_parent_evidence(
        self,
        output: Path,
        start_binding: ParentEvidenceBinding,
        status_binding: ParentEvidenceBinding,
        lifecycle_binding: ParentEvidenceBinding,
        telemetry_binding: ParentEvidenceBinding,
    ) -> None:
        allowed_before_promotion = set(PARENT_ALWAYS_ARTIFACTS)
        entries = {path.name: path for path in output.iterdir()}
        if set(entries) != allowed_before_promotion:
            raise SupervisorError("parent evidence root contains an unexpected entry")
        bindings = {
            "start_manifest.json": start_binding,
            "run_status.json": status_binding,
            "lifecycle.jsonl": lifecycle_binding,
            "telemetry.jsonl": telemetry_binding,
        }
        errors = self._parent_evidence_errors(output, bindings)
        if errors:
            raise SupervisorError("; ".join(errors))

    def _parent_evidence_errors(
        self,
        output: Path,
        bindings: Mapping[str, ParentEvidenceBinding],
    ) -> list[str]:
        errors: list[str] = []
        for name, binding in bindings.items():
            path = output / name
            try:
                metadata = path.lstat()
            except OSError as error:
                errors.append(f"{name} is unavailable: {type(error).__name__}")
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                errors.append(f"{name} is not a regular parent artifact")
                continue
            try:
                current = path.read_bytes()
            except OSError as error:
                errors.append(f"{name} cannot be read: {type(error).__name__}")
                continue
            if current != binding.expected_bytes:
                errors.append(f"{name} bytes changed after parent write")
                continue
            if hashlib.sha256(current).hexdigest() != binding.sha256:
                errors.append(f"{name} hash changed after parent write")
                continue
            if binding.sequence is not None or binding.chain_head is not None:
                errors.extend(self._hash_chain_errors(name, current, binding))
        return errors

    def _hash_chain_errors(
        self,
        name: str,
        content: bytes,
        binding: ParentEvidenceBinding,
    ) -> list[str]:
        if binding.sequence is None or binding.chain_head is None:
            return [f"{name} has incomplete in-memory chain binding"]
        previous = "0" * 64
        try:
            lines = content.decode("utf-8").splitlines()
            if len(lines) != binding.sequence:
                return [f"{name} sequence count changed after parent write"]
            for sequence, line in enumerate(lines):
                row = json.loads(line)
                if not isinstance(row, dict):
                    return [f"{name} chain row is not an object"]
                event_hash = row.get("event_hash")
                event = {key: value for key, value in row.items() if key != "event_hash"}
                if event.get("sequence") != sequence or event.get("previous_hash") != previous:
                    return [f"{name} chain link is invalid"]
                expected_hash = hashlib.sha256(_canonical_json(event).encode()).hexdigest()
                if event_hash != expected_hash:
                    return [f"{name} event hash is invalid"]
                previous = expected_hash
        except (UnicodeDecodeError, json.JSONDecodeError):
            return [f"{name} chain encoding is invalid"]
        return [] if previous == binding.chain_head else [f"{name} chain head changed"]

    def _finalize_failure_only(
        self,
        output: Path,
        lifecycle: HashChainWriter,
        telemetry: HashChainWriter,
        exit_code: int,
        worker_exit: int | None,
        reason: str,
        start_binding: ParentEvidenceBinding,
        status_binding: ParentEvidenceBinding,
        *,
        validation_error: str | None = None,
    ) -> int:
        # Remove only parent-owned terminal slots and a possibly partial prior manifest.  The
        # start manifest is never repaired or rewritten; its mismatch remains explicit evidence.
        for name in (*TERMINAL_ARTIFACTS, FINAL_MANIFEST):
            candidate = output / name
            if candidate.exists() or candidate.is_symlink():
                if candidate.is_dir() and not candidate.is_symlink():
                    shutil.rmtree(candidate)
                else:
                    candidate.unlink(missing_ok=True)
        return self._write_terminal_and_manifest(
            output,
            exit_code,
            worker_exit,
            reason,
            validation_error,
            start_binding=start_binding,
            status_binding=status_binding,
            lifecycle_binding=lifecycle.binding(),
            telemetry_binding=telemetry.binding(),
            strict_parent_integrity=False,
        )

    def _write_terminal_and_manifest(
        self,
        output: Path,
        exit_code: int,
        worker_exit: int | None,
        reason: str | None,
        validation_error: str | None,
        *,
        start_binding: ParentEvidenceBinding,
        status_binding: ParentEvidenceBinding,
        lifecycle_binding: ParentEvidenceBinding,
        telemetry_binding: ParentEvidenceBinding,
        strict_parent_integrity: bool,
    ) -> int:
        status_name = "completed" if exit_code == 0 else "failed"
        terminal_name = "terminal.json" if exit_code == 0 else "failure.json"
        opposite = output / ("failure.json" if exit_code == 0 else "terminal.json")
        if opposite.exists() or opposite.is_symlink():
            if opposite.is_dir() and not opposite.is_symlink():
                shutil.rmtree(opposite)
            else:
                opposite.unlink(missing_ok=True)
        payload = self._status(
            status_name,
            exit_code=exit_code,
            worker_exit_code=worker_exit,
            reason=reason,
            validation_error=validation_error,
        )
        # Detect child/external mutation before the mutable status is legitimately replaced.
        pre_errors = self._parent_evidence_errors(
            output,
            {
                "start_manifest.json": start_binding,
                "run_status.json": status_binding,
                "lifecycle.jsonl": lifecycle_binding,
                "telemetry.jsonl": telemetry_binding,
            },
        )
        if pre_errors and strict_parent_integrity:
            raise SupervisorError("; ".join(pre_errors))
        if pre_errors:
            detail = "; ".join(pre_errors)
            validation_error = f"{validation_error}; {detail}" if validation_error else detail
        final_status_binding = _write_atomic_json(output / "run_status.json", payload)
        _write_immutable_json(output / terminal_name, payload)
        post_errors = self._parent_evidence_errors(
            output,
            {
                "start_manifest.json": start_binding,
                "run_status.json": final_status_binding,
                "lifecycle.jsonl": lifecycle_binding,
                "telemetry.jsonl": telemetry_binding,
            },
        )
        if post_errors and strict_parent_integrity:
            raise SupervisorError("; ".join(post_errors))
        if post_errors:
            detail = "; ".join(post_errors)
            validation_error = f"{validation_error}; {detail}" if validation_error else detail
        allowed = set(PARENT_ALWAYS_ARTIFACTS) | set(SCIENTIFIC_ARTIFACTS) | {terminal_name}
        hashes: dict[str, str] = {}
        sizes: dict[str, int] = {}
        unexpected: list[str] = []
        for path in sorted(output.iterdir(), key=lambda item: item.name):
            if path.name == FINAL_MANIFEST:
                continue
            metadata = path.lstat()
            if (
                path.name not in allowed
                or stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
            ):
                unexpected.append(path.name)
                continue
            hashes[path.name] = _sha256_file(path)
            sizes[path.name] = metadata.st_size
        manifest_passed = validation_error is None and not unexpected and exit_code == 0
        _write_immutable_json(
            output / FINAL_MANIFEST,
            {
                **FALSE_CLAIM_FLAGS,
                "exit_code": exit_code,
                "artifact_validation_passed": manifest_passed,
                "validation_error": validation_error,
                "unexpected_entries": unexpected,
                "artifacts_sha256": hashes,
                "artifacts_size_bytes": sizes,
            },
        )
        return exit_code


def _worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--eoc-zip", type=Path, required=True)
    parser.add_argument("--eis-zip", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--authorized-output-dir", type=Path)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--authorization-path", type=Path)
    parser.add_argument("--authorization-receipt", type=Path)
    parser.add_argument("--runner-path", type=Path)
    parser.add_argument("--expected-engine-sha256", required=True)
    return parser


def _is_official_engine_module(module: ModuleType | None) -> bool:
    if module is None or getattr(module, "__name__", None) == ENGINE_MODULE:
        return True
    origin = getattr(module, "__file__", None)
    if not isinstance(origin, str):
        return True
    function = getattr(module, ENGINE_CALLABLE, None)
    if callable(function):
        if getattr(function, "__module__", None) == ENGINE_MODULE:
            return True
        code = getattr(function, "__code__", None)
        code_filename = getattr(code, "co_filename", None)
        if isinstance(code_filename, str):
            try:
                code_source = _safe_regular_file(Path(code_filename), "injected callable source")
                official_source = _safe_regular_file(
                    Path(__file__).with_name("kit_dev_training.py"), "official engine module"
                )
                if code_source == official_source:
                    return True
            except (FileNotFoundError, OSError, SupervisorError):
                return True
    try:
        candidate = _safe_regular_file(Path(origin), "injected engine module")
        official = _safe_regular_file(
            Path(__file__).with_name("kit_dev_training.py"), "official engine module"
        )
        return candidate == official or _sha256_file(candidate) == _sha256_file(official)
    except (FileNotFoundError, OSError, SupervisorError):
        return False


class WorkerProgress:
    """Thread-safe progress channel with explicit periodic heartbeat events."""

    def __init__(
        self,
        cancellation: Mapping[str, bool],
        *,
        heartbeat_interval_s: float = 5.0,
        emit: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if heartbeat_interval_s <= 0:
            raise ValueError("heartbeat interval must be positive")
        self.cancellation = cancellation
        self.heartbeat_interval_s = heartbeat_interval_s
        self.emit = emit or self._emit_stdout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _emit_stdout(self, payload: dict[str, object]) -> None:
        print(_canonical_json(payload), flush=True)

    def __call__(self, payload: dict[str, object]) -> None:
        if self.cancellation.get("requested", False) and payload.get("stage") in {
            "fit_start",
            "fit_started",
        }:
            raise SoftDrainCancellation("soft drain observed at fit boundary")
        with self._lock:
            self.emit(payload)

    def emit_heartbeat(self) -> None:
        with self._lock:
            self.emit({"stage": "heartbeat"})

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_s):
            self.emit_heartbeat()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("heartbeat channel already started")
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="kit-worker-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.heartbeat_interval_s * 2.0))


def _worker_main_impl(
    argv: Sequence[str] | None = None,
    module: ModuleType | None = None,
    *,
    heartbeat_interval_s: float = 5.0,
    require_authorization: bool,
) -> int:
    args = _worker_parser().parse_args(argv)
    cancellation = {"requested": False}
    progress_channel: WorkerProgress | None = None

    def _request_soft_drain(_signal: int, _frame: object) -> None:
        cancellation["requested"] = True

    if not _platform_supported():
        return 3
    prior = signal.signal(signal.SIGUSR1, _request_soft_drain)
    try:
        if require_authorization:
            if any(
                value is None
                for value in (
                    args.authorized_output_dir,
                    args.authorization_path,
                    args.authorization_receipt,
                    args.runner_path,
                )
            ):
                raise WorkerContractError(
                    "direct paired worker invocation requires consumed external authorization"
                )
            assert args.authorized_output_dir is not None
            assert args.authorization_path is not None
            assert args.authorization_receipt is not None
            assert args.runner_path is not None
            bindings, expected_argv, _ = prepare_paired_run_bindings(
                eoc_zip=args.eoc_zip,
                eis_zip=args.eis_zip,
                output_dir=args.authorized_output_dir,
                config_path=args.config_path,
                authorization_path=args.authorization_path,
                runner_path=args.runner_path,
                expected_engine_sha256=args.expected_engine_sha256,
            )
            if args.output_dir.expanduser().absolute() != _paired_scientific_staging_path(
                args.authorized_output_dir
            ):
                raise WorkerContractError("scientific staging path is not authorization-derived")
            observed_argv = fixed_worker_argv(
                eoc_zip=_safe_regular_file(args.eoc_zip, "EOC archive"),
                eis_zip=_safe_regular_file(args.eis_zip, "EIS archive"),
                output_dir=args.output_dir.expanduser().absolute(),
                authorized_output_dir=args.authorized_output_dir.expanduser().absolute(),
                config_path=_safe_regular_file(args.config_path, "paired config"),
                authorization_path=_safe_regular_file(
                    args.authorization_path, "paired authorization"
                ),
                authorization_receipt=args.authorization_receipt.expanduser().absolute(),
                runner_path=_safe_regular_file(args.runner_path, "paired runner"),
                expected_engine_sha256=args.expected_engine_sha256,
            )
            if observed_argv != expected_argv:
                raise WorkerContractError("child command differs from authorized command")
            validate_consumed_paired_authorization(
                args.authorization_path,
                args.authorization_receipt,
                expected_bindings=bindings,
                output_dir=args.authorized_output_dir.expanduser().absolute(),
            )
        verify_engine_source(args.expected_engine_sha256, module)
        validate_engine_contract(module)
        loaded = importlib.import_module(ENGINE_MODULE) if module is None else module
        engine: Callable[..., dict[str, object]] = getattr(loaded, ENGINE_CALLABLE)

        progress_channel = WorkerProgress(
            cancellation,
            heartbeat_interval_s=heartbeat_interval_s,
        )
        progress_channel.start()

        result = engine(
            eoc_zip=args.eoc_zip,
            eis_zip=args.eis_zip,
            output_dir=args.output_dir,
            config_path=args.config_path,
            progress=progress_channel,
        )
        progress_channel({"stage": "engine_complete", **result})
        return 0
    except WorkerContractError as error:
        print(
            _canonical_json({"stage": "contract_failure", "error_type": type(error).__name__}),
            flush=True,
        )
        return 3
    except SoftDrainCancellation as error:
        print(
            _canonical_json({"stage": "soft_drain_cancelled", "error_type": type(error).__name__}),
            flush=True,
        )
        return 124
    except Exception as error:  # noqa: BLE001 - worker boundary maps all engine failures to exit 2
        error_code = _allowlisted_development_error_code(getattr(error, "error_code", None))
        error_type = (
            "DevelopmentRunError"
            if type(error).__name__ == "DevelopmentRunError"
            else "UnknownDevelopmentError"
        )
        print(
            _canonical_json(
                {
                    "stage": "worker_failure",
                    "error_type": error_type,
                    "error_code": error_code,
                }
            ),
            flush=True,
        )
        return 2
    finally:
        if progress_channel is not None:
            progress_channel.stop()
        signal.signal(signal.SIGUSR1, prior)


def _worker_main_test_fixture(
    argv: Sequence[str],
    module: ModuleType,
    *,
    heartbeat_interval_s: float = 5.0,
) -> int:
    """Private seam for synthetic contract fixtures; never reachable from ``__main__``."""
    if _is_official_engine_module(module):
        raise WorkerContractError("official engine is forbidden in the synthetic fixture seam")
    return _worker_main_impl(
        argv,
        module,
        heartbeat_interval_s=heartbeat_interval_s,
        require_authorization=False,
    )


def worker_main(
    argv: Sequence[str] | None = None,
    module: ModuleType | None = None,
    *,
    heartbeat_interval_s: float = 5.0,
) -> int:
    """Run the production worker; external one-use authorization is always mandatory."""
    return _worker_main_impl(
        argv,
        module,
        heartbeat_interval_s=heartbeat_interval_s,
        require_authorization=True,
    )


if __name__ == "__main__":
    raise SystemExit(worker_main())
