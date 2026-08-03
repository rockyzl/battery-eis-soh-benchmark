"""Strict, streaming audit helpers for the KIT battery ageing v2 archives.

This module deliberately does not discover files, train models, or infer snapshot
completion semantics.  Callers must provide the two archive paths explicitly.
"""

from __future__ import annotations

import csv
import hashlib
import math
import os
import re
import stat
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from io import TextIOWrapper
from itertools import pairwise
from pathlib import Path
from typing import BinaryIO, Literal
from zipfile import BadZipFile, ZipFile

EOC_HEADER = (
    "timestamp_s",
    "timestamp_origin",
    "sd_block_id",
    "cyc_condition",
    "cyc_charged",
    "age_type",
    "age_temp",
    "age_soc",
    "age_chg_rate",
    "age_dischg_rate",
    "age_profile",
    "v_max_target_V",
    "v_min_target_V",
    "i_chg_max_A",
    "i_dischg_min_A",
    "i_chg_cutoff_A",
    "i_dischg_cutoff_A",
    "cap_aged_est_Ah",
    "soh_cap",
    "delta_q_Ah",
    "delta_q_chg_Ah",
    "delta_q_dischg_Ah",
    "delta_e_Wh",
    "delta_e_chg_Wh",
    "delta_e_dischg_Wh",
    "coulomb_efficiency",
    "energy_efficiency",
    "ocv_est_start_V",
    "ocv_est_end_V",
    "soc_est_start",
    "soc_est_end",
    "t_start_degC",
    "t_end_degC",
    "cyc_duration_s",
    "num_cycles_op",
    "num_cycles_checkup",
    "total_q_chg_CU_RT_Ah",
    "total_q_dischg_CU_RT_Ah",
    "total_q_chg_cyc_OT_Ah",
    "total_q_dischg_cyc_OT_Ah",
    "total_q_chg_other_RT_Ah",
    "total_q_dischg_other_RT_Ah",
    "total_q_chg_other_OT_Ah",
    "total_q_dischg_other_OT_Ah",
    "total_q_chg_sum_Ah",
    "total_q_dischg_sum_Ah",
    "total_e_chg_CU_RT_Wh",
    "total_e_dischg_CU_RT_Wh",
    "total_e_chg_cyc_OT_Wh",
    "total_e_dischg_cyc_OT_Wh",
    "total_e_chg_other_RT_Wh",
    "total_e_dischg_other_RT_Wh",
    "total_e_chg_other_OT_Wh",
    "total_e_dischg_other_OT_Wh",
    "total_e_chg_sum_Wh",
    "total_e_dischg_sum_Wh",
    "uptime_ticks",
)

EIS_HEADER = (
    "timestamp_s",
    "timestamp_origin",
    "sd_block_id",
    "cyc_charged",
    "is_rt",
    "soc_nom",
    "valid",
    "z_ref_init_mOhm",
    "z_ref_now_mOhm",
    "soh_imp",
    "ocv_est_avg_V",
    "t_avg_degC",
    "eis_duration_s",
    "uptime_ticks",
    "seq_nr",
    "freq_Hz",
    "z_amp_mOhm",
    "z_ph_deg",
    "z_amp_comp_mOhm",
    "z_ph_comp_deg",
    "z_re_comp_mOhm",
    "z_im_comp_mOhm",
)

_EOC_MEMBER_RE = re.compile(
    r"^cell_eocv2_P(?P<condition>\d{3})_(?P<replicate>\d+)_S(?P<slave>\d{2})_C(?P<channel>\d{2})\.csv$"
)
_EIS_MEMBER_RE = re.compile(
    r"^cell_eisv2_P(?P<condition>\d{3})_(?P<replicate>\d+)_S(?P<slave>\d{2})_C(?P<channel>\d{2})\.csv$"
)

_EOC_INTEGER_FIELDS = frozenset(
    {
        "timestamp_origin",
        "sd_block_id",
        "cyc_condition",
        "cyc_charged",
        "age_type",
        "age_temp",
        "age_soc",
        "age_profile",
        "num_cycles_op",
        "num_cycles_checkup",
    }
)
_EIS_INTEGER_FIELDS = frozenset(
    {
        "timestamp_origin",
        "sd_block_id",
        "cyc_charged",
        "is_rt",
        "soc_nom",
        "valid",
        "seq_nr",
    }
)


class KitV2AuditError(ValueError):
    """The archive is malformed or violates a fail-closed audit invariant."""


class SnapshotSemanticsUnverified(KitV2AuditError):
    """Raised when a caller asks for completion time without verified semantics."""


@dataclass(frozen=True, order=True)
class CellIdentity:
    condition: int
    replicate: int
    slave: int
    channel: int
    member: str

    @property
    def cell_id(self) -> str:
        return f"P{self.condition:03d}_{self.replicate}_S{self.slave:02d}_C{self.channel:02d}"


@dataclass(frozen=True)
class RowProvenance:
    archive_name: str
    archive_sha256: str
    member: str
    line_number: int
    timestamp_s: float
    timestamp_origin: int
    duration_s: float


