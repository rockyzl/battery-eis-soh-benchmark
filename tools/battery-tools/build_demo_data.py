"""Build a small, attributed browser dataset from checksum-verified public measurements.

This demo is separate from the repository's preregistered benchmark protocol.
Run with requirements-demo.txt in an isolated environment. Vendor/source files stay unchanged.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
import warnings
from pathlib import Path

import numpy as np
from impedance.models.circuits import CustomCircuit
from impedance.validation import linKK
from impedance.models.circuits.elements import circuit_elements

# impedance 1.7.1 emits NumPy scalar reprs in its internally generated expressions
# under NumPy 2.x. Expose the numerical namespace used by those fixed expressions.
# No user-supplied expression is evaluated by this demo.
circuit_elements["np"] = np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from battery_eis_soh.dataset import load_state_dataset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Authentication precedes use, including when reusing extracted data.
    archives = args.data_root.parent
    checksums = {"EIS data.zip": "9f31069d770d7d323d8e2bf21267afe2",
                 "Capacity data.zip": "0dba677f0beeb15b450ef35f5b4faac8"}
    for name, expected in checksums.items():
        actual = hashlib.md5((archives / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"source checksum mismatch: {name}")
    data = load_state_dataset(args.data_root)
    assert len(set(data.cells)) == 12 and data.X.shape == (2597, 120)
    assert np.all(np.isfinite(data.X)) and np.all(data.capacities_mah > 0)
    # Feature locations are fixed before test evaluation. No target-based frequency selection.
    requested_frequencies = [1000, 100, 10, 1, 0.1]
    indices = [int(np.argmin(abs(np.log(data.frequencies_hz / f))))
               for f in requested_frequencies]
    samples = []
    spectra = []
    for cell in data.audit.cells:
        rows = np.flatnonzero(data.cells == cell)
        for i in rows:
            samples.append({"cell": cell, "cycle": int(data.cycles[i]),
                            "temperature": int(data.temperatures_c[i]),
                            "capacity": round(float(data.capacities_mah[i]), 7),
                            "q0": round(float(data.initial_capacities_mah[i]), 7),
                            "features": np.round(data.X[i, indices + [j + 60 for j in indices]], 7).tolist()})
        chosen = sorted({int(rows[0]), int(rows[len(rows) // 2]), int(rows[-1])})
        for i in chosen:
            spectra.append({"cell": cell, "cycle": int(data.cycles[i]),
                            "real": np.round(data.X[i, :60], 7).tolist(),
                            "negImag": np.round(data.X[i, 60:], 7).tolist()})
    # Reference tool run on three spectra for one held-out cell. This is descriptive
    # analysis only and never supplies prediction features or model selection labels.
    references = []
    for spectrum in [s for s in spectra if s["cell"] == "25C05"]:
        f = data.frequencies_hz
        z = np.array(spectrum["real"]) - 1j * np.array(spectrum["negImag"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            M, mu, zkk, rr, ri = linKK(f, z, c=0.85, max_M=30, fit_type="complex", add_cap=True)
        kk = {"M": int(M), "mu": float(mu),
              "rms_percent": float(100 * np.sqrt(np.mean(rr ** 2 + ri ** 2))),
              "real": zkk.real.tolist(), "negImag": (-zkk.imag).tolist(),
              "residualRealPercent": (100 * rr).tolist(), "residualImagPercent": (100 * ri).tolist()}
        # Exclude inductive part explicitly for these capacitive circuit candidates.
        mask = z.imag < 0
        models = []
        for name, circuit, seeds in [
            ("RC + Warburg", "R0-p(R1,C1)-W1", [[0.3, 0.5, 0.02, 0.2], [0.4, 1, 0.1, 0.5]]),
            ("CPE + Warburg", "R0-p(R1,CPE1)-W1", [[0.3, 0.5, 0.02, 0.8, 0.2], [0.4, 1, 0.1, 0.6, 0.5]])]:
            fits = []
            for seed in seeds:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        model = CustomCircuit(circuit, initial_guess=seed)
                        model.fit(f[mask], z[mask], weight_by_modulus=True, maxfev=20000)
                    pred = model.predict(f)
                    res = (pred[mask] - z[mask]) / abs(z[mask])
                    rss = float(np.sum(abs(res) ** 2))
                    n = 2 * int(mask.sum())
                    k = len(seed)
                    fits.append({"parameters": model.parameters_.tolist(),
                                 "names": model.get_param_names()[0], "units": model.get_param_names()[1],
                                 "weighted_rms_percent": 100 * np.sqrt(rss / mask.sum()),
                                 "aic": n * np.log(max(rss / n, 1e-30)) + 2 * k,
                                 "real": pred.real.tolist(), "negImag": (-pred.imag).tolist()})
                except (ValueError, RuntimeError) as exc:
                    print(f"fit failed: {spectrum['cycle']} {name}: {exc}", file=sys.stderr)
            if fits:
                best = min(fits, key=lambda x: x["weighted_rms_percent"])
                models.append({"name": name, "circuit": circuit, "best": best,
                               "successful_starts": len(fits),
                               "parameter_runs": [x["parameters"] for x in fits]})
        references.append({"cell": spectrum["cell"], "cycle": spectrum["cycle"], "kk": kk,
                           "fit_points": int(mask.sum()), "models": models})
    manifest = {"schema": "sciencesloop.battery-demo.v1", "created": "2026-10-29",
                "source": {"title": "Identifying degradation patterns of lithium ion batteries from impedance spectroscopy using machine learning",
                           "authors": "Yunwei Zhang et al.", "doi": "10.5281/zenodo.3633835",
                           "paper": "10.1038/s41467-020-15235-7", "license": "CC BY 4.0",
                           "url": "https://zenodo.org/records/3633835", "checksums_md5": checksums,
                           "chemistry": "LCO/graphite coin cells, nominal 45 mAh",
                           "state": "V: 15-minute rest after full charge",
                           "alignment": "EIS cycle k joins discharge-capacity diagnostic k-1. Browser cycle is the EIS index, not physical cycle count.",
                           "archive_origin": "Public Cambridge study, not Lu's Argonne archive"},
                "audit": dataclasses.asdict(data.audit),
                "feature_frequencies_hz": data.frequencies_hz[indices].tolist(),
                "frequency_hz": data.frequencies_hz.tolist(), "samples": samples,
                "spectra": spectra, "references": references,
                "tools": {"parser": "battery_eis_soh.dataset, clean-room implementation",
                          "eis_reference": "impedance.py 1.7.1, linKK and CustomCircuit",
                          "web_prediction": "Browser ridge regression, grouped development validation, held-out cells",
                          "live_llm": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, separators=(",", ":"), allow_nan=False))
    print(json.dumps({"samples": len(samples), "cells": len(data.audit.cells),
                      "spectra": len(spectra), "reference_runs": len(references),
                      "output_bytes": args.output.stat().st_size, "audit": dataclasses.asdict(data.audit)}))


if __name__ == "__main__":
    main()
