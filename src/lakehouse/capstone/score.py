"""The capstone's scorer: an answer's text against its expected answer, by rule, with no AI judge.

`score(answer_text, expected, kind, verdict=None)` takes `expected` as `compute_expected` returns
it (`{"value", "currency", "rows"}`; ignored for `unanswerable`) and one of five kinds.
`score_question(answer_text, expected, question)` does the same from a question dict, passing a
count question's selected `verdict` along.

- **money**: a figure within 1% or 0.01 (whichever is wider) of the expected value. Once any
  figure carries a currency marker (a bare `$` included), plain numbers (days, counts) are not
  money. Currency is then judged per figure: a figure stated in another currency is ignored
  (neither a match nor counted toward the shotgun limit). If every figure is in another
  currency, the answer is wrong.
- **count**: a plain number (no currency marker, no percent) exactly equal to the expected count.
  With a `verdict` (`fail`, `pass` or `not-run`) and several counts in an answer that uses verdict
  words, each count belongs to its nearest verdict word; the count nearest to a word for the
  asked verdict must match. Several counts and no word for the asked verdict is wrong.
- **rate**: if any figure is a percent, only percents are read (as fractions). Otherwise only
  decimal fractions from 0 to 1 are read (`0.5`, `1.0`), never a bare whole number such as `1`.
  A figure within 0.005 passes.
- **labels**: the set of labels found in the text equals the expected set, case-insensitively.
  The text is searched for the expected labels and for any label of the same shape (digits
  generalised, so `p5` is found next to an expected `p4`); a label must stand alone, so the `p3`
  in `p3-agent` is not one. When the labels are project IDs (`p4`), `project 4` and
  `projects 4 and 6` read as `p4` and `p6`. A label is not present when it is negated: when it is
  in the list straight after a cue from `NEGATING_CUES` (`except p5 and p6`; the list is labels
  joined by commas, `and`, `or`, `nor` or `&`, and ends at the first other word), or straight
  before `had none`, `has no ...` and the like. A cue from `AMBIGUOUS_CUES` (`besides`,
  `apart from`) before a label may mean "in addition to" or "except", so the answer is
  `unreadable` (wrong), never guessed; except that `apart from` after `all`, `every` or `each` in
  the same clause (`every project apart from p5`) negates its list like `except`. Single-letter labels would match prose and are not
  supported.
- **unanswerable**: passes only if all three hold: the text has a refusal or caveat (a phrase from
  `REFUSAL_PHRASES`; a verb from `REFUSAL_VERBS` followed, at most two words later, by an object
  from `REFUSAL_OBJECTS`, as in `can't tell` or `cannot be determined`; or `didn't change` not
  followed by `much`, `a lot` and the like); it states no figure at all (`parse_numbers` is
  empty: dates and `version 2` are not figures; ruling R5, so a refusal that states any figure,
  such as "all 60 runs used one version, so I can't compare", stays wrong); and it uses no word
  from `CHANGE_WORDS` anywhere. `if` and `whether` excuse only an either/or pair from
  `EITHER_OR_PAIRS` after them in the same clause ("I can't tell whether it went up or down"); a
  lone change word stays a claim ("if you ask me, it rose"). The `down` of `break down` and the
  `up` of `set up`, `look up`, `make up`, `sum up` and `add up` are not directions.

For money, count and rate, an answer with no figure of the scored kind is wrong (`unreadable`), as
is one whose scored figure is a range; one with more than `SHOTGUN_LIMIT` such figures is wrong
(`shotgun`) even if one matches.

`parse_numbers(text)` is the documented parser. It first blanks out what is not a quantity: ISO
dates and times (`2026-10-25`, `2026-10`, `2026-10-09T14:05:00Z`, `14:05`), `10/25/2026`,
month-name dates with a year (`October 2026`, `25 October 2026`, `Oct 9, 2026`) or day-first
without one (`25 October`; not `October 6`, which may be a month and a count), `project 3`,
`top 1` and `version 2`. It then reads each number that does not touch a letter, digit, `.` or `-` before it (so
`p3`, `E3`, `gpt-5.6-luna` and `top-1` hold none) and no letter or digit after it (so `3rd` and
`3ms` are not numbers). Digits are read atomically: a failed suffix never backtracks into them.

- digits with `,` thousands separators and a `.` decimal (`1,234.56`, `.75`); after a currency
  marker, spaces (plain, no-break or narrow no-break) also separate thousands (`A$ 1 234`);
  a `-` sign before the digits or before the currency (`-0.25`, `-A$3`, `A$-3`);
- an attached `k`/`K` (thousand) or `m`/`M` (million) suffix (`1.2k`, `A$1.2m`), or `x` (`1.5x`
  is 1.5);
- a following `%`, `percent` or `per cent`, which sets `percent` (the value stays 98.3);
- a currency before (`A$`, `AU$`, `AUD`, `US$`, `USD`, `$`, `EUR`, `€`, `GBP`, `£`) or after
  (`AUD`, `USD`, `EUR`, `GBP`), which sets `marked`. `A$`, `AU$` and `AUD` are AUD; `US$` and
  `USD` are USD; a bare `$` names no currency (None), and a code after it names it;
- `decimal` is set when the digits hold a `.`;
- a range is one Number, the first end, with `range` set. Only three forms are ranges: two
  figures joined by a hyphen or en dash with no space (`5-7`, `A$3.40–A$3.60`), `X to Y`, and
  `between X and Y` (or `between X-Y`). An em dash, or a hyphen or en dash with spaces on both
  sides, separates two figures (`98.3% — 59 of 60`, `A$3.50 - 12 days`).

Number words (`six`) are not read.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

KINDS = ("money", "count", "rate", "labels", "unanswerable")
MONEY_RELATIVE = 0.01  # 1% of the expected value...
MONEY_ABSOLUTE = 0.01  # ...or 0.01 of the billing currency, whichever is wider
RATE_ABSOLUTE = 0.005
SHOTGUN_LIMIT = 3  # more figures of the scored kind than this is a shotgun answer
_EPSILON = 1e-9  # float slack on the tolerances, so 0.52 - 0.51 counts as 0.01

# The refusal rules. Lowercase and fixed: changing them changes the rules, and the freeze hashes
# this file. Curly apostrophes are straightened before matching.
REFUSAL_PHRASES = (  # caveats that say the data cannot answer, matched as substrings
    "not in the data",
    "isn't in the data",
    "not available in the data",
    "no data on",
    "no data about",
    "no version",
    "only one version",
    "one version per",
    "a single version",
    "single model version",
    "does not record",
    "doesn't record",
    "not recorded",
    "isn't recorded",
    "no record of",
    "not tracked",
    "doesn't track",
    "does not track",
)
REFUSAL_VERBS = ("can't", "cannot", "can not", "unable to", "not able to", "not possible to", "impossible to")
REFUSAL_OBJECTS = (  # each word may take an ending: "determine" also matches "determined"
    "answer",
    "tell",
    "told",
    "determine",
    "say",
    "said",
    "break down",
    "compare",
    "calculate",
    "compute",
    "measure",
    "show",
    "know",
    "identify",
    "work out",
)
NO_CHANGE = ("didn't change", "did not change")
NO_CHANGE_UNLESS = ("much", "a lot", "significantly", "greatly", "substantially", "that much", "very much", "by")
CHANGE_WORDS = (  # direction and comparison words, matched as whole words anywhere in the answer
    "better", "worse", "improved", "improvement", "worsened", "rose", "risen", "fell", "fallen",
    "increased", "decreased", "dropped", "higher", "lower", "up", "down", "grew", "grown",
    "declined", "gained", "jumped", "climbed",
)  # fmt: skip
# An either/or pair after "whether" or "if" in the same clause asks, it does not claim: "I can't
# tell whether it went up or down". A lone change word stays a claim, wherever it is.
EITHER_OR_PAIRS = (("up", "down"), ("better", "worse"), ("rose", "fell"), ("increased", "decreased"), ("higher", "lower"))
VERDICT_WORDS = {
    "fail": r"fail(?:s|ed|ing|ures?)?",
    "pass": r"pass(?:es|ed|ing)?",
    "not-run": r"not[\s-]run|(?:didn't|did\s+not|wasn't|was\s+not|weren't|were\s+not)\s+run|skipped",
}


@dataclass(frozen=True)
class Number:
    """A number read from an answer.

    `value` (a percent stays 98.3), `percent` (it carried `%`), `currency` (AUD, USD, EUR, GBP or
    None), `marked` (it carried a currency marker, a bare `$` included), `decimal` (its digits
    held a `.`) and `range` (it is the first end of a range such as `5-7`).
    """

    value: float
    percent: bool
    currency: str | None
    marked: bool = False
    decimal: bool = False
    range: bool = False


@dataclass(frozen=True)
class Verdict:
    """Right or wrong, and why: the reason starts with correct, mismatch, unreadable, shotgun,
    currency, ambiguous or refusal."""

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
    re.compile(r"\bprojects?\s+\d+\b", re.I),
    re.compile(r"\btop[-\s]?\d+\b", re.I),
    re.compile(r"\bversions?\s+v?\d+(?:\.\d+)*\b", re.I),
]
_PRE = r"A\$|AU\$|US\$|\$|€|£|(?:AUD|USD|EUR|GBP)(?=\s?[-.\d])"
_CODE = r"AUD|USD|EUR|GBP"
# The second end of a range: after "and" or "to" (spaces allowed), or straight after a hyphen or
# en dash (no space). An em dash, or a dash with spaces, separates two figures instead.
_FIG_TIGHT = rf"(?:{_PRE})?-?(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?:[kKmM](?![A-Za-z]))?(?:\s?%|\s?(?i:per\s?cent)\b)?(?:\s?(?:{_CODE})\b)?"
_FIG = rf"(?:(?:{_PRE})\s?)?{_FIG_TIGHT}"
_NUMBER = re.compile(
    rf"""
    (?P<between>(?i:\bbetween)\s+)?
    (?<![\w.\-])
    (?P<lead>-)?
    (?:(?P<pre>{_PRE})\s?)?
    (?P<sign>-)?
    (?P<digits>(?>
        (?(pre)\d{{1,3}}(?:[ ]\d{{3}})+(?:\.\d+)?|(?!))
        |\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?
        |\d+(?:\.\d+)?
        |\.\d+
    ))
    (?P<suffix>[kKmMx×](?![A-Za-z]))?
    (?:\s?(?P<pct>%|(?i:per\s?cent)\b)|(?!\w))
    (?:\s?(?P<post>{_CODE})\b)?
    (?P<range>
        [-–]{_FIG_TIGHT}
        |\s+(?i:to)\s+{_FIG}
        |(?(between)\s+(?i:and)\s+{_FIG}|(?!))
    )?
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
_MULTIPLIER = {"k": 1000, "K": 1000, "m": 1_000_000, "M": 1_000_000}


