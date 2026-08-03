"""Build commit-safe UI projections from the read-only KIT paired development run.

This script never edits the governed run directory. It exports only compact replay fields and
aggregate development-OOF metrics. Raw source data, authorization grants and receipts are never
copied. Every export remains non-formal, non-protected, unselected and non-publication-ready.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "artifacts/local/paired-runs/kit-f1-paired-b-dcore-dev-v2-20260802T201053-cdt"
DEFAULT_OUTPUT = ROOT / "demo/fleet-replay/kit-development-oof.js"
DEFAULT_SUMMARY = ROOT / "demo/fleet-replay/data/kit-paired-development-summary.json"
DEFAULT_METRICS_CSV = ROOT / "demo/fleet-replay/data/kit-paired-development-metrics.csv"
DEFAULT_DELTAS_CSV = ROOT / "demo/fleet-replay/data/kit-paired-development-deltas.csv"

MODEL_KEYS = {
    "persistence_zero_loss": "persistence",
    "recent_slope_h3": "recentSlope",
    "elastic_net_regression": "elasticNet",
    "hist_gradient_boosting_regressor": "hgb",
}
FITTED_FAMILIES = {"elastic_net_regression", "hist_gradient_boosting_regressor"}
CLAIM_FIELDS = (
    "formal_model_result",
    "protected_test_result",
    "publication_ready",
    "scientific_result",
    "selection_performed",
    "resume_approved",
)
EXPECTED_COUNTS = {
    "cells": 228,
    "conditions": 76,
    "snapshots": 3999,
    "eligible_snapshots": 2919,
    "eligible_cells": 180,
    "predictions": 23352,
    "fits": 54,
    "metrics": 24,
    "deltas": 12,
    "retry_sensitivity": 6,
}
PAIRED_RUN_KIND = "KIT_F1_PAIRED_B_D_CORE_DEVELOPMENT_V2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path.name} must contain a JSON object")
    return value


def _round(value: Any) -> Any:
    return round(float(value), 6) if value is not None else None


def _canonical_json(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
    )


def _validate_governed_run(run_dir: Path) -> tuple[dict[str, Any], ...]:
    final = _read_json(run_dir / "final_manifest.json")
    terminal = _read_json(run_dir / "terminal.json")
    start = _read_json(run_dir / "start_manifest.json")
    summary = _read_json(run_dir / "engine_summary.json")
    deltas = _read_json(run_dir / "paired_deltas.json")
    retry = _read_json(run_dir / "retry_sensitivity.json")

    if terminal.get("status") != "completed" or terminal.get("exit_code") != 0:
        raise ValueError("KIT paired development run is not completed successfully")
    if final.get("artifact_validation_passed") is not True or final.get("exit_code") != 0:
        raise ValueError("KIT paired development artifact validation did not pass")
    for document in (final, summary, deltas, retry):
        if any(document[field] is not False for field in CLAIM_FIELDS if field in document):
            raise ValueError("derived data must retain every negative scientific/publication flag")
    if any(summary.get(field) is not False for field in CLAIM_FIELDS):
        raise ValueError("engine summary must carry every negative scientific/publication flag")
    if start.get("run_kind") != PAIRED_RUN_KIND:
        raise ValueError("unexpected paired start-manifest run kind")
    if summary.get("completed_fits") != EXPECTED_COUNTS["fits"]:
        raise ValueError("expected exactly 54 completed paired-development fits")
    if summary.get("o2_results") != []:
        raise ValueError("O2 output must remain empty for this display")
    if len(summary.get("metrics", [])) != EXPECTED_COUNTS["metrics"]:
        raise ValueError("expected exactly 24 metric identities")
    if len(deltas.get("paired_deltas", [])) != EXPECTED_COUNTS["deltas"]:
        raise ValueError("expected exactly 12 paired-delta identities")
    if len(retry.get("results", [])) != EXPECTED_COUNTS["retry_sensitivity"]:
        raise ValueError("expected exactly 6 retry-sensitivity identities")

    expected_hashes = final.get("artifacts_sha256", {})
    for name in (
        "engine_summary.json",
        "fold_registry.json",
        "oof_predictions.jsonl",
        "paired_deltas.json",
        "retry_sensitivity.json",
        "snapshot_manifest.jsonl",
    ):
        if expected_hashes.get(name) != _sha256(run_dir / name):
            raise ValueError(f"governed artifact hash mismatch: {name}")
    return final, terminal, start, summary, deltas, retry


def _compact_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    b_values = row["b_values"]
    elapsed_days = b_values[6] if isinstance(b_values, list) and b_values[6] is not None else None
    q0_throughput = b_values[7] if isinstance(b_values, list) and b_values[7] is not None else None
    return {
        "i": row["checkup_index"],
        "t": row["timestamp_s"],
        "q": _round(row["capacity_ah"]),
        "s": _round(row["soh_percent"]),
        "d": _round(elapsed_days),
        "x": _round(q0_throughput),
        "status": row["eligibility_status"],
        "h3i": row["h3_checkup_index"],
        "h3s": _round(row["future_soh_percent"]),
        "h3loss": _round(row["soh_loss_pp_h3"]),
        "pred": {},
    }


def _metric_row(row: dict[str, Any]) -> dict[str, Any]:
    values = row["metrics"]
    return {
        "mode": row["mode"],
        "policy": row["acquisition_policy"],
        "view": row["view"],
        "family": row["model_family"],
        "familyKey": MODEL_KEYS[row["model_family"]],
        "role": row["model_role"],
        "pairedDesignSha256": row["paired_design_sha256"],
        "mae": _round(values["condition_balanced_mae_pp_v1"]),
        "medianAe": _round(values["condition_balanced_median_ae_pp_v1"]),
        "rmse": _round(values["condition_balanced_rmse_pp_v1"]),
        "bias": _round(values["condition_balanced_signed_bias_pp_v1"]),
        "r2": _round(values["condition_balanced_r2_v1"]),
        "snapshots": values["snapshots"],
        "cells": values["cells"],
        "conditions": values["conditions"],
        "negativeLossSnapshots": values["negative_loss_snapshots"],
    }


def _delta_interpretation(row: dict[str, Any]) -> str:
    mode = row["mode"]
    family = row["model_family"]
    if mode == "cyclic" and family == "hist_gradient_boosting_regressor":
        return "improvement_signal"
    if mode == "cyclic" and family == "elastic_net_regression":
        return "worsening_signal"
    if mode == "profile" and family == "hist_gradient_boosting_regressor":
        return "mixed_rmse_unclear_mae_worse"
    if mode == "profile" and family == "elastic_net_regression":
        return "no_clear_change_point_better"
    if mode == "calendar" and family == "elastic_net_regression":
        return "no_clear_change_point_worse"
    return "no_clear_change"


def _delta_row(row: dict[str, Any]) -> dict[str, Any]:
    bootstrap = row["bootstrap"]
    return {
        "mode": row["mode"],
        "policy": row["acquisition_policy"],
        "family": row["model_family"],
        "familyKey": MODEL_KEYS[row["model_family"]],
        "maeDelta": _round(row["mae_delta_d_core_minus_b_pp"]),
        "medianAeDelta": _round(row["median_ae_delta_d_core_minus_b_pp"]),
        "rmseDelta": _round(row["rmse_delta_d_core_minus_b_pp"]),
        "biasDelta": _round(row["signed_bias_delta_d_core_minus_b_pp"]),
        "r2Delta": _round(row["r2_delta_d_core_minus_b"]),
        "maeCi95": [_round(value) for value in bootstrap["mae_delta_percentile_95"]],
        "rmseCi95": [_round(value) for value in bootstrap["rmse_delta_percentile_95"]],
        "draws": bootstrap["draws"],
        "validDraws": bootstrap["valid_draws"],
        "minimumValidDraws": bootstrap["minimum_valid_draws"],
        "seed": bootstrap["seed"],
        "method": bootstrap["method"],
        "sameSnapshotsAndWeights": row["same_snapshot_ids_and_weights"],
        "interpretation": _delta_interpretation(row),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: _canonical_json(value) if isinstance(value, (list, dict)) else value
                    for key, value in row.items()
                }
            )


def build(
    run_dir: Path,
    output: Path,
    summary_output: Path,
    metrics_csv: Path,
    deltas_csv: Path,
) -> None:
    final, terminal, start, summary, deltas, retry = _validate_governed_run(run_dir)

    cells: dict[str, dict[str, Any]] = {}
    snapshots_by_id: dict[str, dict[str, Any]] = {}
    with (run_dir / "snapshot_manifest.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            cell_id = row["cell_id"]
            match = re.match(r"^P(\d{3})_(\d)_", cell_id)
            if not match:
                raise ValueError(f"unrecognized KIT cell ID: {cell_id}")
            cell = cells.setdefault(
                cell_id,
                {
                    "id": cell_id,
                    "condition": row["condition_id"],
                    "replicate": int(match.group(2)),
                    "mode": row["mode"],
                    "q0": _round(row["q0_ah"]),
                    "snapshots": [],
                },
            )
            snapshot = _compact_snapshot(row)
            cell["snapshots"].append(snapshot)
            snapshots_by_id[row["snapshot_id"]] = snapshot

    prediction_identities: set[tuple[str, str, str, str, str]] = set()
    prediction_rows = 0
    with (run_dir / "oof_predictions.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            family_key = MODEL_KEYS[row["model_family"]]
            identity = (
                row["snapshot_id"],
                row["acquisition_policy"],
                row["view"],
                row["model_family"],
                row["fit_id"],
            )
            if identity in prediction_identities:
                raise ValueError("duplicate composite OOF prediction identity")
            prediction_identities.add(identity)
            matched = snapshots_by_id.get(row["snapshot_id"])
            if matched is None:
                raise ValueError("OOF prediction references an unknown snapshot")
            policy = matched["pred"].setdefault(row["acquisition_policy"], {})
            view = policy.setdefault(row["view"], {})
            if family_key in view:
                raise ValueError("composite prediction key collision")
            view[family_key] = {
                "loss": _round(row["predicted_soh_loss_pp_h3"]),
                "future": _round(row["predicted_future_soh_percent"]),
                "fold": row["outer_fold"],
                "weight": _round(row["outer_validation_weight_mean_one"]),
                "fitId": row["fit_id"],
                "design": row["paired_design_sha256"],
            }
            prediction_rows += 1

    if len(cells) != EXPECTED_COUNTS["cells"]:
        raise ValueError(f"expected 228 KIT cells, found {len(cells)}")
    if len({cell["condition"] for cell in cells.values()}) != EXPECTED_COUNTS["conditions"]:
        raise ValueError("expected 76 KIT conditions")
    if sum(len(cell["snapshots"]) for cell in cells.values()) != EXPECTED_COUNTS["snapshots"]:
        raise ValueError("expected 3,999 KIT capacity snapshots")
    if prediction_rows != EXPECTED_COUNTS["predictions"]:
        raise ValueError("expected exactly 23,352 OOF prediction rows")

    for cell in cells.values():
        cell["snapshots"].sort(key=lambda snapshot: snapshot["i"])
        q0_timestamp = cell["snapshots"][0]["t"]
        for snapshot in cell["snapshots"]:
            if snapshot["d"] is None:
                snapshot["d"] = _round((snapshot["t"] - q0_timestamp) / 86_400.0)

    metric_rows = sorted(
        (_metric_row(row) for row in summary["metrics"]),
        key=lambda row: (row["mode"], row["policy"], row["view"], row["family"]),
    )
    delta_rows = sorted(
        (_delta_row(row) for row in deltas["paired_deltas"]),
        key=lambda row: (row["mode"], row["policy"], row["family"]),
    )
    retry_rows = sorted(
        retry["results"],
        key=lambda row: (row["mode"], row["model_family"]),
    )
    eligible_cells = {
        cell["id"]
        for cell in cells.values()
        if any(snapshot["status"] == "eligible_f1_and_d_core" for snapshot in cell["snapshots"])
    }
    if len(eligible_cells) != EXPECTED_COUNTS["eligible_cells"]:
        raise ValueError("expected exactly 180 paired-eligible cells")

    governed_hashes = {
        "finalManifest": _sha256(run_dir / "final_manifest.json"),
        "engineSummary": _sha256(run_dir / "engine_summary.json"),
        "foldRegistry": _sha256(run_dir / "fold_registry.json"),
        "oofPredictions": _sha256(run_dir / "oof_predictions.jsonl"),
        "pairedDeltas": _sha256(run_dir / "paired_deltas.json"),
        "retrySensitivity": _sha256(run_dir / "retry_sensitivity.json"),
        "snapshotManifest": _sha256(run_dir / "snapshot_manifest.jsonl"),
        "config": summary["config_sha256"],
        "schema": summary["artifact_schema_sha256"],
        "eoc": summary["source_archives"]["eoc"]["sha256"],
        "eis": summary["source_archives"]["eis"]["sha256"],
    }
    metadata = {
        "schemaVersion": "kit_paired_development_projection_v1",
        "artifactSchema": summary["artifact_schema_version"],
        "sourceRunId": run_dir.name,
        "evidenceMode": "KIT condition-held-out DEVELOPMENT OOF",
        "runKind": start["run_kind"],
        "runStatus": terminal["status"],
        "artifactValidationPassed": final["artifact_validation_passed"],
        "rosterCount": len(cells),
        "conditionCount": EXPECTED_COUNTS["conditions"],
        "cellsWithPrediction": len(eligible_cells),
        "cellsWithoutPrediction": len(cells) - len(eligible_cells),
        "snapshotCount": EXPECTED_COUNTS["snapshots"],
        "eligibleSnapshotCount": EXPECTED_COUNTS["eligible_snapshots"],
        "predictionRowsShown": prediction_rows,
        "completedFits": summary["completed_fits"],
        "metricRows": len(metric_rows),
        "deltaRows": len(delta_rows),
        "retryRows": len(retry_rows),
        "featureViews": ["B", "D_CORE"],
        "horizon": 3,
        "target": "soh_loss_pp_h3",
        "targetUnit": "SoH percentage points",
        "selectionPerformed": False,
        "formalModelResult": False,
        "scientificResult": False,
        "protectedTestResult": False,
        "publicationReady": False,
        "resumeApproved": False,
        "deploymentReady": False,
        "timestampSemanticsProvisional": summary["timestamp_semantics_provisional"],
        "retrySemanticsProvisional": summary["retry_semantics_provisional"],
        "sourceHashes": governed_hashes,
    }
    limitations = [
        "Development OOF only; no protected or external test has been opened.",
        "No model selection, champion promotion, publication approval or deployment approval.",
        "B versus D_CORE is an association-focused paired diagnostic, not a causal EIS claim.",
        "The 2,000 bootstrap draws resample whole conditions; they are not new independent cells.",
        "No O2 classifier, failure, thermal-runaway, RUL or replacement decision is reported.",
        "Timestamp and retry semantics remain provisional.",
    ]
    forbidden_claims = [
        "EIS universally improves prediction",
        "a universal winning model",
        "protected, external, final, publication-ready or production-ready evidence",
        "causal EIS benefit",
        "failure, thermal-runaway, RUL, replacement timing or cost savings",
    ]
    compact_summary = {
        "metadata": metadata,
        "metrics": metric_rows,
        "pairedDeltas": delta_rows,
        "retrySensitivity": retry_rows,
        "limitations": limitations,
        "forbiddenClaims": forbidden_claims,
    }
    sorted_cell_ids = sorted(
        cells,
        key=lambda cell_id: (
            int(cells[cell_id]["condition"]),
            int(cells[cell_id]["replicate"]),
        ),
    )
    payload = {
        **compact_summary,
        "cells": [cells[cell_id] for cell_id in sorted_cell_ids],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "// Generated by scripts/build_fleet_replay_data.py; do not hand-edit.\n"
        f"export const kitDevelopmentOof = Object.freeze({_canonical_json(payload)});\n",
        encoding="utf-8",
    )
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(_canonical_json(compact_summary, pretty=True), encoding="utf-8")
    _write_csv(metrics_csv, metric_rows)
    _write_csv(deltas_csv, delta_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--metrics-csv", type=Path, default=DEFAULT_METRICS_CSV)
    parser.add_argument("--deltas-csv", type=Path, default=DEFAULT_DELTAS_CSV)
    args = parser.parse_args()
    build(
        args.run_dir.resolve(),
        args.output.resolve(),
        args.summary_output.resolve(),
        args.metrics_csv.resolve(),
        args.deltas_csv.resolve(),
    )


if __name__ == "__main__":
    main()
