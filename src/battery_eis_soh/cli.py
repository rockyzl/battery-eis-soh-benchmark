from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .dataset import load_selection_dataset, load_state_dataset
from .evaluation import evaluate_paper_split
from .tasks import load_task_registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Battery EIS-to-SoH benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)
    full_audit = subparsers.add_parser(
        "full-audit", help="audit all cells, including fixed-test labels and Q0"
    )
    full_audit.add_argument("--data-root", type=Path, required=True)
    full_audit.add_argument("--acknowledge-test-label-exposure", action="store_true")
    selection_audit = subparsers.add_parser(
        "selection-audit", help="audit only the four selection cells without opening test labels"
    )
    selection_audit.add_argument("--data-root", type=Path, required=True)
    evaluate = subparsers.add_parser(
        "engineering-evaluate",
        help="run a bounded smoke that intentionally exposes historical fixed-test labels",
    )
    evaluate.add_argument("--data-root", type=Path, required=True)
    evaluate.add_argument("--task-id", default="T0")
    evaluate.add_argument(
        "--models", default=None, help="comma-separated override; defaults to the task registry"
    )
    evaluate.add_argument("--row-scope", choices=("all_valid", "through_eol"), default="all_valid")
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--acknowledge-test-label-exposure", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "full-audit":
        if not args.acknowledge_test_label_exposure:
            raise SystemExit(
                "refusing full audit without --acknowledge-test-label-exposure; "
                "use selection-audit for the protected training view"
            )
        dataset = load_state_dataset(args.data_root)
        audit_result = asdict(dataset.audit) | {
            "rows": int(dataset.X.shape[0]),
            "features": int(dataset.X.shape[1]),
            "soh_min": float(dataset.y.min()),
            "soh_max": float(dataset.y.max()),
        }
        print(json.dumps(audit_result, indent=2, sort_keys=True))
        return
    if args.command == "selection-audit":
        dataset = load_selection_dataset(args.data_root)
        selection_result = asdict(dataset.audit) | {
            "rows": int(dataset.X.shape[0]),
            "features": int(dataset.X.shape[1]),
            "soh_min": float(dataset.y.min()),
            "soh_max": float(dataset.y.max()),
            "fixed_test_labels_opened": False,
        }
        print(json.dumps(selection_result, indent=2, sort_keys=True))
        return
    if not args.acknowledge_test_label_exposure:
        raise SystemExit(
            "refusing fixed-test evaluation without --acknowledge-test-label-exposure; "
            "this command is engineering-only"
        )
    project_root = Path(__file__).resolve().parents[2]
    registry = load_task_registry(project_root / "config/target_registry.json")
    if args.task_id not in registry:
        raise SystemExit(f"unregistered task ID: {args.task_id}")
    task = registry[args.task_id]
    models = (
        list(task.allowed_smoke_models)
        if args.models is None
        else [name.strip() for name in args.models.split(",") if name.strip()]
    )
    invalid_models = sorted(set(models) - set(task.allowed_smoke_models))
    if invalid_models:
        raise SystemExit(f"models not allowed for task {args.task_id}: {invalid_models}")
    dataset = load_state_dataset(args.data_root)
    evaluation_result = evaluate_paper_split(
        dataset,
        models,
        args.output,
        project_root,
        task_id=args.task_id,
        row_scope=args.row_scope,
    )
    print(json.dumps(evaluation_result.metrics, indent=2, sort_keys=True))
