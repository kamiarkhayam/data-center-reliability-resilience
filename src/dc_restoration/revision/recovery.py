"""Paired, event-driven feeder restoration with site-resolved support ledgers."""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd

from .export import (
    availability_intervals,
    equipment_config,
    export_power,
    grid_interval,
    initial_state,
)
from .network import digest_json
from .settings import case_rules, demand, validate


def equipment_history(cfg, network, key):
    cache_key = (
        key,
        cfg["seed"],
        cfg["equipment_namespace"],
        cfg["equipment_replicate"],
        cfg["equipment_model"],
    )
    cache = getattr(network, "_equipment_cache", {})
    if cache_key in cache:
        return cache[cache_key]
    horizon = sum(network.damage_map[key].values()) + 1.0
    old, probe, spec = equipment_config(cfg, horizon)
    identity = ["equipment", key]
    if cfg["equipment_replicate"]:
        identity.append(cfg["equipment_replicate"])
    replicate = int(digest_json(identity)[:8], 16)
    intervals = availability_intervals(
        old, probe, replicate=replicate if cfg["equipment_model"] != "ideal" else None
    )
    cache[cache_key] = old, spec, intervals
    network._equipment_cache = cache
    return cache[cache_key]


def current_online(intervals, now):
    for start, end, online in intervals:
        if start - 1e-9 <= now < end - 1e-9:
            return online
    raise ValueError("Time outside locked equipment history")


def supported_rates(network, remaining, states, spec, now, online, internal, rule):
    raw, dc = network.state(remaining)
    if not dc.all():
        raise ValueError("DC grid interruption requires a different event model")
    allocation = np.zeros(len(network.mw))
    powers = []
    for site, zone in enumerate(network.zones):
        requested = float(network.mw[zone][raw[zone]].sum())
        p = export_power(
            states[site],
            spec,
            now=now,
            online=int(online[site]),
            internal_mw=internal,
            request_mw=requested,
            rule=rule,
        )
        allocation += network.allocate(raw, site, p)
        powers.append(p)
    rates = np.maximum(0, network.mw * raw - allocation)
    return rates, allocation, powers


