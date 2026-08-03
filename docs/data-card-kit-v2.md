# Data card — KIT battery ageing result data, version 2

Source review date: 2026-08-01
Repository integration status: `strict parser/audit implemented; QA and timestamp-semantics review pending`

This card distinguishes facts stated by the official sources from counts reproduced by the local
strict audit. The audit establishes inventory and structure; it does not yet establish a
training-eligible EIS/capacity snapshot alignment.

## Authoritative sources

- KITopen record: <https://publikationen.bibliothek.kit.edu/1000169196>
- Dataset DOI: <https://doi.org/10.35097/1969>
- Data descriptor: <https://doi.org/10.1038/s41597-024-03831-x>
- Official example scripts: <https://github.com/energystatusdata/bat-age-data-scripts>
- License shown by KITopen: Creative Commons Attribution 4.0 International (`CC BY 4.0`)

Official title:

> *Comprehensive battery aging dataset: capacity and impedance fade measurements of a lithium-ion
> NMC/C-SiO cell [dataset — version 2: result data]*

Version 2 result data contain processed capacity, impedance and pulse-resistance results. The separate
high-frequency log dataset is a different deposit and is not silently included in this project's
initial scope.

## What was measured

The official record and article report:

- 228 commercial LG INR18650HG2 NMC/C+SiO cells;
- 76 operating-condition parameter sets, with three physical replicate cells per condition;
- 16 calendar-ageing, 48 cyclic-ageing and 12 driving-profile conditions;
- operating temperatures of 0, 10, 25 and 40 °C;
- repeated check-ups that place cells under comparable room-temperature capacity-test conditions;
- EIS and current-pulse measurements at nominal 10%, 30%, 50%, 70% and 90% SoC, first at room
  temperature and then at operating temperature;
- ageing that continued much farther than the common 70–80% capacity boundary, subject to the
  experiment's stated stopping rules.

The three ageing modes are not interchangeable:

| Mode | What happens between check-ups | Useful lifetime coordinate |
|---|---|---|
| Calendar | Cell is held near a target voltage/SoC and is not continuously cycled | Elapsed days under the declared storage condition |
| Cyclic | Repeated controlled charge/discharge within a specified voltage window and rate | Equivalent full cycles and declared operating condition |
| Driving profile | Dynamic WLTP Class 3b-derived power demand followed by recharge | Equivalent full cycles and/or distance under the declared profile |

Every mode still experiences some passage of calendar time, and the common check-up itself cycles the
cell. Models and explanations must not describe the modes as perfectly isolated physical mechanisms.

## Result-file groups

The version 2 result archive reviewed during project discovery includes these principal groups:

| Archive/file | Intended content |
|---|---|
| `cell_eocv2.zip` | Per-cell end-of-cycle/check-up capacity, energy, voltage and related result rows |
| `cell_eisv2.zip` | Per-cell EIS spectra and measurement metadata |
| `cell_plsv2.zip` | Per-cell pulse-test result data |
| `cfg.zip` | Configuration/reference tables used to interpret conditions and states |
| `Cycling Experiment Cell Overview ... .xlsx` | Mapping of cells to ageing conditions and parameters |
| `Data Structure v05.3.xlsx` | Field definitions and data-structure guidance |

The repository audit emits archive checksums, schema validation, member/row counts, frequency
registry, source provenance and ambiguity flags. This document is not a substitute for that
machine-readable report.

## Reproduced local audit inventory

`scripts/audit_kit_v2.py` produced the local-only report
`artifacts/local/kit-v2-audit.json` at `2026-08-01T22:29:48.576446+00:00`. The report is intentionally
outside the tracked scientific-result set while QA is open.

| Audit item | Reproduced count |
|---|---:|
| Real experimental cells in both EOC and EIS | 228 |
| Excluded `P000_0` system/test members per archive | 12 |
| EOC real rows | 1,286,612 |
| Standardized capacity endpoints | 3,999 |
| EIS real frequency rows | 1,177,835 |
| EIS spectra | 40,615 |
| Source-valid EIS spectra | 40,536 |
| RT-50% spectra | 3,998 |
| Source-valid RT-50% spectra | 3,993 |
| Registered frequency points per spectrum | 29 |

The audited archives are:

| Archive | SHA-256 |
|---|---|
| `cell_eocv2.zip` | `95de03af7d73efdeed97fcd13fbdca2eeabe85b2971b08394b665c8639b3e140` |
| `cell_eisv2.zip` | `bc1c56e05f50a41ed9d9f752f65fc7833b49ad6bc369f67ccfc14288a9815fed` |

The audit also pins the official example-script evidence at commit
`db5ec90cd2827824d11f74f95ed149b73acc6015`. No source code was copied.

### Blocking timestamp ambiguity

