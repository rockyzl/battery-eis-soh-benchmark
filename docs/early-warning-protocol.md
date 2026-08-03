# Early-warning protocol — third-checkup SoH-loss prediction

Protocol lineage: `kit-early-warning-v1`

Status: `paired B/D_CORE condition-held-out development OOF complete; no selection, protected test, publication or deployment`

Authoritative decision: `docs/decisions/early-warning-target-and-oof-replay-v2.md`

Execution note (2026-08-02): the governed paired `B`/`D_CORE` packet completed 54/54 planned fits on
2,919 identical eligible snapshots and produced 23,352 OOF prediction rows. O2 fit/prediction counts
remain zero. Independent recomputation supports bounded development interpretation only.

Unified limitation: development OOF only; no O2, model selection, protected test, publication or
deployment. The observed EIS contrasts are mixed and do not establish a universal winner or causal
EIS effect. KIT is the only active project; CALCE is stopped and Cambridge is frozen historical
material. Current evidence and remaining work are tracked in
[`live-early-warning-tasks.md`](live-early-warning-tasks.md).

This protocol defines a distinct research lineage based on the public KIT comprehensive
battery-ageing dataset. It does not revise Cambridge SoH-v2 results. The source study contains 228
commercial NMC/C+SiO cells, 76 operating conditions, three replicate cells per condition, and
calendar, cyclic and WLTP-profile ageing. Published result data include capacity, impedance and pulse
check-up measurements. See the [KIT data record](https://publikationen.bibliothek.kit.edu/1000169196)
and [Scientific Data descriptor](https://doi.org/10.1038/s41597-024-03831-x).

## 1. Scientific question and claim boundary

At a completed diagnostic check-up, predict how many SoH percentage points a cell will lose by its
exact third subsequent completed check-up. Then ask whether current-check-up EIS improves that
condition-held-out prediction beyond capacity history, initial capacity, elapsed exposure and
declared operating conditions.

This is a future degradation-regression problem. It is not a current-SoH estimator: current SoH is
already measured at the check-up. It is also not RUL, survival, multiclass failure classification,
multitask learning, thermal-runaway prediction or a validated maintenance policy.

No result, threshold, operational benefit or model winner exists merely because this protocol or its
implementation tests pass.

## 2. Prediction moment, horizon and target

- Independent unit: physical cell.
- Group: complete KIT experimental condition containing three replicate cells.
- Snapshot: one completed diagnostic check-up for one cell.
- Prediction time: check-up completion time; all inputs and transforms use records completed at or
  before it.
- Horizon: exactly the third subsequent completed check-up (`H3`).
- Outcome window: records strictly after prediction time.

Primary target F1:

```text
soh_loss_pp_h3 = SoH_t - SoH_t+3
```

The unit is percentage points. The target is positive-oriented: positive means SoH degradation.
Observed negative values remain signed and visible for quality/noise review; they are not silently
clipped.

Displayed future SoH is derived from the primary prediction:

```text
predicted_future_soh = current_soh - predicted_soh_loss_pp_h3
```

If predicted loss has interval `[low, high]`, future SoH has interval
`[current_soh - high, current_soh - low]`.

Secondary O2 is the candidate probability `P(soh_loss_pp_h3 >= 5 pp)`. Five percentage points is not
an action threshold. O2 remains blocked until a development-only measurement-noise, event-support and
decision-utility review. It is a secondary derived risk view, not a joint multitask objective. A
separate classifier would require a separate preregistered sensitivity protocol.

A snapshot without an exact completed H3 endpoint is `no H3 follow-up`, not a negative or zero-loss
row. History eligibility and EIS availability are tracked independently.

## 3. SoH, Q0 and exposure definitions

```text
SoH (%) = 100 × current standardized check-up discharge capacity / Q0
```

`Q0` is the first valid standardized room-temperature check-up discharge capacity chosen by a
predeclared endpoint rule. Preserve source archive/member, cell ID, row locator, timestamp, endpoint
rule, raw value/unit and source-manifest hash. Lifetime maximum and future measurements are forbidden.
A snapshot without complete `Q0` provenance is ineligible.

The source `soh_cap` remains source metadata because its definition differs from this conventional
percentage. Source `soh_imp` is forbidden as an input or label shortcut.

Every snapshot stores check-up index for navigation and real exposure for scientific interpretation:

- calendar: elapsed days;
- cyclic: EFC, with elapsed days retained;
- driving profile: EFC plus source-backed distance when conversion is explicit and traceable; if not,
  distance is `not available` rather than inferred.

Check-up number is never presented as a unit of physical time.

## 4. Candidate input blocks

All fields are candidates until excluded for leakage, invalidity, unsupported deployment meaning or
insufficient training-fold coverage. Variables are not removed solely because an investigator expects
them to be weak.

### A — capacity history

- `Q0`, current capacity and current SoH;
- past capacity/SoH values and fold-local lag, change, slope and curvature summaries;
- check-up count/spacing, elapsed days and accumulated EFC through the snapshot.

### B_CONTEXT — declared use and environment

- ageing mode, temperature, SoC/voltage window;
- rates or profile identity and declared condition parameters;
- cumulative exposure available through the snapshot.

### C_EIS — current check-up EIS

- valid compensated real/imaginary impedance at declared frequencies;
- measurement temperature, nominal SoC and source quality indicators;
- governed paired-development view: room-temperature, nominal 50%-SoC spectrum, as approved by the
  D_CORE readiness review for this development-only lineage.

Frequency rows and repeated spectra are not independent batteries. Aggregation, missing-channel
handling and physical transforms must be frozen without held-out outcomes and retain acquisition
lineage.

### D_AUX — optional diagnostics

- audited pulse resistance/relaxation;
- measured temperature and quality/data-availability history.

Absence is reported; source-derived health labels never substitute for missing diagnostics.

## 5. Feature views and primary comparison

| View | Inputs | Question |
|---|---|---|
| `A` | capacity history | What does measured degradation history predict? |
| `B` | `A + B_CONTEXT` | What do declared conditions/exposure add? |
| `C` | `B_CONTEXT + C_EIS` | What can EIS plus context predict without capacity history? |
| `D_CORE` | `B + C_EIS` | Does EIS add value beyond the strong trajectory/context baseline? |
| `D_FULL` | `D_CORE + D_AUX` | Do separately audited auxiliary diagnostics add value? |

The central paired contrast is `D_CORE` versus `B` on identical eligible snapshots. Missing EIS may
make `D_CORE` unavailable while `B` remains valid. `D_FULL` cannot be used to attribute a pulse-data
gain to EIS.

## 6. Ageing-mode and split strategy

Calendar, cyclic and profile ageing represent different exposure processes. Mode-specific results
are primary. Any pooled model is secondary, must include explicit mode effects/interactions and cannot
win only because it has more rows.

Primary validation is condition-held-out OOF replay:

- all three replicate cells and all check-ups from a condition stay in one fold;
- transforms, imputation, variable selection, hyperparameters and intervals are trained without the
  held condition;
- overlapping snapshots from a cell never cross folds;
- random row, spectrum, check-up and fragmented-cell splits are prohibited.

Protected condition selection remains target-free using condition, mode and temperature metadata.
Protected capacity outcomes cannot influence EDA, label feasibility, model selection, thresholds or
calibration. A protected evaluation needs later explicit authorization after a winner is frozen.

Known-condition replicate transfer may be studied only as a clearly separate secondary deployment
question. It cannot be mixed with the primary OOF result.

Temperature interpolation and leave-one-temperature-out robustness are not evidence of reliable
temperature extrapolation.

## 7. Preprocessing and variable selection

Within every applicable training fold:

1. validate snapshot, source lineage and time boundary;
2. apply frozen missing/invalid EIS-channel rules;
3. derive only preregistered physical transforms (for example magnitude/phase);
4. fit imputation and scaling;
5. screen constant, high-missingness and redundant fields;
6. perform supervised selection, if used, only in inner condition-grouped CV;
7. fit the candidate and any interval/probability method;
8. emit untouched held-condition predictions with provenance.

Report selection frequency and effect/importance stability by held condition and mode. Instability is
a limitation, not mechanistic evidence.

## 8. Model-selection boundary

This protocol delegates model families, fixed budgets, metrics and uncertainty details to a reviewed
model-selection packet. For the completed paired development run, that packet froze persistence and
recent-slope baselines plus ElasticNet and HGB candidates, condition-balanced MAE, secondary error
metrics and whole-condition paired bootstrap intervals. It performed no hyperparameter search,
ensemble, champion selection or O2 probability modelling.

Deep sequence models and ensembles are not default. An ensemble requires complete same-target OOF
predictions, demonstrated held-condition error diversity, inner-fold-only weights, stable intervals
and a preregistered meaningful paired gain. Protected performance cannot create or rescue it.

No threshold may be searched for a flattering score.

## 9. OOF replay and truth maturation

The product displays all 228 real KIT histories as condition-held-out OOF replay, never as physics
simulation. At historical check-up `t`, future H3 ground truth is hidden. It unlocks only when replay
time reaches that measurement, after which measured future SoH, signed loss and eligible error/interval
evidence may appear.

Required states include `eligible`, `insufficient history`, `missing EIS`, `no H3 follow-up` and
`abstained`. They retain distinct reasons. The complete product contract is
`docs/product/kit-oof-replay-ui-spec-v1.md`.

## 10. Formal gates

1. Source, license, checksum and schema review.
2. Snapshot completion/alignment, `Q0`, exposure and acquisition-semantics review.
3. Development-only signed-target distribution, negative-loss/noise and exact-H3 follow-up review.
4. Candidate 5 pp O2 noise/support/utility review; it may be rejected without changing F1.
5. Target registry/schema and implementation alignment with this protocol; freeze hashes.
6. Condition-disjoint split and leakage review.
7. Bounded deterministic smoke with no protected outcomes and no performance ranking.
8. Preregistered model/variable-selection/uncertainty packet review.
9. Nested development OOF run and independent recomputation.
10. Winner and reporting contract freeze.
11. Separate authorization for one protected evaluation.
12. Independent result/limitation review and separate publication/resume-claim approval.

## 11. Current implementation status

The F1 development implementation is now aligned with this protocol. The governed paired B/D_CORE
run completed 54/54 planned fits on 2,919 identical eligible snapshots from 180 cells and emitted
23,352 condition-held-out OOF prediction rows. Independent recomputation supports bounded
development display only. O2 fits and predictions remain zero.

The earlier O2 Logistic/HGB robustness lineage remains unused for this F1 result and cannot be
relabeled as compatible evidence. The completed development artifacts retain
`scientific_result=false`, `formal_model_result=false`, `protected_test_result=false`,
`publication_ready=false`, `selection_performed=false`, `resume_approved=false` and
`deployment_ready=false`. A separate authorization is still required for any protected/external
evaluation, model selection, publication or deployment.