@dataclass(frozen=True)
class CapacityEndpoint:
    cell: CellIdentity
    capacity_ah: float
    provenance: RowProvenance
    age_type: int
    age_temperature_c: int
    age_soc_percent: int
    age_charge_rate_c: float
    age_discharge_rate_c: float
    age_profile: int
    operating_cycles: int
    checkup_cycles: int
    total_charge_ah: float
    total_discharge_ah: float


@dataclass(frozen=True)
class Q0Record:
    cell: CellIdentity
    capacity_ah: float
    provenance: RowProvenance
    ambiguity_flags: tuple[str, ...]


@dataclass(frozen=True)
class EisPoint:
    seq_nr: int
    frequency_hz: float
    z_amp_mohm: float | None
    z_phase_deg: float | None
    z_amp_comp_mohm: float | None
    z_phase_comp_deg: float | None
    z_real_comp_mohm: float | None
    z_imag_comp_mohm: float | None


@dataclass(frozen=True)
class EisSpectrum:
    cell: CellIdentity
    sd_block_id: int
    charged: int
    is_rt: int
    soc_nom: int
    valid: int
    provenance: RowProvenance
    points: tuple[EisPoint, ...]


@dataclass(frozen=True)
class RetryCandidate:
    cell_id: str
    earlier_timestamp_s: float
    later_timestamp_s: float
    gap_s: float
    is_rt: int
    soc_nom: int
    resolution: str = "candidate_only_do_not_drop"


@dataclass(frozen=True)
class AlignmentRecord:
    cell_id: str
    eis_timestamp_s: float
    capacity_timestamp_s: float | None
    capacity_ah: float | None
    time_delta_s: float | None
    status: str
    ambiguity_flags: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedSnapshotCompletionPolicy:
    mode: Literal["timestamp_is_completion", "timestamp_plus_duration_is_completion"]
    evidence_id: str

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise ValueError("completion policy requires a non-empty evidence_id")


@dataclass(frozen=True)
class RetryCandidatePolicy:
    max_gap_s: float
    evidence_id: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_gap_s) or self.max_gap_s <= 0:
            raise ValueError("retry max_gap_s must be finite and positive")
        if not self.evidence_id.strip():
            raise ValueError("retry policy requires a non-empty evidence_id")


@dataclass(frozen=True)
class KitV2Audit:
    eoc_archive: dict[str, object]
    eis_archive: dict[str, object]
    counts: dict[str, int]
    q0_records: tuple[Q0Record, ...]
    frequency_registry_hz: tuple[float, ...]
    missingness: dict[str, int]
    retry_policy: dict[str, object]
    retry_candidates: tuple[RetryCandidate, ...]
    alignments: tuple[AlignmentRecord, ...]
    alignment_status_counts: dict[str, int]
    snapshot_completion_semantics: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class KitV2DevelopmentRecords:
    """Strictly parsed records exposed only for provisional development experiments."""

    eoc_archive: dict[str, object]
    eis_archive: dict[str, object]
    counts: dict[str, int]
    q0_records: tuple[Q0Record, ...]
    capacity_endpoints: tuple[CapacityEndpoint, ...]
    eis_spectra: tuple[EisSpectrum, ...]
    frequency_registry_hz: tuple[float, ...]
    missingness: dict[str, int]


@dataclass(frozen=True)
class KitV2CapacityRecords:
    """Strict capacity/use records for a B-only model that must never open EIS data."""

    eoc_archive: dict[str, object]
    counts: dict[str, int]
    q0_records: tuple[Q0Record, ...]
    capacity_endpoints: tuple[CapacityEndpoint, ...]


def snapshot_completion_time(
    provenance: RowProvenance,
    policy: VerifiedSnapshotCompletionPolicy | None = None,
) -> float:
    """Return completion time only under an explicit evidence-backed policy."""
    if policy is None:
        raise SnapshotSemanticsUnverified(
            "timestamp/duration completion semantics are unverified; provide an evidence-backed policy"
        )
    if policy.mode == "timestamp_is_completion":
        return provenance.timestamp_s
    return provenance.timestamp_s + provenance.duration_s


def _archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_zip(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"explicit {label} archive path is not a file: {path}")
    if path.suffix.lower() != ".zip":
        raise KitV2AuditError(f"explicit {label} archive path must end in .zip: {path}")


def _parse_member(name: str, pattern: re.Pattern[str], *, archive: str) -> CellIdentity:
    if "/" in name or "\\" in name:
        raise KitV2AuditError(f"nested or unsafe member path in {archive}: {name!r}")
    match = pattern.fullmatch(name)
    if match is None:
        raise KitV2AuditError(f"unexpected member filename in {archive}: {name!r}")
    identity = CellIdentity(
        condition=int(match.group("condition")),
        replicate=int(match.group("replicate")),
        slave=int(match.group("slave")),
        channel=int(match.group("channel")),
        member=name,
    )
    if identity.condition != 0 and identity.replicate <= 0:
        raise KitV2AuditError(f"non-positive member identity in {archive}: {name!r}")
    return identity


