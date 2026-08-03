#!/usr/bin/env python3
"""Launch the KIT development-only engine under the fail-closed parent supervisor."""

from __future__ import annotations

import argparse
from pathlib import Path

from battery_eis_soh.kit_dev_worker import Supervisor, SupervisorError, WorkerContractError


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Supervised KIT development-only run; never a formal or protected result."
    )
    parser.add_argument("--eoc-zip", type=Path, required=True)
    parser.add_argument("--eis-zip", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument(
        "--authorization-path",
        type=Path,
        required=True,
        help="External one-use paired-run authorization reviewed outside this runner.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        return Supervisor(
            eoc_zip=args.eoc_zip,
            eis_zip=args.eis_zip,
            output_dir=args.output_dir,
            config_path=args.config_path,
            authorization_path=args.authorization_path,
            runner_path=Path(__file__).resolve(),
        ).run()
    except (SupervisorError, WorkerContractError, OSError, ValueError):
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
