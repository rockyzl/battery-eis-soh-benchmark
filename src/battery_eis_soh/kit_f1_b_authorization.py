"""Strict one-run authorization consumption for the KIT F1 B-only runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Mapping
from pathlib import Path

RUN_KIND = "KIT_F1_B_CAPACITY_SEQUENCE_V1"
SCOPE = "F1 capacity-sequence B-family training only"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN = {
    "d_core": True,
    "eis": True,
    "o2": True,
    "protected_test": True,
    "publication": True,
    "deployment": True,
}
BINDING_KEYS = frozenset(
    {
        "eoc_sha256",
        "config_sha256",
        "schema_sha256",
        "engine_sha256",
        "worker_sha256",
        "runner_sha256",
        "loader_sha256",
        "lockfile_sha256",
        "source_tree_sha256",
        "command_sha256",
        "cwd_sha256",
        "output_sha256",
        "environment_sha256",
    }
)


class AuthorizationError(RuntimeError):
    """The external one-run authorization is absent, stale, mismatched or consumed."""


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_exclusive_json(path: Path, value: object) -> None:
    """Create final bytes with O_EXCL; never replace or overwrite an existing target."""
    payload = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    # Retain a partial final-path inode on write failure as fail-closed evidence;
    # never unlink a name another actor could have swapped after exclusive create.
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_linked_exclusive_json(path: Path, value: object) -> None:
    """Publish complete bytes atomically without ever replacing the final name."""
    pending = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(16)}.pending")
    create_exclusive_json(pending, value)
    linked = False
    try:
        os.link(pending, path, follow_symlinks=False)
        linked = True
        _fsync_directory(path.parent)
    except BaseException:
        if linked:
            try:
                pending_stat = pending.lstat()
                final_stat = path.lstat()
                if (pending_stat.st_dev, pending_stat.st_ino) == (
                    final_stat.st_dev,
                    final_stat.st_ino,
                ):
                    path.unlink()
                    _fsync_directory(path.parent)
            except OSError:
                pass
        raise
    finally:
        try:
            pending.unlink()
            _fsync_directory(path.parent)
        except OSError:
            pass


def _read_regular_nofollow(path: Path, label: str) -> tuple[Path, bytes]:
    """Read a regular file while rejecting symlinks in every existing path component."""
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        metadata = current.lstat()
        if _is_link_or_reparse(metadata):
            raise AuthorizationError(f"{label} path contains a symlink or reparse point")
    before = absolute.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise AuthorizationError(f"{label} must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise AuthorizationError(f"{label} changed during secure open")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
    finally:
        os.close(descriptor)
    return absolute, raw


def peek_authorization_nonce(authorization_path: Path) -> str:
    """Read only the nonce needed to construct the exact child command binding."""
    _, raw = _read_regular_nofollow(authorization_path, "authorization")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise AuthorizationError("authorization must be a JSON object")
    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or HEX64.fullmatch(nonce) is None:
        raise AuthorizationError("authorization nonce must be 64 lowercase hex characters")
    return nonce


def _validate_authorization(
    payload: object,
    *,
    expected_bindings: Mapping[str, str],
    now_unix_s: float,
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise AuthorizationError("authorization must be a JSON object")
    required = {
        "authorization_version",
        "authorization_id",
        "status",
        "single_use",
        "run_kind",
        "scope",
        "nonce",
        "issued_at_unix_s",
        "expires_at_unix_s",
        "bindings",
        "forbidden",
        "approver_role",
        "evidence_reviewer",
    }
    if set(payload) != required:
        raise AuthorizationError("authorization fields do not match the closed contract")
    if payload["authorization_version"] != "kit_f1_b_one_run_v1":
        raise AuthorizationError("authorization version is not B-only v1")
    if payload["status"] != "authorized" or payload["single_use"] is not True:
        raise AuthorizationError("authorization is not an active single-use grant")
    if payload["run_kind"] != RUN_KIND or payload["scope"] != SCOPE:
        raise AuthorizationError("authorization scope/run kind mismatch")
    if payload["forbidden"] != FORBIDDEN:
        raise AuthorizationError("authorization fails to prohibit out-of-scope work")
    if not isinstance(payload["authorization_id"], str) or not payload["authorization_id"]:
        raise AuthorizationError("authorization ID is missing")
    nonce = payload["nonce"]
    if not isinstance(nonce, str) or HEX64.fullmatch(nonce) is None:
        raise AuthorizationError("authorization nonce must be 64 lowercase hex characters")
    issued = float(payload["issued_at_unix_s"])
    expires = float(payload["expires_at_unix_s"])
    if not (issued <= now_unix_s <= expires and expires - issued <= 86_400):
        raise AuthorizationError("authorization is not currently valid or exceeds 24 hours")
    bindings = payload["bindings"]
    if not isinstance(bindings, dict) or set(bindings) != BINDING_KEYS:
        raise AuthorizationError("authorization bindings do not match the closed contract")
    if set(expected_bindings) != BINDING_KEYS:
        raise AuthorizationError("runtime did not supply the complete binding set")
    for key in sorted(BINDING_KEYS):
        value = bindings[key]
        expected = expected_bindings[key]
        if not isinstance(value, str) or HEX64.fullmatch(value) is None or value != expected:
            raise AuthorizationError(f"authorization binding mismatch: {key}")
    for identity in ("approver_role", "evidence_reviewer"):
        if not isinstance(payload[identity], str) or not payload[identity].strip():
            raise AuthorizationError(f"authorization {identity} is missing")
    return nonce, str(payload["authorization_id"])


def consume_authorization(
    authorization_path: Path,
    *,
    expected_bindings: Mapping[str, str],
    output_dir: Path,
    now_unix_s: float | None = None,
) -> Path:
    """Validate and atomically consume an external hash-bound authorization once."""
    path, raw = _read_regular_nofollow(authorization_path, "authorization")
    payload = json.loads(raw)
    now_value = time.time() if now_unix_s is None else now_unix_s
    nonce, authorization_id = _validate_authorization(
        payload, expected_bindings=expected_bindings, now_unix_s=now_value
    )
    receipt = path.with_name(f".{path.name}.{nonce}.consumed.json")
    create_exclusive_json(
        receipt,
        {
            "authorization_id": authorization_id,
            "authorization_sha256": sha256_bytes(raw),
            "bindings": dict(expected_bindings),
            "consumed": True,
            "consumed_at_unix_s": now_value,
            "nonce": nonce,
            "output_sha256": sha256_bytes(str(output_dir).encode()),
            "run_kind": RUN_KIND,
            "scope": SCOPE,
        },
    )
    return receipt


def validate_consumption_receipt(path: Path) -> dict[str, object]:
    _, raw = _read_regular_nofollow(path, "authorization receipt")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise AuthorizationError("authorization receipt is not an object")
    required = {
        "authorization_id",
        "authorization_sha256",
        "bindings",
        "consumed",
        "consumed_at_unix_s",
        "nonce",
        "output_sha256",
        "run_kind",
        "scope",
    }
    if (
        set(payload) != required
        or not isinstance(payload.get("authorization_id"), str)
        or HEX64.fullmatch(str(payload.get("authorization_sha256"))) is None
        or not isinstance(payload.get("bindings"), dict)
        or set(payload["bindings"]) != BINDING_KEYS
        or any(
            not isinstance(value, str) or HEX64.fullmatch(value) is None
            for value in payload["bindings"].values()
        )
        or not isinstance(payload.get("consumed_at_unix_s"), (int, float))
        or HEX64.fullmatch(str(payload.get("output_sha256"))) is None
        or payload.get("consumed") is not True
        or payload.get("run_kind") != RUN_KIND
        or payload.get("scope") != SCOPE
        or not isinstance(payload.get("nonce"), str)
        or HEX64.fullmatch(str(payload["nonce"])) is None
    ):
        raise AuthorizationError("authorization receipt failed closed")
    return payload


def validate_consumed_authorization(
    authorization_path: Path,
    receipt_path: Path,
    *,
    expected_bindings: Mapping[str, str],
    output_dir: Path,
    now_unix_s: float | None = None,
) -> dict[str, object]:
    """Revalidate external authorization, receipt derivation, and current runtime bytes."""
    authorization, raw = _read_regular_nofollow(authorization_path, "authorization")
    payload = json.loads(raw)
    now_value = time.time() if now_unix_s is None else now_unix_s
    nonce, authorization_id = _validate_authorization(
        payload,
        expected_bindings=expected_bindings,
        now_unix_s=now_value,
    )
    expected_receipt = authorization.with_name(f".{authorization.name}.{nonce}.consumed.json")
    if receipt_path.expanduser().absolute() != expected_receipt:
        raise AuthorizationError("authorization receipt path is not derived from authorization")
    receipt = validate_consumption_receipt(expected_receipt)
    if (
        receipt["authorization_id"] != authorization_id
        or receipt["authorization_sha256"] != sha256_bytes(raw)
        or receipt["nonce"] != nonce
        or receipt["bindings"] != dict(expected_bindings)
        or receipt["output_sha256"] != sha256_bytes(str(output_dir.absolute()).encode())
    ):
        raise AuthorizationError("consumed authorization evidence does not match this run")
    return receipt
