"""Leakage-resistant contracts for degradation early warning.

The module is intentionally source-agnostic.  It does not parse KIT archives, train a
model, or claim that a capacity threshold was crossed at an unobserved continuous time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from math import isfinite
from statistics import median
from typing import Literal

import numpy as np
from sklearn.metrics import average_precision_score


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_normalized_text(value: str, name: str) -> None:
    """Reject empty or ambiguously normalized identifiers and policy fields."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty and already whitespace-normalized")


def _require_finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _validate_mode_unit(ageing_mode: AgeingMode, unit: str, *, name: str) -> None:
    """Enforce the registered primary remaining-exposure scale for each mode."""
    if not isinstance(ageing_mode, AgeingMode):
        raise TypeError(f"{name} ageing_mode must be an AgeingMode")
    _require_normalized_text(unit, f"{name} unit")
    expected = "days" if ageing_mode is AgeingMode.CALENDAR else "EFC"
    if unit != expected:
        detail = (
            "; profile distance may be recorded only as a separate traceable secondary field"
            if ageing_mode is AgeingMode.PROFILE
            else ""
        )
        raise ValueError(
            f"{name} unit {unit!r} is incompatible with {ageing_mode.value}; "
            f"expected {expected!r}{detail}"
        )


class AgeingMode(StrEnum):
    CALENDAR = "calendar"
    CYCLIC = "cyclic"
    PROFILE = "profile"


@dataclass(frozen=True, order=True)
class SnapshotKey:
    """Stable identity for one physical-cell diagnostic checkup."""

    cell_id: str
    checkpoint: int


