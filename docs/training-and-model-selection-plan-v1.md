# Training and model-selection plan v1

Status: `draft — planning only`

This document does **not** authorize formal training, access to the fixed-test labels, a scientific
result, or a resume claim. Every run produced before the authorization gates below remains an
engineering result with `scientific_result=false` and `resume_approved=false`.

Baseline reviewed commit: `4cfc405` (`Build clean public EIS capacity benchmark foundation`). The
working tree was clean when this plan was prepared. A later authorized run must record and review
its own commit and working-tree state; this statement is not a reusable authorization.

## 1. Objective and claim definitions

### Scientific objective

Estimate absolute discharge capacity in mAh for a physical 25 °C cell absent from fitting, using one
state-V EIS spectrum. Report SoH as predicted capacity divided by that cell's known cycle-0 discharge
capacity. The primary score is cell-macro SoH MAE. Secondary scores are cell-macro SoH RMSE, pooled
capacity MAE/RMSE, pooled SoH MAE/RMSE, each cell's errors, and probabilistic interval coverage.

The benchmark asks whether EIS adds predictive value beyond all required non-EIS baselines:

1. the training-set mean-capacity predictor; and
2. an age-only polynomial Ridge predictor using current diagnostic cycle, with no EIS features; and
3. an age-only one-dimensional GPR given the same optimizer budget as the EIS GPR.

If no EIS model clears both baselines during training-cell-only nested evaluation, the correct result
is **“EIS improvement not demonstrated under protocol v1.”** A model is never promoted merely
because it is the least-bad EIS candidate.

### `match` and `surpass`

There are three different comparisons, and their labels must not be mixed:

- **Paper performance match/surpass:** not defined for v1. The article does not publish an aggregate
  metric with the same cleaned-source capacity protocol, and its packaged arrays have documented
  cell-target swaps and truncation. V1 therefore cannot claim a numerical reproduction of, match to,
  or improvement over the paper.
- **Method reference:** the paper-aligned zero-mean full-ARD GPR is a reference implementation of the
  reported model family on v1 data, not a paper replay and not proof of reproduced performance.
- **Single-model selection — the only rule:** first apply the eligibility and strongest-age-baseline
  gates in Section 7. Then, starting from the lowest-complexity eligible model, compare a complex
  candidate `c` with the simpler candidate `s` using paired outer-OOF per-cell differences
  `d_i = MAE_s,i - MAE_c,i` for the four training cells and
  `SE_diff = sd(d_i) / sqrt(4)`. Select `c` only when `mean(d_i) > SE_diff`; otherwise retain `s`.
  There is no fixed `0.002` tie margin for single-model selection: later `0.002` thresholds govern RF
  seed stability or ensemble entry only and cannot replace this paired one-SE rule.
- **Cleaned-benchmark non-inferior:** relative to the frozen same-data `P0` paper-method reference,
  `delta = MAE_candidate - MAE_P0` must be at most `+0.010` SoH and no cell delta may exceed `+0.020`.
- **Cleaned-benchmark match:** `abs(delta) <= 0.005` SoH and every cell's absolute MAE delta is at most
  `0.010`.
- **Cleaned-benchmark surpass:** `delta <= -max(0.005, 0.05 * MAE_P0)`, at least three of four test
  cells are non-worse, and the worst per-cell delta is at most `+0.005` SoH.

These operational margins are project governance thresholds, not paper-specified statistical tests.
They require protocol review before test access, may not be relaxed after results are seen, and do not
authorize a resume claim. Paired cell/block bootstrap intervals may be reported as descriptive
uncertainty, but four test cells cannot support a strong population claim.

The paper's only explicit 25 °C capacity anchor is the Figure 1a `25C05` state-V `R²=0.88`; it is one
panel, not a four-cell aggregate. Until the paper's R² definition and a frozen replay are verified,
report both `1-SSE/SST` and squared correlation only as diagnostics. A rounding band of
`[0.875, 0.885)` may describe numerical agreement with that panel only; it cannot establish an
overall paper match or guide model selection.

## 2. Benchmark identity and track separation

### Cleaned benchmark — the only v1 training track

