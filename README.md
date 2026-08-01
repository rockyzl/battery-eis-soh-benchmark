# Battery EIS → SoH Benchmark

An independent, clean-room, cleaned-source benchmark based on the public Cambridge lithium-ion
battery impedance study. The project asks a narrow question:

> Can a model estimate a held-out cell's state of health from one state-V EIS spectrum, and does EIS
> add useful information beyond cycle number and the cell's measured initial capacity?

This is an independent portfolio project inspired by related battery-performance work in Lu Zhang's
scientific background. It is **not** the original Cambridge implementation and is not presented as
Argonne work.

## Why this benchmark exists

The source record contains more than 20,000 spectra, but they come from only 12 physical cells. A
row-random split would allow spectra from the same cell into training and test data and can produce an
overly optimistic result. This benchmark treats the cell—not the spectrum—as the independent group.

Version 2 is intentionally bounded:

- public CC BY 4.0 data from Zenodo record `3633835`;
- state V only: 15-minute rest after full charge;
- SoH percent as the primary fitted target, with absolute capacity and capacity loss retained as
  separately reported ablations;
- an explicit `Q0` ablation that measures the value of known cycle-0 capacity without allowing
  future-capacity leakage;
- original 25 °C train/test cell split plus grouped leave-one-cell-out evaluation;
- mean, Ridge, PLS and preregistered GPR-ARD candidates;
- immutable prediction rows, per-cell metrics and provenance manifests.

RUL prediction, NASA/CALCE data, dashboards, APIs and agents are not part of v1.

## Current status

Data-source provenance and the clean-room parser are implemented. Baseline evaluation is an
engineering result until the protocol is frozen, independently reviewed and executed from a clean
commit. No metric is resume-approved yet.

CI uses synthetic fixtures and does not download the approximately 119 MB public archives. A formal
local data gate must run the checksum-verified download plus `audit`, which checks the 12-cell source
inventory and 60-frequency spectra.

## Local setup

```bash
uv sync --extra dev
uv run python scripts/fetch_public_data.py
uv run battery-eis-soh selection-audit --data-root "data/raw/extracted"
uv run battery-eis-soh full-audit \
  --data-root "data/raw/extracted" \
  --acknowledge-test-label-exposure
uv run battery-eis-soh engineering-evaluate \
  --data-root "data/raw/extracted" \
  --task-id T0 \
  --row-scope all_valid \
  --output artifacts/soh-primary-smoke \
  --acknowledge-test-label-exposure

uv run battery-eis-soh engineering-evaluate \
  --data-root "data/raw/extracted" \
  --task-id T2 \
  --row-scope all_valid \
  --output artifacts/q0-capacity-ablation-smoke \
  --acknowledge-test-label-exposure
```

This command is an engineering smoke and currently loads the historical fixed-test labels. It is not
the formal nested-selection runner and cannot produce a scientific or resume-approved result.
The separate `selection-audit` path opens only `25C01`–`25C04`; its tests prove that fixed-test
capacity and `Q0` files are not parsed.

Download instructions and official checksums are in [`docs/data-card.md`](docs/data-card.md).

## Claim boundary

Allowed after independent verification:

> Built the public-data ingestion, audit and engineering evaluation pipeline for an independent,
> leakage-resistant Li-ion EIS/capacity benchmark with cell-held-out validation.

Not allowed:

- claiming authorship of the original dataset or paper;
- describing this as work performed at Argonne;
- quoting a metric that is not linked to a reviewed immutable artifact;
- treating thousands of spectra as thousands of independent batteries.
