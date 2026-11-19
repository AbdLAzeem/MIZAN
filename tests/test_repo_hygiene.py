"""Guards for the project rules that apply to every tracked text file."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".txt", ".example", ""}

# Built from code points so this file does not contain the banned characters or words itself.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
BANNED_WORDS = ["Thn" + "dr", "Co-Authored" + "-By", "Cla" + "ude"]


def tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [ROOT / p for p in out if (ROOT / p).suffix in TEXT_SUFFIXES and (ROOT / p).is_file()]


def test_no_em_or_en_dashes_in_tracked_files() -> None:
    bad = [
        str(p.relative_to(ROOT))
        for p in tracked_text_files()
        if {EM_DASH, EN_DASH} & set(p.read_text(encoding="utf-8"))
    ]
    assert not bad, f"em or en dash found in: {bad}"


def test_no_banned_words_in_tracked_files() -> None:
    bad = [
        (str(p.relative_to(ROOT)), w)
        for p in tracked_text_files()
        for w in BANNED_WORDS
        if w in p.read_text(encoding="utf-8")
    ]
    assert not bad, f"banned words found: {bad}"
