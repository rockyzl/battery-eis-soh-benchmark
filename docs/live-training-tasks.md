# Live training tasks

Status: `planning only — formal training not authorized`

No task in `SEARCH_AUTHORIZED`, `TRAINING`, `FINAL_FIT_AUTHORIZED`, `TEST_AUTHORIZED`,
`SCIENTIFIC_APPROVED` or `RESUME_APPROVED` is active. Existing metrics are engineering diagnostics;
none is scientific or resume-approved.

Allowed status values: `done`, `in review`, `pending`, `blocked`, `authorized`, `running`, `failed`,
`accepted`, `rejected`. Only a named reviewer or human authorizer may change a gate status. The runner
may update execution tasks and the monitor may mark a task `failed`/`blocked`; neither may authorize
or accept its own output.

## Role separation

| Role | Responsibility | Prohibited |
|---|---|---|
| Planner | Draft protocol, registry, dependencies and acceptance criteria | Self-review or authorize |
| Independent scientific reviewer | Check data identity, leakage, model/search and claim logic | Run or approve own implementation |
| Human authorizer | Sign exact hashes, scope, budget and next lifecycle state | Blanket or retrospective authorization |
| Authorized runner | Execute only the signed registry/state transition | Read locked test labels, alter rules, self-authorize |
| Monitor | Record telemetry/events and enforce stop gates | Tune models or enlarge budget |
| Independent result reviewer | Recompute metrics and audit selection/access logs | Rewrite rules after seeing results |
| Resume claim approver | Approve exact claim wording after scientific acceptance | Infer approval from a good metric |

Required lifecycle:

`PROTOCOL_DRAFT → HUMAN_REVIEWED → DATA_AUDITED → SPLIT_SEALED → SEARCH_AUTHORIZED → TRAINING → TRAIN_REVIEW → FINAL_FIT_AUTHORIZED → FINAL_FITTED → TEST_AUTHORIZED → TEST_EVALUATED → INDEPENDENT_REVIEW → PROMOTED or HALTED`

## Structured task register

