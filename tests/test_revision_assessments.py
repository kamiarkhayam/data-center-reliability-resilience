import itertools

import numpy as np
import pandas as pd
import pytest

from dc_restoration.revision.export import equipment_config, grid_interval, initial_state
from dc_restoration.revision.settings import reference_config
from dc_restoration.revision.supply import capacity_distribution, shortage_expectation
from dc_restoration.revision.workflows import economic_summary


def test_capacity_convolution_against_direct_enumeration():
    ratings, probabilities = [2, 3, 5], [0.1, 0.2, 0.3]
    fleet = pd.DataFrame({"capacity_mw": ratings, "q": probabilities})
    capacity, distribution = capacity_distribution(fleet)
    expected = np.zeros(11)
    for state in itertools.product([False, True], repeat=3):
        mw = sum(r for r, up in zip(ratings, state) if up)
        probability = np.prod([1 - q if up else q for q, up in zip(probabilities, state)])
        expected[mw] += probability
    np.testing.assert_allclose(distribution, expected)
    demand = np.array([0, 2.5, 7, 15])
    energy, hours = shortage_expectation(demand, capacity, distribution)
    np.testing.assert_allclose(
        energy, (np.maximum(0, demand[:, None] - capacity) * expected).sum(axis=1)
    )
    np.testing.assert_allclose(hours, ((capacity < demand[:, None]) * expected).sum(axis=1))


@pytest.mark.parametrize("reserve,expected", [(44, 120), (48, 0)])
def test_export_stops_exactly_at_fixed_reserve(reserve, expected):
    _, _, spec = equipment_config(reference_config(), 1000)
    state = initial_state(spec)
    rows = grid_interval(
        state,
        spec,
        start_h=0,
        duration_h=1000,
        online=13,
        it_mw=20,
        auxiliary_mw=6,
        request_mw=5,
        reserve_hours=reserve,
    )
    assert sum(r["generator_to_export_mwh"] for r in rows) == pytest.approx(expected)
    assert state.fuel_mwh == pytest.approx(1440 - expected)


@pytest.mark.parametrize(
    "internal,online,expected", [(26, 13, 5), (26, 12, 3.975), (30, 13, 2.7), (30, 12, 0)]
)
def test_generation_headroom_and_contingency(internal, online, expected):
    _, _, spec = equipment_config(reference_config(), 10)
    rows = grid_interval(
        initial_state(spec),
        spec,
        start_h=1,
        duration_h=1,
        online=online,
        it_mw=20,
        auxiliary_mw=internal - 20,
        request_mw=50,
        reserve_hours=44,
    )
    assert rows[0]["export_mw"] == pytest.approx(expected)


def test_synthetic_economic_identity_and_accounting():
    # Invented one-year example: annuity factor = 1 / 1.1.
    config = {
        "real_discount_rate": 0.1,
        "years": 1,
        "export_resource_cost_usd_per_mwh": 5,
        "illustrative_interval_years": 2,
        "illustrative_capital_usd": 100,
        "annual_incremental_fixed_cost_usd": 10,
    }
    result = economic_summary(400, 4, config)
    assert result["annualized_avoided_damage_usd"] == pytest.approx(200)
    assert result["annualized_cost_usd"] == pytest.approx(130)
    assert result["net_annual_value_usd"] == pytest.approx(70)
    assert result["npv_usd"] == pytest.approx(
        result["annuity_factor"] * result["net_annual_value_usd"]
    )
    assert result["npv_usd"] == pytest.approx(700 / 11)