- Source: checksum-verified Zenodo record `3633835`, state V only.
- Join: EIS cycle `k` to capacity diagnostic cycle `k-1`.
- Target: absolute discharge capacity in mAh; no target scaling for the paper-aligned GPR.
- SoH reporting denominator: the same cell's explicit cycle-0 capacity. For test cells this is locked
  label material, available only to the authorized evaluator and never a model input.
- Primary capacity range: all otherwise valid aligned rows. The paper uses 80% EOL for RUL and
  experimental termination but does not document first-crossing truncation for the capacity model.
- Sensitivity only: an EOL-bounded evaluation may retain each cell through its first observed SoH at
  or below `0.80`, including the crossing. The mask is created by the authorized evaluator after test
  unlock and cannot affect model/feature selection.
- Identity: preserve source-file cell identities and all otherwise valid aligned rows.

This primary-range decision conflicts with the current draft `docs/protocol-v1.md`, which makes the
project-specific EOL mask part of its capacity data boundary. Formal training is blocked until the
protocol owner resolves that conflict, all documents agree, and an independent reviewer signs the
new protocol hash.

### Paper replay — excluded from v1

A future paper replay would intentionally reproduce the authors' packaged arrays, including explicit
decisions about the apparent `25C02`/`25C03` target swap and cell-specific truncation. It must use a
separate protocol version, dataset and split hashes, output namespace, manifests, tables, and claim
language. No replay row, metric, fitted object, or selection decision may be combined with the
cleaned benchmark.

## 3. Data partitions and leakage controls

### Fixed 25 °C track

| Partition | Cells | Current all-aligned rows | EOL sensitivity rows | Permitted use |
|---|---|---:|---:|---|
| Selection/train | `25C01`–`25C04` | 679 | 243 | Nested selection, final fit |
| Fixed test | `25C05`–`25C08` | 664 | 172 | One authorized batch evaluation only |

The fixed-test metrics were already exposed during engineering smokes. Protocol v1 can prevent new
test-directed tuning, but cannot retroactively restore a pristine blind test. The final report must
call the fixed test **historically exposed, prospectively rule-locked** and must not call it unseen,
untouched, or blind.

Current `evaluate_paper_split` loads all labels in one process. That interface is **not eligible** for
formal agentic execution. Before authorization, implementation work must create:

1. a selection view that exposes labels only for `25C01`–`25C04`; and
2. a separately permissioned evaluator that can read `25C05`–`25C08` labels only after a signed test
   authorization and records exactly one label-read event.

A prompt telling an agent not to inspect test labels is not a test seal.

### Training-cell nested evaluation

Use four outer leave-one-cell-out folds. Within each outer fold, tune on the other three cells using
three inner leave-one-cell-out folds. Fit scaling, polynomial construction, PCA, kernel
hyperparameters, and any ensemble weights only inside the relevant training fold.

Pool predictions from the four outer held-cell folds and calculate the primary cell-macro SoH MAE.
For every complexity comparison, retain the four paired per-cell MAE differences and calculate
`SE_diff = sd(d_i) / sqrt(4)`; do not use unpaired model-wise standard errors. Also report cell-macro
RMSE, per-cell metrics, capacity MAE, and GPR interval coverage. Four groups are too few for a strong
confidence claim; this paired standard error is used only for the preregistered simplicity rule.

After selection, rerun inner LOGO over all four training cells to choose final hyperparameters, then
fit once on all four. The fixed test cannot select a model, tune a parameter, choose a seed, set an
ensemble weight, or decide which results to report.

### Secondary grouped robustness track

Leave-one-cell-out analysis across all 12 cells is secondary and must have its own frozen analysis
record. Temperature is a domain variable, not an interchangeable row group. Results must be reported
by held cell and temperature. This track cannot alter the fixed 25 °C winner or be pooled with its
test score.

## 4. Feature engineering

Feature families are part of the model definition. Their formulas, input frequency order, numerical
guards and selector grids must be serialized in `feature_registry.json` before search authorization.
No test feature or label may be used to choose a frequency, summary, component count, circuit, bound
or transform. Unsupervised does not mean leakage-safe: PCA and any statistics still fit on the fold's
training cells only.