| ID | State/phase | Task | Owner | Status | Depends on | Required evidence / completion gate |
|---|---|---|---|---|---|---|
| `P-01` | `PROTOCOL_DRAFT` | Reconcile README, protocol, data card, diagnostic log and source anomalies | Planner | done | — | Reviewed commit `4cfc405`; EOL/capacity conflict recorded as blocker `P-06` |
| `P-02` | `PROTOCOL_DRAFT` | Define cleaned benchmark vs paper replay boundary | Planner | done | `P-01` | Separate identities/namespaces; v1 forbids paper-performance reproduction claim |
| `P-03` | `PROTOCOL_DRAFT` | Freeze objective, match/surpass, primary metric and baselines | Planner | in review | `P-01` | Plan hash; paper comparison marked unavailable; same-benchmark rules explicit |
| `P-04` | `PROTOCOL_DRAFT` | Freeze nested LOGO design, candidates, grids, seeds, ensemble and resources | Planner | in review | `P-03` | Search/seed/resource registry drafts match plan exactly |
| `P-05` | `PROTOCOL_DRAFT` | Freeze agent roles, state machine, stop gates and claim gates | Planner | in review | `P-03` | No self-authorization/test unlock/rule change path |
| `P-06` | `PROTOCOL_DRAFT` | Reconcile capacity primary range with EOL sensitivity | Protocol owner | blocked | `P-01` | `docs/protocol-v1.md` uses EOL truncation, but evidence review says paper capacity task does not; freeze all-aligned primary or justify alternative and align every doc |
| `P-07` | `PROTOCOL_DRAFT` | Freeze feature registry: paper raw vs extended families | Planner + feature owner | in review | `P-03` | Exact raw order/sign; transform formulas/grids; fold-local scope; multiplicity cap; circuit track either fully specified or blocked |
| `R-01` | `HUMAN_REVIEWED` | Independently review scientific plan and source comparability | Independent scientific reviewer | pending | `P-03`,`P-04`,`P-05`,`P-06`,`P-07` | Signed findings; conflicts resolved; reviewer differs from planner |
| `R-02` | `HUMAN_REVIEWED` | Review implementation design for test-label isolation | Independent scientific reviewer | blocked | `I-01`,`I-02` | Selection process demonstrably cannot load fixed-test labels |
| `I-01` | pre-authorization implementation | Implement label-limited selection view for `25C01`–`25C04` | Implementation owner | pending | `P-04` | Unit/integration test denies access to `25C05`–`25C08` labels |
| `I-02` | pre-authorization implementation | Implement permission-separated fixed-test evaluator and append-only access log | Implementation owner | pending | `I-01` | Denial before authorization; exactly-one-read test; evaluator cannot train/select |
| `I-03` | pre-authorization implementation | Implement nested LOGO candidates and serialized trial registry | Implementation owner | pending | `P-04`,`P-07` | Deterministic trial count/config hashes; fold-local transform tests |
| `I-06` | pre-authorization implementation | Implement paper-raw and approved extended feature pipelines | Feature owner | pending | `P-07` | 60-frequency order/sign tests; fold-local scaler/PCA/selector tests; no test-guided selection; exact schema hashes |
| `R-07` | feature review | Independently audit feature engineering and multiplicity budget | Independent scientific reviewer | blocked | `I-06` | `F0` has exactly 60 Re then 60 -Im; extended sets separately labeled; circuit failures/identifiability governed |
| `I-04` | pre-authorization implementation | Implement telemetry, limits, failure ledger and identical-retry links | Implementation owner | pending | `P-05`,`I-03` | Injected timeout/RSS/nonfinite failures stop and persist evidence |
| `I-05` | pre-authorization implementation | Implement immutable prediction/OOF/manifest and metric recomputation | Implementation owner | pending | `I-03` | Stored/recomputed metrics agree; artifact hash mutation is detected |
| `S-01` | engineering smoke | Run synthetic nested-LOGO and artifact smoke | Authorized runner for smoke only | blocked | `I-03`,`I-04`,`I-05`,`R-07` | Tests/lint pass; manifest remains `scientific_result=false`, `resume_approved=false` |
| `S-02` | engineering smoke | Run 0-restart GPR resource/instrumentation smoke | Authorized runner for smoke only | blocked | `S-01` | Per-fit time/RSS/optimizer/bound telemetry; no selection or scientific claim |
| `D-01` | `DATA_AUDITED` | Re-run checksum and source audit from candidate commit | Independent reviewer | blocked | `R-01`,`I-01` | 12 cells, 60 frequencies, 2,597 aligned rows, 120 features, unique keys, cycle-0 refs |
| `D-02` | `DATA_AUDITED` | Freeze primary-range and EOL-sensitivity algorithms before test | Independent reviewer | blocked | `D-01`,`P-06` | Algorithm/config hashes and training-row identities signed; expected audit counts recorded, but no test row/mask identities generated |
| `D-03` | `SPLIT_SEALED` | Seal training view and separately encrypt/permission test labels | Human authorizer + data custodian | blocked | `D-02`,`R-02` | Training cell-cycle hash; test partition schema/inventory and access-control seal without test row/mask identities; custodian signature |
| `A-01` | `SEARCH_AUTHORIZED` | Review candidate commit, clean tree, environment and all registry hashes | Independent scientific reviewer | blocked | `D-03`,`S-01`,`S-02`,`R-07` | Review accepts exact commit/lock/protocol/feature/search/seed/resource/unknown hashes |
| `A-02` | `SEARCH_AUTHORIZED` | Authorize training-cell nested search only | Human authorizer | blocked | `A-01` | Signed search authorization names runner, hashes, 6 h/24 CPU-h/8 GiB cap; excludes test |
| `T-01` | `TRAINING` | Execute complete registered nested LOGO ledger | Authorized runner | blocked | `A-02` | All registered trials completed/failed; no unregistered config or test access |
| `T-02` | `TRAINING` | Monitor heartbeat, time/CPU/RSS, concurrency, convergence and failures | Monitor | blocked | `A-02`,`T-01` | Append-only events/telemetry; stop gates enforced; incidents linked |
| `T-03` | `TRAINING` | Freeze OOF predictions and recompute primary/secondary metrics | Authorized runner | blocked | `T-01` | Complete row identities; immutable hashes; cell-macro metrics reproducible |
| `R-03` | `TRAIN_REVIEW` | Audit trials, failures, baseline gates, paired 1-SE rule and optional ensemble | Independent result reviewer | blocked | `T-02`,`T-03` | Signed report uses `SE_diff=sd(paired per-cell MAE differences)/sqrt(4)`; ensemble itself clears strongest-age `0.005`/per-cell degradation gates; winner or “EIS improvement not demonstrated” |
| `R-04` | `TRAIN_REVIEW` | Sign one candidate decision without test feedback | Human authorizer | blocked | `R-03` | Candidate ID/hyperparameter-selection procedure/OOF hash signed |
| `A-03` | `FINAL_FIT_AUTHORIZED` | Authorize inner-LOGO retune and one all-training-cell fit | Human authorizer | blocked | `R-04` | Exact scope, model family, registries, runner and resource cap signed |
| `F-01` | `FINAL_FITTED` | Retune inside four training cells and fit signed winner | Authorized runner | blocked | `A-03` | Final-fit manifest, transform/model hashes, no test access |
| `R-05` | `FINAL_FITTED` | Verify final fit matches signed candidate decision | Independent result reviewer | blocked | `F-01` | Hyperparameters, feature schema, fit rows and hashes accepted |
| `A-04` | `TEST_AUTHORIZED` | Authorize one batch fixed-test evaluation | Human authorizer + data custodian | blocked | `R-05` | Signed batch `{B0,strongest(B1,B2),P0,selected-modern-if-different}`, frozen primary/sensitivity algorithm hashes and evaluator hash; no precomputed test identities |
| `E-01` | `TEST_EVALUATED` | Read fixed-test labels once, generate test identities/masks, and evaluate signed batch | Authorized test evaluator | blocked | `A-04` | Access log has `label_reads=1`; post-unlock test row/mask identity hashes plus immutable predictions/metrics/errors/coverage |
| `E-02` | `TEST_EVALUATED` | Seal result bundle before interpretation | Monitor | blocked | `E-01` | Bundle and file hashes, timestamps, status, no mutation |
| `R-06` | `INDEPENDENT_REVIEW` | Recompute test metrics and audit every event/access/selection rule | Independent result reviewer | blocked | `E-02` | Accepted or rejected evidence report; per-cell and historical-exposure disclosure |
| `C-01` | `PROMOTED/HALTED` | Decide whether artifact qualifies as a descriptive scientific result | Independent result reviewer + human approver | blocked | `R-06` | New promotion artifact; old manifests not edited; otherwise explicit `HALTED` |
| `C-02` | claim review | Draft exact public/project wording | Claim writer | blocked | `C-01=accepted` | Wording distinguishes cleaned benchmark/method reference/paper replay |
| `C-03` | claim review | Independently approve or reject resume wording | Resume claim approver | blocked | `C-02` | Signed wording-level decision; only then may a new artifact say `resume_approved=true` |