def _float(raw: str, *, where: str, allow_missing: bool = False) -> float | None:
    try:
        value = float(raw)
    except ValueError as exc:
        raise KitV2AuditError(f"non-numeric value at {where}: {raw!r}") from exc
    if not math.isfinite(value):
        if allow_missing and math.isnan(value):
            return None
        raise KitV2AuditError(f"non-finite value at {where}: {raw!r}")
    return value


def _intlike(raw: str, *, where: str) -> int:
    value = _float(raw, where=where)
    assert value is not None
    if not value.is_integer():
        raise KitV2AuditError(f"non-integer numeric value at {where}: {raw!r}")
    return int(value)


def _validate_row_shape(row: dict[str | None, str | list[str] | None], *, where: str) -> None:
    if None in row or any(value is None for value in row.values()):
        raise KitV2AuditError(f"wrong column count at {where}")


def _validate_numeric_row(
    row: dict[str | None, str | list[str] | None],
    header: tuple[str, ...],
    integer_fields: frozenset[str],
    *,
    where: str,
) -> None:
    _validate_row_shape(row, where=where)
    for field_name in header:
        raw = row[field_name]
        assert isinstance(raw, str)
        if field_name in integer_fields:
            _intlike(raw, where=f"{where}:{field_name}")
        else:
            _float(raw, where=f"{where}:{field_name}", allow_missing=True)


def _provenance(
    row: dict[str | None, str | list[str] | None],
    *,
    duration_field: str,
    archive_name: str,
    archive_sha256: str,
    member: str,
    line_number: int,
) -> RowProvenance:
    timestamp = _float(str(row["timestamp_s"]), where=f"{member}:{line_number}:timestamp_s")
    duration = _float(str(row[duration_field]), where=f"{member}:{line_number}:{duration_field}")
    assert timestamp is not None and duration is not None
    if timestamp < 0 or duration < 0:
        raise KitV2AuditError(f"negative timestamp or duration at {member}:{line_number}")
    return RowProvenance(
        archive_name=archive_name,
        archive_sha256=archive_sha256,
        member=member,
        line_number=line_number,
        timestamp_s=timestamp,
        timestamp_origin=_intlike(
            str(row["timestamp_origin"]), where=f"{member}:{line_number}:timestamp_origin"
        ),
        duration_s=duration,
    )


