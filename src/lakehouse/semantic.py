"""The model-versus-gold check (spec 4.5): the semantic model's DAX answers equal gold's.

`QUERIES` holds one DAX query per gold answer, at that answer's own grain. Each is an `EVALUATE
SELECTCOLUMNS(SUMMARIZECOLUMNS(...))`: SUMMARIZECOLUMNS cannot rename a group-by column, and the
quality grain groups by two columns that share the name `model_version`, so the outer SELECTCOLUMNS
gives every column its answer's name (`QUERY_COLUMNS`). The figures come from the model's measures;
where an answer has a column no measure mirrors (cost per result's unpriced count, its rate and
month), the query computes it in a query-scoped `DEFINE MEASURE`, which changes nothing in the model.

`check_model_matches_gold` runs each query through `evaluate(dax, user)`: as the owner (`user`
None, unfiltered, since workspace Admins are not filtered) every query must equal its gold answer,
and then, as each UPN in `security.user_project`, cost per project and quality across models must
show only that lead's projects, with the owner's figures. Its detail names projects, never a UPN.
`compare` is the pure comparison: counts exact, floats within 0.005, gold 0 against a DAX blank (or
a row SUMMARIZECOLUMNS left out) equal, and null against null equal.

Only `evaluate` is live (semantic link's `evaluate_dax`, wrapped by `fabric_entry.check_model`); the
rest runs offline.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

from pyspark.sql import SparkSession

from lakehouse import answers
from lakehouse.checks import CheckResult
from lakehouse.gateway.answers import GATEWAY_RESOURCE_GROUP, cost_per_gateway_call
from lakehouse.paths import LakePaths
from lakehouse.security import USER_PROJECT_LAYER, USER_PROJECT_TABLE

CHECK_NAME = "model_matches_gold"
TOLERANCE = 0.005  # money and rates (the plan's global constraint); counts are exact
AUD = "AUD"  # `Cost (AUD)` sums AUD lines only (ruling R6)
DUPLICATE_KEY = "(duplicate key)"
MAX_REPORTED = 40  # differences listed in the detail; the rest are counted

COST_PER_PROJECT = "cost_per_project"
QUALITY_ACROSS_MODELS = "quality_across_models"
COST_PER_RESULT = "cost_per_result"
COST_PER_GATEWAY_CALL = "cost_per_gateway_call"

# Compared as each lead, too. Not cost per result: its AUD figure converts at the export's USD-to-AUD
# rate, which `Estimated cost per result (AUD)` reads from `fact_daily_cost` with its filters removed,
# but row-level security still applies, so a lead's rate comes only from the cost lines they can see
# while gold's comes from every line. It is checked as the owner only (VERIFY.md). Not the gateway
# answer: it is per session, not per lead.
USER_ANSWERS = (COST_PER_PROJECT, QUALITY_ACROSS_MODELS)

INNER = "x_"  # SUMMARIZECOLUMNS's own names, so no `[name]` in the outer select can mean a measure
_SCORED = f'KEEPFILTERS(fact_eval_attempt[attempt_kind] = "{answers.SCORED}")'


def _query(groups, values, *, constants=(), keep=None, define=""):
    """`EVALUATE SELECTCOLUMNS(SUMMARIZECOLUMNS(...))` and its output column names.

    `groups` and `values` are (answer column, DAX) pairs: group-by columns, and the measures (or
    query measures) computed per group. `constants` are (answer column, DAX) pairs added in the
    outer select only. `keep` filters the summarised rows.
    """
    inner = [f"            {column}" for _, column in groups]
    inner += [f'            "{INNER}{name}", {expression}' for name, expression in values]
    table = "SUMMARIZECOLUMNS(\n" + ",\n".join(inner) + "\n        )"
    if keep:
        table = f"FILTER(\n        {table},\n        {keep}\n    )"
    outer = list(groups) + [(name, f"[{INNER}{name}]") for name, _ in values] + list(constants)
    pairs = ",\n".join(f'    "{name}", {expression}' for name, expression in outer)
    text = f"EVALUATE\nSELECTCOLUMNS(\n    {table},\n{pairs}\n)\n"
    if define:
        text = f"DEFINE\n{define.strip(chr(10))}\n{text}"
    return text, [name for name, _ in outer]


# --- cost per project: `Cost (AUD)` per project and billing currency ---------------------------

_COST_PER_PROJECT, _COST_PER_PROJECT_COLUMNS = _query(
    groups=[("project", "fact_daily_cost[project]"), ("billing_currency", "fact_daily_cost[billing_currency]")],
    values=[("total", "[Cost (AUD)]")],
)

# --- quality across models: per study, arm, model and model version ----------------------------
# Gold's model version is the deployment version's, falling back to the version the study recorded
# (`answers.quality_across_models`). The model has no calculated columns, so the query groups by both
# (`dim_model_deployment[model_version]` and `fact_eval_attempt[model_version]`) and `shape_rows`
# coalesces them, as gold does.

QUALITY_MEASURES = {
    "n": "Results (n)",
    "success_rate": "Success rate",
    "latency_ms": "Latency (ms)",
    "groundedness": "Groundedness (mean)",
    "citation_recall": "Citation recall",
    "citation_precision": "Citation precision",
    "must_contain_rate": "Must-contain rate",
    "top1_rate": "Top-1 rate",
    "mrr": "MRR",
    "tool_call_accuracy": "Tool-call accuracy",
    "task_adherence": "Task adherence",
    "intent_resolution": "Intent resolution",
}
DIM_VERSION, RECORDED_VERSION = "dim_model_version", "recorded_model_version"

_QUALITY, _QUALITY_COLUMNS = _query(
    groups=[
        ("study", "fact_eval_attempt[study]"),
        ("arm", "fact_eval_attempt[arm]"),
        ("model", "dim_model_deployment[model_name]"),
        (DIM_VERSION, "dim_model_deployment[model_version]"),
        (RECORDED_VERSION, "fact_eval_attempt[model_version]"),
    ],
    values=[(column, f"[{measure}]") for column, measure in QUALITY_MEASURES.items()],
)

# --- cost per result: per project, estimated and actual side by side -----------------------------
# Grouped by `dim_project[project]`, which filters both facts. The query measures mirror the parts of
# `answers.cost_per_result` that no model measure covers, with the same rules: the estimate is unknown
# while any result is unpriced, the month is the earliest scored start's, and the rate is the export's
# one USD-to-AUD rate for that month.

_COST_PER_RESULT_DEFINE = f"""
    MEASURE fact_eval_attempt[Check unpriced results] =
        CALCULATE(
            COUNTROWS(fact_eval_attempt),
            {_SCORED},
            KEEPFILTERS(ISBLANK(fact_eval_attempt[cost_usd])),
            KEEPFILTERS(
                NOT ISBLANK(fact_eval_attempt[cost_basis])
                    && fact_eval_attempt[cost_basis] <> "{answers.NO_MODEL_BASIS}"
            )
        )
    MEASURE fact_eval_attempt[Check estimated USD total] =
        IF(
            ISBLANK([Check unpriced results]),
            CALCULATE(SUM(fact_eval_attempt[cost_usd]), {_SCORED})
        )
    MEASURE fact_eval_attempt[Check FX month] =
        VAR firstStart = CALCULATE(MIN(fact_eval_attempt[started_at]), {_SCORED})
        RETURN IF(NOT ISBLANK(firstStart), FORMAT(firstStart, "yyyy-MM"))
    MEASURE fact_eval_attempt[Check actual currency] =
        IF(
            NOT ISEMPTY(
                CALCULATETABLE(fact_daily_cost, KEEPFILTERS(fact_daily_cost[billing_currency] = "{AUD}"))
            ),
            "{AUD}"
        )
    MEASURE fact_eval_attempt[Check USD to AUD] =
        VAR fxMonth = [Check FX month]
        VAR rateLines =
            CALCULATETABLE(
                fact_daily_cost,
                REMOVEFILTERS(fact_daily_cost),
                fact_daily_cost[billing_month] = fxMonth,
                fact_daily_cost[billing_currency] = "{AUD}",
                fact_daily_cost[pricing_currency] = "{answers.PRICING_CURRENCY}",
                NOT ISBLANK(fact_daily_cost[fx_pricing_to_billing])
            )
        VAR lowRate = MINX(rateLines, fact_daily_cost[fx_pricing_to_billing])
        VAR highRate = MAXX(rateLines, fact_daily_cost[fx_pricing_to_billing])
        RETURN
            IF(
                NOT ISBLANK([Check actual currency]) && NOT ISBLANK(lowRate) && lowRate = highRate,
                lowRate
            )
    MEASURE fact_eval_attempt[Check estimated in billing total] =
        VAR rate = [Check USD to AUD]
        VAR estimated = [Check estimated USD total]
        RETURN IF(NOT ISBLANK(rate) && NOT ISBLANK(estimated), estimated * rate)
