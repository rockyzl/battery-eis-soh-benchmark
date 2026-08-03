"""Development-only KIT F1 capability pipeline with O2 blocked.

The module consumes only development records, emits no protected/test result, performs no winner
selection, and writes only the seven immutable scientific artifacts owned by the engine contract.
Timestamp and retry semantics remain explicitly provisional.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import warnings
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
from jsonschema import Draft202012Validator
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import ElasticNet, LogisticRegression
from sklearn.metrics import average_precision_score, r2_score
from sklearn.model_selection import GroupKFold
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from battery_eis_soh.kit_v2 import (
    CapacityEndpoint,
    EisSpectrum,
    KitV2DevelopmentRecords,
    load_kit_v2_development_records,
)

SEED = 20260801
ARTIFACT_SCHEMA_VERSION = "kit_f1_o2_dev_v2"
F1_TASK_ID = "F1_SOH_LOSS_PP_H3"
F1_TARGET_NAME = "soh_loss_pp_h3"
O2_TASK_ID = "O2_RAPID_LOSS_5PP_H3"
O2_TARGET_NAME = "rapid_loss_ge_5pp_h3"
O2_HORIZON_CHECKUPS = 3
O2_LOSS_THRESHOLD_PERCENTAGE_POINTS = 5.0
RETRY_WINDOW_S = 216_000.0
ALLOWED_EIS_SEQUENCES = tuple(seq for seq in range(29) if seq not in {0, 13})
MODE_BY_AGE_TYPE = {1: "calendar", 2: "cyclic", 3: "profile"}
ARTIFACT_NAMES = (
    "acquisition_manifest.jsonl",
    "snapshot_manifest.jsonl",
    "fold_registry.json",
    "oof_predictions.jsonl",
    "paired_deltas.json",
    "retry_sensitivity.json",
    "engine_summary.json",
)
FALSE_RESULT_FLAGS: dict[str, bool] = {
    "scientific_result": False,
    "publication_ready": False,
    "resume_approved": False,
    "formal_model_result": False,
    "protected_test_result": False,
    "timestamp_semantics_provisional": True,
    "retry_semantics_provisional": True,
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
EIS_FEATURE_NAMES = tuple(
    name
    for seq in ALLOWED_EIS_SEQUENCES
    for name in (f"z_real_comp_seq_{seq}", f"z_imag_comp_seq_{seq}")
)


DEVELOPMENT_ERROR_CODES = frozenset(
    {
        "convergence_failed",
        "invalid_predictions",
        "invalid_input",
        "unknown_development_failure",
    }
)


class DevelopmentRunError(RuntimeError):
    """The development-only analysis cannot safely continue."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "unknown_development_failure",
    ) -> None:
        if error_code not in DEVELOPMENT_ERROR_CODES:
            raise ValueError("development error code is not allowlisted")
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class AcquisitionRecord:
    acquisition_id: str
    cell_id: str
    condition_id: int
    timestamp_s: float
    timestamp_origin: int
    duration_s: float
    sd_block_id: int
    source_archive: str
    source_archive_sha256: str
    source_member: str
    source_line: int
    aligned_snapshot_id: str | None
    within_60h_candidate_only: bool
    same_aligned_checkup_as_previous: bool
    within_60h_candidate_reason: str
    eis_values: tuple[float, ...]


@dataclass(frozen=True)
class DevelopmentSnapshot:
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
    o2_label: int | None
    b_values: tuple[float, ...]
    primary_eis_values: tuple[float, ...]
    keep_last_eis_values: tuple[float, ...]
    acquisition_ids: tuple[str, ...]
    finite_acquisition_count_by_feature: tuple[int, ...]
    keep_last_acquisition_id: str | None
    prediction_available_at_s: float | None = None


@dataclass(frozen=True)
class AnalysisPlan:
    outer_group_folds: int = 3
    inner_group_folds: int = 2
    minimum_conditions_per_mode: int = 3
    minimum_conditions_per_validation_fold: int = 1
    bootstrap_draws: int = 2_000
    minimum_valid_bootstrap_draws: int = 1_900
    bootstrap_seed: int = SEED


@dataclass(frozen=True)
class FoldPlan:
    outer_fold: int
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    inner_folds: tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]


@dataclass(frozen=True)
class PlattParameters:
    slope: float
    intercept: float
    iterations: int


