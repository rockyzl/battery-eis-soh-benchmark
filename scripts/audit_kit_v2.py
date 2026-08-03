#!/usr/bin/env python3
"""Run the strict KIT v2 archive audit and write a local-only JSON report."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from battery_eis_soh.kit_v2 import RetryCandidatePolicy, audit_kit_v2

OFFICIAL_REFERENCE_COMMIT = "db5ec90cd2827824d11f74f95ed149b73acc6015"
OFFICIAL_REFERENCE_BASE = (
    f"https://github.com/energystatusdata/bat-age-data-scripts/blob/{OFFICIAL_REFERENCE_COMMIT}"
)
RETRY_EVIDENCE_ID = (
    f"{OFFICIAL_REFERENCE_BASE}/generate_log_age.py#L64;"
    f"{OFFICIAL_REFERENCE_BASE}/generate_log_age.py#L492-L505"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strictly audit explicit KIT v2 EOC/EIS archives without extracting them."
    )
    parser.add_argument("--eoc-zip", type=Path, required=True)
    parser.add_argument("--eis-zip", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSON output; paths inside this repository must be under ignored artifacts/",
    )
    parser.add_argument("--expected-cells", type=int, default=228)
    parser.add_argument("--expected-capacity-endpoints", type=int, default=3999)
    parser.add_argument("--expected-valid-rt50", type=int, default=3993)
    parser.add_argument(
        "--retry-window-s",
        type=float,
        default=60 * 60 * 60,
        help="Official processing code's checkup grouping gap; candidates are flagged, never dropped.",
    )
    return parser.parse_args()


def _safe_output(path: Path) -> Path:
    repository_root = Path(__file__).resolve().parents[1]
    output = path.expanduser().resolve()
    try:
        relative = output.relative_to(repository_root)
    except ValueError:
        return output
    if not relative.parts or relative.parts[0] != "artifacts":
        raise ValueError(
            "audit JSON inside the repository must be written under ignored artifacts/"
        )
    return output


def main() -> int:
    args = _parse_args()
    output = _safe_output(args.output)
    audit = audit_kit_v2(
        args.eoc_zip,
        args.eis_zip,
        expected_cells=args.expected_cells,
        expected_capacity_endpoints=args.expected_capacity_endpoints,
        expected_valid_rt50=args.expected_valid_rt50,
        retry_policy=RetryCandidatePolicy(
            max_gap_s=args.retry_window_s,
            evidence_id=RETRY_EVIDENCE_ID,
        ),
        # Deliberately omit a completion policy. Official code documents timestamp origin
        # and duration as separate fields but does not establish one universal snapshot-
        # completion interpretation for this audit's EOC/EIS join.
        snapshot_completion_policy=None,
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "audit": audit.to_dict(),
        "official_read_only_evidence": {
            "repository": "energystatusdata/bat-age-data-scripts",
            "commit": OFFICIAL_REFERENCE_COMMIT,
            "timestamp_semantics": [
                f"{OFFICIAL_REFERENCE_BASE}/config_main.py#L51-L59",
                f"{OFFICIAL_REFERENCE_BASE}/config_labels.py#L8-L10",
                f"{OFFICIAL_REFERENCE_BASE}/config_labels.py#L105-L123",
            ],
            "frequency_registry": f"{OFFICIAL_REFERENCE_BASE}/config_main.py#L359-L362",
            "retry_candidate_basis": RETRY_EVIDENCE_ID,
            "interpretation": (
                "The source is evidence for field meanings, a 29-point SD EIS registry, "
                "and a 60-hour checkup/retry heuristic. No source code was copied. Retry "
                "runs remain candidates; timestamp completion semantics remain blocked."
            ),
            "accessed_at": "2026-08-01",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"output": str(output), "counts": audit.counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
