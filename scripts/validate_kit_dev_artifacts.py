#!/usr/bin/env python3
"""Validate a completed KIT F1-v2 supervised terminal tree without executing training."""

from __future__ import annotations

import argparse
from pathlib import Path

from battery_eis_soh.kit_dev_worker import SupervisorError, validate_kit_f1_v2_artifact_tree


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate_kit_f1_v2_artifact_tree(
            args.run_dir,
            args.config_path,
            require_terminal_tree=True,
            enforce_frozen_official_counts=True,
        )
    except (SupervisorError, OSError, ValueError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
