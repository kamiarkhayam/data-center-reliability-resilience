"""Paired annual adequacy with private backup and optional coordinated transfer.

Divisible rationing maps to isolated equivalent internal blocks, not a whole-site
voltage trip. Coordinated transfer is an additional instructed operating mode.
"""

from __future__ import annotations

import itertools
import math

import numpy as np

from .export import equipment_config
from .internal_service import private_interval
from .service import initial_state, validate_state
from .settings import demand, recharge_parameters, validate_annual
from .supply import allocate

EPS = 1e-8
ENERGY_FIELDS = (
    "grid_to_it_mwh",
    "grid_to_aux_mwh",
    "grid_to_ups_mwh",
    "generator_to_it_mwh",
    "generator_to_aux_mwh",
    "generator_to_export_mwh",
    "ups_to_it_mwh",
    "it_unserved_mwh",
    "auxiliary_unserved_mwh",
    "fuel_delivery_accepted_mwh",
    "fuel_delivery_rejected_mwh",
)


class Equipment:
    """Per-unit start/repair clocks and running exposure, conditional on a call.

    All available units are exposed while their site generates. This conservative
    bank-running approximation is not an optimized unit-commitment model.
    """

    def __init__(self, spec, dc, seed, stochastic=True):
        self.spec, self.dc = spec, dc
        self.rng = np.random.default_rng(seed)
        self.stochastic = stochastic
        n = spec.installed_units
        self.down_until = np.full(n, -np.inf)
        self.ready = np.full(n, np.inf)
        self.life = np.full(n, np.inf)
        self.called = False
        if stochastic:
            for u in range(n):
                if self.rng.random() > dc["initial_availability"]:
                    self.down_until[u] = self.repair_time(0)

    def repair_time(self, now):
        return (
            now + self.rng.exponential(self.dc["repair_mttr_hours"])
            if self.dc["repair_enabled"]
            else np.inf
        )

    def start_unit(self, u, now, delay):
        if self.stochastic and self.rng.random() < self.dc["failure_to_start"]:
            self.down_until[u] = self.repair_time(now)
            self.ready[u] = np.inf
        else:
            self.down_until[u] = -np.inf
            self.ready[u] = now + delay
            self.life[u] = (
                self.rng.exponential(self.dc["running_mttf_hours"]) if self.stochastic else np.inf
            )

    def call(self, now, ready_delay):
        self.called = True
        for u in range(self.spec.installed_units):
            if self.down_until[u] <= now:
                self.start_unit(u, now, ready_delay)

    def stop(self):
        self.called = False
        self.ready[:] = np.inf

    def refresh(self, now, accept_delay):
        if not self.called:
            return
        for u in range(self.spec.installed_units):
            if self.life[u] <= EPS and self.ready[u] <= now + EPS:
                self.ready[u] = np.inf
                self.down_until[u] = self.repair_time(now)
            if self.down_until[u] != -np.inf and self.down_until[u] <= now + EPS:
                self.start_unit(u, now, accept_delay)

    def online(self, now):
        return self.called & (self.ready <= now + EPS)

    def boundary(self, now, generating):
        future = np.r_[
            self.ready[self.ready > now + EPS],
            self.down_until[self.down_until > now + EPS],
        ]
        result = float(future.min()) if len(future) else np.inf
        if generating and self.online(now).any():
            result = min(result, now + float(self.life[self.online(now)].min()))
        return result

    def consume(self, now, duration, generating):
        if generating:
            self.life[self.online(now)] -= duration


