"""Explicit grid-fed DC / generator-fed community connection extension.

This is not ordinary grid injection and not the v5 self-islanding mode. The
caller must provide an electrically enabled, isolated community support path.
Fuel remains an electrical-energy equivalent without a part-load fuel curve.
"""

from __future__ import annotations

import math

from .equipment import availability_intervals, specification
from .service import dispatch_interval, initial_state, validate_state

__all__ = [
    "availability_intervals",
    "equipment_config",
    "export_power",
    "grid_interval",
    "initial_state",
]


def equipment_config(cfg, horizon):
    old = cfg["facility"]["old"]
    probe = dict(cfg["facility"]["service"])
    probe.update(
        seed=cfg["seed"],
        random_namespace=cfg["equipment_namespace"],
        horizons_hours=[float(horizon)],
    )
    return old, probe, specification(old, probe)


def export_power(
    state,
    spec,
    *,
    now,
    online,
    internal_mw,
    request_mw,
    rule="shared_fuel",
    path_available=True,
    reserve_hours=0.0,
):
    validate_state(state, spec)
    if rule not in {"disabled", "shared_fuel", "full_reserve"}:
        raise ValueError("Unknown export rule")
    if not isinstance(online, int) or not 0 <= online <= spec.installed_units:
        raise ValueError("Invalid available-unit count")
    if any(not math.isfinite(v) or v < 0 for v in (now, internal_mw, request_mw)):
        raise ValueError("Invalid power/time")
    floor = retained_floor(spec, rule, reserve_hours)
    if (
        rule == "disabled"
        or not path_available
        or now < spec.export_authorized_h - 1e-10
        or state.fuel_mwh <= floor + 1e-9
    ):
        return 0.0
    # Keep internal-demand plus one-unit power headroom despite grid supply.
    power = min(
        request_mw,
        spec.export_cap_mw,
        max(0.0, (online - spec.contingency_units) * spec.unit_mw - internal_mw),
    )
    if state.export_remaining_mwh is not None and state.export_remaining_mwh <= 1e-9:
        return 0.0
    return power


def retained_floor(spec, rule, reserve_hours):
    if isinstance(reserve_hours, bool) or not math.isfinite(reserve_hours) or reserve_hours < 0:
        raise ValueError("Invalid retained reserve")
    floor = spec.design_internal_mw * reserve_hours
    if floor > spec.fuel_capacity_mwh + 1e-9:
        raise ValueError("Retained reserve exceeds tank capacity")
    return spec.fuel_capacity_mwh if rule == "full_reserve" else floor


def grid_interval(
    state,
    spec,
    *,
    start_h,
    duration_h,
    online,
    it_mw,
    auxiliary_mw,
    request_mw,
    rule="shared_fuel",
    path_available=True,
    grid_available=True,
    reserve_hours=0.0,
):
    """Dispatch constant equipment/topology interval, splitting at fuel limits."""
    if not math.isfinite(duration_h) or duration_h <= 0:
        raise ValueError("Positive finite duration required")
    if any(not math.isfinite(v) or v < 0 for v in (it_mw, auxiliary_mw)):
        raise ValueError("Invalid internal demand")
    if not grid_available:
        # Unsupported by the inherited hurricane set, but preserve internal
        # priority if a future model explicitly supplies a DC grid outage.
        return dispatch_interval(
            state,
            spec,
            start_h=start_h,
            duration_h=duration_h,
            it_mw=it_mw,
            auxiliary_mw=auxiliary_mw,
            online_units=online,
            policy="internal_only",
        )
    rows = []
    now, end = start_h, start_h + duration_h
    while now < end - 1e-10:
        p = export_power(
            state,
            spec,
            now=now,
            online=online,
            internal_mw=it_mw + auxiliary_mw,
            request_mw=request_mw,
            rule=rule,
            path_available=path_available,
            reserve_hours=reserve_hours,
        )
        stop = end
        if now < spec.export_authorized_h < stop:
            stop = spec.export_authorized_h
        if p > 0:
            usable = max(0.0, state.fuel_mwh - retained_floor(spec, rule, reserve_hours))
            if state.export_remaining_mwh is not None:
                usable = min(usable, state.export_remaining_mwh)
            stop = min(stop, now + usable / p)
        dt = stop - now
        fuel_before = state.fuel_mwh
        used = min(fuel_before, p * dt)
        state.fuel_mwh = max(0.0, fuel_before - used)
        if state.export_remaining_mwh is not None:
            state.export_remaining_mwh = max(0.0, state.export_remaining_mwh - used)
        row = {
            "start_h": now,
            "end_h": stop,
            "duration_h": dt,
            "operating_mode": "grid_supplied_dc_separate_export_connection",
            "online_units": online,
            "export_mw": p,
            "generator_to_export_mwh": used,
            "grid_to_it_mwh": it_mw * dt,
            "grid_to_aux_mwh": auxiliary_mw * dt,
            "it_unserved_mwh": 0.0,
            "auxiliary_unserved_mwh": 0.0,
            "generator_to_it_mwh": 0.0,
            "generator_to_aux_mwh": 0.0,
            "ups_to_it_mwh": 0.0,
            "fuel_start_mwh": fuel_before,
            "fuel_end_mwh": state.fuel_mwh,
            "ups_end_mwh": state.ups_mwh,
            "remaining_design_backup_hours": state.fuel_mwh / spec.design_internal_mw,
            "retained_floor_mwh": retained_floor(spec, rule, reserve_hours),
        }
        if not math.isclose(used, p * dt, abs_tol=1e-7) or dt <= 0:
            raise AssertionError("Fuel/export energy conservation failed")
        validate_state(state, spec)
        if state.fuel_mwh < retained_floor(spec, rule, reserve_hours) - 1e-8:
            raise AssertionError("Export crossed retained fuel floor")
        rows.append(row)
        now = stop
    return rows