def _normalise(text: str) -> str:
    return (
        (text or "")
        .replace(" ", " ")
        .replace(" ", " ")
        .replace(" ", " ")
        .replace("−", "-")
        .replace("‘", "'")
        .replace("’", "'")
    )


def _scan(text: str) -> list[tuple[Number, int, int]]:
    """Every quantity in the normalised `text` with its span (blanked text keeps its length)."""
    for pattern in _NOT_QUANTITIES:
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    found = []
    for match in _NUMBER.finditer(text):
        digits = match["digits"].replace(",", "").replace(" ", "")
        value = Decimal(digits) * _MULTIPLIER.get(match["suffix"], 1)
        if match["lead"] or match["sign"]:
            value = -value
        pre, post = match["pre"], match["post"]
        number = Number(
            float(value),
            match["pct"] is not None,
            (_CURRENCY[pre] if pre else None) or (_CURRENCY[post] if post else None),
            marked=bool(pre or post),
            decimal="." in digits,
            range=match["range"] is not None,
        )
        found.append((number, match.start(), match.end()))
    return found


def parse_numbers(text: str) -> list[Number]:
    """Every quantity in `text`, in order (see the module docstring for what is read)."""
    return [number for number, _, _ in _scan(_normalise(text))]


def _wrong(reason: str) -> Verdict:
    return Verdict(False, reason)


