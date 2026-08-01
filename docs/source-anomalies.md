# Source anomalies and benchmark identity

Status: documented during independent protocol QA on 2026-08-01.

## What was checked

The first 120-feature row in the authors' published modeling input was matched element-by-element to
the `25C01` state-V EIS spectrum at diagnostic cycle 1 in the Zenodo source files. Its published target
is `37.20271 mAh`, which is capacity cycle 0. Capacity cycle 1 is `36.22303 mAh`. This establishes the
alignment used here: `EIS cycle k -> capacity cycle k-1`.

The same QA reconstructed all 1,358 EIS rows in the authors' packaged modeling arrays against the
Zenodo state-V spectra. It found that the packaged `25C02` EIS block is paired with the Zenodo
`25C03` capacity sequence, while the packaged `25C03` block is paired with the `25C02` sequence. The
packaged arrays also use cell-specific truncation that differs from the complete public files.

## Version-1 decision

Version 1 preserves the cell identities and all valid aligned rows in the authoritative Zenodo files.
It is a clean-room, cleaned-source benchmark using the paper's fixed 25 °C cell split. It is **not** a
byte-for-byte reproduction of the authors' packaged MATLAB arrays.

A future paper-replay track may reproduce those arrays for diagnosis, but its results must remain
separate and must not be combined with the cleaned benchmark.

## Claim boundary

Until the cleaned protocol and a final GPR run receive independent review, the project may claim only
that the public data ingestion, audit and engineering evaluation pipeline were implemented. It may not
claim that the paper's performance was reproduced.

Sources:

- Dataset: <https://doi.org/10.5281/zenodo.3633835>
- Article: <https://doi.org/10.1038/s41467-020-15235-7>
- Authors' code repository (inspected, not copied because no license is displayed):
  <https://github.com/YunweiZhang/ML-identify-battery-degradation>
