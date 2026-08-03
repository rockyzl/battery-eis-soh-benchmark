# mypy: follow_untyped_imports = True

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import cast

import numpy as np
import pytest

from battery_eis_soh.kit_dev_training import (
    DevelopmentRunError,
    DevelopmentSnapshot,
    condition_balanced_regression_metrics,
    paired_condition_regression_bootstrap,
)
from battery_eis_soh.kit_dev_worker import (
    FALSE_CLAIM_FLAGS,
    SCIENTIFIC_ARTIFACTS,
    SCIENTIFIC_RESULT_FLAGS,
    ProcessController,
    ProcessTelemetry,
    Supervisor,
    SupervisorError,
    SupervisorLimits,
    WorkerContractError,
    WorkerProgress,
    _parse_proc_stat,
    _worker_main_test_fixture,
    fixed_worker_argv,
    prepare_paired_run_bindings,
    validate_engine_contract,
    validate_kit_f1_v2_artifact_tree,
    worker_main,
)
from battery_eis_soh.kit_paired_authorization import (
    ALLOWED as PAIRED_ALLOWED,
)
from battery_eis_soh.kit_paired_authorization import (
    BINDING_KEYS as PAIRED_BINDING_KEYS,
)
from battery_eis_soh.kit_paired_authorization import (
    FORBIDDEN as PAIRED_FORBIDDEN,
)
from battery_eis_soh.kit_paired_authorization import (
    RUN_KIND as PAIRED_RUN_KIND,
)
from battery_eis_soh.kit_paired_authorization import (
    SCOPE as PAIRED_SCOPE,
)
from battery_eis_soh.kit_paired_authorization import (
    VERSION as PAIRED_AUTHORIZATION_VERSION,
)
from battery_eis_soh.kit_paired_authorization import (
    PairedAuthorizationError,
)
from battery_eis_soh.kit_paired_authorization import (
    consume as consume_paired_authorization,
)
from battery_eis_soh.kit_paired_authorization import (
    validate_consumed as validate_consumed_paired_authorization,
)

F1_V2_CONFIG = Path(__file__).resolve().parents[1] / "config" / "kit_f1_o2_development_v2.json"
F1_V2_SCHEMA_VERSION = "kit_f1_o2_dev_v2"
F1_TASK_ID = "F1_SOH_LOSS_PP_H3"
O2_TASK_ID = "O2_RAPID_LOSS_5PP_H3"
F1_MODE_COUNTS = {"calendar": 1134, "cyclic": 1190, "profile": 595}
F1_FIXTURE_EOC_BYTES = b"formal-module synthetic EOC identity"
F1_FIXTURE_EIS_BYTES = b"formal-module synthetic EIS identity"


def _paired_authorization_payload(
    bindings: dict[str, str], *, nonce: str = "a" * 64, now: float | None = None
) -> dict[str, object]:
    issued = time.time() if now is None else now
    return {
        "authorization_version": PAIRED_AUTHORIZATION_VERSION,
        "authorization_id": "independent-paired-review-test",
        "status": "authorized",
        "single_use": True,
        "run_kind": PAIRED_RUN_KIND,
        "scope": PAIRED_SCOPE,
        "nonce": nonce,
        "issued_at_unix_s": issued - 1,
        "expires_at_unix_s": issued + 600,
        "bindings": bindings,
        "allowed": PAIRED_ALLOWED,
        "forbidden": PAIRED_FORBIDDEN,
        "approver_role": "independent reviewer fixture",
        "evidence_reviewer": "test-only independent review fixture",
    }


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_paired_readiness_config(tmp_path: Path) -> Path:
    """Create a portable, repository-shaped config with hash-bound readiness evidence."""
    repository = tmp_path / "repository"
    config_dir = repository / "config"
    evidence_dir = repository / "artifacts" / "audits"
    config_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)

    schema_source = F1_V2_CONFIG.with_name("kit_f1_o2_development_v2.schema.json")
    schema_path = config_dir / schema_source.name
    schema_path.write_bytes(schema_source.read_bytes())
    config = json.loads(F1_V2_CONFIG.read_text())
    readiness = config["readiness"]
    evidence = {
        "decision": readiness["eis_alignment_decision"],
        "cohorts": {
            "b_h3_total": readiness["expected_paired_snapshots"],
            "d_core_total": readiness["expected_paired_snapshots"],
            "b_missing_eis_total": 0,
        },
        "features": {
            "d_core_frequencies": readiness["expected_eis_frequencies"],
            "d_core_values": readiness["expected_eis_values"],
        },
        "alignment": {
            "eis_completion_after_or_at_h3_endpoint_violations": 0,
            "next_capacity_boundary_violations": 0,
        },
        "training_executed": False,
    }
    evidence_path = evidence_dir / "kit-d-core-eis-readiness.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    readiness["eis_readiness_artifact_sha256"] = hashlib.sha256(
        evidence_path.read_bytes()
    ).hexdigest()
    config["artifact_schema_sha256"] = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    config_path = config_dir / F1_V2_CONFIG.name
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    source_root = Path(__file__).resolve().parents[1]
    package_dir = repository / "src" / "battery_eis_soh"
    package_dir.mkdir(parents=True)
    for name in ("kit_v2.py", "kit_dev_training.py"):
        shutil.copy2(source_root / "src" / "battery_eis_soh" / name, package_dir / name)
    shutil.copy2(source_root / "uv.lock", repository / "uv.lock")
    shutil.copy2(source_root / "pyproject.toml", repository / "pyproject.toml")
    scripts_dir = repository / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(
        source_root / "scripts" / "run_kit_dev_engineering.py",
        scripts_dir / "run_kit_dev_engineering.py",
    )
    return config_path


def _artifact_base() -> dict[str, object]:
    return {**SCIENTIFIC_RESULT_FLAGS, "artifact_schema_version": F1_V2_SCHEMA_VERSION}


def _fixture_development_snapshot(row: dict[str, object]) -> DevelopmentSnapshot:
    """Convert one JSON-shape fixture row for training-side expected calculations."""
    return DevelopmentSnapshot(
        snapshot_id=str(row["snapshot_id"]),
        cell_id=str(row["cell_id"]),
        condition_id=int(_number(row["condition_id"])),
        mode=str(row["mode"]),
        checkup_index=int(_number(row["checkup_index"])),
        timestamp_s=_number(row["timestamp_s"]),
        q0_ah=_number(row["q0_ah"]),
        capacity_ah=_number(row["capacity_ah"]),
        soh_percent=_number(row["soh_percent"]),
        future_soh_percent=(
            None if row["future_soh_percent"] is None else _number(row["future_soh_percent"])
        ),
        soh_loss_pp_h3=(None if row["soh_loss_pp_h3"] is None else _number(row["soh_loss_pp_h3"])),
        h3_endpoint_snapshot_id=cast("str | None", row["h3_endpoint_snapshot_id"]),
        h3_checkup_index=(
            None if row["h3_checkup_index"] is None else int(_number(row["h3_checkup_index"]))
        ),
        f1_target_available=bool(row["f1_target_available"]),
        eligibility_status=str(row["eligibility_status"]),
        o2_label=None if row["o2_label"] is None else int(_number(row["o2_label"])),
        b_values=tuple(float(value) for value in cast("list[float]", row["b_values"])),
        primary_eis_values=tuple(
            float("nan") if value is None else float(value)
            for value in cast("list[float | None]", row["primary_eis_values"])
        ),
        keep_last_eis_values=tuple(
            float("nan") if value is None else float(value)
            for value in cast("list[float | None]", row["keep_last_eis_values"])
        ),
        acquisition_ids=tuple(cast("list[str]", row["acquisition_ids"])),
        finite_acquisition_count_by_feature=tuple(
            int(value) for value in cast("list[int]", row["finite_acquisition_count_by_feature"])
        ),
        keep_last_acquisition_id=cast("str | None", row["keep_last_acquisition_id"]),
        prediction_available_at_s=(
            None
            if row["prediction_available_at_s"] is None
            else _number(row["prediction_available_at_s"])
        ),
    )


def _fixture_prediction(row: dict[str, object], *, policy: str, view: str, family: str) -> float:
    """Create deterministic, non-degenerate predictions for public-validator tests."""
    target = _number(row["soh_loss_pp_h3"])
    condition = int(_number(row["condition_id"]))
    checkup = int(_number(row["checkup_index"]))
    direction = 1.0 if checkup % 2 == 0 else -1.0
    small_bias = 0.001 * (1 + condition % 2)
    if family == "persistence_zero_loss":
        return 0.0
    if family == "recent_slope_h3":
        return 3.0 * _number(cast("list[float]", row["b_values"])[3])
    if family == "elastic_net_regression":
        base_error, spread = (
            (0.04, 0.003)
            if view == "B"
            else (0.02, 0.001)
            if policy == "primary"
            else (0.07, 0.006)
        )
    elif family == "hist_gradient_boosting_regressor":
        base_error, spread = (
            (0.05, 0.004)
            if view == "B"
            else (0.03, 0.002)
            if policy == "primary"
            else (0.04, 0.003)
        )
    else:  # pragma: no cover - fixture authoring guard
        raise AssertionError(f"unexpected fixture model family {family}")
    return target + direction * (base_error + spread * (condition % 3)) + small_bias


