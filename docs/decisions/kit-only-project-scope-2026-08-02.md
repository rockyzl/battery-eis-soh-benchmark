# Decision: KIT is the only active battery project

Date: 2026-08-02
Status: accepted

## Decision

Only the public KIT battery-ageing project is active.

- KIT remains the sole data, modelling, UI, report and seminar track.
- CALCE is stopped. Do not download it, train on it, use it for validation, or create CALCE tasks,
  configs, demos, reports or repositories.
- Do not reopen CALCE as a fallback when KIT work is slow or blocked. Resolve the KIT blocker within
  the KIT lineage or report it explicitly.
- Existing Cambridge code is frozen historical material only. It is not a backup project and must
  not receive new modelling, UI or publication work unless the user explicitly authorizes a separate
  future decision.

## Why

Running several battery datasets in parallel diluted effort and made it harder to finish one coherent,
auditable portfolio project. KIT already supports the intended end-to-end story: data description,
EDA, preprocessing, feature engineering, condition-held-out training, B versus D_CORE/EIS comparison,
fleet replay UI, report and research-seminar slides.

## Agent rule

Future agents must treat `KIT only` as a scope gate. Mentions of CALCE are permitted only when clearly
labelled as a stopped historical option. Any proposed reactivation requires an explicit user decision.
