# Battery Degradation Early Warning and Maintenance Decision System

This repository is being extended into an independent, public-data benchmark for a practical battery
question:

> From the measurements available at a completed battery check-up, can we identify cells whose
> degradation is becoming abnormal, estimate their future capacity trajectory or remaining life under
> a declared use scenario, and recommend a reviewable maintenance action?

The current development track uses the public KIT battery-ageing dataset. It is not a sudden-failure
or thermal-runaway predictor. The available evidence supports research on capacity fade, impedance
growth, accelerated degradation and capacity-defined end of life.

## What the system is intended to show

At one diagnostic check-up, the eventual product view should present four connected outputs:

1. **Risk:** whether the cell looks normal, uncertain or unusually degraded relative to appropriate
   peers.
2. **Future:** a capacity trajectory and conditional remaining-life estimate for a clearly stated
   future-use scenario.
3. **Evidence:** the capacity history, EIS changes, pulse/temperature/usage context and limitations
   supporting the result.
4. **Action:** deterministic decision support such as continue, shorten the check interval, retest,
   derate/inspect or plan service/replacement. A person confirms operational decisions.

The official KIT result archive has been recovered and checksum-bound. The current authoritative
milestone is a paired **B versus D_CORE development-OOF run**. `B` uses capacity history and operating
context; `D_CORE` adds current-check-up EIS features while keeping the same snapshots, held-out
conditions, model family and weights.

The reviewed run covers 228 real cells and 3,999 capacity endpoints. Exactly 2,919 endpoints from 180
cells have the required history, paired EIS view and exact third-follow-up (`H3`) truth. The run
completed 54/54 planned fits and produced 23,352 OOF prediction rows, 24 aggregate metric rows, 12
paired deltas and six retry-sensitivity records. The result is deliberately mixed: cyclic HGB shows
an EIS-associated improvement signal, cyclic ElasticNet shows worsening, calendar is unclear, and
profile is mixed or uncertain. There is no universal winner and no causal claim.

This is still development evidence only: `selection_performed=false`,
`protected_test_result=false`, `publication_ready=false` and deployment approval is absent. No
failure, thermal-runaway, RUL or maintenance rule has been established.

## Start here

- **Current evidence and next work:**
  [`docs/live-early-warning-tasks.md`](docs/live-early-warning-tasks.md)
- **Plain-language research report and complete metrics:**
  [`docs/reports/battery-early-warning-project-report-zh.md`](docs/reports/battery-early-warning-project-report-zh.md)
- **Independent post-run review:**
  `artifacts/local/governance/kit-f1-b-capacity-sequence-v1-postrun-independent-review-20260802.md`
  (local, intentionally ignored governance evidence)
- **Interactive 228-cell replay:**
  [`docs/run-fleet-replay-ui.md`](docs/run-fleet-replay-ui.md)
- **Frozen target and evaluation protocol:**
  [`docs/early-warning-protocol.md`](docs/early-warning-protocol.md)

## Why KIT is the new main track

The KIT study reports 228 commercial NMC/C+SiO cells across 76 operating conditions, with three
physical replicate cells per condition. It covers calendar ageing, controlled cyclic ageing and
dynamic driving-profile ageing. Repeated check-ups provide comparable capacity, EIS and pulse
measurements over cell life.

The data are hierarchical:

```text
physical cell
  -> repeated check-up
      -> EIS spectrum at a temperature and state of charge
          -> multiple frequency rows
```

Frequency rows and spectra are repeated observations, not independent batteries. Splits, uncertainty
and model comparisons must therefore be based on held-out physical cells and, where required, held-out
operating conditions. See [`docs/data-card-kit-v2.md`](docs/data-card-kit-v2.md).

The central scientific comparison is not merely whether EIS predicts current SoH. It is:

> Does EIS add useful early-warning information beyond measured initial capacity, capacity history,
> elapsed exposure and known operating conditions?

## Active project and frozen historical material

