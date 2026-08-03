"""Typed loader and executable invariants for the early-warning task registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL_REGISTRY = _REPOSITORY_ROOT / "config" / "early_warning_task_registry.json"
_STRICT_SCHEMA = _REPOSITORY_ROOT / "config" / "early_warning_task_registry.schema.json"
EXPECTED_BINARY_METRICS = (
    "condition_macro_pr_auc",
    "roc_auc",
    "recall_at_frozen_alert_budget",
    "precision_at_frozen_alert_budget",
    "missed_event_rate",
    "false_alerts_per_cell_checkup",
    "brier_score",
    "calibration_slope_and_intercept",
    "event_level_early_warning_lead_time",
)
EXPECTED_SURVIVAL_METRICS = (
    "censoring_aware_integrated_brier_score",
    "concordance",
    "time_dependent_discrimination",
    "survival_calibration_at_registered_horizons",
    "prediction_interval_coverage",
)


@dataclass(frozen=True)
class RegisteredFeatureView:
    view_id: str
    blocks: tuple[str, ...]


@dataclass(frozen=True)
class RegisteredOutcome:
    outcome_id: str
    family: str
    horizon_checkups: int | None
    threshold_status: str | None


@dataclass(frozen=True)
class EarlyWarningRegistry:
    schema_version: int
    protocol_lineage: str
    status: str
    primary_horizon_checkups: int
    feature_views: tuple[RegisteredFeatureView, ...]
    outcomes: tuple[RegisteredOutcome, ...]
    binary_metrics: tuple[str, ...]
    survival_metrics: tuple[str, ...]
    action_policy_status: str

    @property
    def feature_view_ids(self) -> frozenset[str]:
        return frozenset(view.view_id for view in self.feature_views)

    @property
    def outcome_ids(self) -> frozenset[str]:
        return frozenset(outcome.outcome_id for outcome in self.outcomes)


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list")
    return value


def _assert_exact_property_shape(value: Any, canonical: Any, location: str) -> None:
    """Require every v1 object/list to retain the reviewed canonical property shape."""
    if isinstance(canonical, dict):
        if not isinstance(value, dict):
            raise TypeError(f"{location} must be an object")
        if set(value) != set(canonical):
            missing = sorted(set(canonical) - set(value))
            unknown = sorted(set(value) - set(canonical))
            raise ValueError(
                f"{location} property shape changed; missing={missing}, unknown={unknown}"
            )
        for key in canonical:
            _assert_exact_property_shape(value[key], canonical[key], f"{location}.{key}")
    elif isinstance(canonical, list):
        if not isinstance(value, list):
            raise TypeError(f"{location} must be a list")
        if len(value) != len(canonical):
            raise ValueError(f"{location} list length changed from the reviewed v1 contract")
        for index, (item, canonical_item) in enumerate(zip(value, canonical, strict=True)):
            _assert_exact_property_shape(item, canonical_item, f"{location}[{index}]")


def _validate_complete_registry(payload: dict[str, Any]) -> None:
    schema = _object(json.loads(_STRICT_SCHEMA.read_text(encoding="utf-8")), "schema")
    canonical = _object(
        json.loads(_CANONICAL_REGISTRY.read_text(encoding="utf-8")), "canonical registry"
    )
    formal_gates = _list(payload.get("formal_gates"), "formal_gates")
    fingerprints = [json.dumps(item, sort_keys=True) for item in formal_gates]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError(
            "registry violates Draft 2020-12 schema scientific invariants: "
            "formal_gates items must be unique"
        )
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = "registry" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}" for item in error.absolute_path
        )
        label = {
            "required": "missing required properties",
            "additionalProperties": "unknown properties",
            "const": "does not match its registered constant",
            "format": "must be an absolute URI",
            "uniqueItems": "items must be unique",
        }.get(error.validator, error.message)
        raise ValueError(f"registry violates Draft 2020-12 schema at {location}: {label}")

    source = _object(payload.get("source"), "source")
    for field in ("official_record", "descriptor_doi"):
        value = source.get(field)
        if not isinstance(value, str):
            raise TypeError(f"source.{field} must be a string")
        parsed = urlsplit(value)
        if not parsed.scheme or (parsed.scheme in {"http", "https"} and not parsed.netloc):
            raise ValueError(f"source.{field} must be an absolute URI")
    _assert_exact_property_shape(payload, canonical, "registry")


def load_early_warning_registry(path: Path) -> EarlyWarningRegistry:
    """Load the reviewed JSON contract and reject internally inconsistent variants."""
    payload = _object(json.loads(path.read_text(encoding="utf-8")), "registry")
    if payload.get("$schema") != "./early_warning_task_registry.schema.json":
        raise ValueError("registry must identify its local strict schema")
    schema_version = payload.get("schema_version")
    if schema_version != 1:
        raise ValueError(f"unsupported early-warning registry schema: {schema_version}")
    lineage = payload.get("protocol_lineage")
    status = payload.get("status")
    if not isinstance(lineage, str) or not lineage.strip():
        raise ValueError("protocol_lineage must be non-empty")
    if not isinstance(status, str) or "formal_training_blocked" not in status:
        raise ValueError("v0 registry must keep formal training blocked")

    snapshot = _object(payload.get("snapshot_contract"), "snapshot_contract")
    horizon = _object(snapshot.get("primary_horizon"), "primary_horizon")
    if horizon.get("type") != "completed_checkups":
        raise ValueError("primary horizon must use completed_checkups")
    if horizon.get("status") != "fixed_by_protocol":
        raise ValueError("primary completed-checkup horizon must be fixed by protocol")
    horizon_value = horizon.get("value")
    if not isinstance(horizon_value, int) or isinstance(horizon_value, bool) or horizon_value < 1:
        raise ValueError("primary completed-checkup horizon must be a positive integer")
    if snapshot.get("insufficient_followup") != "censored_not_negative":
        raise ValueError("insufficient follow-up must remain censored")

    capacity = _object(payload.get("capacity_contract"), "capacity_contract")
    q0 = _object(capacity.get("q0"), "capacity_contract.q0")
    required_q0_provenance = [
        "source_archive",
        "source_member_or_file",
        "physical_cell_id",
        "source_row_locator",
        "measurement_timestamp",
        "endpoint_selection_rule",
        "raw_value",
        "unit",
        "source_manifest_hash",
    ]
    if q0.get("provenance_required") is not True:
        raise ValueError("Q0 provenance must be required")
    if q0.get("required_provenance_fields") != required_q0_provenance:
        raise ValueError("Q0 provenance field contract changed")
    if q0.get("missing_provenance_behavior") != "snapshot_ineligible_fail_closed":
        raise ValueError("missing Q0 provenance must fail closed")
    forbidden = " ".join(str(item) for item in _list(q0.get("forbidden"), "q0.forbidden"))
    if ("future" not in forbidden and "after" not in forbidden) or "maximum" not in forbidden:
        raise ValueError("Q0 contract must prohibit lifetime maxima and future capacity")
    if capacity.get("source_soh_imp_role") != "forbidden_as_feature_or_label":
        raise ValueError("source soh_imp must remain prohibited")

    raw_views = _list(payload.get("feature_views"), "feature_views")
    views: list[RegisteredFeatureView] = []
    for index, raw in enumerate(raw_views):
        item = _object(raw, f"feature_views[{index}]")
        view_id = item.get("id")
        blocks = item.get("blocks")
        if not isinstance(view_id, str) or not view_id:
            raise ValueError("feature view ID must be non-empty")
        if not isinstance(blocks, list) or not all(isinstance(block, str) for block in blocks):
            raise ValueError(f"feature blocks for {view_id} must be strings")
        views.append(RegisteredFeatureView(view_id, tuple(blocks)))
    if len({view.view_id for view in views}) != len(views):
        raise ValueError("duplicate feature-view ID")
    expected_views = {"A", "B", "C", "D_CORE", "D_FULL"}
    if {view.view_id for view in views} != expected_views:
        raise ValueError(f"feature views must be exactly {sorted(expected_views)}")
    blocks_by_view = {view.view_id: view.blocks for view in views}
    expected_blocks = {
        "A": ("A",),
        "B": ("A", "B_CONTEXT"),
        "C": ("C_EIS", "B_CONTEXT"),
        "D_CORE": ("A", "B_CONTEXT", "C_EIS"),
        "D_FULL": ("A", "B_CONTEXT", "C_EIS", "D_AUX"),
    }
    if blocks_by_view != expected_blocks:
        raise ValueError("A/B/C/D_CORE/D_FULL blocks must exactly match the reviewed contract")

    raw_outcomes = _list(payload.get("outcomes"), "outcomes")
    outcomes: list[RegisteredOutcome] = []
    for index, raw in enumerate(raw_outcomes):
        item = _object(raw, f"outcomes[{index}]")
        outcome_id = item.get("id")
        family = item.get("family")
        outcome_horizon = item.get("horizon_checkups")
        if not isinstance(outcome_id, str) or not isinstance(family, str):
            raise TypeError("outcome ID and family must be strings")
        if outcome_horizon is not None and (
            not isinstance(outcome_horizon, int)
            or isinstance(outcome_horizon, bool)
            or outcome_horizon < 1
        ):
            raise ValueError(f"invalid checkup horizon for {outcome_id}")
        outcomes.append(
            RegisteredOutcome(
                outcome_id,
                family,
                outcome_horizon,
                item.get("threshold_status")
                if isinstance(item.get("threshold_status"), str)
                else None,
            )
        )
    if len({outcome.outcome_id for outcome in outcomes}) != len(outcomes):
        raise ValueError("duplicate outcome ID")
    by_id = {outcome.outcome_id: outcome for outcome in outcomes}
    for required in ("O1_THRESHOLD_80_H3", "O2_RAPID_LOSS_5PP_H3"):
        if required not in by_id or by_id[required].horizon_checkups != horizon_value:
            raise ValueError(f"{required} must use the primary completed-checkup horizon")
    raw_by_id = {
        str(_object(item, "outcome")["id"]): _object(item, "outcome") for item in raw_outcomes
    }
    o1 = raw_by_id["O1_THRESHOLD_80_H3"]
    if (
        o1.get("eligibility") != "snapshot conventional SoH is above threshold"
        or o1.get("event_observation") != "discrete_completed_checkup_index"
        or o1.get("threshold_soh_percent") != 80.0
    ):
        raise ValueError("O1 eligibility, discrete observation, or threshold changed")
    if o1.get("true_crossing_time") != (
        "interval_censored_between_last_observed_above_and_first_observed_at_or_below"
    ):
        raise ValueError("O1 must not claim an exact continuous crossing time")
    o2 = raw_by_id["O2_RAPID_LOSS_5PP_H3"]
    if o2.get("loss_threshold_percentage_points") != 5.0 or "third future" not in str(
        o2.get("event")
    ):
        raise ValueError("O2 must compare with exactly the third future completed checkup")
    r1 = raw_by_id["R1_CONDITIONAL_RUL_80"]
    r1_representation = _object(r1.get("event_representation"), "R1 event representation")
    if (
        r1.get("type") != "interval_or_right_censored_time_to_observed_event"
        or r1_representation.get("exact_continuous_crossing_claim") != "prohibited"
        or r1.get("interval_and_right_censoring") != "required"
    ):
        raise ValueError("R1 must preserve interval and right censoring")
    expected_feature_views = ["A", "B", "C", "D_CORE", "D_FULL"]
    for outcome_id, raw in raw_by_id.items():
        if raw.get("eligible_feature_views") != expected_feature_views:
            raise ValueError(f"eligible feature-view contract changed for {outcome_id}")

    validation = _object(payload.get("validation"), "validation")
    primary = _object(validation.get("primary"), "validation.primary")
    if primary.get("outer_group") != "condition_id_all_three_replicates":
        raise ValueError("primary evaluation must hold out complete condition groups")
    split_construction = primary.get("split_construction")
    if not isinstance(split_construction, str) or not split_construction.startswith(
        "metadata_only_before_protected_outcome_read"
    ):
        raise ValueError("protected split construction must remain metadata-only")
    if primary.get("event_support_use") not in {None, "prohibited"}:
        raise ValueError("protected split construction cannot use event support")

    metrics = _object(payload.get("metrics"), "metrics")
    binary_metrics = tuple(str(item) for item in _list(metrics.get("binary"), "metrics.binary"))
    survival_metrics = tuple(
        str(item) for item in _list(metrics.get("survival"), "metrics.survival")
    )
    if binary_metrics != EXPECTED_BINARY_METRICS:
        raise ValueError("registered binary metric contract changed")
    if survival_metrics != EXPECTED_SURVIVAL_METRICS:
        raise ValueError("registered survival metric contract changed")
    if metrics.get("uncertainty_unit") != "condition_group":
        raise ValueError("metric uncertainty unit must remain condition_group")
    if metrics.get("uncensored_rul_mae_role") != "descriptive_only":
        raise ValueError("uncensored RUL MAE must remain descriptive only")

    action_policy = _object(payload.get("action_policy"), "action_policy")
    action_status = action_policy.get("status")
    if not isinstance(action_status, str) or "blocked" not in action_status:
        raise ValueError("registry action policy must remain blocked pending review")
    if action_policy.get("production_defaults_allowed") is not False:
        raise ValueError("production recommendation defaults must remain prohibited")
    if action_policy.get("human_review_and_acknowledgement_required_for_all_levels") is not True:
        raise ValueError("all recommendation levels must require human review")
    if action_policy.get("rul_policy_scope") != (
        "separate thresholds by ageing mode, exposure unit and declared future-use scenario"
    ):
        raise ValueError("RUL action thresholds must remain mode/unit/scenario specific")
    formal_states = _object(payload.get("formal_gate_states"), "formal_gate_states")
    if not formal_states or set(formal_states.values()) != {"blocked"}:
        raise ValueError("all formal v0 gates must remain blocked")
    _validate_complete_registry(payload)
    return EarlyWarningRegistry(
        schema_version,
        lineage,
        status,
        horizon_value,
        tuple(views),
        tuple(outcomes),
        binary_metrics,
        survival_metrics,
        action_status,
    )