@dataclass(frozen=True)
class EngineAnalysis:
    acquisitions: tuple[AcquisitionRecord, ...]
    snapshots: tuple[DevelopmentSnapshot, ...]
    fold_registry: dict[str, object]
    predictions: tuple[dict[str, object], ...]
    paired_deltas: tuple[dict[str, object], ...]
    retry_sensitivity: tuple[dict[str, object], ...]
    summary: dict[str, object]
    planned_fits: int
    completed_fits: int


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _assert_json_finite(value: object, *, path: str = "<root>") -> None:
    """Reject non-finite JSON numbers before schema validation or persistence."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DevelopmentRunError(f"non-finite JSON number at {path}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_json_finite(item, path=f"{path}/{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _assert_json_finite(item, path=f"{path}/{index}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_number(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def _finite_float(value: object, *, name: str) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise DevelopmentRunError(f"{name} is not numeric") from error
    if not math.isfinite(numeric):
        raise DevelopmentRunError(f"{name} is not finite")
    return numeric


def _validated_binary_arrays(
    probabilities: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    *,
    context: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    probability_array = np.asarray(probabilities, dtype=np.float64)
    label_array = np.asarray(labels, dtype=np.int64)
    weight_array = np.asarray(weights, dtype=np.float64)
    if not (
        probability_array.ndim == label_array.ndim == weight_array.ndim == 1
        and len(probability_array) == len(label_array) == len(weight_array)
        and len(probability_array) > 0
    ):
        raise DevelopmentRunError(f"{context} arrays must be aligned non-empty vectors")
    if not np.all(np.isfinite(probability_array)) or np.any(
        (probability_array < 0.0) | (probability_array > 1.0)
    ):
        raise DevelopmentRunError(f"{context} probabilities must be finite and within [0, 1]")
    if {int(value) for value in np.unique(label_array)} != {0, 1}:
        raise DevelopmentRunError(f"{context} requires binary labels with both classes")
    if not np.all(np.isfinite(weight_array)) or np.any(weight_array <= 0.0):
        raise DevelopmentRunError(f"{context} weights must be finite and positive")
    return probability_array, label_array, weight_array


def _eis_vector(spectrum: EisSpectrum) -> tuple[float, ...]:
    points = {point.seq_nr: point for point in spectrum.points}
    values: list[float] = []
    for seq in ALLOWED_EIS_SEQUENCES:
        point = points[seq]
        values.extend(
            float("nan") if value is None else float(value)
            for value in (point.z_real_comp_mohm, point.z_imag_comp_mohm)
        )
    return tuple(values)


def _acquisition_id(spectrum: EisSpectrum) -> str:
    identity = {
        "archive_sha256": spectrum.provenance.archive_sha256,
        "member": spectrum.provenance.member,
        "source_line": spectrum.provenance.line_number,
        "timestamp_s": spectrum.provenance.timestamp_s,
        "sd_block_id": spectrum.sd_block_id,
    }
    return f"ACQ-{_sha256_bytes(_canonical_json(identity).encode())[:20]}"


def _keep_last_key(record: AcquisitionRecord) -> tuple[float, int, str, int, str]:
    return (
        record.timestamp_s,
        record.sd_block_id,
        record.source_member,
        record.source_line,
        record.acquisition_id,
    )


def _equal_finite_mean(
    vectors: Sequence[tuple[float, ...]],
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    if not vectors:
        raise DevelopmentRunError("cannot aggregate an empty acquisition group")
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(EIS_FEATURE_NAMES):
        raise DevelopmentRunError("unexpected EIS acquisition feature shape")
    finite = np.isfinite(matrix)
    counts = np.sum(finite, axis=0)
    sums = np.sum(np.where(finite, matrix, 0.0), axis=0)
    means = np.divide(
        sums,
        counts,
        out=np.full(matrix.shape[1], np.nan, dtype=np.float64),
        where=counts > 0,
    )
    return tuple(float(value) for value in means), tuple(int(value) for value in counts)


def materialize_development_snapshots(
    records: KitV2DevelopmentRecords,
    *,
    retry_window_s: float = RETRY_WINDOW_S,
) -> tuple[list[DevelopmentSnapshot], list[AcquisitionRecord], dict[str, object]]:
    """Emit one primary snapshot per aligned check-up and retain every acquisition."""
    q0_by_cell = {record.cell.cell_id: record for record in records.q0_records}
    endpoints_by_cell: dict[str, list[CapacityEndpoint]] = defaultdict(list)
    for endpoint in records.capacity_endpoints:
        endpoints_by_cell[endpoint.cell.cell_id].append(endpoint)

    eligible_spectra = [
        spectrum
        for spectrum in records.eis_spectra
        if spectrum.valid == 1
        and spectrum.charged == 1
        and spectrum.is_rt == 1
        and spectrum.soc_nom == 50
    ]
    spectra_by_cell: dict[str, list[EisSpectrum]] = defaultdict(list)
    for spectrum in eligible_spectra:
        spectra_by_cell[spectrum.cell.cell_id].append(spectrum)

    failures: Counter[str] = Counter()
    snapshots: list[DevelopmentSnapshot] = []
    acquisitions: list[AcquisitionRecord] = []
    within_60h_candidate_count = 0

    for cell_id in sorted(endpoints_by_cell):
        q0 = q0_by_cell.get(cell_id)
        if q0 is None or q0.ambiguity_flags:
            failures["missing_or_ambiguous_q0"] += 1
            continue
        endpoints = sorted(
            endpoints_by_cell[cell_id],
            key=lambda item: (item.provenance.timestamp_s, item.provenance.line_number),
        )
        endpoint_timestamps = [item.provenance.timestamp_s for item in endpoints]
        spectra = sorted(
            spectra_by_cell.get(cell_id, []),
            key=lambda item: (
                item.provenance.timestamp_s,
                item.sd_block_id,
                item.provenance.member,
                item.provenance.line_number,
                _acquisition_id(item),
            ),
        )
        endpoint_to_acquisitions: dict[int, list[AcquisitionRecord]] = defaultdict(list)
        previous_timestamp: float | None = None
        previous_aligned_snapshot_id: str | None = None
        for spectrum in spectra:
            endpoint_index = bisect_right(endpoint_timestamps, spectrum.provenance.timestamp_s) - 1
            snapshot_id = f"{cell_id}:checkup-{endpoint_index}" if endpoint_index >= 0 else None
            within_60h = (
                previous_timestamp is not None
                and spectrum.provenance.timestamp_s - previous_timestamp <= retry_window_s
            )
            same_aligned_checkup = (
                within_60h
                and snapshot_id is not None
                and snapshot_id == previous_aligned_snapshot_id
            )
            if same_aligned_checkup:
                candidate_reason = "within_60h_and_same_aligned_checkup_candidate_only"
            elif within_60h:
                candidate_reason = "within_60h_but_different_aligned_checkup_candidate_only"
            else:
                candidate_reason = "not_within_60h_candidate_window"
            within_60h_candidate_count += int(within_60h)
            record = AcquisitionRecord(
                acquisition_id=_acquisition_id(spectrum),
                cell_id=cell_id,
                condition_id=spectrum.cell.condition,
                timestamp_s=spectrum.provenance.timestamp_s,
                timestamp_origin=spectrum.provenance.timestamp_origin,
                duration_s=spectrum.provenance.duration_s,
                sd_block_id=spectrum.sd_block_id,
                source_archive=spectrum.provenance.archive_name,
                source_archive_sha256=spectrum.provenance.archive_sha256,
                source_member=spectrum.provenance.member,
                source_line=spectrum.provenance.line_number,
                aligned_snapshot_id=snapshot_id,
                within_60h_candidate_only=within_60h,
                same_aligned_checkup_as_previous=same_aligned_checkup,
                within_60h_candidate_reason=candidate_reason,
                eis_values=_eis_vector(spectrum),
            )
            acquisitions.append(record)
            if endpoint_index >= 0:
                endpoint_to_acquisitions[endpoint_index].append(record)
            else:
                failures["eis_without_prior_capacity"] += 1
            previous_timestamp = spectrum.provenance.timestamp_s
            previous_aligned_snapshot_id = snapshot_id

        soh = [100.0 * endpoint.capacity_ah / q0.capacity_ah for endpoint in endpoints]
        for index, endpoint in enumerate(endpoints):
            mode = MODE_BY_AGE_TYPE.get(endpoint.age_type)
            if mode is None:
                failures["unknown_mode_excluded"] += 1
                continue
            future_index = index + O2_HORIZON_CHECKUPS
            aligned = endpoint_to_acquisitions.get(index, [])
            history_available = index >= 2
            h3_available = future_index < len(endpoints)
            if not history_available:
                eligibility_status = "insufficient_history"
                failures[eligibility_status] += 1
            elif not h3_available:
                eligibility_status = "no_h3_followup"
                failures[eligibility_status] += 1
            elif not aligned:
                eligibility_status = "missing_eis"
                failures[eligibility_status] += 1
            else:
                eligibility_status = "eligible_f1_and_d_core"
                failures[eligibility_status] += 1
            if aligned:
                primary_values, finite_counts = _equal_finite_mean(
                    [record.eis_values for record in aligned]
                )
                keep_last = max(aligned, key=_keep_last_key)
                keep_last_values = keep_last.eis_values
                keep_last_id: str | None = keep_last.acquisition_id
                prediction_available_at_s: float | None = max(
                    record.timestamp_s + record.duration_s for record in aligned
                )
            else:
                primary_values = (float("nan"),) * len(EIS_FEATURE_NAMES)
                finite_counts = (0,) * len(EIS_FEATURE_NAMES)
                keep_last_values = (float("nan"),) * len(EIS_FEATURE_NAMES)
                keep_last_id = None
                prediction_available_at_s = None
            elapsed_days = (endpoint.provenance.timestamp_s - q0.provenance.timestamp_s) / 86_400.0
            normalized_throughput = (endpoint.total_charge_ah + endpoint.total_discharge_ah) / (
                2.0 * q0.capacity_ah
            )
            b_values: tuple[float, ...]
            if history_available:
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
                DevelopmentSnapshot(
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
                    eligibility_status=eligibility_status,
                    o2_label=(
                        int(loss >= O2_LOSS_THRESHOLD_PERCENTAGE_POINTS)
                        if loss is not None
                        else None
                    ),
                    b_values=b_values,
                    primary_eis_values=primary_values,
                    keep_last_eis_values=keep_last_values,
                    acquisition_ids=tuple(
                        record.acquisition_id for record in sorted(aligned, key=_keep_last_key)
                    ),
                    finite_acquisition_count_by_feature=finite_counts,
                    keep_last_acquisition_id=keep_last_id,
                    prediction_available_at_s=prediction_available_at_s,
                )
            )

    snapshot_ids = [snapshot.snapshot_id for snapshot in snapshots]
    if len(snapshot_ids) != len(set(snapshot_ids)):
        raise DevelopmentRunError("snapshot identity is not one-to-one with aligned check-ups")
    return (
        snapshots,
        acquisitions,
        {
            "counts": dict(sorted(failures.items())),
            "eligible_acquisitions": len(acquisitions),
            "within_60h_candidate_count": within_60h_candidate_count,
            "policy": {
                "primary": "equal_finite_mean_per_frequency_v1",
                "sensitivity": "keep_last_acquisition_v1",
                "retry_window_seconds": retry_window_s,
                "retry_window_role": "flag_only_never_group_or_drop",
                "timestamp_semantics_provisional": True,
            },
        },
    )


def hierarchical_weights(snapshots: Sequence[DevelopmentSnapshot]) -> np.ndarray:
    """Return label-free condition/cell/check-up weights normalized to mean one."""
    if not snapshots:
        raise DevelopmentRunError("cannot weight an empty partition")
    conditions = sorted({snapshot.condition_id for snapshot in snapshots})
    cells_by_condition: dict[int, set[str]] = defaultdict(set)
    checkups_by_cell: Counter[tuple[int, str]] = Counter()
    for snapshot in snapshots:
        cells_by_condition[snapshot.condition_id].add(snapshot.cell_id)
        checkups_by_cell[(snapshot.condition_id, snapshot.cell_id)] += 1
    raw = np.asarray(
        [
            1.0
            / (
                len(conditions)
                * len(cells_by_condition[snapshot.condition_id])
                * checkups_by_cell[(snapshot.condition_id, snapshot.cell_id)]
            )
            for snapshot in snapshots
        ],
        dtype=np.float64,
    )
    if not np.all(np.isfinite(raw)) or np.any(raw <= 0):
        raise DevelopmentRunError("hierarchical weights are invalid")
    return raw / float(np.mean(raw))


def _eligible_for_f1_b(snapshot: DevelopmentSnapshot) -> bool:
    return snapshot.eligibility_status in {"eligible_f1_and_d_core", "missing_eis"}


def _eligible_for_d_core(snapshot: DevelopmentSnapshot) -> bool:
    return snapshot.eligibility_status == "eligible_f1_and_d_core"


def _class_support(
    snapshots: Sequence[DevelopmentSnapshot], indices: Sequence[int]
) -> dict[str, int]:
    selected = [snapshots[int(index)] for index in indices]
    observed_labels = [snapshot.o2_label for snapshot in selected]
    if any(label not in {0, 1} for label in observed_labels):
        raise DevelopmentRunError("O2 support received a row without an observed H3 label")
    positive_conditions = {snapshot.condition_id for snapshot in selected if snapshot.o2_label == 1}
    negative_conditions = {snapshot.condition_id for snapshot in selected if snapshot.o2_label == 0}
    return {
        "snapshots": len(selected),
        "events": sum(int(label) for label in observed_labels if label is not None),
        "non_events": len(selected)
        - sum(int(label) for label in observed_labels if label is not None),
        "cells": len({snapshot.cell_id for snapshot in selected}),
        "conditions": len({snapshot.condition_id for snapshot in selected}),
        "positive_supporting_conditions": len(positive_conditions),
        "negative_supporting_conditions": len(negative_conditions),
    }


def _regression_support(
    snapshots: Sequence[DevelopmentSnapshot], indices: Sequence[int]
) -> dict[str, int]:
    selected = [snapshots[int(index)] for index in indices]
    observed_losses = [snapshot.soh_loss_pp_h3 for snapshot in selected]
    if any(loss is None for loss in observed_losses):
        raise DevelopmentRunError("F1 support received a row without an observed H3 target")
    return {
        "snapshots": len(selected),
        "cells": len({snapshot.cell_id for snapshot in selected}),
        "conditions": len({snapshot.condition_id for snapshot in selected}),
        "negative_loss_snapshots": sum(
            float(loss) < 0.0 for loss in observed_losses if loss is not None
        ),
        "o2_candidate_events": sum(int(snapshot.o2_label or 0) for snapshot in selected),
    }


def build_fold_plans(
    snapshots: Sequence[DevelopmentSnapshot], plan: AnalysisPlan
) -> tuple[dict[str, list[FoldPlan]], dict[str, object]]:
    """Create one condition-grouped split registry shared by F1/O2 and every feature view."""
    folds_by_mode: dict[str, list[FoldPlan]] = {}
    support_report: dict[str, object] = {}
    for mode in ("calendar", "cyclic", "profile"):
        cohort = [
            snapshot
            for snapshot in snapshots
            if snapshot.mode == mode and _eligible_for_f1_b(snapshot)
        ]
        if not cohort:
            raise DevelopmentRunError(f"mode {mode} has no eligible snapshots")
        all_indices = tuple(range(len(cohort)))
        overall = _regression_support(cohort, all_indices)
        if overall["conditions"] < plan.minimum_conditions_per_mode:
            raise DevelopmentRunError(f"mode {mode} fails overall condition-support preflight")
        y = np.asarray(
            [
                float(snapshot.soh_loss_pp_h3)
                for snapshot in cohort
                if snapshot.soh_loss_pp_h3 is not None
            ],
            dtype=np.float64,
        )
        if len(y) != len(cohort):
            raise DevelopmentRunError(f"mode {mode} contains an unavailable F1 target")
        groups = np.asarray([snapshot.condition_id for snapshot in cohort], dtype=np.int64)
        if len(np.unique(groups)) < plan.outer_group_folds:
            raise DevelopmentRunError(f"mode {mode} lacks outer condition groups")
        outer = GroupKFold(n_splits=plan.outer_group_folds)
        mode_folds: list[FoldPlan] = []
        mode_support: list[dict[str, object]] = []
        for outer_fold, (train_index, validation_index) in enumerate(
            outer.split(np.zeros(len(cohort)), y, groups)
        ):
            validation_support = _regression_support(cohort, validation_index)
            if validation_support["conditions"] < plan.minimum_conditions_per_validation_fold:
                raise DevelopmentRunError(
                    f"mode {mode} outer fold {outer_fold} fails validation support"
                )
            train_groups = groups[train_index]
            if len(np.unique(train_groups)) < plan.inner_group_folds:
                raise DevelopmentRunError(f"mode {mode} outer fold lacks inner groups")
            inner = GroupKFold(n_splits=plan.inner_group_folds)
            inner_folds: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
            inner_support: list[dict[str, object]] = []
            for inner_fold, (relative_train, relative_validation) in enumerate(
                inner.split(
                    np.zeros(len(train_index)),
                    y[train_index],
                    train_groups,
                )
            ):
                inner_train = train_index[relative_train]
                inner_validation = train_index[relative_validation]
                support = _regression_support(cohort, inner_validation)
                if support["conditions"] < plan.minimum_conditions_per_validation_fold:
                    raise DevelopmentRunError(
                        f"mode {mode} outer {outer_fold} inner {inner_fold} fails validation support"
                    )
                inner_folds.append(
                    (
                        tuple(int(index) for index in inner_train),
                        tuple(int(index) for index in inner_validation),
                    )
                )
                inner_support.append({"inner_fold": inner_fold, **support})
            mode_folds.append(
                FoldPlan(
                    outer_fold=outer_fold,
                    train_indices=tuple(int(index) for index in train_index),
                    validation_indices=tuple(int(index) for index in validation_index),
                    inner_folds=tuple(inner_folds),
                )
            )
            mode_support.append(
                {
                    "outer_fold": outer_fold,
                    "validation": validation_support,
                    "inner_validation": inner_support,
                }
            )
        folds_by_mode[mode] = mode_folds
        support_report[mode] = {"overall": overall, "folds": mode_support}
    return folds_by_mode, support_report


def _matrix(
    snapshots: Sequence[DevelopmentSnapshot], view: str, acquisition_policy: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    if view == "B":
        values = [snapshot.b_values for snapshot in snapshots]
        names = list(B_FEATURE_NAMES)
    elif view == "D_CORE":
        if acquisition_policy == "primary":
            eis = [snapshot.primary_eis_values for snapshot in snapshots]
        elif acquisition_policy == "keep_last_sensitivity":
            eis = [snapshot.keep_last_eis_values for snapshot in snapshots]
        else:
            raise DevelopmentRunError(f"unknown acquisition policy {acquisition_policy}")
        values = [
            (*snapshot.b_values, *eis_values)
            for snapshot, eis_values in zip(snapshots, eis, strict=True)
        ]
        names = [*B_FEATURE_NAMES, *EIS_FEATURE_NAMES]
    else:
        raise DevelopmentRunError(f"unsupported feature view {view}")
    return (
        np.asarray(values, dtype=np.float64),
        np.asarray([snapshot.soh_loss_pp_h3 for snapshot in snapshots], dtype=np.float64),
        np.asarray([snapshot.condition_id for snapshot in snapshots], dtype=np.int64),
        names,
    )


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    finite = np.isfinite(values)
    if not np.any(finite):
        return 0.0
    selected = values[finite]
    selected_weights = weights[finite]
    order = np.argsort(selected, kind="stable")
    selected = selected[order]
    selected_weights = selected_weights[order]
    cutoff = 0.5 * float(np.sum(selected_weights))
    return float(selected[np.searchsorted(np.cumsum(selected_weights), cutoff, side="left")])


def _fold_preprocess(
    train: np.ndarray,
    evaluation: np.ndarray,
    train_weights: np.ndarray,
    names: Sequence[str],
    *,
    standardize: bool,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    if (
        train.ndim != 2
        or evaluation.ndim != 2
        or train.shape[0] == 0
        or train.shape[1] != evaluation.shape[1]
        or train.shape[1] != len(names)
        or train_weights.ndim != 1
        or len(train_weights) != train.shape[0]
        or not np.all(np.isfinite(train_weights))
        or np.any(train_weights <= 0.0)
        or np.any(np.isinf(train))
        or np.any(np.isinf(evaluation))
    ):
        raise DevelopmentRunError(
            "fold preprocessing received invalid arrays or weights",
            error_code="invalid_input",
        )
    missing_columns = np.any(np.isnan(train), axis=0)
    medians = np.asarray(
        [_weighted_median(train[:, column], train_weights) for column in range(train.shape[1])]
    )
    train_missing = np.isnan(train[:, missing_columns]).astype(np.float64)
    eval_missing = np.isnan(evaluation[:, missing_columns]).astype(np.float64)
    train_filled = np.where(np.isnan(train), medians, train)
    eval_filled = np.where(np.isnan(evaluation), medians, evaluation)
    prepared_names = list(names)
    if np.any(missing_columns):
        train_filled = np.column_stack([train_filled, train_missing])
        eval_filled = np.column_stack([eval_filled, eval_missing])
        prepared_names.extend(
            f"{name}__missing"
            for name, missing in zip(names, missing_columns, strict=True)
            if missing
        )
    if standardize:
        means = np.average(train_filled, axis=0, weights=train_weights)
        variance = np.average((train_filled - means) ** 2, axis=0, weights=train_weights)
        scales = np.sqrt(variance)
        scales[scales == 0] = 1.0
        train_filled = (train_filled - means) / scales
        eval_filled = (eval_filled - means) / scales
    if not np.all(np.isfinite(train_filled)) or not np.all(np.isfinite(eval_filled)):
        raise DevelopmentRunError(
            "fold preprocessing produced nonfinite values",
            error_code="invalid_input",
        )
    return train_filled, eval_filled, prepared_names


def _preprocessing_audit(
    snapshots: Sequence[DevelopmentSnapshot],
    folds_by_mode: Mapping[str, Sequence[FoldPlan]],
) -> dict[str, list[dict[str, object]]]:
    """Freeze fold-local preprocessing provenance without persisting learned values."""
    audit: dict[str, list[dict[str, object]]] = {}
    workflows = (
        ("primary", "B"),
        ("primary", "D_CORE"),
        ("keep_last_sensitivity", "D_CORE"),
    )
    for mode, folds in folds_by_mode.items():
        cohort = [
            snapshot
            for snapshot in snapshots
            if snapshot.mode == mode and _eligible_for_f1_b(snapshot)
        ]
        rows: list[dict[str, object]] = []
        for acquisition_policy, view in workflows:
            x, _, _, names = _matrix(cohort, view, acquisition_policy)
            for family in ("elastic_net_regression", "hist_gradient_boosting_regressor"):
                for fold in folds:
                    train = x[list(fold.train_indices)]
                    train_snapshots = [cohort[index] for index in fold.train_indices]
                    weights = hierarchical_weights(train_snapshots)
                    missing_columns = np.any(np.isnan(train), axis=0)
                    medians = np.asarray(
                        [
                            _weighted_median(train[:, column], weights)
                            for column in range(train.shape[1])
                        ],
                        dtype=np.float64,
                    )
                    filled = np.where(np.isnan(train), medians, train)
                    indicator_names = [
                        f"{name}__missing"
                        for name, missing in zip(names, missing_columns, strict=True)
                        if missing
                    ]
                    if indicator_names:
                        filled = np.column_stack(
                            [filled, np.isnan(train[:, missing_columns]).astype(np.float64)]
                        )
                    imputation_payload = {
                        "input_feature_names": list(names),
                        "median_hex": [float(value).hex() for value in medians],
                        "indicator_names": indicator_names,
                    }
                    standardize = family == "elastic_net_regression"
                    scaling_hash: str | None = None
                    if standardize:
                        means = np.average(filled, axis=0, weights=weights)
                        variance = np.average((filled - means) ** 2, axis=0, weights=weights)
                        scales = np.sqrt(variance)
                        scales[scales == 0] = 1.0
                        scaling_hash = _sha256_bytes(
                            _canonical_json(
                                {
                                    "mean_hex": [float(value).hex() for value in means],
                                    "scale_hex": [float(value).hex() for value in scales],
                                }
                            ).encode()
                        )
                    rows.append(
                        {
                            "outer_fold": fold.outer_fold,
                            "acquisition_policy": acquisition_policy,
                            "view": view,
                            "model_family": family,
                            "retained_eis_feature_names": (
                                list(EIS_FEATURE_NAMES) if view == "D_CORE" else []
                            ),
                            "missing_indicator_names": indicator_names,
                            "imputation_provenance_sha256": _sha256_bytes(
                                _canonical_json(imputation_payload).encode()
                            ),
                            "scaling_provenance_sha256": scaling_hash,
                        }
                    )
        audit[mode] = rows
    return audit


def _fit_base_model(
    family: str,
    x_train: np.ndarray,
    y_train: np.ndarray,
    train_weights: np.ndarray,
    x_evaluation: np.ndarray,
    names: Sequence[str],
) -> np.ndarray:
    if len(np.unique(y_train)) != 2:
        raise DevelopmentRunError("base-model training partition lacks both classes")
    is_logistic = family == "logistic_elastic_net"
    x_train_p, x_eval_p, _ = _fold_preprocess(
        x_train,
        x_evaluation,
        train_weights,
        names,
        standardize=is_logistic,
    )
    if is_logistic:
        model: Any = LogisticRegression(
            C=1.0,
            l1_ratio=0.5,
            solver="saga",
            max_iter=500,
            tol=1e-4,
            class_weight=None,
            random_state=SEED,
        )
    elif family == "hist_gradient_boosting":
        model = HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_leaf_nodes=7,
            max_iter=10,
            min_samples_leaf=20,
            l2_regularization=1.0,
            early_stopping=False,
            class_weight=None,
            random_state=SEED,
        )
    else:
        raise DevelopmentRunError(f"unsupported fixed model family {family}")
    with warnings.catch_warnings(), threadpool_limits(limits=1):
        warnings.filterwarnings("error", category=ConvergenceWarning)
        try:
            model.fit(x_train_p, y_train, sample_weight=train_weights)
        except (TypeError, ConvergenceWarning) as error:
            raise DevelopmentRunError(f"weighted {family} fit failed closed: {error}") from error
    probabilities = np.asarray(model.predict_proba(x_eval_p)[:, 1], dtype=np.float64)
    if not np.all(np.isfinite(probabilities)):
        raise DevelopmentRunError(f"{family} produced nonfinite probabilities")
    return probabilities


def _fit_regression_model(
    family: str,
    x_train: np.ndarray,
    y_train: np.ndarray,
    train_weights: np.ndarray,
    x_evaluation: np.ndarray,
    names: Sequence[str],
) -> np.ndarray:
    """Fit one frozen F1 development candidate using outer-training data only."""
    targets = np.asarray(y_train, dtype=np.float64)
    weights = np.asarray(train_weights, dtype=np.float64)
    if (
        targets.ndim != 1
        or weights.ndim != 1
        or len(targets) != len(weights)
        or not np.all(np.isfinite(targets))
        or not np.all(np.isfinite(weights))
        or np.any(weights <= 0.0)
    ):
        raise DevelopmentRunError(
            "F1 regression fit received invalid targets or weights",
            error_code="invalid_input",
        )
    is_linear = family == "elastic_net_regression"
    x_train_p, x_eval_p, _ = _fold_preprocess(
        x_train,
        x_evaluation,
        weights,
        names,
        standardize=is_linear,
    )
    if is_linear:
        model: Any = ElasticNet(
            alpha=0.01,
            l1_ratio=0.1,
            fit_intercept=True,
            max_iter=250_000,
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
        raise DevelopmentRunError(f"unsupported fixed F1 model family {family}")
    with warnings.catch_warnings(), threadpool_limits(limits=1):
        warnings.filterwarnings("error", category=ConvergenceWarning)
        try:
            model.fit(x_train_p, targets, sample_weight=weights)
        except ConvergenceWarning as error:
            raise DevelopmentRunError(
                "weighted regression failed to converge",
                error_code="convergence_failed",
            ) from error
        except TypeError as error:
            raise DevelopmentRunError(
                "weighted regression rejected the frozen fit contract",
                error_code="invalid_input",
            ) from error
    predictions = np.asarray(model.predict(x_eval_p), dtype=np.float64)
    if predictions.ndim != 1 or not np.all(np.isfinite(predictions)):
        raise DevelopmentRunError(
            f"{family} produced invalid F1 predictions",
            error_code="invalid_predictions",
        )
    return predictions


def _f1_baseline_prediction(snapshot: DevelopmentSnapshot, family: str) -> float:
    if snapshot.soh_loss_pp_h3 is None:
        raise DevelopmentRunError("F1 baseline received a row without an observed H3 target")
    if family == "persistence_zero_loss":
        return 0.0
    if family == "recent_slope_h3":
        if len(snapshot.b_values) != len(B_FEATURE_NAMES):
            raise DevelopmentRunError("recent-slope baseline lacks frozen B features")
        loss_last_interval = float(snapshot.b_values[3])
        if not math.isfinite(loss_last_interval):
            raise DevelopmentRunError("recent-slope baseline received nonfinite recent loss")
        return 3.0 * loss_last_interval
    raise DevelopmentRunError(f"unknown F1 baseline {family}")


def condition_balanced_regression_metrics(
    snapshots: Sequence[DevelopmentSnapshot], predictions: np.ndarray
) -> dict[str, object]:
    """Return the frozen hierarchical-weight F1 metric packet."""
    targets = np.asarray([snapshot.soh_loss_pp_h3 for snapshot in snapshots], dtype=np.float64)
    predicted = np.asarray(predictions, dtype=np.float64)
    weights = hierarchical_weights(snapshots)
    if (
        targets.ndim != 1
        or predicted.ndim != 1
        or len(targets) != len(predicted)
        or len(targets) == 0
        or not np.all(np.isfinite(targets))
        or not np.all(np.isfinite(predicted))
    ):
        raise DevelopmentRunError("F1 metric vectors must be aligned, non-empty and finite")
    errors = predicted - targets
    absolute_errors = np.abs(errors)
    squared_errors = errors**2
    r2_value: float | None
    if len(targets) < 2 or np.allclose(targets, targets[0]):
        r2_value = None
    else:
        candidate_r2 = float(r2_score(targets, predicted, sample_weight=weights))
        r2_value = candidate_r2 if math.isfinite(candidate_r2) else None
    return {
        "metric_id": "condition_balanced_mae_pp_v1",
        "condition_balanced_mae_pp_v1": float(np.average(absolute_errors, weights=weights)),
        "condition_balanced_median_ae_pp_v1": _weighted_median(absolute_errors, weights),
        "condition_balanced_rmse_pp_v1": float(
            math.sqrt(float(np.average(squared_errors, weights=weights)))
        ),
        "condition_balanced_signed_bias_pp_v1": float(np.average(errors, weights=weights)),
        "condition_balanced_r2_v1": r2_value,
        "r2_role": "secondary_only",
        "snapshots": len(snapshots),
        "cells": len({snapshot.cell_id for snapshot in snapshots}),
        "conditions": len({snapshot.condition_id for snapshot in snapshots}),
        "negative_loss_snapshots": int(np.sum(targets < 0.0)),
    }


def paired_condition_regression_bootstrap(
    snapshots: Sequence[DevelopmentSnapshot],
    b_predictions: np.ndarray,
    d_predictions: np.ndarray,
    *,
    draws: int = 2_000,
    seed: int = SEED,
    minimum_valid_draws: int = 1_900,
) -> dict[str, object]:
    """Bootstrap intact conditions and recompute paired F1 deltas."""
    b_values = np.asarray(b_predictions, dtype=np.float64)
    d_values = np.asarray(d_predictions, dtype=np.float64)
    if draws != 2_000 or minimum_valid_draws != 1_900:
        raise DevelopmentRunError("F1 paired bootstrap packet drifted from 2000/1900")
    if (
        not snapshots
        or b_values.ndim != 1
        or d_values.ndim != 1
        or len(b_values) != len(snapshots)
        or len(d_values) != len(snapshots)
    ):
        raise DevelopmentRunError("F1 paired bootstrap vectors are not aligned")
    if not np.all(np.isfinite(b_values)) or not np.all(np.isfinite(d_values)):
        raise DevelopmentRunError("F1 paired bootstrap predictions must be finite")
    condition_indices: dict[int, list[int]] = defaultdict(list)
    for index, snapshot in enumerate(snapshots):
        condition_indices[snapshot.condition_id].append(index)
    conditions = sorted(condition_indices)
    rng = np.random.default_rng(seed)
    mae_deltas: list[float] = []
    rmse_deltas: list[float] = []
    for _ in range(draws):
        sampled_conditions = rng.choice(conditions, size=len(conditions), replace=True)
        sampled_indices: list[int] = []
        sampled_snapshots: list[DevelopmentSnapshot] = []
        for draw_slot, condition in enumerate(sampled_conditions):
            for index in condition_indices[int(condition)]:
                sampled_indices.append(index)
                snapshot = snapshots[index]
                sampled_snapshots.append(
                    replace(
                        snapshot,
                        condition_id=draw_slot,
                        cell_id=f"bootstrap-{draw_slot}:{snapshot.cell_id}",
                    )
                )
        b_metrics = condition_balanced_regression_metrics(
            sampled_snapshots, b_values[sampled_indices]
        )
        d_metrics = condition_balanced_regression_metrics(
            sampled_snapshots, d_values[sampled_indices]
        )
        mae_deltas.append(
            _finite_float(d_metrics["condition_balanced_mae_pp_v1"], name="bootstrap D MAE")
            - _finite_float(b_metrics["condition_balanced_mae_pp_v1"], name="bootstrap B MAE")
        )
        rmse_deltas.append(
            _finite_float(d_metrics["condition_balanced_rmse_pp_v1"], name="bootstrap D RMSE")
            - _finite_float(b_metrics["condition_balanced_rmse_pp_v1"], name="bootstrap B RMSE")
        )
    valid = len(mae_deltas)
    if valid < minimum_valid_draws:
        raise DevelopmentRunError(
            f"F1 paired bootstrap has only {valid} valid draws; requires {minimum_valid_draws}"
        )
    return {
        "method": "whole_condition_paired_bootstrap_v1",
        "draws": draws,
        "valid_draws": valid,
        "minimum_valid_draws": minimum_valid_draws,
        "seed": seed,
        "interval_percent": 95,
        "interval_valid": True,
        "mae_delta_percentile_95": [
            float(value) for value in np.percentile(mae_deltas, [2.5, 97.5])
        ],
        "rmse_delta_percentile_95": [
            float(value) for value in np.percentile(rmse_deltas, [2.5, 97.5])
        ],
    }


def _sigmoid(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def fit_weighted_platt(
    probabilities: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    *,
    max_iter: int = 200,
    probability_clip: float = 1e-6,
) -> PlattParameters:
    """Fit a deterministic weighted two-parameter sigmoid by damped Newton updates."""
    probabilities, labels, weights = _validated_binary_arrays(
        probabilities, labels, weights, context="Platt calibration"
    )
    if not math.isfinite(probability_clip) or not 0.0 < probability_clip < 0.5:
        raise DevelopmentRunError("Platt probability clip must be finite and within (0, 0.5)")
    if max_iter < 1:
        raise DevelopmentRunError("Platt maximum iterations must be positive")
    clipped = np.clip(probabilities, probability_clip, 1.0 - probability_clip)
    score = np.log(clipped / (1.0 - clipped))
    design = np.column_stack([score, np.ones(len(score), dtype=np.float64)])
    parameters = np.asarray([1.0, 0.0], dtype=np.float64)
    total_weight = float(np.sum(weights))

    def objective(candidate: np.ndarray) -> float:
        linear = design @ candidate
        return float(np.sum(weights * (np.logaddexp(0.0, linear) - labels * linear)) / total_weight)

    for iteration in range(1, max_iter + 1):
        linear = design @ parameters
        fitted = _sigmoid(linear)
        loss = objective(parameters)
        gradient = design.T @ (weights * (fitted - labels)) / total_weight
        curvature = weights * fitted * (1.0 - fitted)
        hessian = (design.T * curvature) @ design / total_weight
        hessian += np.eye(2) * 1e-8
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError as error:
            raise DevelopmentRunError("Platt calibration Hessian is singular") from error
        scale = 1.0
        candidate: np.ndarray | None = None
        for _ in range(40):
            proposal = parameters - scale * step
            if np.all(np.isfinite(proposal)) and objective(proposal) <= loss + 1e-12:
                candidate = proposal
                break
            scale *= 0.5
        if candidate is None:
            raise DevelopmentRunError("Platt calibration damping could not reduce objective")
        accepted_step = candidate - parameters
        parameters = candidate
        if np.linalg.norm(accepted_step, ord=2) <= 1e-8:
            return PlattParameters(float(parameters[0]), float(parameters[1]), iteration)
    raise DevelopmentRunError("weighted Platt calibration did not converge")


def fit_weighted_inner_oof_platt(
    probabilities: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    *,
    prediction_snapshot_ids: Sequence[str],
    outer_training_snapshot_ids: Sequence[str],
    forbidden_outer_validation_snapshot_ids: Sequence[str],
    max_iter: int = 200,
    probability_clip: float = 1e-6,
) -> PlattParameters:
    """Fit Platt only when pooled inner-OOF identities exactly match outer training."""
    prediction_ids = tuple(prediction_snapshot_ids)
    training_ids = tuple(outer_training_snapshot_ids)
    forbidden_ids = tuple(forbidden_outer_validation_snapshot_ids)
    if not prediction_ids or len(prediction_ids) != len(set(prediction_ids)):
        raise DevelopmentRunError("inner OOF snapshot identities must be non-empty and unique")
    if len(training_ids) != len(set(training_ids)):
        raise DevelopmentRunError("outer-training snapshot identities must be unique")
    if prediction_ids != training_ids:
        raise DevelopmentRunError(
            "inner OOF predictions must align exactly to ordered outer-training snapshot identities"
        )
    if set(prediction_ids).intersection(forbidden_ids):
        raise DevelopmentRunError("outer-validation snapshot leaked into inner OOF calibration")
    return fit_weighted_platt(
        probabilities,
        labels,
        weights,
        max_iter=max_iter,
        probability_clip=probability_clip,
    )


def apply_platt(probabilities: np.ndarray, parameters: PlattParameters) -> np.ndarray:
    probabilities = np.asarray(probabilities, dtype=np.float64)
    if (
        probabilities.ndim != 1
        or not np.all(np.isfinite(probabilities))
        or np.any((probabilities < 0.0) | (probabilities > 1.0))
    ):
        raise DevelopmentRunError("probabilities to calibrate must be a finite [0, 1] vector")
    clipped = np.clip(probabilities, 1e-6, 1.0 - 1e-6)
    score = np.log(clipped / (1.0 - clipped))
    result = _sigmoid(parameters.slope * score + parameters.intercept)
    if not np.all(np.isfinite(result)):
        raise DevelopmentRunError("calibrated probabilities are nonfinite")
    return result


def condition_balanced_metrics(
    snapshots: Sequence[DevelopmentSnapshot], probabilities: np.ndarray
) -> dict[str, object]:
    labels = np.asarray([snapshot.o2_label for snapshot in snapshots], dtype=np.int64)
    weights = hierarchical_weights(snapshots)
    probabilities, labels, weights = _validated_binary_arrays(
        probabilities, labels, weights, context="pooled metric"
    )
    return {
        "metric_id": "condition_balanced_pooled_pr_auc_v1",
        "condition_balanced_pooled_pr_auc_v1": float(
            average_precision_score(labels, probabilities, sample_weight=weights)
        ),
        "condition_balanced_pooled_brier_v1": float(
            np.average((probabilities - labels) ** 2, weights=weights)
        ),
        "snapshots": len(snapshots),
        "events": int(np.sum(labels)),
        "non_events": int(len(labels) - np.sum(labels)),
        "cells": len({snapshot.cell_id for snapshot in snapshots}),
        "conditions": len({snapshot.condition_id for snapshot in snapshots}),
        "positive_supporting_conditions": len(
            {snapshot.condition_id for snapshot in snapshots if snapshot.o2_label == 1}
        ),
        "negative_supporting_conditions": len(
            {snapshot.condition_id for snapshot in snapshots if snapshot.o2_label == 0}
        ),
        "weighted_event_mass": float(np.sum(weights[labels == 1]) / np.sum(weights)),
    }


def paired_condition_bootstrap(
    snapshots: Sequence[DevelopmentSnapshot],
    b_probabilities: np.ndarray,
    d_probabilities: np.ndarray,
    *,
    draws: int = 2_000,
    seed: int = SEED,
    minimum_valid_draws: int = 1_900,
) -> dict[str, object]:
    """Bootstrap intact conditions with identical paired resamples."""
    if len(snapshots) != len(b_probabilities) or len(snapshots) != len(d_probabilities):
        raise DevelopmentRunError("paired bootstrap inputs are not aligned")
    if draws != 2_000 or seed != SEED or minimum_valid_draws != 1_900:
        raise DevelopmentRunError("paired bootstrap must use the frozen 2000/20260801/1900 packet")
    labels = np.asarray([snapshot.o2_label for snapshot in snapshots], dtype=np.int64)
    weights = hierarchical_weights(snapshots)
    b_probabilities, labels, _ = _validated_binary_arrays(
        b_probabilities, labels, weights, context="paired bootstrap B"
    )
    d_probabilities, d_labels, _ = _validated_binary_arrays(
        d_probabilities, labels, weights, context="paired bootstrap D_CORE"
    )
    if not np.array_equal(labels, d_labels):
        raise DevelopmentRunError("paired bootstrap labels drifted between feature views")
    condition_ids = sorted({snapshot.condition_id for snapshot in snapshots})
    index_by_condition: dict[int, np.ndarray] = {
        condition: np.asarray(
            [
                index
                for index, snapshot in enumerate(snapshots)
                if snapshot.condition_id == condition
            ]
        )
        for condition in condition_ids
    }
    payload_by_condition: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
    for condition, indices in index_by_condition.items():
        selected = [snapshots[int(index)] for index in indices]
        within = hierarchical_weights(selected)
        payload_by_condition[condition] = (
            np.asarray([item.o2_label for item in selected], dtype=np.int64),
            b_probabilities[indices],
            d_probabilities[indices],
            within / float(np.sum(within)),
        )
    rng = np.random.default_rng(seed)
    pr_deltas: list[float] = []
    brier_deltas: list[float] = []
    invalid = 0
    cached_draws: dict[tuple[int, ...], tuple[float, float] | None] = {}
    for _ in range(draws):
        sampled = rng.choice(np.asarray(condition_ids), size=len(condition_ids), replace=True)
        signature = tuple(sorted(int(condition) for condition in sampled))
        if signature in cached_draws:
            cached = cached_draws[signature]
            if cached is None:
                invalid += 1
            else:
                pr_deltas.append(cached[0])
                brier_deltas.append(cached[1])
            continue
        labels_parts: list[np.ndarray] = []
        b_parts: list[np.ndarray] = []
        d_parts: list[np.ndarray] = []
        weights_parts: list[np.ndarray] = []
        for condition in signature:
            condition_labels, condition_b, condition_d, within = payload_by_condition[condition]
            labels_parts.append(condition_labels)
            b_parts.append(condition_b)
            d_parts.append(condition_d)
            weights_parts.append(within / len(sampled))
        labels = np.concatenate(labels_parts)
        if len(np.unique(labels)) != 2:
            cached_draws[signature] = None
            invalid += 1
            continue
        b_values = np.concatenate(b_parts)
        d_values = np.concatenate(d_parts)
        weights = np.concatenate(weights_parts)
        pr_delta = float(average_precision_score(labels, d_values, sample_weight=weights)) - float(
            average_precision_score(labels, b_values, sample_weight=weights)
        )
        brier_delta = float(np.average((d_values - labels) ** 2, weights=weights)) - float(
            np.average((b_values - labels) ** 2, weights=weights)
        )
        cached_draws[signature] = (pr_delta, brier_delta)
        pr_deltas.append(pr_delta)
        brier_deltas.append(brier_delta)
    interval_valid = len(pr_deltas) >= minimum_valid_draws
    return {
        "draws": draws,
        "seed": seed,
        "valid_draws": len(pr_deltas),
        "invalid_draws": invalid,
        "minimum_valid_draws": minimum_valid_draws,
        "interval_valid": interval_valid,
        "pr_auc_delta_percentile_95": (
            [float(value) for value in np.percentile(pr_deltas, [2.5, 97.5])]
            if interval_valid
            else None
        ),
        "brier_delta_percentile_95": (
            [float(value) for value in np.percentile(brier_deltas, [2.5, 97.5])]
            if interval_valid
            else None
        ),
    }


def retry_sensitivity_stop_decision(
    *,
    primary_pr_auc_delta: float,
    keep_last_pr_auc_delta: float,
    primary_brier_delta: float,
    keep_last_brier_delta: float,
) -> dict[str, object]:
    """Apply the frozen diagnostic stop margins; this is never a promotion decision."""
    values = {
        "primary_pr_auc_delta": primary_pr_auc_delta,
        "keep_last_pr_auc_delta": keep_last_pr_auc_delta,
        "primary_brier_delta": primary_brier_delta,
        "keep_last_brier_delta": keep_last_brier_delta,
    }
    checked = {name: _finite_float(value, name=name) for name, value in values.items()}
    sign_reversal = checked["primary_pr_auc_delta"] * checked["keep_last_pr_auc_delta"] < 0.0
    pr_change = abs(checked["keep_last_pr_auc_delta"] - checked["primary_pr_auc_delta"])
    brier_change = abs(checked["keep_last_brier_delta"] - checked["primary_brier_delta"])
    return {
        "pr_auc_delta_sign_reversal": sign_reversal,
        "pr_auc_delta_absolute_change": pr_change,
        "brier_delta_absolute_change": brier_change,
        "stop_margins": {
            "pr_auc_delta_absolute_change": 0.02,
            "brier_delta_absolute_change": 0.01,
            "stop_on_sign_reversal": True,
        },
        "prohibit_eis_increment_conclusion": (
            sign_reversal or pr_change > 0.02 or brier_change > 0.01
        ),
        "decision_role": "diagnostic_stop_only_never_promotion",
    }


_FORBIDDEN_SELECTION_KEYS = frozenset(
    {
        "champion",
        "winner",
        "selected_model",
        "promoted_model",
        "model_ranking",
        "promotion_decision",
    }
)


def assert_no_champion_fields(value: object, *, path: str = "$") -> None:
    """Reject winner-selection fields recursively in development robustness payloads."""
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower()
            if key in _FORBIDDEN_SELECTION_KEYS or "champion" in key or "winner" in key:
                raise DevelopmentRunError(f"prohibited selection field at {path}.{raw_key}")
            assert_no_champion_fields(nested, path=f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_no_champion_fields(nested, path=f"{path}[{index}]")


def _progress_fit(
    progress: Callable[[dict[str, object]], None],
    *,
    stage: str,
    completed: int,
    planned: int,
    detail: Mapping[str, object],
) -> None:
    progress(
        {
            "stage": stage,
            "completed_fits": completed,
            "planned_fits": planned,
            **detail,
        }
    )


def _partition_weight_payload(
    cohort: Sequence[DevelopmentSnapshot], indices: Sequence[int]
) -> list[dict[str, object]]:
    selected = [cohort[int(index)] for index in indices]
    weights = hierarchical_weights(selected)
    return [
        {"snapshot_id": snapshot.snapshot_id, "weight_mean_one": float(weight)}
        for snapshot, weight in zip(selected, weights, strict=True)
    ]


def _paired_design_payload(
    cohort: Sequence[DevelopmentSnapshot], folds: Sequence[FoldPlan]
) -> dict[str, object]:
    cohort_indices = tuple(range(len(cohort)))
    outer_payload: list[dict[str, object]] = []
    for fold in folds:
        outer_payload.append(
            {
                "outer_fold": fold.outer_fold,
                "training_weights": _partition_weight_payload(cohort, fold.train_indices),
                "validation_weights": _partition_weight_payload(cohort, fold.validation_indices),
                "inner_folds": [
                    {
                        "inner_fold": inner_fold,
                        "training_weights": _partition_weight_payload(cohort, train),
                        "validation_weights": _partition_weight_payload(cohort, validation),
                    }
                    for inner_fold, (train, validation) in enumerate(fold.inner_folds)
                ],
            }
        )
    payload: dict[str, object] = {
        "fold_assignment_task_independent": True,
        "applies_to_tasks": [F1_TASK_ID, O2_TASK_ID],
        "b_cohort_snapshot_ids": [item.snapshot_id for item in cohort],
        "d_core_eligible_snapshot_ids": [
            item.snapshot_id for item in cohort if _eligible_for_d_core(item)
        ],
        "paired_comparison_subset": "d_core_eligible_intersection_only",
        "applies_identically_to_acquisition_policies": [
            "primary",
            "keep_last_sensitivity",
        ],
        "ordered_cohort_snapshot_ids": [item.snapshot_id for item in cohort],
        "metric_weights": _partition_weight_payload(cohort, cohort_indices),
        "folds": outer_payload,
    }
    payload["paired_design_sha256"] = _sha256_bytes(_canonical_json(payload).encode("utf-8"))
    return payload


def paired_cohort_identity_sha256(
    snapshots: Sequence[DevelopmentSnapshot],
) -> str:
    """Hash the exact ordered B/D paired cohort and its current/H3 target lineage."""
    rows = [
        {
            "snapshot_id": snapshot.snapshot_id,
            "cell_id": snapshot.cell_id,
            "condition_id": snapshot.condition_id,
            "mode": snapshot.mode,
            "checkup_index": snapshot.checkup_index,
            "timestamp_s_hex": float(snapshot.timestamp_s).hex(),
            "h3_endpoint_snapshot_id": snapshot.h3_endpoint_snapshot_id,
            "h3_checkup_index": snapshot.h3_checkup_index,
            "soh_percent_hex": float(snapshot.soh_percent).hex(),
            "future_soh_percent_hex": float(cast("float", snapshot.future_soh_percent)).hex(),
        }
        for snapshot in sorted(
            (item for item in snapshots if item.eligibility_status == "eligible_f1_and_d_core"),
            key=lambda item: (item.mode, item.snapshot_id),
        )
    ]
    return _sha256_bytes(_canonical_json(rows).encode("utf-8"))


def planned_trial_keys_sha256() -> str:
    """Hash the exact 54 fixed F1 fit identities before any model is fit."""
    rows = [
        {
            "task_id": F1_TASK_ID,
            "mode": mode,
            "outer_fold": outer_fold,
            "acquisition_policy": acquisition_policy,
            "view": view,
            "model_family": model_family,
        }
        for mode in ("calendar", "cyclic", "profile")
        for outer_fold in range(3)
        for acquisition_policy, view in (
            ("primary", "B"),
            ("primary", "D_CORE"),
            ("keep_last_sensitivity", "D_CORE"),
        )
        for model_family in (
            "elastic_net_regression",
            "hist_gradient_boosting_regressor",
        )
    ]
    return _sha256_bytes(_canonical_json(rows).encode("utf-8"))


def _f1_prediction_row(
    snapshot: DevelopmentSnapshot,
    *,
    acquisition_policy: str,
    view: str,
    model_role: str,
    model_family: str,
    fit_id: str | None,
    paired_design_sha256: str,
    outer_fold: int,
    weight: float,
    predicted_loss: float,
) -> dict[str, object]:
    if snapshot.soh_loss_pp_h3 is None or snapshot.future_soh_percent is None:
        raise DevelopmentRunError("F1 prediction row requires exact observed H3 truth")
    predicted = _finite_float(predicted_loss, name="predicted_soh_loss_pp_h3")
    observed = _finite_float(snapshot.soh_loss_pp_h3, name="observed_soh_loss_pp_h3")
    current = _finite_float(snapshot.soh_percent, name="current_soh_percent")
    observed_future = _finite_float(snapshot.future_soh_percent, name="observed_future_soh_percent")
    if not math.isclose(current - observed_future, observed, rel_tol=0.0, abs_tol=1e-9):
        raise DevelopmentRunError("F1 current/future/observed-loss invariant failed")
    return {
        **FALSE_RESULT_FLAGS,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "task_id": F1_TASK_ID,
        "target_kind": "continuous_regression_primary",
        "target_name": F1_TARGET_NAME,
        "snapshot_id": snapshot.snapshot_id,
        "cell_id": snapshot.cell_id,
        "condition_id": snapshot.condition_id,
        "mode": snapshot.mode,
        "acquisition_policy": acquisition_policy,
        "view": view,
        "model_role": model_role,
        "model_family": model_family,
        "fit_id": fit_id,
        "paired_design_sha256": paired_design_sha256,
        "outer_fold": outer_fold,
        "outer_validation_weight_mean_one": _finite_float(weight, name="OOF weight"),
        "observed_soh_loss_pp_h3": observed,
        "predicted_soh_loss_pp_h3": predicted,
        "current_soh_percent": current,
        "observed_future_soh_percent": observed_future,
        "predicted_future_soh_percent": current - predicted,
        "prediction_interval_low_pp": None,
        "prediction_interval_high_pp": None,
        "prediction_interval_status": "unavailable_not_frozen",
    }


def _f1_metric_row(
    cohort: Sequence[DevelopmentSnapshot],
    predictions: np.ndarray,
    *,
    mode: str,
    acquisition_policy: str,
    view: str,
    model_role: str,
    model_family: str,
    paired_design_sha256: str,
) -> dict[str, object]:
    return {
        **FALSE_RESULT_FLAGS,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "task_id": F1_TASK_ID,
        "target_kind": "continuous_regression_primary",
        "target_name": F1_TARGET_NAME,
        "mode": mode,
        "acquisition_policy": acquisition_policy,
        "view": view,
        "model_role": model_role,
        "model_family": model_family,
        "paired_design_sha256": paired_design_sha256,
        "metrics": condition_balanced_regression_metrics(cohort, predictions),
    }


def _run_f1_oof_workflows(
    snapshots: Sequence[DevelopmentSnapshot],
    folds_by_mode: Mapping[str, Sequence[FoldPlan]],
    paired_design_by_mode: Mapping[str, dict[str, object]],
    progress: Callable[[dict[str, object]], None],
) -> tuple[list[dict[str, object]], list[dict[str, object]], int, int]:
    """Run fixed F1 baselines/candidates only; O2 remains zero-fit and zero-output."""
    workflows = (
        ("primary", "B"),
        ("primary", "D_CORE"),
        ("keep_last_sensitivity", "D_CORE"),
    )
    fitted_families = ("elastic_net_regression", "hist_gradient_boosting_regressor")
    baseline_families = ("persistence_zero_loss", "recent_slope_h3")
    workflow_state: dict[
        tuple[str, str, str],
        tuple[list[DevelopmentSnapshot], list[tuple[FoldPlan, list[int], list[int]]]],
    ] = {}
    planned = 0
    for mode in sorted(folds_by_mode):
        base_cohort = [
            snapshot
            for snapshot in snapshots
            if snapshot.mode == mode and _eligible_for_f1_b(snapshot)
        ]
        for acquisition_policy, view in workflows:
            cohort = (
                base_cohort
                if view == "B"
                else [snapshot for snapshot in base_cohort if _eligible_for_d_core(snapshot)]
            )
            index_by_id = {snapshot.snapshot_id: index for index, snapshot in enumerate(cohort)}
            active_folds: list[tuple[FoldPlan, list[int], list[int]]] = []
            for fold in folds_by_mode[mode]:
                train_ids = {base_cohort[index].snapshot_id for index in fold.train_indices}
                validation_ids = {
                    base_cohort[index].snapshot_id for index in fold.validation_indices
                }
                train_indices = [
                    index_by_id[snapshot_id]
                    for snapshot_id in train_ids
                    if snapshot_id in index_by_id
                ]
                validation_indices = [
                    index_by_id[snapshot_id]
                    for snapshot_id in validation_ids
                    if snapshot_id in index_by_id
                ]
                if not validation_indices:
                    continue
                if not train_indices:
                    raise DevelopmentRunError(
                        f"{mode} {view} fold {fold.outer_fold} has validation rows but no training rows"
                    )
                active_folds.append((fold, sorted(train_indices), sorted(validation_indices)))
            if not cohort or not active_folds:
                raise DevelopmentRunError(f"{mode} {view} has no evaluable F1 cohort")
            workflow_state[(mode, acquisition_policy, view)] = (cohort, active_folds)
            planned += len(active_folds) * len(fitted_families)
    completed = 0
    rows: list[dict[str, object]] = []
    metrics: list[dict[str, object]] = []
    for mode in sorted(folds_by_mode):
        base_cohort = [
            snapshot
            for snapshot in snapshots
            if snapshot.mode == mode and _eligible_for_f1_b(snapshot)
        ]
        design_hash = str(paired_design_by_mode[mode]["paired_design_sha256"])
        fold_by_index: dict[int, tuple[int, float]] = {}
        for fold in folds_by_mode[mode]:
            validation = [base_cohort[index] for index in fold.validation_indices]
            weights = hierarchical_weights(validation)
            for index, weight in zip(fold.validation_indices, weights, strict=True):
                if index in fold_by_index:
                    raise DevelopmentRunError("F1 outer validation membership is not unique")
                fold_by_index[index] = (fold.outer_fold, float(weight))
        if set(fold_by_index) != set(range(len(base_cohort))):
            raise DevelopmentRunError("F1 outer folds do not cover the exact cohort")
        for family in baseline_families:
            values = np.asarray(
                [_f1_baseline_prediction(snapshot, family) for snapshot in base_cohort],
                dtype=np.float64,
            )
            for index, (snapshot, predicted) in enumerate(zip(base_cohort, values, strict=True)):
                outer_fold, weight = fold_by_index[index]
                rows.append(
                    _f1_prediction_row(
                        snapshot,
                        acquisition_policy="primary",
                        view="B",
                        model_role="baseline",
                        model_family=family,
                        fit_id=None,
                        paired_design_sha256=design_hash,
                        outer_fold=outer_fold,
                        weight=weight,
                        predicted_loss=float(predicted),
                    )
                )
            metrics.append(
                _f1_metric_row(
                    base_cohort,
                    values,
                    mode=mode,
                    acquisition_policy="primary",
                    view="B",
                    model_role="baseline",
                    model_family=family,
                    paired_design_sha256=design_hash,
                )
            )
        for acquisition_policy, view in workflows:
            cohort, active_folds = workflow_state[(mode, acquisition_policy, view)]
            x, y, _, names = _matrix(cohort, view, acquisition_policy)
            for family in fitted_families:
                oof = np.full(len(cohort), np.nan, dtype=np.float64)
                for fold, train_indices, validation_indices in active_folds:
                    fit_id = (
                        f"{F1_TASK_ID}:{mode}:{acquisition_policy}:{view}:"
                        f"{family}:outer-{fold.outer_fold}"
                    )
                    detail = {
                        "task_id": F1_TASK_ID,
                        "mode": mode,
                        "acquisition_policy": acquisition_policy,
                        "view": view,
                        "model_family": family,
                        "outer_fold": fold.outer_fold,
                        "fit_kind": "outer_regression",
                        "fit_id": fit_id,
                    }
                    _progress_fit(
                        progress,
                        stage="fit_start",
                        completed=completed,
                        planned=planned,
                        detail=detail,
                    )
                    train_snapshots = [cohort[index] for index in train_indices]
                    fold_predictions = _fit_regression_model(
                        family,
                        x[train_indices],
                        y[train_indices],
                        hierarchical_weights(train_snapshots),
                        x[validation_indices],
                        names,
                    )
                    completed += 1
                    _progress_fit(
                        progress,
                        stage="fit_complete",
                        completed=completed,
                        planned=planned,
                        detail=detail,
                    )
                    oof[validation_indices] = fold_predictions
                    validation = [cohort[index] for index in validation_indices]
                    weights = hierarchical_weights(validation)
                    for index, value, weight in zip(
                        validation_indices, fold_predictions, weights, strict=True
                    ):
                        rows.append(
                            _f1_prediction_row(
                                cohort[index],
                                acquisition_policy=acquisition_policy,
                                view=view,
                                model_role="fitted_candidate",
                                model_family=family,
                                fit_id=fit_id,
                                paired_design_sha256=design_hash,
                                outer_fold=fold.outer_fold,
                                weight=float(weight),
                                predicted_loss=float(value),
                            )
                        )
                if np.any(~np.isfinite(oof)):
                    raise DevelopmentRunError("F1 outer OOF prediction vector is incomplete")
                metrics.append(
                    _f1_metric_row(
                        cohort,
                        oof,
                        mode=mode,
                        acquisition_policy=acquisition_policy,
                        view=view,
                        model_role="fitted_candidate",
                        model_family=family,
                        paired_design_sha256=design_hash,
                    )
                )
    if completed != planned:
        raise DevelopmentRunError(f"F1 fit accounting mismatch: {completed} != {planned}")
    return rows, metrics, planned, completed


def _legacy_v1_run_oof_workflows(
    snapshots: Sequence[DevelopmentSnapshot],
    folds_by_mode: dict[str, list[FoldPlan]],
    paired_design_by_mode: Mapping[str, dict[str, object]],
    plan: AnalysisPlan,
    progress: Callable[[dict[str, object]], None],
) -> tuple[list[dict[str, object]], list[dict[str, object]], int, int]:
    workflows = (
        ("primary", "B"),
        ("primary", "D_CORE"),
        ("keep_last_sensitivity", "D_CORE"),
    )
    families = ("logistic_elastic_net", "hist_gradient_boosting")
    """Isolated superseded O2-v1 helper; never called by the v2 engine."""
    planned = sum(
        (len(fold.inner_folds) + 2)
        for mode_folds in folds_by_mode.values()
        for fold in mode_folds
        for _workflow in workflows
        for _family in families
    )
    completed = 0
    predictions: list[dict[str, object]] = []
    metrics: list[dict[str, object]] = []
    for mode in ("cyclic", "profile"):
        cohort = [snapshot for snapshot in snapshots if snapshot.mode == mode]
        paired_design_sha256 = str(paired_design_by_mode[mode]["paired_design_sha256"])
        for acquisition_policy, view in workflows:
            x, y, _, names = _matrix(cohort, view, acquisition_policy)
            for family in families:
                oof = np.full(len(cohort), np.nan, dtype=np.float64)
                raw_oof = np.full(len(cohort), np.nan, dtype=np.float64)
                for fold in folds_by_mode[mode]:
                    inner_raw = np.full(len(fold.train_indices), np.nan, dtype=np.float64)
                    outer_position = {
                        index: position for position, index in enumerate(fold.train_indices)
                    }
                    for inner_fold, (inner_train, inner_validation) in enumerate(fold.inner_folds):
                        detail = {
                            "mode": mode,
                            "acquisition_policy": acquisition_policy,
                            "view": view,
                            "model_family": family,
                            "outer_fold": fold.outer_fold,
                            "inner_fold": inner_fold,
                            "fit_kind": "inner_base",
                        }
                        _progress_fit(
                            progress,
                            stage="fit_start",
                            completed=completed,
                            planned=planned,
                            detail=detail,
                        )
                        train_snapshots = [cohort[index] for index in inner_train]
                        probability = _fit_base_model(
                            family,
                            x[list(inner_train)],
                            y[list(inner_train)],
                            hierarchical_weights(train_snapshots),
                            x[list(inner_validation)],
                            names,
                        )
                        for global_index, value in zip(inner_validation, probability, strict=True):
                            inner_raw[outer_position[global_index]] = value
                        completed += 1
                        _progress_fit(
                            progress,
                            stage="fit_complete",
                            completed=completed,
                            planned=planned,
                            detail=detail,
                        )
                    if np.any(~np.isfinite(inner_raw)):
                        raise DevelopmentRunError("inner OOF calibration vector is incomplete")
                    outer_train_snapshots = [cohort[index] for index in fold.train_indices]
                    calibration_detail = {
                        "mode": mode,
                        "acquisition_policy": acquisition_policy,
                        "view": view,
                        "model_family": family,
                        "outer_fold": fold.outer_fold,
                        "fit_kind": "weighted_platt",
                    }
                    _progress_fit(
                        progress,
                        stage="fit_start",
                        completed=completed,
                        planned=planned,
                        detail=calibration_detail,
                    )
                    outer_training_ids = tuple(
                        cohort[index].snapshot_id for index in fold.train_indices
                    )
                    outer_validation_ids = tuple(
                        cohort[index].snapshot_id for index in fold.validation_indices
                    )
                    calibration = fit_weighted_inner_oof_platt(
                        inner_raw,
                        y[list(fold.train_indices)],
                        hierarchical_weights(outer_train_snapshots),
                        prediction_snapshot_ids=outer_training_ids,
                        outer_training_snapshot_ids=outer_training_ids,
                        forbidden_outer_validation_snapshot_ids=outer_validation_ids,
                    )
                    completed += 1
                    _progress_fit(
                        progress,
                        stage="fit_complete",
                        completed=completed,
                        planned=planned,
                        detail=calibration_detail,
                    )
                    final_detail = {**calibration_detail, "fit_kind": "outer_base"}
                    _progress_fit(
                        progress,
                        stage="fit_start",
                        completed=completed,
                        planned=planned,
                        detail=final_detail,
                    )
                    raw_probability = _fit_base_model(
                        family,
                        x[list(fold.train_indices)],
                        y[list(fold.train_indices)],
                        hierarchical_weights(outer_train_snapshots),
                        x[list(fold.validation_indices)],
                        names,
                    )
                    completed += 1
                    _progress_fit(
                        progress,
                        stage="fit_complete",
                        completed=completed,
                        planned=planned,
                        detail=final_detail,
                    )
                    calibrated = apply_platt(raw_probability, calibration)
                    raw_oof[list(fold.validation_indices)] = raw_probability
                    oof[list(fold.validation_indices)] = calibrated
                    eval_snapshots = [cohort[index] for index in fold.validation_indices]
                    eval_weights = hierarchical_weights(eval_snapshots)
                    for index, raw_value, calibrated_value, weight in zip(
                        fold.validation_indices,
                        raw_probability,
                        calibrated,
                        eval_weights,
                        strict=True,
                    ):
                        snapshot = cohort[index]
                        predictions.append(
                            {
                                **FALSE_RESULT_FLAGS,
                                "snapshot_id": snapshot.snapshot_id,
                                "cell_id": snapshot.cell_id,
                                "condition_id": snapshot.condition_id,
                                "mode": mode,
                                "acquisition_policy": acquisition_policy,
                                "view": view,
                                "model_family": family,
                                "paired_design_sha256": paired_design_sha256,
                                "outer_fold": fold.outer_fold,
                                "label": snapshot.o2_label,
                                "raw_probability": float(raw_value),
                                "calibrated_probability": float(calibrated_value),
                                "outer_validation_weight_mean_one": float(weight),
                                "platt": asdict(calibration),
                            }
                        )
                if np.any(~np.isfinite(oof)) or np.any(~np.isfinite(raw_oof)):
                    raise DevelopmentRunError("outer OOF prediction vector is incomplete")
                metrics.append(
                    {
                        **FALSE_RESULT_FLAGS,
                        "mode": mode,
                        "acquisition_policy": acquisition_policy,
                        "view": view,
                        "model_family": family,
                        "paired_design_sha256": paired_design_sha256,
                        "calibrated": condition_balanced_metrics(cohort, oof),
                        "raw": condition_balanced_metrics(cohort, raw_oof),
                    }
                )
    if completed != planned:
        raise DevelopmentRunError(f"fit accounting mismatch: {completed} != {planned}")
    return predictions, metrics, planned, completed


def _legacy_v1_prediction_vector(
    predictions: Sequence[dict[str, object]],
    snapshots: Sequence[DevelopmentSnapshot],
    *,
    mode: str,
    acquisition_policy: str,
    view: str,
    model_family: str,
) -> np.ndarray:
    """Isolated superseded O2-v1 helper; never called by the v2 engine."""
    lookup = {
        str(row["snapshot_id"]): _finite_float(
            row["calibrated_probability"], name="calibrated_probability"
        )
        for row in predictions
        if row["mode"] == mode
        and row["acquisition_policy"] == acquisition_policy
        and row["view"] == view
        and row["model_family"] == model_family
    }
    cohort = [snapshot for snapshot in snapshots if snapshot.mode == mode]
    if set(lookup) != {snapshot.snapshot_id for snapshot in cohort}:
        raise DevelopmentRunError("paired prediction cohort mismatch")
    return np.asarray([lookup[snapshot.snapshot_id] for snapshot in cohort], dtype=np.float64)


def _f1_prediction_vector(
    predictions: Sequence[dict[str, object]],
    cohort: Sequence[DevelopmentSnapshot],
    *,
    acquisition_policy: str,
    view: str,
    model_family: str,
) -> np.ndarray:
    expected = {snapshot.snapshot_id for snapshot in cohort}
    lookup = {
        str(row["snapshot_id"]): _finite_float(
            row["predicted_soh_loss_pp_h3"], name="predicted_soh_loss_pp_h3"
        )
        for row in predictions
        if row.get("task_id") == F1_TASK_ID
        and row["acquisition_policy"] == acquisition_policy
        and row["view"] == view
        and row["model_family"] == model_family
        and str(row["snapshot_id"]) in expected
    }
    if set(lookup) != expected:
        raise DevelopmentRunError("F1 paired prediction cohort mismatch")
    return np.asarray([lookup[snapshot.snapshot_id] for snapshot in cohort], dtype=np.float64)


def _f1_paired_outputs(
    snapshots: Sequence[DevelopmentSnapshot],
    predictions: Sequence[dict[str, object]],
    plan: AnalysisPlan,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    paired: list[dict[str, object]] = []
    sensitivity: list[dict[str, object]] = []
    for mode in sorted({snapshot.mode for snapshot in snapshots}):
        cohort = [
            snapshot
            for snapshot in snapshots
            if snapshot.mode == mode and snapshot.eligibility_status == "eligible_f1_and_d_core"
        ]
        if not cohort:
            continue
        for family in ("elastic_net_regression", "hist_gradient_boosting_regressor"):
            b = _f1_prediction_vector(
                predictions,
                cohort,
                acquisition_policy="primary",
                view="B",
                model_family=family,
            )
            policy_results: dict[str, dict[str, object]] = {}
            for policy in ("primary", "keep_last_sensitivity"):
                d_core = _f1_prediction_vector(
                    predictions,
                    cohort,
                    acquisition_policy=policy,
                    view="D_CORE",
                    model_family=family,
                )
                b_metrics = condition_balanced_regression_metrics(cohort, b)
                d_metrics = condition_balanced_regression_metrics(cohort, d_core)
                bootstrap = paired_condition_regression_bootstrap(
                    cohort,
                    b,
                    d_core,
                    draws=plan.bootstrap_draws,
                    seed=plan.bootstrap_seed,
                    minimum_valid_draws=plan.minimum_valid_bootstrap_draws,
                )
                result: dict[str, object] = {
                    **FALSE_RESULT_FLAGS,
                    "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
                    "task_id": F1_TASK_ID,
                    "target_kind": "continuous_regression_primary",
                    "target_name": F1_TARGET_NAME,
                    "mode": mode,
                    "model_family": family,
                    "acquisition_policy": policy,
                    "same_snapshot_ids_and_weights": True,
                    "mae_delta_d_core_minus_b_pp": _finite_float(
                        d_metrics["condition_balanced_mae_pp_v1"], name="D_CORE MAE"
                    )
                    - _finite_float(b_metrics["condition_balanced_mae_pp_v1"], name="B MAE"),
                    "median_ae_delta_d_core_minus_b_pp": _finite_float(
                        d_metrics["condition_balanced_median_ae_pp_v1"],
                        name="D_CORE median AE",
                    )
                    - _finite_float(
                        b_metrics["condition_balanced_median_ae_pp_v1"],
                        name="B median AE",
                    ),
                    "rmse_delta_d_core_minus_b_pp": _finite_float(
                        d_metrics["condition_balanced_rmse_pp_v1"], name="D_CORE RMSE"
                    )
                    - _finite_float(b_metrics["condition_balanced_rmse_pp_v1"], name="B RMSE"),
                    "signed_bias_delta_d_core_minus_b_pp": _finite_float(
                        d_metrics["condition_balanced_signed_bias_pp_v1"],
                        name="D_CORE signed bias",
                    )
                    - _finite_float(
                        b_metrics["condition_balanced_signed_bias_pp_v1"],
                        name="B signed bias",
                    ),
                    "r2_delta_d_core_minus_b": (
                        None
                        if d_metrics["condition_balanced_r2_v1"] is None
                        or b_metrics["condition_balanced_r2_v1"] is None
                        else _finite_float(d_metrics["condition_balanced_r2_v1"], name="D_CORE R2")
                        - _finite_float(b_metrics["condition_balanced_r2_v1"], name="B R2")
                    ),
                    "bootstrap": bootstrap,
                    "comparison_role": "paired_diagnostic_only_never_promotion",
                }
                paired.append(result)
                policy_results[policy] = result
            primary_delta = _finite_float(
                policy_results["primary"]["mae_delta_d_core_minus_b_pp"],
                name="primary MAE delta",
            )
            keep_last_delta = _finite_float(
                policy_results["keep_last_sensitivity"]["mae_delta_d_core_minus_b_pp"],
                name="keep-last MAE delta",
            )
            sign_reversal = primary_delta * keep_last_delta < 0.0
            sensitivity.append(
                {
                    **FALSE_RESULT_FLAGS,
                    "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
                    "task_id": F1_TASK_ID,
                    "target_kind": "continuous_regression_primary",
                    "target_name": F1_TARGET_NAME,
                    "mode": mode,
                    "model_family": family,
                    "primary_policy": "equal_finite_mean_per_frequency_v1",
                    "sensitivity_policy": "keep_last_acquisition_v1",
                    "mae_delta_sign_reversal": sign_reversal,
                    "mae_delta_absolute_change_pp": abs(keep_last_delta - primary_delta),
                    "prohibit_eis_increment_conclusion": sign_reversal,
                    "decision_role": "diagnostic_stop_only_never_promotion",
                }
            )
    return paired, sensitivity


def _legacy_v1_paired_outputs(
    snapshots: Sequence[DevelopmentSnapshot],
    predictions: Sequence[dict[str, object]],
    plan: AnalysisPlan,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Isolated superseded O2-v1 helper; never called by the v2 engine."""
    paired: list[dict[str, object]] = []
    sensitivity: list[dict[str, object]] = []
    for mode in ("cyclic", "profile"):
        cohort = [snapshot for snapshot in snapshots if snapshot.mode == mode]
        for family in ("logistic_elastic_net", "hist_gradient_boosting"):
            b = _legacy_v1_prediction_vector(
                predictions,
                snapshots,
                mode=mode,
                acquisition_policy="primary",
                view="B",
                model_family=family,
            )
            policy_results: dict[str, dict[str, object]] = {}
            for policy in ("primary", "keep_last_sensitivity"):
                d_core = _legacy_v1_prediction_vector(
                    predictions,
                    snapshots,
                    mode=mode,
                    acquisition_policy=policy,
                    view="D_CORE",
                    model_family=family,
                )
                b_metrics = condition_balanced_metrics(cohort, b)
                d_metrics = condition_balanced_metrics(cohort, d_core)
                pr_delta = _finite_float(
                    d_metrics["condition_balanced_pooled_pr_auc_v1"], name="D_CORE PR-AUC"
                ) - _finite_float(b_metrics["condition_balanced_pooled_pr_auc_v1"], name="B PR-AUC")
                brier_delta = _finite_float(
                    d_metrics["condition_balanced_pooled_brier_v1"], name="D_CORE Brier"
                ) - _finite_float(b_metrics["condition_balanced_pooled_brier_v1"], name="B Brier")
                bootstrap = paired_condition_bootstrap(
                    cohort,
                    b,
                    d_core,
                    draws=plan.bootstrap_draws,
                    seed=plan.bootstrap_seed,
                    minimum_valid_draws=plan.minimum_valid_bootstrap_draws,
                )
                result: dict[str, object] = {
                    **FALSE_RESULT_FLAGS,
                    "mode": mode,
                    "model_family": family,
                    "acquisition_policy": policy,
                    "same_snapshot_ids_and_weights": True,
                    "pr_auc_delta_d_core_minus_b": pr_delta,
                    "brier_delta_d_core_minus_b": brier_delta,
                    "bootstrap": bootstrap,
                }
                paired.append(result)
                policy_results[policy] = result
            primary = policy_results["primary"]
            keep_last = policy_results["keep_last_sensitivity"]
            primary_pr = _finite_float(
                primary["pr_auc_delta_d_core_minus_b"], name="primary PR-AUC delta"
            )
            keep_last_pr = _finite_float(
                keep_last["pr_auc_delta_d_core_minus_b"], name="keep-last PR-AUC delta"
            )
            primary_brier = _finite_float(
                primary["brier_delta_d_core_minus_b"], name="primary Brier delta"
            )
            keep_last_brier = _finite_float(
                keep_last["brier_delta_d_core_minus_b"], name="keep-last Brier delta"
            )
            stop_decision = retry_sensitivity_stop_decision(
                primary_pr_auc_delta=primary_pr,
                keep_last_pr_auc_delta=keep_last_pr,
                primary_brier_delta=primary_brier,
                keep_last_brier_delta=keep_last_brier,
            )
            sensitivity.append(
                {
                    **FALSE_RESULT_FLAGS,
                    "mode": mode,
                    "model_family": family,
                    "primary_policy": "equal_finite_mean_per_frequency_v1",
                    "sensitivity_policy": "keep_last_acquisition_v1",
                    **stop_decision,
                }
            )
    return paired, sensitivity


