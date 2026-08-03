# Early-warning integration hygiene audit — 2026-08-01

Observed at: `2026-08-01T19:05:29-05:00`

Branch/HEAD: `protocol/soh-target-v2` at
`57f98bd64bcf5b3d3886252ab363b591ffb12fe0`

Scope: integration audit plus a later bounded typing cleanup in `tests/test_early_warning.py`. No
scientific/runtime/config/packaging/CLI/slide file was edited. No training was run. The live task
ledger and this report are the only other files updated.

Verified starting source-of-truth hashes:

- live ledger before this wave update:
  `d422635969006a57da833452a35682fd8819e4786992d7d1355b6c797166190`;
- frozen decision record:
  `f64882d30a03575d7bac4630b7f8e772775c5cb7b1d749540da4f558a3bece7d`.

The working tree contains a large pre-existing uncommitted early-warning change set. Nothing in this
audit resets, stages, stashes or rewrites it.

## Integration boundary

The scientific engine owns its robustness config/schema, engine and engine tests. The runtime
supervisor owns the isolated worker, runner and supervisor tests. Their only cross-worker callable is:

```python
execute_engineering_analysis(
    *,
    eoc_zip: Path,
    eis_zip: Path,
    output_dir: Path,
    config_path: Path,
    progress: Callable[[dict[str, object]], None],
) -> dict[str, object]
```

The seven allowed scientific artifacts are fixed in the live ledger. QA must not start from a moving
working tree: it needs the decision hash, final config/schema hashes, final engine/test hashes, final
worker/runner/test hashes and a clean integration diff. If an author changes a file after reporting a
hash, QA restarts for that ownership packet.

## Findings and exact cleanup plan

| Priority | Finding and evidence | Owner / safe timing | Required cleanup |
|---|---|---|---|
| Blocker while authors are active | Full pytest stops during collection because `tests/test_kit_dev_training.py:13` imports the old `OofConfiguration`, `RunBudget`, `_keep_last_retry_candidate`, `run_development_engineering` and `run_grouped_oof` surface, while the engine author is replacing it with the frozen callable. | Scientific engine; **not safe** for integration to fix before checkpoint C. | Author aligns its owned tests to the frozen public callable and retained unit-level helpers, runs targeted tests, then reports hashes. Do not add compatibility aliases merely to make collection pass unless they are part of the reviewed contract. |
| Blocker while authors are active | `scripts/run_kit_dev_engineering.py:11` imports absent `run_development_engineering`; lines 55–65 call obsolete acknowledgment/heartbeat/count arguments instead of the frozen callable. | Runtime supervisor; **not safe** for integration to fix before runtime handoff. | Replace with the isolated-supervisor runner and frozen signature. Preserve explicit provisional acknowledgment outside the scientific callable, validate config/path inputs, and promote only the seven allowlisted artifacts after a successful child process. |
| Resolved bounded cleanup | The earlier moving-tree mypy snapshot contained 7 stable errors in `tests/test_early_warning.py` around the former lines 107/385/409. The earlier report of “9 full-mypy test errors” was not reproducible. | Integration worker; completed without production changes, ignores or casts. | `_signals` now has explicit typed keyword parameters. Policy mutations are typed `Callable[[RecommendationPolicy], RecommendationPolicy]` factories. Two deliberately wrong runtime types enter through a named JSON boundary, accurately exercising untyped external data while preserving the real `__post_init__` assertions. Targeted tests and type/lint/format checks pass. In-flight author errors remain their owners' responsibility. |
| Blocker while author is active | Ruff currently reports unused `replace` at `src/battery_eis_soh/kit_dev_training.py:18` and unnecessary nested spreads around lines 1183 and 1193; format check also reports that same owned file needs formatting. | Scientific engine; **not safe** before checkpoint C. | Engine author runs Ruff fixes deliberately, formats its file and re-runs targeted tests before hashing. Integration must not auto-format a live author file. |
| Decision required | `jsonschema` is a direct production dependency at `pyproject.toml:12`. It is imported at runtime by `src/battery_eis_soh/early_warning_registry.py:11` and the in-progress engine at `src/battery_eis_soh/kit_dev_training.py:23`; `uv.lock` is synchronized. No repository-wide “no new production dependency” rule, dependency-policy document, AGENTS instruction or ADR was found. The only related tracked wording says the slide source should not add a renderer dependency. Therefore “no new production dependency” is an earlier bounded-task constraint, not an authoritative repository-wide rule. | Orchestrator/maintainer after engine handoff; safe mechanically, but the dependency decision must still be explicit. | Keep it in production dependencies if strict registry/config validation remains on installed runtime paths, and record that rationale in the existing decision trail. Do **not** move it to the dev extra while installed modules import it. If production dependencies must stay minimal, replace runtime use with a reviewed built-in validator first—never silently skip strict schema execution. Do not modify this dependency until the orchestrator decides. |
| Compatibility watch | Existing CLI compatibility is presently sound: `src/battery_eis_soh/cli.py:39–60` adds the contract-demo command and deprecated alias, and `tests/test_cli.py:48–77` covers the non-scientific flags/warning. A focused CLI/contract suite passed 53/53. The new runner has not yet established final argv/exit semantics. | Runtime owner, then CLI/docs integration; safe only after runtime handoff. | Decide whether the supervisor remains a script or becomes a CLI subcommand. Only then add compatibility tests for missing acknowledgment, child failure, timeout, stale heartbeat, output collision and successful promotion. Do not reuse “smoke” wording for the contract demo. |
| Deliverable mismatch | The Markdown seminar source has 14 `---` separators, therefore 15 logical source slides. The checked PPTX contains 22 slide XML pages and zero PowerPoint notes slides. The source warns that export pagination may split dense slides, but the binary is not demonstrated to match the latest source or preserve its HTML speaker notes. | Documentation/export worker after scientific/runtime QA; safe after content freeze. | Choose and document one accepted outcome: redesign to one rendered page per logical slide, or retain 22 pages with a source-to-export page map. Regenerate from the final Markdown, verify count/citations/fonts/clipping visually, and either convert notes to real PowerPoint notes or explicitly state they remain source-only. Record source and PPTX hashes. |
| Stale status wording | Parser/contract status is stale at `docs/slides/battery-early-warning-research-seminar.md:20`, `:270`, `:403`; `docs/process/early-warning-development-process.md:294`, `:392–394`; and `docs/early-warning-runbook.md:224–229`. The wording still says parser fixes/current QA failure and real-data implementation “not run,” while the new EW-13–EW-17 wave is actively implementing a development-only engine. Timestamp completion remains unresolved, so formal eligibility is still blocked. | Documentation worker after author handoff and QA disposition; safe then. | Replace only status text supported by frozen evidence: bounded parser accepted; timestamp semantics provisional; robustness engine/supervisor implemented or failed as QA actually finds; no formal/protected/public claim. Do not insert performance numbers or call the development engine a formal training result. |

