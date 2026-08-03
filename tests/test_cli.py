import json
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


def test_early_warning_contract_demo_is_not_a_scientific_or_gate3_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["battery-eis-soh", "early-warning-contract-demo"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["synthetic_only"] is True
    assert payload["contract_demo"] is True
    assert payload["gate3_smoke"] is False
    assert payload["formal_model_result"] is False
    assert payload["scientific_result"] is False
    assert payload["publication_ready"] is False
    assert payload["resume_approved"] is False
    assert payload["snapshot"]["checkpoint"] == 1
    assert payload["threshold_horizon_checkups"] == 3
    assert payload["future_checkpoints_in_horizon"] == [2, 3, 4]
    assert payload["snapshot"]["checkpoint"] not in payload["future_checkpoints_in_horizon"]
    assert payload["red_criterion_repeat_confirmed"] is False
    assert payload["recommendation"] == "orange"


def test_deprecated_smoke_alias_warns_that_it_is_not_gate3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["battery-eis-soh", "early-warning-smoke"])
    main()
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "not a Gate-3 smoke" in captured.err
    assert json.loads(captured.out)["gate3_smoke"] is False
