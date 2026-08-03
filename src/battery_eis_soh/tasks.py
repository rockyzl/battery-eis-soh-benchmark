from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import numpy as np
from numpy.typing import NDArray

from .dataset import BatteryDataset

TargetName = Literal["soh_percent", "capacity_mah", "capacity_loss_mah"]
FeatureView = Literal["eis", "eis_q0", "cycle", "cycle_q0"]

TARGET_NAMES: tuple[TargetName, ...] = (
    "soh_percent",
    "capacity_mah",
    "capacity_loss_mah",
)
FEATURE_VIEWS: tuple[FeatureView, ...] = ("eis", "eis_q0", "cycle", "cycle_q0")


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    features: FeatureView | Literal["none"]
    target: TargetName
    allowed_smoke_models: tuple[str, ...]


def soh_fraction(dataset: BatteryDataset) -> NDArray[np.float64]:
    """Validate and return the physical capacity/Q0 definition of SoH."""
    capacity = np.asarray(dataset.capacities_mah, dtype=np.float64)
    q0 = np.asarray(dataset.initial_capacities_mah, dtype=np.float64)
    cached = np.asarray(dataset.y, dtype=np.float64)
    if capacity.shape != q0.shape or capacity.shape != cached.shape:
        raise ValueError("capacity, Q0 and cached SoH shapes differ")
    if np.any(~np.isfinite(capacity)) or np.any(~np.isfinite(q0)) or np.any(q0 <= 0):
        raise ValueError("capacity and Q0 must be finite, with positive Q0")
    derived = capacity / q0
    if not np.allclose(cached, derived, rtol=1e-12, atol=1e-12):
        raise ValueError("cached SoH does not equal current capacity / Q0")
    return derived


def load_task_registry(path: Path) -> dict[str, TaskSpec]:
    """Load the reviewed task registry used as the executable source of truth."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_tasks = [
        payload["primary_task"],
        *payload["ablations"],
        *payload["required_non_eis_baselines"],
    ]
    registry: dict[str, TaskSpec] = {}
    for raw in raw_tasks:
        task_id = str(raw["id"])
        if task_id in registry:
            raise ValueError(f"duplicate task ID: {task_id}")
        target = str(raw["target"])
        features = str(raw["features"])
        models = tuple(str(model) for model in raw["allowed_smoke_models"])
        if target not in TARGET_NAMES:
            raise ValueError(f"unsupported registry target for {task_id}: {target}")
        if features not in {*FEATURE_VIEWS, "none"}:
            raise ValueError(f"unsupported registry feature view for {task_id}: {features}")
        if not models:
            raise ValueError(f"task {task_id} has no allowed smoke models")
        registry[task_id] = TaskSpec(
            task_id=task_id,
            features=cast(FeatureView | Literal["none"], features),
            target=cast(TargetName, target),
            allowed_smoke_models=models,
        )
    return registry


def target_values(dataset: BatteryDataset, target: TargetName) -> NDArray[np.float64]:
    """Return the registered training target without clipping scientific values."""
    if target == "soh_percent":
        values = 100.0 * soh_fraction(dataset)
    elif target == "capacity_mah":
        values = dataset.capacities_mah
    elif target == "capacity_loss_mah":
        values = dataset.initial_capacities_mah - dataset.capacities_mah
    else:  # pragma: no cover - static typing plus caller validation should make this unreachable
        raise ValueError(f"unsupported target: {target}")
    values = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise ValueError(f"non-finite values in target {target}")
    return values


def design_matrix(dataset: BatteryDataset, view: FeatureView) -> NDArray[np.float64]:
    """Build a registered feature view; Q0 always means explicit cycle-0 capacity."""
    q0 = dataset.initial_capacities_mah.reshape(-1, 1)
    cycle = dataset.cycles.astype(np.float64).reshape(-1, 1)
    if view == "eis":
        matrix = dataset.X
    elif view == "eis_q0":
        matrix = np.column_stack((dataset.X, q0))
    elif view == "cycle":
        matrix = cycle
    elif view == "cycle_q0":
        matrix = np.column_stack((cycle, q0))
    else:  # pragma: no cover
        raise ValueError(f"unsupported feature view: {view}")
    matrix = np.asarray(matrix, dtype=np.float64)
    if not np.all(np.isfinite(matrix)):
        raise ValueError(f"non-finite values in feature view {view}")
    return matrix


def prediction_outputs(
    prediction: NDArray[np.float64],
    target: TargetName,
    initial_capacity_mah: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Convert a model output into capacity and SoH percent without hiding extrapolation."""
    prediction = np.asarray(prediction, dtype=np.float64).reshape(-1)
    q0 = np.asarray(initial_capacity_mah, dtype=np.float64).reshape(-1)
    if prediction.shape != q0.shape:
        raise ValueError("prediction and initial-capacity shapes differ")
    if np.any(~np.isfinite(q0)) or np.any(q0 <= 0):
        raise ValueError("initial capacity must be finite and positive")
    if target == "soh_percent":
        soh_percent = prediction
        capacity_mah = q0 * soh_percent / 100.0
    elif target == "capacity_mah":
        capacity_mah = prediction
        soh_percent = 100.0 * capacity_mah / q0
    elif target == "capacity_loss_mah":
        capacity_mah = q0 - prediction
        soh_percent = 100.0 * capacity_mah / q0
    else:  # pragma: no cover
        raise ValueError(f"unsupported target: {target}")
    if not np.all(np.isfinite(capacity_mah)) or not np.all(np.isfinite(soh_percent)):
        raise ValueError(f"non-finite converted predictions from target {target}")
    return capacity_mah, soh_percent
