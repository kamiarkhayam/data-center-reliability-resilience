"""Baseline and paired fixed-repair conservation checks."""

import numpy as np


def validate_baseline(result, row):
    for actual, expected in (
        ("ens_mwh", "cached_ens_mwh"),
        ("critical_ens_mwh", "cached_critical_ens_mwh"),
        ("physical_completion_h", "cached_service_completion_h"),
    ):
        if not np.isclose(result[actual], row[expected], atol=1e-6, rtol=1e-10):
            raise AssertionError(
                f"Original no-export replay mismatch {row['scenario_key']}: {actual} {result[actual]} != {row[expected]}"
            )


def check_fixed(base, summary):
    if base["repair_order_hash"] != summary["repair_order_hash"]:
        raise AssertionError("Fixed repair order changed")
    avoided = base["ens_mwh"] - summary["ens_mwh"]
    if not np.isclose(avoided, summary["export_mwh"], atol=1e-6):
        raise AssertionError("Fixed-order avoided ENS is not delivered export")
    if summary["critical_ens_mwh"] > base["critical_ens_mwh"] + 1e-7:
        raise AssertionError("Fixed-order support worsened critical service")
    if summary["min_remaining_design_backup_h"] < summary["retained_reserve_hours"] - 1e-7:
        raise AssertionError("Retained reserve violated")