The official fields include a timestamp, duration and timestamp-origin metadata, but the reviewed
evidence does not establish one universal rule for whether a relevant snapshot completes at
`timestamp` or `timestamp + duration`. The local audit therefore marks all 3,993 RT-50 EIS/capacity
matches as `ambiguous_timestamp_candidate` with
`timestamp_only_candidate_not_training_eligible`. A documented 60-hour grouping heuristic is used
only to flag possible retry measurements; candidates are not silently dropped.

Until a source-backed completion policy is reviewed and bound to the manifest, these matches cannot
become model snapshots and Gate-3 real-data training cannot start.

## Observation hierarchy and label alignment

The hierarchy is:

```text
condition
  -> physical replicate cell
      -> repeated check-up
          -> capacity/pulse result
          -> EIS measurement context (RT/OT, SoC)
              -> spectrum
                  -> frequency rows
```

One check-up can produce multiple EIS spectra. An exploratory alignment found roughly ten spectra per
capacity target because measurements were taken at five SoCs at both room and operating temperature.
Those rows share one capacity outcome and cannot be counted as ten independent labels.

The initial clean view should use information available at a completed check-up. A model snapshot
must not include measurements, summaries or peer outcomes from later check-ups.

## Targets and derived outcomes

The raw standardized completed discharge capacity remains the auditable measurement. Conventional
SoH, where used, is derived as:

```text
SoH (%) = 100 x capacity at check-up t / predeclared initial capacity Q0
```

`Q0` must use one frozen, early-life rule. It must not be replaced with a later lifetime maximum.
Exploratory `Q0` values were approximately 2.926–2.971 Ah, showing that initial capacity is a real
cell-level factor rather than a universal constant.

The source field named `soh_cap` uses a dataset-specific transformation in which zero corresponds to a
capacity threshold around the experiment's 40–50% stopping boundary. It is not the conventional
0–100% SoH definition above and must not be relabeled as such.

The early-warning track derives future-looking outcomes from repeated check-ups, including:

- rapid degradation within a fixed future horizon;
- crossing a declared capacity threshold within a fixed future horizon;
- deviation from matched replicate cells under the same condition;
- conditional future capacity trajectory and remaining life.

When a threshold is observed only at check-ups, its exact continuous crossing time is unknown. It is
interval-censored between the last completed check-up above the threshold and the first completed
check-up at or below it. Cells with no observed crossing are right-censored at the last valid follow-
up; they must not be discarded or assigned a fabricated failure time.

## Candidate inputs and exclusions

Candidate information available at a check-up includes:

- initial capacity and capacity history up to that check-up;
- elapsed calendar time and accumulated usage coordinates;
- declared ageing condition, temperature, voltage/SoC window and rates/profile;
- current and historical EIS values at explicitly selected context(s);
- pulse-derived resistance and temperature/context metadata where complete.

The source `soh_imp` field is derived from impedance and must not be used as an input to an
impedance-based early-warning model; doing so would create a shortcut rather than an independent
feature. Any learned trajectory summary, imputation, scaling or variable selection must be fit inside
the training fold.

The local audit reports context-dependent missingness, including an entirely missing compensated
room-temperature 50%-SoC value at the highest-frequency sequence and partial coverage around 100 Hz.
The QA-accepted audit must freeze missingness by frequency and context. It must not silently fill
these fields before the split.

## Split and leakage boundary

- Unit of independence: physical cell.
- Replicate-aware grouping: the three cells under one condition remain identifiable.
- Row-random, spectrum-random and frequency-random train/test splits are prohibited.
- Same-cell future check-ups are unavailable to a historical snapshot.
- A stronger robustness evaluation holds out operating conditions, not only cells.
- A held-out cell's post-snapshot observations—and, for the O3 outcome, the held-out peers' aligned
  post-snapshot observations—may be read on the protected **label/evaluation side** after the applicable
  authorization to determine a future event target. They are prohibited from model inputs, fitting,
  imputation, scaling, normalization, variable selection, threshold choice or calibration.
- Operational peer-reference features, when a known-condition replicate scenario explicitly enables
  them, may use only preassigned reference cells and observations completed no later than the target
  prediction time. They are prohibited in the primary unseen-condition evaluation.
- Formal test labels remain locked until model, variables, hyperparameters, seeds and ensemble rules
  are frozen.

## Intended use and limitations

Appropriate research uses include degradation modelling, condition-held-out robustness, impedance
incremental-value tests, conditional RUL and maintenance decision support.

This dataset does **not** by itself establish a predictor of sudden internal short circuit, fire,
thermal runaway or every field failure mode. A risk tier is evidence-based decision support, not a
safety certification. Future-use assumptions must accompany every RUL estimate.

## Rights and attribution

The KITopen record reports CC BY 4.0. Reuse requires attribution, a license reference and indication
of changes. Large source files remain outside Git and are not relicensed as project code. The official
example scripts are a source of schema guidance; their code must only be reused according to their
own repository license and attribution terms.
