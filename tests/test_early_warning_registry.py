import json
from pathlib import Path

import pytest

from battery_eis_soh.early_warning_registry import load_early_warning_registry

REGISTRY = Path("config/early_warning_task_registry.json")


def _write_registry(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_loads_reviewed_early_warning_registry() -> None:
    registry = load_early_warning_registry(REGISTRY)
    assert registry.protocol_lineage == "kit-early-warning-v0"
    assert registry.primary_horizon_checkups == 3
    assert registry.feature_view_ids == {"A", "B", "C", "D_CORE", "D_FULL"}
    assert {"O1_THRESHOLD_80_H3", "O2_RAPID_LOSS_5PP_H3"} <= registry.outcome_ids
    assert "blocked" in registry.action_policy_status


def test_registry_rejects_exposure_primary_horizon(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["snapshot_contract"]["primary_horizon"]["type"] = "equivalent_full_cycles"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="completed_checkups"):
        load_early_warning_registry(path)


def test_registry_rejects_unblocked_action_policy(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["action_policy"]["status"] = "frozen"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="blocked"):
        load_early_warning_registry(path)


def test_registry_rejects_single_measurement_red_or_probability_red(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["action_policy"]["red_eligibility"] = "single_low_capacity_measurement"
    with pytest.raises(ValueError, match="registered constant"):
        load_early_warning_registry(_write_registry(tmp_path, payload))

    payload = json.loads(REGISTRY.read_text())
    payload["action_policy"]["model_probability_without_observed_confirmed_threshold_max_level"] = (
        "red"
    )
    with pytest.raises(ValueError, match="registered constant"):
        load_early_warning_registry(_write_registry(tmp_path, payload))


def test_registry_rejects_d_full_masquerading_as_d_core(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    for view in payload["feature_views"]:
        if view["id"] == "D_FULL":
            view["blocks"] = ["A", "B_CONTEXT", "C_EIS"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="D_FULL"):
        load_early_warning_registry(path)


def test_registry_rejects_missing_q0_provenance_contract(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["capacity_contract"]["q0"]["provenance_required"] = False
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Q0 provenance"):
        load_early_warning_registry(path)


def test_registry_rejects_open_formal_gate(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["formal_gate_states"]["formal_model_search"] = "open"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="formal v0 gates"):
        load_early_warning_registry(path)


def test_registry_rejects_unknown_top_level_and_nested_properties(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["unreviewed_override"] = True
    with pytest.raises(ValueError, match="unknown properties"):
        load_early_warning_registry(_write_registry(tmp_path, payload))

    payload = json.loads(REGISTRY.read_text())
    payload["metrics"]["unregistered_metric_group"] = []
    with pytest.raises(ValueError, match="unknown properties|property shape changed"):
        load_early_warning_registry(_write_registry(tmp_path, payload))


def test_registry_rejects_missing_deep_required_property(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    del payload["outcomes"][3]["event_representation"]["right_censor_bound"]
    with pytest.raises(ValueError, match="missing required properties"):
        load_early_warning_registry(_write_registry(tmp_path, payload))


@pytest.mark.parametrize(
    "view_id,blocks",
    [
        ("B", ["B_CONTEXT", "A"]),
        ("C", ["A", "C_EIS"]),
        ("D_CORE", ["A", "B_CONTEXT", "C_EIS", "D_AUX"]),
        ("D_FULL", ["A", "B_CONTEXT", "C_EIS"]),
    ],
)
def test_registry_rejects_any_feature_view_block_alias(
    tmp_path: Path, view_id: str, blocks: list[str]
) -> None:
    payload = json.loads(REGISTRY.read_text())
    next(view for view in payload["feature_views"] if view["id"] == view_id)["blocks"] = blocks
    with pytest.raises(ValueError, match="registered constant|exactly match"):
        load_early_warning_registry(_write_registry(tmp_path, payload))


def test_registry_validates_source_semantics_and_uri(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["source"]["expected_physical_cells"] = 227
    with pytest.raises(ValueError, match="registered constant"):
        load_early_warning_registry(_write_registry(tmp_path, payload))

    payload = json.loads(REGISTRY.read_text())
    payload["source"]["official_record"] = "not-a-uri"
    with pytest.raises(ValueError, match="absolute URI"):
        load_early_warning_registry(_write_registry(tmp_path, payload))


def test_registry_rejects_changed_outcome_science(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["outcomes"][0]["true_crossing_time"] = "exact_interpolated_time"
    with pytest.raises(ValueError, match="exact continuous crossing|registered constant"):
        load_early_warning_registry(_write_registry(tmp_path, payload))

    payload = json.loads(REGISTRY.read_text())
    payload["outcomes"][1]["loss_threshold_percentage_points"] = 4.0
    with pytest.raises(ValueError, match="O2|registered constant"):
        load_early_warning_registry(_write_registry(tmp_path, payload))


def test_registry_rejects_changed_or_duplicate_metrics(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["metrics"]["binary"][0] = "snapshot_micro_accuracy"
    with pytest.raises(ValueError, match="binary metric"):
        load_early_warning_registry(_write_registry(tmp_path, payload))

    payload = json.loads(REGISTRY.read_text())
    payload["metrics"]["binary"][1] = payload["metrics"]["binary"][0]
    with pytest.raises(ValueError, match="binary metric"):
        load_early_warning_registry(_write_registry(tmp_path, payload))


def test_draft_2020_12_schema_rejects_duplicate_formal_gate(tmp_path: Path) -> None:
    payload = json.loads(REGISTRY.read_text())
    payload["formal_gates"][1] = payload["formal_gates"][0]
    with pytest.raises(ValueError, match="Draft 2020-12 schema.*unique"):
        load_early_warning_registry(_write_registry(tmp_path, payload))