### Paper feature set

`F0-paper-raw` is the only paper-aligned feature set: 60 `Re(Z)` values followed by 60 source
`-Im(Z)` values, ordered from high to low frequency across the fixed 0.02 Hz–20 kHz grid. It has
exactly 120 columns. A training-fold `StandardScaler` transforms train/validation/test with the same
learned values. The paper says training-data mean/standard-deviation normalization, but the exact
25 °C implementation and degrees-of-freedom convention are unavailable. The cleaned track therefore
freezes a consistent `ddof=0` convention; a future literal replay must reproduce and label its own
script semantics. `F0` never includes PCA, frequency selection, handcrafted summaries or test-cell
normalization.

### Extended feature track

All extended sets are explicitly non-paper features and compete only through the nested training-cell
selection process:

| ID | Representation | Required freeze/review |
|---|---|---|
| `F1-polar` | Per-frequency magnitude and phase derived from the same 60 complex values | Freeze sign/phase convention, zero guard and exact 120-column order |
| `F2-logfreq-summary` | Preregistered band/slope/area summaries on the fixed log-frequency grid | Freeze exact formulas, bands, interpolation and missing/failure behavior before any data-driven comparison |
| `F3-lowfreq` | A fixed literature-prior pair near 17.80/2.16 Hz as secondary, or a bounded low-frequency subset grid | Verify frequencies exist; any subset/count choice occurs inner-fold only; exhaustive pair search is forbidden |
| `F4-pca` | Deterministic full-SVD PCA of `F0`, components `{2,4,8,16}` | Fit scaler and PCA inside each fold; store explained variance and component hashes |
| `F5-circuit` | Optional equivalent-circuit parameters | Blocked until topology, solver, bounds, initialization, identifiability, non-fit policy and units receive separate review |

Magnitude/phase conversion is deterministic, but all downstream centering, scaling, summary selection
and model choice remain fold-local. Equivalent-circuit fit failures must be retained as failures; they
cannot be imputed or repaired using held-cell behavior without a new protocol. Extended feature
results must be reported separately from `F0-paper-raw` and cannot be called paper replay.

The feature review gate must verify: exact 60-frequency identity/order; `Re/-Im` sign convention;
train-only fit scope; deterministic output schema; no target/cycle-0/EOL leakage; identical pipeline
serialization at final fit; and an explicit multiplicity budget across feature/model combinations.

## 5. Candidate registry

All candidates, grids and seeds must be serialized and hashed in `search_space.json` and
`seed_registry.json` before search authorization. Unsupported or failed configurations remain in the
trial ledger; they are not silently replaced.

| ID | Candidate | Frozen search | Role |
|---|---|---|---|
| `B0` | Training-capacity mean | none | Required baseline |
| `B1` | Age-only polynomial Ridge | degree `{1,2}`; Ridge alpha `10^-6` through `10^6` in 13 decade steps | Required non-EIS baseline |
| `B2` | Age-only 1-D GPR | Same registered optimizer-start and fit budget policy as EIS GPR; current cycle only | Strong required age baseline |
| `E1` | EIS Ridge | Ridge alpha `10^-6` through `10^6` in 13 decade steps | Linear EIS baseline |
| `E2` | EIS PLS | components `{1,2,3,5,8,10,15,20,30,40}`, clipped below fold sample/rank limits | Latent linear baseline |
| `P0` | Paper-aligned GPR-ARD | `StandardScaler(X)`; `Constant*RBF(120-D ARD)+WhiteKernel`; bounds below; optimizer seeds `{17,43,101}`; `normalize_y=false` | Paper method-family reference |
| `M0` | Stabilized full-ARD GPR | `F0`; same family as `P0`, but train-fold `normalize_y=true` | Non-paper numerical candidate |
| `M1` | PCA-GPR | `F4`; kernel `{RBF, Matérn-3/2}`; seeds `{17,43,101}`; `normalize_y=true` | Reduced modern candidate |
| `S0` | Random forest | 400 trees; `max_features={0.25,0.5,1.0}`; `min_samples_leaf={2,5,10}`; `max_depth={None,8}`; seeds `{17,43,101}` | Supporting nonlinear sensitivity |
| `S1` | Extended-feature models | Only reviewed `F1`–`F3` representations with a separately capped Ridge/GPR registry | Feature sensitivity; not paper-aligned |
| `D0` | EIS+age additive GPR | Separately registered EIS and age components | Incremental diagnostic only; cannot replace the EIS-only primary question |

