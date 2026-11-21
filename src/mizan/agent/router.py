from enum import StrEnum


class Intent(StrEnum):
    EDUCATION = "education"
    PORTFOLIO = "portfolio"
    ORDERS = "orders"
    OUT_OF_SCOPE = "out_of_scope"


_OUT_OF_SCOPE_SUBSTRINGS: tuple[str, ...] = (
    "should i buy",
    "should i sell",
    "best fund",
    "best stock",
    "which stock",
    "recommend",
    "guarantee",
    "will it go up",
    "هل أشتري",
    "هل اشتري",
    "انصحني",
)

_ORDERS_SUBSTRINGS: tuple[str, ...] = (
    "order",
    "buy",
    "sell",
    "rebalance",
    "draft",
    "confirm",
    "cancel",
    "اشتري",
    "بيع",
    "أوامر",
    "اوامر",
    "جهز",
    "اعادة توازن",
)

_PORTFOLIO_SUBSTRINGS: tuple[str, ...] = (
    "portfolio",
    "holding",
    "allocation",
    "gap",
    "drift",
    "balance",
    "my account",
    "محفظ",
    "توزيع",
    "رصيد",
    "فجوة",
)

_EDUCATION_SUBSTRINGS: tuple[str, ...] = (
    "what is",
    "what are",
    "explain",
    "how does",
    "how do",
    "why",
    "risk",
    "diversif",
    "equity",
    "stock",
    "bond",
    "fixed income",
    "gold",
    "horizon",
    "ما هو",
    "ما هي",
    "اشرح",
    "يعني",
    "مخاطر",
    "ذهب",
    "أسهم",
    "سندات",
)

# A concept question ("what is portfolio drift?") is education even when it contains portfolio
# words, unless it is about the user's own account. Found by the evals (education-004, -006).
_QUESTION_STARTS: tuple[str, ...] = (
    "what is",
    "what are",
    "explain",
    "how does",
    "how do",
    "why",
    "ما هو",
    "ما هي",
    "اشرح",
    "لماذا",
)

_PERSONAL_MARKERS: tuple[str, ...] = (
    " my ",
    " me ",
    "mine",
    "حسابي",
    "محفظتي",
    "رصيد",
    "فجوة",
)


def _is_concept_question(lowered: str) -> bool:
    padded = f" {lowered} "
    return lowered.startswith(_QUESTION_STARTS) and not any(m in padded for m in _PERSONAL_MARKERS)


def classify(message: str) -> Intent:
    lowered = message.lower()

    for pattern in _OUT_OF_SCOPE_SUBSTRINGS:
        if pattern in lowered:
            return Intent.OUT_OF_SCOPE

    for pattern in _ORDERS_SUBSTRINGS:
        if pattern in lowered:
            return Intent.ORDERS

    if _is_concept_question(lowered):
        return Intent.EDUCATION

    for pattern in _PORTFOLIO_SUBSTRINGS:
        if pattern in lowered:
            return Intent.PORTFOLIO

    for pattern in _EDUCATION_SUBSTRINGS:
        if pattern in lowered:
            return Intent.EDUCATION

    return Intent.OUT_OF_SCOPE
