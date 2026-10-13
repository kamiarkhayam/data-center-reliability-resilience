"""Frozen physical reference configuration and validated operating controls."""

import copy
import json
import math
from pathlib import Path

from .network import sha

RESOURCES = Path(__file__).parent / "resources"
EXPORT_RULES = {
    "shared_finite_fuel_with_backup_power_headroom": "shared_fuel",
    "shared_fuel": "shared_fuel",
    "disabled": "disabled",
    "full_reserve": "full_reserve",
}


def facility():
    return json.loads((RESOURCES / "facility.json").read_text())


def reference_config():
    return effective(json.loads((RESOURCES / "hurricane.json").read_text()))


def effective(cfg):
    cfg = copy.deepcopy(cfg)
    cfg.setdefault("facility", facility())
    cfg.setdefault("equipment_replicate", 0)
    cfg.setdefault("reserve_hours", 0.0)
    cfg.setdefault("disabled_paths", [])
    cfg.setdefault("equipment_model", "continuous_emergency_call_stress")
    cfg.setdefault("connectivity_rule", "inherited_bundled_link_all_records_available")
    validate(cfg)
    return cfg


def validate(cfg):
    keys = {
        "seed",
        "equipment_namespace",
        "equipment_replicate",
        "cohort_size",
        "candidate_k",
        "demand_case",
        "operating_condition",
        "upstream_condition",
        "export_rule",
        "full_standby_reserve_control",
        "automatic_refueling",
        "automatic_ups_recharge",
        "fixed_policy",
        "fixed_order_resource",
        "paths",
        "facility",
        "reserve_hours",
        "disabled_paths",
        "equipment_model",
        "connectivity_rule",
    }
    if set(cfg) != keys:
        raise ValueError("Unknown or missing hurricane settings")
    if cfg["export_rule"] not in EXPORT_RULES:
        raise ValueError("Unsupported export_rule")
    if cfg["demand_case"] not in {"nominal", "design"}:
        raise ValueError("Unsupported demand_case")
    reserve = cfg["reserve_hours"]
    if isinstance(reserve, bool) or not math.isfinite(reserve) or not 0 <= reserve <= 48:
        raise ValueError("Retained reserve must be between zero and 48 hours")
    paths = cfg["disabled_paths"]
    if (
        not isinstance(paths, list)
        or any(type(i) is not int or i not in range(3) for i in paths)
        or len(set(paths)) != len(paths)
    ):
        raise ValueError("Disabled paths must be unique site indices")
    if cfg["equipment_model"] not in {"continuous_emergency_call_stress", "ideal"}:
        raise ValueError("Unknown equipment exposure model")
    if cfg["connectivity_rule"] != "inherited_bundled_link_all_records_available":
        raise ValueError("Preserve the original restoration connectivity convention")
    for key in (
        "seed",
        "equipment_namespace",
        "equipment_replicate",
        "candidate_k",
        "cohort_size",
    ):
        v = cfg[key]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"Invalid integer {key}")
    if cfg["candidate_k"] != 50:
        raise ValueError("This frozen-checkpoint integration retains K=50")
    if type(cfg["full_standby_reserve_control"]) is not bool:
        raise ValueError("Reserve-control flag must be Boolean")
    for key in ("automatic_refueling", "automatic_ups_recharge"):
        if cfg[key] is not False:
            raise ValueError(f"Hurricane {key} is unsupported; cannot ignore it")
    required = {
        "operating_condition": "grid_supplied_dc_separate_generator_support_connection",
        "upstream_condition": "planned_supply_available_no_new_upstream_storm_failure",
        "fixed_policy": "TRUE_GNN Harmonized A2C",
        "fixed_order_resource": "R0_no_dc_anchor",
    }
    for key, value in required.items():
        if cfg[key] != value:
            raise ValueError(f"Unsupported {key}")
    # Hardware changes require a new planned-capacity check, not a hidden override.
    for path, expected in cfg["facility"]["source_hashes"].items():
        if sha(path) != expected:
            raise ValueError("Facility source changed after snapshot")
    actual = facility()
    if any(cfg["facility"][k] != actual[k] for k in ("old", "service")):
        raise ValueError("Unvalidated facility change; preserve the locked archetype")


def demand(cfg):
    dc = cfg["facility"]["old"]["dc"]
    it = dc["it_mw_per_site"]
    pue = dc["reference_pue" if cfg["demand_case"] == "nominal" else "design_pue"]
    return it, it * pue


def case_rules(cfg):
    validate(cfg)
    result = {
        "A_no_dc": "disabled",
        "B_dc_internal_only": "disabled",
        "C_dc_shared_export": EXPORT_RULES[cfg["export_rule"]],
    }
    if cfg["full_standby_reserve_control"]:
        result["C_full_reserve_control"] = "full_reserve"
    return result


RECHARGE_FACTORS = {"manufacturer_reference": 1.0, "half_rate": 0.5, "no_recharge": 0.0}


def validate_annual(cfg):
    expected = {
        "seed",
        "stream_stage",
        "outage_years",
        "demand_cases",
        "recharge_cases",
        "refuel_enabled",
        "equipment_failures",
    }
    if set(cfg) != expected:
        raise ValueError("Unknown or missing annual settings")
    for key in ("seed", "stream_stage", "outage_years"):
        if type(cfg[key]) is not int or cfg[key] < 0:
            raise ValueError(f"Invalid {key}")
    if cfg["stream_stage"] < 7 or cfg["outage_years"] < 2:
        raise ValueError("Use validation streams and at least two years")
    if not cfg["demand_cases"] or len(set(cfg["demand_cases"])) != len(cfg["demand_cases"]):
        raise ValueError("Empty or duplicated demand cases")
    if set(cfg["demand_cases"]) - {"nominal", "design"}:
        raise ValueError("Unknown annual demand case")
    cases = cfg["recharge_cases"]
    if not cases or len(set(cases)) != len(cases) or set(cases) - RECHARGE_FACTORS.keys():
        raise ValueError("Unknown, empty, or repeated recharge cases")
    for key in ("refuel_enabled", "equipment_failures"):
        if type(cfg[key]) is not bool:
            raise ValueError(f"Invalid Boolean {key}")


def recharge_parameters(spec, case):
    """VRLA bulk-recovery approximation; no unreferenced final charging tail."""
    if case not in RECHARGE_FACTORS:
        raise ValueError("Unknown recharge case")
    discharge_h = spec.ups_capacity_mwh / spec.ups_power_mw
    target_mwh = 0.9 * spec.ups_capacity_mwh
    return {
        "case": case,
        "target_mwh": target_mwh,
        "rate_mw": RECHARGE_FACTORS[case] * target_mwh / (10 * discharge_h),
        "full_discharge_h": discharge_h,
        "reference_hours_to_90_percent_from_empty": 10 * discharge_h,
    }
