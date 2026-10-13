# Reproduce the revised study

Run from the repository root after `python -m pip install -e ".[dev]"`. Outputs must be new directories. `dc-revision` is equivalent to `python scripts/run_revision.py`.

## External input bundle

The complete network, profile arrays and archived repairs are not distributed in Git; no public bundle download URL is currently provided. Missing inputs are never synthesized.

| File under `data/external/revision` | Contract |
|---|---|
| `case/network/{nodes,edges,loads,data_centers}.csv` | Original graph, baseline demands, sectors, critical indicators, profile IDs and DC source nodes |
| `case/dc_scenarios/anchor_zones.csv` | Nonoverlapping eligible load sets |
| `case/scenario_design/benchmark_hard_hurricane_scenario_manifest.csv` | Original scenario metadata |
| `case/scenario_design/benchmark_hard_damage_components.csv` | Damaged components and repair durations |
| `locked_scenarios.csv` | 131 unique cases and baseline checks |
| `fixed_orders.json` | Complete archived repair order per scenario, in `order` |
| `group_memberships.csv` | Overlapping scenario/subset memberships |
| `community_profile.csv` | 35,040 rows; `community_mw`, `critical_mw` |
| `profiles.npy`, `profile_ids.json` | Multiplier array: ordered profile rows, 35,040 columns |
| `samples.csv` | 960 chronological samples with scenario, sample, replicate, phase and offset |
| `planned_supply.json` | Frozen baseline scale and reference addition |
| `manifest.json` | Relative file names with SHA-256 fields, checked before runs |

With access to the original research workspace:

```sh
python scripts/prepare_revision_inputs.py --workspace /path/to/original-workspace --output data/external/revision
```

The helper copies only the listed inputs and preserves original relative provenance and hashes. It does not write to source files. Independently supplied bundles can use the same schema.

## Supply adequacy

```sh
python scripts/run_revision.py adequacy --inputs data/external/revision --output outputs/revision_adequacy
```

`matched_supply.csv` contains 18 combinations: six supply levels and 0/78/90 MW demand. EENS is MWh/year; shortfall duration is hours/year. No Monte Carlo intervals apply to these exact expectations. Fixed fleet and hardware parameters ship in `src/dc_restoration/revision/resources/`.

## Hurricane support and sensitivities

```sh
python scripts/run_revision.py hurricane --inputs data/external/revision --output outputs/revision_hurricane
python scripts/run_revision.py hurricane --inputs data/external/revision --output outputs/revision_resources --demands nominal design --reserve-hours 0 12 24 36 44 46 48 --connection-sensitivities --ideal-reference
```

The first command uses 131 scenarios, 32 equipment histories, 78 MW demand and a 44-hour reserve. The second covers the full demand/resource sweep. Connection removals and ideal equipment use zero reserve. Equipment identity and repair timing remain paired.

`summary.csv`, `scenario_means.csv` and `aggregate.csv` retain distinct aggregation definitions. Per-case files contain history-zero trajectories and per-load ENS. Community recovery averages 131 trajectories; critical recovery uses 130 with initial critical interruption. Each replay checks its archived baseline, fuel floor and equality of avoided ENS to support energy.

Use `--limit 1 --replicates 1` only for a separate smoke run. Runs are serial and do not resume or overwrite completed directories; retain interrupted output and choose a new destination. Limited runs are not paper results.

## Chronological internal service

```sh
python scripts/run_revision.py chronology --inputs data/external/revision --output outputs/revision_internal_service
```

All 960 sample pairs use nominal demand, private backup and the full supply addition: export disabled versus 44-hour-reserve export. Source random streams and load phases are preserved. `internal_service.csv` and sample files contain full-year and post-restoration measures. Energy columns are MWh; multiply by 1,000 for the SI's kWh/year presentation. Post-restoration losses are subsets of full-year losses. `--limit 1` is a smoke check.

This command reproduces the selected internal-service comparison, not earlier coordinated self-supply or joint annual-risk explorations.

## Economics

```sh
python scripts/run_revision.py economics --inputs data/external/revision --hurricane outputs/revision_hurricane --output outputs/revision_economics
```

Requires history-zero, nominal-demand load outcomes for all 30 historical cases under `private_no_export` and `reserve_44h`. Valuation is applied before averaging. Outputs are event damages, annual value/NPV and investment sensitivity. `--valuation` defaults to `configs/economics/interruption_damage.yaml`.

Reference financial inputs and exclusions are in `src/dc_restoration/revision/resources/economics.json`. They are illustrative 2025-dollar assumptions. Event intervals are not inferred from scenario counts; ENS is not added again as a monetary benefit.

## Support-responsive repair and policy evidence

```sh
python scripts/run_revision.py responsive --inputs data/external/revision --checkpoint /path/to/selected_graph_a2c.pt --output outputs/revision_responsive
```

The trusted selected checkpoint is external. Both demands use the same frozen policy, history zero and **zero reserve**. Support previews cannot change actual inventory or reveal future equipment transitions. `inference.json` records checkpoint hash, device and message-passing execution. The observation bridge is serial. `--limit 1` checks installation.

Earlier policy benchmarks and fresh 60-case validation use [legacy commands](legacy_reproduction.md). They retain their separate power-bounded controller and population. A newly trained checkpoint is not automatically equivalent to the archived selected model.

See [validation](revision_validation.md) for the scope of software and packaging checks. Research outputs and manuscript artifacts are excluded from this code-only release. Keep any locally generated results in ignored output directories.
