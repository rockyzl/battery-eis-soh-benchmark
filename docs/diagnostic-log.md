# Diagnostic log

## 2026-08-01 — initial fixed-split engineering smoke

Observable result before the EOL-range correction:

- 676 training rows and 663 test rows;
- a training-mean predictor outperformed fixed Ridge and PLS baselines;
- all three learned-model pooled R² values were negative.

This is not a scientific result and is not resume evidence. Competing explanations include:

1. post-EOL rows far outside the paper's stated 80% EOL boundary were included;
2. linear Ridge/PLS cannot model the cell-to-cell EIS offsets;
3. the exact paper target/cycle alignment may still be incomplete;
4. the paper's nonlinear GPR-ARD model has not been run;
5. repeated observations from only four training cells make transfer genuinely difficult.

Containment:

- artifacts remain marked `scientific_result=false` and `resume_approved=false`;
- no model is promoted;
- evaluation now stops after each cell's first SoH observation at or below 0.80;
- a new output directory is required for the corrected smoke.

Next falsification steps:

1. rerun bounded baselines under the explicit EOL mask;
2. reconcile row counts and cycle semantics with the article figures;
3. implement and profile the preregistered GPR-ARD candidate;
4. perform grouped leave-one-cell-out analysis only after the fixed protocol is reviewed.

## 2026-08-01 — independent protocol review blocked promotion

The independent review found that the initial loader used an incorrect same-cycle join. The source
experiment and the authors' first published modeling pair instead use `EIS cycle k -> capacity cycle
k-1`. The earlier smoke artifacts are invalid for scientific comparison and remain ignored and
unapproved.

The review also found that the authors' packaged modeling arrays appear to swap the `25C02` and
`25C03` capacity sequences and truncate cells differently from the complete Zenodo files. Version 1
will therefore be described as a cleaned, cell-identity-preserving Zenodo benchmark, not as a faithful
replay of the packaged arrays.

Required containment before a formal GPR run:

1. correct and regression-test the `k-1` alignment;
2. require an explicit cycle-0 reference capacity;
3. rerun all engineering baselines, including a cycle-only trend baseline;
4. freeze the cleaned-benchmark protocol after another independent review;
5. run GPR only from a clean commit with source, protocol, lockfile, split and row hashes.

## 2026-08-01 — corrected-alignment engineering smoke

After applying the `k-1` alignment, the EOL-bounded 25 °C split contains 243 training rows and 172
test rows. A cycle-only trend baseline was added to test whether impedance contributes information
beyond diagnostic age.

Observed SoH pooled MAE (engineering only):

- training-capacity mean: `0.0494`;
- cycle-only Ridge: `0.0512`;
- 120-feature EIS Ridge: `0.1702`;
- 120-feature EIS PLS: `0.2083`.

All pooled R² values remain negative. These values are **not scientific results and not resume
evidence**. They show that fixed linear EIS baselines do not transfer across the held-out cells under
the current cleaned-data/EOL-bounded protocol. They do not yet test the paper's nonlinear GPR-ARD
model, and they do not establish that EIS lacks predictive information.
