from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from battery_eis_soh.kit_f1_b_authorization import (
    BINDING_KEYS,
    FORBIDDEN,
    RUN_KIND,
    SCOPE,
    AuthorizationError,
    consume_authorization,
)
from battery_eis_soh.kit_f1_b_worker import (
    BOnlyWorkerError,
    WorkerProgress,
    _atomic,
    _drain_worker_stdout,
    prepare_run_bindings,
    runtime_action,
    supervise,
    validate_engine_contract,
)
from battery_eis_soh.kit_v2 import EOC_HEADER


def _authorization_payload(bindings: dict[str, str], *, nonce: str = "b" * 64) -> dict[str, object]:
    now = time.time()
    return {
        "authorization_version": "kit_f1_b_one_run_v1",
        "authorization_id": "independent-review-test",
        "status": "authorized",
        "single_use": True,
        "run_kind": RUN_KIND,
        "scope": SCOPE,
        "nonce": nonce,
        "issued_at_unix_s": now - 1,
        "expires_at_unix_s": now + 600,
        "bindings": bindings,
        "forbidden": FORBIDDEN,
        "approver_role": "independent-reviewer",
        "evidence_reviewer": "test-reviewer",
    }


def _repo_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = Path(__file__).parents[1]
    eoc = tmp_path / "eoc.zip"
    eoc.write_bytes(b"synthetic-not-a-training-archive")
    config = root / "config/kit_f1_b_capacity_sequence_v1.json"
    runner = root / "scripts/run_kit_f1_b.py"
    output = tmp_path / "output"
    authorization = tmp_path / "authorization.json"
    authorization.write_text(
        json.dumps(_authorization_payload({key: "0" * 64 for key in BINDING_KEYS}))
    )
    return eoc, config, runner, output, authorization


def _bind_authorization(
    eoc: Path, config: Path, runner: Path, output: Path, authorization: Path
) -> dict[str, str]:
    bindings, _, _, _ = prepare_run_bindings(
        eoc_zip=eoc,
        output_dir=output,
        config_path=config,
        authorization_path=authorization,
        runner_path=runner,
    )
    authorization.write_text(json.dumps(_authorization_payload(bindings)))
    return bindings


def test_authorization_is_hash_bound_and_single_use(tmp_path: Path) -> None:
    output = tmp_path / "output"
    authorization = tmp_path / "authorization.json"
    bindings = {key: "a" * 64 for key in BINDING_KEYS}
    authorization.write_text(json.dumps(_authorization_payload(bindings)))
    receipt = consume_authorization(authorization, expected_bindings=bindings, output_dir=output)
    assert receipt.is_file()
    with pytest.raises(FileExistsError):
        consume_authorization(authorization, expected_bindings=bindings, output_dir=output)
    poisoned = {**bindings, "command_sha256": "c" * 64}
    other = tmp_path / "other-authorization.json"
    other.write_text(json.dumps(_authorization_payload(bindings, nonce="d" * 64)))
    with pytest.raises(AuthorizationError, match="command_sha256"):
        consume_authorization(other, expected_bindings=poisoned, output_dir=output)


def test_authorization_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    bindings = {key: "a" * 64 for key in BINDING_KEYS}
    target.write_text(json.dumps(_authorization_payload(bindings)))
    link = tmp_path / "authorization.json"
    link.symlink_to(target)
    with pytest.raises(AuthorizationError, match="symlink"):
        consume_authorization(link, expected_bindings=bindings, output_dir=tmp_path / "out")


def test_immutable_runtime_write_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "terminal.json"
    _atomic(path, {"status": "first"})
    with pytest.raises(BOnlyWorkerError, match="immutable"):
        _atomic(path, {"status": "second"})
    assert json.loads(path.read_text()) == {"status": "first"}


def test_engine_summary_schema_has_no_o2_escape_hatch() -> None:
    root = Path(__file__).parents[1]
    schema = json.loads((root / "config/kit_f1_b_capacity_sequence_v1.schema.json").read_text())
    contract = schema["$defs"]["engineSummary"]["allOf"][1]
    assert contract["additionalProperties"] is False
    assert all(not key.startswith("o2_") for key in contract["required"])
    assert all(not key.startswith("o2_") for key in contract["properties"])


