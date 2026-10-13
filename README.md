# Reliability and Resilience Implications of Data-Center Demand and Emergency Support in Distribution Systems

Research software by **Kamiar Khayambashi and Negin Alemazkoor**, University of Virginia.

This repository implements an assessment of how data-center demand and emergency backup export affect distribution-system reliability and hurricane recovery. It combines supply adequacy, equipment and fuel constraints, chronological internal-service simulation, and incremental export-enablement economics in a benchmark-derived synthetic testbed.

**This is a code-only release.** It contains implementation, model configurations, source provenance, and invented test fixtures. Research result tables, result figures, manuscript files, and highlights are excluded. Full study inputs and trained weights are supplied separately; this repository alone does not reproduce the complete paper.

## Assessment workflows

| Workflow | Comparison | Implementation |
|---|---|---|
| Supply adequacy | Community supply with 0, 78, or 90 MW of data-center demand at matched supply capacity | Stationary generation-capacity distribution integrated over annual demand |
| Hurricane support | Community export disabled or enabled with identical physical repair sequences | Paired equipment histories, site export limits, and retained fuel reserves |
| Internal service | Internal IT and auxiliary service with export disabled or enabled | Chronological supply outages, generator operation, fuel, UPS, and hurricane recovery |
| Economics | Incremental participation in community export | Load-level interruption valuation, annualization, and investment sensitivity |

GraphRL-A2C supplies frozen repair sequences for the primary hurricane comparison. Optional support-responsive replay and earlier policy benchmarks are documented separately. Annual adequacy and event-conditioned hurricane interruption use different accounting periods and are not directly added.

## Quick start: validate the software

Use Python 3.11 or newer. Windows with Python 3.13 is the CI-tested platform.

```sh
git clone https://github.com/kamiarkhayam/data-center-reliability-resilience.git
cd data-center-reliability-resilience
python -m venv .venv
```

Activate the environment with `.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` in a POSIX shell, then run:

```sh
python -m pip install -e ".[dev]"
python scripts/check_release_contents.py
python scripts/validate_installation.py
python -m pytest --basetemp=.runtime/pytest
python scripts/run_revision.py --help
```

These checks use invented cases and do not require research inputs or reproduce paper results. They exercise allocation, generator headroom, fuel reserves, internal backup, chronological accounting, economics, and policy interfaces.

For the pinned Windows CI environment, install `requirements-lock.txt` before the editable package. Other platforms resolve dependencies from `pyproject.toml`. Optional map preparation also requires the `mapping` extra.

## Run the study workflows with your input bundle

The full network and demand-profile arrays, scenario records, archived repair orders, and chronological samples are external inputs. The selected checkpoint is additionally required for support-responsive replay. **A complete public input-bundle download is not currently available.**

The [input contract and execution guide](docs/reproduction.md) specifies the files and schemas. If you have the original research workspace, assemble the bundle with:

```sh
python scripts/prepare_revision_inputs.py --workspace /path/to/original-workspace --output data/external/revision
```

This helper collects existing inputs; it does not download or reconstruct missing research records. Once a valid bundle is present:

```sh
python scripts/run_revision.py adequacy --inputs data/external/revision --output outputs/revision_adequacy
python scripts/run_revision.py hurricane --inputs data/external/revision --output outputs/revision_hurricane
python scripts/run_revision.py chronology --inputs data/external/revision --output outputs/revision_internal_service
python scripts/run_revision.py economics --inputs data/external/revision --hurricane outputs/revision_hurricane --output outputs/revision_economics
```

The installed `dc-revision` command is equivalent. Runs require new output directories, validate input hashes, and record configuration, code, resource, and runtime information. Execution is serial and does not resume interrupted runs. Generated outputs stay local in ignored directories.

## Model configuration

The reference portfolio contains three sites, each with 20 MW IT demand, 26 MW operating demand, and a 30 MW design basis. Each has 13 generators rated at 2.725 MW, a 5 MW export connection, 1,440 MWh of electrical-equivalent fuel capacity, and an IT-only UPS. Export requires full internal utility service and reserves generation headroom for internal demand plus one contingency unit. The reference retained fuel floor is 44 design-load hours.

These are study inputs, not site-specific deployment recommendations. Resource parameters and citations are documented in [parameter sources](docs/revision_sources.md), with modeling boundaries in [model scope](docs/model_scope.md). The current site-level controller is distinct from the earlier pooled E12/E24 support model.

## Repository guide

| Location | Contents |
|---|---|
| `src/dc_restoration/revision/` | Current assessment implementations and resource configurations |
| `scripts/run_revision.py` | Assessment command-line entry point |
| `configs/` | Economic assumptions and supporting policy/testbed settings |
| `tests/` | Synthetic software checks and the invented small network fixture |
| `docs/` | Methods, input contracts, source lineage, and validation scope |

- [Current methods](docs/methods.md), [workflow instructions](docs/reproduction.md), and [software validation](docs/revision_validation.md)
- [Earlier RL methods](docs/legacy_methods.md) and [supporting reproduction guide](docs/legacy_reproduction.md)
- [Source provenance](docs/provenance/revision_source_manifest.json) and [introductory map workflow](docs/intro_map_reproduction.md)
- [Contribution and release-content rules](CONTRIBUTING.md)

Earlier policy benchmarks and fiscal workflows remain available as legacy methods. The Python package and import names are retained for compatibility.

## Citation and license

[CITATION.cff](CITATION.cff) provides the software title and authors without asserting journal acceptance or a DOI. Code is distributed under the [MIT License](LICENSE); external datasets retain their own terms.
