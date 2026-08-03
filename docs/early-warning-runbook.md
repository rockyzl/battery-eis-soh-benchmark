# Early-warning implementation and training runbook

Status: `paired B/D_CORE development OOF complete; no O2, selection, protected test, publication or deployment`

Current evidence (2026-08-02): 54/54 planned paired fits completed on 2,919 identical eligible
snapshots, producing 23,352 OOF prediction rows. O2 fit/prediction counts are zero.

Unified limitation: development OOF only; no O2, model selection, protected test, publication or
deployment. The observed EIS contrasts are mixed and do not establish a universal winner or causal
EIS effect. KIT is the only active project; CALCE is stopped and Cambridge is frozen historical
material.

This runbook governs the KIT-based Battery Degradation Early Warning and Maintenance Decision System.
It does not replace the historical Cambridge SoH-v2 protocol, and a Cambridge engineering artifact
cannot be promoted into this lineage.

## Product/science contract

At a completed diagnostic check-up, the system should eventually produce:

```text
measurements available now
  -> risk assessment
  -> future capacity/RUL under a declared scenario
  -> evidence and uncertainty
  -> deterministic reviewable action
```

The primary research question is whether EIS improves future-degradation warning beyond initial
capacity, capacity history, elapsed exposure and known operating conditions. Predicting current SoH
alone is a measurement-reconstruction task and is not the flagship outcome.

Maintenance tiers are decision support, not autonomous commands:

| Tier | Intended meaning | Example reviewable action |
|---|---|---|
| Green | No registered warning trigger | Continue under the declared monitoring plan |
| Yellow | Uncertainty or early deviation requires more evidence | Shorten check interval or repeat a diagnostic |
| Orange | Registered degradation-risk trigger persists | Derate or inspect conditions and plan maintenance |
| Red | Confirmed high-risk/end-of-life criterion | Human decision on service or replacement |

Thresholds, confirmation counts and action wording must be frozen before formal evaluation. They
cannot be chosen after observing protected test results.

## Gate 0 — plan and freeze the scientific contract

Required evidence:

- prediction timepoint and information available at that time;
- physical-cell unit of independence;
- fixed forecast horizons and future-use scenarios;
- outcome definitions for rapid degradation, threshold crossing and matched-peer deviation;
- right-censoring treatment;
- model matrix, variable-selection rules, ensemble eligibility and evaluation metrics;
- deterministic action policy and human-confirmation boundary;
- formal train/test authorization roles.

Exit condition: an independent reviewer accepts the protocol and task registry by content hash.

## Gate 1 — implement and audit the data boundary

Required work:

1. obtain the official version 2 result archive through the recorded source;
2. record source filenames, bytes, checksums, license and extraction manifest;
3. identify the 228 experimental cells separately from system/test channels;
4. map every cell to one of the 76 conditions and its replicate index;
5. reproduce capacity, EIS and pulse inventories and missingness summaries;
6. freeze the conventional `Q0` and SoH rules;
7. align each snapshot using only data available through its check-up;
8. emit counts at the cell, condition, check-up, spectrum and frequency-row levels;
9. reject duplicate identities, future joins and silent imputation.

The strict local audit summarized in [`data-card-kit-v2.md`](data-card-kit-v2.md) now reproduces 228
cells, 3,999 capacity endpoints, 40,615 spectra, 40,536 source-valid spectra and 3,993 source-valid
RT-50 spectra from checksum-identified archives. The RT-50 EIS/capacity alignment passed its separate
readiness review and the governed paired B/D_CORE development run subsequently completed on the
identical 2,919 eligible snapshots. This does not authorize selection or protected evaluation.

Exit condition: data audit, leakage tests and source manifest pass independent review.

## Gate 2 — implement bounded snapshots, labels and actions

Build one immutable row per `(physical cell, prediction check-up, registered task)` with:

- predeclared history available at the prediction time;
- outcome window that begins strictly after that time;
- censoring status and observation limit;
- group identifiers needed for cell- and condition-held-out evaluation;
- source lineage for every derived feature and target.

Implement deterministic label and action rules before model training. Synthetic fixtures must cover
horizon boundaries, censored cells, replicate-peer construction, missing EIS contexts and a cell whose
degradation accelerates after the prediction time.

Exit condition: unit and integration tests demonstrate no future leakage and reproducible outputs.

## Gate 3 — deterministic synthetic contract demo

The implemented `early-warning-contract-demo` CLI is a **synthetic contract demo**. The deprecated
`early-warning-smoke` alias prints an explicit warning and does not change the scientific status. The
CLI executes only this narrow path:

1. construct an explicit synthetic `Q0` and four completed check-ups;
2. calculate conventional SoH at the prediction check-up;
3. build a capacity-threshold event label over the next three check-ups; and
4. map synthetic warning inputs through an explicitly provisional deterministic action policy whose
   output requires human review.

Its output carries negative scientific/publication/resume status flags. The unit-test suite separately
checks past-only history features, rapid-degradation labels, conditional-RUL censoring, peer-reference
roles, grouped splits and engineering metrics. Those contracts are **not run by this CLI command** and
must not be listed as demo outputs.

It does **not** parse KIT data, train a model, validate a real backend, exercise protected labels or
satisfy the formal monitored-smoke gate in the protocol. The historical CLI command name must not be
used to imply otherwise. The demo is not a scientific result and must retain:

```text
scientific_result = false
formal_model_result = false
publication_ready = false
resume_approved = false
```

Exit condition: software QA accepts the contract implementation. This still does not authorize
formal training.

## Gate 4 — bounded real-backend development run

After the QA-accepted KIT parser/audit, verified snapshot alignment, grouped split registry and
protected-label boundary exist, run a bounded real-data development-only job. The paired B/D_CORE
run completed this gate on 2026-08-02. It verified:

