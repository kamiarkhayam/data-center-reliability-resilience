"""Portable entry points around the audited revised-study numerical routines."""

import argparse
import copy
import json
import math
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dc_restoration.economics.duration_damage import _valuation_parameters

from .checks import check_fixed, validate_baseline
from .chronology import simulate_service
from .economics import annuity_factor, damage_by_load, investment_ceiling
from .network import Network, sha
from .recovery import replay
from .settings import RESOURCES, reference_config
from .storm import Storm
from .supply import capacity_distribution, expand_fleet, shortage_expectation
from .supply_traces import added_traces, reference_trace


def resource(name):
    return json.loads((RESOURCES / name).read_text())


def checked_inputs(root):
    manifest = json.loads((root / "manifest.json").read_text())
    for name, record in manifest.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != record["sha256"]:
            raise ValueError(f"Input manifest mismatch: {name}")
    return manifest


def supply_context(root):
    profile = pd.read_csv(root / "community_profile.csv")
    total, critical = profile.community_mw.to_numpy(), profile.critical_mw.to_numpy()
    if (
        len(total) != 35040
        or not np.isfinite([total, critical]).all()
        or (critical > total).any()
        or (critical < 0).any()
    ):
        raise ValueError("Expected the complete, nonnegative native annual community profile")
    plan = json.loads((root / "planned_supply.json").read_text())
    grouped = pd.read_csv(RESOURCES / "rts_fleet.csv")
    fleet = expand_fleet(grouped)
    addition = next(x for x in plan["additions"] if x["is_reference"])
    scale = total.max() / 2850 * plan["baseline_scale"]
    return {
        "total": total,
        "critical": critical,
        "fleet": fleet,
        "scale": scale,
        "addition": addition,
        "addrow": grouped.loc[grouped.unit_group == addition["unit_group"]].iloc[0],
    }


def adequacy_table(ctx):
    capacity, probability = capacity_distribution(ctx["fleet"])
    capacity = capacity * ctx["scale"]
    total, critical = ctx["total"], ctx["critical"]
    addition = ctx["addition"]
    q, rating = addition["forced_outage_rate"], addition["unit_nameplate_mw"]
    rows = []
    for count in range(addition["added_units"] + 1):
        for dc in [0, 78, 90]:
            fraction = np.divide(
                total - critical,
                total - critical + dc,
                out=np.zeros_like(total),
                where=total - critical + dc > 0,
            )
            eens = crit_eens = hours = 0.0
            for up in range(count + 1):
                weight = math.comb(count, up) * (1 - q) ** up * q ** (count - up)
                e_total, p_total = shortage_expectation(
                    total + dc, capacity + up * rating, probability
                )
                e_crit, p_crit = shortage_expectation(critical, capacity + up * rating, probability)
                eens += weight * float((fraction * e_total + (1 - fraction) * e_crit).sum()) * 0.25
                crit_eens += weight * float(e_crit.sum()) * 0.25
                hours += weight * float(np.where(total > critical, p_total, p_crit).sum()) * 0.25
            rows.append(
                {
                    "added_supply_mw": count * rating,
                    "dc_demand_mw": dc,
                    "community_eens_mwh_year": eens,
                    "critical_eens_mwh_year": crit_eens,
                    "community_shortfall_hours_year": hours,
                }
            )
    return pd.DataFrame(rows)


def load_network(root):
    cfg = reference_config()
    cfg["paths"]["case"] = str((root / "case").resolve())
    net = Network(cfg)
    orders = json.loads((root / "fixed_orders.json").read_text())
    cohort = pd.read_csv(root / "locked_scenarios.csv")
    if (
        len(cohort) != 131
        or not cohort.scenario_key.is_unique
        or set(cohort.scenario_key) != set(orders)
    ):
        raise ValueError("Expected the archived 131-scenario bank and matching repair orders")
    for key, record in orders.items():
        order = record["order"]
        if len(order) != len(set(order)) or set(order) != set(net.damage_map[key]):
            raise ValueError(f"Invalid complete repair order: {key}")
    return cfg, net, orders, cohort


