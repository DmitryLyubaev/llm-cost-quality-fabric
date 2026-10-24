"""The capstone's scorer: an answer's text against its expected answer, by rule, with no AI judge.

`score(answer_text, expected, kind)` takes `expected` as `compute_expected` returns it
(`{"value", "currency", "rows"}`; ignored for `unanswerable`) and one of five kinds:

- **money**: a figure within 1% or 0.01 (whichever is wider) of the expected value. When any
  figure states a currency, only figures that state one are read as money; a stated currency
  other than the expected one makes the answer wrong (costs are never converted).
- **count**: a plain number (no currency, no percent) exactly equal to the expected count.
- **rate**: a percent (read as a fraction) or a plain number from 0 to 1, within 0.005.
- **labels**: the set of labels found in the text equals the expected set, case-insensitively.
  The text is searched for the expected labels and for any label of the same shape (digits
  generalised, so `p5` is found next to an expected `p4`); a label must stand alone, so the `p3`
  in `p3-agent` is not one. Single-letter labels would match prose and are not supported.
- **unanswerable**: passes only when the text holds a refusal or caveat phrase from
  `REFUSAL_PHRASES` (case-insensitive substring).

For money, count and rate, an answer with no number of the scored kind is wrong (`unreadable`),
and one with more than `SHOTGUN_LIMIT` such numbers is wrong (`shotgun`) even if one matches.

`parse_numbers(text)` is the documented parser. It first removes what is not a quantity: ISO
dates and times (`2026-10-25`, `2026-10`, `2026-10-09T14:05:00Z`, `14:05`), `10/25/2026`,
month-name dates (`October 2026`, `25 October 2026`, `Oct 9, 2026`), `project 3` and `top 1`.
It then reads each number that does not touch a letter, digit, `.` or `-` before it (so `p3`,
`E3`, `gpt-5.6-luna` and `top-1` hold none) and no letter or digit after it (so `3rd` is not one):

- digits with `,` thousands separators and a `.` decimal (`1,234.56`, `.75`), with a `-` sign
  before the digits or before the currency (`-0.25`, `-A$3`, `A$-3`);
- an attached `k`/`K` (thousand) or `M` (million) suffix (`1.2k`);
- a following `%` or `percent`, which sets `percent` (the value stays 98.3, not 0.983);
- a currency before (`A$`, `AU$`, `AUD`, `US$`, `USD`, `$`, `EUR`, `€`, `GBP`, `£`) or after
  (`AUD`, `USD`, `EUR`, `GBP`). `A$`, `AU$` and `AUD` are AUD; `US$` and `USD` are USD; a bare `$`
  names no currency (None), and a code after it names it (`$3.50 AUD` is AUD).

Number words (`six`) are not read.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

KINDS = ("money", "count", "rate", "labels", "unanswerable")
MONEY_RELATIVE = 0.01  # 1% of the expected value...
MONEY_ABSOLUTE = 0.01  # ...or 0.01 of the billing currency, whichever is wider
RATE_ABSOLUTE = 0.005
SHOTGUN_LIMIT = 3  # more numbers of the scored kind than this is a shotgun answer
_EPSILON = 1e-9  # float slack on the tolerances, so 0.52 - 0.51 counts as 0.01

# Lowercase, matched as substrings after curly apostrophes are straightened. Fixed: changing
# this list changes the rules, and the freeze hashes this file.
REFUSAL_PHRASES = (
    "not in the data",
    "isn't in the data",
    "not available in the data",
    "no data on",
    "no data about",
    "can't",
    "cannot",
    "can not",
    "unable to",
    "not possible to",
    "no version",
    "only one version",
    "one version per",
    "a single version",
    "single model version",
    "didn't change",
    "did not change",
    "does not record",
    "doesn't record",
    "not recorded",
    "isn't recorded",
    "no record of",
    "not tracked",
    "doesn't track",
    "does not track",
)


@dataclass(frozen=True)
class Number:
    """A number read from an answer: its value, whether it carried a percent sign, its currency."""

    value: float
    percent: bool
    currency: str | None


@dataclass(frozen=True)
class Verdict:
    """Right or wrong, and why: the reason starts with correct, mismatch, unreadable, shotgun,
    currency or refusal."""

    correct: bool
    reason: str


_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_MONTH_NOT_MAY = _MONTH.replace("|may|", "|")  # "it may 2" is not a date, so May needs its year
_ORDINAL = r"\d{1,2}(?:st|nd|rd|th)?"
_NOT_QUANTITIES = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"),
    re.compile(r"\b\d{4}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
    # day-first before month-first, so "25 October 2026" goes whole rather than leaving the 25
    re.compile(rf"\b{_ORDINAL}\s+{_MONTH}\b\.?,?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b{_MONTH}\.?\s+(?:{_ORDINAL},?\s+)?\d{{4}}\b", re.I),
    re.compile(rf"\b{_ORDINAL}\s+{_MONTH_NOT_MAY}\b", re.I),
    re.compile(rf"\b{_MONTH_NOT_MAY}\.?\s+{_ORDINAL}\b", re.I),
    re.compile(r"\bprojects?\s+\d+\b", re.I),
    re.compile(r"\btop[-\s]?\d+\b", re.I),
]
_NUMBER = re.compile(
    r"""
    (?<![\w.\-])
    (?P<lead>-)?
    (?:(?P<pre>A\$|AU\$|US\$|\$|€|£|(?:AUD|USD|EUR|GBP)(?=\s?[-.\d]))\s?)?
    (?P<sign>-)?
    (?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    (?P<suffix>[kKM](?![A-Za-z]))?
    (?!\w)
    (?:\s?(?P<pct>%|(?i:percent)\b))?
    (?:\s?(?P<post>AUD|USD|EUR|GBP)\b)?
    """,
    re.VERBOSE,
)
_CURRENCY = {
    "A$": "AUD", "AU$": "AUD", "AUD": "AUD",
    "US$": "USD", "USD": "USD",
    "€": "EUR", "EUR": "EUR",
    "£": "GBP", "GBP": "GBP",
    "$": None,
}  # fmt: skip
_MULTIPLIER = {None: Decimal(1), "k": Decimal(1000), "K": Decimal(1000), "M": Decimal(1_000_000)}


def _normalise(text: str) -> str:
    return (
        text.replace(" ", " ")
        .replace(" ", " ")
        .replace(" ", " ")
        .replace("−", "-")
        .replace("‘", "'")
        .replace("’", "'")
    )


def parse_numbers(text: str) -> list[Number]:
    """Every quantity in `text`, in order (see the module docstring for what is read)."""
    text = _normalise(text or "")
    for pattern in _NOT_QUANTITIES:
        text = pattern.sub(" ", text)
    numbers = []
    for match in _NUMBER.finditer(text):
        value = Decimal(match["digits"].replace(",", "")) * _MULTIPLIER[match["suffix"]]
        if match["lead"] or match["sign"]:
            value = -value
        pre = _CURRENCY.get(match["pre"]) if match["pre"] else None
        post = _CURRENCY.get(match["post"]) if match["post"] else None
        numbers.append(Number(float(value), match["pct"] is not None, pre or post))
    return numbers


def _wrong(reason: str) -> Verdict:
    return Verdict(False, reason)


def _expected_number(expected: dict | None, kind: str) -> float:
    value = expected.get("value") if isinstance(expected, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"a {kind} question needs a number as its expected value, got {value!r}")
    return value


def _too_many(candidates: list, kind: str) -> Verdict | None:
    if not candidates:
        return _wrong(f"unreadable: no {kind} figure in the answer")
    if len(candidates) > SHOTGUN_LIMIT:
        return _wrong(f"shotgun: {len(candidates)} {kind} figures (more than {SHOTGUN_LIMIT})")
    return None


def _score_money(numbers: list[Number], expected: dict) -> Verdict:
    want = _expected_number(expected, "money")
    currency = expected.get("currency")
    amounts = [n for n in numbers if not n.percent]
    stated = [n for n in amounts if n.currency is not None]
    candidates = stated or amounts
    refused = _too_many(candidates, "money")
    if refused:
        return refused
    others = sorted({n.currency for n in stated if currency is not None and n.currency != currency})
    if others:
        return _wrong(f"currency: the answer states {others}, the expected currency is {currency}")
    tolerance = max(MONEY_ABSOLUTE, MONEY_RELATIVE * abs(want)) + _EPSILON
    if any(abs(n.value - want) <= tolerance for n in candidates):
        return Verdict(True, f"correct: within {tolerance:.6g} of {want}")
    return _wrong(f"mismatch: {[n.value for n in candidates]} not within 1% or 0.01 of {want}")


def _score_count(numbers: list[Number], expected: dict) -> Verdict:
    want = _expected_number(expected, "count")
    candidates = [n for n in numbers if not n.percent and n.currency is None]
    refused = _too_many(candidates, "count")
    if refused:
        return refused
    if any(n.value == want for n in candidates):
        return Verdict(True, f"correct: exactly {want}")
    return _wrong(f"mismatch: {[n.value for n in candidates]} is not exactly {want}")


def _score_rate(numbers: list[Number], expected: dict) -> Verdict:
    want = _expected_number(expected, "rate")
    candidates = [
        n.value / 100 if n.percent else n.value
        for n in numbers
        if n.currency is None and (n.percent or 0 <= n.value <= 1)
    ]
    refused = _too_many(candidates, "rate")
    if refused:
        return refused
    if any(abs(c - want) <= RATE_ABSOLUTE + _EPSILON for c in candidates):
        return Verdict(True, f"correct: within {RATE_ABSOLUTE} of {want}")
    return _wrong(f"mismatch: {candidates} not within {RATE_ABSOLUTE} of {want}")


def _shape(label: str) -> str:
    """The label as a pattern with its digit runs generalised: `p4` -> `p\\d+`."""
    return "".join(r"\d+" if part.isdigit() else re.escape(part) for part in re.split(r"(\d+)", label) if part)


def _score_labels(text: str, expected: dict) -> Verdict:
    value = expected.get("value") if isinstance(expected, dict) else None
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"a labels question needs a non-empty list of labels as its expected value, got {value!r}")
    want = {v.lower() for v in value}
    shapes = sorted({_shape(v) for v in value}, key=len, reverse=True)
    pattern = re.compile(r"(?<![\w\-])(?:" + "|".join(shapes) + r")(?![\w\-])", re.I)
    found = {m.group(0).lower() for m in pattern.finditer(_normalise(text or ""))}
    if not found:
        return _wrong("unreadable: no label in the answer")
    if found == want:
        return Verdict(True, f"correct: {sorted(found)}")
    parts = []
    if want - found:
        parts.append(f"missing {sorted(want - found)}")
    if found - want:
        parts.append(f"extra {sorted(found - want)}")
    return _wrong("mismatch: " + ", ".join(parts))


def _score_unanswerable(text: str) -> Verdict:
    lowered = _normalise(text or "").lower()
    for phrase in REFUSAL_PHRASES:
        if phrase in lowered:
            return Verdict(True, f"correct: refusal or caveat ({phrase!r})")
    return _wrong("refusal: no refusal or caveat phrase in the answer")


def score(answer_text: str, expected: dict | None, kind: str) -> Verdict:
    """The verdict on `answer_text` for a question of `kind` (see the module docstring).

    ValueError for an unknown kind, or when a money, count or rate question's expected value is
    not a number, or a labels question's is not a non-empty list of labels.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {list(KINDS)}")
    if kind == "unanswerable":
        return _score_unanswerable(answer_text)
    if kind == "labels":
        return _score_labels(answer_text, expected)
    numbers = parse_numbers(answer_text)
    if kind == "money":
        return _score_money(numbers, expected)
    if kind == "count":
        return _score_count(numbers, expected)
    return _score_rate(numbers, expected)
