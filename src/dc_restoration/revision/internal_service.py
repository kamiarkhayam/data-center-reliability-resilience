"""Private backup for interrupted internal load blocks, not utility export.

The grid-served and isolated fractions are disjoint equivalent load blocks.
Pooling site fuel, UPS energy, and available units is an aggregate approximation,
not a resolved transfer-switch topology or voltage-trigger simulation.
"""

import math

from .export import equipment_config
from .service import check_record, dispatch_interval

__all__ = ["equipment_config", "private_interval"]
EPS = 1e-8


def private_interval(
    state,
    spec,
    *,
    start_h,
    duration_h,
    it_mw,
    auxiliary_mw,
    grid_fraction,
    online_units,
    recharge,
    grid_charge_offer_mw=0.0,
    fuel_delivery_mwh=0.0,
):
    """One storage update for the entire site, including disjoint utility loads."""
    if not math.isfinite(grid_fraction) or not 0 <= grid_fraction <= 1:
        raise ValueError("Grid-served fraction must lie in [0, 1]")
    if not math.isfinite(grid_charge_offer_mw) or grid_charge_offer_mw < 0:
        raise ValueError("Invalid charging offer")
    internal = it_mw + auxiliary_mw
    if internal > spec.design_internal_mw + EPS:
        raise ValueError("Internal demand exceeds locked design envelope")
    if grid_fraction < 1 - EPS:
        if grid_charge_offer_mw > EPS:
            raise ValueError("No recharge during internal interruption or islanding")
        isolated = 1 - grid_fraction
        rows = dispatch_interval(
            state,
            spec,
            start_h=start_h,
            duration_h=duration_h,
            it_mw=it_mw * isolated,
            auxiliary_mw=auxiliary_mw * isolated,
            online_units=online_units,
            fuel_delivery_mwh=fuel_delivery_mwh,
        )
        for row in rows:
            dt = row["duration_h"]
            row["it_demand_mw"] = it_mw
            row["auxiliary_demand_mw"] = auxiliary_mw
            row["grid_to_it_mwh"] = it_mw * grid_fraction * dt
            row["grid_to_aux_mwh"] = auxiliary_mw * grid_fraction * dt
            row["grid_connected"] = grid_fraction > 0
            row["utility_served_fraction"] = grid_fraction
            row["operating_mode"] = (
                "split_utility_and_isolated_internal_blocks"
                if grid_fraction > 0
                else "whole_site_internal_island"
            )
            check_record(row, spec)
        return rows
    charge = min(
        grid_charge_offer_mw,
        recharge["rate_mw"],
        max(0.0, spec.design_internal_mw - internal),
    )
    now, end, rows = start_h, start_h + duration_h, []
    while now < end - 1e-10:
        remaining = max(0.0, recharge["target_mwh"] - state.ups_mwh)
        rate = charge if remaining > EPS else 0.0
        span = min(end - now, remaining / rate) if rate > EPS else end - now
        part = dispatch_interval(
            state,
            spec,
            start_h=now,
            duration_h=span,
            it_mw=it_mw,
            auxiliary_mw=auxiliary_mw,
            online_units=online_units,
            grid_connected=True,
            grid_offer_mw=internal + rate,
            ups_charge_request_mw=rate,
            fuel_delivery_mwh=fuel_delivery_mwh if not rows else 0.0,
        )
        rows.extend(
            {
                "utility_served_fraction": 1.0,
                "operating_mode": "utility_supplied_internal_load",
                **r,
            }
            for r in part
        )
        now += span
    return rows