def _expected_number(expected: dict | None, kind: str) -> float:
    value = expected.get("value") if isinstance(expected, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"a {kind} question needs a number as its expected value, got {value!r}")
    return value


def _unscorable(candidates: list[Number], kind: str) -> Verdict | None:
    if not candidates:
        return _wrong(f"unreadable: no {kind} figure in the answer")
    if any(n.range for n in candidates):
        return _wrong(f"unreadable: a range is not a {kind}")
    if len(candidates) > SHOTGUN_LIMIT:
        return _wrong(f"shotgun: {len(candidates)} {kind} figures (more than {SHOTGUN_LIMIT})")
    return None


def _score_money(numbers: list[Number], expected: dict) -> Verdict:
    want = _expected_number(expected, "money")
    currency = expected.get("currency")
    amounts = [n for n in numbers if not n.percent]
    # once any figure carries a currency marker, plain numbers (days, counts) are not money
    amounts = [n for n in amounts if n.marked] or amounts
    candidates = [n for n in amounts if currency is None or n.currency in (None, currency)]
    if amounts and not candidates:
        return _wrong(f"currency: every figure is in {sorted({n.currency for n in amounts})}, not {currency}")
    refused = _unscorable(candidates, "money")
    if refused:
        return refused
    tolerance = max(MONEY_ABSOLUTE, MONEY_RELATIVE * abs(want)) + _EPSILON
    if any(abs(n.value - want) <= tolerance for n in candidates):
        return Verdict(True, f"correct: within {tolerance:.6g} of {want}")
    return _wrong(f"mismatch: {[n.value for n in candidates]} not within 1% or 0.01 of {want}")


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(b[0] - a[1], a[0] - b[1], 0)


