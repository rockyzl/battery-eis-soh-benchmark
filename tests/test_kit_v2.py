from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from battery_eis_soh.kit_v2 import (
    EIS_HEADER,
    EOC_HEADER,
    KitV2AuditError,
    RetryCandidatePolicy,
    SnapshotSemanticsUnverified,
    VerifiedSnapshotCompletionPolicy,
    audit_kit_v2,
    snapshot_completion_time,
)


def _csv_text(header: tuple[str, ...], rows: list[dict[str, str]]) -> str:
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=header, delimiter=";", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _eoc_row(*, timestamp: str, capacity: str, duration: str = "30") -> dict[str, str]:
    row = dict.fromkeys(EOC_HEADER, "0")
    row.update(
        timestamp_s=timestamp,
        timestamp_origin="1",
        sd_block_id="7",
        cyc_condition="2.0000",
        cyc_charged="0.0000",
        cap_aged_est_Ah=capacity,
        cyc_duration_s=duration,
    )
    return row


def _eis_rows(
    *,
    timestamp: str,
    charged: str = "1",
    valid: str = "1",
    duration: str = "60",
    frequency_mutation: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for seq in range(29):
        row = dict.fromkeys(EIS_HEADER, "0")
        frequency = 20_000.0 / (1.5**seq)
        if frequency_mutation and seq == 10:
            frequency *= 1.02
        row.update(
            timestamp_s=timestamp,
            timestamp_origin="2",
            sd_block_id="8",
            cyc_charged=charged,
            is_rt="1.0000",
            soc_nom="50.0000",
            valid=valid,
            eis_duration_s=duration,
            seq_nr=str(seq),
            freq_Hz=f"{frequency:.12g}",
            z_amp_mOhm=str(10 + seq),
            z_ph_deg=str(-seq),
            z_amp_comp_mOhm="nan" if seq == 0 else str(9 + seq),
            z_ph_comp_deg="nan" if seq == 0 else str(-seq),
            z_re_comp_mOhm="nan" if seq == 0 else str(8 + seq),
            z_im_comp_mOhm="nan" if seq == 0 else str(-1 - seq),
        )
        rows.append(row)
    return rows


def _write_archives(
    tmp_path: Path,
    *,
    bad_eoc_header: bool = False,
    bad_member: bool = False,
    frequency_mutation: bool = False,
    eis_timestamps: tuple[str, ...] = ("110", "120"),
    eis_charged: str = "1",
) -> tuple[Path, Path]:
    eoc_zip = tmp_path / "cell_eocv2.zip"
    eis_zip = tmp_path / "cell_eisv2.zip"
    eoc_name = "wrong.csv" if bad_member else "cell_eocv2_P001_1_S01_C10.csv"
    eoc_header = EOC_HEADER[:-1] if bad_eoc_header else EOC_HEADER
    with ZipFile(eoc_zip, "w", ZIP_DEFLATED) as archive:
        archive.writestr("cell_eocv2_P000_1_S01_C01.csv", "ignored")
        archive.writestr(
            eoc_name,
            _csv_text(
                eoc_header,
                [
                    {
                        key: value
                        for key, value in _eoc_row(timestamp="100", capacity="3.0").items()
                        if key in eoc_header
                    },
                    {
                        key: value
                        for key, value in _eoc_row(timestamp="200", capacity="2.9").items()
                        if key in eoc_header
                    },
                ],
            ),
        )
    with ZipFile(eis_zip, "w", ZIP_DEFLATED) as archive:
        archive.writestr("cell_eisv2_P000_1_S01_C01.csv", "ignored")
        archive.writestr(
            "cell_eisv2_P001_1_S01_C10.csv",
            _csv_text(
                EIS_HEADER,
                [
                    row
                    for index, timestamp in enumerate(eis_timestamps)
                    for row in _eis_rows(
                        timestamp=timestamp,
                        charged=eis_charged,
                        frequency_mutation=frequency_mutation and index == len(eis_timestamps) - 1,
                    )
                ],
            ),
        )
    return eoc_zip, eis_zip


def test_strict_streaming_audit_q0_retry_missingness_and_alignment(tmp_path: Path) -> None:
    eoc_zip, eis_zip = _write_archives(tmp_path)
    audit = audit_kit_v2(
        eoc_zip,
        eis_zip,
        expected_cells=1,
        expected_capacity_endpoints=2,
        expected_valid_rt50=2,
        retry_policy=RetryCandidatePolicy(max_gap_s=60.0, evidence_id="synthetic-test"),
    )

    assert audit.counts["real_cells"] == 1
    assert audit.counts["eoc_p000_members_excluded"] == 1
    assert audit.counts["eis_p000_members_excluded"] == 1
    assert audit.counts["capacity_endpoints"] == 2
    assert audit.counts["valid_rt50_spectra"] == 2
    assert audit.q0_records[0].capacity_ah == 3.0
    assert audit.q0_records[0].provenance.line_number == 2
    assert audit.q0_records[0].provenance.timestamp_s == 100.0
    assert audit.q0_records[0].provenance.duration_s == 30.0
    assert audit.missingness["z_amp_comp_mOhm:seq_0"] == 2
    assert len(audit.frequency_registry_hz) == 29
    assert len(audit.retry_candidates) == 1
    assert audit.retry_candidates[0].resolution == "candidate_only_do_not_drop"
    assert audit.snapshot_completion_semantics == "blocked_unverified"
    assert all(item.status == "ambiguous_timestamp_candidate" for item in audit.alignments)
    assert all(
        "timestamp_only_candidate_not_training_eligible" in item.ambiguity_flags
        for item in audit.alignments
    )


def test_snapshot_completion_semantics_fail_closed_then_preserve_duration(tmp_path: Path) -> None:
    eoc_zip, eis_zip = _write_archives(tmp_path)
    provenance = audit_kit_v2(eoc_zip, eis_zip).q0_records[0].provenance
    with pytest.raises(SnapshotSemanticsUnverified):
        snapshot_completion_time(provenance)
    assert snapshot_completion_time(
        provenance,
        VerifiedSnapshotCompletionPolicy(
            mode="timestamp_is_completion", evidence_id="verified-fixture"
        ),
    ) == pytest.approx(100.0)
    assert snapshot_completion_time(
        provenance,
        VerifiedSnapshotCompletionPolicy(
            mode="timestamp_plus_duration_is_completion", evidence_id="verified-fixture"
        ),
    ) == pytest.approx(130.0)


@pytest.mark.parametrize("mutation", ["header", "member", "frequency"])
def test_mutations_fail_closed(tmp_path: Path, mutation: str) -> None:
    eoc_zip, eis_zip = _write_archives(
        tmp_path,
        bad_eoc_header=mutation == "header",
        bad_member=mutation == "member",
        frequency_mutation=mutation == "frequency",
    )
    with pytest.raises(KitV2AuditError):
        audit_kit_v2(eoc_zip, eis_zip)


def test_wrong_numeric_type_and_expected_count_fail_closed(tmp_path: Path) -> None:
    eoc_zip, eis_zip = _write_archives(tmp_path)
    with ZipFile(eoc_zip, "w", ZIP_DEFLATED) as archive:
        bad = _eoc_row(timestamp="100", capacity="3.0")
        bad["cyc_condition"] = "2.5"
        archive.writestr("cell_eocv2_P001_1_S01_C10.csv", _csv_text(EOC_HEADER, [bad]))
    with pytest.raises(KitV2AuditError, match="non-integer"):
        audit_kit_v2(eoc_zip, eis_zip)

    eoc_zip, eis_zip = _write_archives(tmp_path)
    with pytest.raises(KitV2AuditError, match="expected real_cells"):
        audit_kit_v2(eoc_zip, eis_zip, expected_cells=228)


def test_explicit_archive_paths_only(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        audit_kit_v2(tmp_path / "missing-eoc.zip", tmp_path / "missing-eis.zip")
    wrong = tmp_path / "archive.txt"
    wrong.write_text("not a zip", encoding="utf-8")
    with pytest.raises(KitV2AuditError, match="must end in .zip"):
        audit_kit_v2(wrong, wrong)


def test_alignment_never_uses_a_closer_future_capacity_endpoint(tmp_path: Path) -> None:
    eoc_zip, eis_zip = _write_archives(tmp_path, eis_timestamps=("190",))
    audit = audit_kit_v2(eoc_zip, eis_zip)
    assert audit.alignments[0].capacity_timestamp_s == 100.0
    assert audit.alignments[0].status == "ambiguous_timestamp_candidate"


def test_eis_before_all_capacity_endpoints_has_no_prior_candidate(tmp_path: Path) -> None:
    eoc_zip, eis_zip = _write_archives(tmp_path, eis_timestamps=("90",))
    audit = audit_kit_v2(eoc_zip, eis_zip)
    alignment = audit.alignments[0]
    assert alignment.capacity_timestamp_s is None
    assert alignment.capacity_ah is None
    assert alignment.status == "no_prior_capacity_candidate"
    assert "timestamp_only_candidate_not_training_eligible" in alignment.ambiguity_flags


def test_discharged_rt50_is_not_a_primary_valid_spectrum(tmp_path: Path) -> None:
    eoc_zip, eis_zip = _write_archives(tmp_path, eis_timestamps=("110",), eis_charged="0")
    audit = audit_kit_v2(eoc_zip, eis_zip)
    assert audit.counts["rt50_spectra"] == 0
    assert audit.counts["valid_rt50_spectra"] == 0
    assert audit.alignments == ()
