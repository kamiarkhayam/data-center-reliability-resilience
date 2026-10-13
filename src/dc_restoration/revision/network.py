"""Explicit external network inputs and inherited bundled-link connectivity."""

import hashlib
import json
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_json(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def boolean(series):
    converted = (
        series.astype(str).str.lower().map({"true": True, "false": False, "1": True, "0": False})
    )
    if converted.isna().any():
        raise ValueError("Missing or invalid Boolean field")
    return converted.to_numpy(dtype=bool)


class Network:
    def __init__(self, cfg):
        case = Path(cfg["paths"]["case"])
        self.paths = {
            name: case / rel
            for name, rel in {
                "nodes": "network/nodes.csv",
                "edges": "network/edges.csv",
                "loads": "network/loads.csv",
                "dcs": "network/data_centers.csv",
                "anchors": "dc_scenarios/anchor_zones.csv",
                "manifest": "scenario_design/benchmark_hard_hurricane_scenario_manifest.csv",
                "damage": "scenario_design/benchmark_hard_damage_components.csv",
            }.items()
        }
        for name, path in self.paths.items():
            setattr(self, name, pd.read_csv(path))
        for frame, key in [
            (self.nodes, "node_id"),
            (self.edges, "edge_id"),
            (self.loads, "load_id"),
            (self.dcs, "dc_id"),
            (self.manifest, "scenario_key"),
        ]:
            if frame[key].duplicated().any():
                raise ValueError(f"Duplicate {key}")
        if boolean(self.loads.is_data_center).any():
            raise ValueError("Community load table already contains DC demand")
        self.node_index = {key: i for i, key in enumerate(self.nodes.node_id.astype(str))}
        self.source = self.node_index["T_SUPER_SOURCE_V03"]
        self.edge_index = {key: i for i, key in enumerate(self.edges.edge_id.astype(str))}
        self.adjacency = [[] for _ in self.node_index]
        endpoint_groups = {}
        for row in self.edges.itertuples(index=False):
            u, v = (
                self.node_index[str(row.from_node)],
                self.node_index[str(row.to_node)],
            )
            index = self.edge_index[str(row.edge_id)]
            endpoint_groups.setdefault(tuple(sorted((u, v))), []).append(index)
            self.adjacency[u].append((v, index))
            self.adjacency[v].append((u, index))
        # Preserve the inherited simple-graph availability contract. Any damaged
        # record blocks its endpoint connection, including co-endpoint records.
        self.parallel_blockers = {
            edge: tuple(group)
            for group in endpoint_groups.values()
            if len(group) > 1
            for edge in group
        }
        self.load_index = {key: i for i, key in enumerate(self.loads.load_id.astype(str))}
        self.load_nodes = np.array([self.node_index[str(v)] for v in self.loads.node_id])
        self.mw = self.loads.baseline_p_kw.to_numpy(float) / 1000
        self.critical = boolean(self.loads.is_critical)
        if not np.isfinite(self.mw).all() or (self.mw < 0).any():
            raise ValueError("Invalid community demand")
        self.dc_nodes = np.array([self.node_index[str(n)] for n in self.dcs.connected_node_id])
        self.zones = []
        assigned = set()
        for dc in self.dcs.itertuples(index=False):
            row = self.anchors.loc[self.anchors.anchor_zone_id == dc.anchor_zone_id]
            if len(row) != 1:
                raise ValueError("Each DC must have one defined support zone")
            ids = list(map(str, json.loads(row.iloc[0].anchor_load_ids)))
            if any(lid not in self.load_index for lid in ids) or len(ids) != len(set(ids)):
                raise ValueError("Invalid anchor load IDs")
            indices = [self.load_index[lid] for lid in ids]
            if assigned.intersection(indices):
                raise ValueError("Overlapping zones require explicit non-duplicating allocation")
            assigned.update(indices)
            # Preserve a fixed critical-first service priority; the old VoLL
            # field is only a secondary ranking, not a new economic valuation.
            indices.sort(
                key=lambda i: (
                    -int(self.critical[i]),
                    -float(self.loads.iloc[i].voll_usd_per_kwh),
                    -self.mw[i],
                    str(self.loads.iloc[i].load_id),
                )
            )
            self.zones.append(np.array(indices, dtype=int))
        self.damage_map = {}
        eligible = self.edges.set_index("edge_id").is_hurricane_damage_eligible
        for key, part in self.damage.groupby("scenario_key", sort=False):
            if (
                part.edge_id.duplicated().any()
                or not np.isfinite(part.repair_time_hours).all()
                or (part.repair_time_hours <= 0).any()
            ):
                raise ValueError("Invalid scenario damage/repair records")
            if not boolean(eligible.loc[part.edge_id]).all():
                raise ValueError("Damage falls outside the original eligible edge set")
            self.damage_map[str(key)] = dict(
                zip(part.edge_id.astype(str), part.repair_time_hours.astype(float))
            )
        self._cache = {}
        intact = self.state(frozenset())
        if intact[0].any() or not intact[1].all():
            raise ValueError("Intact graph must connect all community and DC supply nodes")

    def state(self, remaining):
        key = frozenset(map(str, remaining))
        if key in self._cache:
            return self._cache[key]
        blocked = {self.edge_index[edge] for edge in key}
        blocked.update(
            peer for edge in tuple(blocked) for peer in self.parallel_blockers.get(edge, ())
        )
        reached = np.zeros(len(self.node_index), dtype=bool)
        reached[self.source] = True
        queue = deque([self.source])
        while queue:
            u = queue.popleft()
            for v, edge in self.adjacency[u]:
                if edge not in blocked and not reached[v]:
                    reached[v] = True
                    queue.append(v)
        result = (~reached[self.load_nodes], reached[self.dc_nodes])
        for value in result:
            value.flags.writeable = False
        self._cache[key] = result
        return result

    def allocate(self, unserved, site, power):
        if not np.isfinite(power) or power < -1e-9:
            raise ValueError("Invalid allocation power")
        ids = self.zones[site]
        ids = ids[unserved[ids]]
        allocation = np.zeros(len(self.mw))
        remaining = max(0.0, power)
        for i in ids:
            used = min(self.mw[i], remaining)
            allocation[i] = used
            remaining -= used
            if remaining <= 1e-10:
                break
        if remaining > 1e-7:
            raise AssertionError("Export exceeds eligible disconnected demand")
        return allocation
