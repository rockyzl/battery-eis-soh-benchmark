from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .dataset import load_state_dataset
from .evaluation import evaluate_paper_split


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Battery EIS-to-SoH benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit", help="audit and join the public source data")
    audit.add_argument("--data-root", type=Path, required=True)
    evaluate = subparsers.add_parser("evaluate", help="run a bounded fixed-split engineering smoke")
    evaluate.add_argument("--data-root", type=Path, required=True)
    evaluate.add_argument("--models", default="mean,cycle,ridge,pls")
    evaluate.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    dataset = load_state_dataset(args.data_root)
    if args.command == "audit":
        result = asdict(dataset.audit) | {
            "rows": int(dataset.X.shape[0]),
            "features": int(dataset.X.shape[1]),
            "soh_min": float(dataset.y.min()),
            "soh_max": float(dataset.y.max()),
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    project_root = Path(__file__).resolve().parents[2]
    models = [name.strip() for name in args.models.split(",") if name.strip()]
    result = evaluate_paper_split(dataset, models, args.output, project_root)
    print(json.dumps(result.metrics, indent=2, sort_keys=True))
