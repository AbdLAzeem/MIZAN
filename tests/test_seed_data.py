import json
from decimal import Decimal
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "data" / "seed" / "instruments.json"
ASSET_CLASSES = {"equity", "fixed_income", "gold"}


def test_seed_instruments_are_valid_and_cover_every_asset_class() -> None:
    items = json.loads(SEED.read_text(encoding="utf-8"))
    assert {i["asset_class"] for i in items} == ASSET_CLASSES
    assert len({i["symbol"] for i in items}) == len(items)
    assert all(Decimal(i["price"]) > 0 for i in items)