@pytest.mark.parametrize(
    ("elapsed", "rss", "free", "soft_sent", "expected"),
    [
        (1.0, 1, 99, False, "disk_floor_breached"),
        (1.0, 1_000, 1_000, False, "hard_rss_exceeded"),
        (20.0, 1, 1_000, False, "hard_stop_exceeded"),
        (10.0, 1, 1_000, False, "soft_drain"),
        (10.0, 1, 1_000, True, None),
    ],
)
def test_resource_governance_actions(
    elapsed: float, rss: int, free: int, soft_sent: bool, expected: str | None
) -> None:
    limits = {
        "minimum_free_bytes": 100,
        "hard_rss_bytes": 100,
        "hard_stop_seconds": 20,
        "soft_drain_seconds": 10,
    }
    assert (
        runtime_action(
            elapsed_s=elapsed,
            rss_bytes=rss,
            free_bytes=free,
            limits=limits,
            soft_sent=soft_sent,
        )
        == expected
    )


def test_worker_progress_stops_at_fit_boundary() -> None:
    progress = WorkerProgress({"requested": True}, interval_s=60)
    with pytest.raises(BOnlyWorkerError, match="soft drain"):
        progress({"stage": "fit_start"})


@pytest.mark.parametrize("stage", ["worker_success", "worker_failure"])
def test_bounded_final_stdout_drain_preserves_terminal_event(tmp_path: Path, stage: str) -> None:
    output = tmp_path / "drain"
    output.mkdir()
    payload: dict[str, object] = {"stage": stage}
    if stage == "worker_success":
        payload["result"] = {"artifact_paths": []}
    else:
        payload["error_type"] = "SyntheticFailure"
    stream = StringIO(json.dumps(payload) + "\n")
    state: dict[str, object] = {
        "result": None,
        "failure": None,
        "terminal_event_count": 0,
    }
    _drain_worker_stdout(stream, output=output, state=state)
    assert state["terminal_event_count"] == 1
    if stage == "worker_success":
        assert isinstance(state["result"], dict)
    else:
        assert state["failure"] == "SyntheticFailure"


def test_existing_output_is_never_reused(tmp_path: Path) -> None:
    eoc, config, runner, output, authorization = _repo_inputs(tmp_path)
    _bind_authorization(eoc, config, runner, output, authorization)
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("keep")
    assert (
        supervise(
            eoc_zip=eoc,
            output_dir=output,
            config_path=config,
            authorization_path=authorization,
            runner_path=runner,
        )
        == 3
    )
    assert sentinel.read_text() == "keep"
    assert not (output / "terminal.json").exists()