def _read_eoc(
    path: Path | BinaryIO, archive_sha: str, *, archive_name: str | None = None
) -> tuple[list[CapacityEndpoint], dict[str, int], set[str]]:
    name = archive_name if archive_name is not None else Path(path).name  # type: ignore[arg-type]
    endpoints: list[CapacityEndpoint] = []
    counts = Counter[str]()
    real_cells: set[str] = set()
    try:
        with ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    raise KitV2AuditError(f"directory member is not allowed: {info.filename!r}")
                cell = _parse_member(info.filename, _EOC_MEMBER_RE, archive=name)
                if cell.condition == 0:
                    counts["eoc_p000_members_excluded"] += 1
                    continue
                if cell.cell_id in real_cells:
                    raise KitV2AuditError(f"duplicate EOC cell member identity: {cell.cell_id}")
                real_cells.add(cell.cell_id)
                counts["eoc_real_members"] += 1
                with (
                    archive.open(info, "r") as raw,
                    TextIOWrapper(raw, encoding="utf-8", newline="") as text,
                ):
                    reader = csv.DictReader(text, delimiter=";")
                    if tuple(reader.fieldnames or ()) != EOC_HEADER:
                        raise KitV2AuditError(f"unexpected EOC header in {info.filename}")
                    latest_age_context: dict[str, str] | None = None
                    previous_timestamp_s = -math.inf
                    for line_number, row in enumerate(reader, start=2):
                        counts["eoc_real_rows"] += 1
                        _validate_numeric_row(
                            row,
                            EOC_HEADER,
                            _EOC_INTEGER_FIELDS,
                            where=f"{info.filename}:{line_number}",
                        )
                        timestamp_s = _float(
                            str(row["timestamp_s"]),
                            where=f"{info.filename}:{line_number}:timestamp_s",
                        )
                        assert timestamp_s is not None
                        if timestamp_s < previous_timestamp_s:
                            raise KitV2AuditError(
                                f"EOC rows must be timestamp-monotone for past-only context: "
                                f"{info.filename}:{line_number}"
                            )
                        previous_timestamp_s = timestamp_s
                        condition = _intlike(
                            str(row["cyc_condition"]),
                            where=f"{info.filename}:{line_number}:cyc_condition",
                        )
                        charged = _intlike(
                            str(row["cyc_charged"]),
                            where=f"{info.filename}:{line_number}:cyc_charged",
                        )
                        if condition == 1:
                            latest_age_context = {
                                field: str(row[field])
                                for field in (
                                    "age_type",
                                    "age_temp",
                                    "age_soc",
                                    "age_chg_rate",
                                    "age_dischg_rate",
                                    "age_profile",
                                )
                            }
                        if condition == 2 and charged == 0:
                            capacity = _float(
                                str(row["cap_aged_est_Ah"]),
                                where=f"{info.filename}:{line_number}:cap_aged_est_Ah",
                            )
                            assert capacity is not None
                            if capacity <= 0:
                                raise KitV2AuditError(
                                    f"non-positive capacity endpoint at {info.filename}:{line_number}"
                                )
                            context = latest_age_context or {
                                field: str(row[field])
                                for field in (
                                    "age_type",
                                    "age_temp",
                                    "age_soc",
                                    "age_chg_rate",
                                    "age_dischg_rate",
                                    "age_profile",
                                )
                            }
                            total_charge = _float(
                                str(row["total_q_chg_sum_Ah"]),
                                where=f"{info.filename}:{line_number}:total_q_chg_sum_Ah",
                            )
                            total_discharge = _float(
                                str(row["total_q_dischg_sum_Ah"]),
                                where=f"{info.filename}:{line_number}:total_q_dischg_sum_Ah",
                            )
                            assert total_charge is not None and total_discharge is not None
                            endpoints.append(
                                CapacityEndpoint(
                                    cell=cell,
                                    capacity_ah=capacity,
                                    provenance=_provenance(
                                        row,
                                        duration_field="cyc_duration_s",
                                        archive_name=name,
                                        archive_sha256=archive_sha,
                                        member=info.filename,
                                        line_number=line_number,
                                    ),
                                    age_type=_intlike(
                                        context["age_type"],
                                        where=f"{info.filename}:{line_number}:age_type",
                                    ),
                                    age_temperature_c=_intlike(
                                        context["age_temp"],
                                        where=f"{info.filename}:{line_number}:age_temp",
                                    ),
                                    age_soc_percent=_intlike(
                                        context["age_soc"],
                                        where=f"{info.filename}:{line_number}:age_soc",
                                    ),
                                    age_charge_rate_c=float(context["age_chg_rate"]),
                                    age_discharge_rate_c=float(context["age_dischg_rate"]),
                                    age_profile=_intlike(
                                        context["age_profile"],
                                        where=f"{info.filename}:{line_number}:age_profile",
                                    ),
                                    operating_cycles=_intlike(
                                        str(row["num_cycles_op"]),
                                        where=f"{info.filename}:{line_number}:num_cycles_op",
                                    ),
                                    checkup_cycles=_intlike(
                                        str(row["num_cycles_checkup"]),
                                        where=f"{info.filename}:{line_number}:num_cycles_checkup",
                                    ),
                                    total_charge_ah=total_charge,
                                    total_discharge_ah=total_discharge,
                                )
                            )
    except BadZipFile as exc:
        raise KitV2AuditError(f"invalid EOC zip archive: {path}") from exc
    counts["capacity_endpoints"] = len(endpoints)
    return endpoints, dict(counts), real_cells


def _point_from_row(
    row: dict[str | None, str | list[str] | None], *, member: str, line_number: int
) -> EisPoint:
    prefix = f"{member}:{line_number}"
    values: dict[str, float | None] = {}
    for field_name in (
        "z_amp_mOhm",
        "z_ph_deg",
        "z_amp_comp_mOhm",
        "z_ph_comp_deg",
        "z_re_comp_mOhm",
        "z_im_comp_mOhm",
    ):
        values[field_name] = _float(
            str(row[field_name]), where=f"{prefix}:{field_name}", allow_missing=True
        )
    frequency = _float(str(row["freq_Hz"]), where=f"{prefix}:freq_Hz")
    assert frequency is not None
    if frequency <= 0:
        raise KitV2AuditError(f"non-positive EIS frequency at {prefix}")
    return EisPoint(
        seq_nr=_intlike(str(row["seq_nr"]), where=f"{prefix}:seq_nr"),
        frequency_hz=frequency,
        z_amp_mohm=values["z_amp_mOhm"],
        z_phase_deg=values["z_ph_deg"],
        z_amp_comp_mohm=values["z_amp_comp_mOhm"],
        z_phase_comp_deg=values["z_ph_comp_deg"],
        z_real_comp_mohm=values["z_re_comp_mOhm"],
        z_imag_comp_mohm=values["z_im_comp_mOhm"],
    )


def _same_spectrum_key(
    row: dict[str | None, str | list[str] | None], cell_id: str, *, where: str
) -> tuple[object, ...]:
    timestamp = _float(str(row["timestamp_s"]), where=f"{where}:timestamp_s")
    assert timestamp is not None
    return (
        cell_id,
        timestamp,
        _intlike(str(row["sd_block_id"]), where=f"{where}:sd_block_id"),
        _intlike(str(row["cyc_charged"]), where=f"{where}:cyc_charged"),
        _intlike(str(row["is_rt"]), where=f"{where}:is_rt"),
        _intlike(str(row["soc_nom"]), where=f"{where}:soc_nom"),
    )