## Validation snapshot

These checks describe the moving shared tree and are not release acceptance:

- focused CLI/contract tests: `53 passed`;
- bounded test-typing cleanup: `32 passed` in `tests/test_early_warning.py`;
- bounded mypy: `uv run mypy src/battery_eis_soh/early_warning.py tests/test_early_warning.py`
  passed with no issues. Running mypy on the test file alone treats the installed local package as an
  untyped third-party distribution because it has no `py.typed` marker; including the owned production
  source file is the correct bounded command and does not change package typing policy;
- bounded Ruff check/format and scoped diff-check: passed;
- full pytest: collection error in the scientific author's in-flight test module;
- the earlier full-mypy snapshot had 37 errors; the 7 stable test errors are now resolved, while the
  shared author files remain in flight and require a fresh full run after hash freeze;
- Ruff lint: 3 in-flight scientific-engine findings;
- Ruff format: scientific engine would be reformatted;
- `uv lock --check`: passed;
- `git diff --check`: passed;
- PPTX structural inspection: 22 slide XML pages, zero notes pages; no visual QA performed.

After both authors freeze hashes, independent QA should run, in this order:

```bash
uv sync --extra dev --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv lock --check
git diff --check
```

The existing CI currently runs only pytest and Ruff (`.github/workflows/ci.yml:13–16`); it does not run
mypy or `uv lock --check`. After the full local gate passes, add those checks to CI in a separately
owned integration change if the maintainer approves. No `validate_repository.py` or tracked-file scan
script exists in this repository, so such commands must not be claimed as completed.

## Handoff order

1. Scientific engine completes checkpoints A–C, targeted tests and formatting, then reports hashes.
2. Runtime supervisor integrates only against that frozen signature/hash and reports its hashes plus
   argv/exit/timeout semantics. Its current bounded vocabulary is intended as exit `0` success, `2`
   engine error, `3` supervisor/contract error and `124` soft drain or resource limit; QA must verify
   this before it becomes authoritative.
3. Integration worker has repaired the unowned test typing. Dependency rationale, CLI/docs and slide
   export items still require separate orchestrator authorization after author handoff.
4. Independent QA checks the hash-bound packets, artifact allowlist, failure paths and full validation.
5. Documentation is synchronized to the QA result; only then may a bounded development run be
   considered. EW-10–EW-12 remain blocked.