def _score_count(scanned: list[tuple[Number, int, int]], text: str, expected: dict, verdict: str | None) -> Verdict:
    want = _expected_number(expected, "count")
    counts = [(n, (s, e)) for n, s, e in scanned if not n.percent and not n.marked]
    refused = _unscorable([n for n, _ in counts], "count")
    if refused:
        return refused
    if verdict is not None and len(counts) > 1:
        words = [
            (name, (m.start(), m.end()))
            for name, pattern in VERDICT_WORDS.items()
            for m in re.finditer(rf"\b(?:{pattern})\b", text, re.I)
        ]
        if words:
            asked = [span for name, span in words if name == verdict]
            if not asked:
                return _wrong(f"ambiguous: several counts and no word for {verdict!r}")
            # each count belongs to its nearest verdict word; of the asked verdict's counts, the
            # one nearest to its word is the answer
            mine = [(n, span) for n, span in counts if min(words, key=lambda w: _gap(span, w[1]))[0] == verdict]
            if not mine:
                return _wrong(f"mismatch: no count sits next to a word for {verdict!r}")
            chosen, _ = min(mine, key=lambda c: min(_gap(c[1], a) for a in asked))
            if chosen.value == want:
                return Verdict(True, f"correct: {want} next to {verdict!r}")
            return _wrong(f"mismatch: the count next to {verdict!r} is {chosen.value}, not {want}")
    if any(n.value == want for n, _ in counts):
        return Verdict(True, f"correct: exactly {want}")
    return _wrong(f"mismatch: {[n.value for n, _ in counts]} is not exactly {want}")


