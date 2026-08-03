# Decision: development-only early-warning robustness design v1

Date: 2026-08-01

Status: `provisional development-only v1 engineering contract; not a source fact or formal scientific result`

Scope: KIT early-warning development lineage, EW-13 through EW-17

## Decision

Use one primary development snapshot per eligible physical cell and aligned completed check-up. Keep
all source-valid EIS acquisitions aligned to that check-up. At each frequency, take the equal
arithmetic mean over all finite repeated-acquisition values. If no acquisition has a finite value at a
frequency, leave that frequency missing for the fold-local missing-data path; do not synthesize it.
Preserve contributing acquisition identities, timestamps, source locators and counts. Emit exactly
one snapshot per check-up. Do not treat acquisitions or spectra as independent samples and do not
select an acquisition because it improves a model result.

Balance each eligible partition hierarchically without using labels:

```text
equal total mass per condition
  -> equal mass per physical cell within that condition
    -> equal mass per eligible check-up within that cell
```

For snapshot `i` in a partition, define the unnormalized weight as:

```text
w_i = 1 / (number of conditions
           * cells in i's condition
           * eligible check-ups for i's cell)
```

Normalize to mean weight 1 and record the normalization. Recompute weights inside the applicable
training, calibration or evaluation partition so outside groups cannot influence a fit. Use the same
eligible cohort and weights for paired `B` versus `D_CORE` comparisons.

Run deterministic keep-last acquisition selection only as a sensitivity analysis. It must share the
primary cohort, labels and hierarchical weights. Select the latest recorded acquisition timestamp and
break timestamp ties by stable source locator. This is an engineering ordering rule, not evidence of
the source's true retry semantics. Keep the result separately named; it cannot replace or rescue the
keep-all primary analysis.

Stop and prohibit an EIS-increment conclusion when keep-all versus keep-last causes any of:

1. a sign reversal of the paired condition-balanced `D_CORE - B` delta;
2. an absolute change greater than 0.02 in the paired condition-balanced PR-AUC delta; or
3. an absolute change greater than 0.01 in the paired condition-balanced Brier delta.

Apply these to paired `B` versus `D_CORE` deltas, not raw pooled PR-AUC alone. They are diagnostic stop
margins, not promotion or winner-selection margins.

Treat these fixed configurations as robustness candidates:

```text
Logistic: elastic-net; C=1.0; l1_ratio=0.5; solver=saga;
          max_iter=500; tol=1e-4; class_weight=None;
          random_state=20260801
HGB:      learning_rate=0.05; max_leaf_nodes=7; max_iter=10;
          min_samples_leaf=20; l2_regularization=1.0;
          early_stopping=False; class_weight=None;
          random_state=20260801
```

Use the same snapshots and hierarchical sample weights for Logistic/HGB and `B`/`D_CORE`. Fit a
two-parameter sigmoid (Platt calibration) to weighted, pooled inner grouped-OOF predictions from the
outer-training partition, then apply it to that outer fold's predictions. Fail closed if weighted
fitting or calibration is unsupported or has inadequate class/group support. Do not use outer-fold
outcomes for configuration or calibration, and do not select an engineering champion.

Use `condition_balanced_pooled_pr_auc_v1` as the versioned pooled discrimination metric. Compute
average precision over pooled OOF rows using the condition/cell/check-up weights above. Report event,
cell and condition support with the metric so the pooled value cannot hide unsupported groups.

Estimate the paired incremental EIS contrast with exactly 2,000 whole-condition bootstrap draws with
replacement. Use seed `20260801`, apply identical resamples to `B` and `D_CORE`, and report the 95%
percentile interval (2.5th and 97.5th percentiles) for `metric(D_CORE) - metric(B)`. Invalidate the
interval if fewer than 1,900 draws produce valid paired metrics. Report paired PR-AUC and Brier deltas.
Do not name an engineering champion; report model-family and feature-view robustness separately.

Before any model fit, run a development-only event-support preflight. For each ageing mode require at
least 8 condition groups supporting positives and at least 8 supporting negatives. Every outer and
inner validation fold must contain at least 2 positive-supporting and 2 negative-supporting condition
groups. Report support by mode, condition and physical cell and fail closed if any requirement is not
met. A condition supports a class when at least one eligible snapshot has that class; a condition may
support both classes. Do not merge modes merely to make a model runnable.

Begin a soft drain at 420 wall-clock seconds: launch no new fit, calibration or bootstrap unit, finish
only an in-flight bounded unit, and persist an incomplete status. Hard-stop the whole run at 480
seconds. Emit a heartbeat at intervals no greater than 15 seconds and treat more than 30 seconds
without a heartbeat as stale/failure. Use 1.6 GiB RSS as the soft-drain threshold and 2 GiB RSS as the
hard fail-closed limit. Record environment, heartbeat, resource and termination state. A drained,
timed-out, stale or memory-limited run is diagnostic and incomplete, never score-eligible.

## Deliberately unchanged boundary

Timestamp and completed-check-up semantics and the source's true retry semantics remain provisional.
This decision does not validate them. The numeric values above are frozen engineering choices for v1,
not facts reported by KIT. Every artifact must retain explicit provisional-timestamp and negative
result/claim flags until the source-semantic issues are resolved and reviewed.

## Rejected interpretations

- Do not expand multiple acquisitions, spectra or frequency rows into independent training samples.
- Do not make keep-last the primary analysis.
- Do not perform open-ended hyperparameter search or select a development engineering champion.
- Do not tune aggregation, weighting, calibration or metrics from outer-fold or protected outcomes.
- Do not read protected labels or call development OOF a fixed/protected test.
- Do not make formal scientific, operational, public, publication or resume claims.

## Implementation and acceptance dependencies

1. Implement and independently test equal-mean aggregation and deterministic keep-last sensitivity.
2. Implement and test hierarchical weights for fitting, Platt calibration and evaluation.
3. Replace tiny-grid selection with the fixed Logistic/HGB packets above.
4. Enforce event-support checks before fitting and inside every grouped validation plan.
5. Implement the versioned metric and the fixed 2,000-draw paired bootstrap contract.
6. Replace the 600-second engineering default with the 420/480-second and 1.6/2-GiB guards above.
7. Verify timestamp/completion and retry semantics separately. Until then, retain diagnostic-only
   status.

EW-10 formal training authorization, EW-11 protected evaluation and EW-12 claim approval remain
blocked regardless of whether EW-13 through EW-17 pass their engineering tests.