"""

_COST_PER_RESULT, _COST_PER_RESULT_COLUMNS = _query(
    groups=[("project", "dim_project[project]")],
    values=[
        ("results", "[Results (n)]"),
        ("unpriced_results", "[Check unpriced results]"),
        ("estimated_usd_total", "[Check estimated USD total]"),
        ("estimated_usd_per_result", "[Estimated cost per result (USD)]"),
        ("actual_total", "[Cost (AUD)]"),
        ("actual_per_result", "DIVIDE([Cost (AUD)], [Results (n)])"),
        ("actual_currency", "[Check actual currency]"),
        ("fx_month", "[Check FX month]"),
        ("fx_usd_to_billing", "[Check USD to AUD]"),
        ("estimated_in_billing_total", "[Check estimated in billing total]"),
        ("estimated_in_billing_per_result", "[Estimated cost per result (AUD)]"),
    ],
    constants=[("estimated_currency", f'"{answers.ESTIMATE_CURRENCY}"')],
    keep=f"NOT ISBLANK([{INNER}results])",  # gold has a row only for a project with scored results
    define=_COST_PER_RESULT_DEFINE,
)

# --- cost per gateway call: per session and call date ------------------------------------------
# The currency and the day's gateway cost are the lines `Cost per 1,000 calls` reads, kept only when
# they are in one currency (gold then has one row per currency, which this grain cannot match, so a
# day billed in two currencies fails the check). Sessions are filtered in Python, never spliced in.

_GATEWAY_LINES = f"""
        VAR gatewayLines =
            CALCULATETABLE(
                fact_daily_cost,
                REMOVEFILTERS(dim_project),
                TREATAS(VALUES(fact_gateway_call[call_date]), fact_daily_cost[cost_date]),
                KEEPFILTERS(fact_daily_cost[resource_group] = "{GATEWAY_RESOURCE_GROUP}")
            )
        VAR currencies = DISTINCT(SELECTCOLUMNS(gatewayLines, "currency", fact_daily_cost[billing_currency]))"""

_GATEWAY_DEFINE = f"""
    MEASURE fact_gateway_call[Check gateway currency] ={_GATEWAY_LINES}
        RETURN IF(COUNTROWS(currencies) = 1, CONCATENATEX(currencies, [currency]))
    MEASURE fact_gateway_call[Check gateway cost] ={_GATEWAY_LINES}
        RETURN IF(COUNTROWS(currencies) = 1, SUMX(gatewayLines, fact_daily_cost[cost]))
