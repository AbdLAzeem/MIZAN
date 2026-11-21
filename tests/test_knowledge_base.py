import re
from pathlib import Path

KB = Path(__file__).resolve().parents[1] / "data" / "knowledge"
FILES = [
    "equity.md",
    "fixed-income.md",
    "gold.md",
    "diversification.md",
    "risk-and-time-horizon.md",
    "rebalancing-and-drift.md",
    "how-mizan-works.md",
]
LAST_LINE = "Educational simulation. This is not investment advice."
EM, EN = chr(0x2014), chr(0x2013)
BANNED = ["Thn" + "dr", "guaranteed return", "will definitely", "you should buy"]


def read(name: str) -> str:
    return (KB / name).read_text(encoding="utf-8")


def test_exactly_the_expected_files_exist() -> None:
    assert sorted(p.name for p in KB.glob("*")) == sorted(FILES)


def test_structure_and_length() -> None:
    for name in FILES:
        text = read(name)
        lines = text.strip().splitlines()
        assert lines[0].startswith("# ") and not lines[0].startswith("## "), name
        assert 2 <= len(re.findall(r"^## ", text, flags=re.M)) <= 4, name
        assert lines[-1] == LAST_LINE, name
        words = len(text.split())
        assert 250 <= words <= 450, f"{name} has {words} words"


def test_no_forbidden_characters_or_claims() -> None:
    for name in FILES:
        text = read(name)
        assert EM not in text and EN not in text, name
        for word in BANNED:
            assert word.lower() not in text.lower(), (name, word)
        assert not re.search(r"\d+(\.\d+)?\s*%", text), f"{name} has a percentage"


def test_mizan_explainer_covers_consent_flow() -> None:
    text = read("how-mizan-works.md").lower()
    for phrase in ["draft", "confirm", "10 minutes"]:
        assert phrase in text


def test_fund_examples_use_fictional_names_only() -> None:
    joined = " ".join(read(n) for n in FILES)
    assert "NGEF" in joined and "DIBF" in joined and "SGLD" in joined
