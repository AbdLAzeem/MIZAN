import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from mizan.agent.guardrails import is_suspicious_passage

_WORD_RE = re.compile(r"[a-z]+")
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "what",
        "how",
        "does",
        "are",
        "for",
        "with",
        "this",
        "that",
        "you",
        "your",
        "can",
        "should",
        "about",
    }
)


@dataclass(frozen=True)
class Passage:
    source: str
    heading: str
    text: str


def load_passages(directory: Path) -> list[Passage]:
    passages: list[Passage] = []
    md_files = sorted(directory.glob("*.md"), key=lambda p: p.name)

    for file_path in md_files:
        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()

        main_title = ""
        current_heading: str | None = None
        current_lines: list[str] = []

        for line in lines:
            if line.startswith("# ") and not line.startswith("## "):
                if not main_title:
                    main_title = line[2:].strip()
            elif line.startswith("## "):
                if current_heading is None:
                    # Text before the first ## section
                    body = "\n".join(current_lines).strip()
                    if body:
                        passages.append(
                            Passage(
                                source=file_path.name,
                                heading=main_title or "Introduction",
                                text=body,
                            )
                        )
                else:
                    body = "\n".join(current_lines).strip()
                    if body:
                        passages.append(
                            Passage(
                                source=file_path.name,
                                heading=current_heading,
                                text=body,
                            )
                        )
                current_heading = line[3:].strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_heading is not None:
            body = "\n".join(current_lines).strip()
            if body:
                passages.append(
                    Passage(
                        source=file_path.name,
                        heading=current_heading,
                        text=body,
                    )
                )
        elif main_title:
            body = "\n".join(current_lines).strip()
            if body:
                passages.append(
                    Passage(
                        source=file_path.name,
                        heading=main_title,
                        text=body,
                    )
                )

    return passages


def _tokenize(text: str) -> list[str]:
    words = _WORD_RE.findall(text.lower())
    return [w for w in words if len(w) >= 3 and w not in _STOP_WORDS]


def search(passages: Sequence[Passage], query: str, k: int = 3) -> list[Passage]:
    query_tokens = _tokenize(query)
    distinct_query_tokens = list(dict.fromkeys(query_tokens))

    scored: list[tuple[int, Passage]] = []

    for passage in passages:
        if is_suspicious_passage(passage.text):
            continue

        passage_text_tokens = _tokenize(passage.text)
        passage_heading_tokens = set(_tokenize(passage.heading))

        score = 0
        for token in distinct_query_tokens:
            count = passage_text_tokens.count(token)
            score += min(count, 3)
            if token in passage_heading_tokens:
                score += 2

        if score > 0:
            scored.append((score, passage))

    # Sort by score descending, then source ascending, then heading ascending
    scored.sort(key=lambda item: (-item[0], item[1].source, item[1].heading))

    return [item[1] for item in scored[:k]]
