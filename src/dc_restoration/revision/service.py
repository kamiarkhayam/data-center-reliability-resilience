"""Stateful site-level electricity and shared-fuel accounting.

Fuel and UPS storage use delivered-electrical-energy equivalents, not a diesel
fuel curve. The caller supplies actual grid availability, generator availability,
export eligibility, and authorization. This module does not decide grid dispatch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EPS = 1e-9
POLICIES = {"internal_only", "shared_export", "reserve48_export"}


@dataclass(frozen=True)
class SiteSpec:
    installed_units: int
    unit_mw: float
    contingency_units: int
    design_internal_mw: float
    export_cap_mw: float
    fuel_capacity_mwh: float
    ups_capacity_mwh: float
    ups_power_mw: float
    export_authorized_h: float
    reserve_start_h: float
    reserve_until_h: float
    reserve_demand_mw: float

    def __post_init__(self):
        if (
            isinstance(self.installed_units, bool)
            or not isinstance(self.installed_units, int)
            or self.installed_units < 1
        ):
            raise ValueError("Positive integer installed-unit count required")
        if (
            isinstance(self.contingency_units, bool)
            or not isinstance(self.contingency_units, int)
            or not 0 <= self.contingency_units < self.installed_units
        ):
            raise ValueError("Invalid contingency headroom")
        for name in ["unit_mw", "design_internal_mw", "fuel_capacity_mwh"]:
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"Positive finite {name} required")
        for name in [
            "export_cap_mw",
            "ups_capacity_mwh",
            "ups_power_mw",
            "export_authorized_h",
            "reserve_start_h",
            "reserve_until_h",
            "reserve_demand_mw",
        ]:
            if not math.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"Nonnegative finite {name} required")
        if self.reserve_until_h < self.reserve_start_h:
            raise ValueError("Reserve deadline precedes its start")


@dataclass
class SiteState:
    fuel_mwh: float
    ups_mwh: float
    export_remaining_mwh: float | None = None


def initial_state(spec, export_budget_mwh=None):
    state = SiteState(spec.fuel_capacity_mwh, spec.ups_capacity_mwh, export_budget_mwh)
    validate_state(state, spec)
    return state


def validate_state(state, spec):
    for value, upper, name in [
        (state.fuel_mwh, spec.fuel_capacity_mwh, "fuel"),
        (state.ups_mwh, spec.ups_capacity_mwh, "UPS"),
    ]:
        if not math.isfinite(value) or not -EPS <= value <= upper + EPS:
            raise ValueError(f"Invalid {name} inventory")
    if state.export_remaining_mwh is not None and (
        not math.isfinite(state.export_remaining_mwh) or state.export_remaining_mwh < -EPS
    ):
        raise ValueError("Invalid remaining export allowance")


def reserve_floor(spec, now):
    return spec.reserve_demand_mw * max(0.0, spec.reserve_until_h - max(now, spec.reserve_start_h))


def dispatch_interval(
    state,
    spec,
    *,
    start_h,
    duration_h,
    it_mw,
    auxiliary_mw,
    online_units,
    grid_connected=False,
    grid_offer_mw=0.0,
    export_request_mw=0.0,
    export_path_available=True,
    policy="internal_only",
    ups_charge_request_mw=0.0,
    fuel_delivery_mwh=0.0,
):
    """Advance one site, splitting exactly at storage/guard/control boundaries.

    IT and auxiliary loads are served before export. UPS serves IT only and is
    charged only from explicitly offered utility power. Utility-connected and
    generator-island modes are mutually exclusive; no implicit parallel export.
    Startup is represented by caller-provided available units, not a 15-min delay.
    """
    validate_state(state, spec)
    if policy not in POLICIES:
        raise ValueError("Unknown service policy")
    for name, value in {
        "start_h": start_h,
        "duration_h": duration_h,
        "it_mw": it_mw,
        "auxiliary_mw": auxiliary_mw,
        "grid_offer_mw": grid_offer_mw,
        "export_request_mw": export_request_mw,
        "ups_charge_request_mw": ups_charge_request_mw,
        "fuel_delivery_mwh": fuel_delivery_mwh,
    }.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Nonnegative finite {name} required")
    if duration_h <= 0 or it_mw + auxiliary_mw > spec.design_internal_mw + EPS:
        raise ValueError("Positive interval within declared design demand required")
    if (
        isinstance(online_units, bool)
        or not isinstance(online_units, int)
        or not 0 <= online_units <= spec.installed_units
    ):
        raise ValueError("Invalid online-unit count")
    if not isinstance(grid_connected, bool) or not isinstance(export_path_available, bool):
        raise TypeError("Explicit Boolean connection/path states required")
    if not grid_connected and grid_offer_mw > EPS:
        raise ValueError("An islanded site cannot import utility power")
    accepted = min(fuel_delivery_mwh, spec.fuel_capacity_mwh - state.fuel_mwh)
    rejected = fuel_delivery_mwh - accepted
    pre_delivery = state.fuel_mwh
    state.fuel_mwh += accepted
    end_h, now = start_h + duration_h, start_h
    records = []
    for _ in range(100):
        remaining = end_h - now
        if remaining <= 1e-12:
            break
        if state.fuel_mwh < EPS:
            state.fuel_mwh = 0.0
        if state.ups_mwh < EPS:
            state.ups_mwh = 0.0
        fuel_start, ups_start = state.fuel_mwh, state.ups_mwh
        grid_it = min(it_mw, grid_offer_mw) if grid_connected else 0.0
        grid_aux = min(auxiliary_mw, max(0.0, grid_offer_mw - grid_it)) if grid_connected else 0.0
        capacity = online_units * spec.unit_mw if not grid_connected and state.fuel_mwh > 0 else 0.0
        gen_it = min(it_mw, capacity)
        gen_aux = min(auxiliary_mw, max(0.0, capacity - gen_it))
        internal_gen = gen_it + gen_aux
        missing_it = max(0.0, it_mw - grid_it - gen_it)
        ups_it = min(missing_it, spec.ups_power_mw) if state.ups_mwh > 0 else 0.0
        it_unserved = max(0.0, missing_it - ups_it)
        aux_unserved = max(0.0, auxiliary_mw - grid_aux - gen_aux)
        charge = 0.0
        if (
            grid_connected
            and missing_it <= EPS
            and aux_unserved <= EPS
            and state.ups_mwh < spec.ups_capacity_mwh - EPS
        ):
            charge = min(ups_charge_request_mw, max(0.0, grid_offer_mw - grid_it - grid_aux))
        export = 0.0
        if (
            policy != "internal_only"
            and not grid_connected
            and export_path_available
            and now >= spec.export_authorized_h - 1e-12
            and missing_it <= EPS
            and aux_unserved <= EPS
            and (state.export_remaining_mwh is None or state.export_remaining_mwh > EPS)
        ):
            export = min(
                export_request_mw,
                spec.export_cap_mw,
                max(0.0, capacity - internal_gen - spec.contingency_units * spec.unit_mw),
            )
        reserve_active = policy == "reserve48_export" and now < spec.reserve_until_h - 1e-12
        floor = reserve_floor(spec, now) if policy == "reserve48_export" else 0.0
        release_rate = spec.reserve_demand_mw if now >= spec.reserve_start_h - 1e-12 else 0.0
        free_fuel = fuel_start - floor
        if reserve_active and free_fuel <= EPS:
            export = min(export, max(0.0, release_rate - internal_gen))
            if free_fuel < -EPS:
                export = 0.0
        rate = internal_gen + export
        span = remaining
        for boundary in [
            spec.export_authorized_h,
            spec.reserve_start_h,
            spec.reserve_until_h,
        ]:
            if now + 1e-12 < boundary < now + span:
                span = boundary - now
        if rate > EPS:
            span = min(span, state.fuel_mwh / rate)
        if ups_it > EPS:
            span = min(span, state.ups_mwh / ups_it)
        if charge > EPS:
            span = min(span, (spec.ups_capacity_mwh - state.ups_mwh) / charge)
        if export > EPS and state.export_remaining_mwh is not None:
            span = min(span, state.export_remaining_mwh / export)
        if reserve_active and free_fuel > EPS and rate > release_rate + EPS:
            span = min(span, free_fuel / (rate - release_rate))
        if span <= 1e-12:
            raise AssertionError("Service integrator stalled at a resource boundary")
        state.fuel_mwh = max(0.0, state.fuel_mwh - rate * span)
        state.ups_mwh = max(
            0.0, min(spec.ups_capacity_mwh, state.ups_mwh + (charge - ups_it) * span)
        )
        if state.export_remaining_mwh is not None:
            state.export_remaining_mwh = max(0.0, state.export_remaining_mwh - export * span)
        record = {
            "start_h": now,
            "end_h": now + span,
            "duration_h": span,
            "online_units": online_units,
            "grid_connected": grid_connected,
            "it_demand_mw": it_mw,
            "auxiliary_demand_mw": auxiliary_mw,
            "grid_to_it_mwh": grid_it * span,
            "grid_to_aux_mwh": grid_aux * span,
            "grid_to_ups_mwh": charge * span,
            "generator_to_it_mwh": gen_it * span,
            "generator_to_aux_mwh": gen_aux * span,
            "generator_to_export_mwh": export * span,
            "ups_to_it_mwh": ups_it * span,
            "it_unserved_mwh": it_unserved * span,
            "auxiliary_unserved_mwh": aux_unserved * span,
            "fuel_start_mwh": pre_delivery if not records else fuel_start,
            "fuel_delivery_accepted_mwh": accepted if not records else 0.0,
            "fuel_delivery_rejected_mwh": rejected if not records else 0.0,
            "fuel_end_mwh": state.fuel_mwh,
            "ups_start_mwh": ups_start,
            "ups_end_mwh": state.ups_mwh,
            "reserve_floor_end_mwh": reserve_floor(spec, now + span)
            if policy == "reserve48_export"
            else 0.0,
            "export_power_mw": export,
        }
        check_record(record, spec)
        records.append(record)
        now += span
    else:
        raise AssertionError("Too many resource sub-intervals")
    validate_state(state, spec)
    if not math.isclose(sum(r["duration_h"] for r in records), duration_h, abs_tol=1e-8):
        raise AssertionError("Service interval lost elapsed time")
    return records


def check_record(r, spec):
    dt = r["duration_h"]
    checks = [
        r["grid_to_it_mwh"]
        + r["generator_to_it_mwh"]
        + r["ups_to_it_mwh"]
        + r["it_unserved_mwh"]
        - r["it_demand_mw"] * dt,
        r["grid_to_aux_mwh"]
        + r["generator_to_aux_mwh"]
        + r["auxiliary_unserved_mwh"]
        - r["auxiliary_demand_mw"] * dt,
        r["fuel_start_mwh"]
        + r["fuel_delivery_accepted_mwh"]
        - r["fuel_end_mwh"]
        - r["generator_to_it_mwh"]
        - r["generator_to_aux_mwh"]
        - r["generator_to_export_mwh"],
        r["ups_start_mwh"] + r["grid_to_ups_mwh"] - r["ups_to_it_mwh"] - r["ups_end_mwh"],
    ]
    if any(abs(x) > 1e-7 for x in checks):
        raise AssertionError(f"Site energy balance failed: {checks}")
    if (
        r["generator_to_it_mwh"] + r["generator_to_aux_mwh"] + r["generator_to_export_mwh"]
        > r["online_units"] * spec.unit_mw * dt + EPS
    ):
        raise AssertionError("Generator power exceeded available units")
    if r["generator_to_export_mwh"] > EPS and (
        r["grid_connected"]
        or r["it_unserved_mwh"] > EPS
        or r["auxiliary_unserved_mwh"] > EPS
        or r["ups_to_it_mwh"] > EPS
    ):
        raise AssertionError("Export stole internal service or used UPS/grid support")
    if not 0 <= r["export_power_mw"] <= spec.export_cap_mw + EPS:
        raise AssertionError("Export-power cap violated")