"""

_GATEWAY, _GATEWAY_COLUMNS = _query(
    groups=[("session", "fact_gateway_call[session]"), ("call_date", "fact_gateway_call[call_date]")],
    values=[
        ("calls", "[Calls]"),
        ("refused", "[Refused by gateway]"),
        ("tokens_total", "[Tokens (total)]"),
        ("tokens_per_call", "[Tokens per call]"),
        ("billing_currency", "[Check gateway currency]"),
        ("apim_cost", "[Check gateway cost]"),
        ("cost_per_1000_calls", "[Cost per 1,000 calls]"),  # ruling R5: against 1000 x cost_per_call
    ],
    define=_GATEWAY_DEFINE,
)

QUERIES: dict[str, str] = {
    COST_PER_PROJECT: _COST_PER_PROJECT,
    QUALITY_ACROSS_MODELS: _QUALITY,
    COST_PER_RESULT: _COST_PER_RESULT,
    COST_PER_GATEWAY_CALL: _GATEWAY,
}
QUERY_COLUMNS: dict[str, list[str]] = {
    COST_PER_PROJECT: _COST_PER_PROJECT_COLUMNS,
    QUALITY_ACROSS_MODELS: _QUALITY_COLUMNS,
    COST_PER_RESULT: _COST_PER_RESULT_COLUMNS,
    COST_PER_GATEWAY_CALL: _GATEWAY_COLUMNS,
}
KEYS: dict[str, list[str]] = {
    COST_PER_PROJECT: ["project", "billing_currency"],
    QUALITY_ACROSS_MODELS: ["study", "arm", "model", "model_version"],
    COST_PER_RESULT: ["project", "actual_currency"],
    COST_PER_GATEWAY_CALL: ["session", "call_date", "billing_currency"],
}
# answer column -> the model measure it is compared with, for the failure detail
MEASURE_NAMES: dict[str, dict[str, str]] = {
    COST_PER_PROJECT: {"total": "Cost (AUD)"},
    QUALITY_ACROSS_MODELS: dict(QUALITY_MEASURES),
    COST_PER_RESULT: {
        "results": "Results (n)",
        "estimated_usd_per_result": "Estimated cost per result (USD)",
        "actual_total": "Cost (AUD)",
        "estimated_in_billing_per_result": "Estimated cost per result (AUD)",
    },
    COST_PER_GATEWAY_CALL: {
        "calls": "Calls",
        "refused": "Refused by gateway",
        "tokens_total": "Tokens (total)",
        "tokens_per_call": "Tokens per call",
        "cost_per_1000_calls": "Cost per 1,000 calls",
    },
}


def compared_columns(answer: str) -> list[str]:
    """The columns compared for `answer`: the query's, with the two quality versions as one."""
    columns = []
    for column in QUERY_COLUMNS[answer]:
        if column == DIM_VERSION:
            columns.append("model_version")
        elif column != RECORDED_VERSION:
            columns.append(column)
    return columns


