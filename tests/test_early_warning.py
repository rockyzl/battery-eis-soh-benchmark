import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from battery_eis_soh.early_warning import (
    AgeingMode,
    BatterySnapshot,
    FeatureView,
    LabelStatus,
    PeerDeploymentScenario,
    PeerFeatureRoles,
    PolicyStatus,
    ProbabilityPrediction,
    Q0Provenance,
    RecommendationLevel,
    RecommendationPolicy,
    WarningSignals,
    build_conditional_rul,
    build_feature_view,
    build_next_checkup_threshold_label,
    build_nth_checkup_rapid_loss_label,
    conventional_soh_percent,
    engineering_micro_warning_metrics,
    peer_deviation_at_snapshot,
    recommend_maintenance,
    validate_group_splits,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def _history(
    cell: str = "P001_1",
    condition: str = "P001",
    capacities: tuple[float, ...] = (3.0, 2.85, 2.64, 2.34),
    *,
    auxiliary: bool = False,
) -> list[BatterySnapshot]:
    q0 = _q0(cell)
    return [
        BatterySnapshot(
            cell_id=cell,
            condition_id=condition,
            checkpoint=index,
            completed_at=START + timedelta(days=7 * index),
            capacity_ah=capacity,
            q0=q0,
            ageing_mode=AgeingMode.CYCLIC,
            exposure=10.0 * index,
            exposure_unit="EFC",
            elapsed_days=7.0 * index,
            equivalent_full_cycles=10.0 * index,
            temperature_c=25.0,
            context_features=(("charge_rate_c", 1.0),),
            eis_features=(1.0 + index, 2.0 + index),
            eis_valid=True,
            auxiliary_features=(("pulse_resistance_ohm", 0.1 + index),) if auxiliary else (),
        )
        for index, capacity in enumerate(capacities)
    ]


def _q0(
    cell: str,
    *,
    available_at: datetime = START,
    measurement_timestamp: datetime = START,
) -> Q0Provenance:
    return Q0Provenance(
        capacity_ah=3.0,
        source_cell_id=cell,
        source_identity=f"{cell}:standardized-checkup-0:capacity-row",
        source_archive="synthetic://test-fixture",
        source_member_or_file="capacity.csv",
        source_row_locator="row=1",
        measurement_timestamp=measurement_timestamp,
        endpoint_selection_rule="first standardized room-temperature discharge endpoint",
        unit="Ah",
        source_manifest_hash="synthetic-test-manifest",
        available_at=available_at,
    )


def _policy(status: PolicyStatus = PolicyStatus.FROZEN) -> RecommendationPolicy:
    return RecommendationPolicy(
        policy_id="cyclic-P001-v1",
        status=status,
        ageing_mode=AgeingMode.CYCLIC,
        future_usage_scenario="continue P001",
        rul_unit="EFC",
        confirmed_red_soh_percent=70.0,
        confirmed_orange_soh_percent=80.0,
        yellow_probability=0.35,
        orange_probability=0.70,
        yellow_peer_abs_z=2.0,
        yellow_rul=25.0,
        orange_rul=10.0,
    )


def _signals(
    *,
    data_quality_ok: bool = True,
    repeated_capacity_criterion_confirmed: bool = False,
    confirmed_soh_percent: float | None = None,
    threshold_crossing_probability: float | None = None,
    rapid_degradation_probability: float | None = None,
    peer_robust_z_score: float | None = None,
    conditional_rul: float | None = None,
) -> WarningSignals:
    return WarningSignals(
        ageing_mode=AgeingMode.CYCLIC,
        future_usage_scenario="continue P001",
        rul_unit="EFC",
        data_quality_ok=data_quality_ok,
        repeated_capacity_criterion_confirmed=repeated_capacity_criterion_confirmed,
        confirmed_soh_percent=confirmed_soh_percent,
        threshold_crossing_probability=threshold_crossing_probability,
        rapid_degradation_probability=rapid_degradation_probability,
        peer_robust_z_score=peer_robust_z_score,
        conditional_rul=conditional_rul,
    )


def _signals_from_untyped_json(payload: str) -> WarningSignals:
    """Exercise validation at a deliberately untyped external-data boundary."""
    return WarningSignals(
        ageing_mode=AgeingMode.CYCLIC,
        future_usage_scenario="continue P001",
        rul_unit="EFC",
        **json.loads(payload),
    )


def test_soh_uses_explicit_q0_and_is_not_clipped() -> None:
    assert conventional_soh_percent(2.4, 3.0) == pytest.approx(80.0)
    assert conventional_soh_percent(3.06, 3.0) == pytest.approx(102.0)


def test_q0_requires_source_identity_and_past_availability() -> None:
    future_q0 = _q0("A", available_at=START + timedelta(days=1))
    with pytest.raises(ValueError, match="Q0 was not available"):
        BatterySnapshot(
            cell_id="A",
            condition_id="C",
            checkpoint=0,
            completed_at=START,
            capacity_ah=3.0,
            q0=future_q0,
            ageing_mode=AgeingMode.CYCLIC,
            exposure=0,
            exposure_unit="EFC",
            elapsed_days=0,
            equivalent_full_cycles=0,
            temperature_c=25,
        )


def test_completed_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _q0("A", available_at=START.replace(tzinfo=None))


def test_q0_provenance_cannot_come_from_another_cell() -> None:
    history = _history()
    wrong_q0 = _q0("other-cell")
    with pytest.raises(ValueError, match="same physical cell"):
        BatterySnapshot(**{**history[0].__dict__, "q0": wrong_q0})


def test_capacity_ah_contract_rejects_milliamphour_aliases() -> None:
    with pytest.raises(ValueError, match="exactly 'Ah'"):
        replace(_q0("A"), unit="mAh")
    with pytest.raises(ValueError, match="capacity_unit='Ah'"):
        replace(_history()[0], capacity_unit="mAh")  # type: ignore[arg-type]


def test_features_are_completed_at_past_only() -> None:
    features = build_feature_view(_history(), 1, view=FeatureView.D_CORE)
    assert [key.checkpoint for key in features.source_snapshots] == [0, 1]
    assert features.latest_source_completed_at == START + timedelta(days=7)
    changed = _history(capacities=(3.0, 2.85, 0.3, 0.2))
    assert build_feature_view(changed, 1, view=FeatureView.D_CORE) == features


def test_registered_feature_views_keep_scientific_blocks_distinct() -> None:
    history = _history(auxiliary=True)
    view_a = build_feature_view(history, 1, view=FeatureView.A)
    assert "temperature_c" not in view_a.names
    assert "charge_rate_c" not in view_a.names
    assert not any(name.startswith("eis_") for name in view_a.names)

    view_c = build_feature_view(history, 1, view=FeatureView.C)
    assert "current_capacity_ah" not in view_c.names
    assert "temperature_c" in view_c.names
    assert "eis_0" in view_c.names

    view_full = build_feature_view(history, 1, view=FeatureView.D_FULL)
    assert view_full.view is FeatureView.D_FULL
    assert "pulse_resistance_ohm" in view_full.names


def test_d_core_and_d_full_fail_closed_when_required_block_is_absent() -> None:
    no_eis = _history()
    no_eis[1] = BatterySnapshot(**{**no_eis[1].__dict__, "eis_features": (), "eis_valid": False})
    with pytest.raises(ValueError, match="quality-valid current EIS"):
        build_feature_view(no_eis, 1, view=FeatureView.D_CORE)
    with pytest.raises(ValueError, match="audited auxiliary"):
        build_feature_view(_history(), 1, view=FeatureView.D_FULL)


def test_o1_uses_future_checkups_only_and_reports_crossing_interval() -> None:
    label = build_next_checkup_threshold_label(_history(), 0, horizon_checkups=3)
    assert label.status is LabelStatus.OBSERVED_EVENT
    assert label.label == 1
    assert label.event_interval is not None
    assert label.event_interval.last_above_key.checkpoint == 2
    assert label.event_interval.first_observed_below_key.checkpoint == 3
    assert label.event_interval.remaining_exposure_lower_bound == pytest.approx(20.0)
    assert label.event_interval.remaining_exposure_upper_bound == pytest.approx(30.0)


def test_o1_requires_current_above_threshold_and_censors_short_followup() -> None:
    with pytest.raises(ValueError, match="already at/below"):
        build_next_checkup_threshold_label(_history(), 3, horizon_checkups=1)
    censored = build_next_checkup_threshold_label(_history()[:2], 1, horizon_checkups=3)
    assert censored.status is LabelStatus.RIGHT_CENSORED
    assert censored.label is None


def test_o2_compares_exactly_the_nth_future_checkup() -> None:
    history = _history(capacities=(3.0, 2.85, 2.0, 2.82))
    label = build_nth_checkup_rapid_loss_label(
        history,
        1,
        horizon_checkups=2,
        minimum_drop_percentage_points=5.0,
    )
    assert label.status is LabelStatus.OBSERVED_NONEVENT
    assert label.label == 0
    censored = build_nth_checkup_rapid_loss_label(
        history[:3],
        1,
        horizon_checkups=2,
        minimum_drop_percentage_points=5.0,
    )
    assert censored.status is LabelStatus.RIGHT_CENSORED


def test_conditional_rul_is_interval_observed_or_right_censored() -> None:
    observed = build_conditional_rul(
        _history(), 1, threshold_percent=80.0, future_usage_condition="continue P001"
    )
    assert observed.event_observed
    assert observed.event_interval is not None
    assert observed.event_interval.remaining_exposure_lower_bound == pytest.approx(10.0)
    assert observed.event_interval.remaining_exposure_upper_bound == pytest.approx(20.0)

    censored = build_conditional_rul(
        _history()[:3], 1, threshold_percent=80.0, future_usage_condition="continue P001"
    )
    assert not censored.event_observed
    assert censored.event_interval is None
    assert censored.right_censored_after == pytest.approx(10.0)


def test_peer_features_are_disabled_for_primary_unseen_condition() -> None:
    target = _history()[1]
    result = peer_deviation_at_snapshot(target, {})
    assert not result.enabled
    assert not result.available
    with pytest.raises(ValueError, match="prohibited"):
        PeerFeatureRoles(
            enabled=True,
            scenario=PeerDeploymentScenario.PRIMARY_UNSEEN_CONDITION,
            fitted_reference_cell_ids=frozenset({"P001_2"}),
            target_cell_ids=frozenset({"P001_1"}),
        )
    with pytest.raises(TypeError, match="PeerDeploymentScenario"):
        PeerFeatureRoles(
            enabled=True,
            scenario="known_condition_replicate",  # type: ignore[arg-type]
            fitted_reference_cell_ids=frozenset({"P001_2"}),
            target_cell_ids=frozenset({"P001_1"}),
        )


def test_peer_features_use_explicit_fitted_roles_and_past_completion_only() -> None:
    roles = PeerFeatureRoles(
        enabled=True,
        scenario=PeerDeploymentScenario.KNOWN_CONDITION_REPLICATE,
        fitted_reference_cell_ids=frozenset({"P001_2", "P001_3"}),
        target_cell_ids=frozenset({"P001_1"}),
    )
    result = peer_deviation_at_snapshot(
        _history()[1],
        {"P001_2": _history("P001_2"), "P001_3": _history("P001_3")},
        roles=roles,
    )
    assert result.enabled and result.available
    assert {key.cell_id for key in result.reference_snapshots} == {"P001_2", "P001_3"}
    assert all(key.checkpoint <= 1 for key in result.reference_snapshots)


def test_peer_features_reject_mapping_alias_and_target_history_bypass() -> None:
    roles = PeerFeatureRoles(
        enabled=True,
        scenario=PeerDeploymentScenario.KNOWN_CONDITION_REPLICATE,
        fitted_reference_cell_ids=frozenset({"P001_2", "P001_3"}),
        target_cell_ids=frozenset({"P001_1"}),
    )
    with pytest.raises(ValueError, match="mapping key must exactly match"):
        peer_deviation_at_snapshot(
            _history()[1],
            {"P001_2": _history("P001_1"), "P001_3": _history("P001_3")},
            roles=roles,
        )
    with pytest.raises(ValueError, match="whitespace-normalized"):
        PeerFeatureRoles(
            enabled=True,
            scenario=PeerDeploymentScenario.KNOWN_CONDITION_REPLICATE,
            fitted_reference_cell_ids=frozenset({"P001_2 "}),
            target_cell_ids=frozenset({"P001_1"}),
        )


def test_group_validator_rejects_cell_and_condition_leakage() -> None:
    with pytest.raises(ValueError, match="physical cell"):
        validate_group_splits({"train": ["A"], "test": ["A"]})
    with pytest.raises(ValueError, match="condition C1"):
        validate_group_splits(
            {"train": ["A"], "test": ["B"]},
            condition_by_cell={"A": "C1", "B": "C1"},
            require_condition_disjoint=True,
        )


def test_engineering_metrics_validate_censored_prediction_metadata() -> None:
    observed = build_next_checkup_threshold_label(_history(), 0, horizon_checkups=3)
    censored = build_next_checkup_threshold_label(_history()[:2], 1, horizon_checkups=3)
    predictions = [
        ProbabilityPrediction(observed.snapshot_key, observed.outcome_id, 3, 0.8),
        ProbabilityPrediction(censored.snapshot_key, censored.outcome_id, 3, 0.4),
    ]
    metrics = engineering_micro_warning_metrics([observed, censored], predictions)
    assert metrics["metric_scope"] == "engineering_only_snapshot_micro"
    assert metrics["observed_count"] == 1
    assert metrics["censored_count"] == 1
    assert metrics["condition_macro_brier_score"] == pytest.approx(0.04)

    bad = [predictions[0], ProbabilityPrediction(censored.snapshot_key, "wrong", 3, 0.4)]
    with pytest.raises(ValueError, match="outcome"):
        engineering_micro_warning_metrics([observed, censored], bad)


def test_engineering_metrics_use_composite_identity_and_reject_duplicates() -> None:
    label = build_next_checkup_threshold_label(_history(), 0, horizon_checkups=3)
    second_outcome = replace(label, outcome_id="SECOND_OUTCOME")
    predictions = [
        ProbabilityPrediction(label.snapshot_key, label.outcome_id, 3, 0.8),
        ProbabilityPrediction(second_outcome.snapshot_key, second_outcome.outcome_id, 3, 0.7),
    ]
    metrics = engineering_micro_warning_metrics([label, second_outcome], predictions)
    assert metrics["observed_count"] == 2

    with pytest.raises(ValueError, match="duplicate label identity"):
        engineering_micro_warning_metrics([label, label], [predictions[0]])
    with pytest.raises(ValueError, match="duplicate prediction identity"):
        engineering_micro_warning_metrics([label], [predictions[0], predictions[0]])
    wrong_horizon = replace(predictions[0], horizon_checkups=2)
    with pytest.raises(ValueError, match="snapshot, outcome, horizon"):
        engineering_micro_warning_metrics([label], [wrong_horizon])


def test_recommendation_requires_explicit_usable_compatible_policy() -> None:
    with pytest.raises(ValueError, match="blocked"):
        recommend_maintenance(_signals(), _policy(PolicyStatus.BLOCKED))
    with pytest.raises(ValueError, match="self-authorize FROZEN"):
        recommend_maintenance(_signals(), _policy(PolicyStatus.FROZEN))
    with pytest.raises(ValueError, match="contract demo"):
        recommend_maintenance(_signals(), _policy(PolicyStatus.PROVISIONAL))
    incompatible = WarningSignals(AgeingMode.CALENDAR, "continue P001", "days")
    with pytest.raises(ValueError, match="must match"):
        recommend_maintenance(
            incompatible,
            _policy(PolicyStatus.PROVISIONAL),
            allow_provisional_contract_demo=True,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda policy: replace(policy, confirmed_red_soh_percent=float("nan")), "finite"),
        (lambda policy: replace(policy, confirmed_red_soh_percent=85.0), "SoH thresholds"),
        (lambda policy: replace(policy, confirmed_orange_soh_percent=101.0), "0-100"),
        (lambda policy: replace(policy, yellow_probability=float("inf")), "finite"),
        (lambda policy: replace(policy, yellow_peer_abs_z=0.0), "absolute-z"),
        (lambda policy: replace(policy, yellow_rul=float("nan")), "finite"),
        (lambda policy: replace(policy, orange_rul=True), "finite number"),
        (
            lambda policy: replace(policy, future_usage_scenario=" continue P001"),
            "whitespace-normalized",
        ),
        (lambda policy: replace(policy, rul_unit="days"), "incompatible"),
    ],
)
def test_policy_thresholds_and_mode_units_fail_closed(
    mutation: Callable[[RecommendationPolicy], RecommendationPolicy], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        mutation(_policy())


def test_policy_status_requires_registered_enum() -> None:
    with pytest.raises(TypeError, match="PolicyStatus"):
        replace(_policy(), **json.loads('{"status": "provisional"}'))


def test_signal_contract_rejects_negative_rul_and_incompatible_units() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _signals(conditional_rul=-1.0)
    with pytest.raises(ValueError, match="incompatible"):
        WarningSignals(AgeingMode.PROFILE, "profile continuation", "km")
    with pytest.raises(ValueError, match="whitespace-normalized"):
        WarningSignals(AgeingMode.CALENDAR, " calendar continuation", "days")
    with pytest.raises(ValueError, match="requires an observed"):
        _signals(repeated_capacity_criterion_confirmed=True)
    with pytest.raises(ValueError, match="finite number"):
        _signals_from_untyped_json('{"peer_robust_z_score": "high"}')
    with pytest.raises(ValueError, match="between 0 and 100"):
        _signals(confirmed_soh_percent=101.0)
    with pytest.raises(ValueError, match="between zero and one"):
        _signals(rapid_degradation_probability=-0.01)
    with pytest.raises(TypeError, match="boolean"):
        _signals_from_untyped_json('{"data_quality_ok": 1}')


def test_actions_are_advisory_and_all_require_human_review() -> None:
    policy = _policy(PolicyStatus.PROVISIONAL)
    green = recommend_maintenance(
        _signals(threshold_crossing_probability=0.1),
        policy,
        allow_provisional_contract_demo=True,
    )
    assert green.level is RecommendationLevel.GREEN
    assert green.requires_human_review
    orange = recommend_maintenance(
        _signals(threshold_crossing_probability=0.9),
        policy,
        allow_provisional_contract_demo=True,
    )
    assert orange.level is RecommendationLevel.ORANGE
    assert orange.requires_human_review
    single_low = recommend_maintenance(
        _signals(confirmed_soh_percent=69.0),
        policy,
        allow_provisional_contract_demo=True,
    )
    assert single_low.level is RecommendationLevel.ORANGE
    red = recommend_maintenance(
        _signals(
            confirmed_soh_percent=69.0,
            repeated_capacity_criterion_confirmed=True,
        ),
        policy,
        allow_provisional_contract_demo=True,
    )
    assert red.level is RecommendationLevel.RED
    assert red.requires_human_review


def test_bad_data_requests_retest_not_red() -> None:
    recommendation = recommend_maintenance(
        _signals(data_quality_ok=False, threshold_crossing_probability=0.99),
        _policy(PolicyStatus.PROVISIONAL),
        allow_provisional_contract_demo=True,
    )
    assert recommendation.level is RecommendationLevel.YELLOW
    assert "retest" in recommendation.action