For `P0`, the paper establishes zero mean, Gaussian likelihood and squared-exponential ARD with 120
length scales plus a signal scale. Its exact 25 °C optimizer, initialization, restarts, tolerance,
jitter, stopping rule, preprocessing degrees of freedom and R² implementation are **unknown**. The
project's operational mapping (`ConstantKernel*RBF + WhiteKernel`), initial values, bounds, fixed
numerical jitter and 300-iteration/20-minute limits must be serialized as benchmark choices and must
not be attributed to the paper. The formal registry must record every value. `M0`/`M1` are distinct
modern candidates and must not be described as reproductions.

Neural networks, CNNs and Transformers are excluded from v1 because four training groups cannot
support fair architecture and early-stopping selection. New candidates require a new reviewed
protocol version before any test access.

The fixed seed registry is `{17,43,101}`. Deterministic models do not pretend to have seed replicates.
RF uses seed 17 during search; 43/101 audit only the finalist, and OOF seed standard deviation above
`0.002` SoH makes it ineligible. GPR seeds are optimizer initializations: within a fold choose the
valid solution with highest training log marginal likelihood, never the seed with best validation
score. Final seed/solution aggregation must be frozen before test unlock.

## 6. Training procedure and resource budget

### Fit sequence

1. Verify source archive checksums, the exact 12-cell/60-frequency inventory, 2,597 valid aligned
   samples, 120 features, and unique `(cell, EIS cycle)` identities.
2. Verify dataset-row, training primary/sensitivity identities, frozen test-mask algorithms,
   train-view, feature, protocol, search-space, seed, dependency-lock and Git hashes against the
   authorization record. Test row/mask identities do not exist before test unlock.
3. Execute the complete registered nested LOGO ledger. Each trial emits start/end events, fold IDs,
   resource telemetry, warnings, fold-local transform hashes and predictions.
4. Produce one immutable OOF table containing every outer-held training-cell prediction. Recompute
   metrics from that table; do not accept only console summaries.
5. Apply the frozen baseline, 1-SE, complexity and optional ensemble gates. An independent reviewer,
   not the runner, signs the candidate decision.
6. After separate final-fit authorization, retune within the four training cells and fit the signed
   winner on all four cells.
7. After separate test authorization, the isolated evaluator reads the fixed-test labels and cycle-0
   denominators once and evaluates the required batch: `B0`, strongest of `B1`/`B2`, `P0`, and the one
   selected-modern model if different. It then applies the preregistered EOL sensitivity mask.
8. Freeze predictions and metrics before any narrative interpretation.

### Resource limits on the 16-core/31 GiB reference host

| Scope | Expected budget | Hard action |
|---|---|---|
| Linear/PLS nested ledger | 5–10 min, under 1 GiB RSS | Stop at applicable global cap |
| Random forest ledger | 20–40 min, under 4 GiB RSS | Stop at applicable global cap |
| Full 120-D ARD GPR | 0-restart smoke first; preliminary 243-row estimate 0.5–4 h | 20 min per fit; 300 optimizer iterations |
| PCA-GPR ledger | expected 1–2 h | 20 min per fit; 300 optimizer iterations |
| Complete authorized search | hard cap 6 h wall time, 24 CPU-hours, 8 GiB peak RSS | Terminate cleanly and request review |

At most four fits may run concurrently. Runtime estimates are medium-confidence planning values based
on the earlier 243-row EOL-bounded design; the 679-row all-aligned primary may exceed them. The smoke
must calibrate the ledger against the hard cap, and exceeding it requires plan review—not automatic
budget growth. No formal GPR runtime has yet been measured. The zero-restart GPR smoke is operational only:
it may validate memory, telemetry and failure handling, but cannot select hyperparameters or become a
scientific result.

