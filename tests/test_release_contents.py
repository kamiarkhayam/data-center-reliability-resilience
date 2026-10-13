"""The code-only boundary must reject results even if force-added to Git."""

import runpy
from pathlib import Path

import pytest

CHECK = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/check_release_contents.py")
)["violations"]


@pytest.mark.parametrize(
    "path",
    [
        "paper/reference_results/event_summary.csv",
        "docs/chart.PNG",
        "examples/report.pdf",
        "outputs/summary.txt",
        "docs/experiment.csv",
        "data/external/input.json",
        "models/policy.pt",
    ],
)
def test_rejects_research_artifact_paths(path):
    assert CHECK([path])


def test_permits_code_parameters_provenance_and_invented_fixtures():
    assert not CHECK(
        [
            "README.md",
            "data/README.md",
            "src/dc_restoration/revision/workflows.py",
            "configs/economics/interruption_damage.yaml",
            "docs/provenance/revision_regression.json",
            "src/dc_restoration/revision/resources/rts_fleet.csv",
            "tests/fixtures/tiny_case/network/loads.csv",
        ]
    )