@dataclass(frozen=True, kw_only=True)
class Q0Provenance:
    """The explicit source and availability time of the capacity denominator."""

    capacity_ah: float
    source_cell_id: str
    source_identity: str
    source_archive: str
    source_member_or_file: str
    source_row_locator: str
    measurement_timestamp: datetime
    endpoint_selection_rule: str
    unit: str
    source_manifest_hash: str
    available_at: datetime

    def __post_init__(self) -> None:
        if not isfinite(self.capacity_ah) or self.capacity_ah <= 0:
            raise ValueError("Q0 capacity must be finite and positive")
        text_fields = (
            self.source_cell_id,
            self.source_identity,
            self.source_archive,
            self.source_member_or_file,
            self.source_row_locator,
            self.endpoint_selection_rule,
            self.unit,
            self.source_manifest_hash,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("all Q0 provenance fields must be non-empty")
        if self.unit != "Ah":
            raise ValueError("Q0 capacity_ah provenance unit must be exactly 'Ah'")
        _require_aware(self.measurement_timestamp, "Q0 measurement_timestamp")
        _require_aware(self.available_at, "Q0 available_at")
        if self.measurement_timestamp > self.available_at:
            raise ValueError("Q0 cannot be available before its measurement timestamp")


@dataclass(frozen=True, kw_only=True)
class BatterySnapshot:
    """Information available when one diagnostic checkup has completed."""

    cell_id: str
    condition_id: str
    checkpoint: int
    completed_at: datetime
    capacity_ah: float
    q0: Q0Provenance
    ageing_mode: AgeingMode
    exposure: float
    exposure_unit: str
    elapsed_days: float
    equivalent_full_cycles: float
    temperature_c: float
    context_features: tuple[tuple[str, float], ...] = ()
    eis_features: tuple[float, ...] = ()
    eis_valid: bool = False
    auxiliary_features: tuple[tuple[str, float], ...] = ()
    capacity_unit: Literal["Ah"] = "Ah"

    def __post_init__(self) -> None:
        _require_normalized_text(self.cell_id, "cell_id")
        _require_normalized_text(self.condition_id, "condition_id")
        if self.capacity_unit != "Ah":
            raise ValueError("capacity_ah must use capacity_unit='Ah'")
        _validate_mode_unit(self.ageing_mode, self.exposure_unit, name="snapshot exposure")
        if self.checkpoint < 0:
            raise ValueError("checkpoint must be non-negative")
        _require_aware(self.completed_at, "snapshot completed_at")
        if self.q0.source_cell_id != self.cell_id:
            raise ValueError("Q0 provenance must belong to the same physical cell")
        if self.q0.available_at > self.completed_at:
            raise ValueError("Q0 was not available when the snapshot completed")
        for name, value in (
            ("capacity", self.capacity_ah),
            ("exposure", self.exposure),
            ("elapsed_days", self.elapsed_days),
            ("equivalent_full_cycles", self.equivalent_full_cycles),
        ):
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not isfinite(self.temperature_c):
            raise ValueError("temperature must be finite")
        for block_name, block in (
            ("context", self.context_features),
            ("auxiliary", self.auxiliary_features),
        ):
            names = [name for name, _ in block]
            if len(names) != len(set(names)) or any(not name.strip() for name in names):
                raise ValueError(f"{block_name} feature names must be non-empty and unique")
            if any(not isfinite(value) for _, value in block):
                raise ValueError(f"{block_name} features must be finite")
        if any(not isfinite(value) for value in self.eis_features):
            raise ValueError("EIS features must be finite")
        if self.eis_valid and not self.eis_features:
            raise ValueError("valid EIS must contain at least one feature")

    @property
    def key(self) -> SnapshotKey:
        return SnapshotKey(self.cell_id, self.checkpoint)

    @property
    def soh_percent(self) -> float:
        return conventional_soh_percent(self.capacity_ah, self.q0.capacity_ah)


def conventional_soh_percent(capacity_ah: float, initial_capacity_ah: float) -> float:
    """Return 100 * current standardized capacity / explicit Q0, without clipping."""
    if not isfinite(capacity_ah) or capacity_ah < 0:
        raise ValueError("capacity must be finite and non-negative")
    if not isfinite(initial_capacity_ah) or initial_capacity_ah <= 0:
        raise ValueError("Q0 must be finite and positive")
    return 100.0 * capacity_ah / initial_capacity_ah


def validate_history(history: Sequence[BatterySnapshot]) -> tuple[BatterySnapshot, ...]:
    """Validate one cell trajectory in completed-checkup order."""
    if not history:
        raise ValueError("history must not be empty")
    ordered = tuple(sorted(history, key=lambda item: item.checkpoint))
    first = ordered[0]
    for item in ordered:
        if item.cell_id != first.cell_id:
            raise ValueError("history contains multiple physical cells")
        if item.condition_id != first.condition_id:
            raise ValueError("condition changed within a physical-cell history")
        if item.ageing_mode != first.ageing_mode:
            raise ValueError("ageing mode changed within a physical-cell history")
        if item.exposure_unit != first.exposure_unit:
            raise ValueError("exposure unit changed within a physical-cell history")
        if item.q0 != first.q0:
            raise ValueError("Q0 value or provenance changed within a physical-cell history")
    for previous, current in pairwise(ordered):
        if current.checkpoint <= previous.checkpoint:
            raise ValueError("checkpoints must be unique and strictly increasing")
        if current.completed_at <= previous.completed_at:
            raise ValueError("completed_at must be strictly increasing")
        if current.exposure < previous.exposure:
            raise ValueError("mode-specific exposure must not decrease")
        if current.elapsed_days < previous.elapsed_days:
            raise ValueError("elapsed days must not decrease")
        if current.equivalent_full_cycles < previous.equivalent_full_cycles:
            raise ValueError("equivalent full cycles must not decrease")
    return ordered


class FeatureView(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D_CORE = "D_CORE"
    D_FULL = "D_FULL"


@dataclass(frozen=True)
class FeatureVector:
    snapshot_key: SnapshotKey
    view: FeatureView
    names: tuple[str, ...]
    values: tuple[float, ...]
    source_snapshots: tuple[SnapshotKey, ...]
    latest_source_completed_at: datetime


def _snapshot_at(
    history: tuple[BatterySnapshot, ...], checkpoint: int
) -> tuple[int, BatterySnapshot]:
    matches = [(index, item) for index, item in enumerate(history) if item.checkpoint == checkpoint]
    if len(matches) != 1:
        raise ValueError(f"checkpoint {checkpoint} is not unique in history")
    return matches[0]


def _capacity_features(prefix: tuple[BatterySnapshot, ...], recent_points: int) -> dict[str, float]:
    current = prefix[-1]
    window = prefix[-recent_points:]
    slope = 0.0
    curvature = 0.0
    if len(window) >= 2:
        x = np.arange(len(window), dtype=np.float64)
        y = np.asarray([item.soh_percent for item in window], dtype=np.float64)
        slope = float(np.polyfit(x, y, 1)[0])
        if len(window) >= 3:
            curvature = float(np.polyfit(x, y, 2)[0])
    return {
        "initial_capacity_ah": current.q0.capacity_ah,
        "current_capacity_ah": current.capacity_ah,
        "current_soh_percent": current.soh_percent,
        "elapsed_days": current.elapsed_days,
        "equivalent_full_cycles": current.equivalent_full_cycles,
        "recent_soh_slope_per_checkup": slope,
        "recent_soh_curvature_per_checkup2": curvature,
        "completed_checkup_count": float(len(prefix)),
    }


def _context_features(current: BatterySnapshot) -> dict[str, float]:
    output = {
        "temperature_c": current.temperature_c,
        "ageing_mode_calendar": float(current.ageing_mode is AgeingMode.CALENDAR),
        "ageing_mode_cyclic": float(current.ageing_mode is AgeingMode.CYCLIC),
        "ageing_mode_profile": float(current.ageing_mode is AgeingMode.PROFILE),
        "mode_specific_exposure": current.exposure,
    }
    output.update(current.context_features)
    return output


def _eis_features(current: BatterySnapshot) -> dict[str, float]:
    if not current.eis_valid or not current.eis_features:
        raise ValueError("feature view requires a non-empty, quality-valid current EIS block")
    return {f"eis_{index}": value for index, value in enumerate(current.eis_features)}


def build_feature_view(
    history: Sequence[BatterySnapshot],
    checkpoint: int,
    *,
    view: FeatureView,
    recent_points: int = 3,
) -> FeatureVector:
    """Build registered A/B/C/D views from data completed by the prediction time."""
    if recent_points < 2:
        raise ValueError("recent_points must be at least two")
    ordered = validate_history(history)
    position, current = _snapshot_at(ordered, checkpoint)
    prefix = ordered[: position + 1]
    if any(item.completed_at > current.completed_at for item in prefix):
        raise ValueError("future-completed measurements entered the feature prefix")

    capacity = _capacity_features(prefix, recent_points)
    context = _context_features(current)
    values: dict[str, float]
    if view is FeatureView.A:
        values = capacity
    elif view is FeatureView.B:
        values = capacity | context
    elif view is FeatureView.C:
        values = context | _eis_features(current)
    elif view is FeatureView.D_CORE:
        values = capacity | context | _eis_features(current)
    elif view is FeatureView.D_FULL:
        if not current.auxiliary_features:
            raise ValueError("D_FULL requires a non-empty audited auxiliary block")
        values = capacity | context | _eis_features(current) | dict(current.auxiliary_features)
    else:  # pragma: no cover
        raise ValueError(f"unsupported feature view: {view}")
    return FeatureVector(
        current.key,
        view,
        tuple(values),
        tuple(float(value) for value in values.values()),
        tuple(item.key for item in prefix),
        max(item.completed_at for item in prefix),
    )


class LabelStatus(StrEnum):
    OBSERVED_EVENT = "observed_event"
    OBSERVED_NONEVENT = "observed_nonevent"
    RIGHT_CENSORED = "right_censored"


@dataclass(frozen=True)
class ObservedCrossingInterval:
    """Interval containing a first observed threshold transition, not an exact crossing."""

    last_above_key: SnapshotKey
    last_above_completed_at: datetime
    first_observed_below_key: SnapshotKey
    first_observed_below_completed_at: datetime
    remaining_exposure_lower_bound: float
    remaining_exposure_upper_bound: float


@dataclass(frozen=True)
class FutureEventLabel:
    snapshot_key: SnapshotKey
    condition_id: str
    outcome_id: str
    status: LabelStatus
    label: int | None
    horizon_checkups: int
    event_interval: ObservedCrossingInterval | None = None


def _future_checkups(
    history: Sequence[BatterySnapshot], checkpoint: int
) -> tuple[BatterySnapshot, tuple[BatterySnapshot, ...]]:
    ordered = validate_history(history)
    position, current = _snapshot_at(ordered, checkpoint)
    future = ordered[position + 1 :]
    if any(item.completed_at <= current.completed_at for item in future):
        raise ValueError("future outcome checkups must complete strictly after the snapshot")
    return current, future


def build_next_checkup_threshold_label(
    history: Sequence[BatterySnapshot],
    checkpoint: int,
    *,
    horizon_checkups: int,
    threshold_percent: float = 80.0,
    outcome_id: str = "O1_THRESHOLD_80_H3",
) -> FutureEventLabel:
    """O1: first observed future threshold transition within the next N checkups."""
    if horizon_checkups < 1:
        raise ValueError("horizon_checkups must be positive")
    if not isfinite(threshold_percent) or threshold_percent <= 0:
        raise ValueError("threshold must be finite and positive")
    current, future = _future_checkups(history, checkpoint)
    if current.soh_percent <= threshold_percent:
        raise ValueError("O1 snapshot is ineligible because it is already at/below threshold")
    last_above = current
    for candidate in future[:horizon_checkups]:
        if candidate.soh_percent <= threshold_percent:
            interval = ObservedCrossingInterval(
                last_above.key,
                last_above.completed_at,
                candidate.key,
                candidate.completed_at,
                last_above.exposure - current.exposure,
                candidate.exposure - current.exposure,
            )
            return FutureEventLabel(
                current.key,
                current.condition_id,
                outcome_id,
                LabelStatus.OBSERVED_EVENT,
                1,
                horizon_checkups,
                interval,
            )
        last_above = candidate
    if len(future) >= horizon_checkups:
        return FutureEventLabel(
            current.key,
            current.condition_id,
            outcome_id,
            LabelStatus.OBSERVED_NONEVENT,
            0,
            horizon_checkups,
        )
    return FutureEventLabel(
        current.key,
        current.condition_id,
        outcome_id,
        LabelStatus.RIGHT_CENSORED,
        None,
        horizon_checkups,
    )


def build_nth_checkup_rapid_loss_label(
    history: Sequence[BatterySnapshot],
    checkpoint: int,
    *,
    horizon_checkups: int,
    minimum_drop_percentage_points: float,
    outcome_id: str = "O2_RAPID_LOSS_5PP_H3",
) -> FutureEventLabel:
    """O2: compare current SoH with exactly the Nth future completed checkup."""
    if horizon_checkups < 1:
        raise ValueError("horizon_checkups must be positive")
    if not isfinite(minimum_drop_percentage_points) or minimum_drop_percentage_points <= 0:
        raise ValueError("minimum drop must be finite and positive")
    current, future = _future_checkups(history, checkpoint)
    if len(future) < horizon_checkups:
        return FutureEventLabel(
            current.key,
            current.condition_id,
            outcome_id,
            LabelStatus.RIGHT_CENSORED,
            None,
            horizon_checkups,
        )
    nth = future[horizon_checkups - 1]
    event = current.soh_percent - nth.soh_percent >= minimum_drop_percentage_points
    return FutureEventLabel(
        current.key,
        current.condition_id,
        outcome_id,
        LabelStatus.OBSERVED_EVENT if event else LabelStatus.OBSERVED_NONEVENT,
        int(event),
        horizon_checkups,
    )


@dataclass(frozen=True)
class ConditionalRUL:
    snapshot_key: SnapshotKey
    condition_id: str
    threshold_percent: float
    ageing_mode: AgeingMode
    exposure_unit: str
    future_usage_condition: str
    event_observed: bool
    event_interval: ObservedCrossingInterval | None
    right_censored_after: float | None


def build_conditional_rul(
    history: Sequence[BatterySnapshot],
    checkpoint: int,
    *,
    threshold_percent: float,
    future_usage_condition: str,
) -> ConditionalRUL:
    """R1: interval-observed or right-censored remaining exposure under one scenario."""
    if not isfinite(threshold_percent) or threshold_percent <= 0:
        raise ValueError("threshold must be finite and positive")
    if not future_usage_condition.strip():
        raise ValueError("future usage condition must be explicit")
    current, future = _future_checkups(history, checkpoint)
    if current.soh_percent <= threshold_percent:
        raise ValueError("RUL snapshot is ineligible because threshold was already observed")
    last_above = current
    for candidate in future:
        if candidate.soh_percent <= threshold_percent:
            interval = ObservedCrossingInterval(
                last_above.key,
                last_above.completed_at,
                candidate.key,
                candidate.completed_at,
                last_above.exposure - current.exposure,
                candidate.exposure - current.exposure,
            )
            return ConditionalRUL(
                current.key,
                current.condition_id,
                threshold_percent,
                current.ageing_mode,
                current.exposure_unit,
                future_usage_condition,
                True,
                interval,
                None,
            )
        last_above = candidate
    return ConditionalRUL(
        current.key,
        current.condition_id,
        threshold_percent,
        current.ageing_mode,
        current.exposure_unit,
        future_usage_condition,
        False,
        None,
        future[-1].exposure - current.exposure if future else 0.0,
    )


class PeerDeploymentScenario(StrEnum):
    PRIMARY_UNSEEN_CONDITION = "primary_unseen_condition"
    KNOWN_CONDITION_REPLICATE = "known_condition_replicate"


@dataclass(frozen=True)
class PeerFeatureRoles:
    """Explicit fit/reference and target roles for optional operational peer features."""

    enabled: bool = False
    scenario: PeerDeploymentScenario = PeerDeploymentScenario.PRIMARY_UNSEEN_CONDITION
    fitted_reference_cell_ids: frozenset[str] = frozenset()
    target_cell_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("peer enabled flag must be boolean")
        if not isinstance(self.scenario, PeerDeploymentScenario):
            raise TypeError("peer scenario must be a PeerDeploymentScenario")
        for role_name, cell_ids in (
            ("fitted reference", self.fitted_reference_cell_ids),
            ("target", self.target_cell_ids),
        ):
            for cell_id in cell_ids:
                _require_normalized_text(cell_id, f"{role_name} cell_id")
        if self.enabled and self.scenario is PeerDeploymentScenario.PRIMARY_UNSEEN_CONDITION:
            raise ValueError("peer features are prohibited for primary unseen-condition evaluation")
        overlap = self.fitted_reference_cell_ids & self.target_cell_ids
        if overlap:
            raise ValueError(f"peer reference and target roles overlap: {sorted(overlap)}")


@dataclass(frozen=True)
class PeerDeviation:
    snapshot_key: SnapshotKey
    enabled: bool
    available: bool
    reference_snapshots: tuple[SnapshotKey, ...]
    peer_median_soh_percent: float | None = None
    deviation_percentage_points: float | None = None
    robust_z_score: float | None = None


def peer_deviation_at_snapshot(
    target: BatterySnapshot,
    fitted_reference_histories: Mapping[str, Sequence[BatterySnapshot]],
    *,
    roles: PeerFeatureRoles | None = None,
    minimum_peers: int = 2,
) -> PeerDeviation:
    """Use fitted same-condition references completed no later than target time."""
    roles = roles or PeerFeatureRoles()
    if not roles.enabled:
        return PeerDeviation(target.key, False, False, ())
    if target.cell_id not in roles.target_cell_ids:
        raise ValueError("target cell is not assigned the target peer-feature role")
    if target.cell_id in roles.fitted_reference_cell_ids:
        raise ValueError("target cell cannot be a fitted peer reference")
    if minimum_peers < 1:
        raise ValueError("minimum_peers must be positive")
    for mapping_cell_id in fitted_reference_histories:
        _require_normalized_text(mapping_cell_id, "peer history mapping cell_id")
    unexpected = set(fitted_reference_histories) - set(roles.fitted_reference_cell_ids)
    if unexpected:
        raise ValueError(f"histories include non-reference cells: {sorted(unexpected)}")

    peers: list[BatterySnapshot] = []
    for cell_id in sorted(roles.fitted_reference_cell_ids):
        if cell_id not in fitted_reference_histories:
            continue
        reference_history = validate_history(fitted_reference_histories[cell_id])
        actual_cell_id = reference_history[0].cell_id
        if actual_cell_id != cell_id:
            raise ValueError(
                "peer history mapping key must exactly match the history physical cell_id"
            )
        if actual_cell_id not in roles.fitted_reference_cell_ids:
            raise ValueError("peer history physical cell is not assigned the reference role")
        if actual_cell_id in roles.target_cell_ids or actual_cell_id == target.cell_id:
            raise ValueError("target-role cell cannot enter fitted peer reference histories")
        eligible = [
            item
            for item in reference_history
            if item.condition_id == target.condition_id and item.completed_at <= target.completed_at
        ]
        if eligible:
            peers.append(max(eligible, key=lambda item: item.completed_at))
    if len(peers) < minimum_peers:
        return PeerDeviation(target.key, True, False, tuple(item.key for item in peers))
    values = [item.soh_percent for item in peers]
    center = float(median(values))
    deviation = target.soh_percent - center
    mad = float(median(abs(value - center) for value in values))
    robust_z = deviation / (1.4826 * mad) if mad > 0 else None
    return PeerDeviation(
        target.key,
        True,
        True,
        tuple(item.key for item in peers),
        center,
        deviation,
        robust_z,
    )


def validate_group_splits(
    splits: Mapping[str, Sequence[str]],
    *,
    condition_by_cell: Mapping[str, str] | None = None,
    require_condition_disjoint: bool = False,
) -> None:
    """Fail if a physical cell, or optionally a condition, crosses split boundaries."""
    owners: dict[str, str] = {}
    for split_name, cells in splits.items():
        if len(cells) != len(set(cells)):
            raise ValueError(f"duplicate physical cell within {split_name}")
        for cell in cells:
            if cell in owners:
                raise ValueError(f"physical cell {cell} occurs in {owners[cell]} and {split_name}")
            owners[cell] = split_name
    if require_condition_disjoint:
        if condition_by_cell is None:
            raise ValueError("condition mapping is required for condition-disjoint validation")
        conditions: dict[str, str] = {}
        for cell, split_name in owners.items():
            if cell not in condition_by_cell:
                raise ValueError(f"missing condition for cell {cell}")
            condition = condition_by_cell[cell]
            if condition in conditions and conditions[condition] != split_name:
                raise ValueError(
                    f"condition {condition} occurs in {conditions[condition]} and {split_name}"
                )
            conditions[condition] = split_name


@dataclass(frozen=True)
class ProbabilityPrediction:
    snapshot_key: SnapshotKey
    outcome_id: str
    horizon_checkups: int
    value: float
    unit: Literal["probability"] = "probability"


def engineering_micro_warning_metrics(
    labels: Sequence[FutureEventLabel],
    predictions: Sequence[ProbabilityPrediction],
    *,
    decision_threshold: float = 0.5,
) -> dict[str, float | int | str | None]:
    """Engineering-only snapshot-micro metrics; not a formal protocol evaluation."""
    if not 0 <= decision_threshold <= 1:
        raise ValueError("decision threshold must be between zero and one")

    def label_identity(label: FutureEventLabel) -> tuple[SnapshotKey, str, int]:
        return label.snapshot_key, label.outcome_id, label.horizon_checkups

    def prediction_identity(
        prediction: ProbabilityPrediction,
    ) -> tuple[SnapshotKey, str, int]:
        return prediction.snapshot_key, prediction.outcome_id, prediction.horizon_checkups

    label_identities = [label_identity(label) for label in labels]
    prediction_identities = [prediction_identity(prediction) for prediction in predictions]
    if len(set(label_identities)) != len(label_identities):
        raise ValueError("duplicate label identity (snapshot, outcome, horizon)")
    if len(set(prediction_identities)) != len(prediction_identities):
        raise ValueError("duplicate prediction identity (snapshot, outcome, horizon)")
    by_key = {prediction_identity(prediction): prediction for prediction in predictions}
    if set(by_key) != set(label_identities):
        raise ValueError(
            "predictions must map one-to-one to every (snapshot, outcome, horizon) label"
        )
    observed: list[tuple[FutureEventLabel, int, float]] = []
    for label in labels:
        prediction = by_key[label_identity(label)]
        if prediction.unit != "probability":
            raise ValueError("binary warning prediction unit must be probability")
        if not isfinite(prediction.value) or not 0 <= prediction.value <= 1:
            raise ValueError("probability must be finite and between zero and one")
        if label.label is not None:
            observed.append((label, label.label, prediction.value))
    if not observed:
        raise ValueError("no observed labels are available for engineering evaluation")

    y = np.asarray([label_value for _, label_value, _ in observed], dtype=np.int64)
    p = np.asarray([value for _, _, value in observed], dtype=np.float64)
    predicted = p >= decision_threshold
    positive = y == 1
    negative = ~positive
    tp = int(np.sum(predicted & positive))
    fp = int(np.sum(predicted & negative))
    fn = int(np.sum(~predicted & positive))
    tn = int(np.sum(~predicted & negative))
    condition_briers = [
        float(
            np.mean(
                [
                    (value - label_value) ** 2
                    for label, label_value, value in observed
                    if label.condition_id == condition
                ]
            )
        )
        for condition in sorted({label.condition_id for label, _, _ in observed})
    ]
    return {
        "metric_scope": "engineering_only_snapshot_micro",
        "observed_count": int(y.size),
        "censored_count": len(labels) - int(y.size),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "recall": tp / (tp + fn) if tp + fn else None,
        "precision": tp / (tp + fp) if tp + fp else None,
        "false_alarm_rate": fp / (fp + tn) if fp + tn else None,
        "brier_score": float(np.mean((p - y) ** 2)),
        "condition_macro_brier_score": float(np.mean(condition_briers)),
        "average_precision": (
            float(average_precision_score(y, p)) if len(set(y.tolist())) == 2 else None
        ),
    }


class RecommendationLevel(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class PolicyStatus(StrEnum):
    BLOCKED = "blocked"
    PROVISIONAL = "provisional"
    FROZEN = "frozen"


@dataclass(frozen=True)
class RecommendationPolicy:
    policy_id: str
    status: PolicyStatus
    ageing_mode: AgeingMode
    future_usage_scenario: str
    rul_unit: str
    confirmed_red_soh_percent: float
    confirmed_orange_soh_percent: float
    yellow_probability: float
    orange_probability: float
    yellow_peer_abs_z: float
    yellow_rul: float
    orange_rul: float

    def __post_init__(self) -> None:
        if not isinstance(self.status, PolicyStatus):
            raise TypeError("policy status must use the registered PolicyStatus enum")
        _require_normalized_text(self.policy_id, "policy_id")
        _require_normalized_text(self.future_usage_scenario, "future_usage_scenario")
        _validate_mode_unit(self.ageing_mode, self.rul_unit, name="policy RUL")
        for name, value in (
            ("confirmed_red_soh_percent", self.confirmed_red_soh_percent),
            ("confirmed_orange_soh_percent", self.confirmed_orange_soh_percent),
            ("yellow_probability", self.yellow_probability),
            ("orange_probability", self.orange_probability),
            ("yellow_peer_abs_z", self.yellow_peer_abs_z),
            ("yellow_rul", self.yellow_rul),
            ("orange_rul", self.orange_rul),
        ):
            _require_finite_number(value, name)
        red_soh = float(self.confirmed_red_soh_percent)
        orange_soh = float(self.confirmed_orange_soh_percent)
        yellow_probability = float(self.yellow_probability)
        orange_probability = float(self.orange_probability)
        peer_z = float(self.yellow_peer_abs_z)
        yellow_rul = float(self.yellow_rul)
        orange_rul = float(self.orange_rul)
        if not 0 <= red_soh < orange_soh <= 100:
            raise ValueError("SoH thresholds must be within 0-100 and ordered red < orange")
        if not 0 <= yellow_probability < orange_probability <= 1:
            raise ValueError("probability thresholds must be ordered within zero and one")
        if peer_z <= 0:
            raise ValueError("peer absolute-z threshold must be positive")
        if not 0 < orange_rul < yellow_rul:
            raise ValueError("RUL thresholds must be positive and ordered")


@dataclass(frozen=True)
class WarningSignals:
    ageing_mode: AgeingMode
    future_usage_scenario: str
    rul_unit: str
    data_quality_ok: bool = True
    repeated_capacity_criterion_confirmed: bool = False
    confirmed_soh_percent: float | None = None
    threshold_crossing_probability: float | None = None
    rapid_degradation_probability: float | None = None
    peer_robust_z_score: float | None = None
    conditional_rul: float | None = None

    def __post_init__(self) -> None:
        _require_normalized_text(self.future_usage_scenario, "future_usage_scenario")
        _validate_mode_unit(self.ageing_mode, self.rul_unit, name="signal RUL")
        if not isinstance(self.data_quality_ok, bool):
            raise TypeError("data_quality_ok must be boolean")
        if not isinstance(self.repeated_capacity_criterion_confirmed, bool):
            raise TypeError("repeated_capacity_criterion_confirmed must be boolean")
        if self.repeated_capacity_criterion_confirmed and self.confirmed_soh_percent is None:
            raise ValueError("repeat confirmation requires an observed confirmed_soh_percent")
        for name in ("threshold_crossing_probability", "rapid_degradation_probability"):
            value = getattr(self, name)
            if value is not None and not 0 <= _require_finite_number(value, name) <= 1:
                raise ValueError(f"{name} must be between zero and one")
        for name in ("confirmed_soh_percent", "peer_robust_z_score", "conditional_rul"):
            value = getattr(self, name)
            if value is not None:
                _require_finite_number(value, name)
        if self.confirmed_soh_percent is not None and not 0 <= self.confirmed_soh_percent <= 100:
            raise ValueError("confirmed_soh_percent must be between 0 and 100")
        if self.conditional_rul is not None and self.conditional_rul < 0:
            raise ValueError("conditional_rul must be non-negative")


@dataclass(frozen=True)
class MaintenanceRecommendation:
    level: RecommendationLevel
    action: str
    reasons: tuple[str, ...]
    requires_human_review: bool
    policy_id: str
    policy_status: PolicyStatus


def recommend_maintenance(
    signals: WarningSignals,
    policy: RecommendationPolicy,
    *,
    allow_provisional_contract_demo: bool = False,
) -> MaintenanceRecommendation:
    """Map evidence to advice only under an explicit compatible policy."""
    if policy.status is PolicyStatus.FROZEN:
        raise ValueError("v0 action gate is closed; callers cannot self-authorize FROZEN policy")
    if policy.status is PolicyStatus.BLOCKED:
        raise ValueError("blocked recommendation policy cannot be used")
    if policy.status is PolicyStatus.PROVISIONAL and not allow_provisional_contract_demo:
        raise ValueError("provisional policy is allowed only in an explicit contract demo")
    if (
        signals.ageing_mode is not policy.ageing_mode
        or signals.future_usage_scenario != policy.future_usage_scenario
        or signals.rul_unit != policy.rul_unit
    ):
        raise ValueError("signal mode, scenario, and RUL unit must match the policy")

    def result(
        level: RecommendationLevel, action: str, reasons: Sequence[str]
    ) -> MaintenanceRecommendation:
        return MaintenanceRecommendation(
            level, action, tuple(reasons), True, policy.policy_id, policy.status
        )

    if not signals.data_quality_ok:
        return result(
            RecommendationLevel.YELLOW,
            "retest before changing operation",
            ["input data failed quality checks"],
        )
    if (
        signals.repeated_capacity_criterion_confirmed
        and signals.confirmed_soh_percent is not None
        and signals.confirmed_soh_percent <= policy.confirmed_red_soh_percent
    ):
        return result(
            RecommendationLevel.RED,
            "human service or replacement review",
            ["repeat-confirmed capacity SoH is at/below the critical threshold"],
        )
    orange: list[str] = []
    if (
        signals.confirmed_soh_percent is not None
        and signals.confirmed_soh_percent <= policy.confirmed_orange_soh_percent
    ):
        orange.append("confirmed capacity SoH reached the maintenance threshold")
    for label, value in (
        ("threshold-crossing", signals.threshold_crossing_probability),
        ("rapid-degradation", signals.rapid_degradation_probability),
    ):
        if value is not None and value >= policy.orange_probability:
            orange.append(f"{label} probability exceeds the orange policy threshold")
    if signals.conditional_rul is not None and signals.conditional_rul <= policy.orange_rul:
        orange.append("conditional RUL is within the mode/scenario-specific orange window")
    if orange:
        return result(RecommendationLevel.ORANGE, "inspect and plan maintenance", orange)
    yellow: list[str] = []
    for label, value in (
        ("threshold-crossing", signals.threshold_crossing_probability),
        ("rapid-degradation", signals.rapid_degradation_probability),
    ):
        if value is not None and value >= policy.yellow_probability:
            yellow.append(f"{label} probability exceeds the watch threshold")
    if (
        signals.peer_robust_z_score is not None
        and abs(signals.peer_robust_z_score) >= policy.yellow_peer_abs_z
    ):
        yellow.append("cell deviates from its fitted same-condition reference cohort")
    if signals.conditional_rul is not None and signals.conditional_rul <= policy.yellow_rul:
        yellow.append("conditional RUL is within the mode/scenario-specific watch window")
    if yellow:
        return result(
            RecommendationLevel.YELLOW, "shorten check interval and repeat diagnostic", yellow
        )
    return result(
        RecommendationLevel.GREEN,
        "continue under the evaluated usage scenario",
        ["no configured warning threshold triggered"],
    )


def synthetic_early_warning_contract_demo() -> dict[str, object]:
    """Run a deterministic contract demo; this is not a Gate-3 backend smoke."""
    from datetime import UTC, timedelta

    start = datetime(2026, 1, 1, tzinfo=UTC)
    q0 = Q0Provenance(
        capacity_ah=3.0,
        source_cell_id="demo-1",
        source_identity="synthetic:q0-row",
        source_archive="synthetic://contract-demo",
        source_member_or_file="capacity.csv",
        source_row_locator="row=1",
        measurement_timestamp=start,
        endpoint_selection_rule="synthetic first standardized endpoint",
        unit="Ah",
        source_manifest_hash="synthetic-contract-manifest",
        available_at=start,
    )
    history = [
        BatterySnapshot(
            cell_id="demo-1",
            condition_id="condition-A",
            checkpoint=index,
            completed_at=start + timedelta(days=7 * index),
            capacity_ah=capacity,
            q0=q0,
            ageing_mode=AgeingMode.CYCLIC,
            exposure=10.0 * index,
            exposure_unit="EFC",
            elapsed_days=7.0 * index,
            equivalent_full_cycles=10.0 * index,
            temperature_c=25.0,
            eis_features=(1.0 + index, 2.0 + index),
            eis_valid=True,
        )
        for index, capacity in enumerate((3.0, 2.79, 2.49, 2.25, 2.16))
    ]
    crossing = build_next_checkup_threshold_label(history, 1, horizon_checkups=3)
    policy = RecommendationPolicy(
        "synthetic-contract-policy",
        PolicyStatus.PROVISIONAL,
        AgeingMode.CYCLIC,
        "synthetic condition-A continuation",
        "EFC",
        70.0,
        80.0,
        0.35,
        0.70,
        2.0,
        25.0,
        10.0,
    )
    signals = WarningSignals(
        AgeingMode.CYCLIC,
        "synthetic condition-A continuation",
        "EFC",
        confirmed_soh_percent=history[1].soh_percent,
        threshold_crossing_probability=0.75,
        rapid_degradation_probability=0.72,
    )
    recommendation = recommend_maintenance(
        signals,
        policy,
        allow_provisional_contract_demo=True,
    )
    return {
        "synthetic_only": True,
        "contract_demo": True,
        "gate3_smoke": False,
        "formal_model_result": False,
        "scientific_result": False,
        "publication_ready": False,
        "resume_approved": False,
        "snapshot": {"cell_id": "demo-1", "checkpoint": 1},
        "threshold_horizon_checkups": crossing.horizon_checkups,
        "future_checkpoints_in_horizon": [2, 3, 4],
        "red_criterion_repeat_confirmed": signals.repeated_capacity_criterion_confirmed,
        "soh_percent": history[1].soh_percent,
        "threshold_label": crossing.status.value,
        "crossing_interval_checkpoints": [
            crossing.event_interval.last_above_key.checkpoint,
            crossing.event_interval.first_observed_below_key.checkpoint,
        ]
        if crossing.event_interval
        else None,
        "recommendation": recommendation.level.value,
        "action": recommendation.action,
        "requires_human_review": recommendation.requires_human_review,
        "policy_status": recommendation.policy_status.value,
    }