## 7. Failure handling and model selection

### Fit failure rules

Mark a registered fit `failed` when it produces non-finite inputs/predictions/objectives, a Cholesky
failure, timeout, resource-limit violation, or an optimizer that fails to produce a valid result.
Record convergence warnings and kernel-bound hits. Consecutive boundary hits across the three seeds
trigger review; they do not permit an unregistered bound expansion. Do not silently change jitter,
bounds, iteration limits, features, seeds or grids. A retry may repeat the identical registered trial
after an infrastructure fault; a scientific change requires a new plan version and authorization.

### Single-model selection

1. A candidate is eligible only with complete outer-OOF predictions and valid artifacts.
2. An EIS candidate must beat `B0` and the stronger of `B1`/`B2`; to be promotable it must beat the
   stronger age baseline by at least `0.005` SoH and may not worsen any held cell by more than `0.010`.
3. Rank eligible candidates by that primary score.
4. Starting from the lowest-complexity eligible model, compare a more complex candidate using the four
   paired outer-cell differences `d_i = MAE_simple,i - MAE_complex,i` and
   `SE_diff = sd(d_i) / sqrt(4)`. Select the complex model only if `mean(d_i) > SE_diff`; otherwise keep
   the simpler/more paper-aligned model. Complexity order is mean, linear age, nonlinear age/EIS
   Ridge, PLS/RF, PCA-GPR, full-ARD GPR, ensemble.
5. Use macro RMSE, each held-cell result, capacity MAE, calibration and warnings as review diagnostics,
   never as post-hoc replacements for the primary rule.
6. The test evaluator reports the signed candidate; it does not choose another winner if the result
   is disappointing.

## 8. Ensemble rule

No ensemble is promoted by default in v1. A preregistered exploratory ensemble may advance only if:

- at least three base models have complete, same-target, same-split outer-OOF predictions;
- at least two EIS base models beat all required non-EIS baselines;
- pairwise OOF error correlation among included models is below `0.95`;
- nonnegative, sum-to-one weights are learned inside each outer fold from inner OOF predictions;
- final weights are learned only from four-training-cell LOGO OOF predictions;
- the ensemble itself beats the strongest age-only baseline on outer-OOF cell-macro MAE by at least
  `0.005` SoH, with no held cell worsening by more than `0.010` SoH relative to that baseline;
- raw outer-OOF cell-macro MAE beats the best single model by at least 5% relative and `0.002` SoH;
- no held training cell's MAE worsens by more than 10%; and
- weights are stable across folds with effective model count `1/sum(w^2) >= 1.5`.

For selection, report both raw macro MAE and the preregistered complexity score
`macro_MAE + 0.001 * (active_models - 1)`. The raw-improvement gate still controls eligibility.
Test-derived weights, incomplete OOF predictions, only two or three usable training groups, weight
collapse, or bases trained on different targets/splits forbid ensemble promotion.

## 9. Agentic process and separation of duties

The required process is:

`Planner → Independent reviewer → Human authorizer → Authorized runner → Monitor → Independent result reviewer`

The lifecycle is:

`PROTOCOL_DRAFT → HUMAN_REVIEWED → DATA_AUDITED → SPLIT_SEALED → SEARCH_AUTHORIZED → TRAINING → TRAIN_REVIEW → FINAL_FIT_AUTHORIZED → FINAL_FITTED → TEST_AUTHORIZED → TEST_EVALUATED → INDEPENDENT_REVIEW → PROMOTED or HALTED`

No agent may authorize its own work. The planner writes the plan and registry; the independent
reviewer checks them; a human records authorization; the runner executes only the signed registry;
the monitor can stop or flag a run but cannot change it; and an independent result reviewer recomputes
and adjudicates the evidence.

Agents may execute a hashed trial, retry the identical trial after an operational fault, append
telemetry, summarize training/validation OOF results, and file an incident or review request. Agents
may not read locked-test labels before authorization, change the protocol/search/seeds/metric/budget,
add candidates, self-pass a gate, unlock a test, rewrite a selection after seeing outcomes, or announce
scientific/resume promotion. Human authorization must be an explicit artifact, not inferred from
silence or a chat continuation.

