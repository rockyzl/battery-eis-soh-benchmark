import sys
from pathlib import Path

import pytest

from battery_eis_soh.cli import main


def test_engineering_evaluation_requires_test_exposure_acknowledgment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "must-not-exist"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "battery-eis-soh",
            "engineering-evaluate",
            "--data-root",
            str(tmp_path / "missing-data"),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit, match="acknowledge-test-label-exposure"):
        main()
    assert not output.exists()


def test_full_audit_requires_test_exposure_acknowledgment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "battery-eis-soh",
            "full-audit",
            "--data-root",
            str(tmp_path / "missing-data"),
        ],
    )
    with pytest.raises(SystemExit, match="acknowledge-test-label-exposure"):
        main()
