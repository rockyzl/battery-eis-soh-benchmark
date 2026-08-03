# KIT battery early-warning development process

Status: `paired B/D_CORE development OOF completed; no protected test or publication approval`
Active data project: **KIT only; CALCE stopped**

## 1. Purpose

The project asks a future-looking question at a completed battery check-up:

> Using only evidence available now, how many SoH percentage points will the cell lose by its third
> subsequent completed check-up?

It then asks whether current-check-up EIS adds information beyond capacity history and operating
context. It is not a current-SoH calculator, a sudden-failure predictor or a production maintenance
system.

## 2. Public data and hierarchy

KIT result data v2 contains 228 commercial NMC/C+SiO cells under 76 conditions, with three physical
replicates per condition. Repeated check-ups include capacity, EIS and pulse measurements across
calendar, cyclic and driving-profile ageing.

```text
condition -> physical cell -> check-up -> capacity endpoint + EIS acquisitions -> frequency rows
```

Frequency rows, spectra and check-ups are not independent batteries. Complete conditions remain
together during outer validation.

Sources:

- <https://publikationen.bibliothek.kit.edu/1000169196>
- <https://doi.org/10.1038/s41597-024-03831-x>
- <https://github.com/energystatusdata/bat-age-data-scripts>

## 3. Target and prediction moment

```text
SoH_t (%) = 100 × Capacity_t / Q0
soh_loss_pp_h3 = SoH_t − SoH_t+3
future_soh = SoH_t − predicted_loss
```

H3 means the third subsequent completed check-up, not a fixed number of days, cycles or kilometres.
Inputs are prefix-only. Missing future H3 truth is `no_h3_followup`, never zero loss.

## 4. EDA and eligibility

The governed materialization contains 3,999 capacity snapshots:

- 2,919 `eligible_f1_and_d_core` snapshots from 180 cells;
- 456 `insufficient_history` snapshots;
- 624 `no_h3_followup` snapshots.

Paired support is calendar 1,134 snapshots / 48 cells / 16 conditions; cyclic 1,190 / 99 / 35;
profile 595 / 33 / 11. The lower profile support is an explicit limitation.

## 5. Feature engineering

`B` contains current and recent SoH history, signed recent loss, check-up index, elapsed exposure,
Q0-normalized throughput and declared ageing context. `D_CORE` contains B plus frozen
current-check-up RT-50 EIS features and missingness indicators.

The comparison keeps snapshot IDs, folds, target, weights and model family identical. Learned
preprocessing is fit inside each outer-training partition. Missing features are not silently treated
as physical zero.

No supervised variable-selection search was run. With only 76 independent condition groups, global
outcome-driven screening would invite leakage and instability. ElasticNet regularization is bounded
shrinkage, not proof of stable scientific variable selection.

## 6. Models and reason for inclusion

| Candidate | Role |
|---|---|
| Persistence | zero-loss lower-bound baseline |
| Recent slope | transparent local-trend baseline |
| ElasticNet | constrained linear fitted candidate |
| HGB | constrained nonlinear fitted candidate |

Persistence and recent slope exist only in B. ElasticNet and HGB are compared across B and D_CORE.
There was no hyperparameter search, ensemble or winner promotion.

## 7. Split, training and validation

Each ageing mode has three complete-condition outer folds. All replicate cells and check-ups from a
held condition stay out of fitting. The fixed plan completed 54/54 fits:

```text
3 modes × 3 folds × (2 B fitted families
                     + 2 D_CORE primary families
                     + 2 D_CORE keep-last sensitivity families)
```

The run produced 23,352 OOF rows. O2 training/predictions are zero. Protected labels were not opened.

The first run lineage failed after 42/54 fits and remains immutable. A real-data read-only audit plus
synthetic stress reproducer supported convergence-budget exhaustion for ElasticNet on an
ill-conditioned D_CORE matrix. The minimal correction changed only `max_iter` from 5,000 to 250,000
and sanitized failure reporting. This is a numerical convergence budget, not model tuning or evidence
of better generalization. A new lineage then ran from the beginning.

## 8. Metrics and paired uncertainty

Primary metric: condition-balanced MAE. Secondary: median AE, RMSE, signed bias and R².

```text
delta = metric(D_CORE) − metric(B)
```

Negative MAE/RMSE delta means lower error with EIS. Each paired delta uses 2,000 whole-condition
bootstrap draws with a 95% percentile interval. Draws do not become independent cells, and the
interval is not a causal significance test.

## 9. Result summary

| Mode | Family | ΔMAE [95% CI] | Interpretation |
|---|---|---|---|
| Calendar | ElasticNet | +0.051 [-0.020, +0.144] | point worse, direction unclear |
| Calendar | HGB | -0.001 [-0.013, +0.009] | no clear change |
| Cyclic | ElasticNet | +0.804 [+0.285, +1.361] | worsening signal |
| Cyclic | HGB | -0.625 [-1.056, -0.241] | improvement signal |
| Profile | ElasticNet | -0.330 [-1.208, +0.433] | point better, direction unclear |
| Profile | HGB | +0.215 [+0.036, +0.384] | MAE worse; RMSE unclear |

Primary and keep-last sensitivity results are identical for all six combinations, with no sign
reversal. This does not validate timestamp semantics, which remain provisional.

The bounded conclusion is mode × family dependence. It is not “EIS works,” “EIS fails,” a universal
winner or a causal statement.

## 10. UI and delivery process

The static fleet replay exposes all 228 cells and 180 paired-eligible cells. Users can step through
historical check-ups, filter ageing mode, switch HGB/ElasticNet, compare B with D_CORE for the selected
cell, inspect six aggregate delta/CI cards and open matured H3 truth. Missing history and future truth
are explicit; no value is fabricated.

The commit-safe projection is built only from hash-authenticated governed artifacts. Composite
identity includes mode, policy, view, family and design/snapshot identity, preventing B/D_CORE or
primary/sensitivity overwrite. Raw KIT data and local grants/receipts are never exported.

## 11. Review gates

1. Source/readiness review: complete.
2. Frozen paired plan and implementation QA: complete.
3. One-use execution authorization: consumed by the completed run.
4. Independent artifact/metric/bootstrap replay: complete for bounded development display.
5. Model selection: not performed.
6. Protected/external test: not authorized.
7. Publication/deployment review: not approved.

All display surfaces must retain:

```text
scientific_result=false
formal_model_result=false
protected_test_result=false
publication_ready=false
selection_performed=false
deployment_ready=false
```

## 12. What this could eventually change

If independently validated prospectively, future versions might help engineers prioritize repeat
tests and organize evidence. This run contains no operational threshold, action study or cost model,
so it cannot support claims about replacement timing, safety improvement or savings.

## 13. Next scientific work

- diagnose why cyclic HGB and ElasticNet react oppositely to D_CORE;
- inspect collinearity, feature stability, mode support and condition-level failure cases;
- independently resolve timestamp/retry semantics;
- freeze the next question before opening any additional outcome evidence;
- require a separate review before protected/external evaluation or publication.