`done` above means document discovery/decision drafting is complete, not that a scientific gate is
passed. `in review` means the item still requires independent acceptance.

## Current blockers

1. `docs/protocol-v1.md` still makes EOL truncation part of the capacity boundary; evidence review
   supports all valid aligned rows as primary and EOL-bounded results as sensitivity only.
2. The plan and feature registry have not received independent scientific review or human authorization.
3. The current evaluation command exposes all labels in one process; a real selection/test boundary
   does not exist yet.
4. GPR formal code/runtime has not been validated; the all-row primary is larger than prior estimates.
5. Trial, feature, telemetry, authorization and test-access artifact schemas are not implemented.
6. The fixed test has historical engineering exposure and can only be prospectively rule-locked.

These blockers prohibit formal training and test evaluation.

## Authorization record template

Each gate uses a new immutable record; blank fields mean **not authorized**.

| Field | Required value |
|---|---|
| Lifecycle transition | |
| Scope and expressly excluded actions | |
| Commit and clean-tree evidence | |
| Protocol/feature/search/seed/resource/unknown/lock hashes | |
| Dataset/train/test split hashes | |
| Runner/evaluator identity | |
| Human authorizer identity | |
| Independent reviewer evidence hash | |
| Wall/CPU/RSS/concurrency budget | |
| Authorization timestamp and expiry | |
| Signature/record hash | |

## Live monitor checklist

For every authorized process, append—not overwrite—the following:

- run, state, fold and trial ID;
- start/end/heartbeat timestamps and process exit;
- elapsed time, CPU-hours, current/peak RSS and concurrent-fit count;
- optimizer iteration/objective, warnings, kernel bounds and non-finite count;
- feature family/schema, fold-local transform/selector hashes and circuit-fit failures;
- input/registry/output hashes and row counts;
- identical retry parent and infrastructure-fault reason;
- stop-gate trigger, process termination and incident-report ID;
- test access request/decision; for the evaluator, label-read count.

The monitor must halt on any hash mismatch, leakage, unauthorized access/change, corrupt artifact,
non-finite result, per-fit/global budget breach or self-authorization. It cannot modify a grid, seed,
bound, jitter, candidate, selection rule or budget.

## Incident log template

| Incident ID | Timestamp | State/trial | Detection | Stop gate | Artifacts preserved | Scientific change needed? | Reviewer decision | Recovery authorization |
|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — |

A scientific change creates a new plan/registry version and returns to the earliest invalidated gate.
An identical retry is allowed only for a documented infrastructure fault and must link to the failed
trial. A halted or failed artifact is never deleted or relabeled as successful.

## Decision log

| Date | Decision | Evidence | Status |
|---|---|---|---|
| 2026-08-01 | Treat v1 as cleaned-source benchmark, not packaged-array replay | Data card and source-anomaly audit | Drafted; needs independent acceptance |
| 2026-08-01 | Keep absolute mAh as fitted target and report cycle-0-normalized SoH | Protocol and source-pair alignment | Drafted; needs independent acceptance |
| 2026-08-01 | Use nested cell-group selection and never row-random validation | 12-cell structure; only four fixed-split training cells | Drafted; needs independent acceptance |
| 2026-08-01 | Mark fixed test historically exposed and prospectively rule-locked | Corrected engineering smoke already reported test metrics | Drafted; disclosure mandatory |
| 2026-08-01 | Require permission separation before agentic test execution | Current evaluator loads all labels | Blocker |
| 2026-08-01 | Use all valid aligned rows for cleaned capacity primary; keep EOL-bounded as sensitivity | Paper assigns 80% EOL to RUL/termination, not capacity sample filtering | Proposed; protocol conflict blocks authorization |
| 2026-08-01 | Keep 120 raw Re/-Im as paper feature set; label all engineered representations extended | Paper Methods, source schema and independent evidence review | Drafted; feature review required |
| 2026-08-01 | Keep all metrics non-scientific and non-resume for this round | No independent/human gates passed | Active |
