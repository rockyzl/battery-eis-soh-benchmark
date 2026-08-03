# Decision: F1 third-checkup SoH-loss target and OOF replay product contract

Date: 2026-08-01

Status: `frozen design record; paired B/D_CORE development packet completed; later gates blocked`

Scope: KIT early-warning lineage, scientific target, secondary risk view and public-facing replay UI

Execution update (2026-08-02): after the earlier B-view engineering milestone, the separately
reviewed `KIT_F1_O2_DEVELOPMENT_V2` paired packet completed on a new governed lineage. It completed
54/54 fits and 23,352 OOF rows across B, D_CORE primary and D_CORE keep-last sensitivity for 2,919
paired snapshots. O2 remained at zero fits/predictions. Twelve B/D_CORE deltas and six sensitivity
records passed independent reconstruction. The results are mixed across mode and model family; they
do not authorize selection, protected evaluation, publication or deployment. Historical readiness
paragraphs below describe the gate state when this record was frozen; current status lives in
[`../live-early-warning-tasks.md`](../live-early-warning-tasks.md).

The first paired attempt failed after 42/54 fits and remains immutable. The replacement lineage used
the same scientific design; only the ElasticNet numerical convergence budget changed from 5,000 to
250,000 after a synthetic stress reproducer and independent review. This is not outcome-driven model
tuning and does not prove a universal failure mechanism.

## Frozen decision

The primary target is a future continuous degradation quantity at exactly the third completed
check-up after prediction time `t`:

```text
soh_loss_pp_h3 = SoH_t - SoH_t+3
```

The unit is SoH percentage points (`pp`). The sign is **positive-oriented**: a positive value means
the cell lost SoH. Observed negative values are not silently clipped to zero because they may expose
measurement variability, apparent recovery or a data-quality problem. Any later clipping or robust
loss treatment requires a separate, reviewed decision.

The corresponding displayed future-health estimate is derived, not independently predicted:

```text
predicted_future_soh = current_soh - predicted_soh_loss_pp_h3
```

If a valid loss interval is `[loss_low, loss_high]`, the corresponding future-SoH interval is:

```text
[current_soh - loss_high, current_soh - loss_low]
```

The secondary outcome candidate is:

```text
O2 = P(soh_loss_pp_h3 >= 5 pp)
```

Five percentage points is a **candidate scientific event definition**, not an operational alert or
service threshold. In the conditional Stage 2 packet, O2 is feasibility-only:

```text
training_authorized = false
display_authorized = false
selection_role = none
```

Stage 2 produces zero O2 fits, predictions, paired comparisons or retry analyses. O2 may be retained
as descriptive development-only prevalence/noise metadata after Wave A, or dropped. It cannot turn
F1 into a multitask optimization problem and cannot influence model selection.

## Eligibility and missing-outcome semantics

- The prediction snapshot is a completed diagnostic check-up.
- Inputs use only information completed at or before that snapshot.
- F1 requires an exact completed third subsequent check-up with trustworthy alignment semantics.
- A row without that endpoint is `no H3 follow-up`; it is not a zero-loss or negative example.
- Feature-history requirements are separate. A row may be `insufficient history` even when H3 exists.
- Missing EIS can make `D_CORE` unavailable while a non-EIS baseline remains eligible.
- Model abstention is distinct from missing history, missing EIS and missing future truth.

## Evaluation boundary

The primary generalization question remains unseen-condition generalization. Every displayed model
prediction must come from a fold in which the cell's entire experimental condition was held out.
All three replicate cells and all their check-ups remain together. Random row, spectrum, cell-history
fragment or check-up splitting is prohibited.

The main scientific comparison remains paired `D_CORE` versus `B` on the same eligible OOF rows:

- `B`: capacity history plus declared operating conditions and exposure;
- `D_CORE`: `B` plus eligible current-check-up EIS.

## Conditional Stage 2 development packet

This packet is frozen for one bounded development OOF milestone. F1 formal development OOF is
scientifically authorized only after Wave A receives an explicit `GO-A`. Execution additionally
requires v2 engine/runtime compatibility (`GO-B`) and the separate human run gate. Before those gates,
`formal_model_result=false` and no fitting is authorized.

Current Wave A disposition is `NO-GO-A`, recorded without reinterpretation in
`artifacts/finish-wave/A-target-data-readiness.json` and
`docs/audits/kit-f1-o2-target-readiness-zh.md`: the official source archives are unavailable for
current recomputation, snapshot completion semantics remain unverified, and capacity
technical-repeat noise has not established O2 feasibility. The conditional packet below is therefore
frozen but not executable.

Run identity is exact:

```text
run_kind = KIT_F1_O2_DEVELOPMENT_V2
```