def _score_rate(numbers: list[Number], expected: dict) -> Verdict:
    want = _expected_number(expected, "rate")
    unmarked = [n for n in numbers if not n.marked]
    percents = [n for n in unmarked if n.percent]
    chosen = percents or [n for n in unmarked if n.decimal and 0 <= n.value <= 1]
    refused = _unscorable(chosen, "rate")
    if refused:
        return refused
    rates = [n.value / 100 if n.percent else n.value for n in chosen]
    if any(abs(r - want) <= RATE_ABSOLUTE + _EPSILON for r in rates):
        return Verdict(True, f"correct: within {RATE_ABSOLUTE} of {want}")
    return _wrong(f"mismatch: {rates} not within {RATE_ABSOLUTE} of {want}")


def _shape(label: str) -> str:
    """The label as a pattern with its digit runs generalised: `p4` -> `p\\d+`."""
    return "".join(r"\d+" if part.isdigit() else re.escape(part) for part in re.split(r"(\d+)", label) if part)


_PROJECT_NUMBERS = re.compile(r"\bprojects?\s+(\d+(?:(?:\s*,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+|\s*&\s*)\d+)*)\b", re.I)
# A cue negates the list of labels straight after it (labels joined by commas, "and", "or", "nor"
# or "&"); the list ends at the first word that is neither a label nor a connector.
NEGATING_CUES = ("except for", "except", "excluding", "other than", "but not", "not including", "not")
# "besides p4" and "apart from p4" may mean "in addition to p4" or "except p4": never guessed.
# The one exception: "apart from" after "all", "every" or "each" in the same clause ("every
# project apart from p5") is an exclusion and negates the list like "except".
AMBIGUOUS_CUES = ("besides", "apart from")
_ALL_BEFORE = re.compile(r"\b(?:all|every|each)\b", re.I)
_CONNECTOR = r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+|\s+nor\s+|\s*&\s*)"
_NONE_AFTER = re.compile(
    r"\s*(?:\([^)]*\)\s*)?(?:(?:had|has|have|showed|shows)\s+(?:none|nothing|zero|no\s+\w+)|-\s*none\b)", re.I
)


def _score_labels(text: str, expected: dict) -> Verdict:
    value = expected.get("value") if isinstance(expected, dict) else None
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"a labels question needs a non-empty list of labels as its expected value, got {value!r}")
    want = {v.lower() for v in value}
    text = _normalise(text)
    if any(re.fullmatch(r"p\d+", v) for v in want):
        text = _PROJECT_NUMBERS.sub(lambda m: re.sub(r"\d+", lambda d: f"p{d.group(0)}", m.group(1)), text)
    shapes = sorted({_shape(v) for v in value}, key=len, reverse=True)
    label = r"(?<![\w\-])(?:" + "|".join(shapes) + r")(?![\w\-])"
    label_list = rf"{label}(?:{_CONNECTOR}{label})*"
    negated = [
        m.span(1) for m in re.finditer(rf"\b(?:{_words(NEGATING_CUES)})\s+({label_list})", text, re.I)
    ]
    for cue in re.finditer(rf"\b(?:{_words(AMBIGUOUS_CUES)})\s+(?=({label_list}))", text, re.I):
        clause_start = max((e.end() for e in _CLAUSE_END.finditer(text, 0, cue.start())), default=0)
        if cue.group(0).lower().startswith("apart") and _ALL_BEFORE.search(text, clause_start, cue.start()):
            negated.append(cue.span(1))  # "every project apart from p5": an exception
            continue
        return _wrong(f"unreadable: ambiguous {cue.group(0).strip()!r} before a label")
    found = set()
    for m in re.finditer(label, text, re.I):
        if any(start <= m.start() < end for start, end in negated) or _NONE_AFTER.match(text, m.end()):
            continue
        found.add(m.group(0).lower())
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


def _words(words: tuple[str, ...], ending: bool = False) -> str:
    """An alternation of the phrases, each word optionally taking an ending."""
    tail = r"\w*" if ending else ""
    return "|".join(r"\s+".join(re.escape(w) + tail for w in phrase.split()) for phrase in words)