def test_post_claim_launch_error_has_durable_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eoc, config, runner, output, authorization = _repo_inputs(tmp_path)
    _bind_authorization(eoc, config, runner, output, authorization)

    def fail_launch(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic launch failure")

    monkeypatch.setattr("battery_eis_soh.kit_f1_b_worker.subprocess.Popen", fail_launch)
    assert (
        supervise(
            eoc_zip=eoc,
            output_dir=output,
            config_path=config,
            authorization_path=authorization,
            runner_path=runner,
        )
        == 3
    )
    terminal = json.loads((output / "terminal.json").read_text())
    assert terminal["status"] == "failed"
    assert "synthetic launch failure" in terminal["failure"]
    assert terminal["worker_exit_code"] is None


def test_direct_worker_rejects_self_forged_receipt_before_training(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    output = tmp_path / "output"
    output.mkdir()
    eoc = tmp_path / "never-read.zip"
    eoc.write_bytes(b"not-training-data")
    bindings = {key: "0" * 64 for key in BINDING_KEYS}
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps(_authorization_payload(bindings)))
    receipt = tmp_path / f".{authorization.name}.{'b' * 64}.consumed.json"
    receipt.write_text(
        json.dumps(
            {
                "authorization_id": "independent-review-test",
                "authorization_sha256": "0" * 64,
                "bindings": bindings,
                "consumed": True,
                "consumed_at_unix_s": time.time(),
                "nonce": "b" * 64,
                "output_sha256": "0" * 64,
                "run_kind": RUN_KIND,
                "scope": SCOPE,
            }
        )
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/run_kit_f1_b.py"),
            "--worker",
            "--eoc-zip",
            str(eoc),
            "--output-dir",
            str(output),
            "--config-path",
            str(root / "config/kit_f1_b_capacity_sequence_v1.json"),
            "--authorization-receipt",
            str(receipt),
            "--authorization-path",
            str(authorization),
            "--expected-engine-sha256",
            validate_engine_contract()["module_sha256"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert not any(
        path.name in {"engine_summary.json", "oof_predictions.jsonl"} for path in output.iterdir()
    )


def test_supervisor_subprocess_success_path_without_training(tmp_path: Path) -> None:
    root = tmp_path / "mini-repo"
    config_dir = root / "config"
    source_dir = root / "src/battery_eis_soh"
    scripts_dir = root / "scripts"
    config_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    scripts_dir.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n")
    (root / "uv.lock").write_text("fixture-lock\n")
    (source_dir / "fixture.py").write_text("FIXTURE = True\n")
    config = config_dir / "kit_f1_b_capacity_sequence_v1.json"
    config.write_text(
        json.dumps(
            {
                "resources": {
                    "soft_drain_seconds": 20,
                    "hard_stop_seconds": 30,
                    "hard_rss_bytes": 2_000_000_000,
                    "minimum_free_bytes": 1,
                }
            }
        )
    )
    (config_dir / "kit_f1_b_capacity_sequence_v1.schema.json").write_text("{}\n")
    runner = scripts_dir / "run_kit_f1_b.py"
    runner.write_text(
        """#!/usr/bin/env python3
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser(add_help=False)
p.add_argument('--worker', action='store_true')
p.add_argument('--output-dir', type=Path)
a,_=p.parse_known_args()
names=['source_manifest.json','snapshot_manifest.jsonl','fold_registry.json','oof_predictions.jsonl']
for name in names: (a.output_dir/name).write_text('{}\\n')
summary={'view':'B','planned_fits':18,'completed_fits':18}
(a.output_dir/'engine_summary.json').write_text(json.dumps(summary)+'\\n')
flags={'scientific_result':False,'publication_ready':False,'resume_approved':False,'formal_model_result':False,'protected_test_result':False,'selection_performed':False}
result={**flags,'artifact_paths':names+['engine_summary.json']}
print(json.dumps({'stage':'worker_success','result':result}), flush=True)
"""
    )
    eoc = tmp_path / "synthetic.zip"
    eoc.write_bytes(b"not training data")
    output = tmp_path / "subprocess-output"
    authorization = tmp_path / "subprocess-authorization.json"
    authorization.write_text(
        json.dumps(_authorization_payload({key: "0" * 64 for key in BINDING_KEYS}))
    )
    _bind_authorization(eoc, config, runner, output, authorization)
    assert (
        supervise(
            eoc_zip=eoc,
            output_dir=output,
            config_path=config,
            authorization_path=authorization,
            runner_path=runner,
        )
        == 0
    )
    assert json.loads((output / "terminal.json").read_text())["status"] == "completed"
    assert json.loads((output / "final_manifest.json").read_text())["status"] == "validated"


def _build_tamper_fixture(
    tmp_path: Path, *, actual_worker: bool = False
) -> tuple[Path, Path, Path, Path, Path]:
    root = tmp_path / "tamper-repo"
    config_dir = root / "config"
    source_dir = root / "src/battery_eis_soh"
    scripts_dir = root / "scripts"
    config_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    scripts_dir.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n")
    (root / "uv.lock").write_text("fixture-lock\n")
    (source_dir / "fixture.py").write_text("FIXTURE = True\n")
    config = config_dir / "kit_f1_b_capacity_sequence_v1.json"
    config.write_text(
        json.dumps(
            {
                "resources": {
                    "soft_drain_seconds": 20,
                    "hard_stop_seconds": 30,
                    "hard_rss_bytes": 2_000_000_000,
                    "minimum_free_bytes": 1,
                }
            }
        )
    )
    (config_dir / "kit_f1_b_capacity_sequence_v1.schema.json").write_text("{}\n")
    runner = scripts_dir / "run_kit_f1_b.py"
    runner.write_text(
        """#!/usr/bin/env python3
from battery_eis_soh.kit_f1_b_worker import main
raise SystemExit(main())
"""
        if actual_worker
        else """#!/usr/bin/env python3
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser(add_help=False)
p.add_argument('--worker', action='store_true')
p.add_argument('--output-dir', type=Path)
a,_=p.parse_known_args()
names=['source_manifest.json','snapshot_manifest.jsonl','fold_registry.json','oof_predictions.jsonl']
for name in names: (a.output_dir/name).write_text('{}\\n')
summary={'view':'B','planned_fits':18,'completed_fits':18}
(a.output_dir/'engine_summary.json').write_text(json.dumps(summary)+'\\n')
flags={'scientific_result':False,'publication_ready':False,'resume_approved':False,'formal_model_result':False,'protected_test_result':False,'selection_performed':False}
result={**flags,'artifact_paths':names+['engine_summary.json']}
print(json.dumps({'stage':'worker_success','result':result}), flush=True)
"""
    )
    eoc = tmp_path / "tamper-synthetic.zip"
    eoc.write_bytes(b"not training data")
    output = tmp_path / "tamper-output"
    authorization = tmp_path / "tamper-authorization.json"
    authorization.write_text(
        json.dumps(_authorization_payload({key: "0" * 64 for key in BINDING_KEYS}))
    )
    _bind_authorization(eoc, config, runner, output, authorization)
    return eoc, config, runner, output, authorization


def _write_synthetic_eoc(path: Path) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for age_type in (1, 2, 3):
            for offset in range(3):
                condition = age_type * 10 + offset + 1
                stream = StringIO(newline="")
                writer = csv.DictWriter(
                    stream, fieldnames=EOC_HEADER, delimiter=";", lineterminator="\n"
                )
                writer.writeheader()
                for sequence in range(8):
                    row = dict.fromkeys(EOC_HEADER, "0")
                    row.update(
                        timestamp_s=str(condition * 1_000_000 + sequence * 86_400),
                        timestamp_origin="1",
                        sd_block_id=str(sequence + 1),
                        cyc_condition="2",
                        cyc_charged="0",
                        age_type=str(age_type),
                        age_temp="25",
                        age_soc="50",
                        age_chg_rate="0.5",
                        age_dischg_rate="1.0",
                        age_profile=str(age_type if age_type == 3 else 0),
                        cap_aged_est_Ah=str(3.0 - 0.01 * sequence - 0.001 * offset),
                        cyc_duration_s="30",
                        num_cycles_op=str(sequence * 10),
                        num_cycles_checkup=str(sequence),
                        total_q_chg_sum_Ah=str(sequence * 6),
                        total_q_dischg_sum_Ah=str(sequence * 6),
                    )
                    writer.writerow(row)
                archive.writestr(
                    f"cell_eocv2_P{condition:03d}_1_S01_C{condition:02d}.csv",
                    stream.getvalue(),
                )


def _build_real_engine_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    eoc, config, runner, output, authorization = _build_tamper_fixture(tmp_path, actual_worker=True)
    _write_synthetic_eoc(eoc)
    repository = Path(__file__).parents[1]
    schema_path = config.with_name(f"{config.stem}.schema.json")
    schema = json.loads(
        (repository / "config/kit_f1_b_capacity_sequence_v1.schema.json").read_text()
    )
    source_properties = schema["properties"]["source"]["properties"]
    source_properties["eoc_sha256"]["const"] = hashlib.sha256(eoc.read_bytes()).hexdigest()
    source_properties["expected_cells"]["const"] = 9
    source_properties["expected_capacity_endpoints"]["const"] = 72
    schema["$defs"]["sourceManifest"]["allOf"][1]["properties"]["source_archive"]["properties"][
        "sha256"
    ]["const"] = hashlib.sha256(eoc.read_bytes()).hexdigest()
    schema_path.write_text(json.dumps(schema))
    config_payload = json.loads(
        (repository / "config/kit_f1_b_capacity_sequence_v1.json").read_text()
    )
    config_payload["source"] = {
        "eoc_sha256": hashlib.sha256(eoc.read_bytes()).hexdigest(),
        "expected_cells": 9,
        "expected_capacity_endpoints": 72,
    }
    config_payload["artifact_schema_sha256"] = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    config.write_text(json.dumps(config_payload))
    _bind_authorization(eoc, config, runner, output, authorization)
    return eoc, config, runner, output, authorization


def test_child_rejects_config_mutation_after_authorization_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eoc, config, runner, output, authorization = _build_tamper_fixture(tmp_path, actual_worker=True)
    real_popen = subprocess.Popen

    def mutate_then_launch(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        config.write_text(config.read_text() + "\n")
        return real_popen(*args, **kwargs)  # type: ignore[call-overload,return-value]

    monkeypatch.setattr("battery_eis_soh.kit_f1_b_worker.subprocess.Popen", mutate_then_launch)
    assert (
        supervise(
            eoc_zip=eoc,
            output_dir=output,
            config_path=config,
            authorization_path=authorization,
            runner_path=runner,
        )
        == 3
    )
    terminal = json.loads((output / "terminal.json").read_text())
    assert terminal["status"] == "failed"
    assert not (output / "engine_summary.json").exists()


def test_final_manifest_failure_cannot_leave_completed_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eoc, config, runner, output, authorization = _build_tamper_fixture(tmp_path)
    from battery_eis_soh import kit_f1_b_worker

    real_atomic = kit_f1_b_worker._atomic

    def fail_final(path: Path, value: object) -> None:
        if path.name == "final_manifest.json":
            raise OSError("synthetic final-manifest failure")
        real_atomic(path, value)

    monkeypatch.setattr(kit_f1_b_worker, "_atomic", fail_final)
    assert (
        supervise(
            eoc_zip=eoc,
            output_dir=output,
            config_path=config,
            authorization_path=authorization,
            runner_path=runner,
        )
        == 3
    )
    terminal = json.loads((output / "terminal.json").read_text())
    assert terminal["status"] == "failed"
    assert "final-manifest failure" in terminal["failure"]


@pytest.mark.parametrize("fault", ["pending_write", "final_link", "directory_fsync"])
def test_success_terminal_publish_fault_recovers_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    eoc, config, runner, output, authorization = _build_tamper_fixture(tmp_path)
    from battery_eis_soh import kit_f1_b_authorization as auth

    if fault == "pending_write":
        real_create = auth.create_exclusive_json

        def fail_completed_pending(path: Path, value: object) -> None:
            if isinstance(value, dict) and value.get("status") == "completed":
                raise OSError("synthetic pending-terminal write failure")
            real_create(path, value)

        monkeypatch.setattr(auth, "create_exclusive_json", fail_completed_pending)
    elif fault == "final_link":
        real_link = auth.os.link
        failed = {"done": False}

        def fail_completed_link(
            source: Path, target: Path, *, follow_symlinks: bool = True
        ) -> None:
            if target.name == "terminal.json" and not failed["done"]:
                failed["done"] = True
                raise OSError("synthetic final-terminal link failure")
            real_link(source, target, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(auth.os, "link", fail_completed_link)
    else:
        real_link = auth.os.link
        real_fsync_directory = auth._fsync_directory
        fail_next = {"value": False}
        failed_once = {"value": False}

        def mark_terminal_link(source: Path, target: Path, *, follow_symlinks: bool = True) -> None:
            real_link(source, target, follow_symlinks=follow_symlinks)
            if target.name == "terminal.json" and not failed_once["value"]:
                fail_next["value"] = True

        def fail_link_directory_fsync(directory: Path) -> None:
            if fail_next["value"]:
                fail_next["value"] = False
                failed_once["value"] = True
                raise OSError("synthetic terminal directory fsync failure")
            real_fsync_directory(directory)

        monkeypatch.setattr(auth.os, "link", mark_terminal_link)
        monkeypatch.setattr(auth, "_fsync_directory", fail_link_directory_fsync)

    assert (
        supervise(
            eoc_zip=eoc,
            output_dir=output,
            config_path=config,
            authorization_path=authorization,
            runner_path=runner,
        )
        == 3
    )
    terminal = json.loads((output / "terminal.json").read_text())
    assert terminal["status"] == "failed"
    assert not (output / "terminal.recovery.json").exists()
    assert not list(output.glob(".*.pending"))


def test_real_worker_and_engine_subprocess_complete_bounded_synthetic_run(
    tmp_path: Path,
) -> None:
    eoc, config, runner, output, authorization = _build_real_engine_fixture(tmp_path)
    assert (
        supervise(
            eoc_zip=eoc,
            output_dir=output,
            config_path=config,
            authorization_path=authorization,
            runner_path=runner,
        )
        == 0
    )
    start = json.loads((output / "start_manifest.json").read_text())
    summary = json.loads((output / "engine_summary.json").read_text())
    final = json.loads((output / "final_manifest.json").read_text())
    terminal = json.loads((output / "terminal.json").read_text())
    assert start["bindings"]["engine_sha256"] == validate_engine_contract()["module_sha256"]
    worker_module_path = __import__("battery_eis_soh.kit_f1_b_worker", fromlist=["x"]).__file__
    assert worker_module_path is not None
    assert (
        start["bindings"]["worker_sha256"]
        == hashlib.sha256(Path(worker_module_path).read_bytes()).hexdigest()
    )
    assert summary["planned_fits"] == summary["completed_fits"] == 18
    assert '"o2_' not in json.dumps(summary, sort_keys=True).lower()
    assert final["status"] == "validated"
    assert terminal["status"] == "completed"
    assert (
        terminal["final_manifest_sha256"]
        == hashlib.sha256((output / "final_manifest.json").read_bytes()).hexdigest()
    )
    for payload in (start, summary, final, terminal):
        for key in (
            "scientific_result",
            "publication_ready",
            "resume_approved",
            "formal_model_result",
            "protected_test_result",
            "selection_performed",
        ):
            assert payload[key] is False
    assert not list(output.glob(".*.pending"))


from io import StringIO
