from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from battery_eis_soh import kit_f1_b_training
from battery_eis_soh.kit_f1_b_authorization import (
    BINDING_KEYS,
    FORBIDDEN,
    RUN_KIND,
    SCOPE,
    AuthorizationError,
    consume_authorization,
)
from battery_eis_soh.kit_f1_b_training import (
    ARTIFACT_NAMES,
    BOnlyRunError,
    _run_oof,
    execute_b_only_analysis,
    materialize_capacity_snapshots,
)
from battery_eis_soh.kit_f1_b_worker import (
    ENGINE_MODULE,
    validate_engine_contract,
)
from battery_eis_soh.kit_v2 import (
    CapacityEndpoint,
    CellIdentity,
    KitV2CapacityRecords,
    Q0Record,
    RowProvenance,
)


def _records() -> KitV2CapacityRecords:
    endpoints: list[CapacityEndpoint] = []
    q0: list[Q0Record] = []
    line = 2
    for mode, age_type in (("calendar", 1), ("cyclic", 2), ("profile", 3)):
        for condition_offset in range(3):
            condition = age_type * 10 + condition_offset
            cell = CellIdentity(
                condition,
                1,
                1,
                condition,
                f"cell_eocv2_P{condition:03d}_1_S01_C{condition:02d}.csv",
            )
            cell_endpoints: list[CapacityEndpoint] = []
            for sequence, counter in enumerate((0, 1, 4, 5, 9, 10, 11, 20)):
                provenance = RowProvenance(
                    "cell_eocv2.zip",
                    "a" * 64,
                    cell.member,
                    line,
                    float(condition * 1_000_000 + sequence * 86_400),
                    1,
                    10.0,
                )
                line += 1
                endpoint = CapacityEndpoint(
                    cell=cell,
                    capacity_ah=3.0 - 0.01 * sequence - 0.001 * condition_offset,
                    provenance=provenance,
                    age_type=age_type,
                    age_temperature_c=25,
                    age_soc_percent=50,
                    age_charge_rate_c=0.5,
                    age_discharge_rate_c=1.0,
                    age_profile=age_type if mode == "profile" else 0,
                    operating_cycles=sequence * 10,
                    checkup_cycles=counter,
                    total_charge_ah=float(sequence * 6),
                    total_discharge_ah=float(sequence * 6),
                )
                endpoints.append(endpoint)
                cell_endpoints.append(endpoint)
            first = cell_endpoints[0]
            q0.append(Q0Record(cell, first.capacity_ah, first.provenance, ()))
    return KitV2CapacityRecords(
        eoc_archive={"path_name": "cell_eocv2.zip", "sha256": "a" * 64},
        counts={"real_cells": 9, "capacity_endpoints": len(endpoints)},
        q0_records=tuple(q0),
        capacity_endpoints=tuple(endpoints),
    )


def _config_bundle(tmp_path: Path, mutation: dict[str, object] | None = None) -> Path:
    root = Path(__file__).parents[1]
    source_schema = root / "config/kit_f1_b_capacity_sequence_v1.schema.json"
    schema = tmp_path / source_schema.name
    schema.write_bytes(source_schema.read_bytes())
    payload = json.loads((root / "config/kit_f1_b_capacity_sequence_v1.json").read_text())
    payload["artifact_schema_sha256"] = hashlib.sha256(schema.read_bytes()).hexdigest()
    payload["readiness"]["execution_authorized"] = False
    if mutation:
        payload.update(mutation)
    config = tmp_path / "kit_f1_b_capacity_sequence_v1.json"
    config.write_text(json.dumps(payload))
    return config


def _authorization_receipt(
    tmp_path: Path,
    output: Path,
    binding_overrides: dict[str, str] | None = None,
) -> tuple[Path, Path, dict[str, str]]:
    bindings = {key: "a" * 64 for key in BINDING_KEYS}
    bindings.update(binding_overrides or {})
    issued = time.time()
    authorization = tmp_path / "authorization.json"
    authorization.write_text(
        json.dumps(
            {
                "authorization_version": "kit_f1_b_one_run_v1",
                "authorization_id": "test-independent-review",
                "status": "authorized",
                "single_use": True,
                "run_kind": RUN_KIND,
                "scope": SCOPE,
                "nonce": "b" * 64,
                "issued_at_unix_s": issued - 1,
                "expires_at_unix_s": issued + 60,
                "bindings": bindings,
                "forbidden": FORBIDDEN,
                "approver_role": "independent-reviewer",
                "evidence_reviewer": "test-reviewer",
            }
        )
    )
    receipt = consume_authorization(
        authorization,
        expected_bindings=bindings,
        output_dir=output,
        now_unix_s=issued,
    )
    return authorization, receipt, bindings


def test_h3_is_third_completed_sequence_not_counter_plus_three() -> None:
    snapshots = materialize_capacity_snapshots(_records())
    cell = snapshots[0].cell_id
    rows = [snapshot for snapshot in snapshots if snapshot.cell_id == cell]
    at_index_two = rows[2]
    assert at_index_two.h3_checkup_index == 5
    assert at_index_two.h3_endpoint_snapshot_id == rows[5].snapshot_id
    assert at_index_two.soh_loss_pp_h3 == pytest.approx(rows[2].soh_percent - rows[5].soh_percent)
    assert rows[-1].f1_target_available is False
    assert rows[-1].soh_loss_pp_h3 is None


