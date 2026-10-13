"""Deterministic protection, recharge, conservation, and input regressions."""

import copy
import json
import unittest

import numpy as np

from dc_restoration.revision.chronology import Equipment, simulate_service
from dc_restoration.revision.export import equipment_config, initial_state
from dc_restoration.revision.internal_service import private_interval
from dc_restoration.revision.service import check_record
from dc_restoration.revision.settings import effective, recharge_parameters, validate_annual
from dc_restoration.revision.settings import reference_config as config
from dc_restoration.revision.supply import allocate


class Fixture(unittest.TestCase):
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
        self.old, _, self.spec = equipment_config(self.cfg, 100)
        self.recharge = recharge_parameters(self.spec, "manufacturer_reference")

    def simulate(self, cap, control="private_backup", **kwargs):
        cap = np.asarray(cap, dtype=float)
        return simulate_service(
            np.full(len(cap), 100.0),
            np.full(len(cap), 20.0),
            cap,
            self.cfg,
            self.annual,
            control=control,
            keep_ledger=True,
            **kwargs,
        )

    def interval(self, state=None, spec=None, **kwargs):
        state = state or initial_state(self.spec)
        defaults = {
            "start_h": 0,
            "duration_h": 1,
            "it_mw": 20,
            "auxiliary_mw": 6,
            "online_units": 13,
            "grid_fraction": 1,
            "recharge": self.recharge,
        }
        defaults.update(kwargs)
        return private_interval(state, spec or self.spec, **defaults)