# --- the comparison --------------------------------------------------------------------------


@dataclass(frozen=True)
class Mismatch:
    answer: str
    key: tuple
    column: str
    expected: object
    actual: object


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _same(expected, actual) -> bool:
    if _blank(expected) and _blank(actual):
        return True
    if _blank(actual):  # a DAX blank, a missing column or a missing row: equal only to gold 0
        return _number(expected) and expected == 0
    if _blank(expected):
        return False
    if _number(expected) and _number(actual):
        if isinstance(expected, int):  # a count: exact (3.0 from a pandas float column is 3)
            return actual == expected
        return abs(expected - actual) <= TOLERANCE + 1e-12  # the epsilon absorbs float noise
    return expected == actual


def _index(rows: list[dict], keys: list[str]) -> tuple[dict[tuple, dict], dict[tuple, int]]:
    index: dict[tuple, dict] = {}
    counts: dict[tuple, int] = {}
    for row in rows:
        key = tuple(None if _blank(row.get(k)) else row.get(k) for k in keys)
        counts[key] = counts.get(key, 0) + 1
        index.setdefault(key, row)
    return index, counts


def compare(answer: str, expected: list[dict], actual: list[dict], keys: list[str]) -> list[Mismatch]:
    """The differences between gold's rows (`expected`) and the model's (`actual`), matched on `keys`.

    Each non-key column is compared: an int in gold is a count and must match exactly; a float must be
    within 0.005; anything else must be equal. Gold 0 against a DAX blank, a missing column or a
    missing row is equal (SUMMARIZECOLUMNS drops a row whose measures are all blank), and so is null
    against null; gold null against a DAX value (0 included) differs. So a gold row the model lacks
    differs in each column that is neither blank nor 0, and a model row gold lacks in each column that
    is not blank. A key that occurs more than once on either side differs as `DUPLICATE_KEY`, with
    both counts.
    """
    expected_rows, expected_counts = _index(expected, keys)
    actual_rows, actual_counts = _index(actual, keys)
    columns: list[str] = []
    for row in [*expected, *actual]:
        columns.extend(c for c in row if c not in keys and c not in columns)
    found = []
    for key in [*expected_rows, *(k for k in actual_rows if k not in expected_rows)]:
        if expected_counts.get(key, 0) > 1 or actual_counts.get(key, 0) > 1:
            found.append(Mismatch(answer, key, DUPLICATE_KEY, expected_counts.get(key, 0), actual_counts.get(key, 0)))
        gold, model = expected_rows.get(key, {}), actual_rows.get(key, {})
        for column in columns:
            if not _same(gold.get(column), model.get(column)):
                found.append(Mismatch(answer, key, column, gold.get(column), model.get(column)))
    return found