def _fake_module(
    tmp_path: Path,
    *,
    bad_signature: bool = False,
    bad_annotation: bool = False,
) -> ModuleType:
    source = tmp_path / "fake_engine.py"
    source.write_text("# synthetic contract fixture\n")
    module = ModuleType("fake_engine")
    module.__file__ = str(source)

    def bad_engine(value: object) -> dict[str, object]:
        return {}

    def exact_engine(
        *,
        eoc_zip: Path,
        eis_zip: Path,
        output_dir: Path,
        config_path: Path,
        progress: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        return {}

    def wrong_annotation_engine(
        *,
        eoc_zip: str,
        eis_zip: Path,
        output_dir: Path,
        config_path: Path,
        progress: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        return {}

    selected: object = bad_engine if bad_signature else exact_engine
    if bad_annotation:
        selected = wrong_annotation_engine
    module.__dict__["execute_engineering_analysis"] = selected
    return module


class _Started:
    pid = 2718


class _StartFailureController:
    def __init__(self) -> None:
        self.argv: tuple[str, ...] | None = None

    def start(self, argv: Sequence[str]) -> _Started:
        self.argv = tuple(argv)
        raise OSError("synthetic start failure")

    def poll(self, process: object) -> int | None:  # pragma: no cover - cannot be reached
        return None

    def telemetry(self, process: object) -> ProcessTelemetry:  # pragma: no cover
        raise AssertionError

    def messages(self, process: object) -> list[dict[str, object]]:  # pragma: no cover
        return []

    def send_group(self, process: object, signal_number: int) -> None:  # pragma: no cover
        raise AssertionError


class _FakeClock:
    def __init__(self) -> None:
        self.current = 0.0

    def monotonic(self) -> float:
        return self.current

    def time(self) -> float:
        return 1_800_000_000.0 + self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


class _FakeProcess:
    pid = 31415


class _DeterministicController:
    def __init__(
        self,
        *,
        output: Path,
        immediate_exit: int | None = None,
        exit_after_polls: int | None = None,
        cpu_step: float = 0.0,
        rss_bytes: int = 123_456,
        create_scientific: bool = False,
        extra_kind: str | None = None,
        lingering_after_exit: bool = False,
        unkillable_group: bool = False,
        parent_tamper: str | None = None,
        heartbeat_messages: bool = False,
        progress_stage: str | None = None,
        progress_messages: list[dict[str, object]] | None = None,
        artifact_source: Path | None = None,
    ) -> None:
        self.output = output
        self.immediate_exit = immediate_exit
        self.exit_after_polls = exit_after_polls
        self.cpu_step = cpu_step
        self.rss_bytes = rss_bytes
        self.create_scientific = create_scientific
        self.extra_kind = extra_kind
        self.lingering_after_exit = lingering_after_exit
        self.unkillable_group = unkillable_group
        self.parent_tamper = parent_tamper
        self.heartbeat_messages = heartbeat_messages
        self.progress_stage = progress_stage
        self.progress_messages = list(progress_messages or [])
        self.artifact_source = artifact_source
        self.cpu = 0.0
        self.signals: list[int] = []
        self.killed = False
        self.exited = False
        self.poll_count = 0
        self.argv: tuple[str, ...] | None = None
        self.scientific_output: Path | None = None
        self.parent_tampered = False

    def start(self, argv: Sequence[str]) -> _FakeProcess:
        self.argv = tuple(argv)
        scientific_output = Path(self.argv[self.argv.index("--output-dir") + 1])
        self.scientific_output = scientific_output
        if self.create_scientific:
            _write_valid_scientific_artifacts(scientific_output)
        elif self.artifact_source is not None:
            for name in SCIENTIFIC_ARTIFACTS:
                os.link(self.artifact_source / name, scientific_output / name)
        if self.extra_kind == "regular":
            (scientific_output / "unexpected.txt").write_text("not allowed")
        elif self.extra_kind == "symlink":
            (scientific_output / SCIENTIFIC_ARTIFACTS[-1]).unlink()
            (scientific_output / SCIENTIFIC_ARTIFACTS[-1]).symlink_to(
                scientific_output / "engine_summary.target"
            )
        elif self.extra_kind == "special":
            (scientific_output / SCIENTIFIC_ARTIFACTS[-1]).unlink()
            os.mkfifo(scientific_output / SCIENTIFIC_ARTIFACTS[-1])
        elif self.extra_kind == "arbitrary_text":
            (scientific_output / "engine_summary.json").write_text("not JSON")
        elif self.extra_kind == "bad_claim_flag":
            summary = json.loads((scientific_output / "engine_summary.json").read_text())
            summary["formal_model_result"] = 0
            (scientific_output / "engine_summary.json").write_text(json.dumps(summary))
        elif self.extra_kind == "bad_artifact_paths":
            summary = json.loads((scientific_output / "engine_summary.json").read_text())
            summary["artifact_paths"] = list(reversed(SCIENTIFIC_ARTIFACTS))
            (scientific_output / "engine_summary.json").write_text(json.dumps(summary))
        elif self.extra_kind == "bad_planned_fits":
            summary = json.loads((scientific_output / "engine_summary.json").read_text())
            summary["planned_fits"] = "arbitrary-text"
            (scientific_output / "engine_summary.json").write_text(json.dumps(summary))
        elif self.extra_kind == "unknown_summary_field":
            summary = json.loads((scientific_output / "engine_summary.json").read_text())
            summary["unreviewed"] = "not allowed"
            (scientific_output / "engine_summary.json").write_text(json.dumps(summary))
        if self.parent_tamper == "run_status_during_start":
            (self.output / "run_status.json").write_text("tampered during controller start")
            self.parent_tampered = True
        return _FakeProcess()

    def _tamper_parent_evidence(self) -> None:
        if self.parent_tampered or self.parent_tamper is None:
            return
        target_by_kind = {
            "start_manifest": "start_manifest.json",
            "run_status": "run_status.json",
            "lifecycle": "lifecycle.jsonl",
            "lifecycle_symlink": "lifecycle.jsonl",
            "telemetry": "telemetry.jsonl",
            "terminal_collision": "terminal.json",
        }
        target = self.output / target_by_kind[self.parent_tamper]
        if self.parent_tamper == "lifecycle_symlink":
            target.unlink()
            external = self.output.parent / "external-parent-evidence-target"
            external.write_text("external")
            target.symlink_to(external)
        else:
            target.write_text("tampered")
        self.parent_tampered = True

    def poll(self, process: object) -> int | None:
        if self.killed:
            self.exited = True
            return -signal.SIGKILL
        self.poll_count += 1
        if self.immediate_exit is not None and (
            self.exit_after_polls is None or self.poll_count >= self.exit_after_polls
        ):
            self._tamper_parent_evidence()
            self.exited = True
            return self.immediate_exit
        return None

    def telemetry(self, process: object) -> ProcessTelemetry:
        self.cpu += self.cpu_step
        process_count = 1
        if not self.unkillable_group and (
            self.killed or self.exited and not self.lingering_after_exit
        ):
            process_count = 0
        return ProcessTelemetry(self.cpu, self.rss_bytes, process_count)

    def messages(self, process: object) -> list[dict[str, object]]:
        if self.progress_messages:
            messages = self.progress_messages
            self.progress_messages = []
            return messages
        if self.heartbeat_messages:
            return [{"stage": "heartbeat"}]
        return [{"stage": self.progress_stage}] if self.progress_stage is not None else []

    def send_group(self, process: object, signal_number: int) -> None:
        self.signals.append(signal_number)
        if signal_number == signal.SIGKILL:
            self.killed = True


def _supervisor(
    tmp_path: Path,
    controller: ProcessController,
    *,
    clock: _FakeClock | None = None,
    limits: SupervisorLimits | None = None,
) -> Supervisor:
    for name in ("eoc.zip", "eis.zip", "config.json"):
        (tmp_path / name).write_bytes(name.encode())
    return Supervisor(
        eoc_zip=tmp_path / "eoc.zip",
        eis_zip=tmp_path / "eis.zip",
        output_dir=tmp_path / "run",
        config_path=tmp_path / "config.json",
        limits=limits or SupervisorLimits(),
        clock=clock or _FakeClock(),
        controller=controller,
        engine_module_for_validation=_fake_module(tmp_path),
        platform_check=lambda: True,
    )


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _number(value: object) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


def _write_valid_scientific_artifacts(output: Path) -> None:
    acquisition = {
        **SCIENTIFIC_RESULT_FLAGS,
        "acquisition_id": "A1",
        "cell_id": "C1",
        "condition_id": 1,
        "timestamp_s": 1.0,
        "timestamp_origin": 0,
        "duration_s": 1.0,
        "sd_block_id": 1,
        "source_archive": "eis.zip",
        "source_archive_sha256": "a" * 64,
        "source_member": "member.csv",
        "source_line": 1,
        "aligned_snapshot_id": "S1",
        "within_60h_candidate_only": False,
        "same_aligned_checkup_as_previous": False,
        "within_60h_candidate_reason": "not_within_60h_candidate_window",
        "eis_values": [0.0] * 54,
    }
    snapshot = {
        **SCIENTIFIC_RESULT_FLAGS,
        "snapshot_id": "S1",
        "cell_id": "C1",
        "condition_id": 1,
        "mode": "cyclic",
        "checkup_index": 2,
        "timestamp_s": 1.0,
        "q0_ah": 1.0,
        "capacity_ah": 0.9,
        "soh_percent": 90.0,
        "future_soh_percent": 84.0,
        "o2_label": 1,
        "b_values": [0.0] * 12,
        "primary_eis_values": [0.0] * 54,
        "keep_last_eis_values": [0.0] * 54,
        "acquisition_ids": ["A1"],
        "finite_acquisition_count_by_feature": [1] * 54,
        "keep_last_acquisition_id": "A1",
    }
    prediction = {
        **SCIENTIFIC_RESULT_FLAGS,
        "snapshot_id": "S1",
        "cell_id": "C1",
        "condition_id": 1,
        "mode": "cyclic",
        "acquisition_policy": "primary",
        "model_family": "logistic_elastic_net",
        "view": "B",
        "paired_design_sha256": "b" * 64,
        "outer_fold": 0,
        "label": 1,
        "raw_probability": 0.25,
        "calibrated_probability": 0.2,
        "outer_validation_weight_mean_one": 1.0,
        "platt": {"slope": 1.0, "intercept": 0.0, "iterations": 1},
    }
    (output / "acquisition_manifest.jsonl").write_text(json.dumps(acquisition) + "\n")
    (output / "snapshot_manifest.jsonl").write_text(json.dumps(snapshot) + "\n")
    (output / "oof_predictions.jsonl").write_text(json.dumps(prediction) + "\n")
    class_support = {
        "snapshots": 1,
        "events": 1,
        "non_events": 0,
        "cells": 1,
        "conditions": 1,
        "positive_supporting_conditions": 1,
        "negative_supporting_conditions": 0,
    }
    mode_support = {"overall": class_support, "folds": []}
    paired_design = {
        "applies_identically_to_feature_views": ["B", "D_CORE"],
        "applies_identically_to_acquisition_policies": [
            "primary",
            "keep_last_sensitivity",
        ],
        "ordered_cohort_snapshot_ids": [],
        "metric_weights": [],
        "folds": [],
        "paired_design_sha256": "b" * 64,
    }
    objects: dict[str, dict[str, object]] = {
        "fold_registry.json": {
            **SCIENTIFIC_RESULT_FLAGS,
            "split_policy": "condition_grouped_outer3_inner2_v1",
            "support": {"cyclic": mode_support, "profile": mode_support},
            "paired_design_by_mode": {
                "cyclic": paired_design,
                "profile": paired_design,
            },
            "modes": {"cyclic": [], "profile": []},
        },
        "paired_deltas.json": {**SCIENTIFIC_RESULT_FLAGS, "paired_deltas": []},
        "retry_sensitivity.json": {**SCIENTIFIC_RESULT_FLAGS, "results": []},
        "engine_summary.json": {
            **SCIENTIFIC_RESULT_FLAGS,
            "run_kind": "KIT_O2_DEVELOPMENT_ROBUSTNESS_V1",
            "outcome": "O2_RAPID_LOSS_5PP_H3",
            "source_archives": {"eoc": "eoc.zip", "eis": "eis.zip"},
            "source_counts": {"real_cells": 1},
            "materialization": {
                "counts": {},
                "eligible_acquisitions": 1,
                "within_60h_candidate_count": 0,
                "policy": {
                    "primary": "equal_finite_mean_per_frequency_v1",
                    "sensitivity": "keep_last_acquisition_v1",
                    "retry_window_seconds": 216000.0,
                    "retry_window_role": "flag_only_never_group_or_drop",
                    "timestamp_semantics_provisional": True,
                },
            },
            "snapshot_count": 1,
            "planned_fits": 1,
            "completed_fits": 1,
            "candidate_role": "fixed_robustness_only_no_selection",
            "config_sha256": "a" * 64,
            "metrics": [],
            "comparison_policy": "report_each_fixed_family_without_ranking_or_promotion",
            "artifact_paths": list(SCIENTIFIC_ARTIFACTS),
        },
    }
    for name, payload in objects.items():
        (output / name).write_text(json.dumps(payload) + "\n")


def _write_f1_v2_scientific_artifacts(output: Path) -> None:
    """Write the frozen official-shape F1-v2 contract without fitting a model."""
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(F1_V2_CONFIG.read_text())
    schema_path = F1_V2_CONFIG.with_name(Path(config["$schema"]).name)
    base = _artifact_base()
    acquisitions: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []
    eligible_by_mode: dict[str, list[dict[str, object]]] = {mode: [] for mode in F1_MODE_COUNTS}
    global_cell_index = 0
    for mode, eligible_total in F1_MODE_COUNTS.items():
        quotient, remainder = divmod(eligible_total, 76)
        for local_cell_index in range(76):
            cell_id = f"cell-{global_cell_index:03d}"
            condition_id = tuple(F1_MODE_COUNTS).index(mode) * 3 + local_cell_index % 3
            history_count = 2 if global_cell_index < 168 else 1
            eligible_count = quotient + int(local_cell_index < remainder)
            sequence_count = history_count + eligible_count + 3
            for checkup_index in range(sequence_count):
                snapshot_id = f"{cell_id}:checkup-{checkup_index}"
                timestamp_s = global_cell_index * 10_000.0 + checkup_index * 10.0
                target_available = checkup_index + 3 < sequence_count
                eligible = history_count <= checkup_index < history_count + eligible_count
                eligibility_status = (
                    "eligible_f1_and_d_core"
                    if eligible
                    else "insufficient_history"
                    if target_available
                    else "no_h3_followup"
                )
                condition_curve = 0.01 * (condition_id % 3)
                current_soh = 100.0 - 0.005 * checkup_index**2 - condition_curve * checkup_index
                future_soh = (
                    100.0 - 0.005 * (checkup_index + 3) ** 2 - condition_curve * (checkup_index + 3)
                    if target_available
                    else None
                )
                observed_loss = current_soh - future_soh if future_soh is not None else None
                omit_acquisition = (
                    mode == "profile"
                    and local_cell_index >= 74
                    and checkup_index >= sequence_count - 3
                )
                acquisition_id = None if omit_acquisition else f"acq:{snapshot_id}"
                available_at = None if omit_acquisition else timestamp_s + 0.5
                eis_values = [float(index) / 10.0 for index in range(54)]
                snapshot = {
                    **base,
                    "snapshot_id": snapshot_id,
                    "cell_id": cell_id,
                    "condition_id": condition_id,
                    "mode": mode,
                    "checkup_index": checkup_index,
                    "timestamp_s": timestamp_s,
                    "q0_ah": 1.0,
                    "capacity_ah": current_soh / 100.0,
                    "soh_percent": current_soh,
                    "future_soh_percent": future_soh,
                    "soh_loss_pp_h3": observed_loss,
                    "h3_endpoint_snapshot_id": (
                        f"{cell_id}:checkup-{checkup_index + 3}" if target_available else None
                    ),
                    "h3_checkup_index": checkup_index + 3 if target_available else None,
                    "f1_target_available": target_available,
                    "eligibility_status": eligibility_status,
                    "o2_label": 0 if target_available else None,
                    "b_values": [float(index) / 100.0 for index in range(12)],
                    "primary_eis_values": eis_values if acquisition_id else [None] * 54,
                    "keep_last_eis_values": eis_values if acquisition_id else [None] * 54,
                    "acquisition_ids": [acquisition_id] if acquisition_id else [],
                    "finite_acquisition_count_by_feature": (
                        [1] * 54 if acquisition_id else [0] * 54
                    ),
                    "keep_last_acquisition_id": acquisition_id,
                    "prediction_available_at_s": available_at,
                }
                snapshots.append(snapshot)
                if eligible:
                    eligible_by_mode[mode].append(snapshot)
                if acquisition_id:
                    acquisitions.append(
                        {
                            **base,
                            "acquisition_id": acquisition_id,
                            "cell_id": cell_id,
                            "condition_id": condition_id,
                            "timestamp_s": timestamp_s + 0.25,
                            "timestamp_origin": 0,
                            "duration_s": 0.25,
                            "sd_block_id": checkup_index,
                            "source_archive": "eis.zip",
                            "source_archive_sha256": config["source"]["eis_sha256"],
                            "source_member": f"{cell_id}.csv",
                            "source_line": checkup_index + 1,
                            "aligned_snapshot_id": snapshot_id,
                            "within_60h_candidate_only": False,
                            "same_aligned_checkup_as_previous": False,
                            "within_60h_candidate_reason": "not_within_60h_candidate_window",
                            "eis_values": eis_values,
                        }
                    )
            global_cell_index += 1

    assert len(snapshots) == 3999
    assert len(acquisitions) == 3993
    assert {mode: len(rows) for mode, rows in eligible_by_mode.items()} == F1_MODE_COUNTS

    support: dict[str, object] = {}
    modes: dict[str, object] = {}
    preprocessing: dict[str, object] = {}
    paired_designs: dict[str, dict[str, object]] = {}
    predictions: list[dict[str, object]] = []
    metrics: list[dict[str, object]] = []
    paired_deltas: list[dict[str, object]] = []
    sensitivities: list[dict[str, object]] = []
    fitted_families = ("elastic_net_regression", "hist_gradient_boosting_regressor")
    baseline_families = ("persistence_zero_loss", "recent_slope_h3")
    workflows = (
        ("primary", "B"),
        ("primary", "D_CORE"),
        ("keep_last_sensitivity", "D_CORE"),
    )
    snapshot_by_id = {str(row["snapshot_id"]): row for row in snapshots}
    for mode, rows in eligible_by_mode.items():
        ids = [str(row["snapshot_id"]) for row in rows]
        ids_by_fold = {
            fold: [
                str(row["snapshot_id"])
                for row in rows
                if int(_number(row["condition_id"])) % 3 == fold
            ]
            for fold in range(3)
        }

        def weight_rows(snapshot_ids: Sequence[str]) -> list[dict[str, object]]:
            conditions = {snapshot_by_id[value]["condition_id"] for value in snapshot_ids}
            cells_by_condition: dict[object, set[object]] = {}
            checkups_by_cell: Counter[tuple[object, object]] = Counter()
            for value in snapshot_ids:
                row = snapshot_by_id[value]
                condition = row["condition_id"]
                cell = row["cell_id"]
                cells_by_condition.setdefault(condition, set()).add(cell)
                checkups_by_cell[(condition, cell)] += 1
            raw = [
                1.0
                / (
                    len(conditions)
                    * len(cells_by_condition[snapshot_by_id[value]["condition_id"]])
                    * checkups_by_cell[
                        (
                            snapshot_by_id[value]["condition_id"],
                            snapshot_by_id[value]["cell_id"],
                        )
                    ]
                )
                for value in snapshot_ids
            ]
            mean = math.fsum(raw) / len(raw)
            return [
                {"snapshot_id": snapshot_id, "weight_mean_one": raw[index] / mean}
                for index, snapshot_id in enumerate(snapshot_ids)
            ]

        design_folds = []
        registry_folds = []
        support_folds = []
        for outer_fold in range(3):
            validation_ids = ids_by_fold[outer_fold]
            training_ids = [item for item in ids if item not in set(validation_ids)]
            inner_folds = []
            registry_inner_folds = []
            inner_support = []
            remaining = [fold for fold in range(3) if fold != outer_fold]
            for inner_fold, validation_condition_fold in enumerate(remaining):
                inner_validation_ids = ids_by_fold[validation_condition_fold]
                inner_training_ids = [
                    item for item in training_ids if item not in set(inner_validation_ids)
                ]
                inner_folds.append(
                    {
                        "inner_fold": inner_fold,
                        "training_weights": weight_rows(inner_training_ids),
                        "validation_weights": weight_rows(inner_validation_ids),
                    }
                )
                registry_inner_folds.append(
                    {
                        "train_snapshot_ids": inner_training_ids,
                        "validation_snapshot_ids": inner_validation_ids,
                    }
                )
                inner_support.append(
                    {
                        "inner_fold": inner_fold,
                        "snapshots": len(inner_validation_ids),
                        "cells": len({item.split(":", 1)[0] for item in inner_validation_ids}),
                        "conditions": 1,
                        "negative_loss_snapshots": 0,
                        "o2_candidate_events": 0,
                    }
                )
            design_folds.append(
                {
                    "outer_fold": outer_fold,
                    "training_weights": weight_rows(training_ids),
                    "validation_weights": weight_rows(validation_ids),
                    "inner_folds": inner_folds,
                }
            )
            registry_folds.append(
                {
                    "outer_fold": outer_fold,
                    "train_snapshot_ids": training_ids,
                    "validation_snapshot_ids": validation_ids,
                    "inner_folds": registry_inner_folds,
                }
            )
            support_folds.append(
                {
                    "outer_fold": outer_fold,
                    "validation": {
                        "snapshots": len(validation_ids),
                        "cells": len({item.split(":", 1)[0] for item in validation_ids}),
                        "conditions": 1,
                        "negative_loss_snapshots": 0,
                        "o2_candidate_events": 0,
                    },
                    "inner_validation": inner_support,
                }
            )
        design: dict[str, object] = {
            "fold_assignment_task_independent": True,
            "applies_to_tasks": [F1_TASK_ID, O2_TASK_ID],
            "b_cohort_snapshot_ids": ids,
            "d_core_eligible_snapshot_ids": ids,
            "paired_comparison_subset": "d_core_eligible_intersection_only",
            "applies_identically_to_acquisition_policies": [
                "primary",
                "keep_last_sensitivity",
            ],
            "ordered_cohort_snapshot_ids": ids,
            "metric_weights": weight_rows(ids),
            "folds": design_folds,
        }
        design["paired_design_sha256"] = _canonical_sha256(design)
        paired_designs[mode] = design
        validation_weights_by_fold = {
            int(cast("int", fold["outer_fold"])): {
                str(item["snapshot_id"]): float(item["weight_mean_one"])
                for item in cast("list[dict[str, float | str]]", fold["validation_weights"])
            }
            for fold in design_folds
        }
        modes[mode] = registry_folds
        support[mode] = {
            "overall": {
                "snapshots": len(rows),
                "cells": len({str(row["cell_id"]) for row in rows}),
                "conditions": 3,
                "negative_loss_snapshots": 0,
                "o2_candidate_events": 0,
            },
            "folds": support_folds,
        }
        preprocessing[mode] = [
            {
                "outer_fold": outer_fold,
                "acquisition_policy": policy,
                "view": view,
                "model_family": family,
                "retained_eis_feature_names": (
                    [f"eis_{index:02d}" for index in range(54)] if view == "D_CORE" else []
                ),
                "missing_indicator_names": [],
                "imputation_provenance_sha256": f"{outer_fold + 1:064x}",
                "scaling_provenance_sha256": (
                    f"{outer_fold + 4:064x}" if family == "elastic_net_regression" else None
                ),
            }
            for outer_fold in range(3)
            for policy, view in workflows
            for family in fitted_families
        ]
        design_hash = str(design["paired_design_sha256"])
        for row in rows:
            outer_fold = int(_number(row["condition_id"])) % 3
            common = {
                **base,
                "task_id": F1_TASK_ID,
                "target_kind": "continuous_regression_primary",
                "target_name": "soh_loss_pp_h3",
                "snapshot_id": row["snapshot_id"],
                "cell_id": row["cell_id"],
                "condition_id": row["condition_id"],
                "mode": mode,
                "paired_design_sha256": design_hash,
                "outer_fold": outer_fold,
                "outer_validation_weight_mean_one": validation_weights_by_fold[outer_fold][
                    str(row["snapshot_id"])
                ],
                "observed_soh_loss_pp_h3": row["soh_loss_pp_h3"],
                "current_soh_percent": row["soh_percent"],
                "observed_future_soh_percent": row["future_soh_percent"],
                "prediction_interval_low_pp": None,
                "prediction_interval_high_pp": None,
                "prediction_interval_status": "unavailable_not_frozen",
            }
            for family in baseline_families:
                predicted = _fixture_prediction(row, policy="primary", view="B", family=family)
                predictions.append(
                    {
                        **common,
                        "acquisition_policy": "primary",
                        "view": "B",
                        "model_role": "baseline",
                        "model_family": family,
                        "fit_id": None,
                        "predicted_soh_loss_pp_h3": predicted,
                        "predicted_future_soh_percent": _number(row["soh_percent"]) - predicted,
                    }
                )
            for policy, view in workflows:
                for family in fitted_families:
                    predicted = _fixture_prediction(row, policy=policy, view=view, family=family)
                    predictions.append(
                        {
                            **common,
                            "acquisition_policy": policy,
                            "view": view,
                            "model_role": "fitted_candidate",
                            "model_family": family,
                            "fit_id": (
                                f"{F1_TASK_ID}:{mode}:{policy}:{view}:{family}:outer-{outer_fold}"
                            ),
                            "predicted_soh_loss_pp_h3": predicted,
                            "predicted_future_soh_percent": _number(row["soh_percent"]) - predicted,
                        }
                    )

        typed_rows = [_fixture_development_snapshot(row) for row in rows]
        prediction_identities = [
            *(("primary", "B", family) for family in baseline_families),
            *((policy, view, family) for policy, view in workflows for family in fitted_families),
        ]
        predictions_for = {
            (item_policy, item_view, item_family): [
                _number(item["predicted_soh_loss_pp_h3"])
                for item in predictions
                if item["mode"] == mode
                and item["acquisition_policy"] == item_policy
                and item["view"] == item_view
                and item["model_family"] == item_family
            ]
            for item_policy, item_view, item_family in prediction_identities
        }
        metric_packets = {
            identity: condition_balanced_regression_metrics(
                typed_rows, np.asarray(values, dtype=np.float64)
            )
            for identity, values in predictions_for.items()
        }
        for family in baseline_families:
            metrics.append(
                {
                    **base,
                    "task_id": F1_TASK_ID,
                    "target_kind": "continuous_regression_primary",
                    "target_name": "soh_loss_pp_h3",
                    "mode": mode,
                    "acquisition_policy": "primary",
                    "view": "B",
                    "model_role": "baseline",
                    "model_family": family,
                    "paired_design_sha256": design_hash,
                    "metrics": metric_packets[("primary", "B", family)],
                }
            )
        for policy, view in workflows:
            for family in fitted_families:
                metrics.append(
                    {
                        **base,
                        "task_id": F1_TASK_ID,
                        "target_kind": "continuous_regression_primary",
                        "target_name": "soh_loss_pp_h3",
                        "mode": mode,
                        "acquisition_policy": policy,
                        "view": view,
                        "model_role": "fitted_candidate",
                        "model_family": family,
                        "paired_design_sha256": design_hash,
                        "metrics": metric_packets[(policy, view, family)],
                    }
                )
        for family in fitted_families:
            policy_mae_deltas: dict[str, float] = {}
            for policy in ("primary", "keep_last_sensitivity"):
                b_metrics = metric_packets[("primary", "B", family)]
                d_metrics = metric_packets[(policy, "D_CORE", family)]
                b_predictions = np.asarray(
                    predictions_for[("primary", "B", family)], dtype=np.float64
                )
                d_predictions = np.asarray(
                    predictions_for[(policy, "D_CORE", family)], dtype=np.float64
                )
                bootstrap = paired_condition_regression_bootstrap(
                    typed_rows, b_predictions, d_predictions
                )
                mae_delta = _number(d_metrics["condition_balanced_mae_pp_v1"]) - _number(
                    b_metrics["condition_balanced_mae_pp_v1"]
                )
                policy_mae_deltas[policy] = mae_delta
                b_r2 = b_metrics["condition_balanced_r2_v1"]
                d_r2 = d_metrics["condition_balanced_r2_v1"]
                paired_deltas.append(
                    {
                        **base,
                        "task_id": F1_TASK_ID,
                        "target_kind": "continuous_regression_primary",
                        "target_name": "soh_loss_pp_h3",
                        "mode": mode,
                        "model_family": family,
                        "acquisition_policy": policy,
                        "same_snapshot_ids_and_weights": True,
                        "mae_delta_d_core_minus_b_pp": mae_delta,
                        "median_ae_delta_d_core_minus_b_pp": _number(
                            d_metrics["condition_balanced_median_ae_pp_v1"]
                        )
                        - _number(b_metrics["condition_balanced_median_ae_pp_v1"]),
                        "rmse_delta_d_core_minus_b_pp": _number(
                            d_metrics["condition_balanced_rmse_pp_v1"]
                        )
                        - _number(b_metrics["condition_balanced_rmse_pp_v1"]),
                        "signed_bias_delta_d_core_minus_b_pp": _number(
                            d_metrics["condition_balanced_signed_bias_pp_v1"]
                        )
                        - _number(b_metrics["condition_balanced_signed_bias_pp_v1"]),
                        "r2_delta_d_core_minus_b": (
                            None if b_r2 is None or d_r2 is None else _number(d_r2) - _number(b_r2)
                        ),
                        "bootstrap": bootstrap,
                        "comparison_role": "paired_diagnostic_only_never_promotion",
                    }
                )
            sensitivities.append(
                {
                    **base,
                    "task_id": F1_TASK_ID,
                    "target_kind": "continuous_regression_primary",
                    "target_name": "soh_loss_pp_h3",
                    "mode": mode,
                    "model_family": family,
                    "primary_policy": "equal_finite_mean_per_frequency_v1",
                    "sensitivity_policy": "keep_last_acquisition_v1",
                    "mae_delta_sign_reversal": (
                        policy_mae_deltas["primary"] * policy_mae_deltas["keep_last_sensitivity"]
                        < 0.0
                    ),
                    "mae_delta_absolute_change_pp": abs(
                        policy_mae_deltas["keep_last_sensitivity"] - policy_mae_deltas["primary"]
                    ),
                    "prohibit_eis_increment_conclusion": (
                        policy_mae_deltas["primary"] * policy_mae_deltas["keep_last_sensitivity"]
                        < 0.0
                    ),
                    "decision_role": "diagnostic_stop_only_never_promotion",
                }
            )

    assert len(predictions) == 23_352
    assert len(metrics) == 24
    assert len(paired_deltas) == 12
    assert len(sensitivities) == 6
    assert any(
        cast("dict[str, object]", row["metrics"])["condition_balanced_r2_v1"] is not None
        for row in metrics
    )
    assert all(abs(_number(row["mae_delta_d_core_minus_b_pp"])) > 0.0 for row in paired_deltas)
    assert all(
        _number(cast("list[float]", cast("dict[str, object]", row["bootstrap"])[field])[1])
        > _number(cast("list[float]", cast("dict[str, object]", row["bootstrap"])[field])[0])
        for row in paired_deltas
        for field in ("mae_delta_percentile_95", "rmse_delta_percentile_95")
    )
    assert (
        sum(
            abs(_number(row[field])) > 0.0
            for row in paired_deltas
            for field in (
                "mae_delta_d_core_minus_b_pp",
                "median_ae_delta_d_core_minus_b_pp",
                "rmse_delta_d_core_minus_b_pp",
                "signed_bias_delta_d_core_minus_b_pp",
                "r2_delta_d_core_minus_b",
            )
        )
        >= 5
    )
    retry_reversals = {bool(row["mae_delta_sign_reversal"]) for row in sensitivities}
    assert retry_reversals == {False, True}
    fold_registry = {
        **base,
        "split_policy": "condition_grouped_outer3_inner2_f1_v2",
        "task_independent": True,
        "applies_to_tasks": [F1_TASK_ID, O2_TASK_ID],
        "support": support,
        "preprocessing_by_mode": preprocessing,
        "paired_design_by_mode": paired_designs,
        "modes": modes,
    }
    snapshot_status = {
        "eligible_f1_and_d_core": 2919,
        "insufficient_history": 396,
        "no_h3_followup": 684,
    }
    summary = {
        **base,
        "run_kind": "KIT_F1_O2_DEVELOPMENT_V2",
        "source_archives": {
            "eoc": {"path_name": "eoc.zip", "sha256": config["source"]["eoc_sha256"]},
            "eis": {"path_name": "eis.zip", "sha256": config["source"]["eis_sha256"]},
        },
        "source_counts": {
            "real_cells": 228,
            "capacity_endpoints": 3999,
            "valid_rt50_spectra": 3993,
        },
        "materialization": {
            "counts": snapshot_status,
            "eligible_acquisitions": 3993,
            "within_60h_candidate_count": 0,
            "policy": {
                "primary": "equal_finite_mean_per_frequency_v1",
                "sensitivity": "keep_last_acquisition_v1",
                "retry_window_seconds": 216000.0,
                "retry_window_role": "flag_only_never_group_or_drop",
                "timestamp_semantics_provisional": True,
            },
        },
        "snapshot_count": 3999,
        "target_registry": [
            {
                "task_id": F1_TASK_ID,
                "target_kind": "continuous_regression_primary",
                "target_name": "soh_loss_pp_h3",
                "unit": "percentage_points",
                "status": "development_oof_only_eis_go_with_explicit_limitations",
                "training_authorized": True,
                "display_authorized": False,
                "selection_role": "none",
                "synthetic_capability_exercised": False,
                "planned_fits": 54,
                "completed_fits": 54,
                "prediction_rows": 23_352,
            },
            {
                "task_id": O2_TASK_ID,
                "target_kind": "binary_probability_secondary_candidate",
                "target_name": "rapid_loss_ge_5pp_h3",
                "unit": "probability",
                "status": "blocked_pending_noise_support_utility_review",
                "training_authorized": False,
                "display_authorized": False,
                "selection_role": "none",
                "synthetic_capability_exercised": False,
                "planned_fits": 0,
                "completed_fits": 0,
                "prediction_rows": 0,
            },
        ],
        "target_counts": {
            "snapshot_status": snapshot_status,
            "f1_exact_h3_available": 3315,
            "o2_candidate_events_observed": 0,
            "o2_prediction_rows": 0,
        },
        "planned_fits": 54,
        "completed_fits": 54,
        "fit_counts_by_task": {
            F1_TASK_ID: {"planned": 54, "completed": 54},
            O2_TASK_ID: {"planned": 0, "completed": 0},
        },
        "candidate_role": "fixed_development_oof_only_no_selection",
        "metrics": metrics,
        "comparison_policy": "report_each_fixed_family_without_ranking_or_promotion",
        "o2_results": [],
        "config_sha256": hashlib.sha256(F1_V2_CONFIG.read_bytes()).hexdigest(),
        "artifact_schema_sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
        "artifact_paths": list(SCIENTIFIC_ARTIFACTS),
    }
    jsonl_payloads = {
        "acquisition_manifest.jsonl": acquisitions,
        "snapshot_manifest.jsonl": snapshots,
        "oof_predictions.jsonl": predictions,
    }
    for name, rows in jsonl_payloads.items():
        (output / name).write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
        )
    for name, payload in {
        "fold_registry.json": fold_registry,
        "paired_deltas.json": {**base, "paired_deltas": paired_deltas},
        "retry_sensitivity.json": {**base, "results": sensitivities},
        "engine_summary.json": summary,
    }.items():
        (output / name).write_text(json.dumps(payload, separators=(",", ":")) + "\n")


def _write_f1_v2_fixture_config(artifact_root: Path, config_root: Path) -> Path:
    """Bind the official-scale synthetic fixture to its own exact cohort/design identities."""
    config = json.loads(F1_V2_CONFIG.read_text())
    config["source"]["eoc_sha256"] = hashlib.sha256(F1_FIXTURE_EOC_BYTES).hexdigest()
    config["source"]["eoc_size_bytes"] = len(F1_FIXTURE_EOC_BYTES)
    config["source"]["eis_sha256"] = hashlib.sha256(F1_FIXTURE_EIS_BYTES).hexdigest()
    config["source"]["eis_size_bytes"] = len(F1_FIXTURE_EIS_BYTES)
    source_schema = F1_V2_CONFIG.with_name(Path(config["$schema"]).name)
    schema = json.loads(source_schema.read_text())
    source_properties = schema["properties"]["source"]["properties"]
    for field in ("eoc_sha256", "eoc_size_bytes", "eis_sha256", "eis_size_bytes"):
        source_properties[field]["const"] = config["source"][field]
    snapshots = _jsonl(artifact_root / "snapshot_manifest.jsonl")
    paired_rows = [
        {
            "snapshot_id": row["snapshot_id"],
            "cell_id": row["cell_id"],
            "condition_id": row["condition_id"],
            "mode": row["mode"],
            "checkup_index": row["checkup_index"],
            "timestamp_s_hex": float(cast("int | float", row["timestamp_s"])).hex(),
            "h3_endpoint_snapshot_id": row["h3_endpoint_snapshot_id"],
            "h3_checkup_index": row["h3_checkup_index"],
            "soh_percent_hex": float(cast("int | float", row["soh_percent"])).hex(),
            "future_soh_percent_hex": float(cast("int | float", row["future_soh_percent"])).hex(),
        }
        for row in sorted(
            (item for item in snapshots if item["eligibility_status"] == "eligible_f1_and_d_core"),
            key=lambda item: (str(item["mode"]), str(item["snapshot_id"])),
        )
    ]
    folds = json.loads((artifact_root / "fold_registry.json").read_text())
    config["readiness"]["paired_cohort_identity_sha256"] = _canonical_sha256(paired_rows)
    config["readiness"]["paired_design_sha256_by_mode"] = {
        mode: value["paired_design_sha256"]
        for mode, value in folds["paired_design_by_mode"].items()
    }
    readiness_schema = schema["properties"]["readiness"]["properties"]
    readiness_schema["paired_cohort_identity_sha256"] = {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
    }
    readiness_schema["paired_design_sha256_by_mode"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["calendar", "cyclic", "profile"],
        "properties": {
            mode: {"type": "string", "pattern": "^[0-9a-f]{64}$"}
            for mode in ("calendar", "cyclic", "profile")
        },
    }
    config_root.mkdir()
    schema_path = config_root / source_schema.name
    schema_path.write_text(json.dumps(schema, separators=(",", ":")) + "\n")
    config["artifact_schema_sha256"] = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    config_path = config_root / F1_V2_CONFIG.name
    config_path.write_text(json.dumps(config, separators=(",", ":")) + "\n")
    acquisitions = _jsonl(artifact_root / "acquisition_manifest.jsonl")
    for row in acquisitions:
        row["source_archive_sha256"] = config["source"]["eis_sha256"]
    _replace_jsonl(artifact_root / "acquisition_manifest.jsonl", acquisitions)
    summary_path = artifact_root / "engine_summary.json"
    summary = json.loads(summary_path.read_text())
    summary["source_archives"]["eoc"]["sha256"] = config["source"]["eoc_sha256"]
    summary["source_archives"]["eis"]["sha256"] = config["source"]["eis_sha256"]
    summary["config_sha256"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    summary["artifact_schema_sha256"] = config["artifact_schema_sha256"]
    _replace_json(summary_path, summary)
    return config_path


@pytest.fixture(scope="session")
def f1_v2_artifact_tree(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("f1-v2-artifacts")
    _write_f1_v2_scientific_artifacts(root)
    config_path = _write_f1_v2_fixture_config(
        root, tmp_path_factory.mktemp("f1-v2-config-root") / "config"
    )
    validate_kit_f1_v2_artifact_tree(root, config_path)
    return root, config_path


@pytest.fixture(scope="session")
def f1_v2_terminal_tree(
    tmp_path_factory: pytest.TempPathFactory,
    f1_v2_artifact_tree: tuple[Path, Path],
) -> tuple[Path, Path]:
    artifact_tree, config_path = f1_v2_artifact_tree
    root = tmp_path_factory.mktemp("f1-v2-terminal")
    eoc = root / "eoc.zip"
    eis = root / "eis.zip"
    eoc.write_bytes(F1_FIXTURE_EOC_BYTES)
    eis.write_bytes(F1_FIXTURE_EIS_BYTES)
    output = root / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=0,
        artifact_source=artifact_tree,
    )
    result = Supervisor(
        eoc_zip=eoc,
        eis_zip=eis,
        output_dir=output,
        config_path=config_path,
        controller=controller,
        engine_module_for_validation=None,
        platform_check=lambda: True,
    ).run()
    assert result == 0
    start_path = output / "start_manifest.json"
    start = json.loads(start_path.read_text())
    config = json.loads(config_path.read_text())
    readiness = config["readiness"]
    bindings = {key: "a" * 64 for key in PAIRED_BINDING_KEYS}
    bindings.update(
        {
            "eoc_sha256": config["source"]["eoc_sha256"],
            "eis_sha256": config["source"]["eis_sha256"],
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "schema_sha256": hashlib.sha256(
                config_path.with_name(f"{config_path.stem}.schema.json").read_bytes()
            ).hexdigest(),
            "readiness_sha256": readiness["eis_readiness_artifact_sha256"],
            "engine_sha256": start["engine"]["module_sha256"],
            "paired_cohort_sha256": readiness["paired_cohort_identity_sha256"],
            "paired_designs_sha256": hashlib.sha256(
                json.dumps(
                    readiness["paired_design_sha256_by_mode"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "trial_keys_sha256": readiness["planned_trial_keys_sha256"],
        }
    )
    start["authorization"] = {
        "authorization_id": "terminal-contract-fixture",
        "authorization_sha256": "b" * 64,
        "nonce": "c" * 64,
        "receipt_sha256": "d" * 64,
    }
    start["bindings"] = bindings
    start["inputs"]["eoc"]["sha256"] = config["source"]["eoc_sha256"]
    start["inputs"]["eis"]["sha256"] = config["source"]["eis_sha256"]
    _replace_json(start_path, start)
    final_path = output / "final_manifest.json"
    final = json.loads(final_path.read_text())
    final["artifacts_sha256"]["start_manifest.json"] = hashlib.sha256(
        start_path.read_bytes()
    ).hexdigest()
    final["artifacts_size_bytes"]["start_manifest.json"] = start_path.stat().st_size
    _replace_json(final_path, final)
    validate_kit_f1_v2_artifact_tree(
        output,
        config_path,
        require_terminal_tree=True,
    )
    return output, config_path


def _hardlink_artifact_tree(source: Path, destination: Path) -> Path:
    destination.mkdir()
    for name in SCIENTIFIC_ARTIFACTS:
        os.link(source / name, destination / name)
    return destination


def _hardlink_complete_tree(source: Path, destination: Path) -> Path:
    destination.mkdir()
    for path in source.iterdir():
        os.link(path, destination / path.name)
    return destination


def _replace_json(path: Path, payload: object) -> None:
    replacement = path.with_suffix(path.suffix + ".replacement")
    replacement.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    os.replace(replacement, path)


def _replace_jsonl(path: Path, rows: Sequence[dict[str, object]]) -> None:
    replacement = path.with_suffix(path.suffix + ".replacement")
    replacement.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))
    os.replace(replacement, path)


def test_formal_f1_v2_seven_artifact_fixture_is_accepted(
    f1_v2_artifact_tree: tuple[Path, Path],
) -> None:
    artifact_tree, config_path = f1_v2_artifact_tree
    validate_kit_f1_v2_artifact_tree(artifact_tree, config_path)


@pytest.mark.parametrize("poison", ["fit_id", "fold_coverage"])
def test_f1_v2_public_validator_recomputes_actual_fit_plan_digest_and_coverage(
    f1_v2_artifact_tree: tuple[Path, Path], tmp_path: Path, poison: str
) -> None:
    artifact_tree, config_path = f1_v2_artifact_tree
    root = tmp_path / poison
    shutil.copytree(artifact_tree, root)
    prediction_path = root / "oof_predictions.jsonl"
    rows = [json.loads(line) for line in prediction_path.read_text().splitlines()]
    index = next(
        position
        for position, row in enumerate(rows)
        if row["model_role"] == "fitted_candidate" and row["outer_fold"] == 0
    )
    row = rows[index]
    if poison == "fit_id":
        row["fit_id"] = str(row["fit_id"]).replace("outer-0", "outer-1")
        expected = "fit ID"
    else:
        row["outer_fold"] = 1
        row["fit_id"] = (
            f"{row['task_id']}:{row['mode']}:{row['acquisition_policy']}:"
            f"{row['view']}:{row['model_family']}:outer-1"
        )
        expected = "OOF coverage"
    _replace_jsonl(prediction_path, rows)
    with pytest.raises(SupervisorError, match=expected):
        validate_kit_f1_v2_artifact_tree(root, config_path)


def test_f1_v2_public_validator_recomputes_metric_packet_from_oof(
    f1_v2_artifact_tree: tuple[Path, Path], tmp_path: Path
) -> None:
    artifact_tree, config_path = f1_v2_artifact_tree
    root = tmp_path / "metric-poison"
    shutil.copytree(artifact_tree, root)
    summary_path = root / "engine_summary.json"
    summary = json.loads(summary_path.read_text())
    summary["metrics"][0]["metrics"]["condition_balanced_mae_pp_v1"] += 0.01
    _replace_json(summary_path, summary)
    with pytest.raises(SupervisorError, match="metric packet"):
        validate_kit_f1_v2_artifact_tree(root, config_path)


def test_f1_v2_public_validator_recomputes_baseline_predictions(
    f1_v2_artifact_tree: tuple[Path, Path], tmp_path: Path
) -> None:
    artifact_tree, config_path = f1_v2_artifact_tree
    root = tmp_path / "baseline-poison"
    shutil.copytree(artifact_tree, root)
    prediction_path = root / "oof_predictions.jsonl"
    rows = [json.loads(line) for line in prediction_path.read_text().splitlines()]
    row = next(item for item in rows if item["model_family"] == "recent_slope_h3")
    row["predicted_soh_loss_pp_h3"] += 0.01
    row["predicted_future_soh_percent"] -= 0.01
    _replace_jsonl(prediction_path, rows)
    with pytest.raises(SupervisorError, match="baseline prediction semantic"):
        validate_kit_f1_v2_artifact_tree(root, config_path)


@pytest.mark.parametrize("poison", ["delta", "bootstrap_ci", "retry"])
def test_f1_v2_public_validator_recomputes_delta_bootstrap_and_retry(
    f1_v2_artifact_tree: tuple[Path, Path], tmp_path: Path, poison: str
) -> None:
    artifact_tree, config_path = f1_v2_artifact_tree
    root = tmp_path / poison
    shutil.copytree(artifact_tree, root)
    if poison in {"delta", "bootstrap_ci"}:
        path = root / "paired_deltas.json"
        payload = json.loads(path.read_text())
        row = payload["paired_deltas"][0]
        if poison == "delta":
            row["mae_delta_d_core_minus_b_pp"] += 0.01
        else:
            row["bootstrap"]["mae_delta_percentile_95"][0] += 0.01
        _replace_json(path, payload)
        expected = "paired delta"
    else:
        path = root / "retry_sensitivity.json"
        payload = json.loads(path.read_text())
        row = payload["results"][0]
        row["mae_delta_sign_reversal"] = not row["mae_delta_sign_reversal"]
        row["mae_delta_absolute_change_pp"] += 0.01
        row["prohibit_eis_increment_conclusion"] = not row["prohibit_eis_increment_conclusion"]
        _replace_json(path, payload)
        expected = "retry sensitivity"
    with pytest.raises(SupervisorError, match=expected):
        validate_kit_f1_v2_artifact_tree(root, config_path)


@pytest.mark.parametrize(
    "poison",
    [
        "paired_snapshot_id",
        "paired_outer_fold",
        "paired_weight",
        "paired_count_2919",
        "prediction_count_23352",
        "metric_count_24",
        "delta_count_12",
        "sensitivity_count_6",
        "fit_count_54",
        "mode_counts",
        "target_arithmetic",
        "o2_nonzero",
        "claim_flag_true",
        "condition_leakage",
        "inner_condition_leakage",
        "keep_last_acquisition_mismatch",
        "h3_lineage_mismatch",
        "source_hash_mismatch",
        "source_count_mismatch",
        "rogue_prediction_row",
        "duplicate_prediction_row",
    ],
)
def test_formal_f1_v2_poison_is_rejected(
    tmp_path: Path,
    f1_v2_artifact_tree: tuple[Path, Path],
    poison: str,
) -> None:
    artifact_tree, config_path = f1_v2_artifact_tree
    root = _hardlink_artifact_tree(artifact_tree, tmp_path / "poisoned")
    if poison in {
        "paired_snapshot_id",
        "paired_outer_fold",
        "paired_weight",
        "prediction_count_23352",
        "rogue_prediction_row",
        "duplicate_prediction_row",
    }:
        path = root / "oof_predictions.jsonl"
        rows = _jsonl(path)
        d_index = next(
            index
            for index, row in enumerate(rows)
            if row["model_role"] == "fitted_candidate" and row["view"] == "D_CORE"
        )
        if poison == "paired_snapshot_id":
            rows[d_index]["snapshot_id"] = rows[d_index + 8]["snapshot_id"]
        elif poison == "paired_outer_fold":
            rows[d_index]["outer_fold"] = (int(_number(rows[d_index]["outer_fold"])) + 1) % 3
        elif poison == "paired_weight":
            rows[d_index]["outer_validation_weight_mean_one"] = 2.0
        elif poison == "prediction_count_23352":
            rows.pop()
        elif poison == "rogue_prediction_row":
            rows[d_index]["snapshot_id"] = "rogue:checkup-999"
        else:
            rows[-1] = dict(rows[d_index])
        _replace_jsonl(path, rows)
    elif poison in {
        "paired_count_2919",
        "mode_counts",
        "target_arithmetic",
        "claim_flag_true",
        "keep_last_acquisition_mismatch",
        "h3_lineage_mismatch",
    }:
        path = root / "snapshot_manifest.jsonl"
        rows = _jsonl(path)
        eligible_index = next(
            index
            for index, row in enumerate(rows)
            if row["eligibility_status"] == "eligible_f1_and_d_core"
        )
        if poison == "paired_count_2919":
            rows[eligible_index]["eligibility_status"] = "insufficient_history"
        elif poison == "mode_counts":
            rows[eligible_index]["mode"] = "cyclic"
        elif poison == "target_arithmetic":
            rows[eligible_index]["soh_loss_pp_h3"] = 0.4
        elif poison == "claim_flag_true":
            rows[eligible_index]["formal_model_result"] = True
        elif poison == "h3_lineage_mismatch":
            rows[eligible_index]["h3_endpoint_snapshot_id"] = rows[eligible_index + 1][
                "h3_endpoint_snapshot_id"
            ]
        else:
            rows[eligible_index]["keep_last_acquisition_id"] = "acq:wrong"
        _replace_jsonl(path, rows)
    elif poison in {"condition_leakage", "inner_condition_leakage"}:
        path = root / "fold_registry.json"
        payload = json.loads(path.read_text())
        fold = payload["modes"]["calendar"][0]
        if poison == "condition_leakage":
            fold["train_snapshot_ids"].append(fold["validation_snapshot_ids"][0])
        else:
            inner = fold["inner_folds"][0]
            train_id = inner["train_snapshot_ids"][0]
            validation_id = inner["validation_snapshot_ids"][0]
            inner["train_snapshot_ids"][0] = validation_id
            inner["validation_snapshot_ids"][0] = train_id
        _replace_json(path, payload)
    elif poison in {
        "metric_count_24",
        "fit_count_54",
        "o2_nonzero",
        "source_hash_mismatch",
        "source_count_mismatch",
    }:
        path = root / "engine_summary.json"
        payload = json.loads(path.read_text())
        if poison == "metric_count_24":
            payload["metrics"].pop()
        elif poison == "fit_count_54":
            payload["planned_fits"] = 53
        elif poison == "source_hash_mismatch":
            payload["source_archives"]["eoc"]["sha256"] = "0" * 64
        elif poison == "source_count_mismatch":
            payload["source_counts"]["real_cells"] = 227
        else:
            payload["target_registry"][1]["prediction_rows"] = 1
        _replace_json(path, payload)
    elif poison == "delta_count_12":
        path = root / "paired_deltas.json"
        payload = json.loads(path.read_text())
        payload["paired_deltas"].pop()
        _replace_json(path, payload)
    elif poison == "sensitivity_count_6":
        path = root / "retry_sensitivity.json"
        payload = json.loads(path.read_text())
        payload["results"].pop()
        _replace_json(path, payload)
    else:  # pragma: no cover - parameter list and implementation must stay paired
        raise AssertionError(f"unimplemented poison: {poison}")

    with pytest.raises(Exception, match="F1-v2"):
        validate_kit_f1_v2_artifact_tree(root, config_path)


@pytest.mark.parametrize(
    "poison",
    [
        "scientific_hash_mismatch",
        "terminal_mismatch",
        "manifest_hash_mismatch",
        "manifest_size_mismatch",
        "start_run_kind",
        "start_authorization",
        "start_binding",
        "start_input",
        "start_argv_shape",
    ],
)
def test_formal_f1_v2_terminal_or_hash_mismatch_is_rejected(
    tmp_path: Path,
    f1_v2_terminal_tree: tuple[Path, Path],
    poison: str,
) -> None:
    terminal_tree, config_path = f1_v2_terminal_tree
    root = _hardlink_complete_tree(terminal_tree, tmp_path / "terminal-poison")
    if poison == "scientific_hash_mismatch":
        path = root / "engine_summary.json"
        payload = json.loads(path.read_text())
        payload["metrics"][0]["metrics"]["condition_balanced_mae_pp_v1"] = 0.06
        _replace_json(path, payload)
    elif poison == "terminal_mismatch":
        path = root / "terminal.json"
        payload = json.loads(path.read_text())
        payload["exit_code"] = 3
        _replace_json(path, payload)
    elif poison in {"manifest_hash_mismatch", "manifest_size_mismatch"}:
        path = root / "final_manifest.json"
        payload = json.loads(path.read_text())
        field = "artifacts_sha256" if poison == "manifest_hash_mismatch" else "artifacts_size_bytes"
        payload[field]["engine_summary.json"] = (
            "0" * 64 if poison == "manifest_hash_mismatch" else 1
        )
        _replace_json(path, payload)
    else:
        path = root / "start_manifest.json"
        payload = json.loads(path.read_text())
        if poison == "start_run_kind":
            payload["run_kind"] = "KIT_F1_UNAUTHORIZED"
        elif poison == "start_authorization":
            payload["authorization"]["receipt_sha256"] = "not-a-sha"
        elif poison == "start_binding":
            payload["bindings"]["paired_cohort_sha256"] = "0" * 64
        elif poison == "start_input":
            payload["inputs"]["eis"]["sha256"] = "0" * 64
        else:
            payload["worker_argv_shape"].pop()
        _replace_json(path, payload)
        final_path = root / "final_manifest.json"
        final = json.loads(final_path.read_text())
        final["artifacts_sha256"]["start_manifest.json"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        final["artifacts_size_bytes"]["start_manifest.json"] = path.stat().st_size
        _replace_json(final_path, final)
    with pytest.raises(Exception, match="F1-v2"):
        validate_kit_f1_v2_artifact_tree(
            root,
            config_path,
            require_terminal_tree=True,
        )


def test_contract_validation_accepts_exact_signature_and_rejects_drift(tmp_path: Path) -> None:
    contract = validate_engine_contract(_fake_module(tmp_path))
    assert contract["module_sha256"]
    assert contract["callable"] == "execute_engineering_analysis"
    with pytest.raises(WorkerContractError, match="parameter names drifted"):
        validate_engine_contract(_fake_module(tmp_path, bad_signature=True))
    with pytest.raises(WorkerContractError, match="annotations drifted"):
        validate_engine_contract(_fake_module(tmp_path, bad_annotation=True))


def test_fixed_worker_argv_has_no_user_selected_executable_or_module(tmp_path: Path) -> None:
    argv = fixed_worker_argv(
        eoc_zip=tmp_path / "eoc.zip",
        eis_zip=tmp_path / "eis.zip",
        output_dir=tmp_path / "output",
        config_path=tmp_path / "config.json",
        expected_engine_sha256="a" * 64,
    )
    assert argv[1:4] == ("-m", "battery_eis_soh.kit_dev_worker", "--worker")
    assert "--engine-module" not in argv
    assert "--executable" not in argv


def test_proc_stat_parser_handles_spaces_and_parentheses_in_process_name() -> None:
    fields = [
        "S",
        "1",
        "31415",
        "31415",
        "0",
        "-1",
        "0",
        "0",
        "0",
        "0",
        "0",
        "12",
        "8",
        "0",
        "0",
        "20",
        "0",
        "1",
        "0",
        "100",
        "4096",
        "42",
    ]
    assert _parse_proc_stat(f"99 (python worker (x)) {' '.join(fields)}") == (31415, 12, 8, 42)
    with pytest.raises(ValueError, match="malformed"):
        _parse_proc_stat("99 python-without-parentheses")
    with pytest.raises(ValueError, match="truncated"):
        _parse_proc_stat("99 (python) S 1 2")


def test_start_manifest_precedes_child_and_has_false_flags_and_engine_hash(
    tmp_path: Path,
) -> None:
    inputs = {}
    for name in ("eoc.zip", "eis.zip", "config.json"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        inputs[name] = path
    output = tmp_path / "run"
    controller = _StartFailureController()
    result = Supervisor(
        eoc_zip=inputs["eoc.zip"],
        eis_zip=inputs["eis.zip"],
        output_dir=output,
        config_path=inputs["config.json"],
        controller=controller,
        engine_module_for_validation=_fake_module(tmp_path),
        platform_check=lambda: True,
    ).run()
    assert result == 3
    manifest = json.loads((output / "start_manifest.json").read_text())
    assert manifest["engine"]["module_sha256"]
    assert all(manifest[key] is value for key, value in FALSE_CLAIM_FLAGS.items())
    assert controller.argv is not None
    assert controller.argv[1:4] == ("-m", "battery_eis_soh.kit_dev_worker", "--worker")


def test_telemetry_records_process_group_cpu_rss_and_gap_below_ten_seconds(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=0,
        exit_after_polls=3,
        cpu_step=0.25,
    )
    assert _supervisor(tmp_path, controller).run() == 3
    rows = _jsonl(output / "telemetry.jsonl")
    assert rows[0]["cpu_seconds"] == pytest.approx(0.25)
    assert rows[0]["rss_bytes"] == 123_456
    assert rows[0]["process_count"] == 1
    gaps = [
        _number(second["at_unix_s"]) - _number(first["at_unix_s"])
        for first, second in pairwise(rows)
    ]
    assert len(gaps) == 2
    assert max(gaps) <= 10.0


def test_stale_process_group_sends_term_then_kill(tmp_path: Path) -> None:
    clock = _FakeClock()
    output = tmp_path / "run"
    controller = _DeterministicController(output=output, cpu_step=100.0)
    limits = SupervisorLimits(
        telemetry_interval_s=5,
        stale_s=10,
        soft_drain_s=20,
        terminate_s=30,
        kill_s=40,
        rss_soft_bytes=1_000_000,
        rss_hard_bytes=2_000_000,
    )
    assert _supervisor(tmp_path, controller, clock=clock, limits=limits).run() == 124
    assert controller.signals == [signal.SIGTERM, signal.SIGKILL]
    events = [row["event"] for row in _jsonl(output / "lifecycle.jsonl")]
    assert "term_sent" in events
    assert "kill_sent" in events
    failure = json.loads((output / "failure.json").read_text())
    assert failure["reason"] == "stale_heartbeat"


def test_hard_rss_limit_kills_immediately_without_term_or_soft_drain(tmp_path: Path) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(output=output, rss_bytes=2_000_000)
    limits = SupervisorLimits(
        telemetry_interval_s=1,
        stale_s=30,
        soft_drain_s=40,
        terminate_s=45,
        kill_s=48,
        rss_soft_bytes=1_000_000,
        rss_hard_bytes=2_000_000,
    )
    assert _supervisor(tmp_path, controller, limits=limits).run() == 124
    assert controller.signals == [signal.SIGKILL]
    kill_events = [row for row in _jsonl(output / "lifecycle.jsonl") if row["event"] == "kill_sent"]
    assert len(kill_events) == 1
    assert kill_events[0]["reason"] == "rss_hard_limit"


def test_soft_drain_then_term_and_kill_and_worker_stops_before_new_fit(
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    output = tmp_path / "run"
    controller = _DeterministicController(output=output, cpu_step=0.1)
    limits = SupervisorLimits(
        telemetry_interval_s=1,
        stale_s=30,
        soft_drain_s=4,
        terminate_s=5,
        kill_s=6,
        rss_soft_bytes=1_000_000,
        rss_hard_bytes=2_000_000,
    )
    assert _supervisor(tmp_path, controller, clock=clock, limits=limits).run() == 124
    assert controller.signals == [signal.SIGUSR1, signal.SIGTERM, signal.SIGKILL]

    module = _fake_module(tmp_path)
    fit_calls: list[str] = []

    def engine(
        *,
        eoc_zip: Path,
        eis_zip: Path,
        output_dir: Path,
        config_path: Path,
        progress: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        os.kill(os.getpid(), signal.SIGUSR1)
        progress({"stage": "fit_started"})
        fit_calls.append("fit")
        return {}

    module.__dict__["execute_engineering_analysis"] = engine
    assert (
        _worker_main_test_fixture(
            [
                "--worker",
                "--eoc-zip",
                str(tmp_path / "eoc.zip"),
                "--eis-zip",
                str(tmp_path / "eis.zip"),
                "--output-dir",
                str(tmp_path / "worker-output"),
                "--config-path",
                str(tmp_path / "config.json"),
                "--expected-engine-sha256",
                validate_engine_contract(module)["module_sha256"],
            ],
            module,
        )
        == 124
    )
    assert fit_calls == []


def test_worker_rejects_engine_sha_mismatch_before_use(tmp_path: Path) -> None:
    module = _fake_module(tmp_path)
    assert (
        _worker_main_test_fixture(
            [
                "--worker",
                "--eoc-zip",
                str(tmp_path / "eoc.zip"),
                "--eis-zip",
                str(tmp_path / "eis.zip"),
                "--output-dir",
                str(tmp_path / "worker-output"),
                "--config-path",
                str(tmp_path / "config.json"),
                "--expected-engine-sha256",
                "0" * 64,
            ],
            module,
        )
        == 3
    )


def test_worker_emits_only_allowlisted_development_failure_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("eoc.zip", "eis.zip", "config.json"):
        (tmp_path / name).write_bytes(name.encode())
    module = _fake_module(tmp_path)

    def failing_engine(
        *,
        eoc_zip: Path,
        eis_zip: Path,
        output_dir: Path,
        config_path: Path,
        progress: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        del eoc_zip, eis_zip, output_dir, config_path, progress
        raise DevelopmentRunError(
            "secret=/tmp/private/input.csv traceback=must-not-escape",
            error_code="convergence_failed",
        )

    module.__dict__["execute_engineering_analysis"] = failing_engine
    result = _worker_main_test_fixture(
        [
            "--worker",
            "--eoc-zip",
            str(tmp_path / "eoc.zip"),
            "--eis-zip",
            str(tmp_path / "eis.zip"),
            "--output-dir",
            str(tmp_path / "worker-output"),
            "--config-path",
            str(tmp_path / "config.json"),
            "--expected-engine-sha256",
            validate_engine_contract(module)["module_sha256"],
        ],
        module,
    )

    assert result == 2
    captured = capsys.readouterr().out
    assert json.loads(captured) == {
        "stage": "worker_failure",
        "error_type": "DevelopmentRunError",
        "error_code": "convergence_failed",
    }
    assert "secret" not in captured
    assert "/tmp/private" not in captured
    assert "traceback" not in captured


def test_worker_maps_unknown_exception_type_and_code_to_allowlisted_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("eoc.zip", "eis.zip", "config.json"):
        (tmp_path / name).write_bytes(name.encode())
    module = _fake_module(tmp_path)

    def failing_engine(
        *,
        eoc_zip: Path,
        eis_zip: Path,
        output_dir: Path,
        config_path: Path,
        progress: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        del eoc_zip, eis_zip, output_dir, config_path, progress
        raise ValueError("secret=/tmp/private/input.csv traceback=must-not-escape")

    module.__dict__["execute_engineering_analysis"] = failing_engine
    result = _worker_main_test_fixture(
        [
            "--worker",
            "--eoc-zip",
            str(tmp_path / "eoc.zip"),
            "--eis-zip",
            str(tmp_path / "eis.zip"),
            "--output-dir",
            str(tmp_path / "worker-output"),
            "--config-path",
            str(tmp_path / "config.json"),
            "--expected-engine-sha256",
            validate_engine_contract(module)["module_sha256"],
        ],
        module,
    )

    assert result == 2
    captured = capsys.readouterr().out
    assert json.loads(captured) == {
        "stage": "worker_failure",
        "error_type": "UnknownDevelopmentError",
        "error_code": "unknown_development_failure",
    }
    assert "secret" not in captured
    assert "/tmp/private" not in captured
    assert "traceback" not in captured


@pytest.mark.parametrize(
    ("provided_type", "provided_code", "expected_type", "expected_code"),
    [
        (
            "DevelopmentRunError",
            "convergence_failed",
            "DevelopmentRunError",
            "convergence_failed",
        ),
        (
            "PrivatePoisonError",
            "../../../private-data",
            "UnknownDevelopmentError",
            "unknown_development_failure",
        ),
    ],
)
def test_supervisor_sanitizes_worker_failure_payload_and_terminal_reason(
    tmp_path: Path,
    provided_type: str,
    provided_code: str,
    expected_type: str,
    expected_code: str,
) -> None:
    output = tmp_path / "run"
    poison = "secret=/home/user/private.csv traceback=must-not-escape"
    controller = _DeterministicController(
        output=output,
        immediate_exit=2,
        progress_messages=[
            {
                "stage": "worker_failure",
                "error_type": provided_type,
                "error_code": provided_code,
                "message": poison,
                "traceback": poison,
                "path": "/home/user/private.csv",
                "data": {"target": 99.0},
            }
        ],
    )

    assert _supervisor(tmp_path, controller).run() == 2
    failure_rows = [
        row
        for row in _jsonl(output / "lifecycle.jsonl")
        if row["event"] == "worker_progress"
        and cast("dict[str, object]", row["payload"])["stage"] == "worker_failure"
    ]
    assert [row["payload"] for row in failure_rows] == [
        {
            "stage": "worker_failure",
            "error_type": expected_type,
            "error_code": expected_code,
        }
    ]
    failure = json.loads((output / "failure.json").read_text())
    assert failure["reason"] == expected_code
    serialized = "\n".join(
        (output / name).read_text()
        for name in (
            "lifecycle.jsonl",
            "run_status.json",
            "failure.json",
            "final_manifest.json",
        )
    )
    assert poison not in serialized
    assert "/home/user/private.csv" not in serialized


def test_direct_official_worker_without_paired_authorization_fails_closed(
    tmp_path: Path,
) -> None:
    contract = validate_engine_contract()
    assert (
        worker_main(
            [
                "--worker",
                "--eoc-zip",
                str(tmp_path / "eoc.zip"),
                "--eis-zip",
                str(tmp_path / "eis.zip"),
                "--output-dir",
                str(tmp_path / "scientific-staging"),
                "--config-path",
                str(F1_V2_CONFIG),
                "--expected-engine-sha256",
                contract["module_sha256"],
            ],
            module=None,
        )
        == 3
    )


def test_injected_official_module_object_cannot_bypass_paired_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    official = importlib.import_module("battery_eis_soh.kit_dev_training")
    called: list[bool] = []

    def forbidden_call(**_kwargs: object) -> dict[str, object]:
        called.append(True)
        return {}

    monkeypatch.setattr(official, "execute_engineering_analysis", forbidden_call)
    assert (
        worker_main(
            [
                "--worker",
                "--eoc-zip",
                str(tmp_path / "eoc.zip"),
                "--eis-zip",
                str(tmp_path / "eis.zip"),
                "--output-dir",
                str(tmp_path / "scientific-staging"),
                "--config-path",
                str(F1_V2_CONFIG),
                "--expected-engine-sha256",
                "0" * 64,
            ],
            module=official,
        )
        == 3
    )
    assert called == []


def test_same_byte_alias_module_cannot_bypass_paired_authorization(tmp_path: Path) -> None:
    official = importlib.import_module("battery_eis_soh.kit_dev_training")
    origin = Path(str(official.__file__))
    alias_source = tmp_path / "renamed_engine_alias.py"
    shutil.copyfile(origin, alias_source)
    alias = ModuleType("renamed_engine_alias")
    alias.__file__ = str(alias_source)
    called: list[bool] = []

    def forbidden_call(**_kwargs: object) -> dict[str, object]:
        called.append(True)
        return {}

    alias.__dict__["execute_engineering_analysis"] = forbidden_call
    assert (
        worker_main(
            [
                "--worker",
                "--eoc-zip",
                str(tmp_path / "eoc.zip"),
                "--eis-zip",
                str(tmp_path / "eis.zip"),
                "--output-dir",
                str(tmp_path / "scientific-staging"),
                "--config-path",
                str(F1_V2_CONFIG),
                "--expected-engine-sha256",
                hashlib.sha256(alias_source.read_bytes()).hexdigest(),
            ],
            module=alias,
        )
        == 3
    )
    assert called == []


def test_different_source_alias_with_official_callable_cannot_bypass_authorization(
    tmp_path: Path,
) -> None:
    official = importlib.import_module("battery_eis_soh.kit_dev_training")
    alias_source = tmp_path / "alias_wrapper.py"
    alias_source.write_text("# intentionally different wrapper bytes\n")
    alias = ModuleType("alias_wrapper")
    alias.__file__ = str(alias_source)
    alias.__dict__["execute_engineering_analysis"] = official.execute_engineering_analysis
    assert (
        worker_main(
            [
                "--worker",
                "--eoc-zip",
                str(tmp_path / "missing-eoc.zip"),
                "--eis-zip",
                str(tmp_path / "missing-eis.zip"),
                "--output-dir",
                str(tmp_path / "scientific-staging"),
                "--config-path",
                str(F1_V2_CONFIG),
                "--expected-engine-sha256",
                "0" * 64,
            ],
            module=alias,
        )
        == 3
    )


def test_paired_authorization_is_single_use_and_wrong_eis_binding_fails(
    tmp_path: Path,
) -> None:
    eoc = tmp_path / "eoc.zip"
    eis = tmp_path / "eis.zip"
    eoc.write_bytes(b"paired EOC fixture")
    eis.write_bytes(b"paired EIS fixture")
    output = tmp_path / "paired-run"
    config_path = _write_paired_readiness_config(tmp_path)
    runner = config_path.parent.parent / "scripts" / "run_kit_dev_engineering.py"
    authorization = tmp_path / "paired-authorization.json"
    authorization.write_text(
        json.dumps(_paired_authorization_payload({key: "0" * 64 for key in PAIRED_BINDING_KEYS}))
    )
    bindings, _, _ = prepare_paired_run_bindings(
        eoc_zip=eoc,
        eis_zip=eis,
        output_dir=output,
        config_path=config_path,
        authorization_path=authorization,
        runner_path=runner,
    )
    now = time.time()
    authorization.write_text(json.dumps(_paired_authorization_payload(bindings, now=now)))
    receipt = consume_paired_authorization(
        authorization,
        expected_bindings=bindings,
        output_dir=output,
        now_unix_s=now,
    )
    assert receipt.is_file()
    with pytest.raises(FileExistsError):
        consume_paired_authorization(
            authorization,
            expected_bindings=bindings,
            output_dir=output,
            now_unix_s=now,
        )

    wrong = dict(bindings)
    wrong["eis_sha256"] = "f" * 64
    wrong_authorization = tmp_path / "wrong-eis-authorization.json"
    wrong_authorization.write_text(
        json.dumps(
            _paired_authorization_payload(
                bindings,
                nonce="b" * 64,
                now=now,
            )
        )
    )
    with pytest.raises(PairedAuthorizationError, match="eis_sha256"):
        consume_paired_authorization(
            wrong_authorization,
            expected_bindings=wrong,
            output_dir=output,
            now_unix_s=now,
        )


@pytest.mark.parametrize(
    "binding_key",
    [
        "readiness_sha256",
        "paired_designs_sha256",
        "paired_cohort_sha256",
        "trial_keys_sha256",
        "runner_sha256",
        "source_tree_sha256",
        "command_sha256",
        "cwd_sha256",
    ],
)
def test_paired_authorization_rejects_frozen_identity_drift(
    tmp_path: Path, binding_key: str
) -> None:
    now = time.time()
    bindings = {key: "1" * 64 for key in PAIRED_BINDING_KEYS}
    authorization = tmp_path / f"{binding_key}.authorization.json"
    authorization.write_text(json.dumps(_paired_authorization_payload(bindings, now=now)))
    observed = dict(bindings)
    observed[binding_key] = "2" * 64
    with pytest.raises(PairedAuthorizationError, match=binding_key):
        consume_paired_authorization(
            authorization,
            expected_bindings=observed,
            output_dir=tmp_path / "run",
            now_unix_s=now,
        )


def test_paired_authorization_rejects_expired_decision(tmp_path: Path) -> None:
    now = time.time()
    bindings = {key: "1" * 64 for key in PAIRED_BINDING_KEYS}
    payload = _paired_authorization_payload(bindings, now=now - 1_000)
    authorization = tmp_path / "expired.authorization.json"
    authorization.write_text(json.dumps(payload))
    with pytest.raises(PairedAuthorizationError, match="expired"):
        consume_paired_authorization(
            authorization,
            expected_bindings=bindings,
            output_dir=tmp_path / "run",
            now_unix_s=now,
        )


@pytest.mark.parametrize("poison", ["missing_receipt", "receipt_tamper", "auth_tamper", "output"])
def test_consumed_paired_authorization_fails_closed_on_evidence_drift(
    tmp_path: Path, poison: str
) -> None:
    now = time.time()
    bindings = {key: "1" * 64 for key in PAIRED_BINDING_KEYS}
    authorization = tmp_path / "paired.authorization.json"
    authorization.write_text(json.dumps(_paired_authorization_payload(bindings, now=now)))
    output = tmp_path / "run"
    receipt = consume_paired_authorization(
        authorization,
        expected_bindings=bindings,
        output_dir=output,
        now_unix_s=now,
    )
    observed_output = output
    if poison == "missing_receipt":
        receipt.unlink()
    elif poison == "receipt_tamper":
        payload = json.loads(receipt.read_text())
        payload["authorization_sha256"] = "2" * 64
        receipt.write_text(json.dumps(payload))
    elif poison == "auth_tamper":
        payload = json.loads(authorization.read_text())
        payload["authorization_id"] = "tampered-review"
        authorization.write_text(json.dumps(payload))
    else:
        observed_output = tmp_path / "other-run"
    with pytest.raises((PairedAuthorizationError, FileNotFoundError)):
        validate_consumed_paired_authorization(
            authorization,
            receipt,
            expected_bindings=bindings,
            output_dir=observed_output,
            now_unix_s=now,
        )


def test_supervisor_rejects_preexisting_empty_output_root_atomically(tmp_path: Path) -> None:
    controller = _StartFailureController()
    supervisor = _supervisor(tmp_path, controller)
    supervisor.output_dir.mkdir(mode=0o700)
    with pytest.raises(SupervisorError, match="must not already exist"):
        supervisor.run()
    assert controller.argv is None


def test_failed_finalization_has_exactly_one_failure_outcome(tmp_path: Path) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(output=output, immediate_exit=2)
    assert _supervisor(tmp_path, controller).run() == 2
    assert (output / "failure.json").is_file()
    assert not (output / "terminal.json").exists()
    assert (output / "final_manifest.json").is_file()


@pytest.mark.parametrize(
    ("extra_kind", "error_prefix"),
    [
        ("regular", "SupervisorError: scientific staging does not contain"),
        ("symlink", "SupervisorError: scientific artifact is not a regular file"),
        ("special", "SupervisorError: scientific artifact is not a regular file"),
    ],
)
def test_extra_symlink_and_special_artifacts_are_rejected(
    tmp_path: Path, extra_kind: str, error_prefix: str
) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=0,
        create_scientific=True,
        extra_kind=extra_kind,
    )
    assert _supervisor(tmp_path, controller).run() == 3
    failure = json.loads((output / "failure.json").read_text())
    assert str(failure["validation_error"]).startswith(error_prefix)
    manifest = json.loads((output / "final_manifest.json").read_text())
    assert manifest["artifact_validation_passed"] is False


@pytest.mark.parametrize(
    ("extra_kind", "message"),
    [
        ("arbitrary_text", "invalid JSON artifact"),
        ("bad_claim_flag", "exact false boolean formal_model_result"),
        ("bad_artifact_paths", "artifact_paths must exactly match"),
        ("bad_planned_fits", "planned_fits must be an integer"),
        ("unknown_summary_field", "unknown=['unreviewed']"),
    ],
)
def test_scientific_content_is_validated_before_promotion(
    tmp_path: Path, extra_kind: str, message: str
) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=0,
        create_scientific=True,
        extra_kind=extra_kind,
    )
    assert _supervisor(tmp_path, controller).run() == 3
    failure = json.loads((output / "failure.json").read_text())
    assert message in str(failure["validation_error"])
    assert not any((output / name).exists() for name in SCIENTIFIC_ARTIFACTS)


def test_valid_scientific_staging_is_sibling_then_promoted_and_removed(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=0,
        create_scientific=True,
    )
    assert _supervisor(tmp_path, controller).run() == 0
    assert controller.scientific_output is not None
    assert controller.scientific_output.parent == output.parent
    assert output not in controller.scientific_output.parents
    assert not controller.scientific_output.exists()
    assert all((output / name).is_file() for name in SCIENTIFIC_ARTIFACTS)
    manifest = json.loads((output / "final_manifest.json").read_text())
    expected = {
        "start_manifest.json",
        "run_status.json",
        "lifecycle.jsonl",
        "telemetry.jsonl",
        "terminal.json",
        *SCIENTIFIC_ARTIFACTS,
    }
    assert set(manifest["artifacts_sha256"]) == expected
    assert manifest["artifact_validation_passed"] is True


@pytest.mark.parametrize(
    "parent_tamper",
    [
        "start_manifest",
        "run_status",
        "lifecycle",
        "lifecycle_symlink",
        "telemetry",
        "terminal_collision",
    ],
)
def test_parent_evidence_tamper_or_collision_yields_exactly_one_failure(
    tmp_path: Path, parent_tamper: str
) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=0,
        create_scientific=True,
        parent_tamper=parent_tamper,
    )
    assert _supervisor(tmp_path, controller).run() == 3
    assert (output / "failure.json").is_file()
    assert not (output / "terminal.json").exists()
    assert (output / "final_manifest.json").is_file()
    manifest = json.loads((output / "final_manifest.json").read_text())
    assert manifest["artifact_validation_passed"] is False
    assert manifest["validation_error"]
    assert controller.scientific_output is not None
    assert not controller.scientific_output.exists()


def test_run_status_tamper_during_process_start_is_not_overwritten_by_running(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=0,
        create_scientific=True,
        parent_tamper="run_status_during_start",
    )
    assert _supervisor(tmp_path, controller).run() == 3
    assert (output / "failure.json").is_file()
    assert (output / "final_manifest.json").is_file()
    assert not (output / "terminal.json").exists()
    assert not any((output / name).exists() for name in SCIENTIFIC_ARTIFACTS)
    manifest = json.loads((output / "final_manifest.json").read_text())
    assert manifest["artifact_validation_passed"] is False
    assert "run_status.json bytes changed" in str(manifest["validation_error"])


def test_explicit_heartbeats_prevent_cpu_independent_stale_trigger(tmp_path: Path) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=2,
        exit_after_polls=4,
        cpu_step=0.0,
        heartbeat_messages=True,
    )
    limits = SupervisorLimits(
        telemetry_interval_s=5,
        stale_s=10,
        soft_drain_s=20,
        terminate_s=30,
        kill_s=40,
    )
    assert _supervisor(tmp_path, controller, limits=limits).run() == 2
    assert controller.signals == []


@pytest.mark.parametrize(
    "progress_stage", ["fit_start", "fit_started", "fit_complete", "engine_complete"]
)
def test_nonheartbeat_progress_never_refreshes_stale_clock(
    tmp_path: Path, progress_stage: str
) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(output=output, progress_stage=progress_stage)
    limits = SupervisorLimits(
        telemetry_interval_s=5,
        stale_s=10,
        soft_drain_s=20,
        terminate_s=30,
        kill_s=40,
    )
    assert _supervisor(tmp_path, controller, limits=limits).run() == 124
    failure = json.loads((output / "failure.json").read_text())
    assert failure["reason"] == "stale_heartbeat"


def test_worker_progress_can_emit_explicit_heartbeat_without_sleep() -> None:
    emitted: list[dict[str, object]] = []
    channel = WorkerProgress({"requested": False}, emit=emitted.append)
    channel.emit_heartbeat()
    assert emitted == [{"stage": "heartbeat"}]


def test_cleanup_failure_is_bounded_and_fail_closed(tmp_path: Path) -> None:
    clock = _FakeClock()
    output = tmp_path / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=0,
        create_scientific=True,
        lingering_after_exit=True,
        unkillable_group=True,
    )
    limits = SupervisorLimits(
        cleanup_poll_s=0.1,
        cleanup_term_grace_s=0.2,
        cleanup_kill_grace_s=0.2,
    )
    assert _supervisor(tmp_path, controller, clock=clock, limits=limits).run() == 124
    assert controller.signals == [signal.SIGTERM, signal.SIGKILL]
    assert clock.current < 1.0
    failure = json.loads((output / "failure.json").read_text())
    assert failure["reason"] == "process_group_cleanup_failed"


def test_lingering_process_group_is_terminated_and_killed_after_leader_exit(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    controller = _DeterministicController(
        output=output,
        immediate_exit=0,
        create_scientific=True,
        lingering_after_exit=True,
    )
    assert _supervisor(tmp_path, controller).run() == 0
    assert controller.signals == [signal.SIGTERM, signal.SIGKILL]
    assert (output / "terminal.json").is_file()
    assert not (output / "failure.json").exists()


def test_real_supervised_subprocess_without_authorization_fails_closed_before_engine(
    tmp_path: Path,
) -> None:
    eoc = tmp_path / "not-a-zip-eoc.zip"
    eis = tmp_path / "not-a-zip-eis.zip"
    eoc.write_bytes(b"synthetic invalid archive")
    eis.write_bytes(b"synthetic invalid archive")
    output = tmp_path / "real-run"
    repository = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(repository / "scripts" / "run_kit_dev_engineering.py"),
            "--eoc-zip",
            str(eoc),
            "--eis-zip",
            str(eis),
            "--output-dir",
            str(output),
            "--config-path",
            str(repository / "config" / "kit_f1_o2_development_v2.json"),
            "--authorization-path",
            str(tmp_path / "missing-authorization.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 3
    assert not output.exists()