class ProtectionTests(Fixture):
    def test_no_shortage_no_backup(self):
        r, rows, _ = self.simulate([200] * 96)
        self.assertEqual(r["automatic_backup_calls"], 0)
        self.assertEqual(r["generator_to_it_mwh"], 0)
        self.assertEqual(r["ups_to_it_mwh"], 0)
        self.assertEqual(r["fuel_start_mwh"], r["fuel_end_mwh"])
        self.assertTrue(all(row["utility_served_fraction"] == 1 for row in rows))

    def test_tiny_shortfall_only_interrupts_allocated_fraction(self):
        r, rows, _ = self.simulate([177.9] * 4)
        self.assertEqual(r["coordinated_transfer_site_hours"], 0)
        self.assertTrue(all(0.99 < row["utility_served_fraction"] < 1 for row in rows))
        self.assertLess(r["generator_to_it_mwh"] + r["generator_to_aux_mwh"], 0.1)
        self.assertGreater(r["generator_to_it_mwh"], 0)
        self.assertGreater(r["ups_to_it_mwh"], 0)

    def test_private_grid_allocation_is_unchanged(self):
        cap = np.array([178, 177.9, 150, 90, 0, 200])
        r, _, _ = self.simulate(cap)
        comm, crit, dc = allocate(np.full(6, 100.0), np.full(6, 20.0), 78.0, cap)
        for key, data in [
            ("community_ens_mwh", comm),
            ("critical_ens_mwh", crit),
            ("dc_forced_grid_curtailment_mwh", dc),
        ]:
            self.assertAlmostEqual(r[key], data.sum() * 0.25)

    def test_automatic_generator_ready_at_three_minutes(self):
        r, rows, _ = self.simulate([0] * 4)
        generating = [row for row in rows if row["generator_to_it_mwh"] > 1e-8]
        self.assertAlmostEqual(min(row["start_h"] for row in generating), 0.05)
        self.assertAlmostEqual(r["ups_to_it_mwh"], 3 * 20 * 0.05)
        self.assertAlmostEqual(r["auxiliary_unserved_mwh"], 3 * 6 * 0.05)
        self.assertAlmostEqual(r["it_unserved_mwh"], 0)

    def test_one_site_outage_does_not_trip_other_sites(self):
        available = np.ones((4, 3), dtype=bool)
        available[:, 0] = False
        r, rows, _ = self.simulate([200] * 4, utility_available=available)
        self.assertEqual(r["automatic_backup_calls"], 1)
        self.assertTrue(all(row["generator_to_it_mwh"] == 0 for row in rows if row["site"] != 0))
        self.assertGreater(sum(row["generator_to_it_mwh"] for row in rows if row["site"] == 0), 0)

    def test_coordinated_keeps_private_protection_before_authorization(self):
        r, rows, _ = self.simulate([177] * 4, control="private_plus_coordinated")
        early = [row for row in rows if 0.05 <= row["start_h"] < 0.25]
        self.assertGreater(sum(row["generator_to_it_mwh"] for row in early), 0)
        transfer = [row for row in rows if row["coordinated_transfer"]]
        self.assertAlmostEqual(min(row["start_h"] for row in transfer), 0.25)
        self.assertTrue(all(row["utility_served_fraction"] == 0 for row in transfer))
        self.assertAlmostEqual(r["coordinated_transfer_site_hours"], 3 * 0.75)

    def test_coordinated_releases_grid_capacity_only_after_deadline(self):
        base, _, _ = self.simulate([150] * 4)
        coord, _, community = self.simulate([150] * 4, control="private_plus_coordinated")
        self.assertLess(coord["community_ens_mwh"], base["community_ens_mwh"])
        self.assertTrue(
            all(row["community_ens_mwh"] == 0 for row in community if row["start_h"] >= 0.25)
        )

    def test_zero_fuel_no_free_generator_service(self):
        r, _, _ = self.simulate([0] * 4, initial_fuel_mwh=0)
        self.assertEqual(r["generator_to_it_mwh"], 0)
        self.assertAlmostEqual(r["it_unserved_mwh"], 60 - 10)
        self.assertAlmostEqual(r["auxiliary_unserved_mwh"], 18)

    def test_fuel_exhaustion_changes_service_without_stalling(self):
        r, _, _ = self.simulate([0] * 4, initial_fuel_mwh=1)
        self.assertAlmostEqual(r["generator_to_it_mwh"] + r["generator_to_aux_mwh"], 3)
        self.assertGreater(r["it_unserved_mwh"], 0)

    def test_generator_failures_do_not_suppress_ups(self):
        state = initial_state(self.spec)
        rows = self.interval(state, grid_fraction=0, online_units=0)
        self.assertAlmostEqual(sum(x["ups_to_it_mwh"] for x in rows), 20 / 6)
        self.assertGreater(sum(x["it_unserved_mwh"] for x in rows), 0)

    def test_partial_generator_capacity_prioritizes_interrupted_it(self):
        rows = self.interval(grid_fraction=0.5, online_units=1)
        self.assertAlmostEqual(sum(x["generator_to_it_mwh"] for x in rows), 2.725)
        self.assertEqual(sum(x["generator_to_aux_mwh"] for x in rows), 0)
        self.assertAlmostEqual(sum(x["grid_to_aux_mwh"] for x in rows), 3)

    def test_no_ordinary_community_export(self):
        for control in ("private_backup", "private_plus_coordinated"):
            r, rows, _ = self.simulate([150] * 4, control=control)
            self.assertEqual(r["generator_to_export_mwh"], 0)
            for row in rows:
                check_record(row, self.spec)

    def test_return_to_grid_does_not_reset_storage(self):
        r, _, _ = self.simulate([0, 200, 0, 200], recharge_case="no_recharge")
        self.assertAlmostEqual(r["ups_end_mwh"], 10 - 6)
        self.assertAlmostEqual(r["grid_to_ups_mwh"], 0)

    def test_warmup_storage_carried_and_reporting_balanced(self):
        r, _, _ = self.simulate(
            [0, 200, 0, 200], observation_start_h=0.25, recharge_case="no_recharge"
        )
        self.assertAlmostEqual(r["ups_start_mwh"], 7)
        self.assertAlmostEqual(r["reporting_hours"], 0.75)
        self.assertLess(r["max_balance_residual_mwh"], 1e-8)

    def test_refueling_is_bounded_and_accounted(self):
        self.annual["refuel_enabled"] = True
        r, _, _ = self.simulate([0] * 220, initial_fuel_mwh=1)
        self.assertGreater(r["fuel_delivery_accepted_mwh"], 0)
        self.assertLessEqual(r["fuel_end_mwh"], 4320)
        self.assertLess(r["max_balance_residual_mwh"], 1e-7)