# --- gold's side and the model's side, as comparable rows -------------------------------------


def _rows(frame) -> list[dict]:
    return [row.asDict() for row in frame.collect()]


def gold_rows(spark: SparkSession, paths: LakePaths, answer: str, sessions: list[str]) -> list[dict]:
    """Gold's answer as the rows `compare` takes, in `compared_columns(answer)`.

    The gateway answer gets one set of rows per session in `sessions`, keyed by `session`, with
    `cost_per_1000_calls` (1000 x `cost_per_call`, ruling R5) in place of `cost_per_call`.
    """
    if answer == COST_PER_GATEWAY_CALL:
        rows = []
        for session in sessions:
            for row in _rows(cost_per_gateway_call(spark, paths, session)):
                per_call = row.pop("cost_per_call")
                row["session"] = session
                row["cost_per_1000_calls"] = None if per_call is None else 1000 * per_call
                rows.append(row)
    else:
        rows = _rows(getattr(answers, answer)(spark, paths))
    return [{column: row[column] for column in compared_columns(answer)} for row in rows]


def shape_rows(answer: str, rows: list[dict], sessions: list[str] | None = None) -> list[dict]:
    """The model's rows (plain dicts named as `QUERY_COLUMNS`) as the rows `compare` takes.

    Quality's two versions become `model_version`, the deployment's unless it is blank, as gold
    coalesces them. Gateway rows of sessions outside `sessions` are dropped (None keeps all).
    """
    shaped = []
    for row in rows:
        if answer == COST_PER_GATEWAY_CALL and sessions is not None and row.get("session") not in sessions:
            continue
        values = dict(row)
        if answer == QUALITY_ACROSS_MODELS:
            dim, recorded = values.pop(DIM_VERSION, None), values.pop(RECORDED_VERSION, None)
            values["model_version"] = recorded if _blank(dim) else dim
        shaped.append({column: values.get(column) for column in compared_columns(answer)})
    return shaped


# --- the check -------------------------------------------------------------------------------


def _describe(mismatch: Mismatch, expected: str = "gold", actual: str = "model") -> str:
    """One difference: the answer, the measure (or column), the key and both values, each side
    named by `expected` and `actual`."""
    keys = KEYS[mismatch.answer]
    where = ", ".join(f"{k}={v}" for k, v in zip(keys, mismatch.key))
    measure = MEASURE_NAMES.get(mismatch.answer, {}).get(mismatch.column)
    if mismatch.column == DUPLICATE_KEY:
        return (
            f"{mismatch.answer} has a duplicate key at {where}: "
            f"{expected} x{mismatch.expected}, {actual} x{mismatch.actual}"
        )
    what = f"measure '{measure}' ({mismatch.column})" if measure else f"column {mismatch.column}"
    return f"{mismatch.answer} {what} at {where}: {expected} {mismatch.expected!r}, {actual} {mismatch.actual!r}"


def _other_currencies(rows: list[dict]) -> list[str]:
    """Ruling R6: `Cost (AUD)` sums AUD only, so a gold row in another currency fails, by name."""
    return [
        f"{COST_PER_PROJECT} row project={r['project']}, billing_currency={r['billing_currency']} is billed in "
        f"{r['billing_currency']}, and measure 'Cost (AUD)' sums AUD only (ruling R6)"
        for r in rows
        if r["billing_currency"] not in (AUD, None)
    ]