def analyze_records(
    records: KitV2DevelopmentRecords,
    *,
    plan: AnalysisPlan,
    progress: Callable[[dict[str, object]], None],
    development_authorized: bool = False,
    frozen_readiness: Mapping[str, object] | None = None,
) -> EngineAnalysis:
    """Run the fixed F1-v2 workflows; never fit O2 or authorize a public claim."""
    snapshots, acquisitions, materialization = materialize_development_snapshots(records)
    folds_by_mode, support = build_fold_plans(snapshots, plan)
    paired_design_by_mode = {
        mode: _paired_design_payload(
            [
                snapshot
                for snapshot in snapshots
                if snapshot.mode == mode and _eligible_for_f1_b(snapshot)
            ],
            folds,
        )
        for mode, folds in folds_by_mode.items()
    }
    if frozen_readiness is not None:
        observed_cohort_hash = paired_cohort_identity_sha256(snapshots)
        observed_design_hashes = {
            mode: str(payload["paired_design_sha256"])
            for mode, payload in paired_design_by_mode.items()
        }
        if (
            observed_cohort_hash != frozen_readiness.get("paired_cohort_identity_sha256")
            or observed_design_hashes != frozen_readiness.get("paired_design_sha256_by_mode")
            or planned_trial_keys_sha256() != frozen_readiness.get("planned_trial_keys_sha256")
        ):
            raise DevelopmentRunError(
                "materialized paired cohort or fold design differs from frozen readiness"
            )
    fold_registry = {
        **FALSE_RESULT_FLAGS,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "split_policy": "condition_grouped_outer3_inner2_f1_v2",
        "task_independent": True,
        "applies_to_tasks": [F1_TASK_ID, O2_TASK_ID],
        "support": support,
        "preprocessing_by_mode": _preprocessing_audit(snapshots, folds_by_mode),
        "paired_design_by_mode": paired_design_by_mode,
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
                for fold in folds
            ]
            for mode, folds in folds_by_mode.items()
            for cohort in (
                [
                    snapshot
                    for snapshot in snapshots
                    if snapshot.mode == mode and _eligible_for_f1_b(snapshot)
                ],
            )
        },
    }
    predictions, metrics, planned, completed = _run_f1_oof_workflows(
        snapshots, folds_by_mode, paired_design_by_mode, progress
    )
    paired, sensitivity = _f1_paired_outputs(snapshots, predictions, plan)
    status_counts = Counter(snapshot.eligibility_status for snapshot in snapshots)
    f1_available = sum(snapshot.f1_target_available for snapshot in snapshots)
    o2_candidate_events = sum(int(snapshot.o2_label or 0) for snapshot in snapshots)
    summary: dict[str, object] = {
        **FALSE_RESULT_FLAGS,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_kind": "KIT_F1_O2_DEVELOPMENT_V2",
        "source_archives": {"eoc": records.eoc_archive, "eis": records.eis_archive},
        "source_counts": records.counts,
        "materialization": materialization,
        "snapshot_count": len(snapshots),
        "target_registry": [
            {
                "task_id": F1_TASK_ID,
                "target_kind": "continuous_regression_primary",
                "target_name": F1_TARGET_NAME,
                "unit": "percentage_points",
                "status": (
                    "development_oof_only_eis_go_with_explicit_limitations"
                    if development_authorized
                    else "synthetic_capability_only_wave_a_no_go"
                ),
                "training_authorized": development_authorized,
                "display_authorized": False,
                "selection_role": "none",
                "synthetic_capability_exercised": not development_authorized,
                "planned_fits": planned,
                "completed_fits": completed,
                "prediction_rows": len(predictions),
            },
            {
                "task_id": O2_TASK_ID,
                "target_kind": "binary_probability_secondary_candidate",
                "target_name": O2_TARGET_NAME,
                "unit": "probability",
                "status": "blocked_pending_noise_support_utility_review",
                "training_authorized": False,
                "display_authorized": False,
                "selection_role": "none",
                "synthetic_capability_exercised": False,
                "planned_fits": 0,
                "completed_fits": 0,
                "prediction_rows": 0,
            },
        ],
        "target_counts": {
            "snapshot_status": dict(sorted(status_counts.items())),
            "f1_exact_h3_available": f1_available,
            "o2_candidate_events_observed": o2_candidate_events,
            "o2_prediction_rows": 0,
        },
        "planned_fits": planned,
        "completed_fits": completed,
        "fit_counts_by_task": {
            F1_TASK_ID: {"planned": planned, "completed": completed},
            O2_TASK_ID: {"planned": 0, "completed": 0},
        },
        "candidate_role": (
            "fixed_development_oof_only_no_selection"
            if development_authorized
            else "fixed_capability_only_no_selection"
        ),
        "metrics": metrics,
        "comparison_policy": "report_each_fixed_family_without_ranking_or_promotion",
        "o2_results": [],
    }
    return EngineAnalysis(
        acquisitions=tuple(acquisitions),
        snapshots=tuple(snapshots),
        fold_registry=fold_registry,
        predictions=tuple(predictions),
        paired_deltas=tuple(paired),
        retry_sensitivity=tuple(sensitivity),
        summary=summary,
        planned_fits=planned,
        completed_fits=completed,
    )


