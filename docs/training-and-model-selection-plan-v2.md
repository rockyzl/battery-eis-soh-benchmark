# Training and model-selection plan v2

Status: `draft — engineering implementation only; no formal training authorization`

This plan governs the new SoH-primary lineage defined in `docs/protocol-v2.md` and
`config/target_registry.json`. V1 remains historical and its artifacts cannot be relabeled as v2.

## 1. Primary question and score

Use one state-V EIS spectrum to predict held-cell SoH percent:

```text
SoH (%) = 100 × current discharge capacity / measured cycle-0 discharge capacity (Q0)
```

The primary score is cell-macro SoH MAE in percentage points. Secondary scores are cell-macro RMSE,
worst-cell MAE, pooled/per-cell MAE/RMSE/R², capacity MAE/RMSE and probabilistic coverage/width.
Predictions are not clipped to 0–100%.

The primary EIS model must beat both the target-mean baseline and the strongest cycle-only baseline
on training-cell nested out-of-fold predictions. Otherwise report that incremental EIS value was not
demonstrated.

## 2. Data and split

Primary rows are all valid state-V EIS/capacity alignments. The first-80%-crossing subset is a
post-selection sensitivity analysis only.

| Partition | Cells | Current all-valid rows | Use |
|---|---|---:|---|
| Selection | `25C01`–`25C04` | 679 | Nested model/feature/hyperparameter selection and final fit |
| Fixed test | `25C05`–`25C08` | 664 | One separately authorized evaluation batch |
| Domain robustness | 35 °C and 45 °C cells | 1,254 | Secondary temperature-transfer analysis only |

The test was exposed during earlier engineering work. It is prospectively rule-locked, not blind.
Selection uses four outer leave-one-training-cell-out folds and three inner leave-one-cell-out folds.
Every transform and choice is fitted within its applicable training fold.

## 3. Registered target matrix

| Task | Features | Target | Model-selection role |
|---|---|---|---|
| `T0` | EIS | SoH percent | Primary |
| `T0Q` | EIS + `Q0` | SoH percent | Direct `Q0` ablation on the primary target |
| `T1` | EIS | Capacity, mAh | Auxiliary absolute-capacity task |
| `T2` | EIS + `Q0` | Capacity, mAh | `Q0` incremental-value ablation |
| `T3` | EIS | Capacity loss `Q0-Q`, mAh | Degradation-scale ablation |

Each target has its own predictions, metrics and candidate decision. Never pool different-target OOF
predictions, learn a cross-target ensemble, or use an auxiliary task to replace a failed `T0` result.

`Q0` comes only from the explicit cycle-0 discharge measurement. A lifetime maximum, future row or
test-guided denominator is forbidden. Since `Q0` is extracted from the capacity source, formal test
evaluation treats test-cell `Q0` as protected label-side material.

## 4. Feature views

- `F0/eis`: 60 `Re(Z)` followed by 60 source `-Im(Z)`, descending frequency.
- `F0Q/eis_q0`: `F0` plus measured `Q0`; only for registered `Q0` tasks (`T0Q`, `T2`).
- `A0/cycle`: diagnostic cycle number only.
- `A0Q/cycle_q0`: cycle number plus measured `Q0`.
- Extended magnitude/phase, log-frequency summaries, fixed literature-prior frequencies and
  fold-local PCA retain the definitions in v1 and require registry freeze before search.

Scalers, PCA, feature selectors and any learned summaries are fold-local. Test features cannot guide
even unsupervised representation choices.

## 5. Candidate sequence

Run candidates from simple to complex:

1. target mean;
2. cycle-only polynomial Ridge;
3. cycle-only GPR with the same optimizer budget as EIS GPR;
4. EIS Ridge;
5. EIS PLS;
6. paper-family full ARD GPR as a method reference, not a paper replay;
7. stabilized full-ARD GPR;
8. PCA-GPR using preregistered component/kernel choices;
9. Random Forest as a supporting nonlinear sensitivity.

MLP, CNN and Transformer models remain excluded because the primary selection set contains only four
independent cells.

For `T2`, compare the same eligible capacity-model family with and without `Q0`. Do not compare a
high-capacity model on one feature view against a deliberately weak model on the other.

## 6. Hyperparameter selection

“Fine-tuning” here means ordinary train-only hyperparameter selection:

- Ridge alpha;
- PLS component count;
- PCA component count;
- GPR kernel family, length scales, noise bounds and preregistered optimizer starts;
- Random Forest feature fraction, depth and minimum leaf size.

The fixed test cannot choose a target, feature, frequency, candidate, seed, hyperparameter, ensemble
weight or reporting subset.

Apply the paired simplicity rule on outer-OOF held-cell MAEs:

```text
d_i = MAE_simple,i - MAE_complex,i
SE_diff = sd(d_i) / sqrt(4)
```

Select the complex candidate only when `mean(d_i) > SE_diff`; otherwise retain the simpler eligible
candidate.

## 7. Initial-capacity analysis

The `Q0` question is answered by frozen paired comparisons:

```text
T0Q: EIS + Q0 → SoH       versus T0: EIS → SoH
T2: EIS + Q0 → capacity
T1: EIS      → capacity
```

Also compare cycle+`Q0` against cycle-only prediction for both registered targets. Report cell-wise deltas and the same
paired simplicity rule. The result should distinguish:

- prediction of a cell's starting scale;
- prediction of its relative degradation;
- incremental information in EIS after known age and baseline capacity.

The last question is tested directly by comparing `T2` (EIS+`Q0` → capacity) with `B2`
(cycle+`Q0` → capacity) using matched eligible model families and optimizer/search budgets. Without
that same-target, same-`Q0` contrast, the project may claim value beyond cycle or value from `Q0`, but
not value beyond both.

## 8. Ensemble rule

No ensemble is promoted by default. An exploratory ensemble requires complete same-target nested OOF
predictions from at least three eligible bases, sufficiently different residuals, stable nonnegative
weights summing to one, and improvement over both the strongest age baseline and best eligible single
model under the preregistered margins. Weights are learned inside inner folds only. Test results cannot
create, recalibrate or rescue an ensemble.

## 9. Execution and review gates

1. Freeze source, row, split, target and feature-registry hashes.
2. Independently review the `Q0` definition, task separation and leakage boundary.
3. Implement a selection-only label view and permission-separated fixed-test evaluator.
4. Run a bounded real-backend smoke with heartbeat, stage/fold/seed progress, CPU/RSS, GPR optimizer
   state, ETA, warnings and immutable provenance.
5. Review smoke feasibility before authorizing the nested search.
6. Run the complete train-only nested ledger; incomplete trials suppress ranking.
7. Independently recompute OOF metrics and sign the winner.
8. Separately authorize and review final fit.
9. Separately authorize one fixed-test batch with exactly one label-read event.
10. Independently recompute final metrics before any scientific, publication or resume claim.

Until every applicable gate passes, all artifacts retain `scientific_result=false`,
`publication_ready=false` and `resume_approved=false`.

## 10. Immediate implementation boundary

The current engineering evaluator now requires a registered task ID, exercises all-valid row scope
and percentage-point metrics, and requires an explicit acknowledgment before loading historical
test labels. A separate selection loader opens only `25C01`–`25C04`. The evaluator itself still loads
historical test labels in one process. Therefore
it is useful only for bounded plumbing tests and cannot authorize or perform formal selection.

The next implementation milestone is physical test-label isolation plus nested training-cell OOF
infrastructure. Formal model training remains blocked until those seams and a real-backend monitored
smoke pass independent review.