def _finish_spectrum(
    *,
    cell: CellIdentity,
    rows: list[tuple[int, dict[str | None, str | list[str] | None]]],
    archive_name: str,
    archive_sha: str,
    canonical: tuple[float, ...] | None,
    missingness: Counter[str],
) -> tuple[EisSpectrum, tuple[float, ...]]:
    first_line, first = rows[0]
    points = tuple(_point_from_row(row, member=cell.member, line_number=line) for line, row in rows)
    seq = tuple(point.seq_nr for point in points)
    if seq != tuple(range(29)):
        raise KitV2AuditError(
            f"EIS spectrum must contain exactly seq_nr 0..28 at {cell.member}:{first_line}; got {seq}"
        )
    frequencies = tuple(point.frequency_hz for point in points)
    if any(left <= right for left, right in pairwise(frequencies)):
        raise KitV2AuditError(f"EIS frequency registry is not strictly descending in {cell.member}")
    if canonical is None:
        canonical = frequencies
    elif len(canonical) != len(frequencies) or any(
        not math.isclose(expected, actual, rel_tol=1e-6, abs_tol=1e-8)
        for expected, actual in zip(canonical, frequencies, strict=True)
    ):
        raise KitV2AuditError(f"EIS frequency registry mismatch in {cell.member}:{first_line}")
    invariant_fields = (
        "timestamp_s",
        "timestamp_origin",
        "sd_block_id",
        "cyc_charged",
        "is_rt",
        "soc_nom",
        "valid",
        "eis_duration_s",
    )
    for line, row in rows[1:]:
        for field_name in invariant_fields:
            if _float(str(row[field_name]), where=f"{cell.member}:{line}:{field_name}") != _float(
                str(first[field_name]), where=f"{cell.member}:{first_line}:{field_name}"
            ):
                raise KitV2AuditError(
                    f"inconsistent {field_name} inside spectrum {cell.member}:{first_line}-{line}"
                )
    for point in points:
        for field_name, value in (
            ("z_amp_mOhm", point.z_amp_mohm),
            ("z_ph_deg", point.z_phase_deg),
            ("z_amp_comp_mOhm", point.z_amp_comp_mohm),
            ("z_ph_comp_deg", point.z_phase_comp_deg),
            ("z_re_comp_mOhm", point.z_real_comp_mohm),
            ("z_im_comp_mOhm", point.z_imag_comp_mohm),
        ):
            if value is None:
                missingness[field_name] += 1
                missingness[f"{field_name}:seq_{point.seq_nr}"] += 1
    provenance = _provenance(
        first,
        duration_field="eis_duration_s",
        archive_name=archive_name,
        archive_sha256=archive_sha,
        member=cell.member,
        line_number=first_line,
    )
    return (
        EisSpectrum(
            cell=cell,
            sd_block_id=_intlike(str(first["sd_block_id"]), where=f"{cell.member}:{first_line}"),
            charged=_intlike(str(first["cyc_charged"]), where=f"{cell.member}:{first_line}"),
            is_rt=_intlike(str(first["is_rt"]), where=f"{cell.member}:{first_line}"),
            soc_nom=_intlike(str(first["soc_nom"]), where=f"{cell.member}:{first_line}"),
            valid=_intlike(str(first["valid"]), where=f"{cell.member}:{first_line}"),
            provenance=provenance,
            points=points,
        ),
        canonical,
    )


def _read_eis(
    path: Path, archive_sha: str
) -> tuple[list[EisSpectrum], tuple[float, ...], dict[str, int], dict[str, int], set[str]]:
    spectra: list[EisSpectrum] = []
    canonical: tuple[float, ...] | None = None
    counts = Counter[str]()
    missingness = Counter[str]()
    real_cells: set[str] = set()
    try:
        with ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    raise KitV2AuditError(f"directory member is not allowed: {info.filename!r}")
                cell = _parse_member(info.filename, _EIS_MEMBER_RE, archive=path.name)
                if cell.condition == 0:
                    counts["eis_p000_members_excluded"] += 1
                    continue
                if cell.cell_id in real_cells:
                    raise KitV2AuditError(f"duplicate EIS cell member identity: {cell.cell_id}")
                real_cells.add(cell.cell_id)
                counts["eis_real_members"] += 1
                pending_key: tuple[object, ...] | None = None
                pending_rows: list[tuple[int, dict[str | None, str | list[str] | None]]] = []
                with (
                    archive.open(info, "r") as raw,
                    TextIOWrapper(raw, encoding="utf-8", newline="") as text,
                ):
                    reader = csv.DictReader(text, delimiter=";")
                    if tuple(reader.fieldnames or ()) != EIS_HEADER:
                        raise KitV2AuditError(f"unexpected EIS header in {info.filename}")
                    for line_number, row in enumerate(reader, start=2):
                        counts["eis_real_rows"] += 1
                        _validate_numeric_row(
                            row,
                            EIS_HEADER,
                            _EIS_INTEGER_FIELDS,
                            where=f"{info.filename}:{line_number}",
                        )
                        key = _same_spectrum_key(
                            row, cell.cell_id, where=f"{info.filename}:{line_number}"
                        )
                        if pending_key is not None and key != pending_key:
                            spectrum, canonical = _finish_spectrum(
                                cell=cell,
                                rows=pending_rows,
                                archive_name=path.name,
                                archive_sha=archive_sha,
                                canonical=canonical,
                                missingness=missingness,
                            )
                            spectra.append(spectrum)
                            pending_rows = []
                        pending_key = key
                        pending_rows.append((line_number, row))
                    if pending_rows:
                        spectrum, canonical = _finish_spectrum(
                            cell=cell,
                            rows=pending_rows,
                            archive_name=path.name,
                            archive_sha=archive_sha,
                            canonical=canonical,
                            missingness=missingness,
                        )
                        spectra.append(spectrum)
    except BadZipFile as exc:
        raise KitV2AuditError(f"invalid EIS zip archive: {path}") from exc
    if canonical is None:
        raise KitV2AuditError("EIS archive contains no real spectra")
    counts["eis_spectra"] = len(spectra)
    counts["rt50_spectra"] = sum(
        item.charged == 1 and item.is_rt == 1 and item.soc_nom == 50 for item in spectra
    )
    counts["valid_rt50_spectra"] = sum(
        item.charged == 1 and item.is_rt == 1 and item.soc_nom == 50 and item.valid == 1
        for item in spectra
    )
    return spectra, canonical, dict(counts), dict(missingness), real_cells