def test_b_only_oof_has_exact_views_models_and_no_o2_or_eis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = materialize_capacity_snapshots(_records())
    monkeypatch.setattr(
        "battery_eis_soh.kit_v2._read_eis",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("EIS must be unreachable")),
    )
    folds, predictions, metrics, completed = _run_oof(snapshots, lambda event: None)
    assert set(folds) == {"calendar", "cyclic", "profile"}
    assert completed == 18
    assert predictions
    assert {row["view"] for row in predictions} == {"B"}
    assert {row["model_family"] for row in predictions} == {
        "persistence_zero_loss",
        "recent_slope_h3",
        "elastic_net_regression",
        "hist_gradient_boosting_regressor",
    }
    serialized = json.dumps([predictions, metrics]).lower()
    assert "d_core" not in serialized
    assert '"o2_' not in serialized
    assert '"eis_' not in serialized


@pytest.mark.parametrize(
    "poison",
    [
        {"feature_view": {"id": "D_CORE"}},
        {"o2": {"task_id": "O2_RAPID_LOSS_5PP_H3"}},
        {"eis_zip": "cell_eisv2.zip"},
    ],
)
def test_closed_config_schema_rejects_d_core_o2_and_eis(poison: dict[str, object]) -> None:
    root = Path(__file__).parents[1]
    schema = json.loads((root / "config/kit_f1_b_capacity_sequence_v1.schema.json").read_text())
    payload = json.loads((root / "config/kit_f1_b_capacity_sequence_v1.json").read_text())
    payload.update(poison)
    assert list(Draft202012Validator(schema).iter_errors(payload))


def test_execution_gate_blocks_before_capacity_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[1]
    monkeypatch.setattr(
        kit_f1_b_training,
        "load_kit_v2_capacity_records",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("loader must not run")),
    )
    with pytest.raises((AuthorizationError, FileNotFoundError)):
        execute_b_only_analysis(
            eoc_zip=tmp_path / "missing.zip",
            output_dir=tmp_path / "output",
            config_path=root / "config/kit_f1_b_capacity_sequence_v1.json",
            authorization_path=tmp_path / "missing-authorization.json",
            authorization_receipt=tmp_path / "missing-receipt.json",
            runtime_bindings={key: "a" * 64 for key in BINDING_KEYS},
            progress=lambda event: None,
        )
    assert not (tmp_path / "output").exists()


def test_artifact_contract_excludes_paired_outputs() -> None:
    assert ARTIFACT_NAMES == (
        "source_manifest.json",
        "snapshot_manifest.jsonl",
        "fold_registry.json",
        "oof_predictions.jsonl",
        "engine_summary.json",
    )
    assert "paired_deltas.json" not in ARTIFACT_NAMES


def test_worker_is_bound_to_independent_b_only_engine() -> None:
    contract = validate_engine_contract()
    assert ENGINE_MODULE == "battery_eis_soh.kit_f1_b_training"
    assert contract["module"] == ENGINE_MODULE
    assert contract["callable"] == "execute_b_only_analysis"
    assert "eis_zip" not in contract["signature"]
    assert "kit_dev_training" not in contract["module"]


def test_engine_rejects_non_b_view_even_if_schema_validation_is_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config_bundle(tmp_path)
    payload = json.loads(config.read_text())
    payload["feature_view"] = {**payload["feature_view"], "id": "D_CORE"}
    config.write_text(json.dumps(payload))
    monkeypatch.setattr(
        kit_f1_b_training.Draft202012Validator,
        "iter_errors",
        lambda self, instance: iter(()),
    )
    with pytest.raises(BOnlyRunError, match="only the exact.*B feature view"):
        authorization, receipt, bindings = _authorization_receipt(
            tmp_path,
            tmp_path / "output",
            {
                "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                "schema_sha256": hashlib.sha256(
                    config.with_name(f"{config.stem}.schema.json").read_bytes()
                ).hexdigest(),
            },
        )
        execute_b_only_analysis(
            eoc_zip=tmp_path / "unused.zip",
            output_dir=tmp_path / "output",
            config_path=config,
            authorization_path=authorization,
            authorization_receipt=receipt,
            runtime_bindings=bindings,
            progress=lambda event: None,
        )


def test_q0_ambiguity_fails_closed() -> None:
    records = _records()
    first = records.capacity_endpoints[0]
    duplicate = replace(first, provenance=replace(first.provenance, line_number=999))
    poisoned = replace(records, capacity_endpoints=(*records.capacity_endpoints, duplicate))
    with pytest.raises(BOnlyRunError, match="one check-up-zero"):
        materialize_capacity_snapshots(poisoned)


def test_future_q0_cannot_leak_into_earlier_snapshot() -> None:
    records = _records()
    first = records.capacity_endpoints[0]
    later = records.capacity_endpoints[1]
    poisoned_first = replace(first, checkup_cycles=8)
    poisoned_later = replace(later, checkup_cycles=0)
    endpoints = tuple(
        poisoned_first if item is first else poisoned_later if item is later else item
        for item in records.capacity_endpoints
    )
    poisoned = replace(
        records,
        capacity_endpoints=endpoints,
        q0_records=(
            replace(
                records.q0_records[0],
                capacity_ah=poisoned_later.capacity_ah,
                provenance=poisoned_later.provenance,
            ),
            *records.q0_records[1:],
        ),
    )
    with pytest.raises(BOnlyRunError, match="Q0 is not the first"):
        materialize_capacity_snapshots(poisoned)
