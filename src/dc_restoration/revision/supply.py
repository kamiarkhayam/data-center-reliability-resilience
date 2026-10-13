"""Inherited capacity distribution, allocation, and supply-outage sampling."""

import numpy as np
import pandas as pd

TOL = 1e-8


def expand_fleet(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in table.itertuples():
        if (
            row.count <= 0
            or row.count != int(row.count)
            or row.capacity_mw <= 0
            or row.capacity_mw != int(row.capacity_mw)
        ):
            raise ValueError("Fleet requires positive integer counts and MW ratings")
        if row.mttf_hours <= 0 or row.mttr_hours <= 0:
            raise ValueError("Failure/repair times must be positive")
        q = row.mttr_hours / (row.mttf_hours + row.mttr_hours)
        if not np.isclose(q, row.forced_outage_rate, atol=1e-10):
            raise ValueError(f"Inconsistent availability for {row.unit_group}")
        for number in range(row.count):
            rows.append(
                {
                    "unit_id": f"{row.unit_group}_{number + 1}",
                    "capacity_mw": row.capacity_mw,
                    "q": q,
                    "mttf_hours": row.mttf_hours,
                    "mttr_hours": row.mttr_hours,
                }
            )
    return pd.DataFrame(rows)


def capacity_distribution(fleet: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Exact COPT on original integer MW; scale only after convolution."""
    outage = np.array([1.0])
    for row in fleet.itertuples():
        size = int(row.capacity_mw)
        updated = np.zeros(len(outage) + size)
        updated[: len(outage)] += (1 - row.q) * outage
        updated[size:] += row.q * outage
        outage = updated
    capacity = np.arange(len(outage), dtype=float)
    probabilities = outage[::-1]
    if not np.isclose(probabilities.sum(), 1) or (probabilities < 0).any():
        raise ValueError("Invalid probability table")
    return capacity, probabilities


def shortage_expectation(demand, capacity, probabilities):
    """E[(demand - capacity)+] and P(capacity < demand), without binning demand."""
    cdf = np.r_[0.0, np.cumsum(probabilities)]
    first = np.r_[0.0, np.cumsum(probabilities * capacity)]
    index = np.searchsorted(capacity, demand, side="left")
    return np.maximum(0.0, np.asarray(demand) * cdf[index] - first[index]), cdf[index]


def allocate(total, critical, dc_grid, capacity, mode="conventional"):
    """Return community unserved MW, critical unserved MW and DC grid curtailment.

    DC grid curtailment is NOT DC unserved energy: internal backup may serve it.
    """
    total, critical, dc_grid, capacity = np.broadcast_arrays(
        np.asarray(total, float),
        np.asarray(critical, float),
        np.asarray(dc_grid, float),
        np.asarray(capacity, float),
    )
    if (
        (critical < 0).any()
        or (critical > total + TOL).any()
        or (dc_grid < 0).any()
        or (capacity < -TOL).any()
    ):
        raise ValueError("Invalid demand/capacity balance")
    if mode == "dc_first":
        shed_dc = np.minimum(dc_grid, np.maximum(0, total + dc_grid - capacity))
        return (
            np.maximum(0, total - capacity),
            np.maximum(0, critical - capacity),
            shed_dc,
        )
    if mode == "dc_priority_upper":
        available = np.maximum(0, capacity - dc_grid)
        return (
            np.maximum(0, total - available),
            np.maximum(0, critical - available),
            np.maximum(0, dc_grid - capacity),
        )
    if mode != "conventional":
        raise ValueError(f"Unknown allocation mode: {mode}")
    ordinary = total - critical
    eligible = ordinary + dc_grid
    shortfall = np.maximum(0, eligible - np.maximum(0, capacity - critical))
    community_share = np.divide(ordinary, eligible, out=np.zeros_like(eligible), where=eligible > 0)
    critical_loss = np.maximum(0, critical - capacity)
    return (
        critical_loss + shortfall * community_share,
        critical_loss,
        shortfall * (1 - community_share),
    )


def supply_trace(fleet: pd.DataFrame, steps: int, dt: float, rng) -> np.ndarray:
    """Sample continuous exponential up/down paths at the 15-minute boundaries."""
    trace = np.zeros(steps)
    horizon = steps * dt
    for row in fleet.itertuples():
        up = rng.random() >= row.q
        t = 0.0
        while t < horizon:
            end = min(horizon, t + rng.exponential(row.mttf_hours if up else row.mttr_hours))
            if up:
                start_index = int(np.ceil(t / dt))
                end_index = min(steps, int(np.ceil(end / dt)))
                trace[start_index:end_index] += row.capacity_mw
            t = end
            up = not up
    return trace
