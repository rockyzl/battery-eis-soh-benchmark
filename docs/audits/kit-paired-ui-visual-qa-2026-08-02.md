# KIT paired development-OOF UI visual QA

Date: 2026-08-02
Scope: local static UI only; no deployment, protected test, model selection or publication approval

## Evidence inspected

- Visible Windows Chrome 150 through Chrome DevTools Protocol.
- Local URL: `http://127.0.0.1:8765/` served from `demo/fleet-replay/`.
- Desktop viewport: 1440 × 900.
- Mobile viewport: 390 × 844.

Screenshots:

- `docs/audits/screenshots/kit-paired-ui-qa-desktop.png`
- `docs/audits/screenshots/kit-paired-ui-qa-mobile.png`

## Results

| Check | Result |
|---|---|
| Development/protected-test limitation visible at top | pass |
| 228 total cells and 180 paired-eligible cells visible | pass |
| Six B/D_CORE comparison cards present | pass |
| Desktop horizontal overflow | none: `1425 == 1425 px` |
| Mobile horizontal overflow | none: `390 == 390 px` |
| Mobile header, selector, banner and summary cards | readable single-column layout |
| Calendar/cyclic/profile/all tab interaction | pass; each selected state observed |
| HGB → ElasticNet selector change | pass |
| Fleet tile count after interaction | 228 |
| Console warnings/errors after cache-bypassing reload | none observed |

The screenshots cover the initial viewport rather than every vertical section. DOM contract tests
cover all six paired cards, counts, provenance fields and claim flags. Visual inspection found no CSS
fix required.

## Claim boundary

This is UI QA for a development-OOF historical replay. It is not evidence of protected/external test
performance, a production alarm, a selected winner, a causal EIS effect or publication readiness.
