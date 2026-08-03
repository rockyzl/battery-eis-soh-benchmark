# Live training tasks v2

Status: `protocol implementation in progress; formal training not authorized`

| ID | Task | Status | Gate/evidence |
|---|---|---|---|
| V2-01 | Register SoH-percent primary target and `Q0` ablations | Complete | `config/target_registry.json` |
| V2-02 | Make all-valid rows primary; keep 80% EOL as sensitivity | Complete in v2 protocol/smoke | Protocol and tests |
| V2-03 | Report SoH error in percentage points without clipping | Complete in engineering evaluator | Unit tests and manifest |
| V2-04 | Independently review protocol and registry | Partial | Content review approved; clean-commit hash-bound review still required |
| V2-05 | Build selection-only train-label view | Partial | Loader and audit command complete; nested runner integration pending |
| V2-06 | Build permission-separated test evaluator | Pending | Exactly-one-read denial/approval tests |
| V2-07 | Implement deterministic target/feature registries for nested runner | Partial | Target registry complete; feature/search registries pending |
| V2-08 | Implement nested cell-level OOF runner | Pending | Four outer and three inner group folds |
| V2-09 | Add real-backend monitored smoke | Pending | Heartbeat ≤30 s, resource/budget/provenance evidence |
| V2-10 | Authorize formal nested selection | Blocked | Requires V2-04 through V2-09 |
| V2-11 | Final fit | Blocked | Requires independent selection decision |
| V2-12 | Fixed-test evaluation | Blocked | Requires separate hash-bound authorization |
| V2-13 | Result/publication/resume review | Blocked | Requires immutable accepted scientific artifact |

No blocked item may be advanced by an agent without the corresponding external review and human
authorization artifact.
