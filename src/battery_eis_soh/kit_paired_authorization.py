"""One-use external authorization for the KIT F1 paired B/D_CORE development run."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from pathlib import Path

from battery_eis_soh.kit_f1_b_authorization import (
    _read_regular_nofollow,
    create_exclusive_json,
    sha256_bytes,
)

RUN_KIND = "KIT_F1_PAIRED_B_D_CORE_DEVELOPMENT_V2"
SCOPE = "F1-only paired B/D_CORE development with EIS; no selection or public claim"
VERSION = "kit_f1_paired_one_run_v1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ALLOWED = {"f1": True, "b": True, "d_core": True, "eis": True}
FORBIDDEN = {
    "o2_training": True,
    "o2_display": True,
    "model_selection": True,
    "champion_promotion": True,
    "protected_test": True,
    "publication": True,
    "resume_claim": True,
    "deployment": True,
}
BINDING_KEYS = frozenset(
    {
        "eoc_sha256",
        "eis_sha256",
        "config_sha256",
        "schema_sha256",
        "readiness_sha256",
        "engine_sha256",
        "worker_sha256",
        "runner_sha256",
        "loader_sha256",
        "lockfile_sha256",
        "source_tree_sha256",
        "command_sha256",
        "cwd_sha256",
        "output_sha256",
        "paired_cohort_sha256",
        "paired_designs_sha256",
        "trial_keys_sha256",
    }
)


class PairedAuthorizationError(RuntimeError):
    """The paired authorization is absent, stale, mismatched, or already consumed."""


def _validate(
    payload: object, *, expected_bindings: Mapping[str, str], now_unix_s: float
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise PairedAuthorizationError("paired authorization must be a JSON object")
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
        "allowed",
        "forbidden",
        "approver_role",
        "evidence_reviewer",
    }
    if set(payload) != required:
        raise PairedAuthorizationError("paired authorization fields drifted")
    if (
        payload["authorization_version"] != VERSION
        or payload["status"] != "authorized"
        or payload["single_use"] is not True
        or payload["run_kind"] != RUN_KIND
        or payload["scope"] != SCOPE
        or payload["allowed"] != ALLOWED
        or payload["forbidden"] != FORBIDDEN
    ):
        raise PairedAuthorizationError("paired authorization scope is invalid")
    nonce = payload["nonce"]
    if not isinstance(nonce, str) or HEX64.fullmatch(nonce) is None:
        raise PairedAuthorizationError("paired authorization nonce is invalid")
    issued = float(payload["issued_at_unix_s"])
    expires = float(payload["expires_at_unix_s"])
    if not (issued <= now_unix_s <= expires and expires - issued <= 86_400):
        raise PairedAuthorizationError("paired authorization is expired or exceeds 24 hours")
    bindings = payload["bindings"]
    if (
        not isinstance(bindings, dict)
        or set(bindings) != BINDING_KEYS
        or set(expected_bindings) != BINDING_KEYS
    ):
        raise PairedAuthorizationError("paired authorization binding set drifted")
    for key in BINDING_KEYS:
        value = bindings[key]
        if (
            not isinstance(value, str)
            or HEX64.fullmatch(value) is None
            or value != expected_bindings[key]
        ):
            raise PairedAuthorizationError(f"paired authorization binding mismatch: {key}")
    for key in ("authorization_id", "approver_role", "evidence_reviewer"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise PairedAuthorizationError(f"paired authorization {key} is missing")
    return nonce, str(payload["authorization_id"])


def peek_nonce(path: Path) -> str:
    _, raw = _read_regular_nofollow(path, "paired authorization")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("nonce"), str):
        raise PairedAuthorizationError("paired authorization nonce is missing")
    nonce = str(payload["nonce"])
    if HEX64.fullmatch(nonce) is None:
        raise PairedAuthorizationError("paired authorization nonce is invalid")
    return nonce


def receipt_path(authorization_path: Path, nonce: str) -> Path:
    path = authorization_path.expanduser().absolute()
    return path.with_name(f".{path.name}.{nonce}.consumed.json")


def consume(
    authorization_path: Path,
    *,
    expected_bindings: Mapping[str, str],
    output_dir: Path,
    now_unix_s: float | None = None,
) -> Path:
    path, raw = _read_regular_nofollow(authorization_path, "paired authorization")
    now = time.time() if now_unix_s is None else now_unix_s
    payload = json.loads(raw)
    nonce, authorization_id = _validate(
        payload, expected_bindings=expected_bindings, now_unix_s=now
    )
    receipt = receipt_path(path, nonce)
    create_exclusive_json(
        receipt,
        {
            "authorization_id": authorization_id,
            "authorization_sha256": sha256_bytes(raw),
            "bindings": dict(expected_bindings),
            "consumed": True,
            "consumed_at_unix_s": now,
            "nonce": nonce,
            "output_sha256": sha256_bytes(str(output_dir.absolute()).encode()),
            "run_kind": RUN_KIND,
            "scope": SCOPE,
        },
    )
    return receipt


def validate_consumed(
    authorization_path: Path,
    consumed_receipt: Path,
    *,
    expected_bindings: Mapping[str, str],
    output_dir: Path,
    now_unix_s: float | None = None,
) -> dict[str, object]:
    path, raw = _read_regular_nofollow(authorization_path, "paired authorization")
    now = time.time() if now_unix_s is None else now_unix_s
    payload = json.loads(raw)
    nonce, authorization_id = _validate(
        payload, expected_bindings=expected_bindings, now_unix_s=now
    )
    expected_receipt = receipt_path(path, nonce)
    if consumed_receipt.expanduser().absolute() != expected_receipt:
        raise PairedAuthorizationError("paired authorization receipt path drifted")
    _, receipt_raw = _read_regular_nofollow(expected_receipt, "paired authorization receipt")
    receipt = json.loads(receipt_raw)
    if (
        not isinstance(receipt, dict)
        or receipt.get("authorization_id") != authorization_id
        or receipt.get("authorization_sha256") != sha256_bytes(raw)
        or receipt.get("bindings") != dict(expected_bindings)
        or receipt.get("consumed") is not True
        or receipt.get("nonce") != nonce
        or receipt.get("output_sha256") != sha256_bytes(str(output_dir.absolute()).encode())
        or receipt.get("run_kind") != RUN_KIND
        or receipt.get("scope") != SCOPE
    ):
        raise PairedAuthorizationError("paired authorization receipt failed closed")
    return receipt