def run_hurricane(args):
    cfg, net, orders, cohort = load_network(args.inputs)
    ctx = supply_context(args.inputs)
    base_capacity = float(ctx["fleet"].capacity_mw.sum()) * ctx["scale"]
    capacities = (base_capacity, base_capacity + ctx["addition"]["added_nameplate_mw"])
    protocol = resource("hurricane_protocol.json")
    controls = [
        (f"reserve_{h:02d}h", h, [], "continuous_emergency_call_stress") for h in args.reserve_hours
    ]
    if args.connection_sensitivities:
        controls += [
            (f"path_{i}_disabled", 0, [i], "continuous_emergency_call_stress") for i in range(3)
        ]
    if args.ideal_reference:
        controls += [("ideal_zero_reserve", 0, [], "ideal")]
    summaries = []
    for row in (
        cohort.head(args.limit).to_dict("records") if args.limit else cohort.to_dict("records")
    ):
        key = row["scenario_key"]
        folder = args.output / row["case_id"]
        folder.mkdir()
        load_records, curves, case_rows = [], [], []
        for demand in args.demands:
            cfg.update(
                demand_case=demand,
                equipment_model="continuous_emergency_call_stress",
                equipment_replicate=0,
                disabled_paths=[],
                reserve_hours=44,
            )
            base = replay(net, cfg, key, orders[key]["order"], "B_dc_internal_only", capacities)
            validate_baseline(base[0], row)
            for rep in range(args.replicates):
                baseline = dict(
                    base[0],
                    equipment_replicate=rep,
                    treatment="private_no_export",
                    case_id=row["case_id"],
                )
                case_rows.append(baseline)
                for treatment, reserve, disabled, equipment in controls:
                    if equipment == "ideal" and rep != 0:
                        continue
                    cfg.update(
                        equipment_replicate=rep,
                        reserve_hours=reserve,
                        disabled_paths=disabled,
                        equipment_model=equipment,
                    )
                    result = replay(
                        net, cfg, key, orders[key]["order"], "C_dc_shared_export", capacities
                    )
                    check_fixed(base[0], result[0])
                    case_rows.append(dict(result[0], treatment=treatment, case_id=row["case_id"]))
                    if rep == 0:
                        tags = dict(
                            scenario_key=key,
                            demand_case=demand,
                            equipment_replicate=rep,
                            treatment=treatment,
                        )
                        load_records.append(result[-1].assign(**tags))
                        curves.append(result[1].assign(**tags))
            tags = dict(
                scenario_key=key,
                demand_case=demand,
                equipment_replicate=0,
                treatment="private_no_export",
            )
            load_records.append(base[-1].assign(**tags))
            curves.append(base[1].assign(**tags))
        pd.DataFrame(case_rows).to_csv(folder / "summary.csv", index=False)
        pd.concat(load_records).to_csv(folder / "per_load_ens.csv.gz", index=False)
        pd.concat(curves).to_csv(folder / "trajectories.csv.gz", index=False)
        summaries.extend(case_rows)
        print(f"{row['case_id']}: paired replay verified", flush=True)
        net._equipment_cache.clear()
        net._cache.clear()
    results = pd.DataFrame(summaries)
    results.to_csv(args.output / "summary.csv", index=False)
    metrics = ["ens_mwh", "critical_ens_mwh", "export_mwh"]
    scenario = (
        results.groupby(["scenario_key", "demand_case", "treatment"])[metrics].mean().reset_index()
    )
    baseline = scenario.loc[
        scenario.treatment.eq("private_no_export"),
        ["scenario_key", "demand_case", "ens_mwh", "critical_ens_mwh"],
    ]
    scenario = scenario.merge(
        baseline, on=["scenario_key", "demand_case"], suffixes=("", "_off"), validate="many_to_one"
    )
    for metric in ["ens_mwh", "critical_ens_mwh"]:
        scenario["avoided_" + metric] = scenario[metric + "_off"] - scenario[metric]
        scenario[metric + "_reduction_pct"] = (
            100 * scenario["avoided_" + metric] / scenario[metric + "_off"].replace(0, np.nan)
        )
    scenario.to_csv(args.output / "scenario_means.csv", index=False)
    aggregates = []
    for (demand, treatment), group in scenario.groupby(["demand_case", "treatment"]):
        for metric in ["ens_mwh", "critical_ens_mwh"]:
            values = group[metric].to_numpy()
            rng = np.random.default_rng(protocol["bootstrap_seed"])
            draws = values[rng.integers(len(values), size=(4000, len(values)))].mean(axis=1)
            aggregates.append(
                {
                    "demand_case": demand,
                    "treatment": treatment,
                    "metric": metric,
                    "scenarios": len(group),
                    "mean": values.mean(),
                    "ci95_low": np.quantile(draws, 0.025),
                    "ci95_high": np.quantile(draws, 0.975),
                    "aggregate_reduction_pct": 100
                    * (group[metric + "_off"].mean() - values.mean())
                    / group[metric + "_off"].mean(),
                    "mean_scenario_reduction_pct": group[metric + "_reduction_pct"].mean(),
                }
            )
    pd.DataFrame(aggregates).to_csv(args.output / "aggregate.csv", index=False)


