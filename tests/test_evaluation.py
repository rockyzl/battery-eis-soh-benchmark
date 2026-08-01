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
    result = evaluate_paper_split(dataset, ["mean", "ridge"], output, tmp_path)
    assert set(result.metrics) == {"mean", "ridge"}
    manifest = (output / "run_manifest.json").read_text()
    assert '"scientific_result": false' in manifest
    assert '"resume_approved": false' in manifest
