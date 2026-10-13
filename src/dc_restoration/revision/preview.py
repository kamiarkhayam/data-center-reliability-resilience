"""Load-level preview integration: allocate first, then average."""

from copy import deepcopy

import numpy as np

from .export import grid_interval


def preview_allocation(
    network,
    raw,
    states,
    spec,
    now,
    duration,
    online,
    it,
    internal,
    rule,
    *,
    reserve_hours=0.0,
    disabled_paths=(),
):
    if not np.isfinite(duration) or duration <= 0:
        raise ValueError("Positive preview duration required")
    allocation = np.zeros(len(network.mw))
    for site, zone in enumerate(network.zones):
        rows = grid_interval(
            deepcopy(states[site]),
            spec,
            start_h=now,
            duration_h=duration,
            online=int(online[site]),
            it_mw=it,
            auxiliary_mw=internal - it,
            request_mw=float(network.mw[zone][raw[zone]].sum()),
            rule=rule,
            reserve_hours=reserve_hours,
            path_available=site not in disabled_paths,
        )
        for row in rows:
            allocation += (
                network.allocate(raw, site, row["export_mw"]) * row["duration_h"] / duration
            )
    return allocation
