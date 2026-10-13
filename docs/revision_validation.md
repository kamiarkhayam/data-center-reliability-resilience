# Revision packaging validation

The numerical modules in `dc_restoration.revision` are extracted from the audited research implementation. Original source hashes and adaptations are recorded in [the source manifest](provenance/revision_source_manifest.json). Import paths, explicit input/output handling, and command-line orchestration changed; production research records were left untouched.

## Checks against original results

- All 18 matched-supply/demand combinations were recalculated; the nominal-demand EENS values agree with the reported table at its displayed precision.
- All 131 archived repair sequences were replayed for export disabled and 44-hour-reserve export using equipment history zero. Community ENS, critical ENS, export energy and physical completion time agree exactly with the archived outputs in this runtime.
- One complete paired chronological hurricane year was rerun. Community and critical ENS, internal IT/auxiliary ENS, export and ending fuel agree exactly with its archived sample.
- Economics was recalculated from reproduced history-zero load outcomes for all 30 historical cases. The accounting matches the archived calculation within floating-point precision; economic results are not included in this release.
- One support-responsive scenario was checked at both demand levels using the archived selected checkpoint. Repair-order hashes and interruption outcomes match the original responsive trajectories.

[Machine-readable check metadata](provenance/revision_regression.json) records the tested scope without study outputs. These checks verify packaging equivalence; they are not a rerun of all 32 equipment histories or all 960 chronological pairs. Full research inputs and outputs remain outside the public repository.

## Automated tests

The test suite covers inherited policy functionality and the revised physics, including direct-enumeration capacity checks, exact reserve crossings, contingency headroom, generator readiness, internal backup priority, UPS recharge, fuel continuity, disjoint supply/damage accounting, export suspension during internal shortfalls and economic identities. The revised backup and chronological tests were adapted from the audited source tests.

The code-only release check passed **260 tests and nine subtests**. All 86 modules imported, the six-node fixture completed, Ruff and formatting checks passed, and both source and wheel distributions built. Software-check status is distinct from research findings.

The public release contains code, configurations, source provenance and invented fixtures. Result tables, figure images, manuscript highlights and reported study outcomes are excluded. `python scripts/check_release_contents.py` checks tracked paths against the release boundary and runs in CI; review of prose and numerical examples remains necessary. The economic test uses invented values.

Run `python -m pytest --basetemp=.runtime/pytest` after installation. Full suite, style checks, installation validation and package build are also configured in GitHub Actions. Full research inputs and selected weights remain external. The Windows packaging regression used the available CPU scientific runtime; the inherited CI lock and original GPU training runtime are distinct records.
