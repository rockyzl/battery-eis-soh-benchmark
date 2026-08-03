from __future__ import annotations

import hashlib
import json
import warnings
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from sklearn.metrics import average_precision_score

from battery_eis_soh import kit_dev_training
from battery_eis_soh.kit_dev_training import (
    ALLOWED_EIS_SEQUENCES,
    ARTIFACT_NAMES,
    B_FEATURE_NAMES,
    EIS_FEATURE_NAMES,
    AcquisitionRecord,
    AnalysisPlan,
    DevelopmentRunError,
    DevelopmentSnapshot,
    EngineAnalysis,
    FoldPlan,
    _artifact_payloads,
    _equal_finite_mean,
    _f1_paired_outputs,
    _fit_regression_model,
    _fold_preprocess,
    _keep_last_key,
    _matrix,
    _paired_design_payload,
    _run_f1_oof_workflows,
    apply_platt,
    assert_no_champion_fields,
    condition_balanced_metrics,
    execute_engineering_analysis,
    fit_weighted_inner_oof_platt,
    fit_weighted_platt,
    hierarchical_weights,
    materialize_development_snapshots,
    paired_condition_bootstrap,
    paired_condition_regression_bootstrap,
    retry_sensitivity_stop_decision,
)
from battery_eis_soh.kit_dev_worker import validate_kit_f1_v2_artifact_tree
from battery_eis_soh.kit_v2 import (
    CapacityEndpoint,
    CellIdentity,
    EisPoint,
    EisSpectrum,
    KitV2DevelopmentRecords,
    Q0Record,
    RowProvenance,
)


def _object_dict(value: object) -> dict[str, object]:
    """Narrow a dynamic JSON-like object after checking its runtime shape."""

    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _provenance(
    cell: CellIdentity,
    timestamp: float,
    line: int,
    *,
    archive: str = "synthetic.zip",
) -> RowProvenance:
    return RowProvenance(archive, "a" * 64, cell.member, line, timestamp, 1, 10.0)


def _spectrum(
    cell: CellIdentity,
    timestamp: float,
    *,
    block: int,
    offset: float = 0.0,
    missing_seq: int | None = None,
) -> EisSpectrum:
    points: list[EisPoint] = []
    for seq in range(29):
        excluded = seq in {0, 13}
        missing = seq == missing_seq
        real = None if excluded or missing else offset + seq
        imaginary = None if excluded or missing else -(offset + seq)
        points.append(
            EisPoint(
                seq,
                20_000.0 / (1.5**seq),
                None if excluded else offset + seq + 2.0,
                None if excluded else -seq,
                None if excluded else offset + seq + 1.0,
                None if excluded else -seq,
                real,
                imaginary,
            )
        )
    return EisSpectrum(
        cell,
        block,
        1,
        1,
        50,
        1,
        _provenance(cell, timestamp, block + 2),
        tuple(points),
    )


def _records_for_aggregation() -> KitV2DevelopmentRecords:
    cell = CellIdentity(1, 1, 1, 1, "cell_eisv2_P001_1_S01_C01.csv")
    base = 1_700_000_000.0
    endpoints: list[CapacityEndpoint] = []
    spectra: list[EisSpectrum] = []
    for index in range(8):
        timestamp = base + index * 10 * 86_400.0
        provenance = _provenance(cell, timestamp, index + 2, archive="cell_eocv2.zip")
        endpoints.append(
            CapacityEndpoint(
                cell,
                3.0 - index * 0.02,
                provenance,
                2,
                25,
                0,
                0.5,
                1.0,
                0,
                index * 10,
                index,
                float(index * 6),
                float(index * 6),
            )
        )
        spectra.append(_spectrum(cell, timestamp + 100.0, block=index, offset=float(index)))
        if index == 2:
            spectra.extend(
                (
                    _spectrum(
                        cell,
                        timestamp + 24 * 3_600.0,
                        block=102,
                        offset=10.0,
                        missing_seq=27,
                    ),
                    _spectrum(
                        cell,
                        timestamp + 4 * 24 * 3_600.0,
                        block=202,
                        offset=30.0,
                    ),
                )
            )
    return KitV2DevelopmentRecords(
        {"path_name": "cell_eocv2.zip", "sha256": "a" * 64},
        {"path_name": "cell_eisv2.zip", "sha256": "b" * 64},
        {"real_cells": 1, "capacity_endpoints": 8, "valid_rt50_spectra": 10},
        (Q0Record(cell, endpoints[0].capacity_ah, endpoints[0].provenance, ()),),
        tuple(endpoints),
        tuple(spectra),
        tuple(20_000.0 / (1.5**seq) for seq in range(29)),
        {},
    )


def _snapshot(
    snapshot_id: str,
    *,
    condition: int,
    cell: str,
    checkup: int,
    label: int = 0,
) -> DevelopmentSnapshot:
    b = tuple(float(index + checkup) for index in range(len(B_FEATURE_NAMES)))
    eis = tuple(float(index + checkup) for index in range(len(EIS_FEATURE_NAMES)))
    loss = 6.0 if label else 1.0
    current_soh = 96.7
    future_soh = current_soh - loss
    return DevelopmentSnapshot(
        snapshot_id=snapshot_id,
        cell_id=cell,
        condition_id=condition,
        mode="cyclic",
        checkup_index=checkup,
        timestamp_s=float(checkup),
        q0_ah=3.0,
        capacity_ah=2.9,
        soh_percent=current_soh,
        future_soh_percent=future_soh,
        soh_loss_pp_h3=loss,
        h3_endpoint_snapshot_id=f"{snapshot_id}:h3",
        h3_checkup_index=checkup + 3,
        f1_target_available=True,
        eligibility_status="eligible_f1_and_d_core",
        o2_label=label,
        b_values=b,
        primary_eis_values=eis,
        keep_last_eis_values=tuple(value + 1.0 for value in eis),
        acquisition_ids=(f"ACQ-{snapshot_id}",),
        finite_acquisition_count_by_feature=(1,) * len(EIS_FEATURE_NAMES),
        keep_last_acquisition_id=f"ACQ-{snapshot_id}",
    )


def test_feature_contract_is_54_compensated_values_without_source_shortcuts() -> None:
    assert len(ALLOWED_EIS_SEQUENCES) == 27
    assert 0 not in ALLOWED_EIS_SEQUENCES
    assert 13 not in ALLOWED_EIS_SEQUENCES
    assert len(EIS_FEATURE_NAMES) == 54
    all_names = " ".join((*B_FEATURE_NAMES, *EIS_FEATURE_NAMES))
    assert "soh_cap" not in all_names
    assert "soh_imp" not in all_names
    assert "z_ref" not in all_names


def test_equal_finite_mean_preserves_missing_channel_instead_of_synthesizing() -> None:
    first = tuple(float(index) for index in range(len(EIS_FEATURE_NAMES)))
    second = [float(index + 10) for index in range(len(EIS_FEATURE_NAMES))]
    second[3] = float("nan")
    third = [float(index + 20) for index in range(len(EIS_FEATURE_NAMES))]
    third[3] = float("nan")
    first_missing = list(first)
    first_missing[3] = float("nan")
    means, counts = _equal_finite_mean((tuple(first_missing), tuple(second), tuple(third)))
    assert counts[0] == 3
    assert means[0] == pytest.approx(10.0)
    assert counts[3] == 0
    assert np.isnan(means[3])


def test_materialization_aggregates_same_cell_and_aligned_checkup_with_full_lineage() -> None:
    snapshots, acquisitions, report = materialize_development_snapshots(_records_for_aggregation())
    checkup_two = next(item for item in snapshots if item.checkup_index == 2)
    assert len(checkup_two.acquisition_ids) == 3
    assert checkup_two.finite_acquisition_count_by_feature[0] == 3
    # First retained channel is seq=1: the three fixture values are 2+1, 10+1, 30+1.
    assert checkup_two.primary_eis_values[0] == pytest.approx((3.0 + 11.0 + 31.0) / 3)
    seq_27_offset = ALLOWED_EIS_SEQUENCES.index(27) * 2
    assert checkup_two.finite_acquisition_count_by_feature[seq_27_offset] == 2
    assert checkup_two.primary_eis_values[seq_27_offset] == pytest.approx((29.0 + 57.0) / 2)
    assert checkup_two.keep_last_eis_values[0] == pytest.approx(30.0 + 1.0)
    lineage = [item for item in acquisitions if item.acquisition_id in checkup_two.acquisition_ids]
    assert len(lineage) == 3
    assert all(item.aligned_snapshot_id == checkup_two.snapshot_id for item in lineage)
    assert all(item.source_archive and item.source_archive_sha256 == "a" * 64 for item in lineage)
    assert all(item.source_member and item.source_line > 0 for item in lineage)
    assert all(item.duration_s == 10.0 and item.timestamp_origin == 1 for item in lineage)
    assert report["eligible_acquisitions"] == 10