def run_responsive(args):
    from .responsive import FrozenSelector

    cfg, net, orders, cohort = load_network(args.inputs)
    cfg.update(reserve_hours=0, equipment_replicate=0)
    ctx = supply_context(args.inputs)
    base_capacity = float(ctx["fleet"].capacity_mw.sum()) * ctx["scale"]
    capacities = (base_capacity, base_capacity + ctx["addition"]["added_nameplate_mw"])
    selector = FrozenSelector(cfg, args.policy_config, args.checkpoint)
    records = []
    cases = cohort.head(args.limit) if args.limit else cohort
    try:
        for row in cases.to_dict("records"):
            key = row["scenario_key"]
            for demand in ["nominal", "design"]:
                cfg["demand_case"] = demand
                for mode, case, choose in [
                    ("no_export", "B_dc_internal_only", None),
                    ("fixed_export", "C_dc_shared_export", None),
                    ("responsive_export", "C_dc_shared_export", selector),
                ]:
                    result = replay(
                        net, cfg, key, orders[key]["order"], case, capacities, choose=choose
                    )
                    records.append(dict(result[0], repair_mode=mode))
            net._cache.clear()
            net._equipment_cache.clear()
            print(f"{row['case_id']}: frozen-policy comparison complete", flush=True)
    finally:
        (args.output / "inference.json").write_text(json.dumps(selector.close(), indent=2) + "\n")
    pd.DataFrame(records).to_csv(args.output / "responsive_repairs.csv", index=False)


def run_chronology(args):
    cfg, net, orders, _ = load_network(args.inputs)
    ctx = supply_context(args.inputs)
    ids = json.loads((args.inputs / "profile_ids.json").read_text())
    index = {key: i for i, key in enumerate(ids)}
    profiles = np.load(args.inputs / "profiles.npy", mmap_mode="r")
    ctx.update(
        cfg=cfg,
        network=net,
        orders=orders,
        profiles=profiles,
        profile_index=np.array([index[x] for x in net.loads.profile_id]),
    )
    protocol = resource("chronology_protocol.json")
    samples = pd.read_csv(args.inputs / "samples.csv")
    if len(samples) != 960 or not samples["sample"].is_unique:
        raise ValueError("Expected 960 locked chronological samples")
    if args.limit:
        samples = samples.head(args.limit)
    warm = int(protocol["warmup_hours"] / 0.25)
    n = len(ctx["total"]) + warm
    annual = dict(
        seed=protocol["seed"],
        stream_stage=protocol["stream_stage"],
        outage_years=960,
        demand_cases=["nominal", "design"],
        recharge_cases=["manufacturer_reference"],
        equipment_failures=True,
        refuel_enabled=True,
    )
    records = []
    for row in samples.to_dict("records"):
        sample = row["sample"]
        indices = (np.arange(n) - warm + row["offset"]) % len(ctx["total"])
        capacity = reference_trace(
            ctx["fleet"], protocol, protocol["stream_stage"], sample, n, 0.25, ctx["scale"]
        )
        for extra in added_traces(
            ctx["addrow"],
            ctx["addition"]["added_units"],
            ctx["scale"],
            protocol,
            protocol["stream_stage"],
            sample,
            n,
            0.25,
        ):
            capacity += extra
        storm = Storm(
            ctx,
            row["scenario_key"],
            indices,
            protocol["warmup_hours"] + protocol["event_elapsed_hours"],
        )
        for enabled in [False, True]:
            storm.enabled = enabled
            result, *_ = simulate_service(
                ctx["total"][indices],
                ctx["critical"][indices],
                capacity,
                cfg,
                annual,
                control="private_backup",
                year=sample,
                observation_start_h=protocol["warmup_hours"],
                storm=storm,
                reserve_hours=44 if enabled else 0,
                post_event_refuel_hours=protocol["post_event_refuel_hours"],
            )
            records.append(
                dict(result, **row, export_enabled=enabled, reserve_hours=44 if enabled else -1)
            )
        pd.DataFrame(records[-2:]).to_csv(args.output / f"sample_{sample:04d}.csv", index=False)
        print(f"Sample {sample}: paired chronological year complete", flush=True)
        net._cache.clear()
    pd.DataFrame(records).to_csv(args.output / "internal_service.csv", index=False)


