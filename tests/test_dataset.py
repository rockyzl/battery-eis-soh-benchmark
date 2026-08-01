from pathlib import Path

import numpy as np
import pytest

from battery_eis_soh.dataset import load_state_dataset


def _write_fixture(
    root: Path, cell: str, incomplete: bool = False, include_eis_header: bool = True
) -> None:
    eis = root / "EIS data"
    capacity = root / "Capacity data"
    eis.mkdir(parents=True, exist_ok=True)
    capacity.mkdir(parents=True, exist_ok=True)
    eis_rows = []
    if include_eis_header:
        eis_rows.append("time/s cycle number freq/Hz Re(Z)/Ohm -Im(Z)/Ohm |Z|/Ohm Phase(Z)/deg")
    for cycle in (1, 2):
        points = (100.0, 10.0, 1.0) if not (incomplete and cycle == 2) else (100.0, 10.0)
        for index, frequency in enumerate(points):
            eis_rows.append(
                f"{cycle * 10 + index} {cycle} {frequency} {cycle + index} {cycle - index} 0 0"
            )
    (eis / f"EIS_state_V_{cell}.txt").write_text("\n".join(eis_rows) + "\n")
    capacity_rows = ["time/s cycle number ox/red Ewe/V I/mA Capacity/mA.h"]
    capacity_rows.extend(["0 0 1 4.2 45 49", "1 0 0 3.5 -90 50"])
    for cycle, value in ((1, 40.0), (2, 32.0)):
        capacity_rows.extend(
            [
                f"{cycle * 10} {cycle} 1 4.2 45 39",
                f"{cycle * 10 + 1} {cycle} 0 3.5 -90 {value}",
            ]
        )
    (capacity / f"Data_Capacity_{cell}.txt").write_text("\n".join(capacity_rows) + "\n")


def test_loads_and_normalizes_by_cell(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "25C01")
    dataset = load_state_dataset(tmp_path, expected_frequency_count=3)
    assert dataset.X.shape == (2, 6)
    assert dataset.cells.tolist() == ["25C01", "25C01"]
    # EIS diagnostic cycle k follows capacity diagnostic cycle k-1.
    assert np.allclose(dataset.y, [1.0, 0.8])
    assert np.allclose(dataset.capacities_mah, [50.0, 40.0])
    assert dataset.audit.rows_by_cell == {"25C01": 2}


def test_drops_incomplete_spectrum_with_audit(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "35C01", incomplete=True)
    dataset = load_state_dataset(tmp_path, expected_frequency_count=3)
    assert dataset.X.shape == (1, 6)
    assert dataset.audit.dropped_incomplete_by_cell == {"35C01": 1}


def test_accepts_source_files_without_eis_header(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "45C01", include_eis_header=False)
    dataset = load_state_dataset(tmp_path, expected_frequency_count=3)
    assert dataset.audit.rows_by_cell == {"45C01": 2}


def test_requires_explicit_cycle_zero_reference(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "25C01")
    capacity_path = tmp_path / "Capacity data" / "Data_Capacity_25C01.txt"
    rows = capacity_path.read_text().splitlines()
    capacity_path.write_text("\n".join([rows[0], *rows[3:]]) + "\n")
    with np.testing.assert_raises_regex(ValueError, "missing cycle=0 reference"):
        load_state_dataset(tmp_path, expected_frequency_count=3)


def test_first_public_source_pairing_when_local_data_are_available() -> None:
    root = Path("data/raw/extracted")
    if not root.is_dir():
        pytest.skip("public source archives are intentionally not stored in Git")
    dataset = load_state_dataset(root)
    assert dataset.audit.cells == (
        "25C01",
        "25C02",
        "25C03",
        "25C04",
        "25C05",
        "25C06",
        "25C07",
        "25C08",
        "35C01",
        "35C02",
        "45C01",
        "45C02",
    )
    assert dataset.audit.frequency_count == 60
    assert dataset.X.shape == (2597, 120)
    assert len(set(zip(dataset.cells.tolist(), dataset.cycles.tolist()))) == len(dataset.cells)
    first = np.flatnonzero((dataset.cells == "25C01") & (dataset.cycles == 1))
    assert first.size == 1
    assert dataset.capacities_mah[first[0]] == pytest.approx(37.20271, rel=1e-7)
