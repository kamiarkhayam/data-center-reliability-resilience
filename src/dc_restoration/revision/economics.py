"""Incremental export-enablement economics from paired load-level outcomes."""

import numpy as np

from dc_restoration.economics.duration_damage import _cdf_per_interrupted_kw


def annuity_factor(rate, years):
    if rate < 0 or years < 1 or int(years) != years:
        raise ValueError("Use nonnegative real discount and a positive integer life")
    return sum((1 + rate) ** -t for t in range(1, years + 1))


def investment_ceiling(benefit, event_cost, interval, fixed, rate, years):
    if np.any(np.asarray(interval) <= 0):
        raise ValueError("Event intervals must be positive")
    return ((benefit - event_cost) / np.asarray(interval) - fixed) * annuity_factor(rate, years)


def damage_by_load(energy_mwh, inventory, parameters, critical_value):
    power = inventory.baseline_p_kw.to_numpy(float)
    energy = np.asarray(energy_mwh, dtype=float) * 1000
    if np.any(power <= 0) or np.any(energy < -1e-8) or not np.isfinite(energy).all():
        raise ValueError("Invalid load-level input")
    duration = energy / power
    critical = inventory.is_critical.astype(str).str.lower().eq("true").to_numpy()
    residential = inventory.load_type.str.lower().eq("residential").to_numpy()
    result = np.zeros(len(inventory))
    for mask, value, points, shape in [
        (
            ~critical & residential,
            parameters["residential_noncritical_24h"],
            parameters["residential_noncritical_points"],
            parameters["residential_noncritical_shape"],
        ),
        (
            ~critical & ~residential,
            parameters["commercial_24h"],
            parameters["commercial_points"],
            parameters["commercial_shape"],
        ),
    ]:
        result[mask] = power[mask] * _cdf_per_interrupted_kw(duration[mask], value, points, shape)
    result[critical] = energy[critical] * critical_value
    return result