def replay(network, cfg, key, order, case, capacities, *, choose=None):
    validate(cfg)
    old, spec, intervals = equipment_history(cfg, network, key)
    sites = old["dc"]["sites"]
    if sites != len(network.zones):
        raise ValueError("Mismatch between hardware sites and support zones")
    it, internal = demand(cfg)
    rule = case_rules(cfg)[case]
    has_dc = case != "A_no_dc"
    states = [initial_state(spec) for _ in range(sites)]
    damage = network.damage_map[key]
    remaining = set(damage)
    initial_raw, initial_dc = network.state(remaining)
    if not initial_dc.all():
        raise AssertionError("Grid-supplied DC condition not met")
    initial_mw = float(network.mw[initial_raw].sum())
    initial_critical = float(network.mw[initial_raw & network.critical].sum())
    energy = np.zeros(len(network.mw))
    timeline, ledger, repairs = [], [], []
    now = 0.0
    equipment_bounds = [end for _, end, _ in intervals]

    def record(start, end, unserved_rates, allocation, raw):
        dt = end - start
        grid_mw = float(network.mw[~raw].sum()) + (sites * internal if has_dc else 0.0)
        capacity = capacities[int(has_dc)]
        if grid_mw > capacity + 1e-7:
            raise AssertionError("Available planned upstream capacity is insufficient")
        energy[:] += unserved_rates * dt
        total = float(unserved_rates.sum())
        critical = float(unserved_rates[network.critical].sum())
        timeline.append(
            {
                "start_h": start,
                "end_h": end,
                "duration_h": dt,
                "unserved_mw": total,
                "critical_unserved_mw": critical,
                "service_fraction": 1 - total / initial_mw,
                "critical_service_fraction": 1 - critical / initial_critical
                if initial_critical
                else np.nan,
                "support_mw": float(allocation.sum()),
                "cumulative_ens_mwh": float(energy.sum()),
                "cumulative_critical_ens_mwh": float(energy[network.critical].sum()),
                "grid_supply_mw": grid_mw,
                "available_upstream_mw": capacity,
                "physically_disconnected_mw": float(network.mw[raw].sum()),
            }
        )

    while network.state(remaining)[0].any():
        if not remaining:
            raise AssertionError("Community remains disconnected after all repairs")
        online = current_online(intervals, now)
        if choose is None:
            edge = next(edge for edge in order if edge in remaining)
        else:
            edge = choose(
                network=network,
                remaining=remaining,
                damage=damage,
                energy=energy,
                states=states,
                spec=spec,
                now=now,
                online=online,
                internal=internal,
                rule=rule,
            )
        if edge not in remaining:
            raise AssertionError("Invalid/repeated repair action")
        finish = now + damage[edge]
        raw, _ = network.state(remaining)
        bounds = sorted({now, finish, *[t for t in equipment_bounds if now < t < finish]})
        for start, end in pairwise(bounds):
            online = current_online(intervals, start)
            pieces = []
            if has_dc:
                for site, zone in enumerate(network.zones):
                    request = float(network.mw[zone][raw[zone]].sum())
                    rows = grid_interval(
                        states[site],
                        spec,
                        start_h=start,
                        duration_h=end - start,
                        online=int(online[site]),
                        it_mw=it,
                        auxiliary_mw=internal - it,
                        request_mw=request,
                        rule=rule,
                        reserve_hours=cfg["reserve_hours"] if case != "B_dc_internal_only" else 0.0,
                        path_available=site not in cfg["disabled_paths"],
                    )
                    pieces.append(rows)
                    ledger.extend({"site": site, **row} for row in rows)
            subbounds = sorted({start, end, *[r["end_h"] for rs in pieces for r in rs]})
            for a, b in pairwise(subbounds):
                allocation = np.zeros(len(network.mw))
                for site, rs in enumerate(pieces):
                    midpoint = (a + b) / 2
                    row = next(r for r in rs if r["start_h"] <= midpoint <= r["end_h"])
                    allocation += network.allocate(raw, site, row["export_mw"])
                rates = network.mw * raw - allocation
                if (rates < -1e-8).any() or (allocation[~raw] > 1e-8).any():
                    raise AssertionError("Invalid community service accounting")
                record(a, b, np.maximum(rates, 0), allocation, raw)
        repairs.append(
            {
                "repair_index": len(repairs),
                "edge_id": edge,
                "start_h": now,
                "end_h": finish,
                "repair_duration_h": damage[edge],
            }
        )
        remaining.remove(edge)
        now = finish
    record(
        now,
        now,
        np.zeros(len(energy)),
        np.zeros(len(energy)),
        network.state(remaining)[0],
    )
    trajectory = pd.DataFrame(timeline)
    site_ledger = pd.DataFrame(ledger)
    export = float(site_ledger.generator_to_export_mwh.sum()) if has_dc else 0.0
    if has_dc and not np.isclose(
        sites * spec.fuel_capacity_mwh - sum(s.fuel_mwh for s in states),
        export,
        atol=1e-6,
    ):
        raise AssertionError("Portfolio fuel/export conservation failed")
    if not np.isclose(
        float((trajectory.support_mw * trajectory.duration_h).sum()), export, atol=1e-6
    ):
        raise AssertionError("Export does not equal community energy served")
    summary = {
        "scenario_key": key,
        "case": case,
        "effective_export_rule": rule,
        "demand_case": cfg["demand_case"],
        "equipment_replicate": cfg["equipment_replicate"],
        "equipment_model": cfg["equipment_model"],
        "retained_reserve_hours": cfg["reserve_hours"] if case != "B_dc_internal_only" else 0.0,
        "disabled_paths": ",".join(map(str, cfg["disabled_paths"])),
        "ens_mwh": float(energy.sum()),
        "critical_ens_mwh": float(energy[network.critical].sum()),
        "export_mwh": export,
        "dc_it_ens_mwh": 0.0,
        "dc_aux_ens_mwh": 0.0,
        "physical_completion_h": now,
        "repairs": len(repairs),
        "initial_unserved_mw": initial_mw,
        "initial_critical_unserved_mw": initial_critical,
        "min_remaining_design_backup_h": min(s.fuel_mwh / spec.design_internal_mw for s in states)
        if has_dc
        else np.nan,
        "mean_remaining_design_backup_h": np.mean(
            [s.fuel_mwh / spec.design_internal_mw for s in states]
        )
        if has_dc
        else np.nan,
        "depleted_sites": sum(s.fuel_mwh < 1e-8 for s in states) if has_dc else 0,
        "repair_order_hash": digest_json([r["edge_id"] for r in repairs]),
        "fuel_export_residual_mwh": abs(
            sites * spec.fuel_capacity_mwh - sum(s.fuel_mwh for s in states) - export
        )
        if has_dc
        else 0.0,
        "support_energy_residual_mwh": abs(
            float((trajectory.support_mw * trajectory.duration_h).sum()) - export
        ),
        "max_support_mw": float(trajectory.support_mw.max()),
        "min_upstream_margin_mw": float(
            (trajectory.available_upstream_mw - trajectory.grid_supply_mw).min()
        ),
    }
    for name, column in [
        ("total", "service_fraction"),
        ("critical", "critical_service_fraction"),
    ]:
        for p in (50, 90, 95):
            reached = trajectory.loc[trajectory[column] >= p / 100 - 1e-10]
            summary[f"t{p}_{name}_h"] = float(reached.start_h.min()) if len(reached) else np.nan
    final_sites = (
        pd.DataFrame(
            [
                {
                    "site": i,
                    "fuel_end_mwh": s.fuel_mwh,
                    "remaining_design_backup_hours": s.fuel_mwh / spec.design_internal_mw,
                }
                for i, s in enumerate(states)
            ]
        )
        if has_dc
        else pd.DataFrame()
    )
    per_load = pd.DataFrame(
        {
            "load_id": network.loads.load_id,
            "ens_mwh": energy,
            "critical": network.critical,
        }
    )
    return (
        summary,
        trajectory,
        site_ledger,
        pd.DataFrame(repairs),
        final_sites,
        per_load,
    )
