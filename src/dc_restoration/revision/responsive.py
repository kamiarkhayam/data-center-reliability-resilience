"""Read-only frozen A2C inference using the new support-rate observation bridge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

from .network import sha
from .preview import preview_allocation
from .settings import demand


class FrozenSelector:
    def __init__(self, cfg, policy_config, checkpoint):
        import torch

        from dc_restoration.anchors.controller import RESOURCE_CONFIGS
        from dc_restoration.evaluation import trajectories
        from dc_restoration.policies import training

        self.paper = SimpleNamespace(
            EdgeConditionedMessagePassing=training.EdgeConditionedMessagePassing,
            to_torch_batch=training.to_torch_batch,
            GRAPH_KEYS=training.GRAPH_KEYS,
            RESOURCE_CONFIGS=RESOURCE_CONFIGS,
        )
        self.torch, self.trajectories = torch, trajectories
        torch.set_num_threads(2)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        paper_config = yaml.safe_load(Path(policy_config).read_text())
        paper_config["paths"]["case_dir"] = cfg["paths"]["case"]
        self.model, inventory, valuator, base = training._load_context(paper_config)
        self.adapter = trajectories.HarmonizedResourceAdapter(self.model, inventory, valuator, base)
        self.checkpoint = Path(checkpoint).resolve()
        self.checkpoint_sha = sha(self.checkpoint)
        self.policy, self.edges, _ = training._load_rl_policy_path(
            "true_gnn", self.checkpoint, {}, self.device
        )
        self.policy.eval()
        self.calls = 0
        self.messages = 0
        self.support_observations = 0
        self.cfg = cfg
        self.handles = [
            module.register_forward_hook(self._hook)
            for module in self.policy.modules()
            if isinstance(module, self.paper.EdgeConditionedMessagePassing)
        ]
        if not self.handles:
            raise AssertionError("No message-passing layers in checkpoint")

    def _hook(self, *_):
        self.messages += 1

    def __call__(
        self,
        *,
        network,
        remaining,
        damage,
        energy,
        states,
        spec,
        now,
        online,
        internal,
        rule,
    ):
        paper = self.paper

        def rates(model, candidate_remaining, _cfg, _energy_remaining, duration):
            physical = model.base.metrics(tuple(sorted(candidate_remaining)))
            raw, dc = network.state(candidate_remaining)
            expected_ids = set(network.loads.loc[raw, "load_id"].astype(str))
            if expected_ids != set(map(str, physical.unserved_load_ids)) or not dc.all():
                raise AssertionError("New and inherited connectivity implementations disagree")
            it, _ = demand(self.cfg)
            allocation = preview_allocation(
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
                reserve_hours=self.cfg["reserve_hours"],
                disabled_paths=self.cfg["disabled_paths"],
            )
            values = network.mw * raw - allocation
            result = {
                str(network.loads.iloc[i].load_id): max(0.0, float(values[i]) * 1000)
                for i in np.flatnonzero(raw)
            }
            support = float(allocation.sum())
            return result, support * duration, support, physical

        saved = self.trajectories._resource_rates
        fuel_before = [(s.fuel_mwh, s.ups_mwh) for s in states]
        try:
            # Scoped in-memory bridge only: never edit the research source.
            self.trajectories._resource_rates = rates
            before, scores, obs, _ = self.adapter.build_state(
                sorted(damage),
                damage,
                set(damage) - remaining,
                {
                    str(network.loads.iloc[i].load_id): float(v) * 1000
                    for i, v in enumerate(energy)
                    if v > 0
                },
                paper.RESOURCE_CONFIGS["R0_no_dc_anchor"],
                sum(s.fuel_mwh for s in states),
                self.cfg["candidate_k"],
            )
        finally:
            self.trajectories._resource_rates = saved
        if fuel_before != [(s.fuel_mwh, s.ups_mwh) for s in states]:
            raise AssertionError("Observation preview changed actual inventory")
        previous = self.messages
        batch = paper.to_torch_batch(
            {key: obs[key][None, ...] for key in paper.GRAPH_KEYS}, self.device
        )
        with self.torch.no_grad():
            action, *_ = self.policy.act(batch, self.edges, deterministic=True)
        index = int(action.item())
        if (
            self.messages <= previous
            or not 0 <= index < len(scores)
            or not obs["candidate_mask"][index]
        ):
            raise AssertionError("Invalid frozen graph-policy inference")
        self.calls += 1
        self.support_observations += int(before.dc_support_used_mw > 0)
        return str(scores[index]["edge_id"])

    def close(self):
        for handle in self.handles:
            handle.remove()
        if sha(self.checkpoint) != self.checkpoint_sha:
            raise AssertionError("Frozen checkpoint changed")
        return {
            "checkpoint": self.checkpoint.name,
            "checkpoint_sha256": self.checkpoint_sha,
            "device": str(self.device),
            "decisions": self.calls,
            "message_passing_calls": self.messages,
            "decisions_with_support_visible": self.support_observations,
            "retraining": False,
            "lookahead_equipment_information": False,
        }
