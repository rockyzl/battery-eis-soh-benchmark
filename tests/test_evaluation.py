from pathlib import Path

import numpy as np

from battery_eis_soh.dataset import BatteryDataset, DatasetAudit
from battery_eis_soh.evaluation import evaluate_paper_split, through_eol_mask


def test_through_eol_mask_is_group_local() -> None:
    cells = np.asarray(["25C01"] * 4 + ["25C02"] * 4)
    y = np.asarray([1.0, 0.9, 0.79, 0.7, 1.0, 0.95, 0.85, 0.81])
    dataset = BatteryDataset(
        X=np.zeros((8, 2)),
        y=y,
        cells=cells,
        cycles=np.tile(np.arange(1, 5), 2),
        temperatures_c=np.full(8, 25),
        capacities_mah=y * 40,
        initial_capacities_mah=np.full(8, 40.0),
        frequencies_hz=np.asarray([100.0]),
        audit=DatasetAudit("V", ("25C01", "25C02"), {}, {}, {}, 1),
    )
    assert through_eol_mask(dataset).tolist() == [True, True, True, False, True, True, True, True]


def test_fixed_split_is_group_disjoint_and_writes_unapproved_manifest(tmp_path: Path) -> None:
    train = ["25C01", "25C02", "25C03", "25C04"]
    test = ["25C05", "25C06", "25C07", "25C08"]
    cells = np.asarray([cell for cell in train + test for _ in range(3)])
    cycles = np.tile(np.arange(1, 4), 8)
    y = np.linspace(1.0, 0.7, len(cells))
    X = np.column_stack([y, y**2, cycles])
    dataset = BatteryDataset(
        X=X,
        y=y,
        cells=cells,
        cycles=cycles,
        temperatures_c=np.full(len(cells), 25),
        capacities_mah=y * 40,
        initial_capacities_mah=np.full(len(cells), 40.0),
        frequencies_hz=np.asarray([100.0]),
        audit=DatasetAudit("V", tuple(train + test), {}, {}, {}, 1),
    )
    output = tmp_path / "run"
    result = evaluate_paper_split(dataset, ["ridge"], output, Path.cwd())
    assert set(result.metrics) == {"ridge"}
    assert result.metrics["ridge"]["feature_view"] == "eis"
    assert result.metrics["ridge"]["task_id"] == "T0"
    assert "cell_macro_mae_percentage_points" in result.metrics["ridge"]
    manifest = (output / "run_manifest.json").read_text()
    assert '"scientific_result": false' in manifest
    assert '"publication_ready": false' in manifest
    assert '"resume_approved": false' in manifest
    assert '"formal_training_eligible": false' in manifest
    assert '"fixed_test_labels_loaded": true' in manifest
    assert '"model_target": "soh_percent"' in manifest
    assert '"task_id": "T0"' in manifest
    assert '"prediction_clipping": "none"' in manifest
    assert '"row_scope": "all_valid"' in manifest


def test_registered_q0_task_derives_feature_view_from_registry(tmp_path: Path) -> None:
    train = ["25C01", "25C02", "25C03", "25C04"]
    test = ["25C05", "25C06", "25C07", "25C08"]
    cells = np.asarray([cell for cell in train + test for _ in range(3)])
    cycles = np.tile(np.arange(1, 4), 8)
    y = np.linspace(1.0, 0.7, len(cells))
    dataset = BatteryDataset(
        X=np.column_stack([y, y**2]),
        y=y,
        cells=cells,
        cycles=cycles,
        temperatures_c=np.full(len(cells), 25),
        capacities_mah=y * 40,
        initial_capacities_mah=np.full(len(cells), 40.0),
        frequencies_hz=np.asarray([100.0]),
        audit=DatasetAudit("V", tuple(train + test), {}, {}, {}, 1),
    )
    result = evaluate_paper_split(dataset, ["ridge"], tmp_path / "q0", Path.cwd(), task_id="T0Q")
    assert result.metrics["ridge"]["feature_view"] == "eis_q0"
    assert result.metrics["ridge"]["task_id"] == "T0Q"


def test_unregistered_or_incompatible_task_is_rejected(tmp_path: Path) -> None:
    train = ["25C01", "25C02", "25C03", "25C04"]
    test = ["25C05", "25C06", "25C07", "25C08"]
    cells = np.asarray(train + test)
    dataset = BatteryDataset(
        X=np.ones((8, 2)),
        y=np.ones(8),
        cells=cells,
        cycles=np.ones(8, dtype=np.int64),
        temperatures_c=np.full(8, 25),
        capacities_mah=np.full(8, 40.0),
        initial_capacities_mah=np.full(8, 40.0),
        frequencies_hz=np.asarray([100.0]),
        audit=DatasetAudit("V", tuple(train + test), {}, {}, {}, 1),
    )
    with np.testing.assert_raises_regex(ValueError, "unregistered task ID"):
        evaluate_paper_split(dataset, ["ridge"], tmp_path / "bad-task", Path.cwd(), task_id="X")
    with np.testing.assert_raises_regex(ValueError, "models not allowed"):
        evaluate_paper_split(dataset, ["mean"], tmp_path / "bad-model", Path.cwd(), task_id="T0Q")
    with np.testing.assert_raises_regex(ValueError, "must not be empty"):
        evaluate_paper_split(dataset, [], tmp_path / "empty", Path.cwd(), task_id="T0")
    with np.testing.assert_raises_regex(ValueError, "contains duplicates"):
        evaluate_paper_split(
            dataset, ["ridge", "ridge"], tmp_path / "duplicate", Path.cwd(), task_id="T0"
        )


def test_eol_scope_is_explicit_sensitivity_not_default(tmp_path: Path) -> None:
    train = ["25C01", "25C02", "25C03", "25C04"]
    test = ["25C05", "25C06", "25C07", "25C08"]
    cells = np.asarray([cell for cell in train + test for _ in range(4)])
    cycles = np.tile(np.arange(1, 5), 8)
    y = np.tile(np.asarray([1.0, 0.9, 0.79, 0.7]), 8)
    dataset = BatteryDataset(
        X=np.column_stack([y, y**2]),
        y=y,
        cells=cells,
        cycles=cycles,
        temperatures_c=np.full(len(cells), 25),
        capacities_mah=y * 40,
        initial_capacities_mah=np.full(len(cells), 40.0),
        frequencies_hz=np.asarray([100.0]),
        audit=DatasetAudit("V", tuple(train + test), {}, {}, {}, 1),
    )
    all_valid = tmp_path / "all"
    through_eol = tmp_path / "eol"
    evaluate_paper_split(dataset, ["mean"], all_valid, Path.cwd(), task_id="B0")
    evaluate_paper_split(
        dataset,
        ["mean"],
        through_eol,
        Path.cwd(),
        task_id="B0",
        row_scope="through_eol",
    )
    all_manifest = (all_valid / "run_manifest.json").read_text()
    eol_manifest = (through_eol / "run_manifest.json").read_text()
    assert '"train": 16' in all_manifest
    assert '"train": 16' in eol_manifest
    assert '"test": 16' in all_manifest
    assert '"test": 12' in eol_manifest
