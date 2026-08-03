# Model-selection plan — KIT H3 SoH-loss early warning

Status: `paired B/D_CORE fixed-family development OOF complete; no selection, protected test, publication or deployment`

Dependencies:

- `docs/decisions/early-warning-target-and-oof-replay-v2.md`
- `docs/early-warning-protocol.md`

This plan selects a predictor for the continuous F1 target
`soh_loss_pp_h3 = SoH_t - SoH_t+3`. O2 rapid-loss probability is secondary and cannot drive the F1
winner unless a later reviewed decision explicitly adds that criterion. No multiclass, multitask or
RUL candidate is in scope.

Execution note (2026-08-02): the governed paired `B`/`D_CORE` run completed 54/54 planned fits on
2,919 identical eligible snapshots and produced 23,352 OOF prediction rows. O2 fit/prediction counts
are zero, and no winner selection was performed.

Unified limitation: development OOF only; no O2, model selection, protected test, publication or
deployment. The observed EIS contrasts are mixed and do not establish a universal winner or causal
EIS effect. KIT is the only active project; CALCE is stopped and Cambridge is frozen historical
material. Current evidence is summarized in
[`live-early-warning-tasks.md`](live-early-warning-tasks.md).

## Stage 0 — source audit and target-free snapshot inventory

1. Verify official KIT Version 2 sources, hashes, license and the 228-cell / 76-condition / three-
   replicate inventory.
2. Reproduce counts for valid capacities, EIS, pulse, timestamps, modes, temperatures and conditions.
3. Quantify missingness by cell, condition, mode, check-up and EIS context without reading protected
   capacity outcomes.
4. Build one target-free snapshot identity per eligible completed check-up. Never expand frequency
   rows or acquisitions into independent samples.
5. Compute `Q0`, conventional SoH and traceable exposure using only records available by snapshot
   time.
6. Freeze development/protected condition membership from metadata only.

Deliverable: source-backed snapshot manifest, availability/status fields and protected membership;
not a trained model.

## Stage 1 — F1 feasibility and noise audit

Use development conditions only to:

- align exact third subsequent completed check-ups;
- compute signed `soh_loss_pp_h3` without clipping negative observations;
- report eligible/no-H3 counts by mode, condition and cell;
- separate `insufficient history`, `missing EIS` and `no H3 follow-up`;
- quantify repeated-measurement variability and apparent negative loss;
- inspect target distribution and condition/cell support without changing the frozen horizon;
- evaluate whether candidate O2 `loss >= 5 pp` is distinguishable from measurement noise and useful
  enough to retain.

If O2 fails, drop the secondary probability view; do not retune its threshold for model performance.
F1 remains the primary question unless a new decision record supersedes it.

## Stage 2 — fixed development packet

Construct `B` and `D_CORE` exactly as defined by the protocol for the fixed Stage 2 comparison. Other
feature views are outside this milestone. Within each condition-grouped training partition:

1. source/lineage/time-boundary validation;
2. frozen EIS acquisition and invalid-channel handling;
3. preregistered physical transforms;
4. train-fold imputation/scaling;
5. deterministic constant/missingness/validity handling defined before fitting;
6. fixed candidate fit;
7. held-condition prediction with full provenance.

`Q0`, current SoH, time/exposure and declared conditions stay in the candidate matrix unless a
scientific leakage/validity rule excludes them. There is no separate supervised variable-selection
step in this milestone. Any learned preprocessing and all regularization are fitted inside the
condition-grouped training partition; plausible inputs are not screened using held-condition
outcomes.

The run identity is exact:

```text
KIT_F1_O2_DEVELOPMENT_V2
```

Strict separation from `KIT_O2_DEVELOPMENT_ROBUSTNESS_V1` is mandatory. A v1 registry, config,
artifact, result or validation receipt cannot be relabeled or consumed by v2.

### Fixed baselines

- Persistence: `predicted_soh_loss_pp_h3 = 0`.
- Recent slope: `predicted_soh_loss_pp_h3 = 3 * (SoH_(t-1) - SoH_t)`.

The recent-slope baseline is eligible only when the immediately preceding completed check-up is
available and valid. Negative predictions are retained.

### Fixed fitted regressors

```text
ElasticNet:
  alpha: 0.01
  l1_ratio: 0.1
  max_iter: 5000
  tol: 1e-5
  random_state: 20260801

HistGradientBoostingRegressor:
  learning_rate: 0.05
  max_leaf_nodes: 7
  max_iter: 100
  min_samples_leaf: 20
  l2_regularization: 1.0
  early_stopping: false
  random_state: 20260801
```

The v2 configuration may expose the HGB penalty as `l2: 1`; the implementation mapping to
`l2_regularization=1.0` must be explicit and validator-checked.

There is no hyperparameter search, ensemble or champion selection. Compare `B` and `D_CORE` only
within the same fixed model family and on identical OOF snapshot identities. Cross-family metrics are
descriptive and cannot create a winner.

### Fixed metrics and uncertainty