## 10. Monitoring and stop/fail gates

The monitor records run/fold/trial IDs, timestamps, heartbeat, process exit, elapsed and CPU time,
peak RSS, concurrency, optimizer iterations, objective, convergence warnings, kernel bounds,
non-finite counts, retries and artifact hashes. It may expose operational state and training-only OOF
state. It must not expose fixed-test labels or metrics to the selection process.

Stop immediately and mark the affected stage `HALTED` when any of the following occurs:

- source, dataset, partition, primary/sensitivity mask, feature, protocol, registry, seed, lockfile or
  commit hash mismatch;
- dirty working tree, uncommitted runner code, wrong environment, or an unauthorized dependency;
- unexpected cell/row/frequency/feature count or order, `Re/-Im` sign error, duplicate identity, join
  failure, missing cycle-0 denominator, cell leakage, test-guided frequency/feature choice, or a
  transform fitted outside its fold;
- attempted test-label access before `TEST_AUTHORIZED`, more than one fixed-test label read, or use of
  test output in model selection;
- an unregistered trial, seed, retry, hyperparameter, candidate, ensemble member or resource increase;
- non-finite output, corrupt/missing prediction rows, inconsistent metric recomputation or hash;
- per-fit/global wall, CPU, RSS or concurrency limit breach;
- repeated optimizer/kernel-bound failure requiring a scientific rather than identical retry; or
- any actor self-authorizing, changing a rule after outcomes, or promoting a claim without review.

Halted work remains evidence of a failed/invalid attempt and is never overwritten. Recovery requires
an incident report, a new immutable output directory, and approval at the earliest invalidated gate.

## 11. Artifacts and review gates

### Required immutable artifacts

- `protocol_lock.json`, `feature_registry.json`, `search_space.json`, `seed_registry.json`,
  `unknowns_register.json`, `resource_budget.json`;
- source manifest/checksums, dataset/audit report, training-row identities, frozen primary/EOL-mask
  algorithm hashes and split seal; post-unlock test row/mask identities belong to the test bundle;
- Git commit/status, environment and dependency-lock hashes;
- authorization records and role/signature/timestamp for every transition;
- trial registry, append-only `events.jsonl`, telemetry, warnings, failures and identical-retry links;
- fold-local scaler/PCA/feature-selector/circuit-fit configuration, outputs and hashes;
- all OOF and fixed-test predictions with row identities;
- selection report, 1-SE calculation, baseline gate and signed candidate decision;
- final-fit manifest, fitted-object hash and exact feature schema;
- test authorization and append-only test access log with `label_reads=1`;
- recomputable pooled, macro, per-cell and coverage metrics plus kernel parameters/bound hits;
- independent review, promotion decision and claim text.

### Plan → Review → Authorize → Execute → Review

1. **Plan:** freeze this protocol, source/split decisions, candidate registry, resources, selection and
   claim boundaries.
2. **Review:** an independent scientific reviewer checks leakage, comparability, feature families and
   fit scope, grids, paper-parameter unknowns, budgets, implementation readiness and artifact schema.
   Findings must be resolved or accepted in writing.
3. **Authorize:** a human names the exact hashes, runner identity, budget and permitted next state.
   Search, final fit and test evaluation require separate authorization records.
4. **Execute:** the runner performs only the authorized state transition while the monitor records the
   append-only evidence trail and enforces stop gates.
5. **Review:** an independent result reviewer verifies hashes, recomputes metrics from predictions,
   checks every failed trial and selection rule, and writes an accept/reject decision.

### Scientific and resume claim gates

`scientific_result` may become `true` only in a new reviewed promotion artifact after all stages pass.
Existing run manifests are not edited in place. A fixed-test score with only four cells must retain
its descriptive limitation and the historical-exposure disclosure.