class RechargeTests(Fixture):
    def test_source_derivation(self):
        self.assertAlmostEqual(self.recharge["rate_mw"], 1.8)
        self.assertAlmostEqual(self.recharge["target_mwh"], 3)
        self.assertAlmostEqual(self.recharge["reference_hours_to_90_percent_from_empty"], 100 / 60)

    def test_reference_recovers_90_percent_in_100_minutes(self):
        state = initial_state(self.spec)
        state.ups_mwh = 0
        rows = self.interval(state, duration_h=100 / 60, grid_charge_offer_mw=1.8)
        self.assertAlmostEqual(state.ups_mwh, 3)
        self.assertAlmostEqual(sum(x["grid_to_ups_mwh"] for x in rows), 3)

    def test_half_rate_requires_200_minutes(self):
        state = initial_state(self.spec)
        state.ups_mwh = 0
        slow = recharge_parameters(self.spec, "half_rate")
        self.interval(state, duration_h=100 / 60, recharge=slow, grid_charge_offer_mw=10)
        self.assertAlmostEqual(state.ups_mwh, 1.5)
        self.interval(state, duration_h=100 / 60, recharge=slow, grid_charge_offer_mw=10)
        self.assertAlmostEqual(state.ups_mwh, 3)

    def test_no_unreferenced_tail_to_full(self):
        state = initial_state(self.spec)
        state.ups_mwh = 0
        self.interval(state, duration_h=20, grid_charge_offer_mw=10)
        self.assertAlmostEqual(state.ups_mwh, 3)

    def test_initial_full_inventory_is_not_clipped(self):
        state = initial_state(self.spec)
        self.interval(state, duration_h=20, grid_charge_offer_mw=10)
        self.assertAlmostEqual(state.ups_mwh, 20 / 6)

    def test_no_recharge_sensitivity(self):
        state = initial_state(self.spec)
        state.ups_mwh = 0
        self.interval(
            state,
            recharge=recharge_parameters(self.spec, "no_recharge"),
            grid_charge_offer_mw=10,
        )
        self.assertEqual(state.ups_mwh, 0)

    def test_charger_limited_by_grid_offer(self):
        state = initial_state(self.spec)
        state.ups_mwh = 0
        self.interval(state, grid_charge_offer_mw=0.5)
        self.assertAlmostEqual(state.ups_mwh, 0.5)

    def test_charger_limited_by_facility_design(self):
        state = initial_state(self.spec)
        state.ups_mwh = 0
        self.interval(state, auxiliary_mw=10, grid_charge_offer_mw=10)
        self.assertEqual(state.ups_mwh, 0)

    def test_charger_uses_only_remaining_design_headroom(self):
        state = initial_state(self.spec)
        state.ups_mwh = 0
        self.interval(state, auxiliary_mw=9.5, grid_charge_offer_mw=10)
        self.assertAlmostEqual(state.ups_mwh, 0.5)

    def test_charging_not_allowed_in_island_or_interrupted_blocks(self):
        for fraction in (0, 0.5):
            with self.subTest(fraction=fraction), self.assertRaises(ValueError):
                self.interval(grid_fraction=fraction, grid_charge_offer_mw=1)

    def test_full_grid_load_priority_and_spare_power_sharing(self):
        r, _, _ = self.simulate([180] * 4, initial_ups_mwh=0)
        self.assertAlmostEqual(r["grid_to_ups_mwh"], 2)
        self.assertEqual(r["community_ens_mwh"], 0)

    def test_no_recharge_when_grid_has_no_headroom(self):
        r, _, _ = self.simulate([178] * 4, initial_ups_mwh=0)
        self.assertEqual(r["grid_to_ups_mwh"], 0)

    def test_annual_design_load_cannot_exceed_locked_capacity(self):
        self.cfg["demand_case"] = "design"
        r, _, _ = self.simulate([220] * 4, initial_ups_mwh=0)
        self.assertEqual(r["grid_to_ups_mwh"], 0)

    def test_repeated_outages_exercise_recharge_sensitivity(self):
        totals = {}
        for case in ("manufacturer_reference", "half_rate", "no_recharge"):
            totals[case], _, _ = self.simulate(
                [0, 200] * 10,
                recharge_case=case,
                initial_fuel_mwh=0,
            )
        self.assertLess(
            totals["manufacturer_reference"]["it_unserved_mwh"],
            totals["half_rate"]["it_unserved_mwh"],
        )
        self.assertLess(
            totals["half_rate"]["it_unserved_mwh"],
            totals["no_recharge"]["it_unserved_mwh"],
        )