def _load_config(config_path: Path) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    payload = json.loads(config_path.read_text())
    schema_path = config_path.with_name(f"{config_path.stem}.schema.json")
    schema = json.loads(schema_path.read_text())
    _assert_json_finite(payload, path="config")
    _assert_json_finite(schema, path="artifact_schema")
    schema_hash = _sha256_file(schema_path)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path)
    )
    if errors:
        locations = ["/".join(str(part) for part in error.path) or "<root>" for error in errors]
        raise DevelopmentRunError(f"invalid robustness config at: {', '.join(locations)}")
    if payload["artifact_schema_sha256"] != schema_hash:
        raise DevelopmentRunError(
            "artifact schema sha256 hash does not match the frozen schema file"
        )
    assert_no_champion_fields(payload)
    return payload, _sha256_file(config_path), schema, schema_hash


def _artifact_validator(schema: Mapping[str, Any], definition: str) -> Draft202012Validator:
    definitions = schema.get("$defs")
    if not isinstance(definitions, Mapping) or definition not in definitions:
        raise DevelopmentRunError(f"artifact schema definition is missing: {definition}")
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": dict(definitions),
        "$ref": f"#/$defs/{definition}",
    }
    Draft202012Validator.check_schema(wrapper)
    return Draft202012Validator(wrapper)


