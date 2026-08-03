"""Read-only KIT F1/O2 target-readiness audit.

This module performs no model fitting and never turns missing H3 follow-up into a
negative or zero-loss target.  Timestamp-derived quantities remain provisional
unless a separately reviewed completion policy is supplied.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from itertools import pairwise
from statistics import fmean, pstdev
from typing import Any

import numpy as np

from battery_eis_soh.kit_v2 import (
    CapacityEndpoint,
    KitV2DevelopmentRecords,
    Q0Record,
    VerifiedSnapshotCompletionPolicy,
    snapshot_completion_time,
)

MODE_BY_AGE_TYPE = {1: "calendar", 2: "cyclic", 3: "profile"}
O2_THRESHOLDS_PP = (4.0, 5.0, 6.0)
GRAY_BAND_CENTER_PP = 5.0
GRAY_BAND_HALF_WIDTH_PP = 0.5
MINIMUM_HISTORY_ENDPOINTS = 2


class TargetReadinessAuditError(ValueError):
    """Source records cannot support a deterministic readiness audit."""


def _finite(value: float, *, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise TargetReadinessAuditError(f"{name} must be finite")
    return result


def _summary(values: Iterable[float]) -> dict[str, float | int | None]:
    checked = [_finite(value, name="summary value") for value in values]
    if not checked:
        return {
            "count": 0,
            "minimum": None,
            "p05": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p95": None,
            "maximum": None,
            "mean": None,
        }
    quantiles = np.quantile(np.asarray(checked, dtype=np.float64), [0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        "count": len(checked),
        "minimum": min(checked),
        "p05": float(quantiles[0]),
        "p25": float(quantiles[1]),
        "median": float(quantiles[2]),
        "p75": float(quantiles[3]),
        "p95": float(quantiles[4]),
        "maximum": max(checked),
        "mean": fmean(checked),
    }


def _lineage_complete(record: Q0Record) -> bool:
    provenance = record.provenance
    return bool(
        record.capacity_ah > 0
        and provenance.archive_name
        and provenance.archive_sha256
        and provenance.member
        and provenance.line_number >= 2
        and provenance.timestamp_s >= 0
        and provenance.duration_s >= 0
    )


def _ordered_endpoints(
    endpoints: Sequence[CapacityEndpoint],
) -> dict[str, list[CapacityEndpoint]]:
    grouped: dict[str, list[CapacityEndpoint]] = defaultdict(list)
    for endpoint in endpoints:
        grouped[endpoint.cell.cell_id].append(endpoint)
    for cell_id, items in grouped.items():
        items.sort(
            key=lambda item: (
                item.provenance.timestamp_s,
                item.provenance.member,
                item.provenance.line_number,
            )
        )
        if any(
            right.provenance.timestamp_s < left.provenance.timestamp_s
            for left, right in pairwise(items)
        ):
            raise TargetReadinessAuditError(f"capacity endpoints are not ordered for {cell_id}")
    return grouped


def _mode_for_cell(endpoints: Sequence[CapacityEndpoint]) -> str:
    modes = {MODE_BY_AGE_TYPE.get(item.age_type, "unknown") for item in endpoints}
    if len(modes) != 1:
        return "mixed_or_unknown"
    return next(iter(modes))


def _completion_time(
    endpoint: CapacityEndpoint,
    policy: VerifiedSnapshotCompletionPolicy | None,
) -> float:
    if policy is None:
        return endpoint.provenance.timestamp_s
    return snapshot_completion_time(endpoint.provenance, policy)


def _condition_support(rows: Sequence[dict[str, Any]], threshold: float) -> dict[str, int]:
    positives = [row for row in rows if float(row["loss_pp_h3"]) >= threshold]
    negatives = [row for row in rows if float(row["loss_pp_h3"]) < threshold]
    return {
        "positive_snapshots": len(positives),
        "negative_snapshots": len(negatives),
        "positive_cells": len({str(row["cell_id"]) for row in positives}),
        "negative_cells": len({str(row["cell_id"]) for row in negatives}),
        "positive_conditions": len({int(row["condition_id"]) for row in positives}),
        "negative_conditions": len({int(row["condition_id"]) for row in negatives}),
    }


def audit_target_readiness(
    records: KitV2DevelopmentRecords,
    *,
    completion_policy: VerifiedSnapshotCompletionPolicy | None = None,
) -> dict[str, object]:
    """Audit F1/O2 readiness without fitting, selecting or reading protected data."""

    endpoints_by_cell = _ordered_endpoints(records.capacity_endpoints)
    q0_by_cell = {record.cell.cell_id: record for record in records.q0_records}
    if len(q0_by_cell) != len(records.q0_records):
        raise TargetReadinessAuditError("Q0 records are not one-to-one by physical cell")
    all_cells = set(endpoints_by_cell)
    missing_q0 = sorted(all_cells - set(q0_by_cell))
    extra_q0 = sorted(set(q0_by_cell) - all_cells)

    eligibility: dict[str, Counter[str]] = defaultdict(Counter)
    mode_cells: dict[str, set[str]] = defaultdict(set)
    mode_conditions: dict[str, set[int]] = defaultdict(set)
    f1_rows: list[dict[str, Any]] = []
    adjacent_changes_by_mode: dict[str, list[float]] = defaultdict(list)
    elapsed_h3_days: dict[str, list[float]] = defaultdict(list)
    h3_efc: dict[str, list[float]] = defaultdict(list)
    edge_no_h3_by_mode: Counter[str] = Counter()
    nonedge_no_h3_by_mode: Counter[str] = Counter()
    mixed_mode_cells: list[str] = []

    for cell_id, endpoints in sorted(endpoints_by_cell.items()):
        mode = _mode_for_cell(endpoints)
        if mode == "mixed_or_unknown":
            mixed_mode_cells.append(cell_id)
        condition_id = endpoints[0].cell.condition
        mode_cells[mode].add(cell_id)
        mode_conditions[mode].add(condition_id)
        q0_record = q0_by_cell.get(cell_id)
        if q0_record is None:
            eligibility[mode]["snapshots_missing_q0"] += len(endpoints)
            continue
        q0 = q0_record.capacity_ah
        soh = [100.0 * item.capacity_ah / q0 for item in endpoints]
        for left, right in pairwise(soh):
            adjacent_changes_by_mode[mode].append(right - left)
        for index, endpoint in enumerate(endpoints):
            eligibility[mode]["total_snapshots"] += 1
            history_ready = index >= MINIMUM_HISTORY_ENDPOINTS
            h3_index = index + 3
            h3_ready = h3_index < len(endpoints)
            if not history_ready:
                eligibility[mode]["insufficient_history"] += 1
            if not h3_ready:
                eligibility[mode]["no_h3_followup"] += 1
                if index >= max(0, len(endpoints) - 3):
                    edge_no_h3_by_mode[mode] += 1
                else:
                    nonedge_no_h3_by_mode[mode] += 1
                continue
            eligibility[mode]["h3_target_eligible"] += 1
            if not history_ready:
                continue
            eligibility[mode]["f1_model_candidate_eligible"] += 1
            future = endpoints[h3_index]
            current_soh = soh[index]
            future_soh = soh[h3_index]
            loss = current_soh - future_soh
            current_time = _completion_time(endpoint, completion_policy)
            future_time = _completion_time(future, completion_policy)
            elapsed_days = (future_time - current_time) / 86_400.0
            if elapsed_days < 0:
                raise TargetReadinessAuditError(f"negative H3 elapsed time for {cell_id}")
            current_efc = (endpoint.total_charge_ah + endpoint.total_discharge_ah) / (2.0 * q0)
            future_efc = (future.total_charge_ah + future.total_discharge_ah) / (2.0 * q0)
            delta_efc = future_efc - current_efc
            if delta_efc < -1e-9:
                raise TargetReadinessAuditError(f"decreasing cumulative EFC for {cell_id}")
            elapsed_h3_days[mode].append(elapsed_days)
            h3_efc[mode].append(delta_efc)
            f1_rows.append(
                {
                    "cell_id": cell_id,
                    "condition_id": condition_id,
                    "mode": mode,
                    "checkup_index": index,
                    "current_soh_percent": current_soh,
                    "future_soh_percent_h3": future_soh,
                    "loss_pp_h3": loss,
                    "elapsed_days_h3": elapsed_days,
                    "efc_h3": delta_efc,
                }
            )

    mode_names = tuple(sorted(set(eligibility) | set(mode_cells)))
    eligibility_report: dict[str, dict[str, int]] = {}
    for mode in mode_names:
        eligibility_report[mode] = {
            **dict(sorted(eligibility[mode].items())),
            "cells": len(mode_cells[mode]),
            "conditions": len(mode_conditions[mode]),
        }

    f1_by_mode: dict[str, object] = {}
    o2_by_mode: dict[str, object] = {}
    for mode in mode_names:
        rows = [row for row in f1_rows if row["mode"] == mode]
        losses = [float(row["loss_pp_h3"]) for row in rows]
        f1_by_mode[mode] = {
            "loss_pp_h3": _summary(losses),
            "negative_or_apparent_recovery_count": sum(value < 0 for value in losses),
            "exact_zero_count": sum(value == 0 for value in losses),
        }
        o2_by_mode[mode] = {
            f"threshold_{threshold:g}_pp": {
                **_condition_support(rows, threshold),
                "prevalence": (
                    sum(float(row["loss_pp_h3"]) >= threshold for row in rows) / len(rows)
                    if rows
                    else None
                ),
            }
            for threshold in O2_THRESHOLDS_PP
        }

    replicate_spread: list[float] = []
    by_condition_index: dict[tuple[int, int], list[float]] = defaultdict(list)
    for row in f1_rows:
        by_condition_index[(int(row["condition_id"]), int(row["checkup_index"]))].append(
            float(row["current_soh_percent"])
        )
    for values in by_condition_index.values():
        if len(values) >= 2:
            replicate_spread.append(pstdev(values))

    exact_repeat_candidates = Counter[tuple[str, float, int, int]]()
    for endpoint in records.capacity_endpoints:
        exact_repeat_candidates[
            (
                endpoint.cell.cell_id,
                endpoint.provenance.timestamp_s,
                endpoint.operating_cycles,
                endpoint.checkup_cycles,
            )
        ] += 1
    candidate_repeat_groups = sum(value > 1 for value in exact_repeat_candidates.values())

    q0_values = [record.capacity_ah for record in records.q0_records]
    q0_incomplete = [
        record.cell.cell_id for record in records.q0_records if not _lineage_complete(record)
    ]
    q0_ambiguous = [record.cell.cell_id for record in records.q0_records if record.ambiguity_flags]
    all_rows = f1_rows
    gray_rows = [
        row
        for row in all_rows
        if GRAY_BAND_CENTER_PP - GRAY_BAND_HALF_WIDTH_PP
        <= float(row["loss_pp_h3"])
        <= GRAY_BAND_CENTER_PP + GRAY_BAND_HALF_WIDTH_PP
    ]

    timestamp_status = (
        "accepted_with_evidence"
        if completion_policy is not None
        else "provisional_timestamp_field_only"
    )
    f1_blockers: list[str] = []
    if missing_q0 or q0_incomplete or q0_ambiguous or extra_q0:
        f1_blockers.append("Q0 provenance/inventory is incomplete")
    if mixed_mode_cells:
        f1_blockers.append("one or more cells have mixed/unknown ageing modes")
    if completion_policy is None:
        f1_blockers.append("snapshot completion semantics remain unverified")
    if not f1_rows:
        f1_blockers.append("no model-candidate H3 snapshots")

    o2_blockers = list(f1_blockers)
    o2_blockers.append(
        "capacity technical-repeat noise is not identifiable from the selected result endpoints"
    )
    if any(
        eligibility_report.get(mode, {}).get("conditions", 0) < 8
        for mode in ("calendar", "cyclic", "profile")
    ):
        o2_blockers.append("at least one ageing mode has fewer than eight condition groups")

    return {
        "artifact_schema_version": "kit_f1_o2_target_readiness_v1",
        "development_only": True,
        "model_fitting_performed": False,
        "target_selection_performed": False,
        "protected_data_accessed": False,
        "source": {
            "eoc_archive": records.eoc_archive,
            "eis_archive": records.eis_archive,
            "counts": records.counts,
        },
        "target": {
            "f1": "soh_loss_pp_h3 = SoH_t - SoH_t+3",
            "signed_no_clipping": True,
            "o2_candidate_thresholds_pp": list(O2_THRESHOLDS_PP),
            "history_requirement_prior_checkups": MINIMUM_HISTORY_ENDPOINTS,
            "timestamp_semantics": timestamp_status,
            "completion_policy_evidence_id": (
                completion_policy.evidence_id if completion_policy is not None else None
            ),
        },
        "q0": {
            "source_cells": len(all_cells),
            "records": len(records.q0_records),
            "missing_cells": missing_q0,
            "extra_cells": extra_q0,
            "incomplete_lineage_cells": sorted(q0_incomplete),
            "ambiguous_cells": sorted(q0_ambiguous),
            "capacity_ah": _summary(q0_values),
            "records_with_provenance": [asdict(record) for record in records.q0_records],
        },
        "snapshot_eligibility_by_mode": eligibility_report,
        "f1_distribution_by_mode": f1_by_mode,
        "o2_candidate_prevalence_by_mode": o2_by_mode,
        "o2_gray_band": {
            "definition": "descriptive_only_abs(loss_pp_h3 - 5.0) <= 0.5",
            "count": len(gray_rows),
            "cells": len({str(row["cell_id"]) for row in gray_rows}),
            "conditions": len({int(row["condition_id"]) for row in gray_rows}),
            "not_a_measurement_noise_estimate": True,
        },
        "capacity_repeatability_evidence": {
            "verified_technical_repeat_groups": 0,
            "exact_timestamp_and_counter_candidate_groups": candidate_repeat_groups,
            "candidate_groups_are_verified_technical_repeats": False,
            "adjacent_checkup_soh_change_pp_by_mode": {
                mode: _summary(values) for mode, values in sorted(adjacent_changes_by_mode.items())
            },
            "same_condition_physical_replicate_soh_sd_pp": _summary(replicate_spread),
            "physical_replicate_spread_is_measurement_repeatability": False,
            "conclusion": (
                "The selected result endpoints do not identify controlled technical repeats. "
                "Adjacent changes combine ageing and measurement variation; three cells per "
                "condition are physical replicates, not repeat measurements of one cell."
            ),
        },
        "h3_exposure_by_mode": {
            mode: {
                "elapsed_days": _summary(elapsed_h3_days.get(mode, [])),
                "equivalent_full_cycles": _summary(h3_efc.get(mode, [])),
                "distance_km": {
                    "status": "not_available_from_loaded_result_schema",
                    "inferred": False,
                },
            }
            for mode in mode_names
        },
        "right_edge_followup": {
            "no_h3_by_mode": dict(sorted(edge_no_h3_by_mode.items())),
            "nonedge_no_h3_by_mode": dict(sorted(nonedge_no_h3_by_mode.items())),
            "missing_h3_is_negative_or_zero": False,
        },
        "calendar_scarcity": {
            "cells": len(mode_cells.get("calendar", set())),
            "conditions": len(mode_conditions.get("calendar", set())),
            "candidate_snapshots": sum(1 for row in f1_rows if str(row["mode"]) == "calendar"),
            "requires_separate_mode_disposition": True,
        },
        "go_no_go": {
            "f1_development_oof": {
                "decision": "GO" if not f1_blockers else "NO_GO",
                "reasons": f1_blockers or ["Q0, H3 and completion semantics passed"],
            },
            "o2_candidate_reporting": {
                "decision": "GO" if not o2_blockers else "NO_GO",
                "reasons": o2_blockers or ["F1 readiness and noise/support review passed"],
            },
        },
    }
