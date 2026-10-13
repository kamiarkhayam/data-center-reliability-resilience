"""Exact repair boundaries over native load-profile chronology."""

import numpy as np


class Storm:
    """Exact repair boundaries; native quarterly demand, disjoint support zones."""

    def __init__(self, ctx, key, indices, start, *, enabled=True):
        self.ctx, self.key, self.indices, self.start = ctx, key, indices, start
        self.enabled = enabled
        self.cache = {}
        net = ctx["network"]
        remaining = set(net.damage_map[key])
        self.starts, self.masks = [start], []
        clock = start
        for edge in ctx["orders"][key]["order"]:
            mask, dc = net.state(remaining)
            if not dc.all():
                raise AssertionError("Unexpected damage to DC source paths")
            if not mask.any():
                break
            self.masks.append(mask)
            clock += net.damage_map[key][edge]
            remaining.remove(edge)
            self.starts.append(clock)
        if net.state(remaining)[0].any() or not self.masks:
            raise AssertionError("Unrestored or empty scenario")
        self.starts = np.array(self.starts)
        self.end = float(clock)

    def boundary(self, now):
        k = np.searchsorted(self.starts, now + 1e-8, side="right")
        return self.starts[k] if k < len(self.starts) else np.inf

    def sample(self, now):
        if not self.start <= now < self.end - 1e-9:
            return 0.0, 0.0, np.zeros((3, 2))
        quarter = int((now + 1e-9) / 0.25)
        stage = np.searchsorted(self.starts, now + 1e-8, side="right") - 1
        pair = (quarter, stage)
        if pair not in self.cache:
            net = self.ctx["network"]
            demand = net.mw * self.ctx["profiles"][self.ctx["profile_index"], self.indices[quarter]]
            mask = self.masks[stage]
            req = np.zeros((3, 2))
            for s, zone in enumerate(net.zones):
                ids = zone[mask[zone]]
                req[s] = [
                    demand[ids[net.critical[ids]]].sum(),
                    demand[ids[~net.critical[ids]]].sum(),
                ]
            self.cache[pair] = (
                float(demand[mask].sum()),
                float(demand[mask & net.critical].sum()),
                req,
            )
        total, crit, req = self.cache[pair]
        return total, crit, req if self.enabled else np.zeros((3, 2))
