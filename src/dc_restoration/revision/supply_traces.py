"""Reproducible independent supply-unit outage streams."""

import numpy as np

from .supply import supply_trace


def unit_trace(rating, q, mttf, mttr, steps, dt, rng):
    trace = np.zeros(steps)
    t, horizon = 0.0, steps * dt
    up = rng.random() >= q
    while t < horizon:
        end = min(horizon, t + rng.exponential(mttf if up else mttr))
        if up:
            trace[int(np.ceil(t / dt)) : min(steps, int(np.ceil(end / dt)))] = rating
        t, up = end, not up
    return trace


def random_stream(config, stage, year, component, unit=0):
    return np.random.default_rng([config["seed"], stage, year, component, unit])


def reference_trace(fleet, config, stage, year, steps, dt, initial_scale):
    return initial_scale * supply_trace(fleet, steps, dt, random_stream(config, stage, year, 0))


def added_traces(row, units, scale, config, stage, year, steps, dt):
    for n in range(units):
        yield unit_trace(
            row.capacity_mw * scale,
            row.forced_outage_rate,
            row.mttf_hours,
            row.mttr_hours,
            steps,
            dt,
            random_stream(config, stage, year, int(row.capacity_mw), n),
        )