def simulate_service(
    total,
    critical,
    capacity,
    cfg,
    annual,
    *,
    control,
    recharge_case="manufacturer_reference",
    year=0,
    dt=0.25,
    observation_start_h=0.0,
    keep_ledger=False,
    initial_fuel_mwh=None,
    initial_ups_mwh=None,
    utility_available=None,
    storm=None,
    reserve_hours=0.0,
    post_event_refuel_hours=48.0,
):
    """Paired interval accounting, with physical storage carried across shortages.

    A shortage call is latched against full facility demand, avoiding oscillation
    when transfer itself releases grid capacity. Restarting from grid requires
    that this counterfactual shortage has ended. No ordinary community export.
    """
    if control not in {"private_backup", "private_plus_coordinated"}:
        raise ValueError("Unknown annual control")
    validate_annual(annual)
    arrays = [np.asarray(x, dtype=float) for x in (total, critical, capacity)]
    total, critical, capacity = arrays
    if any(
        a.ndim != 1 or a.shape != total.shape or not np.isfinite(a).all() or (a < 0).any()
        for a in arrays
    ):
        raise ValueError("Matched finite nonnegative chronology required")
    if not len(total) or (critical > total).any() or not math.isfinite(dt) or dt <= 0:
        raise ValueError("Invalid chronology")
    horizon = len(total) * dt
    if not 0 <= observation_start_h < horizon:
        raise ValueError("Invalid reporting window")
    old, probe, spec = equipment_config(cfg, horizon)
    dc = old["dc"]
    it, internal = demand(cfg)
    sites = dc["sites"]
    floor = spec.design_internal_mw * reserve_hours
    if not 0 <= floor <= spec.fuel_capacity_mwh or post_event_refuel_hours < 0:
        raise ValueError("Invalid reserve or restoration logistics")
    if utility_available is None:
        utility_available = np.ones((len(total), sites), dtype=bool)
    else:
        utility_available = np.asarray(utility_available)
        if utility_available.shape != (len(total), sites) or utility_available.dtype != bool:
            raise ValueError("Explicit Boolean utility availability per interval and site required")
    recharge = recharge_parameters(spec, recharge_case)
    coordinated = control == "private_plus_coordinated"
    states = [initial_state(spec) for _ in range(sites)]
    for state in states:
        if initial_fuel_mwh is not None:
            state.fuel_mwh = initial_fuel_mwh
        if initial_ups_mwh is not None:
            state.ups_mwh = initial_ups_mwh
        validate_state(state, spec)
    ready_delay = spec.reserve_start_h
    accept_delay = probe["load_acceptance_delay_seconds"] / 3600
    equipment = [
        Equipment(
            spec,
            dc,
            [annual["seed"], annual["stream_stage"], year, 999, s],
            annual["equipment_failures"],
        )
        for s in range(sites)
    ]
    deliveries = np.full(sites, np.inf)
    totals = {k: 0.0 for k in ENERGY_FIELDS}
    energy_corrections = {k: 0.0 for k in ENERGY_FIELDS}
    totals.update(
        community_ens_mwh=0.0,
        critical_ens_mwh=0.0,
        dc_grid_displacement_mwh=0.0,
        dc_forced_grid_curtailment_mwh=0.0,
        community_lolh=0.0,
        critical_lolh=0.0,
        served_system_lolh=0.0,
        dc_it_lolh=0.0,
        dc_aux_lolh=0.0,
        automatic_backup_calls=0,
        coordinated_transfer_site_hours=0.0,
        grid_interrupted_site_hours=0.0,
        disconnected_community_ens_mwh=0.0,
        connected_supply_ens_mwh=0.0,
        post_event_it_ens_mwh=0.0,
        post_event_aux_ens_mwh=0.0,
    )
    loss_days = {k: set() for k in ("community", "critical", "dc_it", "dc_aux", "any_service")}
    records, community_rows = [], []
    scarce = total + sites * internal > capacity + EPS
    active_indices = np.flatnonzero(scarce | ~utility_available.all(axis=1))
    if storm is not None:
        active_indices = np.union1d(
            active_indices,
            np.arange(int(storm.start / dt), int(np.ceil(storm.end / dt)) + 1),
        )
    now, call_start = 0.0, None
    initial_report = None
    onset = None
    post_ordered = False
    minimum_export_reserve = spec.fuel_capacity_mwh
    max_export = 0.0

    def mark(name, start, end):
        start -= observation_start_h
        end -= observation_start_h
        loss_days[name].update(range(math.floor(start / 24), math.ceil((end - 1e-10) / 24)))

    while now < horizon - EPS:
        idx = min(len(total) - 1, int((now + 1e-9) / dt))
        disconnected, disconnected_critical, requests = (
            storm.sample(now) if storm is not None else (0.0, 0.0, np.zeros((sites, 2)))
        )
        storm_active = storm is not None and storm.start <= now < storm.end
        grid_total = max(0.0, total[idx] - disconnected)
        grid_critical = max(0.0, critical[idx] - disconnected_critical)
        short = bool(grid_total + sites * internal > capacity[idx] + EPS)
        if storm_active and onset is None:
            onset = [(s.fuel_mwh, s.ups_mwh) for s in states]
        if storm is not None and now >= storm.end - EPS and not post_ordered:
            for s, state in enumerate(states):
                if state.fuel_mwh < spec.fuel_capacity_mwh - EPS:
                    deliveries[s] = min(deliveries[s], now + post_event_refuel_hours)
            post_ordered = True
        if storm_active:
            deliveries[(deliveries < storm.end)] = storm.end
        if short and call_start is None:
            call_start = now
        if not short and call_start is not None:
            call_start = None
        for eq in equipment:
            eq.refresh(now, accept_delay)
        online = [int(eq.online(now).sum()) for eq in equipment]
        island = [
            bool(
                short
                and coordinated
                and utility_available[idx, s]
                and now >= call_start + dc["backup_response_hours"] - EPS
                and online[s] * spec.unit_mw >= internal - EPS
                and (state.fuel_mwh > EPS or deliveries[s] <= now + EPS)
            )
            for s, state in enumerate(states)
        ]
        connected_mask = utility_available[idx] & ~np.array(island)
        requested_grid = int(connected_mask.sum()) * internal
        community, crit, curtailed = [
            float(x)
            for x in allocate(
                grid_total, grid_critical, requested_grid, capacity[idx], "conventional"
            )
        ]
        connected = int(connected_mask.sum())
        offer = (requested_grid - curtailed) / connected if connected else 0.0
        fractions = np.where(connected_mask, np.clip(offer / internal, 0, 1), 0.0)
        for s, eq in enumerate(equipment):
            private_interruption = fractions[s] < 1 - EPS and not island[s]
            export_call = (
                storm_active
                and requests[s].sum() > EPS
                and fractions[s] >= 1 - EPS
                and not island[s]
                and states[s].fuel_mwh > floor + EPS
            )
            needed = private_interruption or (coordinated and short) or export_call
            if needed and not eq.called:
                eq.call(now, ready_delay)
                if private_interruption and now >= observation_start_h - EPS:
                    totals["automatic_backup_calls"] += 1
            elif not needed and eq.called:
                eq.stop()
        online = [int(eq.online(now).sum()) for eq in equipment]
        spare = max(0.0, capacity[idx] - grid_total - requested_grid)
        charges = np.array(
            [
                min(recharge["rate_mw"], max(0.0, spec.design_internal_mw - internal))
                if fractions[s] >= 1 - EPS and state.ups_mwh < recharge["target_mwh"] - EPS
                else 0.0
                for s, state in enumerate(states)
            ],
            dtype=float,
        )
        if charges.sum() > spare:
            charges *= spare / charges.sum()
        end = min(horizon, (idx + 1) * dt)
        # Skip service-perfect idle spans, but never skip deliveries or the
        # reporting boundary. Site storage is not reset at the next event.
        bulk_idle = (
            not short
            and not storm_active
            and utility_available[idx].all()
            and (
                recharge["rate_mw"] == 0
                or internal >= spec.design_internal_mw - EPS
                or all(s.ups_mwh >= recharge["target_mwh"] - EPS for s in states)
            )
        )
        if bulk_idle:
            j = np.searchsorted(active_indices, idx, side="left")
            end = active_indices[j] * dt if j < len(active_indices) else horizon
            if end <= now + EPS:
                end = min(horizon, (idx + 1) * dt)
        if now < observation_start_h - EPS:
            end = min(end, observation_start_h)
        if storm is not None:
            end = min(end, storm.boundary(now))
            if now < storm.start + spec.export_authorized_h - EPS:
                end = min(end, storm.start + spec.export_authorized_h)
        if (
            coordinated
            and call_start is not None
            and now < call_start + dc["backup_response_hours"] - EPS
        ):
            end = min(end, call_start + dc["backup_response_hours"])
        fuel_delivery = np.zeros(sites)
        exports = np.zeros(sites)
        for s, (eq, state) in enumerate(zip(equipment, states)):
            if deliveries[s] <= now + EPS:
                fuel_delivery[s] = spec.fuel_capacity_mwh
                deliveries[s] = np.inf
            if (
                annual["refuel_enabled"]
                and not storm_active
                and np.isinf(deliveries[s])
                and fuel_delivery[s] == 0
                and state.fuel_mwh <= spec.fuel_capacity_mwh * dc["refuel_order_fraction"] + EPS
            ):
                deliveries[s] = now + dc["refuel_lead_hours"]
            gen_rate = min(internal * (1 - fractions[s]), online[s] * spec.unit_mw)
            available_fuel = min(spec.fuel_capacity_mwh, state.fuel_mwh + fuel_delivery[s])
            if (
                storm_active
                and now >= storm.start + spec.export_authorized_h - EPS
                and fractions[s] >= 1 - EPS
                and not island[s]
                and available_fuel > floor + EPS
            ):
                exports[s] = min(
                    requests[s].sum(),
                    spec.export_cap_mw,
                    max(
                        0.0,
                        (online[s] - spec.contingency_units) * spec.unit_mw - internal,
                    ),
                )
            generating = gen_rate + exports[s] > EPS and (
                state.fuel_mwh > EPS or fuel_delivery[s] > 0
            )
            end = min(end, deliveries[s], eq.boundary(now, generating))
            if charges[s] > EPS:
                end = min(end, now + (recharge["target_mwh"] - state.ups_mwh) / charges[s])
            if generating:
                available_fuel = min(spec.fuel_capacity_mwh, state.fuel_mwh + fuel_delivery[s])
                end = min(end, now + available_fuel / (gen_rate + exports[s]))
                if exports[s] > EPS:
                    end = min(end, now + max(0.0, available_fuel - floor) / exports[s])
                trigger = spec.fuel_capacity_mwh * dc["refuel_order_fraction"]
                if (
                    annual["refuel_enabled"]
                    and not storm_active
                    and np.isinf(deliveries[s])
                    and available_fuel > trigger + EPS
                ):
                    end = min(end, now + (available_fuel - trigger) / gen_rate)
        if end <= now + 1e-10:
            raise AssertionError("Annual integrator stalled")
        if initial_report is None and now >= observation_start_h - EPS:
            initial_report = [(s.fuel_mwh, s.ups_mwh) for s in states]
        span = end - now
        site_rows = []
        for s, state in enumerate(states):
            rows = private_interval(
                state,
                spec,
                start_h=now,
                duration_h=span,
                it_mw=it,
                auxiliary_mw=internal - it,
                online_units=online[s],
                grid_fraction=float(fractions[s]),
                recharge=recharge,
                grid_charge_offer_mw=charges[s],
                fuel_delivery_mwh=fuel_delivery[s],
            )
            if exports[s] > EPS:
                if fractions[s] < 1 - EPS:
                    raise AssertionError("Export during interrupted internal grid service")
                # Grid-fed internal service and isolated community export share
                # fuel, not conductors; the site energy record includes both.
                cumulative = 0.0
                for r in rows:
                    r["fuel_start_mwh"] -= cumulative
                    used = exports[s] * r["duration_h"]
                    cumulative += used
                    r["fuel_end_mwh"] -= cumulative
                    r["generator_to_export_mwh"] = used
                    r["export_power_mw"] = exports[s]
                    r["export_requested_mw"] = requests[s].sum()
                    r["operating_mode"] = "grid_internal_separate_isolated_export"
                    residual = (
                        r["fuel_start_mwh"]
                        + r["fuel_delivery_accepted_mwh"]
                        - r["fuel_end_mwh"]
                        - r["generator_to_export_mwh"]
                    )
                    if (
                        abs(residual) > 1e-7
                        or r["it_unserved_mwh"] > EPS
                        or r["auxiliary_unserved_mwh"] > EPS
                    ):
                        raise AssertionError("Separate export site accounting failed")
                state.fuel_mwh -= cumulative
                if state.fuel_mwh < floor - 1e-6:
                    raise AssertionError("Community export breached retained fuel")
                minimum_export_reserve = min(minimum_export_reserve, state.fuel_mwh)
                max_export = max(max_export, exports[s])
            site_rows.extend({"site": s, "coordinated_transfer": island[s], **r} for r in rows)
            for row in rows:
                gen_energy = (
                    row["generator_to_it_mwh"]
                    + row["generator_to_aux_mwh"]
                    + row["generator_to_export_mwh"]
                )
                equipment[s].consume(row["start_h"], row["duration_h"], gen_energy > 0.0)
        exported = float(exports.sum())
        exported_critical = float(np.minimum(exports, requests[:, 0]).sum())
        if exported > disconnected + EPS or exported_critical > disconnected_critical + EPS:
            raise AssertionError("Export exceeds eligible disconnected load")
        supply_loss = community
        community += disconnected - exported
        crit += disconnected_critical - exported_critical
        if now >= observation_start_h - EPS:
            for r in site_rows:
                for k in ENERGY_FIELDS:
                    # Compensated sums retain small subinterval energies when
                    # added to full-year service totals of hundreds of GWh.
                    adjusted = r[k] - energy_corrections[k]
                    updated = totals[k] + adjusted
                    energy_corrections[k] = (updated - totals[k]) - adjusted
                    totals[k] = updated
                if storm is not None and now >= storm.end - EPS:
                    totals["post_event_it_ens_mwh"] += r["it_unserved_mwh"]
                    totals["post_event_aux_ens_mwh"] += r["auxiliary_unserved_mwh"]
                for name, key in (
                    ("dc_it", "it_unserved_mwh"),
                    ("dc_aux", "auxiliary_unserved_mwh"),
                ):
                    if r[key] > EPS:
                        mark(name, r["start_h"], r["end_h"])
                        mark("any_service", r["start_h"], r["end_h"])
            # LOLH is portfolio union time, not the sum of site outage hours.
            bounds = sorted({now, end, *[r["end_h"] for r in site_rows]})
            for a, b in itertools.pairwise(bounds):
                active = [r for r in site_rows if r["start_h"] <= (a + b) / 2 < r["end_h"]]
                totals["dc_it_lolh"] += (b - a) * any(r["it_unserved_mwh"] > EPS for r in active)
                totals["dc_aux_lolh"] += (b - a) * any(
                    r["auxiliary_unserved_mwh"] > EPS for r in active
                )
                totals["served_system_lolh"] += (b - a) * (
                    community > EPS
                    or any(
                        r["it_unserved_mwh"] > EPS or r["auxiliary_unserved_mwh"] > EPS
                        for r in active
                    )
                )
            totals["community_ens_mwh"] += community * span
            totals["critical_ens_mwh"] += crit * span
            totals["disconnected_community_ens_mwh"] += (disconnected - exported) * span
            totals["connected_supply_ens_mwh"] += supply_loss * span
            totals["dc_grid_displacement_mwh"] += (
                sites * internal - requested_grid + curtailed
            ) * span
            totals["dc_forced_grid_curtailment_mwh"] += curtailed * span
            totals["coordinated_transfer_site_hours"] += sum(island) * span
            totals["grid_interrupted_site_hours"] += int((fractions < 1 - EPS).sum()) * span
            totals["community_lolh"] += span * (community > EPS)
            totals["critical_lolh"] += span * (crit > EPS)
            if crit > EPS:
                mark("critical", now, end)
            if community > EPS:
                mark("community", now, end)
                mark("any_service", now, end)
            if keep_ledger:
                records.extend(site_rows)
                community_rows.append(
                    {
                        "start_h": now,
                        "end_h": end,
                        "community_ens_mwh": community * span,
                        "critical_ens_mwh": crit * span,
                        "grid_dc_request_mw": requested_grid,
                        "forced_dc_curtailment_mw": curtailed,
                        "islanded_sites": sum(island),
                        "bulk_idle": bulk_idle,
                    }
                )
        # Grid allocations plus recharge never exceed the available supply.
        if (
            not bulk_idle
            and grid_total - supply_loss + (requested_grid - curtailed) + charges.sum()
            > capacity[idx] + EPS
        ):
            raise AssertionError("Recharge or transfer created grid supply")
        now = end
    duration = horizon - observation_start_h
    fuel0, ups0 = np.array(initial_report).sum(axis=0)
    fuel1, ups1 = sum(s.fuel_mwh for s in states), sum(s.ups_mwh for s in states)
    residuals = {
        "fuel": fuel0
        + totals["fuel_delivery_accepted_mwh"]
        - fuel1
        - totals["generator_to_it_mwh"]
        - totals["generator_to_aux_mwh"],
        "ups": ups0 + totals["grid_to_ups_mwh"] - ups1 - totals["ups_to_it_mwh"],
        "it": totals["grid_to_it_mwh"]
        + totals["generator_to_it_mwh"]
        + totals["ups_to_it_mwh"]
        + totals["it_unserved_mwh"]
        - sites * it * duration,
        "auxiliary": totals["grid_to_aux_mwh"]
        + totals["generator_to_aux_mwh"]
        + totals["auxiliary_unserved_mwh"]
        - sites * (internal - it) * duration,
    }
    residuals["fuel"] -= totals["generator_to_export_mwh"]
    residuals["community_partition"] = (
        totals["community_ens_mwh"]
        - totals["disconnected_community_ens_mwh"]
        - totals["connected_supply_ens_mwh"]
    )
    if max(abs(v) for v in residuals.values()) > 1e-5:
        raise AssertionError(f"Annual conservation failed: {residuals}")
    if storm is None and totals["generator_to_export_mwh"] != 0:
        raise AssertionError("Ordinary analysis must not include hurricane export")
    totals.update({k + "_loss_days": len(v) for k, v in loss_days.items()})
    totals.update(
        fuel_start_mwh=fuel0,
        fuel_end_mwh=fuel1,
        ups_start_mwh=ups0,
        ups_end_mwh=ups1,
        max_balance_residual_mwh=max(abs(v) for v in residuals.values()),
        reporting_hours=duration,
        minimum_fuel_after_export_mwh=minimum_export_reserve,
        maximum_site_export_mw=max_export,
        minimum_onset_fuel_mwh=min(x[0] for x in onset) if onset else np.nan,
        minimum_onset_ups_mwh=min(x[1] for x in onset) if onset else np.nan,
        minimum_final_fuel_mwh=min(s.fuel_mwh for s in states),
    )
    return totals, records, community_rows
