import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from mizan.disclaimer import DISCLAIMER

ALLOWED_CONSTANTS: frozenset[Decimal] = frozenset(Decimal(x) for x in (0, 1, 2, 3, 10, 100))

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SUSPICIOUS_RE = re.compile(
    r"\bignore\s+(all|any|previous|prior|the)\b|"
    r"\bdisregard\b|"
    r"\bsystem\s+prompt\b|"
    r"\byou\s+must\b|"
    r"\bconfirm_order\b|"
    r"\bconfirm\s+(the|this|all|every)\b|"
    r"\bdo\s+not\s+tell\b|"
    r"\boverride\b|"
    r"\breveal\b",
    re.IGNORECASE,
)

REFUSAL_OUT_OF_SCOPE: str = (
    "Mizan only helps with educational questions about assets and its simulated portfolio, "
    "and cannot give personal investment advice."
)

FALLBACK_UNVERIFIED: str = (
    "The numbers in the answer could not be verified so none are shown. Please try again."
)


def extract_numbers(text: str) -> list[Decimal]:
    matches = _NUM_RE.findall(text)
    return [Decimal(match.replace(",", "")) for match in matches]


@dataclass(frozen=True)
class FaithfulnessResult:
    ok: bool
    unsupported: tuple[Decimal, ...]


def check_numeric_faithfulness(
    reply: str,
    tool_outputs: Sequence[str],
    allowed: frozenset[Decimal] = ALLOWED_CONSTANTS,
) -> FaithfulnessResult:
    reply_numbers = extract_numbers(reply)
    if not reply_numbers:
        return FaithfulnessResult(ok=True, unsupported=())

    tool_numbers: set[Decimal] = set()
    for output in tool_outputs:
        tool_numbers.update(extract_numbers(output))

    allowed_set = set(allowed)
    unsupported_set: set[Decimal] = set()

    for num in reply_numbers:
        if num not in tool_numbers and num not in allowed_set:
            unsupported_set.add(num)

    if unsupported_set:
        return FaithfulnessResult(ok=False, unsupported=tuple(sorted(unsupported_set)))
    return FaithfulnessResult(ok=True, unsupported=())


def is_suspicious_passage(text: str) -> bool:
    return bool(_SUSPICIOUS_RE.search(text))


def with_disclaimer(text: str) -> str:
    cleaned = text.rstrip()
    if cleaned.endswith(DISCLAIMER):
        return cleaned
    return cleaned + "\n\n" + DISCLAIMER