- checksum/schema binding and deterministic snapshot counts;
- metadata-only condition-group split construction;
- fixed eligible baseline and regression prediction paths;
- immutable manifests, predictions, warnings and failures;
- heartbeat, fold/trial/seed progress, elapsed time, ETA and CPU/memory telemetry;
- no protected-label read.

This run is still not a protected or final scientific result. It performed no model selection.

Exit condition: software and scientific reviewers accept feasibility, leakage isolation and resource
observability.

## Gate 5 — authorize formal train-only model selection

Formal search remains blocked until a human authorization binds the exact source, code, lockfile,
protocol, task registry, split registry and resource budget.

Run matched model families and budgets across the registered feature views:

| View | Inputs | Scientific role |
|---|---|---|
| `A` | capacity history | Directly measured degradation-trajectory baseline |
| `B` | `A` + use/environment | Strong non-EIS baseline |
| `C` | EIS + use/environment, without capacity history | EIS-centric alternative |
| `D-core` | `B` + EIS | Primary incremental-EIS comparison |
| `D-full` | `D-core` + audited pulse/temperature/quality history | Auxiliary-diagnostic ablation |

The main evidence for “EIS adds value” is `D-core` versus `B`. A gain by `D-full` cannot be attributed
to EIS because it may come from pulse or quality data. `C` versus `B` compares two different
information strategies; it does not isolate incremental EIS value.

Within each view, progress from transparent baselines to eligible nonlinear or survival candidates.
An ensemble is considered only if the preregistered same-outcome out-of-fold diversity, stability,
calibration and improvement criteria all pass.

Variable selection, scaling, imputation, trajectory summaries and hyperparameters are fitted inside
each training fold. Progress must expose heartbeat, fold/trial/seed, elapsed time, ETA, warnings and
resource use. A failed or incomplete trial stays visible and cannot silently disappear from ranking.

Exit condition: independently recomputed training-cell results select and freeze one eligible model or
record that no candidate demonstrated incremental value.

## Gate 6 — authorize protected evaluation

Only after the winner and all reporting rules are frozen may the protected evaluator read held-out
cell/condition outcomes. Evaluation should report, as applicable:

- PR-AUC, recall at a frozen false-alert budget, false alarms and missed warnings;
- early-warning lead time and calibration;
- future-capacity trajectory error by horizon;
- interval/right-censor-aware RUL discrimination, calibration and coverage in the declared coordinate
  (days, equivalent full cycles or profile-derived use); uncensored-only RUL error is descriptive;
- per-cell, per-condition and ageing-mode results;
- incremental value of EIS versus the strongest capacity/history baseline.

R² may be a secondary regression diagnostic; it cannot by itself validate an early-warning system.
The test result cannot change a feature, threshold, model, seed, ensemble weight or reporting subset.

Exit condition: independent recomputation and scientific review accept or reject the result artifact.

## Gate 7 — approve product, article and resume claims

Publication approval is separate from software completion and scientific-result acceptance. A reviewer
must confirm that each claim has an immutable metric artifact, data license attribution, appropriate
uncertainty and explicit limitations.

Never claim:

- prediction of sudden catastrophic failure from capacity-ageing data;
- unconditional RUL independent of future use;
- that repeated spectra are independent batteries;
- a maintenance action was proven safe in deployment;
- authorship of KIT data or work performed at Argonne;
- improvement over published work unless the target, split and metric are demonstrably comparable.

## Operating order and stop rules

```text
Plan -> implement -> synthetic contract review -> real-data bounded training smoke
     -> scientific review -> human formal-training authorization
     -> monitored train-only selection -> independent selection review
     -> human protected-test authorization -> independent result review
     -> separate claim approval
```

Stop immediately when:

- a source checksum or schema does not match the frozen registry;
- a cell, condition or timepoint can cross a split boundary;
- future data enter a feature or normalization statistic;
- protected outcomes are read before authorization;
- a formal run lacks progress/resource visibility;
- a requested action would be inferred from an unreviewed or uncalibrated output.

## Current authorization state

- Protocol and F1 exact-H3 target: frozen.
- Official KIT result sources: recovered, checksum-bound and independently reviewed.
- Paired B/D_CORE real-data development OOF: completed; 54/54 fits, 2,919 identical eligible
  snapshots and 23,352 OOF prediction rows; approved only for bounded development interpretation.
- Interactive paired replay: implemented from the hash-bound OOF artifact.
- O2 fit/prediction counts: zero; O2 remains unauthorized.
- Model selection or promotion: **not performed**.
- Protected held-cell/held-condition evaluation: **not authorized**.
- Publication, deployment, maintenance-action and resume claims: **not authorized**.

Live owners, dependencies and evidence are tracked in
[`live-early-warning-tasks.md`](live-early-warning-tasks.md). No agent may advance a human-review gate
because code happens to pass.

## Paired-run recovery record — 2026-08-02

The durable artifacts from the first bounded `B` versus `D_CORE` paired attempt prove only that the
fixed ElasticNet candidate stopped fail-closed with `DevelopmentRunError`; the old worker discarded
the underlying reason. A forensic matrix audit and deterministic synthetic reproduction strongly
support convergence exhaustion, but do not retroactively prove that was the old run's exact cause.
The approved recovery changes only the numerical iteration budget from `5,000` to `250,000`; alpha,
l1 ratio, tolerance, selection rule, seed, features, folds, weights and preprocessing semantics remain
frozen. This is an execution-feasibility correction, not hyperparameter selection and not scientific
evidence.

Worker failures expose only allowlisted `error_type` and `error_code` values. Raw exception text,
tracebacks, paths and data are excluded from worker and supervisor artifacts. The approved code change
does not authorize a retry, formal training, protected-label access, model selection or publication.
