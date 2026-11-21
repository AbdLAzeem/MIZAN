import mizan
from mizan.disclaimer import DISCLAIMER


def test_package_has_version() -> None:
    assert mizan.__version__ == "0.1.0"


def test_disclaimer_says_simulation_and_no_advice() -> None:
    assert "simulation" in DISCLAIMER
    assert "does not give investment advice" in DISCLAIMER