def test_aggregation_is_source_order_invariant() -> None:
    records = _records_for_aggregation()
    forward_snapshots, forward_acquisitions, _ = materialize_development_snapshots(records)
    reversed_records = replace(
        records,
        capacity_endpoints=tuple(reversed(records.capacity_endpoints)),
        eis_spectra=tuple(reversed(records.eis_spectra)),
    )
    reverse_snapshots, reverse_acquisitions, _ = materialize_development_snapshots(reversed_records)
    assert len(forward_snapshots) == len(reverse_snapshots)
    for forward, reverse in zip(forward_snapshots, reverse_snapshots, strict=True):
        assert replace(
            forward,
            b_values=(),
            primary_eis_values=(),
            keep_last_eis_values=(),
        ) == replace(
            reverse,
            b_values=(),
            primary_eis_values=(),
            keep_last_eis_values=(),
        )
        np.testing.assert_allclose(forward.b_values, reverse.b_values, equal_nan=True)
        np.testing.assert_allclose(
            forward.primary_eis_values, reverse.primary_eis_values, equal_nan=True
        )
        np.testing.assert_allclose(
            forward.keep_last_eis_values, reverse.keep_last_eis_values, equal_nan=True
        )
    assert [item.acquisition_id for item in forward_acquisitions] == [
        item.acquisition_id for item in reverse_acquisitions
    ]
    for forward_acquisition, reverse_acquisition in zip(
        forward_acquisitions, reverse_acquisitions, strict=True
    ):
        assert replace(forward_acquisition, eis_values=()) == replace(
            reverse_acquisition, eis_values=()
        )
        np.testing.assert_allclose(
            forward_acquisition.eis_values, reverse_acquisition.eis_values, equal_nan=True
        )


def test_extra_acquisition_changes_aggregate_but_not_snapshot_identity_or_weight() -> None:
    records = _records_for_aggregation()
    baseline, _, _ = materialize_development_snapshots(records)
    cell = records.eis_spectra[0].cell
    checkup_two_time = records.capacity_endpoints[2].provenance.timestamp_s
    extra = _spectrum(
        cell,
        checkup_two_time + 5 * 24 * 3_600.0,
        block=302,
        offset=50.0,
    )
    expanded, _, _ = materialize_development_snapshots(
        replace(records, eis_spectra=(*records.eis_spectra, extra))
    )
    assert [item.snapshot_id for item in expanded] == [item.snapshot_id for item in baseline]
    np.testing.assert_array_equal(hierarchical_weights(expanded), hierarchical_weights(baseline))
    before = next(item for item in baseline if item.checkup_index == 2)
    after = next(item for item in expanded if item.checkup_index == 2)
    assert len(after.acquisition_ids) == len(before.acquisition_ids) + 1
    assert after.primary_eis_values != before.primary_eis_values


def test_sixty_hour_rule_is_flag_only_and_never_groups_or_drops() -> None:
    snapshots, acquisitions, report = materialize_development_snapshots(_records_for_aggregation())
    checkup_two = next(item for item in snapshots if item.checkup_index == 2)
    aligned = [item for item in acquisitions if item.aligned_snapshot_id == checkup_two.snapshot_id]
    assert len(aligned) == 3
    assert sum(item.within_60h_candidate_only for item in aligned) == 1
    assert sum(item.same_aligned_checkup_as_previous for item in aligned) == 1
    assert report["within_60h_candidate_count"] == 1
    policy = report["policy"]
    assert isinstance(policy, dict)
    assert policy["retry_window_role"] == "flag_only_never_group_or_drop"
    assert len(checkup_two.acquisition_ids) == 3
    checkup_three = next(item for item in snapshots if item.checkup_index == 3)
    assert len(checkup_three.acquisition_ids) == 1
    assert set(checkup_two.acquisition_ids).isdisjoint(checkup_three.acquisition_ids)


def test_keep_last_selection_is_whole_acquisition_and_has_stable_tie_break() -> None:
    base = AcquisitionRecord(
        acquisition_id="ACQ-a",
        cell_id="cell-a",
        condition_id=1,
        timestamp_s=100.0,
        timestamp_origin=1,
        duration_s=10.0,
        sd_block_id=5,
        source_archive="eis.zip",
        source_archive_sha256="a" * 64,
        source_member="cell.csv",
        source_line=9,
        aligned_snapshot_id="cell-a:checkup-2",
        within_60h_candidate_only=True,
        same_aligned_checkup_as_previous=True,
        within_60h_candidate_reason="within_60h_and_same_aligned_checkup_candidate_only",
        eis_values=(1.0,) * len(EIS_FEATURE_NAMES),
    )
    tied_later = replace(base, acquisition_id="ACQ-b", source_line=10, eis_values=(2.0,) * 54)
    selected = max((base, tied_later), key=_keep_last_key)
    assert selected.acquisition_id == "ACQ-b"
    assert selected.eis_values == (2.0,) * 54


def test_hierarchical_weights_equalize_condition_cell_and_checkup_mass() -> None:
    snapshots = [
        _snapshot("c1-a-1", condition=1, cell="a", checkup=1),
        _snapshot("c1-a-2", condition=1, cell="a", checkup=2),
        _snapshot("c1-b-1", condition=1, cell="b", checkup=1),
        _snapshot("c2-c-1", condition=2, cell="c", checkup=1),
    ]
    weights = hierarchical_weights(snapshots)
    assert weights.tolist() == pytest.approx([0.5, 0.5, 1.0, 2.0])
    assert float(np.mean(weights)) == pytest.approx(1.0)
    condition_mass = {
        condition: float(
            sum(
                weight for item, weight in zip(snapshots, weights) if item.condition_id == condition
            )
        )
        for condition in (1, 2)
    }
    assert condition_mass[1] == pytest.approx(condition_mass[2])
    assert weights[0] + weights[1] == pytest.approx(weights[2])


def test_hierarchical_weights_are_recomputed_inside_each_partition() -> None:
    snapshots = [
        _snapshot("c1-a-1", condition=1, cell="a", checkup=1),
        _snapshot("c1-a-2", condition=1, cell="a", checkup=2),
        _snapshot("c1-b-1", condition=1, cell="b", checkup=1),
        _snapshot("c2-c-1", condition=2, cell="c", checkup=1),
    ]
    subset = [snapshots[0], snapshots[2], snapshots[3]]
    assert hierarchical_weights(subset).tolist() == pytest.approx([0.75, 0.75, 1.5])


def test_b_and_d_core_share_exact_ids_labels_groups_and_weights() -> None:
    snapshots = [
        _snapshot("one", condition=1, cell="a", checkup=1, label=0),
        _snapshot("two", condition=2, cell="b", checkup=1, label=1),
    ]
    snapshot_ids = tuple(item.snapshot_id for item in snapshots)
    common_weights = hierarchical_weights(snapshots)
    b_x, b_y, b_groups, _ = _matrix(snapshots, "B", "primary")
    d_x, d_y, d_groups, _ = _matrix(snapshots, "D_CORE", "primary")
    assert b_x.shape[0] == d_x.shape[0] == len(snapshots)
    assert snapshot_ids == tuple(item.snapshot_id for item in snapshots)
    np.testing.assert_array_equal(b_y, d_y)
    np.testing.assert_array_equal(b_groups, d_groups)
    np.testing.assert_array_equal(common_weights, hierarchical_weights(snapshots))


def test_u11_condition_balanced_ap_brier_and_support_use_hierarchical_weights() -> None:
    snapshots = [
        _snapshot("c1-a-1", condition=1, cell="a", checkup=1, label=0),
        _snapshot("c1-a-2", condition=1, cell="a", checkup=2, label=1),
        _snapshot("c1-b-1", condition=1, cell="b", checkup=1, label=0),
        _snapshot("c2-c-1", condition=2, cell="c", checkup=1, label=1),
    ]
    probabilities = np.asarray([0.1, 0.9, 0.2, 0.8])
    labels = np.asarray([0, 1, 0, 1])
    weights = np.asarray([0.5, 0.5, 1.0, 2.0])
    result = condition_balanced_metrics(snapshots, probabilities)
    assert result["condition_balanced_pooled_pr_auc_v1"] == pytest.approx(
        average_precision_score(labels, probabilities, sample_weight=weights)
    )
    assert result["condition_balanced_pooled_brier_v1"] == pytest.approx(
        np.average((probabilities - labels) ** 2, weights=weights)
    )
    assert result["events"] == 2
    assert result["cells"] == 3
    assert result["conditions"] == 2
    assert result["positive_supporting_conditions"] == 2
    assert result["negative_supporting_conditions"] == 1