### Only active project: KIT degradation early warning

- product/science framing: risk, future trajectory/RUL, evidence and reviewable action;
- unit of independence: physical cell;
- prediction moment: a completed diagnostic check-up;
- candidate outcomes: near-horizon rapid degradation, threshold crossing and deviation from matched
  replicate cells;
- RUL is conditional on a declared future-use scenario;
- right-censored cells are retained;
- paired B/D_CORE condition-held-out development OOF is complete and independently reviewed for
  bounded display; no protected test, selection, publication approval or deployment approval has
  occurred.

The execution and review gates are in
[`docs/early-warning-runbook.md`](docs/early-warning-runbook.md), and live status is in
[`docs/live-early-warning-tasks.md`](docs/live-early-warning-tasks.md).

For a plain-language end-to-end explanation, including Data Description, EDA, preprocessing, feature
engineering, model selection, training/result artifact contracts and approvals, see
[`docs/process/early-warning-development-process.md`](docs/process/early-warning-development-process.md).
The research-seminar slide source is
[`docs/slides/battery-early-warning-research-seminar.md`](docs/slides/battery-early-warning-research-seminar.md).

The deterministic synthetic contract demo remains available for schema/policy explanation:

```bash
uv run battery-eis-soh early-warning-contract-demo
```

This command does not load KIT data or train a model. It constructs a synthetic `Q0` and four-check-up
trajectory, calculates conventional SoH at the prediction check-up, builds the next-three-check-up
capacity-threshold label and applies an explicitly provisional action policy whose output requires
human review. It emits negative scientific/publication/resume status flags, including
`formal_model_result=false`. Rapid-degradation,
conditional-RUL, peer-reference, grouped-split and metric contracts are covered by unit tests, but the
CLI does not execute them. The reviewed real-data paired OOF run is a separate governed lineage; this
synthetic command is not a shortcut to it.

### Frozen historical material: Cambridge EIS -> SoH benchmark

The existing clean-room Cambridge benchmark remains available as a separate historical lineage. It
asks whether one state-V EIS spectrum can estimate held-cell SoH, using 12 physical cells from Zenodo
record `3633835`. Its v2 protocol uses SoH percent, explicit cycle-0 capacity (`Q0`) ablations,
cell-held-out evaluation and a protected fixed test.

It is not an active fallback project. No new Cambridge or CALCE training, UI, report or publication
work is authorized. The accepted scope decision is recorded in
[`docs/decisions/kit-only-project-scope-2026-08-02.md`](docs/decisions/kit-only-project-scope-2026-08-02.md).

That work must not be relabeled as KIT early-warning evidence. Its engineering evaluator is useful for
plumbing checks only and cannot produce a scientific or resume-approved result.

## Local setup for the existing Cambridge pipeline

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
```

These commands operate on the Cambridge lineage, not the KIT early-warning track. KIT execution
commands will be documented only after the source audit and CLI contract are implemented and
reviewed.

## Source and claim boundaries

- KIT result data: <https://publikationen.bibliothek.kit.edu/1000169196>
- KIT data descriptor: <https://www.nature.com/articles/s41597-024-03831-x>
- KIT example scripts: <https://github.com/energystatusdata/bat-age-data-scripts>
- Cambridge source: <https://doi.org/10.5281/zenodo.3633835>

Both public datasets are reported as CC BY 4.0. Dataset files are not relicensed as project code.

Allowed now:

> Building a leakage-resistant, cell-grouped public-data pipeline for battery degradation early
> warning, conditional RUL research and auditable maintenance decision support.

Not allowed:

- claiming authorship of either source dataset or its original research;
- describing independent reconstruction as work performed at Argonne, KIT or Cambridge;
- calling spectra or frequency rows independent batteries;
- claiming thermal-runaway or sudden catastrophic-failure prediction;
- quoting a metric not tied to an independently reviewed immutable artifact;
- presenting a conditional RUL estimate or maintenance recommendation as experimental truth.