The v1 `KIT_O2_DEVELOPMENT_ROBUSTNESS_V1` registry, configuration, artifacts, validators and results
are historical and incompatible. No v1 artifact may be renamed, coerced, promoted or mixed into v2.

Frozen baselines:

- persistence: `predicted_soh_loss_pp_h3 = 0`;
- recent-slope extrapolation:
  `predicted_soh_loss_pp_h3 = 3 * (SoH_(t-1) - SoH_t)`.

The recent-slope baseline requires the immediately preceding eligible completed check-up and uses
only past/current information. Its signed prediction is not clipped.

Frozen fitted regressors:

```text
ElasticNet(alpha=0.01, l1_ratio=0.1, max_iter=250000,
           tol=1e-5, random_state=20260801)

HistGradientBoostingRegressor(learning_rate=0.05, max_leaf_nodes=7,
                              max_iter=100, min_samples_leaf=20,
                              l2_regularization=1.0,
                              early_stopping=false,
                              random_state=20260801)
```

No hyperparameter search, ensemble, champion declaration or protected-data selection is allowed.
Within each model family, compare feature view `B` with `D_CORE` on exactly paired eligible OOF
identities. Do not rank unlike families to declare a winner.

The primary metric is condition-balanced MAE in SoH percentage points: compute MAE within each held
condition, then give each eligible condition equal weight. Secondary descriptive metrics are median
absolute error, RMSE, signed bias and R-squared. Paired `D_CORE - B` uncertainty uses a whole-condition
bootstrap; no row- or cell-fragment bootstrap is accepted. The bootstrap seed, replicate count and
interval quantiles must be frozen in the v2 configuration before `GO-B`.

Prediction intervals are unavailable in this milestone and must be serialized/displayed as null or
explicitly unavailable. They must not be inferred from bootstrap intervals on aggregate metrics.

All learned preprocessing and regularization are fitted inside the condition-grouped training fold.
There is no separate supervised variable-selection step. `Q0`, current SoH, eligible past history,
exposure and declared conditions stay in their specified feature views unless Wave A identifies a
scientific validity or leakage violation.

## Product contract: condition-held-out OOF replay

The default interface replays the real histories of all 228 KIT cells. It is not a battery physics
simulation, a digital twin or a live deployment. At each historical check-up, the interface shows
only information that would have been available then and, when available, a prediction made by a
condition-held-out OOF model. Future H3 ground truth remains hidden until replay time reaches that
measurement.

Navigation by check-up index is allowed for stepping through the record, but check-up number is not
presented as physical time. The interface always shows the source-backed exposure relevant to the
ageing mode:

- calendar: real elapsed days;
- cyclic: equivalent full cycles (EFC), with elapsed days retained;
- driving profile: source-backed distance in kilometres when traceable, plus EFC/elapsed days as
  available; otherwise distance is explicitly `not available` and is never inferred.

The detailed interaction and claim boundary are frozen in
`docs/product/kit-oof-replay-ui-spec-v1.md`.

## Explicit exclusions

This design does not include or authorize:

- multiclass labels;
- joint multitask training;
- RUL or survival prediction;
- formal alert, maintenance, replacement or safety thresholds;
- a model winner/champion, ensemble, protected-test result or performance claim;
- a physical battery simulation or invented future trajectory;
- thermal-runaway, fire, internal-short or general failure prediction;
- claimed maintenance benefit, economic benefit or deployment validity.

## Supersession and implementation status

This decision supersedes conflicting primary-outcome, RUL, model-metric and action-policy language in
the earlier draft protocol and in
`docs/decisions/early-warning-robustness-design-v1.md`. The earlier record's acquisition aggregation,
hierarchical weighting, bounded runtime and fail-closed ideas may be reused only after they are shown
to fit the F1 regression target and pass a new review. Its fixed Logistic/HGB classification packet,
PR-AUC/Brier primary metric and class-support preflight are not the current primary design.

The current v2 task registry, schemas, scientific engine and runtime artifacts are aligned with this
frozen F1 paired-development decision. O2 remains disabled and produced no model outputs.

The strict v2 schema must use the exact v2 run identity, reject unknown/missing/type-invalid fields,
reject non-finite numeric values and persist the ordered artifact allowlist in
`engine_summary.json.artifact_paths`. Runtime validation must independently recompute target and
derived-future arithmetic, reconcile snapshot/fold/prediction identities and counts across all
artifacts, verify paired `B`/`D_CORE` membership within each model family, and prove zero O2 model
outputs. Filename agreement or callable-return metadata alone is insufficient.

The bounded paired development run has completed and passed independent artifact reconstruction for
development display only. The `Accepted KIT OOF` UI mode remains disabled. Model selection,
protected/external evaluation, publication and deployment remain separate blocked gates. All formal,
scientific, protected-test and publication flags remain false; the completed validator does not turn
development OOF into a formal scientific result.
