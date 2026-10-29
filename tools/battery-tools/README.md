# Battery research tools

An executable battery-data investigation, developed for SciencesLoop's Battery R&D Agent.
This directory is an additive demo module. It does not change the original benchmark's
preregistered protocol, model-selection claims, or data-alignment rules.

## What runs now

- Read and audit public measured EIS/capacity files with explicit units, cell identities and diagnostic alignment.
- Run impedance.py 1.7.1 linear-KK and two candidate equivalent-circuit fits with two initializations.
- Fit an independent browser CPE/Warburg circuit to a measured or imported CSV spectrum.
- Select ridge regularization by development-cell leave-one-cell-out validation and predict later measured capacity in different cells.
- Compare predictions with training-mean and persistence baselines, report per-cell error, inspect temperature transfer, and export predictions/model provenance.
- Check constant-current cycling CSV segments before calculating dQ/dV with a local-linear 20 mV window.

The production conversation uses a live language model to recommend analysis actions, summarizes actual returned evidence and accepts researcher feedback. Each tool is proposed before execution; researchers can edit parameters or choose per-round automatic execution. Numerical work runs in a browser worker. Connected actions: inventory inspection, EIS fitting, grouped capacity prediction and analytic one-RC pulse/rest simulation. The last uses illustrative, user-editable parameters and is not PyBaMM or measured-cell identification.

The live endpoint is https://api.sciencesloop.com/api/battery-agent and the deployed demo is https://sciencesloop.com/agent/battery-research/?lang=zh . This repository contains the frontend and numerical sources; model credentials remain only in the backend deployment. No arbitrary runtime code generation, literature-search action or instrument control is implemented. The [scenario/tool design study](docs/scenario-tool-design.md) covers battery analysis and simulation packages, input requirements, interpretation checks and integration status; PyBaMM/PyBOP and other listed integration candidates are not claimed as live tools.

## Reproduce

From the repository root, create an isolated environment and install the optional tools:

    python3 -m venv --system-site-packages .venv-demo
    .venv-demo/bin/pip install -r tools/battery-tools/requirements-demo.txt
    PYTHONPATH=src .venv-demo/bin/python scripts/fetch_public_data.py
    .venv-demo/bin/python tools/battery-tools/build_demo_data.py --data-root data/raw/extracted --output tools/battery-tools/web/data.json
    node tools/battery-tools/test_analysis.mjs

Serve the repository with a static server and open tools/battery-tools/web/.
The web module imports ../analysis.mjs. For a flattened deployment, copy analysis.mjs into
the deployed folder and change that import to ./analysis.mjs. The data builder is required
before serving; source archives and generated data remain outside the committed module.

## Data and interpretation

Yunwei Zhang et al., *Identifying degradation patterns of lithium ion batteries from
impedance spectroscopy using machine learning* (2020).
Dataset https://doi.org/10.5281/zenodo.3633835 (CC BY 4.0).
Article https://doi.org/10.1038/s41467-020-15235-7.
The original 12-cell public collection is not Lu Zhang's historical Argonne archive.
Archive checksums are verified before use. Raw files remain unchanged.

State-V EIS index k pairs with capacity diagnostic k-1. The browser forecasts target capacity
at k+h-1 using EIS at k. These indices are not physical cycle counts.
Complete spectra without matched capacity are counted and excluded. Future targets require
an exact same-cell index match. No interpolation supplies missing labels.

Default development cells: 25C01–25C04. Default test cells: 25C05–25C08.
Five prespecified frequency locations give ten real/negative-imaginary features.
Standardization refits within each development fold. Alpha grid: 0.1, 1, 10, 100.
Model selection averages MAE over development cells, and test metrics average over test cells.
Residual intervals are exploratory and have no coverage guarantee.
Visible test outcomes make this an exploratory demo, not a sealed validation study.

## Initial result, 2026-10-29

At a five-index horizon and 25°C held-out-cell test:
659 development records from four cells; 644 test records from four other cells.
Training-mean MAE 3.3334 mAh; EIS-ridge MAE 2.3181 mAh; persistence MAE 0.5673 mAh.
Alpha 100 was selected using development-cell validation only.
The EIS model does not outperform persistence in this setting. Negative results remain visible.
This is an independently computed exploratory result, not the original article's performance.

## Verification

Tests cover exact future-label alignment, cell-held-out leakage control, linear-model recovery,
known-circuit curve recovery, dQ/dV against an analytical slope, current-variation rejection,
and missing/duplicate CSV fields. Browser ridge predictions matched scikit-learn StandardScaler
plus Ridge with a maximum absolute difference of 3.91e-14 mAh in the default run.
A NumPy 2.x compatibility namespace is registered for impedance.py 1.7.1's internally generated
fixed circuit expressions; user-supplied expressions are never evaluated.

Cycling input columns: cell_id,cycle,voltage_v,capacity_mah,current_a.
Provide one continuous constant-current segment per cell/cycle/direction. Step merging cannot
be inferred safely from an arbitrary vendor export. EIS input columns:
cell_id,cycle,frequency_hz,z_real_ohm,z_imag_ohm (actual Im(Z), not negative imaginary).
Imported-file analysis stays in the browser session. Vendor-native parsing and imported multi-cell
prediction require subsequent adapters.

See tooling-survey.md for candidate integrations and the judgment each method requires.



## Live research agent, 2026-10-29

The web entry now includes a genuine model-directed tool loop through the SciencesLoop battery-agent API. The model selects inspect_data, fit_eis, or optional predict_capacity; deterministic tools return calculated outputs; the model reviews outputs and finishes with a conclusion and next-step suggestions. Current live model verified: gpt-4.1-mini-2025-04-14. No credentials are in browser source. Previous-round conclusions and measured fit results feed a manually started next iteration. This is iterative open-loop research support, not autonomous physical control or unrestricted code execution. The older deterministic workbench remains as an inspectable tool surface.

Live checks completed: a task explicitly excluding prediction selected inspect_data, fit_eis, finish. A second diagnostic-stage task selected inspect_data, fit_eis, fit_eis, finish. Review caught conflation of KK consistency residual and circuit-fit residual; the model instructions now require separate metric comparisons, diagnostic-index terminology, and a concrete next suggestion. Neither check establishes general scientific reliability. Uploaded private data are not connected to this agent version. Task text and public-data tool outputs are sent to the model backend.
