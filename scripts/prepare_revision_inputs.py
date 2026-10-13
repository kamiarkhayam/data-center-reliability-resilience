"""Export the revised study's explicit input bundle from the original workspace.

This copies selected numerical inputs only, never full research directories.
The original workspace is read-only; the destination must not already exist.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    files = {}
    case = "data_processed/final_case_v0_3_benchmark_hard_case/"
    for name in [
        "network/nodes.csv",
        "network/edges.csv",
        "network/loads.csv",
        "network/data_centers.csv",
        "dc_scenarios/anchor_zones.csv",
        "scenario_design/benchmark_hard_hurricane_scenario_manifest.csv",
        "scenario_design/benchmark_hard_damage_components.csv",
    ]:
        files["case/" + name] = case + name
    bank = "research/hurricane_analysis_v1/outputs/production_v2/"
    for name in ["locked_scenarios.csv", "group_memberships.csv", "fixed_orders.json"]:
        files[name] = bank + name
    for name in ["profiles.npy", "profile_ids.json", "samples.csv"]:
        files[name] = "research/annual_risk_analysis_v1/outputs/prepared/" + name
    files["community_profile.csv"] = (
        "research/reliability_pilot_v1/outputs/pilot_200y_v2/audit/community_profile.csv"
    )
    files["planned_supply.json"] = (
        "research/reliability_pilot_v2/outputs/planned_reference_v1/LOCKED_PLAN.json"
    )
    missing = [rel for rel in files.values() if not (root / rel).is_file()]
    if missing:
        raise FileNotFoundError("Missing original inputs:\n" + "\n".join(missing))
    args.output.mkdir(parents=True, exist_ok=False)
    records = {}
    for target, source in files.items():
        destination = args.output / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / source, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        records[target] = {"source": source, "sha256": digest, "bytes": destination.stat().st_size}
    (args.output / "manifest.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(records)} inputs in {args.output}")


if __name__ == "__main__":
    main()
