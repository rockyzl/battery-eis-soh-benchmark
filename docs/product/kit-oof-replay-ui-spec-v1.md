# Product specification — KIT condition-held-out OOF replay

Status: `paired B/D_CORE development-OOF implementation complete; visual QA in progress; final/protected mode disabled`

Decision dependency: `docs/decisions/early-warning-target-and-oof-replay-v2.md`

## 1. What this product is

The interface is a historical replay of the public KIT ageing dataset. Its default view includes all
228 cell histories. It lets a reader move through each real cell record as time becomes available,
inspect condition-held-out out-of-fold (OOF) predictions, and later compare them with the measured
third-follow-up SoH.

It is **not** a battery physics simulation. It must never animate an invented capacity curve, imply
that a cell was prospectively monitored in production, or present development OOF predictions as a
protected external test.

Within ten seconds, a beginner should be able to answer:

1. Which real KIT cell and ageing mode am I viewing?
2. What information was available at this historical check-up?
3. Was a valid OOF prediction available, and what did it estimate?
4. Has the future H3 measurement matured yet?
5. Why is a prediction missing or abstained?

## 2. Information architecture

### Top-level mode tabs

- `Calendar ageing`
- `Cyclic ageing`
- `Driving-profile ageing`

The tabs separate exposure processes. Results are not pooled visually in a way that makes days, EFC
and kilometres interchangeable.

### Global replay controls

- play/pause;
- previous/next check-up;
- one-step advance;
- history slider;
- current replay date/exposure;
- speed control only if it changes display pacing, never scientific time.

Check-up index is a navigation device only. Beside it, show real source-backed exposure:

- calendar: elapsed days;
- cyclic: EFC and elapsed days;
- driving profile: traceable kilometres when available, otherwise `not available`; retain EFC and
  elapsed days when source-backed.

### Data-mode selector

The interface exposes two clearly separated modes:

- `KIT Development OOF · B vs D_CORE`: enabled only for the reviewed, hash-bound paired development
  artifact. It compares identical snapshots, folds, model families and weights. It remains
  persistently labeled as development evidence, not a final/protected result, and it does not declare
  a universal winner or causal EIS effect.
- `Final / protected test`: disabled because no protected evaluation has occurred. The option cannot
  be enabled merely by placing a file at an expected path.

### Fleet overview

Show all cells in the selected mode as compact tiles or a heatmap. Default ordering is stable and
traceable (condition, replicate, physical cell). The overview exposes availability and maturation,
not invented red/yellow/green risk categories.

Each cell may carry more than one badge because availability states are not mutually exclusive:

- `Eligible`: the required inputs and exact H3 truth exist for evaluation at this replay point;
- `Insufficient history`: required past features are unavailable;
- `Missing EIS`: `D_CORE` is unavailable; a `B` prediction may still exist;
- `No H3 follow-up`: the future endpoint does not exist in the source record;
- `Abstained`: an otherwise callable model declined to predict under a frozen abstention rule.

The UI must not collapse `missing EIS`, `no H3 follow-up` and `abstained` into a single “failed” state.
Source/parser-invalid rows, if encountered, use an additional explicit `Data unavailable` state.

### Alert/review queue

The queue is a research review queue, not an operational maintenance alarm. Until a probability
definition and review threshold are independently frozen, it may filter and sort by data status,
mode, condition, maturation and model/view availability only. Any risk ordering shown later must be
based on reviewed OOF artifacts and say which model/view produced it.

No color thresholds, benefit claims or recommended physical action may be invented. “First warning”
and lead time remain `not available — review threshold pending` until a threshold is frozen.

### Cell drilldown

The drilldown contains:

1. cell identity, condition, replicate and ageing mode;
2. replay check-up and real elapsed exposure;
3. measured current SoH;
4. predicted H3 SoH, predicted H3 loss and prediction interval, when available;
5. secondary rapid-loss probability, only when its candidate 5 pp definition and probability method
   have passed the required review;
6. paired `B` versus `D_CORE` predictions on the identical eligible snapshot, with availability and
   fold provenance;
