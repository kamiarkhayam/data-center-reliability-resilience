# Methods in the revised software

The application assessment is implemented in `dc_restoration.revision`:

1. **Supply adequacy:** independent two-state capacity convolution and integration over the annual profile (`supply.py`, `supply_traces.py`, `workflows.py`).
2. **Hurricane support:** archived repair replay with service integration at repair, equipment, authorization and fuel transitions (`network.py`, `recovery.py`, `equipment.py`, `export.py`). Fixed-repair avoided ENS equals delivered support.
3. **Internal service:** paired chronological supply, generators, fuel, UPS and damage (`chronology.py`, `internal_service.py`, `service.py`, `storm.py`). Running exposure follows actual generation.
4. **Economics:** paired load-level sector/duration valuation and annualization (`economics.py` and existing `economics/duration_damage.py`).

The [source manifest](provenance/revision_source_manifest.json) identifies audited source routines and packaging adaptations. [Validation](revision_validation.md) distinguishes regression checks from original production runs.

Equipment histories are averaged within scenarios, then unique scenarios receive equal weight. Aggregate reductions divide ensemble means; mean scenario percentages divide within each scenario first. Zero baseline critical ENS is excluded from critical percentages. Bootstrap intervals resample scenario means 4,000 times. Recovery curves and economics use history zero, with different populations documented in [reproduction](reproduction.md).

The primary comparison uses fixed frozen-policy repairs. Optional responsive repairs connect current support previews to the inherited graph observation schema without changing weights. Architecture, imitation initialization, actor–critic learning and earlier benchmarks remain in [legacy methods](legacy_methods.md).