def test_u12_condition_balanced_metrics_reject_bad_probability_or_label_contracts() -> None:
    snapshots = [
        _snapshot("one", condition=1, cell="a", checkup=1, label=0),
        _snapshot("two", condition=2, cell="b", checkup=1, label=1),
    ]
    with pytest.raises(DevelopmentRunError, match="aligned"):
        condition_balanced_metrics(snapshots, np.asarray([0.2]))
    with pytest.raises(DevelopmentRunError, match=r"within \[0, 1\]"):
        condition_balanced_metrics(snapshots, np.asarray([0.2, 1.2]))
    with pytest.raises(DevelopmentRunError, match="both classes"):
        condition_balanced_metrics(
            [replace(item, o2_label=0) for item in snapshots], np.asarray([0.2, 0.3])
        )


def test_u13_weighted_platt_is_deterministic_and_weights_affect_the_fit() -> None:
    probabilities = np.asarray([0.1, 0.3, 0.7, 0.9])
    labels = np.asarray([0, 1, 0, 1])
    weighted = np.asarray([1.0, 8.0, 1.0, 1.0])
    first = fit_weighted_platt(probabilities, labels, weighted)
    second = fit_weighted_platt(probabilities, labels, weighted)
    unweighted = fit_weighted_platt(probabilities, labels, np.ones(4))
    assert first == second
    assert (first.slope, first.intercept) != pytest.approx((unweighted.slope, unweighted.intercept))
    calibrated = apply_platt(probabilities, first)
    assert np.all(np.isfinite(calibrated))
    assert np.all((calibrated >= 0.0) & (calibrated <= 1.0))


def test_u14_inner_oof_platt_rejects_missing_duplicate_or_reordered_training_ids() -> None:
    probabilities = np.asarray([0.1, 0.3, 0.7, 0.9])
    labels = np.asarray([0, 1, 0, 1])
    weights = np.ones(4)
    with pytest.raises(DevelopmentRunError, match="align exactly"):
        fit_weighted_inner_oof_platt(
            probabilities,
            labels,
            weights,
            prediction_snapshot_ids=("b", "a", "c", "d"),
            outer_training_snapshot_ids=("a", "b", "c", "d"),
            forbidden_outer_validation_snapshot_ids=("x",),
        )
    with pytest.raises(DevelopmentRunError, match="unique"):
        fit_weighted_inner_oof_platt(
            probabilities,
            labels,
            weights,
            prediction_snapshot_ids=("a", "a", "c", "d"),
            outer_training_snapshot_ids=("a", "b", "c", "d"),
            forbidden_outer_validation_snapshot_ids=("x",),
        )


def test_u15_inner_oof_platt_rejects_outer_validation_leakage() -> None:
    ids = ("a", "b", "c", "d")
    with pytest.raises(DevelopmentRunError, match="outer-validation"):
        fit_weighted_inner_oof_platt(
            np.asarray([0.1, 0.3, 0.7, 0.9]),
            np.asarray([0, 1, 0, 1]),
            np.ones(4),
            prediction_snapshot_ids=ids,
            outer_training_snapshot_ids=ids,
            forbidden_outer_validation_snapshot_ids=("d", "e"),
        )


def _bootstrap_snapshots() -> list[DevelopmentSnapshot]:
    return [
        _snapshot(
            f"c{condition}-{label}",
            condition=condition,
            cell=f"cell-{condition}",
            checkup=label,
            label=label,
        )
        for condition in range(1, 5)
        for label in (0, 1)
    ]


def test_u16_paired_condition_bootstrap_is_exact_2000_draw_and_reproducible() -> None:
    snapshots = _bootstrap_snapshots()
    b = np.asarray([0.2, 0.7, 0.3, 0.6, 0.4, 0.55, 0.35, 0.65])
    d_core = np.asarray([0.1, 0.8, 0.2, 0.7, 0.3, 0.65, 0.25, 0.75])
    first = paired_condition_bootstrap(snapshots, b, d_core)
    second = paired_condition_bootstrap(snapshots, b, d_core)
    assert first == second
    assert first["draws"] == 2000
    assert first["seed"] == 20260801
    assert first["valid_draws"] == 2000
    assert first["interval_valid"] is True
    assert first["pr_auc_delta_percentile_95"] is not None
    assert first["brier_delta_percentile_95"] is not None


def test_u17_bootstrap_invalidates_interval_below_1900_valid_draws() -> None:
    snapshots = [
        _snapshot("negative", condition=1, cell="a", checkup=1, label=0),
        _snapshot("positive", condition=2, cell="b", checkup=1, label=1),
    ]
    result = paired_condition_bootstrap(
        snapshots,
        np.asarray([0.2, 0.8]),
        np.asarray([0.1, 0.9]),
    )
    valid_draws = result["valid_draws"]
    assert isinstance(valid_draws, int)
    assert valid_draws < 1900
    assert result["interval_valid"] is False
    assert result["pr_auc_delta_percentile_95"] is None
    assert result["brier_delta_percentile_95"] is None


def test_u18_bootstrap_rejects_alignment_probability_and_frozen_packet_drift() -> None:
    snapshots = _bootstrap_snapshots()
    values = np.linspace(0.1, 0.9, len(snapshots))
    with pytest.raises(DevelopmentRunError, match="not aligned"):
        paired_condition_bootstrap(snapshots, values[:-1], values)
    with pytest.raises(DevelopmentRunError, match="frozen"):
        paired_condition_bootstrap(snapshots, values, values, draws=1999)
    invalid = values.copy()
    invalid[0] = float("nan")
    with pytest.raises(DevelopmentRunError, match="finite"):
        paired_condition_bootstrap(snapshots, invalid, values)


def test_f1_paired_whole_condition_bootstrap_packet_is_exact_and_reproducible() -> None:
    snapshots = _bootstrap_snapshots()
    b = np.asarray([0.8, 5.2, 1.1, 5.0, 1.4, 4.8, 1.0, 5.1])
    d_core = np.asarray([0.9, 5.8, 1.0, 5.7, 1.2, 5.5, 0.8, 5.9])
    first = paired_condition_regression_bootstrap(snapshots, b, d_core)
    second = paired_condition_regression_bootstrap(snapshots, b, d_core)
    assert first == second
    assert first == {
        **first,
        "method": "whole_condition_paired_bootstrap_v1",
        "draws": 2000,
        "valid_draws": 2000,
        "minimum_valid_draws": 1900,
        "seed": 20260801,
        "interval_percent": 95,
        "interval_valid": True,
    }
    valid_draws = first["valid_draws"]
    minimum_valid_draws = first["minimum_valid_draws"]
    mae_interval = first["mae_delta_percentile_95"]
    rmse_interval = first["rmse_delta_percentile_95"]
    assert isinstance(valid_draws, int)
    assert isinstance(minimum_valid_draws, int)
    assert isinstance(mae_interval, list)
    assert isinstance(rmse_interval, list)
    assert valid_draws >= minimum_valid_draws
    assert len(mae_interval) == 2
    assert len(rmse_interval) == 2


def test_f1_paired_outputs_attach_frozen_bootstrap_to_every_paired_subset() -> None:
    snapshots = _bootstrap_snapshots()
    predictions: list[dict[str, object]] = []
    for family, family_offset in (
        ("elastic_net_regression", 0.1),
        ("hist_gradient_boosting_regressor", 0.2),
    ):
        for policy, view, view_offset in (
            ("primary", "B", 0.0),
            ("primary", "D_CORE", 0.3),
            ("keep_last_sensitivity", "D_CORE", 0.4),
        ):
            for snapshot in snapshots:
                assert snapshot.soh_loss_pp_h3 is not None
                predictions.append(
                    {
                        "task_id": "F1_SOH_LOSS_PP_H3",
                        "snapshot_id": snapshot.snapshot_id,
                        "mode": snapshot.mode,
                        "acquisition_policy": policy,
                        "view": view,
                        "model_family": family,
                        "predicted_soh_loss_pp_h3": float(
                            snapshot.soh_loss_pp_h3 + family_offset + view_offset
                        ),
                    }
                )
    paired, _ = _f1_paired_outputs(snapshots, predictions, AnalysisPlan())
    assert len(paired) == 4
    for row in paired:
        bootstrap = row["bootstrap"]
        assert isinstance(bootstrap, dict)
        assert bootstrap["method"] == "whole_condition_paired_bootstrap_v1"
        assert bootstrap["draws"] == bootstrap["valid_draws"] == 2000
        assert bootstrap["minimum_valid_draws"] == 1900
        assert bootstrap["valid_draws"] >= bootstrap["minimum_valid_draws"]
        assert bootstrap["interval_valid"] is True