def _q0_records(endpoints: list[CapacityEndpoint]) -> tuple[Q0Record, ...]:
    grouped: dict[str, list[CapacityEndpoint]] = defaultdict(list)
    for endpoint in endpoints:
        grouped[endpoint.cell.cell_id].append(endpoint)
    result: list[Q0Record] = []
    for cell_id in sorted(grouped):
        ordered = sorted(
            grouped[cell_id],
            key=lambda item: (
                item.provenance.timestamp_s,
                item.provenance.member,
                item.provenance.line_number,
            ),
        )
        first = ordered[0]
        flags: list[str] = []
        if sum(item.provenance.timestamp_s == first.provenance.timestamp_s for item in ordered) > 1:
            flags.append("duplicate_earliest_capacity_timestamp")
        result.append(
            Q0Record(
                cell=first.cell,
                capacity_ah=first.capacity_ah,
                provenance=first.provenance,
                ambiguity_flags=tuple(flags),
            )
        )
    return tuple(result)


def _retry_candidates(
    spectra: list[EisSpectrum], policy: RetryCandidatePolicy | None
) -> tuple[RetryCandidate, ...]:
    if policy is None:
        return ()
    grouped: dict[tuple[str, int, int], list[EisSpectrum]] = defaultdict(list)
    for spectrum in spectra:
        grouped[(spectrum.cell.cell_id, spectrum.is_rt, spectrum.soc_nom)].append(spectrum)
    candidates: list[RetryCandidate] = []
    for (cell_id, is_rt, soc_nom), items in sorted(grouped.items()):
        ordered = sorted(items, key=lambda item: item.provenance.timestamp_s)
        for earlier, later in pairwise(ordered):
            gap = later.provenance.timestamp_s - earlier.provenance.timestamp_s
            if 0 <= gap <= policy.max_gap_s:
                candidates.append(
                    RetryCandidate(
                        cell_id=cell_id,
                        earlier_timestamp_s=earlier.provenance.timestamp_s,
                        later_timestamp_s=later.provenance.timestamp_s,
                        gap_s=gap,
                        is_rt=is_rt,
                        soc_nom=soc_nom,
                    )
                )
    return tuple(candidates)


