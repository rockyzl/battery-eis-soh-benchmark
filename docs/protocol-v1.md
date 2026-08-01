# Protocol v1 — draft, not yet authorized for final training

Protocol status: `draft`

## Scientific question

Can state-V EIS estimate absolute discharge capacity for a physical cell that was not used to fit the
model, and support reporting normalized SoH relative to that cell's cycle-0 capacity?

Primary metric: cell-macro MAE in normalized SoH. Secondary metrics: cell-macro RMSE, pooled MAE/RMSE,
per-cell error and, for probabilistic models, empirical interval coverage.

## Data boundary

- Input: real and negative-imaginary impedance at 60 frequencies from state V.
- Model target: maximum absolute discharge capacity in mAh (`ox/red == 0`) for the same cell's
  preceding capacity diagnostic, using the source experiment ordering `EIS cycle k -> capacity cycle
  k-1`, matching the first published model input/target pair and the paper's Methods definition.
- Reported SoH: predicted capacity divided by the known `cycle=0` discharge capacity for that cell.
  This normalization is used for comparable reporting, not as the fitted model target.
- This benchmark—not the original paper's capacity-model protocol—includes each cell through its
  first observed SoH at or below 0.80 and excludes later post-EOL rows. The crossing row remains
  included; a cell that never crosses is treated as right-censored and retains all available rows.
- Independent group: cell ID.
- No row-random split.
- All scaling, dimensionality choices and model selection must be fitted on training cells only.

## Evaluation tracks

1. Paper-aligned 25 °C split:
   - train: `25C01`, `25C02`, `25C03`, `25C04`
   - test: `25C05`, `25C06`, `25C07`, `25C08`
2. Grouped leave-one-cell-out: secondary robustness analysis after the fixed split is frozen.

## Models

- mean target baseline;
- cycle-only Ridge trend baseline, which tests whether EIS adds value beyond diagnostic age;
- Ridge with fixed `alpha=1.0`;
- PLS with a preregistered component count bounded by the training sample/feature count;
- GPR with an ARD kernel after an instrumented runtime smoke and explicit run approval.

The first smoke runs mean, cycle-only Ridge, EIS Ridge and EIS PLS. It may validate plumbing but is
not a scientific result.

## Promotion gates

1. source/checksum/license review;
2. cell and join audit;
3. frozen protocol and split hashes;
4. clean committed code and dependency lock;
5. instrumented model smoke;
6. reconcile the cleaned Zenodo benchmark with documented source-array anomalies;
7. independent result review;
8. only then mark a metric as resume-approved.
