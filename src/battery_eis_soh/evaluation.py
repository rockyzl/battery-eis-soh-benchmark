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
    if name in {"cycle", "ridge"}:
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
    mask = np.ones(dataset.y.shape[0], dtype=np.bool_)
    for cell in dataset.audit.cells:
        indices = np.flatnonzero(dataset.cells == cell)
        if not len(indices):
            continue
        ordered = indices[np.argsort(dataset.cycles[indices])]
        crossings = ordered[dataset.y[ordered] <= threshold]
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
) -> EvaluationResult:
    available = set(dataset.cells.tolist())
    missing = (PAPER_TRAIN_CELLS | PAPER_TEST_CELLS) - available
    if missing:
        raise ValueError(f"paper split cells missing: {sorted(missing)}")
    eligible = through_eol_mask(dataset)
    train_mask = eligible & np.isin(dataset.cells, sorted(PAPER_TRAIN_CELLS))
    test_mask = eligible & np.isin(dataset.cells, sorted(PAPER_TEST_CELLS))
    if set(dataset.cells[train_mask]) & set(dataset.cells[test_mask]):
        raise AssertionError("cell leakage across train and test")

    output_dir.mkdir(parents=True, exist_ok=False)
    prediction_lines = ["model,cell,cycle,capacity_true_mah,capacity_pred_mah,soh_true,soh_pred"]
    all_metrics: dict[str, dict[str, object]] = {}
    for name in model_names:
        if name == "cycle":
            train_features = dataset.cycles[train_mask].reshape(-1, 1).astype(np.float64)
            test_features = dataset.cycles[test_mask].reshape(-1, 1).astype(np.float64)
        else:
            train_features = dataset.X[train_mask]
            test_features = dataset.X[test_mask]
        model = build_model(name, int(train_mask.sum()), train_features.shape[1])
        model.fit(train_features, dataset.capacities_mah[train_mask])
        capacity_prediction = np.asarray(model.predict(test_features)).reshape(-1)
        soh_prediction = capacity_prediction / dataset.initial_capacities_mah[test_mask]
        if not np.all(np.isfinite(capacity_prediction)) or not np.all(np.isfinite(soh_prediction)):
            raise ValueError(f"non-finite predictions from {name}")
        per_cell: dict[str, dict[str, float | None]] = {}
        for cell in sorted(PAPER_TEST_CELLS):
            cell_mask = test_mask & (dataset.cells == cell)
            prediction_cell_mask = dataset.cells[test_mask] == cell
            per_cell[cell] = _metrics(dataset.y[cell_mask], soh_prediction[prediction_cell_mask])
        macro_mae = float(np.mean([item["mae"] for item in per_cell.values()]))
        macro_rmse = float(np.mean([item["rmse"] for item in per_cell.values()]))
        all_metrics[name] = {
            "capacity_pooled_mah": _metrics(dataset.capacities_mah[test_mask], capacity_prediction),
            "soh_pooled": _metrics(dataset.y[test_mask], soh_prediction),
            "cell_macro_mae": macro_mae,
            "cell_macro_rmse": macro_rmse,
            "per_cell": per_cell,
        }
        for cell, cycle, capacity_true, capacity_pred, soh_true, soh_pred in zip(
            dataset.cells[test_mask],
            dataset.cycles[test_mask],
            dataset.capacities_mah[test_mask],
            capacity_prediction,
            dataset.y[test_mask],
            soh_prediction,
        ):
            prediction_lines.append(
                f"{name},{cell},{cycle},{capacity_true:.12g},{capacity_pred:.12g},"
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
        "resume_approved": False,
        "purpose": "engineering_smoke",
        "model_target": "absolute_discharge_capacity_mah",
        "reported_health_metric": "capacity_prediction / known_cycle_0_capacity",
        "split": {
            "train_cells": sorted(PAPER_TRAIN_CELLS),
            "test_cells": sorted(PAPER_TEST_CELLS),
            "through_first_soh_at_or_below": EOL_SOH_THRESHOLD,
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
            "protocol_sha256": _sha256_if_present(project_root / "docs/protocol-v1.md"),
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