def _align(
    endpoints: list[CapacityEndpoint],
    spectra: list[EisSpectrum],
    retries: tuple[RetryCandidate, ...],
    completion_policy: VerifiedSnapshotCompletionPolicy | None,
) -> tuple[AlignmentRecord, ...]:
    endpoints_by_cell: dict[str, list[CapacityEndpoint]] = defaultdict(list)
    for endpoint in endpoints:
        endpoints_by_cell[endpoint.cell.cell_id].append(endpoint)
    retry_timestamps = {(item.cell_id, item.earlier_timestamp_s) for item in retries} | {
        (item.cell_id, item.later_timestamp_s) for item in retries
    }
    result: list[AlignmentRecord] = []
    valid_rt50 = [
        item
        for item in spectra
        if item.charged == 1 and item.is_rt == 1 and item.soc_nom == 50 and item.valid == 1
    ]
    for spectrum in sorted(
        valid_rt50, key=lambda item: (item.cell.cell_id, item.provenance.timestamp_s)
    ):
        candidates = sorted(
            endpoints_by_cell.get(spectrum.cell.cell_id, []),
            key=lambda item: item.provenance.timestamp_s,
        )
        flags: list[str] = []
        if completion_policy is None:
            flags.extend(
                (
                    "eoc_snapshot_completion_semantics_unverified",
                    "eis_snapshot_completion_semantics_unverified",
                    "timestamp_only_candidate_not_training_eligible",
                )
            )
            endpoint_times = [item.provenance.timestamp_s for item in candidates]
            eis_time = spectrum.provenance.timestamp_s
        else:
            endpoint_times = [
                snapshot_completion_time(item.provenance, completion_policy) for item in candidates
            ]
            eis_time = snapshot_completion_time(spectrum.provenance, completion_policy)
        if (spectrum.cell.cell_id, spectrum.provenance.timestamp_s) in retry_timestamps:
            flags.append("duplicate_retry_candidate")
        if not candidates:
            result.append(
                AlignmentRecord(
                    cell_id=spectrum.cell.cell_id,
                    eis_timestamp_s=spectrum.provenance.timestamp_s,
                    capacity_timestamp_s=None,
                    capacity_ah=None,
                    time_delta_s=None,
                    status="no_capacity_candidates",
                    ambiguity_flags=tuple(flags),
                )
            )
            continue
        chosen_index = bisect_right(endpoint_times, eis_time) - 1
        if chosen_index < 0:
            result.append(
                AlignmentRecord(
                    cell_id=spectrum.cell.cell_id,
                    eis_timestamp_s=spectrum.provenance.timestamp_s,
                    capacity_timestamp_s=None,
                    capacity_ah=None,
                    time_delta_s=None,
                    status="no_prior_capacity_candidate",
                    ambiguity_flags=tuple(flags),
                )
            )
            continue
        chosen = candidates[chosen_index]
        status = (
            "verified_completion_candidate"
            if completion_policy is not None
            else "ambiguous_timestamp_candidate"
        )
        result.append(
            AlignmentRecord(
                cell_id=spectrum.cell.cell_id,
                eis_timestamp_s=spectrum.provenance.timestamp_s,
                capacity_timestamp_s=chosen.provenance.timestamp_s,
                capacity_ah=chosen.capacity_ah,
                time_delta_s=eis_time - endpoint_times[chosen_index],
                status=status,
                ambiguity_flags=tuple(flags),
            )
        )
    return tuple(result)


def audit_kit_v2(
    eoc_zip: Path,
    eis_zip: Path,
    *,
    expected_cells: int | None = None,
    expected_capacity_endpoints: int | None = None,
    expected_valid_rt50: int | None = None,
    retry_policy: RetryCandidatePolicy | None = None,
    snapshot_completion_policy: VerifiedSnapshotCompletionPolicy | None = None,
) -> KitV2Audit:
    """Audit two explicitly named KIT v2 archives without extracting or loading them wholly."""
    _require_zip(eoc_zip, "EOC")
    _require_zip(eis_zip, "EIS")
    eoc_sha = _archive_sha256(eoc_zip)
    eis_sha = _archive_sha256(eis_zip)
    endpoints, eoc_counts, eoc_cells = _read_eoc(eoc_zip, eoc_sha)
    spectra, frequencies, eis_counts, missingness, eis_cells = _read_eis(eis_zip, eis_sha)
    if eoc_cells != eis_cells:
        raise KitV2AuditError(
            f"EOC/EIS real-cell inventory mismatch; only_eoc={sorted(eoc_cells - eis_cells)}, "
            f"only_eis={sorted(eis_cells - eoc_cells)}"
        )
    counts = {**eoc_counts, **eis_counts, "real_cells": len(eoc_cells)}
    checks = (
        ("real_cells", expected_cells),
        ("capacity_endpoints", expected_capacity_endpoints),
        ("valid_rt50_spectra", expected_valid_rt50),
    )
    for field_name, expected in checks:
        if expected is not None and counts[field_name] != expected:
            raise KitV2AuditError(
                f"expected {field_name}={expected}, observed {counts[field_name]}"
            )
    q0 = _q0_records(endpoints)
    retries = _retry_candidates(spectra, retry_policy)
    alignments = _align(endpoints, spectra, retries, snapshot_completion_policy)
    status_counts = dict(Counter(item.status for item in alignments))
    retry_info: dict[str, object]
    if retry_policy is None:
        retry_info = {"status": "not_assessed_without_explicit_policy"}
    else:
        retry_info = {
            "status": "candidate_detection_only",
            "max_gap_s": retry_policy.max_gap_s,
            "evidence_id": retry_policy.evidence_id,
        }
    return KitV2Audit(
        eoc_archive={"path_name": eoc_zip.name, "sha256": eoc_sha},
        eis_archive={"path_name": eis_zip.name, "sha256": eis_sha},
        counts=counts,
        q0_records=q0,
        frequency_registry_hz=frequencies,
        missingness=missingness,
        retry_policy=retry_info,
        retry_candidates=retries,
        alignments=alignments,
        alignment_status_counts=status_counts,
        snapshot_completion_semantics=(
            "blocked_unverified"
            if snapshot_completion_policy is None
            else snapshot_completion_policy.mode
        ),
    )