def _validate_artifact_semantics(decoded: Mapping[str, list[dict[str, Any]]]) -> None:
    """Cross-check scientific invariants that JSON Schema cannot express."""
    acquisitions = decoded["acquisition_manifest.jsonl"]
    snapshots = decoded["snapshot_manifest.jsonl"]
    predictions = decoded["oof_predictions.jsonl"]
    summary = decoded["engine_summary.json"][0]
    snapshot_by_id = {str(row["snapshot_id"]): row for row in snapshots}
    if len(snapshot_by_id) != len(snapshots):
        raise DevelopmentRunError("snapshot manifest contains duplicate snapshot IDs")

    availability_by_snapshot: dict[str, list[float]] = defaultdict(list)
    for acquisition in acquisitions:
        aligned_id = acquisition["aligned_snapshot_id"]
        if aligned_id is None:
            continue
        snapshot = snapshot_by_id.get(str(aligned_id))
        if snapshot is None:
            raise DevelopmentRunError("EIS acquisition references an unknown aligned snapshot")
        acquisition_start = _finite_float(
            acquisition["timestamp_s"], name="EIS acquisition timestamp"
        )
        acquisition_available = acquisition_start + _finite_float(
            acquisition["duration_s"], name="EIS acquisition duration"
        )
        current_time = _finite_float(snapshot["timestamp_s"], name="current snapshot timestamp")
        if acquisition_available <= current_time:
            raise DevelopmentRunError(
                "aligned EIS acquisition is not available after current snapshot"
            )
        availability_by_snapshot[str(aligned_id)].append(acquisition_available)
        next_snapshot = snapshot_by_id.get(
            f"{snapshot['cell_id']}:checkup-{int(snapshot['checkup_index']) + 1}"
        )
        if next_snapshot is not None:
            next_time = _finite_float(
                next_snapshot["timestamp_s"], name="next capacity endpoint timestamp"
            )
            if acquisition_available >= next_time:
                raise DevelopmentRunError(
                    "EIS acquisition reaches or crosses next capacity boundary"
                )
        h3_id = snapshot["h3_endpoint_snapshot_id"]
        if h3_id is not None:
            h3_snapshot = snapshot_by_id.get(str(h3_id))
            if h3_snapshot is None:
                raise DevelopmentRunError("snapshot references an unknown H3 endpoint")
            h3_time = _finite_float(h3_snapshot["timestamp_s"], name="H3 endpoint timestamp")
            if acquisition_available >= h3_time:
                raise DevelopmentRunError(
                    "EIS acquisition reaches or crosses the H3 target boundary"
                )

    for snapshot_id, snapshot in snapshot_by_id.items():
        observed = snapshot["prediction_available_at_s"]
        availability = availability_by_snapshot.get(snapshot_id, [])
        if not availability:
            if observed is not None:
                raise DevelopmentRunError(
                    "snapshot without aligned EIS has prediction availability"
                )
            continue
        expected = max(availability)
        if observed is None or not math.isclose(
            _finite_float(observed, name="prediction availability"),
            expected,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise DevelopmentRunError(
                "snapshot prediction availability does not match EIS completion"
            )

    for row in snapshots:
        available = bool(row["f1_target_available"])
        dependent = (
            row["future_soh_percent"],
            row["soh_loss_pp_h3"],
            row["h3_endpoint_snapshot_id"],
            row["h3_checkup_index"],
            row["o2_label"],
        )
        if not available and any(value is not None for value in dependent):
            raise DevelopmentRunError("unavailable F1 target has non-null H3 dependent fields")
        if available:
            expected_h3 = int(row["checkup_index"]) + O2_HORIZON_CHECKUPS
            if int(row["h3_checkup_index"]) != expected_h3:
                raise DevelopmentRunError("F1 H3 endpoint is not exactly three checkups ahead")
            loss = _finite_float(row["soh_loss_pp_h3"], name="snapshot H3 loss")
            current = _finite_float(row["soh_percent"], name="snapshot current SOH")
            future = _finite_float(row["future_soh_percent"], name="snapshot future SOH")
            if not math.isclose(current - future, loss, rel_tol=0.0, abs_tol=1e-9):
                raise DevelopmentRunError("snapshot current/future/H3-loss invariant failed")
            if int(row["o2_label"]) != int(loss >= O2_LOSS_THRESHOLD_PERCENTAGE_POINTS):
                raise DevelopmentRunError("snapshot O2 candidate label does not match H3 loss")

    for row in predictions:
        if row["task_id"] != F1_TASK_ID:
            raise DevelopmentRunError("v2 OOF artifact contains a non-F1 prediction")
        snapshot = snapshot_by_id.get(str(row["snapshot_id"]))
        if snapshot is None:
            raise DevelopmentRunError("OOF prediction references an unknown snapshot")
        status = snapshot["eligibility_status"]
        if row["view"] == "B" and status not in {"eligible_f1_and_d_core", "missing_eis"}:
            raise DevelopmentRunError("B prediction escaped the F1-B eligible cohort")
        if row["view"] == "D_CORE" and status != "eligible_f1_and_d_core":
            raise DevelopmentRunError("D_CORE prediction includes a snapshot without matching EIS")

    f1_registry, o2_registry = summary["target_registry"]
    allowed_f1_states = {
        (
            "synthetic_capability_only_wave_a_no_go",
            False,
            True,
            "fixed_capability_only_no_selection",
        ),
        (
            "development_oof_only_eis_go_with_explicit_limitations",
            True,
            False,
            "fixed_development_oof_only_no_selection",
        ),
    }
    observed_f1_state = (
        f1_registry["status"],
        f1_registry["training_authorized"],
        f1_registry["synthetic_capability_exercised"],
        summary["candidate_role"],
    )
    if observed_f1_state not in allowed_f1_states:
        raise DevelopmentRunError("F1 authorization/status/candidate-role tuple is inconsistent")
    fit_counts = summary["fit_counts_by_task"]
    if (
        summary["planned_fits"] != f1_registry["planned_fits"]
        or summary["completed_fits"] != f1_registry["completed_fits"]
    ):
        raise DevelopmentRunError("top-level and F1 target fit counts do not reconcile")
    if fit_counts[F1_TASK_ID] != {
        "planned": summary["planned_fits"],
        "completed": summary["completed_fits"],
    }:
        raise DevelopmentRunError("F1 fit-count packet does not reconcile")
    if fit_counts[O2_TASK_ID] != {"planned": 0, "completed": 0}:
        raise DevelopmentRunError("O2 fit counts must remain exactly zero")
    if f1_registry["prediction_rows"] != len(predictions):
        raise DevelopmentRunError("F1 prediction-row count does not reconcile")
    if o2_registry["prediction_rows"] != 0 or summary["target_counts"]["o2_prediction_rows"] != 0:
        raise DevelopmentRunError("O2 prediction-row counts must remain exactly zero")
    if summary["o2_results"]:
        raise DevelopmentRunError("O2 results must remain empty")

    fitted = [row for row in predictions if row["model_role"] == "fitted_candidate"]
    fitted_lookup = {
        (
            row["mode"],
            row["acquisition_policy"],
            row["view"],
            row["model_family"],
            row["snapshot_id"],
        ): row
        for row in fitted
    }
    if len(fitted_lookup) != len(fitted):
        raise DevelopmentRunError("duplicate fitted OOF prediction identity")
    for row in fitted:
        if row["view"] != "D_CORE":
            continue
        b_key = (
            row["mode"],
            "primary",
            "B",
            row["model_family"],
            row["snapshot_id"],
        )
        b_row = fitted_lookup.get(b_key)
        if b_row is None:
            raise DevelopmentRunError("D_CORE prediction lacks paired B-primary prediction")
        paired_fields = (
            "paired_design_sha256",
            "outer_fold",
            "condition_id",
            "cell_id",
            "outer_validation_weight_mean_one",
        )
        if any(row[field] != b_row[field] for field in paired_fields):
            raise DevelopmentRunError("D_CORE/B-primary pairing identity drifted")

    status_counts = dict(sorted(Counter(row["eligibility_status"] for row in snapshots).items()))
    target_counts = summary["target_counts"]
    if summary["snapshot_count"] != len(snapshots):
        raise DevelopmentRunError("summary snapshot count does not reconcile")
    if target_counts["snapshot_status"] != status_counts:
        raise DevelopmentRunError("summary snapshot-status counts do not reconcile")
    if target_counts["f1_exact_h3_available"] != sum(
        bool(row["f1_target_available"]) for row in snapshots
    ):
        raise DevelopmentRunError("summary F1 target count does not reconcile")
    if target_counts["o2_candidate_events_observed"] != sum(
        int(row["o2_label"] or 0) for row in snapshots
    ):
        raise DevelopmentRunError("summary O2 candidate-event count does not reconcile")


def _validate_artifact_payloads(payloads: Mapping[str, str], schema: Mapping[str, Any]) -> None:
    """Validate every persisted v2 row/envelope against a non-overlapping strict definition."""
    if tuple(payloads) != ARTIFACT_NAMES:
        raise DevelopmentRunError("engine artifact contract drifted")
    definitions = {
        "acquisition_manifest.jsonl": "acquisitionManifestRow",
        "snapshot_manifest.jsonl": "snapshotManifestRow",
        "oof_predictions.jsonl": "oofPredictionRow",
        "fold_registry.json": "foldRegistryArtifact",
        "paired_deltas.json": "pairedDeltasArtifact",
        "retry_sensitivity.json": "retrySensitivityArtifact",
        "engine_summary.json": "engineSummaryArtifact",
    }
    decoded: dict[str, list[dict[str, Any]]] = {}
    for name in ARTIFACT_NAMES:
        text = payloads[name]
        if name.endswith(".jsonl"):
            values = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            values = [json.loads(text)]
        for index, value in enumerate(values, start=1):
            _assert_json_finite(value, path=f"{name}/{index}")
        decoded[name] = values
        validator = _artifact_validator(schema, definitions[name])
        for index, value in enumerate(values, start=1):
            errors = sorted(validator.iter_errors(value), key=lambda item: list(item.path))
            if errors:
                first = errors[0]
                location = "/".join(str(part) for part in first.path) or "<root>"
                raise DevelopmentRunError(
                    f"invalid {name} artifact at row {index}, {location}: {first.message}"
                )
    _validate_artifact_semantics(decoded)


def _artifact_payloads(
    analysis: EngineAnalysis,
    config_hash: str,
    artifact_schema: Mapping[str, Any],
    artifact_schema_hash: str,
) -> dict[str, str]:
    def jsonl(rows: Sequence[dict[str, object]]) -> str:
        return "".join(_canonical_json(row) + "\n" for row in rows)

    acquisition_rows = [
        {
            **FALSE_RESULT_FLAGS,
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            **asdict(record),
            "eis_values": [_json_number(value) for value in record.eis_values],
        }
        for record in sorted(analysis.acquisitions, key=lambda item: item.acquisition_id)
    ]
    snapshot_rows = [
        {
            **FALSE_RESULT_FLAGS,
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            **asdict(snapshot),
            "future_soh_percent": _json_number(snapshot.future_soh_percent)
            if snapshot.future_soh_percent is not None
            else None,
            "soh_loss_pp_h3": _json_number(snapshot.soh_loss_pp_h3)
            if snapshot.soh_loss_pp_h3 is not None
            else None,
            "b_values": [_json_number(value) for value in snapshot.b_values],
            "primary_eis_values": [_json_number(value) for value in snapshot.primary_eis_values],
            "keep_last_eis_values": [
                _json_number(value) for value in snapshot.keep_last_eis_values
            ],
        }
        for snapshot in sorted(analysis.snapshots, key=lambda item: item.snapshot_id)
    ]
    summary = {
        **analysis.summary,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "config_sha256": config_hash,
        "artifact_schema_sha256": artifact_schema_hash,
        "artifact_paths": list(ARTIFACT_NAMES),
    }
    for guarded in (
        acquisition_rows,
        snapshot_rows,
        analysis.fold_registry,
        analysis.predictions,
        analysis.paired_deltas,
        analysis.retry_sensitivity,
        summary,
    ):
        assert_no_champion_fields(guarded)
        _assert_json_finite(guarded, path="artifact_payload")
    payloads = {
        "acquisition_manifest.jsonl": jsonl(acquisition_rows),
        "snapshot_manifest.jsonl": jsonl(snapshot_rows),
        "fold_registry.json": json.dumps(
            {
                **analysis.fold_registry,
                "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            },
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        "oof_predictions.jsonl": jsonl(
            sorted(
                (
                    {**row, "artifact_schema_version": ARTIFACT_SCHEMA_VERSION}
                    for row in analysis.predictions
                ),
                key=lambda row: (
                    str(row["mode"]),
                    str(row["acquisition_policy"]),
                    str(row["view"]),
                    str(row["model_family"]),
                    str(row["snapshot_id"]),
                ),
            )
        ),
        "paired_deltas.json": json.dumps(
            {
                **FALSE_RESULT_FLAGS,
                "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
                "paired_deltas": [
                    {**row, "artifact_schema_version": ARTIFACT_SCHEMA_VERSION}
                    for row in analysis.paired_deltas
                ],
            },
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        "retry_sensitivity.json": json.dumps(
            {
                **FALSE_RESULT_FLAGS,
                "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
                "results": [
                    {**row, "artifact_schema_version": ARTIFACT_SCHEMA_VERSION}
                    for row in analysis.retry_sensitivity
                ],
            },
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        "engine_summary.json": json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
    }
    _validate_artifact_payloads(payloads, artifact_schema)
    return payloads


def _write_once_atomic(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"immutable scientific artifact already exists: {path.name}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise FileExistsError(f"stale artifact staging file exists: {temporary.name}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _verify_limited_go_readiness(config_path: Path, readiness: Mapping[str, Any]) -> None:
    """Bind the limited GO to the reviewed physical evidence without erasing limitations."""
    relative = Path(str(readiness["eis_readiness_artifact_path"]))
    if relative.is_absolute() or ".." not in relative.parts:
        raise DevelopmentRunError(
            "readiness evidence path is not the frozen repository-relative path"
        )
    repository_root = config_path.parent.parent.resolve()
    candidate = config_path.parent / relative
    evidence = candidate.resolve()
    try:
        evidence.relative_to(repository_root)
    except ValueError as error:
        raise DevelopmentRunError("readiness evidence escapes the repository root") from error
    relative_evidence = evidence.relative_to(repository_root)
    current = repository_root
    for component in relative_evidence.parts:
        current = current / component
        if current.is_symlink():
            raise DevelopmentRunError("readiness evidence path contains a symlink")
    if not evidence.is_file():
        raise DevelopmentRunError("readiness evidence is not a regular non-symlink file")
    if _sha256_file(evidence) != readiness["eis_readiness_artifact_sha256"]:
        raise DevelopmentRunError("readiness evidence SHA-256 no longer matches the frozen review")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    if payload.get("decision") != readiness["eis_alignment_decision"]:
        raise DevelopmentRunError("readiness evidence decision does not match configuration")
    cohorts = payload.get("cohorts", {})
    features = payload.get("features", {})
    alignment = payload.get("alignment", {})
    if (
        cohorts.get("b_h3_total") != readiness["expected_paired_snapshots"]
        or cohorts.get("d_core_total") != readiness["expected_paired_snapshots"]
        or cohorts.get("b_missing_eis_total") != 0
        or features.get("d_core_frequencies") != readiness["expected_eis_frequencies"]
        or features.get("d_core_values") != readiness["expected_eis_values"]
        or alignment.get("eis_completion_after_or_at_h3_endpoint_violations") != 0
        or alignment.get("next_capacity_boundary_violations") != 0
        or payload.get("training_executed") is not False
    ):
        raise DevelopmentRunError(
            "readiness evidence semantic invariants no longer match limited GO"
        )


def _verify_source_binding(path: Path, *, sha256: str, size_bytes: int, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise DevelopmentRunError(f"{label} archive is not a regular non-symlink file")
    before = path.stat()
    if before.st_size != size_bytes or _sha256_file(path) != sha256:
        raise DevelopmentRunError(f"{label} archive size/hash differs from frozen source")
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise DevelopmentRunError(f"{label} archive changed while it was verified")


def execute_engineering_analysis(
    *,
    eoc_zip: Path,
    eis_zip: Path,
    output_dir: Path,
    config_path: Path,
    progress: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """Execute the frozen development-only engine and write only scientific artifacts."""
    config, config_hash, artifact_schema, artifact_schema_hash = _load_config(config_path)
    if config.get("config_id") != "kit_f1_o2_development_v2":
        raise DevelopmentRunError("superseded configuration cannot authorize the F1 v2 engine")
    if config.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise DevelopmentRunError(
            "scientific artifact schema version is not the frozen F1 v2 version"
        )
    authorization = config["authorization"]
    if any(
        authorization[key]
        for key in (
            "scientific_result",
            "publication_ready",
            "resume_approved",
            "formal_model_result",
            "protected_test_result",
            "selection_performed",
        )
    ):
        raise DevelopmentRunError("configuration attempts to authorize a prohibited result")
    readiness = config["readiness"]
    if not readiness["wave_a_target_data_go"] or not readiness["f1_training_authorized"]:
        raise DevelopmentRunError(
            "Wave A target/data readiness is NO-GO; F1 execution is blocked before source access or fit"
        )
    if (
        readiness["timestamp_semantics_verified"]
        or not readiness["checkup_completion_semantics_verified"]
        or not authorization["timestamp_semantics_provisional"]
    ):
        raise DevelopmentRunError(
            "limited GO requires verified completion semantics while timestamp semantics remain provisional"
        )
    _verify_limited_go_readiness(config_path, readiness)
    o2 = config["targets"]["o2"]
    if o2["training_authorized"] or o2["display_authorized"] or o2["selection_role"] != "none":
        raise DevelopmentRunError("O2 training and display remain blocked pending Wave A approval")
    source = config["source"]
    _verify_source_binding(
        eoc_zip,
        sha256=source["eoc_sha256"],
        size_bytes=int(source["eoc_size_bytes"]),
        label="EOC",
    )
    _verify_source_binding(
        eis_zip,
        sha256=source["eis_sha256"],
        size_bytes=int(source["eis_size_bytes"]),
        label="EIS",
    )
    output = output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    unexpected = sorted(path.name for path in output.iterdir())
    if unexpected:
        raise FileExistsError(
            f"scientific output directory must be empty; runtime/artifact collision: {unexpected}"
        )
    records = load_kit_v2_development_records(
        eoc_zip,
        eis_zip,
        expected_cells=int(source["expected_real_cells"]),
        expected_capacity_endpoints=int(source["expected_capacity_endpoints"]),
        expected_valid_rt50=int(source["expected_valid_rt50_spectra"]),
    )
    validation = config["validation"]
    metrics = config["metrics"]
    plan = AnalysisPlan(
        outer_group_folds=int(validation["outer_group_folds"]),
        inner_group_folds=int(validation["inner_group_folds"]),
        minimum_conditions_per_mode=int(validation["minimum_conditions_per_mode"]),
        minimum_conditions_per_validation_fold=int(
            validation["minimum_conditions_per_validation_fold"]
        ),
        bootstrap_draws=int(metrics["bootstrap_draws"]),
        minimum_valid_bootstrap_draws=int(metrics["minimum_valid_bootstrap_draws"]),
        bootstrap_seed=int(metrics["bootstrap_seed"]),
    )
    analysis = analyze_records(
        records,
        plan=plan,
        progress=progress,
        development_authorized=True,
        frozen_readiness=readiness,
    )
    expected_paired = int(readiness["expected_paired_snapshots"])
    materialization = analysis.summary["materialization"]
    if not isinstance(materialization, dict):
        raise DevelopmentRunError("materialization summary must be an object")
    materialization_counts = materialization["counts"]
    if not isinstance(materialization_counts, dict):
        raise DevelopmentRunError("materialization counts must be an object")
    if (
        len(analysis.snapshots) != int(source["expected_capacity_endpoints"])
        or materialization_counts.get("eligible_f1_and_d_core") != expected_paired
        or materialization_counts.get("missing_eis", 0) != 0
        or len(EIS_FEATURE_NAMES) != int(readiness["expected_eis_values"])
        or len(ALLOWED_EIS_SEQUENCES) != int(readiness["expected_eis_frequencies"])
    ):
        raise DevelopmentRunError("materialized paired cohort differs from frozen limited GO")
    payloads = _artifact_payloads(analysis, config_hash, artifact_schema, artifact_schema_hash)
    for name in ARTIFACT_NAMES:
        _write_once_atomic(output / name, payloads[name])
    return {
        **FALSE_RESULT_FLAGS,
        "artifact_paths": list(ARTIFACT_NAMES),
        "planned_fits": analysis.planned_fits,
        "completed_fits": analysis.completed_fits,
        "config_sha256": config_hash,
        "artifact_schema_sha256": artifact_schema_hash,
    }