_REFUSAL = re.compile(rf"\b(?:{_words(REFUSAL_VERBS)})\s+(?:\w+\s+){{0,2}}?(?:{_words(REFUSAL_OBJECTS, True)})\b")
_NO_CHANGE = re.compile(rf"\b(?:{_words(NO_CHANGE)})\b(?!\s+(?:{_words(NO_CHANGE_UNLESS)})\b)")
_CHANGE = re.compile(rf"\b(?:{_words(CHANGE_WORDS)})\b")
# Phrasal verbs whose "up" or "down" is not a direction; blanked before CHANGE_WORDS are looked for.
_NOT_A_DIRECTION = re.compile(
    r"\b(?:break|breaks|broke|broken|breaking)\s+(?:\w+\s+)?down\b|\b(?:set|sets|look|looked|make|made|sum|add|adds)\s+up\b"
)


_EITHER_OR = re.compile(
    r"\b(?:" + "|".join(rf"{a}\s+or\s+{b}|{b}\s+or\s+{a}" for a, b in EITHER_OR_PAIRS) + r")\b"
)
_CLAUSE_END = re.compile(r"[.;:!?\n]")
_WHETHER = re.compile(r"\b(?:whether|if)\b")


def _blank_open_questions(lowered: str) -> str:
    """Blank an either/or pair ("up or down") that follows "whether" or "if" in the same clause."""

    def blank(m: re.Match) -> str:
        clause_start = max((e.end() for e in _CLAUSE_END.finditer(lowered, 0, m.start())), default=0)
        return " " * len(m.group(0)) if _WHETHER.search(lowered, clause_start, m.start()) else m.group(0)

    return _EITHER_OR.sub(blank, lowered)


def _score_unanswerable(text: str) -> Verdict:
    text = _normalise(text)
    figures = parse_numbers(text)
    if figures:  # ruling R5: a refusal that states any figure stays wrong
        return _wrong(f"refusal: the answer states {len(figures)} figure(s)")
    lowered = text.lower()
    change = _CHANGE.search(_blank_open_questions(_NOT_A_DIRECTION.sub(" ", lowered)))
    if change:
        return _wrong(f"refusal: the answer uses a direction or comparison word ({change.group(0)!r})")
    for phrase in REFUSAL_PHRASES:
        if phrase in lowered:
            return Verdict(True, f"correct: caveat ({phrase!r})")
    for rule in (_REFUSAL, _NO_CHANGE):
        m = rule.search(lowered)
        if m:
            return Verdict(True, f"correct: refusal ({m.group(0)!r})")
    return _wrong("refusal: no refusal or caveat in the answer")


def score(answer_text: str, expected: dict | None, kind: str, verdict: str | None = None) -> Verdict:
    """The verdict on `answer_text` for a question of `kind` (see the module docstring).

    `verdict` (`fail`, `pass` or `not-run`) is the live-check verdict a count question asks about.
    ValueError for an unknown kind or verdict, or when a money, count or rate question's expected
    value is not a number, or a labels question's is not a non-empty list of labels.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {list(KINDS)}")
    if verdict is not None and verdict not in VERDICT_WORDS:
        raise ValueError(f"unknown verdict {verdict!r}; expected one of {list(VERDICT_WORDS)}")
    if kind == "unanswerable":
        return _score_unanswerable(answer_text)
    if kind == "labels":
        return _score_labels(answer_text, expected)
    text = _normalise(answer_text)
    scanned = _scan(text)
    if kind == "count":
        return _score_count(scanned, text, expected, verdict)
    numbers = [n for n, _, _ in scanned]
    if kind == "money":
        return _score_money(numbers, expected)
    return _score_rate(numbers, expected)


def score_question(answer_text: str, expected: dict | None, question: dict) -> Verdict:
    """`score` for a question dict: its kind, and for a count its selector's `verdict`, if any."""
    kind = question["kind"]
    select = (question.get("source") or {}).get("select") or {}
    verdict = select.get("verdict") if kind == "count" else None
    return score(answer_text, expected, kind, verdict)
