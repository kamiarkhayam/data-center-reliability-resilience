"""Facility sizing inherited from the audited archetype."""

import numpy as np


def dc_design(dc: dict) -> dict:
    demand = dc["it_mw_per_site"] * dc["reference_pue"]
    peak = dc["it_mw_per_site"] * dc["design_pue"]
    size = dc["generator_continuous_mw"]
    required = int(np.ceil(peak / size))
    installed = required + dc["redundant_units"]
    reserve_available = (installed - dc["redundant_units"]) * size
    return {
        "sites": dc["sites"],
        "nominal_mw_per_site": demand,
        "design_mw_per_site": peak,
        "portfolio_nominal_mw": demand * dc["sites"],
        "required_units_per_site": required,
        "installed_units_per_site": installed,
        "installed_continuous_mw_per_site": installed * size,
        "reserve_adjusted_available_mw_per_site": reserve_available,
        "nominal_export_headroom_mw_per_site": min(
            dc["export_cap_mw_per_site"], max(0.0, reserve_available - demand)
        ),
        "design_export_headroom_mw_per_site": min(
            dc["export_cap_mw_per_site"], max(0.0, reserve_available - peak)
        ),
        "backup_electrical_energy_mwh_per_site": peak * dc["fuel_hours_at_design_demand"],
        "status": "proposed_synthetic_archetype_not_existing_facility_or_export_design",
    }
