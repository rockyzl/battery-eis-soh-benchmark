#!/usr/bin/env python3
"""Write the local-only KIT F1/O2 target-readiness audit."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from battery_eis_soh.kit_target_readiness import audit_target_readiness
from battery_eis_soh.kit_v2 import load_kit_v2_development_records


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only KIT F1/O2 target-readiness audit")
    parser.add_argument("--eoc-zip", type=Path, required=True)
    parser.add_argument("--eis-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _safe_output(path: Path) -> Path:
    repository_root = Path(__file__).resolve().parents[1]
    output = path.expanduser().resolve()
    try:
        relative = output.relative_to(repository_root)
    except ValueError:
        return output
    if not relative.parts or relative.parts[0] != "artifacts":
        raise ValueError("repository-local audit output must be under ignored artifacts/")
    return output


def main() -> int:
    args = _parse_args()
    output = _safe_output(args.output)
    records = load_kit_v2_development_records(
        args.eoc_zip,
        args.eis_zip,
        expected_cells=228,
        expected_capacity_endpoints=3999,
        expected_valid_rt50=3993,
    )
    report = audit_target_readiness(records)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "official_source": {
            "kitopen": "https://publikationen.bibliothek.kit.edu/1000169196",
            "doi": "https://doi.org/10.35097/1969",
            "radar": "https://radar.kit.edu/radar/en/dataset/CoNQplSNoVeXExyV",
            "license": "CC BY 4.0",
            "datacite_reported_package": "333.4 MB application/x-tar",
            "accessed_at": "2026-08-01",
        },
        "audit": report,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "go_no_go": report["go_no_go"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
