"""Boundary, pairing, and disjoint loss-accounting tests."""

import importlib.util
import json
import unittest
from unittest.mock import patch

import numpy as np

from dc_restoration.revision.chronology import Equipment, simulate_service
from dc_restoration.revision.settings import effective
from dc_restoration.revision.settings import reference_config as config
from dc_restoration.revision.supply import allocate


class FakeStorm:
    def __init__(self, start=1.0, end=2.0, enabled=True):
        self.start, self.end, self.enabled = start, end, enabled

    def sample(self, now):
        if self.start <= now < self.end - 1e-9:
            return (
                30.0,
                15.0,
                np.array([[10.0, 20.0], [0.0, 0.0], [0.0, 0.0]])
                if self.enabled
                else np.zeros((3, 2)),
            )
        return 0.0, 0.0, np.zeros((3, 2))

    def boundary(self, now):
        return next((t for t in (self.start, self.end) if t > now + 1e-8), np.inf)


class JointTests(unittest.TestCase):
    def setUp(self):
        self.cfg = effective(config())
        self.annual = {
            "seed": 20260913,
            "stream_stage": 7,
            "outage_years": 200,
            "demand_cases": ["nominal", "design"],
            "recharge_cases": ["manufacturer_reference", "half_rate", "no_recharge"],
            "refuel_enabled": True,
            "equipment_failures": True,
        }
        self.annual.update(equipment_failures=False, refuel_enabled=False)

    def run_case(self, storm=None, capacity=200.0, steps=16, **kw):
        return simulate_service(
            np.full(steps, 100.0),
            np.full(steps, 20.0),
            np.full(steps, capacity),
            self.cfg,
            self.annual,
            control="private_backup",
            storm=storm,
            keep_ledger=True,
            **kw,
        )

    def test_exact_event_export(self):
        r, rows, _ = self.run_case(FakeStorm())
        self.assertAlmostEqual(r["generator_to_export_mwh"], 3.75)
        self.assertAlmostEqual(r["community_ens_mwh"], 26.25)
        self.assertAlmostEqual(r["critical_ens_mwh"], 11.25)
        self.assertAlmostEqual(r["fuel_end_mwh"], 4320 - 3.75)
        self.assertEqual(r["it_unserved_mwh"], 0)
        self.assertTrue(
            all(x["grid_to_it_mwh"] > 0 for x in rows if x["generator_to_export_mwh"] > 0)
        )

    def test_full_reserve_and_disabled_match(self):
        a, *_ = self.run_case(FakeStorm(enabled=False))
        b, *_ = self.run_case(FakeStorm(), reserve_hours=48)
        self.assertEqual(a["community_ens_mwh"], b["community_ens_mwh"])
        self.assertEqual(b["generator_to_export_mwh"], 0)

    def test_fractional_repair_completion(self):
        r, *_ = self.run_case(FakeStorm(end=2.13))
        self.assertAlmostEqual(r["community_ens_mwh"], 30 * 1.13 - 5 * 0.88)

    def test_nonfull_initial_fuel_not_reset(self):
        r, *_ = self.run_case(FakeStorm(), initial_fuel_mwh=1321, reserve_hours=44)
        self.assertEqual(r["minimum_onset_fuel_mwh"], 1321)
        self.assertAlmostEqual(r["generator_to_export_mwh"], 1.0)
        self.assertAlmostEqual(r["minimum_fuel_after_export_mwh"], 1320.0)

    def test_low_fuel_suspends_export(self):
        r, *_ = self.run_case(FakeStorm(), initial_fuel_mwh=1200, reserve_hours=44)
        self.assertEqual(r["generator_to_export_mwh"], 0)

    def test_supply_and_damage_not_double_counted(self):
        r, *_ = self.run_case(FakeStorm(enabled=False), capacity=120.0)
        ordinary = float(allocate(100.0, 20.0, 78.0, 120.0)[0])
        during = float(allocate(70.0, 5.0, 78.0, 120.0)[0]) + 30.0
        self.assertAlmostEqual(r["community_ens_mwh"], 3 * ordinary + during)
        self.assertAlmostEqual(
            r["community_ens_mwh"],
            r["disconnected_community_ens_mwh"] + r["connected_supply_ens_mwh"],
        )

    def test_internal_interruption_blocks_export(self):
        r, *_ = self.run_case(FakeStorm(), capacity=120.0)
        self.assertEqual(r["generator_to_export_mwh"], 0)
        self.assertGreater(r["generator_to_it_mwh"], 0)

    def test_post_event_restock(self):
        r, *_ = self.run_case(FakeStorm(), steps=240, post_event_refuel_hours=2)
        self.assertAlmostEqual(r["fuel_end_mwh"], 4320.0)
        self.assertAlmostEqual(r["fuel_delivery_accepted_mwh"], 3.75)

    def test_design_power_headroom(self):
        self.cfg["demand_case"] = "design"
        r, *_ = self.run_case(FakeStorm())
        self.assertAlmostEqual(r["maximum_site_export_mw"], 2.7)
        self.assertAlmostEqual(r["generator_to_export_mwh"], 2.7 * 0.75)

    def test_small_export_advances_running_failure_clock(self):
        class ShortLifeEquipment(Equipment):
            def start_unit(self, u, now, delay):
                super().start_unit(u, now, delay)
                if u == 0:
                    self.life[u] = 1e-4

        class SmallStorm(FakeStorm):
            def sample(self, now):
                a, b, requests = super().sample(now)
                requests *= 1e-6
                return a, b, requests

        with patch("dc_restoration.revision.chronology.Equipment", ShortLifeEquipment):
            r, rows, _ = self.run_case(SmallStorm())
        self.assertLess(len(rows), 200)
        self.assertGreater(r["generator_to_export_mwh"], 0)
        self.assertLess(r["max_balance_residual_mwh"], 1e-7)


if __name__ == "__main__":
    unittest.main()