`resume_approved` is a separate, later human decision. It requires the accepted immutable scientific
artifact, exact scoped wording, evidence that the wording does not imply paper reproduction or an
Argonne affiliation, and an independent claim review. Scientific approval does not imply resume
approval. Until that record exists, every artifact and document must retain
`resume_approved=false`.

## 12. Implementation phases and verification

### Phase A — sealable infrastructure

First reconcile `docs/protocol-v1.md` with the all-aligned capacity primary/EOL sensitivity decision.
Then implement the selection-only data view, permission-separated test evaluator, append-only test
access log, feature pipelines, frozen registries and manifest fields. Unit tests must prove that the
selection process cannot load test labels, every transform/selection is fold-local, and metric
recomputation matches stored predictions.

### Phase B — instrumented non-scientific smokes

Run synthetic/engineering-only tests for nested LOGO, registered failure/retry behavior, the GPR
zero-restart resource smoke and artifact hashes. Every manifest remains false/false for scientific and
resume flags.

### Phase C — independent review and human authorization

Review code, tests, hashes, protocol and budget. No formal fitting starts until a human signs the
exact search authorization.

### Phase D — authorized selection, final fit and test

Execute each lifecycle transition separately. A disappointing training result is reportable and does
not justify rule changes. The test is one batch after a second authorization.

### Phase E — evidence and claim review

Recompute all metrics, audit the access/event logs and publish either an accepted descriptive result
or a halted/rejected record. Resume wording is reviewed only after scientific acceptance.

End-to-end verification must include unit tests, lint, checksum/data audit, split/leakage tests,
deterministic registry enumeration, resource-stop injection, artifact-hash recomputation, test-seal
denial before authorization, exactly-one-read enforcement, raw-feature order/sign tests,
test-guided-frequency-selection denial, independent metric recomputation, and a clean Git status.
Passing existing smoke tests alone is insufficient.

## 13. Anti-pattern guards

- Do not use test results to tune, select, choose seeds, create plots selectively or revise claims.
- Do not label the cleaned benchmark a reproduction of the packaged arrays.
- Do not fit scalers/PCA/selectors on all cells, use test features for unsupervised design, perform
  test-guided frequency selection, or treat spectra as independent batteries.
- Do not call magnitude/phase, log-frequency, low-frequency, PCA or circuit features paper-aligned.
- Do not omit failed trials, warnings, weak cells, negative results or historical test exposure.
- Do not silently widen a kernel bound, add jitter, enlarge the budget or replace a failed candidate.
- Do not promote a complex model inside the 1-SE set when a simpler eligible model exists.
- Do not let one agent plan, authorize, execute and approve the same transition.
- Do not mutate a run directory or flip approval booleans in an old manifest.

## 14. Evidence base and open uncertainties

Local evidence is recorded in `README.md`, `docs/data-card.md`, `docs/protocol-v1.md`,
`docs/diagnostic-log.md` and `docs/source-anomalies.md`. External method references are Zhang et al.
(2020), [Nature Communications](https://doi.org/10.1038/s41467-020-15235-7), and Gasper et al.
(2022), [Cell Reports Physical Science](https://doi.org/10.1016/j.xcrp.2022.101184). The latter
supports strict model/feature comparisons and RF/GPR/linear families; its reported ensemble result
uses different data and conditions and does not supply a transferable threshold for v1.

The [official supplement](https://static-content.springer.com/esm/art%3A10.1038%2Fs41467-020-15235-7/MediaObjects/41467_2020_15235_MOESM2_ESM.pdf)
and [authors' code repository](https://github.com/YunweiZhang/ML-identify-battery-degradation) are
diagnostic sources; the repository has no displayed software license, so code is inspected but not
copied. Supplementary RUL RMSE values must never be used as capacity thresholds.

Design confidence is high for paper task/split/raw features/target/model family, local `k-1` alignment,
leakage controls, nested grouping and claim separation; medium for runtime; and low/unknown for exact
25 °C preprocessing, optimizer, jitter, stopping, R² and direct paper-performance comparability. Open
uncertainties include the unmeasured all-row GPR runtime, optimizer stability with only four training
cells, unstable ensemble weights, and the absence of a paper metric with identical aggregation. These
limitations must remain visible in the final report.
