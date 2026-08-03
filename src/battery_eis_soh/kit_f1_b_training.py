"""Versioned, capacity-only KIT F1 development engine.

This module has no EIS input, no D_CORE view, no O2 task and no protected-evaluation path. It emits
development OOF evidence with every public/result claim flag held false.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import warnings
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import ElasticNet
from sklearn.metrics import r2_score
from sklearn.model_selection import GroupKFold
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from battery_eis_soh.kit_f1_b_authorization import validate_consumed_authorization
from battery_eis_soh.kit_v2 import (
    CapacityEndpoint,
    KitV2CapacityRecords,
    load_kit_v2_capacity_records,
)

RUN_KIND = "KIT_F1_B_CAPACITY_SEQUENCE_V1"
CONFIG_ID = "kit_f1_b_capacity_sequence_v1"
ARTIFACT_SCHEMA_VERSION = "kit_f1_b_capacity_sequence_v1"
F1_TASK_ID = "F1_SOH_LOSS_PP_H3"
HORIZON = 3
SEED = 20260801
MODE_BY_AGE_TYPE = {1: "calendar", 2: "cyclic", 3: "profile"}
ARTIFACT_NAMES = (
    "source_manifest.json",
    "snapshot_manifest.jsonl",
    "fold_registry.json",
    "oof_predictions.jsonl",
    "engine_summary.json",
)
ALLOWED_PARENT_RUNTIME_ARTIFACTS = frozenset(
    {"start_manifest.json", "telemetry.jsonl", "lifecycle.jsonl"}
)
FALSE_FLAGS: dict[str, bool] = {
    "scientific_result": False,
    "publication_ready": False,
    "resume_approved": False,
    "formal_model_result": False,
    "protected_test_result": False,
    "selection_performed": False,
}
B_FEATURE_NAMES = (
    "current_soh_percent",
    "previous_soh_percent",
    "two_checkups_back_soh_percent",
    "last_checkup_loss_pp",
    "two_interval_loss_pp",
    "checkup_index",
    "elapsed_days_since_q0",
    "normalized_cumulative_throughput_q0",
    "age_temperature_c",
    "age_charge_rate_c",
    "age_discharge_rate_c",
    "age_profile",
)


class BOnlyRunError(RuntimeError):
    """The B-only execution cannot safely continue."""


@dataclass(frozen=True)
class BSnapshot:
    snapshot_id: str
    cell_id: str
    condition_id: int
    mode: str
    checkup_index: int
    timestamp_s: float
    q0_ah: float
    capacity_ah: float
    soh_percent: float
    future_soh_percent: float | None
    soh_loss_pp_h3: float | None
    h3_endpoint_snapshot_id: str | None
    h3_checkup_index: int | None
    f1_target_available: bool
    eligibility_status: str
    b_values: tuple[float, ...]


@dataclass(frozen=True)
class FoldPlan:
    outer_fold: int
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    inner_folds: tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_finite(value: object, *, path: str = "$") -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BOnlyRunError(f"non-finite number at {path}")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(item, path=f"{path}/{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _assert_finite(item, path=f"{path}/{index}")


def _json_number(value: float | None) -> float | None:
    if value is None:
        return None
    return float(value) if math.isfinite(float(value)) else None


def _read_regular_bytes(path: Path, label: str) -> bytes:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise BOnlyRunError(f"{label} must be a regular non-symlink file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise BOnlyRunError(f"{label} changed during secure open")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _load_config(
    config_path: Path,
    *,
    expected_config_sha256: str,
    expected_schema_sha256: str,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    config_bytes = _read_regular_bytes(config_path, "B-only config")
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    if config_hash != expected_config_sha256:
        raise BOnlyRunError("B-only config changed after authorization")
    payload = json.loads(config_bytes)
    schema_path = config_path.with_name(f"{config_path.stem}.schema.json")
    schema_bytes = _read_regular_bytes(schema_path, "B-only schema")
    schema_hash = hashlib.sha256(schema_bytes).hexdigest()
    if schema_hash != expected_schema_sha256:
        raise BOnlyRunError("B-only schema changed after authorization")
    schema = json.loads(schema_bytes)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path)
    )
    if errors:
        locations = ["/".join(str(part) for part in error.path) or "<root>" for error in errors]
        raise BOnlyRunError(f"invalid B-only config at: {', '.join(locations)}")
    if payload["artifact_schema_sha256"] != schema_hash:
        raise BOnlyRunError("B-only config is not bound to the adjacent schema bytes")
    if payload["config_id"] != CONFIG_ID:
        raise BOnlyRunError("paired or unknown configuration is prohibited")
    if payload["artifact_schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise BOnlyRunError("B-only artifact schema identity drifted")
    if payload["feature_view"] != {
        "id": "B",
        "feature_names": list(B_FEATURE_NAMES),
        "eis_allowed": False,
        "o2_allowed": False,
    }:
        raise BOnlyRunError("only the exact capacity/context B feature view is allowed")
    if not payload["readiness"]["target_data_go"]:
        raise BOnlyRunError("target/data readiness is not GO")
    if not payload["readiness"]["completion_sequence_verified"]:
        raise BOnlyRunError("completed-checkup sequence semantics are not verified")
    if payload["readiness"]["execution_authorized"]:
        raise BOnlyRunError("frozen config cannot self-authorize B-only execution")
    if any(payload["authorization"].values()):
        raise BOnlyRunError("configuration attempts to authorize a prohibited claim or selection")
    return payload, config_hash, schema, schema_hash


def materialize_capacity_snapshots(records: KitV2CapacityRecords) -> list[BSnapshot]:
    """Create signed exact-H3 targets from ordered completed capacity endpoints only."""
    q0_by_cell = {record.cell.cell_id: record for record in records.q0_records}
    endpoints_by_cell: dict[str, list[CapacityEndpoint]] = defaultdict(list)
    for endpoint in records.capacity_endpoints:
        endpoints_by_cell[endpoint.cell.cell_id].append(endpoint)
    snapshots: list[BSnapshot] = []
    for cell_id in sorted(endpoints_by_cell):
        q0 = q0_by_cell.get(cell_id)
        if q0 is None or q0.ambiguity_flags:
            raise BOnlyRunError(f"cell {cell_id} lacks a unique reviewed Q0")
        endpoints = sorted(
            endpoints_by_cell[cell_id],
            key=lambda item: (item.provenance.timestamp_s, item.provenance.line_number),
        )
        if sum(endpoint.checkup_cycles == 0 for endpoint in endpoints) != 1:
            raise BOnlyRunError(f"cell {cell_id} does not have one check-up-zero endpoint")
        if endpoints[0].checkup_cycles != 0 or q0.provenance != endpoints[0].provenance:
            raise BOnlyRunError(f"cell {cell_id} Q0 is not the first completed endpoint")
        timestamps = [endpoint.provenance.timestamp_s for endpoint in endpoints]
        if any(right <= left for left, right in pairwise(timestamps)):
            raise BOnlyRunError(f"cell {cell_id} capacity endpoints are not strictly ordered")
        soh = [100.0 * endpoint.capacity_ah / q0.capacity_ah for endpoint in endpoints]
        for index, endpoint in enumerate(endpoints):
            mode = MODE_BY_AGE_TYPE.get(endpoint.age_type)
            if mode is None:
                raise BOnlyRunError(f"cell {cell_id} has unsupported age_type {endpoint.age_type}")
            history_available = index >= 2
            future_index = index + HORIZON
            h3_available = future_index < len(endpoints)
            if not history_available:
                status = "insufficient_history"
            elif not h3_available:
                status = "no_h3_followup"
            else:
                status = "eligible_f1_b"
            b_values: tuple[float, ...]
            if history_available:
                elapsed_days = (
                    endpoint.provenance.timestamp_s - q0.provenance.timestamp_s
                ) / 86_400.0
                normalized_throughput = (endpoint.total_charge_ah + endpoint.total_discharge_ah) / (
                    2.0 * q0.capacity_ah
                )
                b_values = (
                    soh[index],
                    soh[index - 1],
                    soh[index - 2],
                    soh[index - 1] - soh[index],
                    soh[index - 2] - soh[index],
                    float(index),
                    elapsed_days,
                    normalized_throughput,
                    float(endpoint.age_temperature_c),
                    endpoint.age_charge_rate_c,
                    endpoint.age_discharge_rate_c,
                    float(endpoint.age_profile),
                )
            else:
                b_values = (float("nan"),) * len(B_FEATURE_NAMES)
            future_soh = soh[future_index] if h3_available else None
            loss = soh[index] - future_soh if future_soh is not None else None
            snapshots.append(
                BSnapshot(
                    snapshot_id=f"{cell_id}:checkup-{index}",
                    cell_id=cell_id,
                    condition_id=endpoint.cell.condition,
                    mode=mode,
                    checkup_index=index,
                    timestamp_s=endpoint.provenance.timestamp_s,
                    q0_ah=q0.capacity_ah,
                    capacity_ah=endpoint.capacity_ah,
                    soh_percent=soh[index],
                    future_soh_percent=future_soh,
                    soh_loss_pp_h3=loss,
                    h3_endpoint_snapshot_id=(
                        f"{cell_id}:checkup-{future_index}" if h3_available else None
                    ),
                    h3_checkup_index=future_index if h3_available else None,
                    f1_target_available=h3_available,
                    eligibility_status=status,
                    b_values=b_values,
                )
            )
    identities = [snapshot.snapshot_id for snapshot in snapshots]
    if len(identities) != len(set(identities)):
        raise BOnlyRunError("B-only snapshot IDs are not unique")
    return snapshots


def _eligible(snapshots: Sequence[BSnapshot], mode: str) -> list[BSnapshot]:
    cohort = [
        snapshot
        for snapshot in snapshots
        if snapshot.mode == mode and snapshot.eligibility_status == "eligible_f1_b"
    ]
    if not cohort:
        raise BOnlyRunError(f"mode {mode} has no exact-H3 B-only cohort")
    return cohort


def _build_folds(cohort: Sequence[BSnapshot]) -> list[FoldPlan]:
    groups = np.asarray([snapshot.condition_id for snapshot in cohort], dtype=np.int64)
    targets = np.asarray([snapshot.soh_loss_pp_h3 for snapshot in cohort], dtype=np.float64)
    if not np.all(np.isfinite(targets)):
        raise BOnlyRunError("fold construction received unavailable target")
    if len(np.unique(groups)) < 3:
        raise BOnlyRunError("B-only cohort lacks three condition groups")
    plans: list[FoldPlan] = []
    for outer_fold, (train, validation) in enumerate(
        GroupKFold(n_splits=3).split(np.zeros(len(cohort)), targets, groups)
    ):
        train_groups = groups[train]
        inner_rows: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
        for inner_train, inner_validation in GroupKFold(n_splits=2).split(
            np.zeros(len(train)), targets[train], train_groups
        ):
            inner_rows.append(
                (
                    tuple(int(train[index]) for index in inner_train),
                    tuple(int(train[index]) for index in inner_validation),
                )
            )
        plans.append(
            FoldPlan(
                outer_fold=outer_fold,
                train_indices=tuple(int(index) for index in train),
                validation_indices=tuple(int(index) for index in validation),
                inner_folds=tuple(inner_rows),
            )
        )
    held = [index for fold in plans for index in fold.validation_indices]
    if sorted(held) != list(range(len(cohort))) or len(held) != len(set(held)):
        raise BOnlyRunError("outer folds do not cover each B-only snapshot exactly once")
    for fold in plans:
        train_conditions = {cohort[index].condition_id for index in fold.train_indices}
        validation_conditions = {cohort[index].condition_id for index in fold.validation_indices}
        if train_conditions & validation_conditions:
            raise BOnlyRunError("complete experimental condition crossed an outer fold")
    return plans


def _weights(snapshots: Sequence[BSnapshot]) -> np.ndarray:
    conditions = sorted({snapshot.condition_id for snapshot in snapshots})
    cells: dict[int, set[str]] = defaultdict(set)
    checkups: Counter[tuple[int, str]] = Counter()
    for snapshot in snapshots:
        cells[snapshot.condition_id].add(snapshot.cell_id)
        checkups[(snapshot.condition_id, snapshot.cell_id)] += 1
    raw = np.asarray(
        [
            1.0
            / (
                len(conditions)
                * len(cells[snapshot.condition_id])
                * checkups[(snapshot.condition_id, snapshot.cell_id)]
            )
            for snapshot in snapshots
        ],
        dtype=np.float64,
    )
    return raw / float(np.mean(raw))


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    selected = values[order]
    selected_weights = weights[order]
    cutoff = 0.5 * float(np.sum(selected_weights))
    return float(selected[np.searchsorted(np.cumsum(selected_weights), cutoff, side="left")])


def _preprocess(
    train: np.ndarray,
    evaluation: np.ndarray,
    train_weights: np.ndarray,
    *,
    standardize: bool,
) -> tuple[np.ndarray, np.ndarray]:
    medians = np.asarray(
        [
            _weighted_median(
                train[np.isfinite(train[:, column]), column],
                train_weights[np.isfinite(train[:, column])],
            )
            if np.any(np.isfinite(train[:, column]))
            else 0.0
            for column in range(train.shape[1])
        ]
    )
    train_filled = np.where(np.isfinite(train), train, medians)
    eval_filled = np.where(np.isfinite(evaluation), evaluation, medians)
    if standardize:
        means = np.average(train_filled, axis=0, weights=train_weights)
        scales = np.sqrt(np.average((train_filled - means) ** 2, axis=0, weights=train_weights))
        scales[scales == 0.0] = 1.0
        train_filled = (train_filled - means) / scales
        eval_filled = (eval_filled - means) / scales
    return train_filled, eval_filled


def _fit(
    family: str,
    train: Sequence[BSnapshot],
    validation: Sequence[BSnapshot],
) -> np.ndarray:
    x_train = np.asarray([snapshot.b_values for snapshot in train], dtype=np.float64)
    x_validation = np.asarray([snapshot.b_values for snapshot in validation], dtype=np.float64)
    y_train = np.asarray([snapshot.soh_loss_pp_h3 for snapshot in train], dtype=np.float64)
    weights = _weights(train)
    linear = family == "elastic_net_regression"
    x_train, x_validation = _preprocess(x_train, x_validation, weights, standardize=linear)
    if linear:
        model: Any = ElasticNet(
            alpha=0.01,
            l1_ratio=0.1,
            fit_intercept=True,
            max_iter=5000,
            tol=1e-5,
            selection="cyclic",
            random_state=SEED,
        )
    elif family == "hist_gradient_boosting_regressor":
        model = HistGradientBoostingRegressor(
            loss="squared_error",
            learning_rate=0.05,
            max_leaf_nodes=7,
            max_iter=100,
            min_samples_leaf=20,
            l2_regularization=1.0,
            early_stopping=False,
            random_state=SEED,
        )
    else:
        raise BOnlyRunError(f"unsupported B-only family {family}")
    with warnings.catch_warnings(), threadpool_limits(limits=1):
        warnings.filterwarnings("error", category=ConvergenceWarning)
        model.fit(x_train, y_train, sample_weight=weights)
    predictions = np.asarray(model.predict(x_validation), dtype=np.float64)
    if predictions.ndim != 1 or not np.all(np.isfinite(predictions)):
        raise BOnlyRunError(f"{family} produced non-finite predictions")
    return predictions


def _metrics(cohort: Sequence[BSnapshot], predictions: np.ndarray) -> dict[str, object]:
    targets = np.asarray([snapshot.soh_loss_pp_h3 for snapshot in cohort], dtype=np.float64)
    weights = _weights(cohort)
    error = np.asarray(predictions, dtype=np.float64) - targets
    r2: float | None = None
    if len(targets) >= 2 and not np.allclose(targets, targets[0]):
        candidate = float(r2_score(targets, predictions, sample_weight=weights))
        r2 = candidate if math.isfinite(candidate) else None
    return {
        "condition_balanced_mae_pp_v1": float(np.average(np.abs(error), weights=weights)),
        "condition_balanced_median_ae_pp_v1": _weighted_median(np.abs(error), weights),
        "condition_balanced_rmse_pp_v1": float(
            math.sqrt(float(np.average(error**2, weights=weights)))
        ),
        "condition_balanced_signed_bias_pp_v1": float(np.average(error, weights=weights)),
        "condition_balanced_r2_v1": r2,
        "snapshots": len(cohort),
        "cells": len({snapshot.cell_id for snapshot in cohort}),
        "conditions": len({snapshot.condition_id for snapshot in cohort}),
    }


def _prediction_row(
    snapshot: BSnapshot,
    *,
    family: str,
    role: str,
    outer_fold: int,
    predicted: float,
) -> dict[str, object]:
    if snapshot.soh_loss_pp_h3 is None or snapshot.future_soh_percent is None:
        raise BOnlyRunError("OOF prediction received a snapshot without exact H3 truth")
    return {
        **FALSE_FLAGS,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "task_id": F1_TASK_ID,
        "view": "B",
        "model_role": role,
        "model_family": family,
        "snapshot_id": snapshot.snapshot_id,
        "cell_id": snapshot.cell_id,
        "condition_id": snapshot.condition_id,
        "mode": snapshot.mode,
        "outer_fold": outer_fold,
        "observed_soh_loss_pp_h3": snapshot.soh_loss_pp_h3,
        "predicted_soh_loss_pp_h3": float(predicted),
        "current_soh_percent": snapshot.soh_percent,
        "observed_future_soh_percent": snapshot.future_soh_percent,
        "predicted_future_soh_percent": snapshot.soh_percent - float(predicted),
    }


def _run_oof(
    snapshots: Sequence[BSnapshot], progress: Callable[[dict[str, object]], None]
) -> tuple[dict[str, list[FoldPlan]], list[dict[str, object]], list[dict[str, object]], int]:
    folds_by_mode = {
        mode: _build_folds(_eligible(snapshots, mode)) for mode in ("calendar", "cyclic", "profile")
    }
    rows: list[dict[str, object]] = []
    metric_rows: list[dict[str, object]] = []
    fitted = ("elastic_net_regression", "hist_gradient_boosting_regressor")
    planned = 3 * 3 * len(fitted)
    completed = 0
    ordered_ids = [snapshot.snapshot_id for snapshot in snapshots]
    fold_ids = {
        mode: [
            {
                "outer": fold.outer_fold,
                "train": list(fold.train_indices),
                "validation": list(fold.validation_indices),
                "inner": [
                    [list(train), list(validation)] for train, validation in fold.inner_folds
                ],
            }
            for fold in folds
        ]
        for mode, folds in folds_by_mode.items()
    }
    trial_keys = [
        [mode, "B", family, outer]
        for mode in ("calendar", "cyclic", "profile")
        for family in fitted
        for outer in range(3)
    ]
    progress(
        {
            "stage": "preflight_frozen",
            "view": "B",
            "ordered_row_sha256": hashlib.sha256(
                _canonical_json(ordered_ids).encode("utf-8")
            ).hexdigest(),
            "fold_sha256": hashlib.sha256(_canonical_json(fold_ids).encode("utf-8")).hexdigest(),
            "trial_plan_sha256": hashlib.sha256(
                _canonical_json(trial_keys).encode("utf-8")
            ).hexdigest(),
            "planned_fits": planned,
            "completed_fits": 0,
        }
    )
    for mode in ("calendar", "cyclic", "profile"):
        cohort = _eligible(snapshots, mode)
        folds = folds_by_mode[mode]
        fold_for_index: dict[int, int] = {
            index: fold.outer_fold for fold in folds for index in fold.validation_indices
        }
        for family in ("persistence_zero_loss", "recent_slope_h3"):
            predictions = np.asarray(
                [
                    0.0 if family == "persistence_zero_loss" else 3.0 * float(snapshot.b_values[3])
                    for snapshot in cohort
                ],
                dtype=np.float64,
            )
            rows.extend(
                _prediction_row(
                    snapshot,
                    family=family,
                    role="baseline",
                    outer_fold=fold_for_index[index],
                    predicted=float(predictions[index]),
                )
                for index, snapshot in enumerate(cohort)
            )
            metric_rows.append(
                {
                    "mode": mode,
                    "view": "B",
                    "model_family": family,
                    "metrics": _metrics(cohort, predictions),
                }
            )
        for family in fitted:
            oof = np.full(len(cohort), np.nan, dtype=np.float64)
            for fold in folds:
                progress(
                    {
                        "stage": "fit_start",
                        "mode": mode,
                        "view": "B",
                        "model_family": family,
                        "outer_fold": fold.outer_fold,
                        "completed_fits": completed,
                        "planned_fits": planned,
                    }
                )
                train = [cohort[index] for index in fold.train_indices]
                validation = [cohort[index] for index in fold.validation_indices]
                predicted = _fit(family, train, validation)
                oof[list(fold.validation_indices)] = predicted
                rows.extend(
                    _prediction_row(
                        snapshot,
                        family=family,
                        role="fitted_candidate",
                        outer_fold=fold.outer_fold,
                        predicted=float(value),
                    )
                    for snapshot, value in zip(validation, predicted, strict=True)
                )
                completed += 1
                progress(
                    {
                        "stage": "fit_complete",
                        "mode": mode,
                        "view": "B",
                        "model_family": family,
                        "outer_fold": fold.outer_fold,
                        "completed_fits": completed,
                        "planned_fits": planned,
                    }
                )
            if np.any(~np.isfinite(oof)):
                raise BOnlyRunError("B-only OOF vector is incomplete")
            metric_rows.append(
                {
                    "mode": mode,
                    "view": "B",
                    "model_family": family,
                    "metrics": _metrics(cohort, oof),
                }
            )
    if completed != planned:
        raise BOnlyRunError("B-only fit accounting mismatch")
    return folds_by_mode, rows, metric_rows, completed


def _validator(schema: Mapping[str, Any], definition: str) -> Draft202012Validator:
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": schema["$defs"],
        "$ref": f"#/$defs/{definition}",
    }
    return Draft202012Validator(wrapper)


def _validate_payloads(payloads: Mapping[str, str], schema: Mapping[str, Any]) -> None:
    if tuple(payloads) != ARTIFACT_NAMES:
        raise BOnlyRunError("B-only artifact allowlist drifted")
    definitions = {
        "source_manifest.json": "sourceManifest",
        "snapshot_manifest.jsonl": "snapshotRow",
        "fold_registry.json": "foldRegistry",
        "oof_predictions.jsonl": "predictionRow",
        "engine_summary.json": "engineSummary",
    }
    for name, definition in definitions.items():
        values = (
            [json.loads(line) for line in payloads[name].splitlines() if line.strip()]
            if name.endswith(".jsonl")
            else [json.loads(payloads[name])]
        )
        for value in values:
            _assert_finite(value)
            errors = list(_validator(schema, definition).iter_errors(value))
            if errors:
                raise BOnlyRunError(f"invalid {name}: {errors[0].message}")
            serialized = _canonical_json(value).lower()
            if any(token in serialized for token in ('"d_core"', '"o2_', '"eis_')):
                raise BOnlyRunError(f"prohibited D_CORE/O2/EIS field escaped into {name}")


def _write_once(path: Path, text: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def execute_b_only_analysis(
    *,
    eoc_zip: Path,
    output_dir: Path,
    config_path: Path,
    authorization_path: Path,
    authorization_receipt: Path,
    runtime_bindings: dict[str, str],
    progress: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """Run the exact authorized B-only development contract."""
    validate_consumed_authorization(
        authorization_path,
        authorization_receipt,
        expected_bindings=runtime_bindings,
        output_dir=output_dir.expanduser().absolute(),
    )
    config, config_hash, schema, schema_hash = _load_config(
        config_path,
        expected_config_sha256=runtime_bindings["config_sha256"],
        expected_schema_sha256=runtime_bindings["schema_sha256"],
    )
    if _sha256_file(eoc_zip) != runtime_bindings["eoc_sha256"]:
        raise BOnlyRunError("EOC archive changed after authorization")
    source = config["source"]
    records = load_kit_v2_capacity_records(
        eoc_zip,
        expected_sha256=str(source["eoc_sha256"]),
        expected_cells=int(source["expected_cells"]),
        expected_capacity_endpoints=int(source["expected_capacity_endpoints"]),
    )
    snapshots = materialize_capacity_snapshots(records)
    folds, predictions, metrics, completed = _run_oof(snapshots, progress)
    fold_payload: dict[str, object] = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_kind": RUN_KIND,
        "split_policy": "condition_grouped_outer3_inner2_b_only_v1",
        "view": "B",
        "modes": {
            mode: [
                {
                    "outer_fold": fold.outer_fold,
                    "train_snapshot_ids": [
                        cohort[index].snapshot_id for index in fold.train_indices
                    ],
                    "validation_snapshot_ids": [
                        cohort[index].snapshot_id for index in fold.validation_indices
                    ],
                    "inner_folds": [
                        {
                            "train_snapshot_ids": [cohort[index].snapshot_id for index in train],
                            "validation_snapshot_ids": [
                                cohort[index].snapshot_id for index in validation
                            ],
                        }
                        for train, validation in fold.inner_folds
                    ],
                }
                for fold in mode_folds
            ]
            for mode, mode_folds in folds.items()
            for cohort in (_eligible(snapshots, mode),)
        },
    }
    fold_payload["fold_sha256"] = hashlib.sha256(
        _canonical_json(fold_payload["modes"]).encode("utf-8")
    ).hexdigest()
    source_manifest = {
        **FALSE_FLAGS,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_kind": RUN_KIND,
        "source_archive": records.eoc_archive,
        "source_counts": records.counts,
    }
    snapshot_rows = [
        {
            **FALSE_FLAGS,
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            **asdict(snapshot),
            "future_soh_percent": _json_number(snapshot.future_soh_percent),
            "soh_loss_pp_h3": _json_number(snapshot.soh_loss_pp_h3),
            "b_values": [_json_number(value) for value in snapshot.b_values],
        }
        for snapshot in sorted(snapshots, key=lambda item: item.snapshot_id)
    ]
    summary = {
        **FALSE_FLAGS,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_kind": RUN_KIND,
        "view": "B",
        "planned_fits": 18,
        "completed_fits": completed,
        "prediction_rows": len(predictions),
        "metrics": metrics,
        "config_sha256": config_hash,
        "artifact_schema_sha256": schema_hash,
        "artifact_paths": list(ARTIFACT_NAMES),
    }
    payloads = {
        "source_manifest.json": json.dumps(source_manifest, indent=2, sort_keys=True) + "\n",
        "snapshot_manifest.jsonl": "".join(_canonical_json(row) + "\n" for row in snapshot_rows),
        "fold_registry.json": json.dumps(fold_payload, indent=2, sort_keys=True) + "\n",
        "oof_predictions.jsonl": "".join(
            _canonical_json(row) + "\n"
            for row in sorted(
                predictions,
                key=lambda row: (
                    str(row["mode"]),
                    str(row["model_family"]),
                    str(row["snapshot_id"]),
                ),
            )
        ),
        "engine_summary.json": json.dumps(summary, indent=2, sort_keys=True) + "\n",
    }
    _validate_payloads(payloads, schema)
    output = output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    existing = {path.name for path in output.iterdir()}
    unexpected = existing - ALLOWED_PARENT_RUNTIME_ARTIFACTS
    if unexpected:
        raise FileExistsError(
            f"B-only output directory has unexpected entries: {sorted(unexpected)}"
        )
    for name in ARTIFACT_NAMES:
        _write_once(output / name, payloads[name])
    return {
        **FALSE_FLAGS,
        "artifact_paths": list(ARTIFACT_NAMES),
        "planned_fits": 18,
        "completed_fits": completed,
        "config_sha256": config_hash,
        "artifact_schema_sha256": schema_hash,
    }
