from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from runpy import run_path
from typing import cast

from battery_eis_soh.kit_target_readiness import audit_target_readiness
from battery_eis_soh.kit_v2 import (
    CapacityEndpoint,
    CellIdentity,
    KitV2DevelopmentRecords,
    Q0Record,
    RowProvenance,
    VerifiedSnapshotCompletionPolicy,
)


def _object_dict(value: object) -> dict[str, object]:
    """Narrow a dynamic audit section after checking its JSON-object shape."""

    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _provenance(cell: CellIdentity, index: int) -> RowProvenance:
    return RowProvenance(
        archive_name="cell_eocv2.zip",
        archive_sha256="a" * 64,
        member=cell.member,
        line_number=index + 2,
        timestamp_s=float(index * 86_400),
        timestamp_origin=1,
        duration_s=60.0,
    )


def _endpoint(
    cell: CellIdentity,
    *,
    index: int,
    capacity: float,
    age_type: int,
) -> CapacityEndpoint:
    return CapacityEndpoint(
        cell=cell,
        capacity_ah=capacity,
        provenance=_provenance(cell, index),
        age_type=age_type,
        age_temperature_c=25,
        age_soc_percent=50,
        age_charge_rate_c=1.0,
        age_discharge_rate_c=1.0,
        age_profile=0 if age_type != 3 else 1,
        operating_cycles=index * 10,
        checkup_cycles=index,
        total_charge_ah=float(index * 3),
        total_discharge_ah=float(index * 3),
    )


def _records(*, omit_q0_for: str | None = None) -> KitV2DevelopmentRecords:
    endpoints: list[CapacityEndpoint] = []
    q0: list[Q0Record] = []
    for condition, age_type, losses in (
        (1, 1, [0.00, 0.01, 0.02, 0.03, 0.07, 0.09, 0.11]),
        (2, 2, [0.00, 0.01, 0.02, 0.03, 0.06, 0.10, 0.13]),
        (3, 3, [0.00, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12]),
    ):
        cell = CellIdentity(
            condition, 1, 1, condition, f"cell_eocv2_P{condition:03d}_1_S01_C{condition:02d}.csv"
        )
        cell_endpoints = [
            _endpoint(cell, index=index, capacity=3.0 - loss, age_type=age_type)
            for index, loss in enumerate(losses)
        ]
        endpoints.extend(cell_endpoints)
        if cell.cell_id != omit_q0_for:
            q0.append(
                Q0Record(
                    cell=cell,
                    capacity_ah=3.0,
                    provenance=cell_endpoints[0].provenance,
                    ambiguity_flags=(),
                )
            )
    return KitV2DevelopmentRecords(
        eoc_archive={"path_name": "cell_eocv2.zip", "sha256": "a" * 64},
        eis_archive={"path_name": "cell_eisv2.zip", "sha256": "b" * 64},
        counts={"real_cells": 3, "capacity_endpoints": len(endpoints)},
        q0_records=tuple(q0),
        capacity_endpoints=tuple(endpoints),
        eis_spectra=(),
        frequency_registry_hz=(),
        missingness={},
    )


def test_h3_target_never_converts_right_edge_to_negative() -> None:
    audit = audit_target_readiness(_records())
    for mode in ("calendar", "cyclic", "profile"):
        eligibility = _object_dict(_object_dict(audit["snapshot_eligibility_by_mode"])[mode])
        assert eligibility["total_snapshots"] == 7
        assert eligibility["h3_target_eligible"] == 4
        assert eligibility["f1_model_candidate_eligible"] == 2
        assert eligibility["no_h3_followup"] == 3
    assert _object_dict(audit["right_edge_followup"])["missing_h3_is_negative_or_zero"] is False


def test_signed_f1_o2_thresholds_and_gray_band_are_descriptive() -> None:
    audit = audit_target_readiness(_records())
    profile = _object_dict(_object_dict(audit["o2_candidate_prevalence_by_mode"])["profile"])
    positive_at_4 = _object_dict(profile["threshold_4_pp"])["positive_snapshots"]
    positive_at_6 = _object_dict(profile["threshold_6_pp"])["positive_snapshots"]
    assert isinstance(positive_at_4, int)
    assert isinstance(positive_at_6, int)
    assert positive_at_4 >= positive_at_6
    assert _object_dict(audit["o2_gray_band"])["not_a_measurement_noise_estimate"] is True
    repeatability = _object_dict(audit["capacity_repeatability_evidence"])
    assert repeatability["verified_technical_repeat_groups"] == 0
    assert repeatability["physical_replicate_spread_is_measurement_repeatability"] is False


def test_unverified_completion_semantics_blocks_f1_and_o2() -> None:
    audit = audit_target_readiness(_records())
    go_no_go = _object_dict(audit["go_no_go"])
    f1 = _object_dict(go_no_go["f1_development_oof"])
    assert f1["decision"] == "NO_GO"
    assert "snapshot completion semantics remain unverified" in cast("list[str]", f1["reasons"])
    assert _object_dict(go_no_go["o2_candidate_reporting"])["decision"] == "NO_GO"


def test_evidence_backed_completion_can_clear_f1_but_not_noise_review() -> None:
    audit = audit_target_readiness(
        _records(),
        completion_policy=VerifiedSnapshotCompletionPolicy(
            mode="timestamp_plus_duration_is_completion",
            evidence_id="reviewed-synthetic-fixture",
        ),
    )
    go_no_go = _object_dict(audit["go_no_go"])
    assert _object_dict(go_no_go["f1_development_oof"])["decision"] == "GO"
    assert _object_dict(go_no_go["o2_candidate_reporting"])["decision"] == "NO_GO"


def test_missing_q0_is_reported_and_never_repaired() -> None:
    missing_cell = "P002_1_S01_C02"
    audit = audit_target_readiness(_records(omit_q0_for=missing_cell))
    assert _object_dict(audit["q0"])["missing_cells"] == [missing_cell]
    modes = _object_dict(audit["snapshot_eligibility_by_mode"])
    assert _object_dict(modes["cyclic"])["snapshots_missing_q0"] == 7
    go_no_go = _object_dict(audit["go_no_go"])
    assert _object_dict(go_no_go["f1_development_oof"])["decision"] == "NO_GO"


def test_ambiguous_q0_blocks_f1() -> None:
    records = _records()
    ambiguous_q0 = replace(records.q0_records[0], ambiguity_flags=("multiple_q0_candidates",))
    audit = audit_target_readiness(
        replace(records, q0_records=(ambiguous_q0, *records.q0_records[1:])),
        completion_policy=VerifiedSnapshotCompletionPolicy(
            mode="timestamp_plus_duration_is_completion",
            evidence_id="reviewed-synthetic-fixture",
        ),
    )
    assert _object_dict(audit["q0"])["ambiguous_cells"] == [ambiguous_q0.cell.cell_id]
    go_no_go = _object_dict(audit["go_no_go"])
    assert _object_dict(go_no_go["f1_development_oof"])["decision"] == "NO_GO"


def test_script_rejects_non_artifact_repository_output() -> None:
    script_path = Path("scripts/audit_kit_target_readiness.py").resolve()
    namespace = run_path(str(script_path))
    safe_output = namespace["_safe_output"]
    try:
        safe_output(Path("docs/unsafe.json"))
    except ValueError as exc:
        assert "ignored artifacts" in str(exc)
    else:
        raise AssertionError("repository-local output outside artifacts must fail closed")
