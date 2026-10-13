"""Continuous emergency-call equipment histories for fixed-order recovery."""

from itertools import pairwise

import numpy as np

from .design import dc_design
from .service import SiteSpec


def specification(old, config):
    d = dc_design(old["dc"])
    ready = (config["start_request_delay_seconds"] + config["load_acceptance_delay_seconds"]) / 3600
    return SiteSpec(
        installed_units=d["installed_units_per_site"],
        unit_mw=old["dc"]["generator_continuous_mw"],
        contingency_units=old["dc"]["redundant_units"],
        design_internal_mw=d["design_mw_per_site"],
        export_cap_mw=old["dc"]["export_cap_mw_per_site"],
        fuel_capacity_mwh=d["backup_electrical_energy_mwh_per_site"],
        ups_capacity_mwh=old["dc"]["it_mw_per_site"] * config["ups_it_bridge_minutes"] / 60,
        ups_power_mw=old["dc"]["it_mw_per_site"],
        export_authorized_h=old["dc"]["backup_response_hours"],
        reserve_start_h=ready,
        reserve_until_h=ready + config["protected_generator_hours"],
        reserve_demand_mw=d["design_mw_per_site"],
    )


def availability_intervals(old, config, replicate=None, repairs=True):
    spec = specification(old, config)
    sites = old["dc"]["sites"]
    horizon = max(config["horizons_hours"])
    ready = (config["start_request_delay_seconds"] + config["load_acceptance_delay_seconds"]) / 3600
    accept = config["load_acceptance_delay_seconds"] / 3600
    events = []
    for site in range(sites):
        for unit in range(spec.installed_units):
            if replicate is None:
                events.append((ready, site, 1))
                continue
            dc = old["dc"]
            rng = np.random.default_rng(
                [config["seed"], config["random_namespace"], replicate, site, unit]
            )
            initially_available = rng.random() < dc["initial_availability"]
            now = (
                ready
                if initially_available
                else max(ready, rng.exponential(dc["repair_mttr_hours"]) + accept)
                if repairs
                else np.inf
            )
            while now < horizon:
                if rng.random() < dc["failure_to_start"]:
                    now = (
                        now + rng.exponential(dc["repair_mttr_hours"]) + accept
                        if repairs
                        else np.inf
                    )
                    continue
                events.append((now, site, 1))
                fail = now + rng.exponential(dc["running_mttf_hours"])
                if fail >= horizon:
                    break
                events.append((fail, site, -1))
                now = (
                    fail + rng.exponential(dc["repair_mttr_hours"]) + accept if repairs else np.inf
                )
    change = {}
    for t, site, delta in events:
        if t <= horizon:
            change.setdefault(float(t), np.zeros(sites, dtype=int))[site] += delta
    boundaries = sorted(
        {
            0.0,
            ready,
            spec.export_authorized_h,
            spec.reserve_until_h,
            *map(float, config["horizons_hours"]),
            *change,
        }
    )
    boundaries = [t for t in boundaries if 0 <= t <= horizon]
    online = np.zeros(sites, dtype=int)
    rows = []
    for start, end in pairwise(boundaries):
        online += change.get(start, 0)
        if (online < 0).any() or (online > spec.installed_units).any():
            raise AssertionError("Invalid equipment availability history")
        rows.append((start, end, online.copy()))
    return rows
