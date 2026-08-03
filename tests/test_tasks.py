import json
from pathlib import Path

import numpy as np

from battery_eis_soh.dataset import BatteryDataset, DatasetAudit
from battery_eis_soh.tasks import design_matrix, prediction_outputs, target_values


def _dataset() -> BatteryDataset:
    return BatteryDataset(
        X=np.asarray([[1.0, 2.0], [3.0, 4.0]]),
        y=np.asarray([1.02, 0.8]),
        cells=np.asarray(["25C01", "25C01"]),
        cycles=np.asarray([1, 2], dtype=np.int64),
        temperatures_c=np.asarray([25, 25], dtype=np.int64),
        capacities_mah=np.asarray([51.0, 40.0]),
        initial_capacities_mah=np.asarray([50.0, 50.0]),
        frequencies_hz=np.asarray([100.0]),
        audit=DatasetAudit("V", ("25C01",), {}, {}, {}, 1),
    )


def test_registered_targets_preserve_physical_scale() -> None:
    dataset = _dataset()
    assert np.allclose(target_values(dataset, "soh_percent"), [102.0, 80.0])
    assert np.allclose(target_values(dataset, "capacity_mah"), [51.0, 40.0])
    assert np.allclose(target_values(dataset, "capacity_loss_mah"), [-1.0, 10.0])


def test_q0_views_use_explicit_initial_capacity() -> None:
    dataset = _dataset()
    assert np.allclose(design_matrix(dataset, "eis_q0")[:, -1], [50.0, 50.0])
    assert np.allclose(design_matrix(dataset, "cycle_q0"), [[1.0, 50.0], [2.0, 50.0]])


def test_prediction_conversion_does_not_clip_soh_percent() -> None:
    q0 = np.asarray([50.0, 50.0])
    capacity, soh = prediction_outputs(np.asarray([102.0, -5.0]), "soh_percent", q0)
    assert np.allclose(capacity, [51.0, -2.5])
    assert np.allclose(soh, [102.0, -5.0])


def test_capacity_loss_prediction_converts_back_to_capacity_and_soh() -> None:
    capacity, soh = prediction_outputs(
        np.asarray([-1.0, 10.0]), "capacity_loss_mah", np.asarray([50.0, 50.0])
    )
    assert np.allclose(capacity, [51.0, 40.0])
    assert np.allclose(soh, [102.0, 80.0])


def test_target_registry_has_unique_tasks_and_explicit_q0_guards() -> None:
    registry = json.loads(Path("config/target_registry.json").read_text())
    tasks = [
        registry["primary_task"],
        *registry["ablations"],
        *registry["required_non_eis_baselines"],
    ]
    task_ids = [task["id"] for task in tasks]
    assert len(task_ids) == len(set(task_ids))
    assert {"T0", "T0Q", "T1", "T2", "T3"}.issubset(task_ids)
    assert registry["primary_task"]["target"] == "soh_percent"
    assert registry["primary_task"]["prediction_clipping"] == "none"
    forbidden = " ".join(registry["reference_capacity"]["forbidden_sources"])
    assert "maximum capacity" in forbidden
    assert "future cycle" in forbidden


def test_inconsistent_cached_soh_is_rejected() -> None:
    dataset = _dataset()
    dataset.y[0] = 0.5
    with np.testing.assert_raises_regex(ValueError, "capacity / Q0"):
        target_values(dataset, "soh_percent")