def load_kit_v2_development_records(
    eoc_zip: Path,
    eis_zip: Path,
    *,
    expected_cells: int | None = None,
    expected_capacity_endpoints: int | None = None,
    expected_valid_rt50: int | None = None,
) -> KitV2DevelopmentRecords:
    """Load strict source records for acknowledged, development-only engineering work.

    This API deliberately exposes no protected split and makes no claim that raw timestamps
    are verified snapshot-completion times. Callers must retain those limitations in every
    downstream artifact.
    """
    _require_zip(eoc_zip, "EOC")
    _require_zip(eis_zip, "EIS")
    eoc_sha = _archive_sha256(eoc_zip)
    eis_sha = _archive_sha256(eis_zip)
    endpoints, eoc_counts, eoc_cells = _read_eoc(eoc_zip, eoc_sha)
    spectra, frequencies, eis_counts, missingness, eis_cells = _read_eis(eis_zip, eis_sha)
    if eoc_cells != eis_cells:
        raise KitV2AuditError(
            f"EOC/EIS real-cell inventory mismatch; only_eoc={sorted(eoc_cells - eis_cells)}, "
            f"only_eis={sorted(eis_cells - eoc_cells)}"
        )
    counts = {**eoc_counts, **eis_counts, "real_cells": len(eoc_cells)}
    for field_name, expected in (
        ("real_cells", expected_cells),
        ("capacity_endpoints", expected_capacity_endpoints),
        ("valid_rt50_spectra", expected_valid_rt50),
    ):
        if expected is not None and counts[field_name] != expected:
            raise KitV2AuditError(
                f"expected {field_name}={expected}, observed {counts[field_name]}"
            )
    return KitV2DevelopmentRecords(
        eoc_archive={"path_name": eoc_zip.name, "sha256": eoc_sha},
        eis_archive={"path_name": eis_zip.name, "sha256": eis_sha},
        counts=counts,
        q0_records=_q0_records(endpoints),
        capacity_endpoints=tuple(endpoints),
        eis_spectra=tuple(spectra),
        frequency_registry_hz=frequencies,
        missingness=missingness,
    )


def load_kit_v2_capacity_records(
    eoc_zip: Path,
    *,
    expected_sha256: str,
    expected_cells: int | None = None,
    expected_capacity_endpoints: int | None = None,
) -> KitV2CapacityRecords:
    """Load only EOC capacity/use data for the F1 B-only lineage.

    ``expected_sha256`` is mandatory so a caller cannot silently replace the reviewed archive.
    Q0 is required to be the unique completed check-up-zero endpoint for every real cell; this is
    stricter than the legacy paired-development convenience loader.
    """
    if eoc_zip.suffix.lower() != ".zip":
        raise KitV2AuditError(f"explicit EOC archive path must end in .zip: {eoc_zip}")
    if len(expected_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        raise KitV2AuditError("expected EOC SHA-256 is malformed")
    before = eoc_zip.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise KitV2AuditError("EOC archive must be a regular non-symlink file")
    descriptor = os.open(eoc_zip, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise KitV2AuditError("EOC archive changed during secure open")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
            eoc_sha = digest.hexdigest()
            if eoc_sha != expected_sha256:
                raise KitV2AuditError("EOC archive SHA-256 does not match the reviewed source")
            source.seek(0)
            endpoints, counts, cells = _read_eoc(
                source,
                eoc_sha,
                archive_name=eoc_zip.name,
            )
    finally:
        os.close(descriptor)
    counts = {**counts, "real_cells": len(cells)}
    for field_name, expected in (
        ("real_cells", expected_cells),
        ("capacity_endpoints", expected_capacity_endpoints),
    ):
        if expected is not None and counts[field_name] != expected:
            raise KitV2AuditError(
                f"expected {field_name}={expected}, observed {counts[field_name]}"
            )

    endpoints_by_cell: dict[str, list[CapacityEndpoint]] = defaultdict(list)
    for endpoint in endpoints:
        endpoints_by_cell[endpoint.cell.cell_id].append(endpoint)
    q0_records: list[Q0Record] = []
    for cell_id in sorted(endpoints_by_cell):
        candidates = [
            endpoint for endpoint in endpoints_by_cell[cell_id] if endpoint.checkup_cycles == 0
        ]
        if len(candidates) != 1:
            raise KitV2AuditError(
                f"cell {cell_id} requires exactly one completed check-up-zero Q0 endpoint; "
                f"observed {len(candidates)}"
            )
        candidate = candidates[0]
        first = min(
            endpoints_by_cell[cell_id],
            key=lambda endpoint: (
                endpoint.provenance.timestamp_s,
                endpoint.provenance.line_number,
            ),
        )
        if candidate is not first:
            raise KitV2AuditError(
                f"cell {cell_id} check-up-zero Q0 must be the first completed capacity endpoint"
            )
        q0_records.append(
            Q0Record(
                cell=candidate.cell,
                capacity_ah=candidate.capacity_ah,
                provenance=candidate.provenance,
                ambiguity_flags=(),
            )
        )
    if len(q0_records) != len(cells):
        raise KitV2AuditError("capacity-only Q0 inventory does not cover every real cell")
    return KitV2CapacityRecords(
        eoc_archive={"path_name": eoc_zip.name, "sha256": eoc_sha},
        counts=counts,
        q0_records=tuple(q0_records),
        capacity_endpoints=tuple(endpoints),
    )
