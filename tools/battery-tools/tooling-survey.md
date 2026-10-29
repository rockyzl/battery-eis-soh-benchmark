# Battery calculation and processing toolkit survey

Researched 2026-10-29. Primary maintainer documentation only. Availability is distinct from integration.

| Tool | Existing capability | Expert decisions required | Demo status |
|---|---|---|---|
| cellpy | Multi-vendor cycling import, summaries, batch analysis | Units, step types, capacity-reset rules, instrument versions | Candidate adapter |
| PyProBE | Cycling analysis, differentiation/ICA/DVA, degradation-mode analysis | Protocol alignment, smoothing sensitivity, electrode references | Candidate analysis adapter |
| galvani | Vendor-native electrochemistry parsing | Format/firmware version and optional system dependencies | Candidate parser |
| impedance.py | Linear-KK, circuit models, weighted numerical fitting | Complex sign, stationary measurement, weighting, initial guesses, identifiability | Executed reference calculations |
| pyimpspec | KK/Z-HIT, fitting, DRT | Validation, regularization, numerical/model dependence of DRT peaks | Candidate advanced EIS adapter |
| ZView | Commercial circuit fitting, simulation and import | License, model choice, initial guesses and reproducible exports | Comparison tool, not automated |
| PyBaMM + PyBOP | Physics-based battery simulation and parameter identification | Geometry, chemistry, protocol, parameter identifiability and runtime | Later physics-model adapter |
| PBT | Pretrained battery-life representations with target adaptation | Compatible input/endpoint, lifecycle labels, target calibration and independent test | Later prediction candidate |

Sources:
- https://cellpy.readthedocs.io/
- https://github.com/ImperialCollegeLondon/PyProBE
- https://pyprobe.readthedocs.io/en/latest/user_guide/analysis.html
- https://github.com/echemdata/galvani
- https://impedancepy.readthedocs.io/en/latest/validation.html
- https://impedancepy.readthedocs.io/en/latest/fitting.html
- https://vyrjana.github.io/pyimpspec/guide_drt.html
- https://vyrjana.github.io/pyimpspec/guide_fitting.html
- https://www.ameteksi.com/products/software/zview-software-en
- https://pybamm.org/learn/
- https://github.com/pybop-team/PyBOP
- https://github.com/Ruifeng-Tan/PBT

A fitted curve is not a unique physical diagnosis. A general agent should select and validate
appropriate tools rather than promise one model for every chemistry and endpoint. The first
research comparison must isolate the agent workflow's benefit from the underlying numerical
model's benefit under equal data and compute budgets.