7. historical measured capacity/SoH and EIS evidence available through the replay point;
8. first warning versus matured H3 outcome and real-unit lead time, only after a warning threshold is
   frozen;
9. explicit reason for every unavailable or abstained value.

Plain-language default labels:

| Beginner label | Technical meaning |
|---|---|
| Health measured now | `SoH_t` |
| Estimated health after 3 check-ups | `current_soh - predicted_soh_loss_pp_h3` |
| Expected health loss | predicted `soh_loss_pp_h3` in percentage points |
| Chance of fast decline | candidate `P(soh_loss_pp_h3 >= 5 pp)`; hidden until authorized |

Technical details are collapsed by default. When expanded they show formal names, units, exact
eligibility reason, held-out condition/fold, feature view, model/artifact version, configuration and
source hashes, interval method and prediction timestamp.

## 3. Truth-maturation behavior

At replay check-up `t`, the measured `SoH_t+3` is hidden even though it exists in the stored dataset.
When the replay advances to the corresponding future check-up, the interface may unlock:

- measured H3 SoH;
- observed signed loss `SoH_t - SoH_t+3`;
- prediction error and interval coverage, if a valid OOF prediction existed;
- whether the candidate five-point rapid-loss event occurred, if that secondary view is authorized.

The unlock must be visual and auditable. The UI never uses a future measurement to alter an earlier
prediction. A missing third follow-up remains `No H3 follow-up`; it is not imputed for display.

## 4. OOF and provenance contract

Every prediction shown must satisfy all of the following:

- the entire experimental condition, including all three cells and every check-up, was excluded from
  that model's training data;
- features existed at or before prediction time;
- the prediction came from a complete, schema-valid OOF artifact;
- cell, condition, check-up, fold, feature view and artifact hash resolve exactly;
- the display distinguishes `B`, `D_CORE` and any later authorized view;
- incomplete, stale, diagnostic or claim-blocked artifacts are not silently presented as valid.

The page must include this concise disclosure near the chart:

> Historical KIT data replay. Predictions are condition-held-out OOF development estimates, not a
> physics simulation, live monitoring result or protected external test.

## 5. Component states and empty states

Every numeric component supports: `available`, `not yet matured`, `insufficient history`, `missing
EIS`, `no H3 follow-up`, `abstained`, `artifact unavailable` and `blocked pending review` as applicable.
Unavailable values display a reason, never zero, a dash without explanation or a fabricated default.

If the governed paired development artifact is unavailable or fails validation, the interface must
fail closed rather than fall back to invented predictions. It may remain useful as a data-history
explorer and says:

> Model estimates are not available because formal model selection and independent QA are not
> complete.

The disabled final/protected option remains visible.

## 6. Accessibility and presentation rules

- Beginner mode is the default and uses plain language.
- Technical/provenance details are collapsed but reachable without leaving the cell view.
- Units appear beside every value; SoH is percent and loss is percentage points.
- Status is communicated with text/icon as well as color.
- Playback is keyboard operable and does not auto-start.
- Charts do not connect missing observations as if they were measured.
- The same real cell identity and replay point persist when switching detail level.

## 7. Acceptance and QA wave

| Gate | Owner | Dependency | Acceptance evidence |
|---|---|---|---|
| Scientific mapping | Scientific protocol reviewer | Frozen F1 decision and aligned OOF schema | Every UI field maps to a reviewed source/prediction field; no future leakage |
| Product behavior | UI/product implementer | Accepted mapping and synthetic fixtures | All tabs, controls, states, truth unlock and paired-view behavior demonstrated |
| Claim review | Independent scientific reviewer | Complete interface build | Development label persists; final/protected mode stays disabled; no protected-test, RUL, safety, causal EIS, winner, lead-time or benefit overclaim |
| Accessibility/usability | Independent QA | Stable build | Keyboard, status text, unit, empty-state and beginner comprehension checks pass |
| Release | Human reviewer | All prior gates | Explicit approval; no automatic publication |

Implementation must use synthetic or clearly marked non-result fixtures until a complete accepted OOF
artifact exists. Passing UI tests does not authorize a performance claim.
