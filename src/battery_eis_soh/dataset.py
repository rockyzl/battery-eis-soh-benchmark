from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

CELL_RE = re.compile(r"(?P<temperature>25|35|45)C(?P<replicate>\d{2})")
SELECTION_CELLS_25C = frozenset({"25C01", "25C02", "25C03", "25C04"})


@dataclass(frozen=True)
class DatasetAudit:
    state: str
    cells: tuple[str, ...]
    rows_by_cell: dict[str, int]
    dropped_incomplete_by_cell: dict[str, int]
    missing_capacity_by_cell: dict[str, int]
    frequency_count: int
    initial_capacity_mah_by_cell: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class BatteryDataset:
    X: NDArray[np.float64]
    y: NDArray[np.float64]
    cells: NDArray[np.str_]
    cycles: NDArray[np.int64]
    temperatures_c: NDArray[np.int64]
    capacities_mah: NDArray[np.float64]
    initial_capacities_mah: NDArray[np.float64]
    frequencies_hz: NDArray[np.float64]
    audit: DatasetAudit

    def subset(self, selected_cells: set[str]) -> BatteryDataset:
        mask = np.isin(self.cells, sorted(selected_cells))
        return BatteryDataset(
            X=self.X[mask],
            y=self.y[mask],
            cells=self.cells[mask],
            cycles=self.cycles[mask],
            temperatures_c=self.temperatures_c[mask],
            capacities_mah=self.capacities_mah[mask],
            initial_capacities_mah=self.initial_capacities_mah[mask],
            frequencies_hz=self.frequencies_hz.copy(),
            audit=self.audit,
        )


def _cell_from_path(path: Path) -> tuple[str, int]:
    match = CELL_RE.search(path.stem)
    if match is None:
        raise ValueError(f"cannot parse cell ID from {path.name}")
    return match.group(0), int(match.group("temperature"))


def _load_discharge_capacities(path: Path) -> dict[int, float]:
    capacities: dict[int, float] = {}
    with path.open(encoding="utf-8", errors="strict") as handle:
        header = next(handle).lower()
        if "cycle" not in header or "capacity" not in header or "ox/red" not in header:
            raise ValueError(f"unexpected capacity header in {path.name}: {header.strip()}")
        for line_number, line in enumerate(handle, start=2):
            parts = line.split()
            if not parts:
                continue
            if len(parts) < 4:
                raise ValueError(f"malformed capacity row {path.name}:{line_number}")
            raw_cycle = float(parts[1])
            raw_direction = float(parts[2])
            if not np.isfinite(raw_cycle) or not raw_cycle.is_integer():
                raise ValueError(f"non-integer capacity cycle {path.name}:{line_number}")
            if not np.isfinite(raw_direction) or not raw_direction.is_integer():
                raise ValueError(f"non-integer ox/red value {path.name}:{line_number}")
            cycle = int(raw_cycle)
            direction = int(raw_direction)
            capacity = float(parts[-1])
            if not np.isfinite(capacity):
                raise ValueError(f"non-finite capacity {path.name}:{line_number}")
            if capacity < 0:
                raise ValueError(f"negative capacity {path.name}:{line_number}")
            if direction == 0:
                capacities[cycle] = max(capacities.get(cycle, float("-inf")), capacity)
    return capacities


def _load_eis_spectra(path: Path) -> dict[int, NDArray[np.float64]]:
    rows: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    with path.open(encoding="utf-8", errors="strict") as handle:
        first_line = next(handle)
        first_lower = first_line.lower()
        has_header = "cycle" in first_lower and "freq" in first_lower and "re(z)" in first_lower
        lines = handle if has_header else chain((first_line,), handle)
        start_line = 2 if has_header else 1
        for line_number, line in enumerate(lines, start=start_line):
            parts = line.split()
            if not parts:
                continue
            if len(parts) < 7:
                raise ValueError(f"malformed EIS row {path.name}:{line_number}")
            raw_cycle = float(parts[1])
            values = (float(parts[2]), float(parts[3]), float(parts[4]))
            if not np.isfinite(raw_cycle) or not raw_cycle.is_integer():
                raise ValueError(f"non-integer EIS cycle {path.name}:{line_number}")
            if not np.all(np.isfinite(values)):
                raise ValueError(f"non-finite EIS value {path.name}:{line_number}")
            rows[int(raw_cycle)].append(values)
    return {cycle: np.asarray(values, dtype=np.float64) for cycle, values in rows.items()}