def _leads(spark: SparkSession, paths: LakePaths) -> list[tuple[str, str, set[str]]]:
    """(label, UPN, projects) per user in the access table. The label names the projects only."""
    table = spark.read.format("delta").load(paths.table(USER_PROJECT_LAYER, USER_PROJECT_TABLE))
    projects: dict[str, set[str]] = {}
    for row in table.collect():
        projects.setdefault(row["upn"], set()).add(row["project"])
    ordered = sorted(projects.items(), key=lambda item: (sorted(item[1]), item[0]))
    leads, used = [], {}
    for upn, theirs in ordered:
        label = "the lead of " + ", ".join(sorted(theirs))
        used[label] = used.get(label, 0) + 1
        leads.append((label if used[label] == 1 else f"{label} ({used[label]})", upn, theirs))
    return leads


def _study_projects(spark: SparkSession, paths: LakePaths) -> dict[str, str]:
    dim = spark.read.format("delta").load(paths.table("gold", "dim_study"))
    return {row["study"]: row["project"] for row in dim.select("study", "project").distinct().collect()}


def check_model_matches_gold(
    spark: SparkSession,
    paths: LakePaths,
    evaluate: Callable[[str, str | None], list[dict]],
    sessions: list[str],
) -> CheckResult:
    """Every query, as the owner, equals its gold answer; as each lead, only their projects show.

    `evaluate(dax, user)` runs a query as the owner when `user` is None, else as that UPN, and
    returns plain dicts named as `QUERY_COLUMNS`. With no `sessions` the gateway answer is skipped,
    and the detail says so. As each user in `security.user_project`, cost per project and quality
    across models (`USER_ANSWERS`) may show only that user's projects, with the owner's figures for
    them. An empty access table fails: no lead could be checked. Leads are named by their projects;
    no UPN, and no error text (which may hold one), ever reaches the detail.
    """
    problems: list[str] = []
    notes: list[str] = []
    owner: dict[str, list[dict]] = {}
    for answer, dax in QUERIES.items():
        if answer == COST_PER_GATEWAY_CALL and not sessions:
            notes.append(f"{COST_PER_GATEWAY_CALL} skipped: no sessions given")
            continue
        expected = gold_rows(spark, paths, answer, sessions)
        if answer == COST_PER_PROJECT:
            problems.extend(_other_currencies(expected))
        owner[answer] = shape_rows(answer, evaluate(dax, None), sessions)
        problems.extend(_describe(m) for m in compare(answer, expected, owner[answer], KEYS[answer]))

    leads = _leads(spark, paths)
    if not leads:
        problems.append("security.user_project holds no users, so no lead was checked")
    study_projects = _study_projects(spark, paths)

    def project_of(answer: str, row: dict) -> str | None:
        return study_projects.get(row["study"]) if answer == QUALITY_ACROSS_MODELS else row["project"]

    for label, upn, projects in leads:
        for answer in USER_ANSWERS:
            try:
                rows = shape_rows(answer, evaluate(QUERIES[answer], upn))
            except Exception as error:  # its message may name the UPN, so only the type is kept
                problems.append(f"{label}: the {answer} query failed ({type(error).__name__})")
                continue
            foreign = sorted({project_of(answer, r) or "(no project)" for r in rows} - projects)
            if foreign:
                problems.append(f"{label}: {answer} shows {', '.join(foreign)}, outside their projects")
            own = [r for r in owner[answer] if project_of(answer, r) in projects]
            visible = [r for r in rows if project_of(answer, r) in projects]
            differences = compare(answer, own, visible, KEYS[answer])
            problems.extend(f"{label}: {_describe(m, 'owner', 'lead')}" for m in differences)

    if problems:
        listed = problems[:MAX_REPORTED]
        if len(problems) > MAX_REPORTED:
            listed.append(f"and {len(problems) - MAX_REPORTED} more")
        detail = f"{len(problems)} difference(s): " + "; ".join(listed + notes)
        return CheckResult(CHECK_NAME, False, detail)
    checked = ", ".join(owner)
    who = "; ".join(label for label, _, _ in leads)
    detail = f"the model matches gold as the owner ({checked}) and as {len(leads)} lead(s) ({who})"
    return CheckResult(CHECK_NAME, True, "; ".join([detail, *notes]))