class IntegrityTests(Fixture):
    def test_repeated_stochastic_run_identical(self):
        self.annual["equipment_failures"] = True
        first = self.simulate([0, 200] * 30)
        self.assertEqual(first, self.simulate([0, 200] * 30))

    def test_energy_balances_random_capacity(self):
        rng = np.random.default_rng(99)
        cap = rng.uniform(0, 210, 100)
        for control in ("private_backup", "private_plus_coordinated"):
            for case in self.annual["recharge_cases"]:
                r, rows, _ = self.simulate(cap, control=control, recharge_case=case)
                self.assertLess(r["max_balance_residual_mwh"], 1e-7)
                for row in rows:
                    check_record(row, self.spec)

    def test_invalid_configuration_rejected(self):
        for key, value in [
            ("recharge_cases", ["unvalidated"]),
            ("recharge_cases", []),
            ("ups_recharge_mw_per_site", 1),
            ("refuel_enabled", "yes"),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_annual({**self.annual, key: value})

    def test_invalid_interruption_inputs_rejected(self):
        for fraction in (-1, 2, float("nan")):
            with self.subTest(fraction=fraction), self.assertRaises(ValueError):
                self.interval(grid_fraction=fraction)
        with self.assertRaises(ValueError):
            self.simulate([200], utility_available=[[1, 0, 1]])
        with self.assertRaises(ValueError):
            self.simulate([200], initial_ups_mwh=-1)

    def test_unknown_control_and_recharge_rejected(self):
        with self.assertRaises(ValueError):
            self.simulate([200], control="conventional")
        with self.assertRaises(ValueError):
            self.simulate([200], recharge_case="unreferenced")

    def test_all_start_failures_leave_ups_and_utility_protection(self):
        dc = copy.deepcopy(self.old["dc"])
        dc.update(failure_to_start=1, initial_availability=1, repair_enabled=False)
        eq = Equipment(self.spec, dc, 1, stochastic=True)
        eq.call(0, 0.05)
        self.assertFalse(eq.online(1).any())
        rows = self.interval(grid_fraction=0.99, online_units=0)
        self.assertGreater(sum(row["ups_to_it_mwh"] for row in rows), 0)
        self.assertAlmostEqual(sum(row["grid_to_it_mwh"] for row in rows), 19.8)

    def test_actual_running_exposure_only(self):
        eq = Equipment(self.spec, self.old["dc"], 1, stochastic=False)
        eq.call(0, 0.05)
        eq.life[:] = 2
        eq.consume(0.1, 1, False)
        np.testing.assert_array_equal(eq.life, np.full(13, 2))
        eq.consume(0.1, 1, True)
        np.testing.assert_array_equal(eq.life, np.ones(13))

    def test_invalid_design_load_rejected(self):
        with self.assertRaises(ValueError):
            self.interval(auxiliary_mw=11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