def economic_summary(benefit, export_energy, cfg):
    factor = annuity_factor(cfg["real_discount_rate"], cfg["years"])
    cost = export_energy * cfg["export_resource_cost_usd_per_mwh"]
    interval = cfg["illustrative_interval_years"]
    capital = cfg["illustrative_capital_usd"]
    annual_cost = capital / factor + cfg["annual_incremental_fixed_cost_usd"] + cost / interval
    net = benefit / interval - annual_cost
    return {
        "mean_event_avoided_damage_usd": benefit,
        "mean_export_mwh_event": export_energy,
        "annualized_avoided_damage_usd": benefit / interval,
        "annualized_cost_usd": annual_cost,
        "net_annual_value_usd": net,
        "npv_usd": factor * net,
        "annuity_factor": factor,
    }


def run_economics(args):
    cfg = resource("economics.json")
    inventory = pd.read_csv(args.inputs / "case/network/loads.csv").set_index("load_id", drop=False)
    valuation = yaml.safe_load(args.valuation.read_text())
    parameters = _valuation_parameters(valuation)[0]["primary"]
    members = pd.read_csv(args.inputs / "group_memberships.csv")
    keys = members.loc[members.eval_subset.eq(cfg["scenario_subset"]), "scenario_key"].unique()
    cohort = pd.read_csv(args.inputs / "locked_scenarios.csv")
    cohort = cohort.loc[cohort.scenario_key.isin(keys)]
    if len(cohort) != 30:
        raise ValueError("Economics requires all 30 historical nonzero-damage scenarios")
    rows = []
    for case in cohort.itertuples(index=False):
        frame = pd.read_csv(args.hurricane / case.case_id / "per_load_ens.csv.gz")
        frame = frame.loc[frame.demand_case.eq("nominal") & frame.equipment_replicate.eq(0)]
        energy = {}
        for treatment in ["private_no_export", "reserve_44h"]:
            part = frame.loc[frame.treatment.eq(treatment)].set_index("load_id")
            if not part.index.is_unique or set(part.index) != set(inventory.index):
                raise ValueError("Missing, duplicated or mismatched load-level event outcomes")
            energy[treatment] = part.reindex(inventory.index).ens_mwh.to_numpy()
        off, on = energy["private_no_export"], energy["reserve_44h"]
        delta = damage_by_load(
            off, inventory, parameters, cfg["critical_planning_value_usd_per_kwh"]
        ) - damage_by_load(on, inventory, parameters, cfg["critical_planning_value_usd_per_kwh"])
        critical = inventory.is_critical.astype(str).str.lower().eq("true").to_numpy()
        residential = inventory.load_type.str.lower().eq("residential").to_numpy()
        rows.append(
            {
                "scenario_key": case.scenario_key,
                "avoided_damage_usd": float(delta.sum()),
                "export_mwh": float((off - on).sum()),
                "critical_damage_usd": float(delta[critical].sum()),
                "commercial_damage_usd": float(delta[~critical & ~residential].sum()),
                "residential_damage_usd": float(delta[~critical & residential].sum()),
            }
        )
    events = pd.DataFrame(rows)
    events.to_csv(args.output / "event_economics.csv", index=False)
    mean = events.select_dtypes("number").mean()
    summary = economic_summary(mean.avoided_damage_usd, mean.export_mwh, cfg)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    sensitivity = []
    for interval in [1, 2, 5, 10, 20, 50]:
        for multiplier in [0.5, 1, 1.5]:
            ceiling = investment_ceiling(
                multiplier * mean.avoided_damage_usd,
                mean.export_mwh * cfg["export_resource_cost_usd_per_mwh"],
                interval,
                cfg["annual_incremental_fixed_cost_usd"],
                cfg["real_discount_rate"],
                cfg["years"],
            )
            sensitivity.append(
                {
                    "interval_years": interval,
                    "valuation_multiplier": multiplier,
                    "break_even_investment_usd": float(ceiling),
                }
            )
    pd.DataFrame(sensitivity).to_csv(args.output / "investment_sensitivity.csv", index=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="analysis", required=True)
    for name in ["adequacy", "hurricane", "chronology", "economics", "responsive"]:
        p = subs.add_parser(name)
        p.add_argument("--inputs", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
        if name in ["hurricane", "chronology", "responsive"]:
            p.add_argument(
                "--limit", type=int, help="First N locked cases/samples for a packaging smoke run"
            )
        if name == "hurricane":
            p.add_argument("--replicates", type=int, choices=range(1, 33), default=32)
            p.add_argument(
                "--demands", nargs="+", choices=["nominal", "design"], default=["nominal"]
            )
            p.add_argument(
                "--reserve-hours",
                nargs="+",
                type=int,
                choices=[0, 12, 24, 36, 44, 46, 48],
                default=[44],
            )
            p.add_argument("--connection-sensitivities", action="store_true")
            p.add_argument("--ideal-reference", action="store_true")
        if name == "economics":
            p.add_argument("--hurricane", type=Path, required=True)
            p.add_argument(
                "--valuation", type=Path, default=Path("configs/economics/interruption_damage.yaml")
            )
        if name == "responsive":
            p.add_argument("--checkpoint", type=Path, required=True)
            p.add_argument(
                "--policy-config", type=Path, default=Path("configs/policies/training.yaml")
            )
    args = parser.parse_args()
    if getattr(args, "limit", None) is not None and args.limit < 1:
        parser.error("--limit must be positive")
    for name in ["demands", "reserve_hours"]:
        values = getattr(args, name, [])
        if len(values) != len(set(values)):
            parser.error(f"--{name.replace('_', '-')} must contain unique values")
    manifest = checked_inputs(args.inputs)
    args.output.mkdir(parents=True, exist_ok=False)
    run = {
        "arguments": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "input_hashes": {k: v["sha256"] for k, v in manifest.items()},
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "code_hashes": {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")},
        "resource_hashes": {p.name: sha(p) for p in RESOURCES.iterdir() if p.is_file()},
    }
    for name in ["valuation", "policy_config", "checkpoint"]:
        path = getattr(args, name, None)
        if path is not None:
            run[name + "_sha256"] = sha(path)
    (args.output / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    if args.analysis == "adequacy":
        adequacy_table(supply_context(args.inputs)).to_csv(
            args.output / "matched_supply.csv", index=False
        )
    else:
        {
            "hurricane": run_hurricane,
            "chronology": run_chronology,
            "economics": run_economics,
            "responsive": run_responsive,
        }[args.analysis](args)
    (args.output / "COMPLETE.json").write_text(
        json.dumps({"analysis": args.analysis, "complete": True}) + "\n"
    )
    print(f"Completed {args.analysis}: {args.output}")