def test_u19_retry_sensitivity_applies_strict_margins_and_sign_reversal() -> None:
    boundary = retry_sensitivity_stop_decision(
        primary_pr_auc_delta=0.01,
        keep_last_pr_auc_delta=0.03,
        primary_brier_delta=0.0,
        keep_last_brier_delta=0.01,
    )
    assert boundary["prohibit_eis_increment_conclusion"] is False
    above = retry_sensitivity_stop_decision(
        primary_pr_auc_delta=0.01,
        keep_last_pr_auc_delta=0.030001,
        primary_brier_delta=0.0,
        keep_last_brier_delta=0.010001,
    )
    assert above["prohibit_eis_increment_conclusion"] is True
    reversed_sign = retry_sensitivity_stop_decision(
        primary_pr_auc_delta=0.001,
        keep_last_pr_auc_delta=-0.001,
        primary_brier_delta=0.0,
        keep_last_brier_delta=0.0,
    )
    assert reversed_sign["pr_auc_delta_sign_reversal"] is True
    assert reversed_sign["prohibit_eis_increment_conclusion"] is True


def test_u20_no_champion_guard_is_recursive_and_allows_false_selection_flag() -> None:
    assert_no_champion_fields(
        {"authorization": {"selection_performed": False}, "models": [{"role": "robustness"}]}
    )
    with pytest.raises(DevelopmentRunError, match=r"\$\.nested\[0\]\.winner"):
        assert_no_champion_fields({"nested": [{"winner": "model-a"}]})


def _two_fold_plan() -> list[FoldPlan]:
    return [
        FoldPlan(
            outer_fold=0,
            train_indices=(0, 1),
            validation_indices=(2, 3),
            inner_folds=(((0,), (1,)), ((1,), (0,))),
        ),
        FoldPlan(
            outer_fold=1,
            train_indices=(2, 3),
            validation_indices=(0, 1),
            inner_folds=(((2,), (3,)), ((3,), (2,))),
        ),
    ]


def _mode_cohort(mode: str, *, condition_offset: int = 0) -> list[DevelopmentSnapshot]:
    return [
        replace(
            _snapshot(
                f"{mode}-{index}",
                condition=condition_offset + index + 1,
                cell=f"{mode}-cell-{index}",
                checkup=0,
                label=index % 2,
            ),
            mode=mode,
        )
        for index in range(4)
    ]


def _synthetic_f1_cohort_with_missing_eis() -> list[DevelopmentSnapshot]:
    eligible = [
        replace(
            _snapshot(
                f"synthetic-{mode}-{condition}",
                condition=mode_index * 100 + condition,
                cell=f"synthetic-{mode}-cell-{condition}",
                checkup=condition,
                label=condition % 2,
            ),
            mode=mode,
        )
        for mode_index, mode in enumerate(("calendar", "cyclic", "profile"))
        for condition in range(1, 10)
    ]
    missing = replace(
        _snapshot(
            "synthetic-missing-eis",
            condition=999,
            cell="synthetic-cyclic-cell-missing",
            checkup=10,
            label=0,
        ),
        mode="cyclic",
        eligibility_status="missing_eis",
        primary_eis_values=(float("nan"),) * len(EIS_FEATURE_NAMES),
        keep_last_eis_values=(float("nan"),) * len(EIS_FEATURE_NAMES),
        acquisition_ids=(),
        finite_acquisition_count_by_feature=(0,) * len(EIS_FEATURE_NAMES),
        keep_last_acquisition_id=None,
    )
    return [*eligible, missing]


def test_c01_paired_design_captures_exact_ids_partition_weights_and_folds() -> None:
    cohort = [
        _snapshot("c1-a-1", condition=1, cell="a", checkup=1, label=0),
        _snapshot("c1-b-1", condition=1, cell="b", checkup=1, label=1),
        _snapshot("c2-c-1", condition=2, cell="c", checkup=1, label=0),
        _snapshot("c2-c-2", condition=2, cell="c", checkup=2, label=1),
    ]
    first = _paired_design_payload(cohort, _two_fold_plan())
    second = _paired_design_payload(cohort, _two_fold_plan())
    assert first == second
    assert first["fold_assignment_task_independent"] is True
    assert first["applies_to_tasks"] == [
        "F1_SOH_LOSS_PP_H3",
        "O2_RAPID_LOSS_5PP_H3",
    ]
    assert first["b_cohort_snapshot_ids"] == [item.snapshot_id for item in cohort]
    assert first["d_core_eligible_snapshot_ids"] == [item.snapshot_id for item in cohort]
    assert first["paired_comparison_subset"] == "d_core_eligible_intersection_only"
    assert first["ordered_cohort_snapshot_ids"] == [item.snapshot_id for item in cohort]
    metric_weights = first["metric_weights"]
    assert isinstance(metric_weights, list)
    assert [row["weight_mean_one"] for row in metric_weights] == pytest.approx(
        hierarchical_weights(cohort)
    )
    folds = first["folds"]
    assert isinstance(folds, list)
    assert len(folds) == 2
    assert all("training_weights" in fold and "validation_weights" in fold for fold in folds)
    assert isinstance(first["paired_design_sha256"], str)


def test_c01b_b_retains_exact_h3_missing_eis_rows_while_d_and_pairing_use_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = _synthetic_f1_cohort_with_missing_eis()
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training.materialize_development_snapshots",
        lambda records: (
            snapshots,
            [],
            {
                "counts": {
                    "eligible_f1_and_d_core": len(snapshots) - 1,
                    "missing_eis": 1,
                },
                "eligible_acquisitions": len(snapshots) - 1,
                "within_60h_candidate_count": 0,
                "policy": {
                    "primary": "equal_finite_mean_per_frequency_v1",
                    "sensitivity": "keep_last_acquisition_v1",
                    "retry_window_seconds": 216000.0,
                    "retry_window_role": "flag_only_never_group_or_drop",
                    "timestamp_semantics_provisional": True,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training._fit_regression_model",
        lambda family, x_train, y_train, train_weights, x_evaluation, names: np.full(
            len(x_evaluation), float(np.mean(y_train))
        ),
    )
    analysis = kit_dev_training.analyze_records(
        _records_for_aggregation(), plan=AnalysisPlan(), progress=lambda event: None
    )
    missing_id = "synthetic-missing-eis"
    candidate_rows = [
        row for row in analysis.predictions if row["model_role"] == "fitted_candidate"
    ]
    for family in ("elastic_net_regression", "hist_gradient_boosting_regressor"):
        b_ids = {
            row["snapshot_id"]
            for row in candidate_rows
            if row["view"] == "B" and row["model_family"] == family
        }
        d_ids = {
            row["snapshot_id"]
            for row in candidate_rows
            if row["view"] == "D_CORE" and row["model_family"] == family
        }
        assert missing_id in b_ids
        assert missing_id not in d_ids
        assert d_ids == {snapshot.snapshot_id for snapshot in snapshots[:-1]}
    assert all(row["same_snapshot_ids_and_weights"] is True for row in analysis.paired_deltas)


def test_c02_exact_fixed_estimator_packets_and_sample_weights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, object], np.ndarray]] = []

    class FakeEstimator:
        def __init__(self, *, family: str, **parameters: object) -> None:
            self.family = family
            self.parameters = parameters

        def fit(self, x: np.ndarray, y: np.ndarray, *, sample_weight: np.ndarray) -> FakeEstimator:
            del x, y
            captured.append((self.family, self.parameters, sample_weight.copy()))
            return self

        def predict(self, x: np.ndarray) -> np.ndarray:
            return np.full(len(x), 0.5)

    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training.ElasticNet",
        lambda **parameters: FakeEstimator(family="elastic_net", **parameters),
    )
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training.HistGradientBoostingRegressor",
        lambda **parameters: FakeEstimator(family="hgb_regressor", **parameters),
    )
    x = np.asarray([[0.0, 1.0], [1.0, 0.0], [2.0, 1.0], [3.0, 0.0]])
    y = np.asarray([-1.0, 0.5, 1.0, 2.5])
    weights = np.asarray([0.5, 1.5, 0.5, 1.5])
    _fit_regression_model("elastic_net_regression", x, y, weights, x[:2], ("a", "b"))
    _fit_regression_model("hist_gradient_boosting_regressor", x, y, weights, x[:2], ("a", "b"))
    linear = captured[0]
    assert linear[1] == {
        "alpha": 0.01,
        "l1_ratio": 0.1,
        "fit_intercept": True,
        "max_iter": 250000,
        "tol": 0.00001,
        "selection": "cyclic",
        "random_state": 20260801,
    }
    frozen_config = json.loads(
        (Path("config") / "kit_f1_o2_development_v2.json").read_text(encoding="utf-8")
    )
    assert linear[1] == frozen_config["models"]["elastic_net_regression"]
    hgb = captured[1]
    assert hgb[1] == {
        "learning_rate": 0.05,
        "max_leaf_nodes": 7,
        "loss": "squared_error",
        "max_iter": 100,
        "min_samples_leaf": 20,
        "l2_regularization": 1.0,
        "early_stopping": False,
        "random_state": 20260801,
    }
    np.testing.assert_array_equal(linear[2], weights)
    np.testing.assert_array_equal(hgb[2], weights)