def load_state_dataset(
    data_root: Path,
    *,
    state: str = "V",
    expected_frequency_count: int = 60,
    selected_cells: frozenset[str] | None = None,
) -> BatteryDataset:
    eis_root = data_root / "EIS data"
    capacity_root = data_root / "Capacity data"
    if not eis_root.is_dir() or not capacity_root.is_dir():
        raise FileNotFoundError("data root must contain 'EIS data' and 'Capacity data'")

    samples: list[tuple[str, int, int, float, NDArray[np.float64]]] = []
    rows_by_cell: dict[str, int] = {}
    incomplete: dict[str, int] = {}
    missing_capacity: dict[str, int] = {}
    reference_capacity_by_cell: dict[str, float] = {}
    canonical_frequencies: NDArray[np.float64] | None = None

    eis_paths = sorted(eis_root.glob(f"EIS_state_{state}_*.txt"))
    if not eis_paths:
        raise FileNotFoundError(f"no state-{state} EIS files under {eis_root}")

    for eis_path in eis_paths:
        cell, temperature = _cell_from_path(eis_path)
        if selected_cells is not None and cell not in selected_cells:
            continue
        capacity_path = capacity_root / f"Data_Capacity_{cell}.txt"
        if not capacity_path.is_file():
            raise FileNotFoundError(f"missing capacity file for {cell}")
        spectra = _load_eis_spectra(eis_path)
        capacities = _load_discharge_capacities(capacity_path)
        if 0 not in capacities:
            raise ValueError(f"missing cycle=0 reference capacity for {cell}")
        reference_capacity_by_cell[cell] = capacities[0]
        complete: list[tuple[int, float, NDArray[np.float64]]] = []
        incomplete[cell] = 0
        missing_capacity[cell] = 0
        for cycle, spectrum in sorted(spectra.items()):
            if spectrum.shape != (expected_frequency_count, 3):
                incomplete[cell] += 1
                continue
            # State-V EIS is measured after the discharge-capacity diagnostic in the source
            # experiment. The authors' published model input pairs EIS cycle k with capacity
            # cycle k-1 (for example 25C01 EIS cycle 1 -> capacity cycle 0).
            capacity_cycle = cycle - 1
            if capacity_cycle not in capacities:
                missing_capacity[cell] += 1
                continue
            frequencies = spectrum[:, 0]
            if not np.all(np.diff(frequencies) < 0):
                raise ValueError(
                    f"frequencies are not strictly descending for {cell} cycle {cycle}"
                )
            if canonical_frequencies is None:
                canonical_frequencies = frequencies.copy()
            elif not np.allclose(frequencies, canonical_frequencies, rtol=1e-5, atol=1e-8):
                raise ValueError(f"frequency grid mismatch for {cell} cycle {cycle}")
            features = np.concatenate((spectrum[:, 1], spectrum[:, 2]))
            complete.append((cycle, capacities[capacity_cycle], features))
        if not complete:
            raise ValueError(f"no complete joined spectra for {cell}")
        reference_capacity = reference_capacity_by_cell[cell]
        if not np.isfinite(reference_capacity) or reference_capacity <= 0:
            raise ValueError(f"invalid reference capacity for {cell}")
        for cycle, capacity, features in complete:
            samples.append((cell, temperature, cycle, capacity, features))
        rows_by_cell[cell] = len(complete)

    if canonical_frequencies is None:
        raise ValueError("no valid frequency grid")
    if selected_cells is not None and set(rows_by_cell) != set(selected_cells):
        missing = sorted(set(selected_cells) - set(rows_by_cell))
        unexpected = sorted(set(rows_by_cell) - set(selected_cells))
        raise ValueError(
            f"selected-cell inventory mismatch; missing={missing}, unexpected={unexpected}"
        )
    identities = [(item[0], item[2]) for item in samples]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate joined (cell, EIS cycle) identity")
    return BatteryDataset(
        X=np.stack([item[4] for item in samples]),
        y=np.asarray([item[3] / reference_capacity_by_cell[item[0]] for item in samples]),
        cells=np.asarray([item[0] for item in samples]),
        cycles=np.asarray([item[2] for item in samples], dtype=np.int64),
        temperatures_c=np.asarray([item[1] for item in samples], dtype=np.int64),
        capacities_mah=np.asarray([item[3] for item in samples]),
        initial_capacities_mah=np.asarray(
            [reference_capacity_by_cell[item[0]] for item in samples]
        ),
        frequencies_hz=canonical_frequencies,
        audit=DatasetAudit(
            state=state,
            cells=tuple(sorted(rows_by_cell)),
            rows_by_cell=rows_by_cell,
            dropped_incomplete_by_cell=incomplete,
            missing_capacity_by_cell=missing_capacity,
            frequency_count=expected_frequency_count,
            initial_capacity_mah_by_cell=reference_capacity_by_cell,
        ),
    )


def load_selection_dataset(
    data_root: Path,
    *,
    state: str = "V",
    expected_frequency_count: int = 60,
) -> BatteryDataset:
    """Load only the four training cells; fixed-test capacity/Q0 files are never opened."""
    return load_state_dataset(
        data_root,
        state=state,
        expected_frequency_count=expected_frequency_count,
        selected_cells=SELECTION_CELLS_25C,
    )
