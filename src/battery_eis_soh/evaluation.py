from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from sklearn.base import RegressorMixin
from sklearn.cross_decomposition import PLSRegression
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .dataset import BatteryDataset
from .tasks import (
    design_matrix,
    load_task_registry,
    prediction_outputs,
    soh_fraction,
    target_values,
)

PAPER_TRAIN_CELLS = {"25C01", "25C02", "25C03", "25C04"}
PAPER_TEST_CELLS = {"25C05", "25C06", "25C07", "25C08"}
EOL_SOH_THRESHOLD = 0.8


class MeanRegressor:
    def fit(self, X: NDArray[np.float64], y: NDArray[np.float64]) -> MeanRegressor:
        del X
        self.mean_ = float(np.mean(y))
        return self

    def predict(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.full(X.shape[0], self.mean_, dtype=np.float64)


def build_model(name: str, n_samples: int, n_features: int) -> RegressorMixin | MeanRegressor:
    if name == "mean":
        return MeanRegressor()
    if name == "ridge":
        return make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    if name == "pls":
        components = min(10, n_features, n_samples - 1)
        return PLSRegression(n_components=components, scale=True, max_iter=1000)
    raise ValueError(f"unsupported smoke model: {name}")


def _metrics(y: NDArray[np.float64], pred: NDArray[np.float64]) -> dict[str, float | None]:
    return {
        "mae": float(mean_absolute_error(y, pred)),
        "rmse": float(mean_squared_error(y, pred) ** 0.5),
        "r2": float(r2_score(y, pred)) if y.size >= 2 else None,
    }


def through_eol_mask(
    dataset: BatteryDataset, threshold: float = EOL_SOH_THRESHOLD
) -> NDArray[np.bool_]:
    """Keep each cell through its first observation at or below the EOL threshold."""
    observed_soh = soh_fraction(dataset)
    mask = np.ones(observed_soh.shape[0], dtype=np.bool_)
    for cell in dataset.audit.cells:
        indices = np.flatnonzero(dataset.cells == cell)
        if not len(indices):
            continue
        ordered = indices[np.argsort(dataset.cycles[indices])]
        crossings = ordered[observed_soh[ordered] <= threshold]
        if len(crossings):
            mask[ordered[dataset.cycles[ordered] > dataset.cycles[crossings[0]]]] = False
    return mask


def _git_identity(root: Path) -> dict[str, str]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return {"commit": "not-a-repository", "working_tree": "unknown"}
    return {"commit": commit, "working_tree": "dirty" if status else "clean"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_if_present(path: Path) -> str:
    return _sha256(path) if path.is_file() else "not-present"


def _dataset_identity(dataset: BatteryDataset) -> str:
    digest = hashlib.sha256()
    for cell, cycle, capacity, initial, features in zip(
        dataset.cells,
        dataset.cycles,
        dataset.capacities_mah,
        dataset.initial_capacities_mah,
        dataset.X,
    ):
        digest.update(f"{cell}\0{cycle}\0{capacity:.17g}\0{initial:.17g}\0".encode())
        digest.update(np.asarray(features, dtype="<f8").tobytes())
    return digest.hexdigest()


def _split_identity(dataset: BatteryDataset, mask: NDArray[np.bool_]) -> str:
    lines = [f"{cell},{cycle}" for cell, cycle in zip(dataset.cells[mask], dataset.cycles[mask])]
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


@dataclass(frozen=True)
class EvaluationResult:
    metrics: dict[str, dict[str, object]]
    output_dir: Path


def evaluate_paper_split(
    dataset: BatteryDataset,
    model_names: list[str],
    output_dir: Path,
    project_root: Path,
    *,
    task_id: str = "T0",
    row_scope: str = "all_valid",
) -> EvaluationResult:
    available = set(dataset.cells.tolist())
    missing = (PAPER_TRAIN_CELLS | PAPER_TEST_CELLS) - available
    if missing:
        raise ValueError(f"paper split cells missing: {sorted(missing)}")
    task_registry_path = project_root / "config/target_registry.json"
    registry = load_task_registry(task_registry_path)
    if task_id not in registry:
        raise ValueError(f"unregistered task ID: {task_id}")
    task = registry[task_id]
    if not model_names:
        raise ValueError("model list must not be empty")
    if len(model_names) != len(set(model_names)):
        raise ValueError("model list contains duplicates")
    invalid_models = sorted(set(model_names) - set(task.allowed_smoke_models))
    if invalid_models:
        raise ValueError(f"models not allowed for task {task_id}: {invalid_models}")
    if row_scope not in {"all_valid", "through_eol"}:
        raise ValueError(f"unsupported row scope: {row_scope}")
    train_mask = np.isin(dataset.cells, sorted(PAPER_TRAIN_CELLS))
    test_mask = np.isin(dataset.cells, sorted(PAPER_TEST_CELLS))
    if row_scope == "through_eol":
        test_mask &= through_eol_mask(dataset)
    if set(dataset.cells[train_mask]) & set(dataset.cells[test_mask]):
        raise AssertionError("cell leakage across train and test")

    output_dir.mkdir(parents=True, exist_ok=False)
    prediction_lines = [
        (
            "model,cell,cycle,initial_capacity_mah,capacity_true_mah,capacity_pred_mah,"
            "soh_true_percent,soh_pred_percent"
        )
    ]
    all_metrics: dict[str, dict[str, object]] = {}
    observed_soh_percent = 100.0 * soh_fraction(dataset)
    target_all = target_values(dataset, task.target)
    feature_view = "eis" if task.features == "none" else task.features
    features = design_matrix(dataset, feature_view)
    for name in model_names:
        train_features = features[train_mask]
        test_features = features[test_mask]
        model = build_model(name, int(train_mask.sum()), train_features.shape[1])
        model.fit(train_features, target_all[train_mask])
        raw_prediction = np.asarray(model.predict(test_features)).reshape(-1)
        capacity_prediction, soh_prediction_percent = prediction_outputs(
            raw_prediction, task.target, dataset.initial_capacities_mah[test_mask]
        )
        soh_true_percent = observed_soh_percent[test_mask]
        per_cell: dict[str, dict[str, float | None]] = {}
        for cell in sorted(PAPER_TEST_CELLS):
            cell_mask = test_mask & (dataset.cells == cell)
            prediction_cell_mask = dataset.cells[test_mask] == cell
            per_cell[cell] = _metrics(
                observed_soh_percent[cell_mask], soh_prediction_percent[prediction_cell_mask]
            )
        macro_mae = float(np.mean([item["mae"] for item in per_cell.values()]))
        macro_rmse = float(np.mean([item["rmse"] for item in per_cell.values()]))
        all_metrics[name] = {
            "capacity_pooled_mah": _metrics(dataset.capacities_mah[test_mask], capacity_prediction),
            "soh_percent_pooled": _metrics(soh_true_percent, soh_prediction_percent),
            "cell_macro_mae_percentage_points": macro_mae,
            "cell_macro_rmse_percentage_points": macro_rmse,
            "per_cell": per_cell,
            "task_id": task.task_id,
            "feature_view": task.features,
        }
        for cell, cycle, q0, capacity_true, capacity_pred, soh_true, soh_pred in zip(
            dataset.cells[test_mask],
            dataset.cycles[test_mask],
            dataset.initial_capacities_mah[test_mask],
            dataset.capacities_mah[test_mask],
            capacity_prediction,
            soh_true_percent,
            soh_prediction_percent,
        ):
            prediction_lines.append(
                f"{name},{cell},{cycle},{q0:.12g},{capacity_true:.12g},{capacity_pred:.12g},"
                f"{soh_true:.12g},{soh_pred:.12g}"
            )

    predictions_path = output_dir / "predictions.csv"
    predictions_path.write_text("\n".join(prediction_lines) + "\n", encoding="utf-8")
    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(all_metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": 1,
        "scientific_result": False,
        "publication_ready": False,
        "resume_approved": False,
        "formal_training_eligible": False,
        "fixed_test_labels_loaded": True,
        "purpose": "engineering_smoke",
        "protocol_lineage": "v2_engineering_smoke",
        "task_id": task.task_id,
        "model_target": task.target,
        "feature_view": task.features,
        "primary_reported_metric": "cell_macro_soh_mae_percentage_points",
        "prediction_clipping": "none",
        "split": {
            "train_cells": sorted(PAPER_TRAIN_CELLS),
            "test_cells": sorted(PAPER_TEST_CELLS),
            "row_scope": row_scope,
            "eol_sensitivity_threshold": EOL_SOH_THRESHOLD,
        },
        "models": model_names,
        "rows": {"train": int(train_mask.sum()), "test": int(test_mask.sum())},
        "identity": {
            "dataset_rows_sha256": _dataset_identity(dataset),
            "train_cell_cycle_sha256": _split_identity(dataset, train_mask),
            "test_cell_cycle_sha256": _split_identity(dataset, test_mask),
            "source_manifest_sha256": _sha256_if_present(
                project_root / "data/source_manifest.json"
            ),
            "protocol_sha256": _sha256_if_present(project_root / "docs/protocol-v2.md"),
            "target_registry_sha256": _sha256(task_registry_path),
            "lockfile_sha256": _sha256_if_present(project_root / "uv.lock"),
        },
        "git": _git_identity(project_root),
        "artifacts": {
            "predictions.csv": _sha256(predictions_path),
            "metrics.json": _sha256(metrics_path),
        },
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return EvaluationResult(metrics=all_metrics, output_dir=output_dir)