def test_fold_preprocess_is_finite_for_missing_and_zero_scale_columns() -> None:
    train = np.asarray(
        [
            [np.nan, 1.0, 4.0, 7.0],
            [np.nan, 2.0, 4.0, 7.0],
            [np.nan, np.nan, 4.0, 7.0],
        ],
        dtype=np.float64,
    )
    evaluation = np.asarray(
        [[np.nan, np.nan, 4.0, 7.0], [np.nan, 3.0, 4.0, 7.0]],
        dtype=np.float64,
    )
    weights = np.asarray([1.0, 2.0, 1.0], dtype=np.float64)

    train_ready, evaluation_ready, names = _fold_preprocess(
        train,
        evaluation,
        weights,
        ("all_missing", "partial_missing", "constant", "zero_scale"),
        standardize=True,
    )

    assert np.all(np.isfinite(train_ready))
    assert np.all(np.isfinite(evaluation_ready))
    assert names == [
        "all_missing",
        "partial_missing",
        "constant",
        "zero_scale",
        "all_missing__missing",
        "partial_missing__missing",
    ]
    np.testing.assert_allclose(train_ready[:, 0], 0.0)
    np.testing.assert_allclose(train_ready[:, 2], 0.0)


@pytest.mark.parametrize(
    ("train", "evaluation", "weights"),
    [
        (
            np.asarray([[0.0, np.inf], [1.0, 2.0]]),
            np.asarray([[0.0, 1.0]]),
            np.asarray([1.0, 1.0]),
        ),
        (
            np.asarray([[0.0, 1.0], [1.0, 2.0]]),
            np.asarray([[0.0, -np.inf]]),
            np.asarray([1.0, 1.0]),
        ),
        (
            np.asarray([[0.0, 1.0], [1.0, 2.0]]),
            np.asarray([[0.0, 1.0]]),
            np.asarray([1.0, 0.0]),
        ),
    ],
)
def test_fold_preprocess_rejects_nonfinite_values_or_invalid_weights(
    train: np.ndarray,
    evaluation: np.ndarray,
    weights: np.ndarray,
) -> None:
    with pytest.raises(DevelopmentRunError) as caught:
        _fold_preprocess(
            train,
            evaluation,
            weights,
            ("a", "b"),
            standardize=True,
        )
    assert caught.value.error_code == "invalid_input"


def _elastic_net_collinear_stress_fixture() -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    tuple[str, ...],
]:
    rng = np.random.default_rng(20260801)
    row_count = 396
    latent = rng.normal(size=(row_count, 1))
    columns = [
        np.full((row_count, 1), 4.0),
        np.full((row_count, 1), -7.0),
    ]
    columns.extend(latent + 1e-17 * rng.normal(size=(row_count, 1)) for _ in range(58))
    columns.extend(
        latent + 1e-17 * (index + 1) * rng.normal(size=(row_count, 1)) for index in range(6)
    )
    features = np.hstack(columns)
    for offset in range(6):
        features[220 + offset, -6 + offset] = np.nan
    targets = 2.7 * latent[:, 0] + 0.03 * rng.normal(size=row_count)
    weights = np.geomspace(1.0, 24.0, row_count)
    weights /= np.mean(weights)
    names = tuple(f"stress_{index:02d}" for index in range(features.shape[1]))
    return features, targets, weights, names


def _stress_elastic_net(*, max_iter: int) -> Any:
    return kit_dev_training.ElasticNet(
        alpha=0.01,
        l1_ratio=0.1,
        fit_intercept=True,
        max_iter=max_iter,
        tol=1e-5,
        selection="cyclic",
        random_state=20260801,
    )


def test_elastic_net_5k_warns_and_production_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    features, targets, weights, names = _elastic_net_collinear_stress_fixture()
    train_ready, _, _ = _fold_preprocess(
        features,
        features[-33:],
        weights,
        names,
        standardize=True,
    )
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always", kit_dev_training.ConvergenceWarning)
        low_cap = _stress_elastic_net(max_iter=5_000)
        low_cap.fit(train_ready, targets, sample_weight=weights)
    assert any(
        issubclass(item.category, kit_dev_training.ConvergenceWarning) for item in caught_warnings
    )

    real_elastic_net = kit_dev_training.ElasticNet

    def low_cap_factory(**parameters: object) -> object:
        parameters["max_iter"] = 5_000
        return real_elastic_net(**parameters)

    monkeypatch.setattr(kit_dev_training, "ElasticNet", low_cap_factory)
    with pytest.raises(DevelopmentRunError) as caught:
        _fit_regression_model(
            "elastic_net_regression",
            features,
            targets,
            weights,
            features[-33:],
            names,
        )
    assert caught.value.error_code == "convergence_failed"


def test_elastic_net_250k_converges_deterministically_under_collinear_stress() -> None:
    features, targets, weights, names = _elastic_net_collinear_stress_fixture()
    train_ready, evaluation_ready, _ = _fold_preprocess(
        features,
        features[-33:],
        weights,
        names,
        standardize=True,
    )
    models: list[Any] = []
    predictions: list[np.ndarray] = []
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always", kit_dev_training.ConvergenceWarning)
        for _ in range(2):
            model = _stress_elastic_net(max_iter=250_000)
            model.fit(train_ready, targets, sample_weight=weights)
            models.append(model)
            predictions.append(np.asarray(model.predict(evaluation_ready), dtype=np.float64))

    assert not any(
        issubclass(item.category, kit_dev_training.ConvergenceWarning) for item in caught_warnings
    )
    assert all(int(model.n_iter_) < 250_000 for model in models)
    prediction_a, prediction_b = predictions

    assert np.all(np.isfinite(prediction_a))
    np.testing.assert_array_equal(prediction_a, prediction_b)
    assert (
        hashlib.sha256(prediction_a.tobytes()).hexdigest()
        == hashlib.sha256(prediction_b.tobytes()).hexdigest()
    )


def test_c03_every_surrogate_fit_has_exact_start_then_complete_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = [*_mode_cohort("cyclic"), *_mode_cohort("profile", condition_offset=10)]
    folds_by_mode = {"cyclic": _two_fold_plan(), "profile": _two_fold_plan()}
    paired = {
        mode: _paired_design_payload(
            [item for item in snapshots if item.mode == mode], folds_by_mode[mode]
        )
        for mode in folds_by_mode
    }
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training._fit_regression_model",
        lambda family, x_train, y_train, train_weights, x_evaluation, names: np.full(
            len(x_evaluation), 0.25 if family == "elastic_net_regression" else 0.75
        ),
    )
    events: list[dict[str, object]] = []
    predictions, metrics, planned, completed = _run_f1_oof_workflows(
        snapshots,
        folds_by_mode,
        paired,
        events.append,
    )
    assert planned == completed
    assert len(events) == planned * 2
    assert [event["stage"] for event in events] == [
        stage for _ in range(planned) for stage in ("fit_start", "fit_complete")
    ]
    assert len(predictions) == len(snapshots) * 8
    assert len(metrics) == 16
    assert {row["task_id"] for row in predictions} == {"F1_SOH_LOSS_PP_H3"}
    assert not any("raw_probability" in row for row in predictions)


def test_c04_cancellation_at_fit_start_prevents_estimator_and_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = [*_mode_cohort("cyclic"), *_mode_cohort("profile", condition_offset=10)]
    folds_by_mode = {"cyclic": _two_fold_plan(), "profile": _two_fold_plan()}
    calls = {"estimator": 0, "comparison": 0}
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training.materialize_development_snapshots",
        lambda records: (snapshots, [], {}),
    )
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training.build_fold_plans",
        lambda snapshots, plan: (folds_by_mode, {}),
    )

    def forbidden_estimator(*args: object, **kwargs: object) -> np.ndarray:
        calls["estimator"] += 1
        return np.asarray([])

    def forbidden_comparison(*args: object, **kwargs: object) -> tuple[list[object], list[object]]:
        calls["comparison"] += 1
        return [], []

    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training._fit_regression_model", forbidden_estimator
    )
    monkeypatch.setattr("battery_eis_soh.kit_dev_training._f1_paired_outputs", forbidden_comparison)

    def cancel(event: dict[str, object]) -> None:
        assert event["stage"] == "fit_start"
        raise RuntimeError("cancelled by supervisor")

    with pytest.raises(RuntimeError, match="cancelled by supervisor"):
        kit_dev_training.analyze_records(
            _records_for_aggregation(), plan=AnalysisPlan(), progress=cancel
        )
    assert calls == {"estimator": 0, "comparison": 0}


