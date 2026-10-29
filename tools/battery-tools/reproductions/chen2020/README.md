# Can a researcher trust this virtual battery under a different load?

A battery researcher wants to try operating conditions on a computer before choosing the next experiment. First, the virtual battery must reproduce what real cells do, including voltage recovery after use. A visually close discharge curve is insufficient.

This case partially reproduces the **validation** in Chen et al. (2020), DOI [10.1149/1945-7111/ab9050](https://doi.org/10.1149/1945-7111/ab9050), using the authors' three LG M50 cell records, dataset [10.5281/zenodo.4032561](https://doi.org/10.5281/zenodo.4032561). Dataset metadata licenses data CC BY 4.0. Credit: Chang-Hui Chen, Ferran Brosa Planella, Kieran O'Regan, Dominika Gastol, W. Dhammika Widanage, Emma Kendrick. No paper figures are copied. Figures here are newly generated from the open data and actual simulation.

## What actually ran

Six PyBaMM 26.10.1.1 DFN simulations: 0.5C, 1C, 1.5C, each with fixed Chen2020 1C parameters and with Table IX's rate-specific negative-electrode diffusivity (1.3e-14, 3.3e-14, 6.3e-14 m2/s). The two 1C runs intentionally coincide. Every run discharges to 2.5 V then rests for two hours. Default isothermal 298.15 K; initial concentrations come from Chen2020, not a voltage-based SOC refit. Solver IDAKLU defaults and mesh defaults. MD5 is checked against Zenodo; SHA256 is recorded too.

The raw Maccor files have 13 preamble rows. Mode D identifies discharge; current magnitude is unsigned. Validation discharge steps are 12, 17, 22; following R steps are rest. Initial partial discharge and C/10 are excluded. This is a single three-cell protocol cohort, not three randomized independent experiments.

| Load | Fixed parameters: discharge / rest RMSE (mV) | Paper rate-specific parameters: discharge / rest RMSE (mV) |
|---|---:|---:|
| 0.5C | 60.68 / 171.94 | 43.07 / 28.93 |
| 1C | 39.79 / 25.53 | 39.79 / 25.53 |
| 1.5C | 35.42 / 104.48 | 29.59 / 28.92 |

Errors are per-cell RMSE on uniform 10-second grids, then arithmetic mean across three cells. Discharge is compared over the shared elapsed duration without time stretching. Unmatched measured tails and cutoff-time errors are separately reported, not silently scored as success. Rest is phase aligned to each curve's own cutoff and scored separately. Consequently these are **not the paper's Table VIII metrics**. Temperature variation, modern PyBaMM implementation and initial-state/protocol simplifications also preclude an exact numerical Figure 17 replication.

## What this changes for the next experiment

A researcher who examined only discharge could miss the large error in post-discharge recovery at 0.5C and 1.5C. Published rate-specific tuning reduces that error, but the model still relaxes with a different shape. This is a reason to test transfer before using it to prioritize new designs. It does not show that a uniquely identified material diffusion coefficient physically depends on current.

A next iteration should freeze parameter choices before evaluating a genuinely independent workload, retain both discharge and relaxation criteria, and inspect temperature/initial-state sensitivity. A concentration-dependent diffusivity is a candidate to test, not a completed explanation.

## Agent boundary

The web agent selects `inspect_validation` and `compare_validation`, reads actual returned evidence, summarizes it, and accepts a researcher-directed next comparison. Numerical comparison runs now in a browser worker from immutable solver output. **The website does not execute a new PyBaMM solve.** Python reproduction below executes the solver. Source/protocol preparation and adapters were developed by a person using a coding assistant; this is not evidence that the deployed agent independently recreated the complete paper or controlled instruments.

## Re-run

Python 3.11 is the tested runtime. Create a virtual environment, install requirements, then:

```sh
PYBAMM_DISABLE_TELEMETRY=true python download.py
PYBAMM_DISABLE_TELEMETRY=true python reproduce.py
```

Results include full curves, individual cell metrics, file hashes, and SVG/PNG plots. `agent-validation.mjs` independently recalculates the same metrics in JavaScript; all six paired Python/JS comparisons agree within 1e-8 mV.