- Primary: condition-balanced MAE in SoH percentage points, formed by equal-weighting the MAE of each
  eligible held condition.
- Secondary: median absolute error, RMSE, signed bias and R-squared.
- Paired feature-view contrast: `D_CORE - B` within each model family.
- Uncertainty for the paired contrast: whole-condition bootstrap only. Bootstrap seed, replicate
  count and quantiles must be frozen in configuration before runtime compatibility approval.
- Prediction interval: unavailable/null for this milestone. An aggregate bootstrap interval is not a
  per-prediction interval and must not be displayed as one.

### O2 feasibility-only lock

```text
training_authorized: false
display_authorized: false
selection_role: none
```

The run must contain zero O2 fits, O2 predictions, O2 paired comparisons and O2 retry analyses. Wave
A may report only descriptive candidate-threshold prevalence, repeated-measurement noise and support.

## Stage 3 — strict v2 engine/runtime compatibility

Before a bounded run, the engine and runtime must agree on a strict v2 schema with
`additionalProperties=false` behavior (or an equivalent exact-key check), exact types, finite numeric
values and the exact run identity. `engine_summary.json` must itself persist the ordered seven
artifact paths; returning them only from the Python callable is insufficient.

Runtime validation must independently:

1. recompute `soh_loss_pp_h3 = current_soh - future_soh_h3` for every eligible truth-bearing row;
2. recompute `predicted_future_soh = current_soh - predicted_soh_loss_pp_h3` wherever a prediction
   exists;
3. reconcile snapshot, cell, condition, fold, feature-view and model-family identities across the
   snapshot manifest, fold registry, OOF predictions, paired deltas and summary;
4. reconcile declared counts with physical line/object counts and unique-identity counts;
5. require exact `B`/`D_CORE` pairing within each model family before a paired result is accepted;
6. verify that O2 fit/prediction/pair/retry counts are all zero and its three authorization fields
   have the frozen values;
7. reject v1 identity, fields or classification metrics even when filenames match.

This validation contract governed the paired `B`/`D_CORE` v2 packet. The completed packet passed
independent bounded review; that review does not promote it to selected, protected, public or
production evidence.

## Stage 4 — bounded condition-grouped OOF development run

- Outer unit: complete condition containing all three physical cells.
- All snapshots from a physical cell remain in one group.
- Learned preprocessing and candidate fitting occur inside the outer-training data.
- Every candidate/view emits predictions on the same traceable OOF rows where pairing is claimed.
- No protected outcome is read, scored or used for any choice.

The paired `B`/`D_CORE` development OOF received its specific data/runtime authorization and
completed. Its authorization was limited to development OOF and cannot be used to bypass later
selection, protected-evaluation, publication or deployment gates.

`formal_model_result` remains false throughout Waves A–C. It may be recognized as an accepted
development OOF result only after independent Wave D scientific and software/runtime approval; that
status still does not make it a protected-test, production or public result.

## Stage 5 — fixed comparisons and reporting

The primary scientific contrast is `D_CORE` versus `B`, evaluated separately for ElasticNet and HGB
regression on identical eligible rows. Baseline scores provide context but do not enter model
selection. Other feature-view contrasts are out of the fixed Stage 2 milestone.

Report the condition-balanced primary metric, all secondary metrics, whole-condition bootstrap of the
paired contrast, per-condition support and errors, missingness/abstention and failure cases. This
milestone has no preregistered meaningful-gain margin and therefore cannot declare that EIS "adds
value" as a formal decision. It may report the observed development OOF contrast with limitations.

## Stage 6 — explicit no-selection gate

No ensemble, hyperparameter search, winner, champion, operational model or production threshold is
created. No protected score may create, tune or rescue a candidate. Any future selection requires a
new decision record and a newly frozen protocol.

## Stage 7 — O2 review gate

O2 remains a feasibility-only scientific question after this milestone. Even if descriptive support
appears adequate, training and display remain unauthorized until a new reviewed decision freezes a
probability method and evaluation contract.

No alert color, first-warning event, action threshold or maintenance recommendation exists until an
independent decision-utility review freezes it. Until then the UI queue organizes research review by
availability/maturation only.

## Stage 8 — OOF replay, protected evaluation and claim review

After independent OOF recomputation, freeze source/snapshot/split/config/code hashes and a
development-only report. No winner or prediction interval exists in this packet. A later decision may
define whether any candidate proceeds to a one-time protected condition-group evaluation.

The replay must identify development OOF predictions as condition-held-out estimates, hide future H3
truth until maturation, and distinguish every unavailable state. Publication and resume claims need a
separate independent review of immutable artifacts.

## Current remaining gates

- The paired B/D_CORE development run is complete and independently recomputed: 54/54 fits, 2,919
  identical eligible snapshots and 23,352 OOF prediction rows. It did not perform selection.
- O2 fit/prediction counts are zero; O2 remains unauthorized because its noise, event support and
  decision utility are not established.
- A winner/promotion rule, protected evaluation and external replication require separate decisions.
- Operational thresholds, maintenance actions, publication and resume claims remain blocked.