def test_c04b_synthetic_v2_analysis_and_artifacts_never_reach_any_legacy_o2_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = [snapshot for snapshot in _synthetic_f1_cohort_with_missing_eis()[:-1]]
    smoke_acquisition = AcquisitionRecord(
        acquisition_id="ACQ-smoke-unaligned",
        cell_id="smoke-cell",
        condition_id=1,
        timestamp_s=1.0,
        timestamp_origin=1,
        duration_s=1.0,
        sd_block_id=1,
        source_archive="synthetic-eis.zip",
        source_archive_sha256="a" * 64,
        source_member="synthetic.csv",
        source_line=2,
        aligned_snapshot_id=None,
        within_60h_candidate_only=False,
        same_aligned_checkup_as_previous=False,
        within_60h_candidate_reason="not_within_60h_candidate_window",
        eis_values=(1.0,) * len(EIS_FEATURE_NAMES),
    )
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training.materialize_development_snapshots",
        lambda records: (
            snapshots,
            [smoke_acquisition],
            {
                "counts": {"eligible_f1_and_d_core": len(snapshots)},
                "eligible_acquisitions": len(snapshots),
                "within_60h_candidate_count": 0,
                "policy": {
                    "primary": "equal_finite_mean_per_frequency_v1",
                    "sensitivity": "keep_last_acquisition_v1",
                    "retry_window_seconds": 216000.0,
                    "retry_window_role": "flag_only_never_group_or_drop",
                    "timestamp_semantics_provisional": True,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training._fit_regression_model",
        lambda family, x_train, y_train, train_weights, x_evaluation, names: np.full(
            len(x_evaluation), float(np.mean(y_train))
        ),
    )

    def explode(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("legacy O2 path must remain unreachable")

    for name in (
        "_fit_base_model",
        "_legacy_v1_run_oof_workflows",
        "_legacy_v1_prediction_vector",
        "_legacy_v1_paired_outputs",
        "condition_balanced_metrics",
        "fit_weighted_platt",
        "fit_weighted_inner_oof_platt",
        "paired_condition_bootstrap",
        "retry_sensitivity_stop_decision",
        "LogisticRegression",
        "HistGradientBoostingClassifier",
    ):
        monkeypatch.setattr(kit_dev_training, name, explode)

    analysis = kit_dev_training.analyze_records(
        _records_for_aggregation(), plan=AnalysisPlan(), progress=lambda event: None
    )
    official_config_path = Path(__file__).parents[1] / "config/kit_f1_o2_development_v2.json"
    fixture_config = json.loads(official_config_path.read_text())
    official_schema_path = official_config_path.with_name(Path(fixture_config["$schema"]).name)
    fixture_schema = json.loads(official_schema_path.read_text())
    fixture_source = {
        "eoc_sha256": "a" * 64,
        "eis_sha256": "b" * 64,
        "expected_real_cells": 1,
        "expected_capacity_endpoints": 8,
        "expected_valid_rt50_spectra": 10,
    }
    fixture_config["source"].update(fixture_source)
    source_schema = fixture_schema["properties"]["source"]["properties"]
    for field, value in fixture_source.items():
        source_schema[field]["const"] = value
    fixture_config_root = tmp_path / "fixture-config"
    fixture_config_root.mkdir()
    fixture_schema_path = fixture_config_root / official_schema_path.name
    fixture_schema_path.write_text(json.dumps(fixture_schema, separators=(",", ":")) + "\n")
    fixture_config["artifact_schema_sha256"] = hashlib.sha256(
        fixture_schema_path.read_bytes()
    ).hexdigest()
    config_path = fixture_config_root / official_config_path.name
    config_path.write_text(json.dumps(fixture_config, separators=(",", ":")) + "\n")
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    payloads = _artifact_payloads(
        analysis,
        config_hash,
        fixture_schema,
        fixture_config["artifact_schema_sha256"],
    )
    assert tuple(payloads) == ARTIFACT_NAMES
    assert all(row["task_id"] == "F1_SOH_LOSS_PP_H3" for row in analysis.predictions)
    assert all(row["task_id"] == "F1_SOH_LOSS_PP_H3" for row in analysis.paired_deltas)
    assert all(row["task_id"] == "F1_SOH_LOSS_PP_H3" for row in analysis.retry_sensitivity)
    target_registry = analysis.summary["target_registry"]
    assert isinstance(target_registry, list)
    o2 = _object_dict(target_registry[1])
    assert o2["training_authorized"] is False
    assert o2["display_authorized"] is False
    assert o2["selection_role"] == "none"
    assert o2["synthetic_capability_exercised"] is False
    assert (o2["planned_fits"], o2["completed_fits"], o2["prediction_rows"]) == (0, 0, 0)
    staging = tmp_path / "production-validator-smoke"
    staging.mkdir()
    for name, text in payloads.items():
        (staging / name).write_text(text)
    validate_kit_f1_v2_artifact_tree(
        staging,
        config_path,
        require_terminal_tree=False,
        enforce_frozen_official_counts=False,
    )
    poisoned_rows = list(analysis.predictions)
    poisoned_index = next(
        index
        for index, row in enumerate(poisoned_rows)
        if row["model_role"] == "fitted_candidate" and row["view"] == "D_CORE"
    )
    outer_fold = poisoned_rows[poisoned_index]["outer_fold"]
    assert isinstance(outer_fold, int)
    poisoned_rows[poisoned_index] = {
        **poisoned_rows[poisoned_index],
        "outer_fold": outer_fold + 1,
    }
    with pytest.raises(DevelopmentRunError, match="pairing"):
        _artifact_payloads(
            replace(analysis, predictions=tuple(poisoned_rows)),
            config_hash,
            _v2_schema(),
            _v2_schema_sha256(),
        )


def _artifact_analysis() -> EngineAnalysis:
    snapshot = replace(
        _snapshot("artifact-one", condition=1, cell="cell", checkup=1, label=0),
        h3_endpoint_snapshot_id="artifact-one",
        acquisition_ids=(),
        keep_last_acquisition_id=None,
        prediction_available_at_s=None,
    )
    acquisition = AcquisitionRecord(
        acquisition_id="ACQ-artifact",
        cell_id="cell",
        condition_id=1,
        timestamp_s=100.0,
        timestamp_origin=1,
        duration_s=10.0,
        sd_block_id=1,
        source_archive="eis.zip",
        source_archive_sha256="a" * 64,
        source_member="cell.csv",
        source_line=2,
        aligned_snapshot_id=None,
        within_60h_candidate_only=True,
        same_aligned_checkup_as_previous=True,
        within_60h_candidate_reason="within_60h_and_same_aligned_checkup_candidate_only",
        eis_values=(1.0,) * 54,
    )
    flags = dict(kit_dev_training.FALSE_RESULT_FLAGS)
    return EngineAnalysis(
        acquisitions=(acquisition,),
        snapshots=(snapshot,),
        fold_registry={
            **flags,
            "artifact_schema_version": "kit_f1_o2_dev_v2",
            "split_policy": "condition_grouped_outer3_inner2_f1_v2",
            "task_independent": True,
            "applies_to_tasks": ["F1_SOH_LOSS_PP_H3", "O2_RAPID_LOSS_5PP_H3"],
            "support": {},
            "preprocessing_by_mode": {},
            "paired_design_by_mode": {},
            "modes": {},
        },
        predictions=(
            {
                **flags,
                "task_id": "F1_SOH_LOSS_PP_H3",
                "target_kind": "continuous_regression_primary",
                "target_name": "soh_loss_pp_h3",
                "snapshot_id": snapshot.snapshot_id,
                "cell_id": snapshot.cell_id,
                "condition_id": snapshot.condition_id,
                "mode": snapshot.mode,
                "acquisition_policy": "primary",
                "view": "B",
                "model_role": "baseline",
                "model_family": "persistence_zero_loss",
                "fit_id": None,
                "paired_design_sha256": "b" * 64,
                "outer_fold": 0,
                "outer_validation_weight_mean_one": 1.0,
                "observed_soh_loss_pp_h3": snapshot.soh_loss_pp_h3,
                "predicted_soh_loss_pp_h3": 0.0,
                "current_soh_percent": snapshot.soh_percent,
                "observed_future_soh_percent": snapshot.future_soh_percent,
                "predicted_future_soh_percent": snapshot.soh_percent,
                "prediction_interval_low_pp": None,
                "prediction_interval_high_pp": None,
                "prediction_interval_status": "unavailable_not_frozen",
            },
        ),
        paired_deltas=(),
        retry_sensitivity=(),
        summary={
            **flags,
            "artifact_schema_version": "kit_f1_o2_dev_v2",
            "run_kind": "KIT_F1_O2_DEVELOPMENT_V2",
            "source_archives": {
                "eoc": {"path_name": "eoc.zip", "sha256": "c" * 64},
                "eis": {"path_name": "eis.zip", "sha256": "d" * 64},
            },
            "source_counts": {"snapshots": 1},
            "materialization": {
                "counts": {"eligible_f1_and_d_core": 1},
                "eligible_acquisitions": 1,
                "within_60h_candidate_count": 1,
                "policy": {
                    "primary": "equal_finite_mean_per_frequency_v1",
                    "sensitivity": "keep_last_acquisition_v1",
                    "retry_window_seconds": 216000.0,
                    "retry_window_role": "flag_only_never_group_or_drop",
                    "timestamp_semantics_provisional": True,
                },
            },
            "snapshot_count": 1,
            "target_registry": [
                {
                    "task_id": "F1_SOH_LOSS_PP_H3",
                    "target_kind": "continuous_regression_primary",
                    "target_name": "soh_loss_pp_h3",
                    "unit": "percentage_points",
                    "status": "synthetic_capability_only_wave_a_no_go",
                    "training_authorized": False,
                    "display_authorized": False,
                    "selection_role": "none",
                    "synthetic_capability_exercised": True,
                    "planned_fits": 0,
                    "completed_fits": 0,
                    "prediction_rows": 1,
                },
                {
                    "task_id": "O2_RAPID_LOSS_5PP_H3",
                    "target_kind": "binary_probability_secondary_candidate",
                    "target_name": "rapid_loss_ge_5pp_h3",
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
                "snapshot_status": {"eligible_f1_and_d_core": 1},
                "f1_exact_h3_available": 1,
                "o2_candidate_events_observed": 0,
                "o2_prediction_rows": 0,
            },
            "planned_fits": 0,
            "completed_fits": 0,
            "fit_counts_by_task": {
                "F1_SOH_LOSS_PP_H3": {"planned": 0, "completed": 0},
                "O2_RAPID_LOSS_5PP_H3": {"planned": 0, "completed": 0},
            },
            "candidate_role": "fixed_capability_only_no_selection",
            "comparison_policy": "report_each_fixed_family_without_ranking_or_promotion",
            "metrics": [],
            "o2_results": [],
        },
        planned_fits=0,
        completed_fits=0,
    )


def _v2_schema() -> dict[str, object]:
    path = Path(__file__).parents[1] / "config/kit_f1_o2_development_v2.schema.json"
    return json.loads(path.read_text())


def _v2_schema_sha256() -> str:
    path = Path(__file__).parents[1] / "config/kit_f1_o2_development_v2.schema.json"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_v2_config_bundle(tmp_path: Path, config: dict[str, object]) -> Path:
    root = Path(__file__).parents[1]
    schema_source = root / "config/kit_f1_o2_development_v2.schema.json"
    schema_path = tmp_path / schema_source.name
    schema_path.write_bytes(schema_source.read_bytes())
    config_path = tmp_path / "kit_f1_o2_development_v2.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return config_path


def test_c05_execute_emits_exactly_seven_scientific_artifacts_no_runtime_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path, monkeypatch
    payloads = _artifact_payloads(_artifact_analysis(), "a" * 64, _v2_schema(), _v2_schema_sha256())
    assert tuple(payloads) == ARTIFACT_NAMES
    summary = json.loads(payloads["engine_summary.json"])
    assert summary["artifact_paths"] == list(ARTIFACT_NAMES)
    acquisition = json.loads(payloads["acquisition_manifest.jsonl"])
    assert acquisition["within_60h_candidate_only"] is True
    assert acquisition["same_aligned_checkup_as_previous"] is True
    assert "possible_retry_with_previous" not in acquisition
    assert "retry" not in acquisition


def test_c05b_synthetic_capability_does_not_claim_f1_training_authorization() -> None:
    registry = _artifact_analysis().summary["target_registry"]
    assert isinstance(registry, list)
    f1, o2 = registry
    assert f1["training_authorized"] is False
    assert f1["display_authorized"] is False
    assert f1["selection_role"] == "none"
    assert f1["synthetic_capability_exercised"] is True
    assert o2["training_authorized"] is False
    assert o2["display_authorized"] is False
    assert o2["selection_role"] == "none"
    assert o2["synthetic_capability_exercised"] is False


@pytest.mark.parametrize(
    ("target", "field", "value"),
    (
        ("targets", "training_authorized", True),
        ("targets", "display_authorized", True),
        ("targets", "selection_role", "candidate"),
        ("models", "training_authorized", True),
    ),
)
def test_c05c_o2_config_schema_rejects_every_authorization_poison(
    target: str, field: str, value: object
) -> None:
    root = Path(__file__).parents[1]
    config = json.loads((root / "config/kit_f1_o2_development_v2.json").read_text())
    if target == "targets":
        config["targets"]["o2"][field] = value
    else:
        config["models"]["o2_capability"][field] = value
    errors = list(Draft202012Validator(_v2_schema()).iter_errors(config))
    assert errors
    assert any(field in "/".join(str(part) for part in error.path) for error in errors)


def test_c05d_artifact_guard_rejects_o2_prediction_fit_comparison_and_result_poison() -> None:
    analysis = _artifact_analysis()
    prediction = {
        **analysis.predictions[0],
        "task_id": "O2_RAPID_LOSS_5PP_H3",
        "target_kind": "binary_probability_secondary_candidate",
        "target_name": "rapid_loss_ge_5pp_h3",
    }
    poisons = (
        replace(analysis, predictions=(*analysis.predictions, prediction)),
        replace(
            analysis,
            paired_deltas=(
                {
                    **kit_dev_training.FALSE_RESULT_FLAGS,
                    "task_id": "O2_RAPID_LOSS_5PP_H3",
                    "model_family": "logistic_elastic_net",
                },
            ),
        ),
        replace(
            analysis,
            retry_sensitivity=(
                {
                    **kit_dev_training.FALSE_RESULT_FLAGS,
                    "task_id": "O2_RAPID_LOSS_5PP_H3",
                    "model_family": "hist_gradient_boosting",
                },
            ),
        ),
        replace(
            analysis,
            summary={
                **analysis.summary,
                "fit_counts_by_task": {
                    **_object_dict(analysis.summary["fit_counts_by_task"]),
                    "O2_RAPID_LOSS_5PP_H3": {"planned": 1, "completed": 1},
                },
            },
        ),
        replace(analysis, summary={**analysis.summary, "o2_results": [{"poison": True}]}),
    )
    for poisoned in poisons:
        with pytest.raises(DevelopmentRunError, match="invalid|O2|reconcil"):
            _artifact_payloads(poisoned, "a" * 64, _v2_schema(), _v2_schema_sha256())


def test_c06_exact_limited_go_reaches_frozen_source_binding_before_source_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "scientific"
    reached: list[str] = []

    def stop_at_source(*args: object, **kwargs: object) -> None:
        del args, kwargs
        reached.append("source")
        raise DevelopmentRunError("source binding reached")

    monkeypatch.setattr("battery_eis_soh.kit_dev_training._verify_source_binding", stop_at_source)
    config_path = Path(__file__).parents[1] / "config/kit_f1_o2_development_v2.json"
    with pytest.raises(DevelopmentRunError, match="source binding reached"):
        execute_engineering_analysis(
            eoc_zip=tmp_path / "eoc.zip",
            eis_zip=tmp_path / "eis.zip",
            output_dir=output,
            config_path=config_path,
            progress=lambda event: None,
        )
    assert not output.exists()
    assert reached == ["source"]


@pytest.mark.parametrize(
    ("timestamp_verified", "completion_verified", "timestamp_provisional"),
    ((True, False, True), (False, True, False), (True, True, True)),
)
def test_c06b_go_readiness_flip_stays_blocked_on_unverified_or_provisional_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timestamp_verified: bool,
    completion_verified: bool,
    timestamp_provisional: bool,
) -> None:
    root = Path(__file__).parents[1]
    config = json.loads((root / "config/kit_f1_o2_development_v2.json").read_text())
    config["artifact_schema_sha256"] = _v2_schema_sha256()
    config["readiness"] = {
        **config["readiness"],
        "wave_a_target_data_go": True,
        "f1_training_authorized": True,
        "timestamp_semantics_verified": timestamp_verified,
        "checkup_completion_semantics_verified": completion_verified,
    }
    config["authorization"] = {
        **config["authorization"],
        "timestamp_semantics_provisional": timestamp_provisional,
    }
    config_path = _write_v2_config_bundle(tmp_path, config)
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training.load_kit_v2_development_records",
        lambda *args: (_ for _ in ()).throw(AssertionError("source loader must not run")),
    )
    output = tmp_path / "scientific"
    with pytest.raises(DevelopmentRunError, match="invalid|timestamp|completion|provisional"):
        execute_engineering_analysis(
            eoc_zip=tmp_path / "eoc.zip",
            eis_zip=tmp_path / "eis.zip",
            output_dir=output,
            config_path=config_path,
            progress=lambda event: None,
        )
    assert not output.exists()


def test_c06c_schema_hash_binding_poison_fails_before_source_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[1]
    config = json.loads((root / "config/kit_f1_o2_development_v2.json").read_text())
    actual = _v2_schema_sha256()
    config["artifact_schema_sha256"] = ("0" if actual[0] != "0" else "1") + actual[1:]
    config_path = _write_v2_config_bundle(tmp_path, config)
    monkeypatch.setattr(
        "battery_eis_soh.kit_dev_training.load_kit_v2_development_records",
        lambda *args: (_ for _ in ()).throw(AssertionError("source loader must not run")),
    )
    output = tmp_path / "scientific"
    with pytest.raises(
        DevelopmentRunError,
        match=r"(?i)schema.*(?:sha|hash)|(?:sha|hash).*schema",
    ):
        execute_engineering_analysis(
            eoc_zip=tmp_path / "eoc.zip",
            eis_zip=tmp_path / "eis.zip",
            output_dir=output,
            config_path=config_path,
            progress=lambda event: None,
        )
    assert not output.exists()


def test_c06d_readiness_evidence_mutation_and_semantic_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    config_dir = repository / "config"
    evidence_dir = repository / "artifacts/audits"
    config_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    config_path = config_dir / "kit_f1_o2_development_v2.json"
    config_path.write_text("{}")
    source = Path(__file__).parents[1] / "artifacts/audits/kit-d-core-eis-readiness.json"
    evidence = evidence_dir / "kit-d-core-eis-readiness.json"
    evidence.write_bytes(source.read_bytes())
    readiness = json.loads(
        (Path(__file__).parents[1] / "config/kit_f1_o2_development_v2.json").read_text()
    )["readiness"]
    kit_dev_training._verify_limited_go_readiness(config_path, readiness)

    evidence.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(DevelopmentRunError, match="SHA-256"):
        kit_dev_training._verify_limited_go_readiness(config_path, readiness)

    payload = json.loads(source.read_text())
    payload["alignment"]["next_capacity_boundary_violations"] = 1
    evidence.write_text(json.dumps(payload, sort_keys=True))
    semantic_readiness = {
        **readiness,
        "eis_readiness_artifact_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
    }
    with pytest.raises(DevelopmentRunError, match="semantic invariants"):
        kit_dev_training._verify_limited_go_readiness(config_path, semantic_readiness)


def test_c07_artifact_payload_guard_rejects_nested_winner_field() -> None:
    analysis = _artifact_analysis()
    poisoned = replace(analysis, summary={**analysis.summary, "nested": {"winner": "x"}})
    with pytest.raises(DevelopmentRunError, match="prohibited selection field"):
        _artifact_payloads(poisoned, "a" * 64, _v2_schema(), _v2_schema_sha256())


@pytest.mark.parametrize("nonfinite", (float("nan"), float("inf"), float("-inf")))
def test_c08_artifact_payload_guard_rejects_nonfinite_prediction_poison(
    nonfinite: float,
) -> None:
    analysis = _artifact_analysis()
    poisoned_prediction = {
        **analysis.predictions[0],
        "predicted_soh_loss_pp_h3": nonfinite,
    }
    poisoned = replace(analysis, predictions=(poisoned_prediction,))
    with pytest.raises(DevelopmentRunError, match="finite|invalid"):
        _artifact_payloads(poisoned, "a" * 64, _v2_schema(), _v2_schema_sha256())


def test_c09_no_h3_snapshot_cannot_retain_truth_or_prediction_linkage() -> None:
    analysis = _artifact_analysis()
    snapshot = analysis.snapshots[0]
    inconsistent_truth = replace(
        snapshot,
        f1_target_available=False,
        eligibility_status="no_h3_followup",
    )
    with pytest.raises(DevelopmentRunError, match="snapshot_manifest|H3|reconcil"):
        _artifact_payloads(
            replace(analysis, snapshots=(inconsistent_truth,)),
            "a" * 64,
            _v2_schema(),
            _v2_schema_sha256(),
        )

    no_h3 = replace(
        snapshot,
        future_soh_percent=None,
        soh_loss_pp_h3=None,
        h3_endpoint_snapshot_id=None,
        h3_checkup_index=None,
        f1_target_available=False,
        eligibility_status="no_h3_followup",
        o2_label=None,
    )
    with pytest.raises(DevelopmentRunError, match="prediction|H3|reconcil|count"):
        _artifact_payloads(
            replace(analysis, snapshots=(no_h3,)),
            "a" * 64,
            _v2_schema(),
            _v2_schema_sha256(),
        )


@pytest.mark.parametrize(
    "poison",
    ("snapshot_count", "snapshot_status", "f1_available", "registry_predictions", "fit_counts"),
)
def test_c10_artifact_payload_guard_reconciles_snapshot_target_registry_and_counts(
    poison: str,
) -> None:
    analysis = _artifact_analysis()
    summary = deepcopy(analysis.summary)
    if poison == "snapshot_count":
        summary["snapshot_count"] = 2
    elif poison == "snapshot_status":
        target_counts = _object_dict(summary["target_counts"])
        snapshot_status = _object_dict(target_counts["snapshot_status"])
        snapshot_status["eligible_f1_and_d_core"] = 2
    elif poison == "f1_available":
        _object_dict(summary["target_counts"])["f1_exact_h3_available"] = 0
    elif poison == "registry_predictions":
        target_registry = summary["target_registry"]
        assert isinstance(target_registry, list)
        _object_dict(target_registry[0])["prediction_rows"] = 0
    else:
        fit_counts = _object_dict(summary["fit_counts_by_task"])
        f1_counts = _object_dict(fit_counts["F1_SOH_LOSS_PP_H3"])
        f1_counts["planned"] = 1
    with pytest.raises(DevelopmentRunError, match="reconcil|count|registry|snapshot|fit"):
        _artifact_payloads(
            replace(analysis, summary=summary),
            "a" * 64,
            _v2_schema(),
            _v2_schema_sha256(),
        )


def test_c11_artifact_summary_binds_exact_raw_schema_hash() -> None:
    payloads = _artifact_payloads(_artifact_analysis(), "a" * 64, _v2_schema(), _v2_schema_sha256())
    summary = json.loads(payloads["engine_summary.json"])
    assert summary["artifact_schema_sha256"] == _v2_schema_sha256()


def test_frozen_config_and_schema_are_exact_and_strict() -> None:
    root = Path(__file__).parents[1]
    config = json.loads((root / "config/kit_f1_o2_development_v2.json").read_text())
    schema = _v2_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(config)
    mutation = dict(config)
    mutation["winner"] = "elastic_net_regression"
    assert list(Draft202012Validator(schema).iter_errors(mutation))
    assert tuple(ARTIFACT_NAMES) == (
        "acquisition_manifest.jsonl",
        "snapshot_manifest.jsonl",
        "fold_registry.json",
        "oof_predictions.jsonl",
        "paired_deltas.json",
        "retry_sensitivity.json",
        "engine_summary.json",
    )


def test_analysis_plan_defaults_are_exact_frozen_values() -> None:
    plan = AnalysisPlan()
    assert (plan.outer_group_folds, plan.inner_group_folds) == (3, 2)
    assert (plan.bootstrap_draws, plan.minimum_valid_bootstrap_draws) == (2000, 1900)
    assert plan.bootstrap_seed == 20260801


def test_empty_aggregation_and_weight_partitions_fail_closed() -> None:
    with pytest.raises(DevelopmentRunError, match="empty acquisition"):
        _equal_finite_mean(())
    with pytest.raises(DevelopmentRunError, match="empty partition"):
        hierarchical_weights(())
