# Protocol v2 — SoH-primary target and initial-capacity ablation

Protocol status: `draft — implementation complete for engineering smoke; formal training blocked`

Protocol v2 is a new scientific lineage. It does not revise or relabel results produced under v1.
No v2 artifact is a scientific result, publication result, or resume-approved claim until the review
and authorization gates below are completed.

## Scientific question

Can one state-V EIS spectrum estimate the state of health of a physical 25 °C cell that was not used
to fit or select the model, and does EIS add predictive information beyond cycle number and known
initial capacity?

The primary fitted target is:

```text
SoH percent = 100 × current discharge capacity / Q0
```

`Q0` is the maximum cumulative discharge-capacity endpoint among `ox/red=0` rows at source cycle 0
for that cell. The source capacity channel is cumulative within a discharge diagnostic, so the
maximum selects its completed discharge endpoint. This exact rule is audited per cell and cannot be
replaced with a later lifetime maximum.
The primary metric is the mean of the four held-cell SoH MAEs, expressed in percentage points. Do not
describe a two-percentage-point MAE as a two-percent relative error.

Secondary metrics are cell-macro SoH RMSE, worst-cell SoH MAE, pooled and per-cell SoH MAE/RMSE/R²,
absolute-capacity MAE/RMSE, and probabilistic interval coverage and width when available.

Predictions are never clipped to 0–100%. Values outside that range remain visible as extrapolation or
model error. User-facing reports may display SoH as a percentage while preserving the raw prediction.

## Data and label boundary

- Input `F0`: 60 `Re(Z)` plus 60 source `-Im(Z)` values from state V, in verified descending-frequency
  order.
- Direct primary target: SoH percent.
- Raw measurement retained for audit: current absolute discharge capacity in mAh.
- Join: state-V EIS diagnostic cycle `k` maps to capacity diagnostic cycle `k-1`.
- Independent group: physical cell ID; row-random splitting is prohibited.
- Primary row scope: all otherwise valid aligned rows.
- Sensitivity row scope: through the first observed SoH at or below 80%, including the crossing row.
  It cannot affect feature, model, hyperparameter, seed, or ensemble selection.

`Q0` is scientifically meaningful baseline metadata, not a harmless constant. It may be used only
in the registered ablations and baselines. It must come from the predeclared source cycle-0
measurement. Using a lifetime maximum, a future observation, or a denominator chosen after seeing
test performance is leakage.

For a held cell, a model that requires `Q0` assumes a deployment workflow in which a baseline capacity
test was completed before ageing. Results must state that requirement. A future deployment without a
measured `Q0` needs a separately reviewed nominal-capacity or baseline-estimation protocol.

## Registered tasks

| ID | Inputs | Direct target | Purpose |
|---|---|---|---|
| `T0` | EIS | SoH percent | Primary scientific task |
| `T0Q` | EIS + `Q0` | SoH percent | Test whether initial capacity adds information after normalization |
| `T1` | EIS | Current capacity, mAh | Absolute-capacity auxiliary task |
| `T2` | EIS + `Q0` | Current capacity, mAh | Quantify the incremental value of initial capacity |
| `T3` | EIS | Capacity loss `Q0-Q`, mAh | Test whether EIS predicts absolute degradation |
| `B0` | None | SoH percent | Training-target mean baseline |
| `B1` | Cycle number | SoH percent | Strong ageing baseline |
| `B1Q` | Cycle number + `Q0` | SoH percent | Initial-capacity-aware ageing baseline |
| `B2` | Cycle number + `Q0` | Current capacity, mAh | Initial-capacity-aware non-EIS baseline |

`T0Q`–`T3` are ablations, not alternate routes for selecting the primary `T0` winner. Their results
must be reported as separate tasks. A favorable auxiliary result cannot replace failure on `T0`.

## Partition and selection

- Selection/training cells: `25C01`–`25C04` (currently 679 all-valid rows).
- Prospectively rule-locked test cells: `25C05`–`25C08` (currently 664 all-valid rows).
- The test was exposed by earlier engineering work and must not be called blind or untouched.
- Model and feature selection use four outer leave-one-training-cell-out folds, with three inner
  leave-one-cell-out folds inside every outer fold.
- Scaling, PCA, feature selection, hyperparameters, seeds and ensemble weights are fitted inside the
  corresponding training fold only.
- After selection is independently signed, final hyperparameters are chosen using only the four
  training cells and the winner is fitted once on those cells.
- The fixed test is evaluated in one authorized batch and may not change any modeling decision.

## Selection and model roles

The candidate families and search budgets remain governed by the reviewed training plan, but every
candidate must declare one registered task and feature view. The primary `T0` EIS winner must beat the
mean and strongest cycle-only SoH baseline in nested training-cell evaluation. Apply the paired
per-cell one-standard-error simplicity rule before choosing a more complex model.

Initial-capacity value is assessed by preregistered contrast, not by informal inspection:

```text
T0Q (EIS + Q0 → SoH) versus T0 (EIS → SoH)
T2 (EIS + Q0 → capacity) versus T1 (EIS → capacity)
B1Q (cycle + Q0 → SoH) versus B1 (cycle → SoH)
B2 (cycle + Q0 → capacity) versus a corresponding cycle-only capacity baseline
T2 (EIS + Q0 → capacity) versus B2 (cycle + Q0 → capacity), using matched model/search budgets
```

The final contrast asks whether EIS adds information beyond both known cycle and known `Q0`; the
first three contrasts isolate the contribution of `Q0`. Report paired held-cell differences and do
not claim incremental value unless the frozen rule is met. Do not combine predictions from different targets in one ensemble. Ensemble weights, if later
eligible, are learned from same-target inner out-of-fold predictions only.

## Test-label and Q0 isolation

The current engineering evaluator can load both train and test labels and is not eligible for formal
training. Before search authorization, implement physically separate selection and evaluation paths:

1. the selection runner can access labels and `Q0` only for `25C01`–`25C04`;
2. target-free test membership and EIS row identities are frozen before any test-label read;
3. the authorized test evaluator alone can access `25C05`–`25C08` capacity labels and `Q0`;
4. the evaluator records exactly one authorized test-label read;
5. selection outputs cannot expose test metrics back to the training process.

Because the primary SoH label depends on `Q0`, test-cell `Q0` is protected label-side material even
when a registered deployment ablation treats it as known baseline metadata.

## Review and authorization gates

1. Data and `Q0` definition review.
2. Protocol and task-registry review.
3. Test-isolation and fold-local transformation review.
4. Instrumented real-backend smoke review with live telemetry and hard resource budgets.
5. Nested training-cell selection review.
6. Final-fit authorization and review.
7. Separate fixed-test authorization.
8. Independent recomputation and scientific-result review.
9. Separate publication and resume-claim review.

Authorization is a hard stop. Approval of this protocol does not authorize formal search, final fit,
test evaluation, publication, or resume wording.
