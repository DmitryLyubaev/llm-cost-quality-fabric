"""Cost attribution (spec 3.6): which project does a cost line belong to.

Pure Python, loaded from the committed seeds. Applied in order; the first match wins:

1. a resource-group rule (exact name or glob, case-insensitive, optional date window) names a project;
2. a group the rules mark `shared` is split by the session window covering the cost date, and
   falls back to `releaselens-shared` on days with no session;
3. a `project` tag, compared case-insensitively;
4. everything else is `untagged`. Nothing is dropped.
"""

import json
from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatchcase

from lakehouse.seeding import read_seed_rows

UNTAGGED = "untagged"
SHARED_PROJECT = "releaselens-shared"
# Value of the `project` tag -> project. "releaselens" covers projects 1, 2 and 4-8, so it cannot
# name one of them; it only says the spend is ReleaseLens's and unsplit.
TAG_PROJECTS = {
    "releaselens": SHARED_PROJECT,
    "foundry-workshop-agent": "p3",
}

RULE_RG = "rg_rule"
RULE_SHARED_WINDOW = "shared_window"
RULE_SHARED_DEFAULT = "shared_default"
RULE_TAG = "tag"
RULE_UNTAGGED = "untagged"


@dataclass(frozen=True)
class RgRule:
    pattern: str
    project: str | None
    shared: bool
    valid_from: date | None
    valid_to: date | None
    evidence: str = ""

    def matches(self, resource_group: str, cost_date: date) -> bool:
        if not fnmatchcase(resource_group.casefold(), self.pattern.casefold()):
            return False
        if self.valid_from is not None and cost_date < self.valid_from:
            return False
        if self.valid_to is not None and cost_date > self.valid_to:
            return False
        return True


@dataclass(frozen=True)
class SessionWindow:
    project: str
    start_date: date
    end_date: date
    evidence: str = ""

    def covers(self, cost_date: date) -> bool:
        return self.start_date <= cost_date <= self.end_date


@dataclass(frozen=True)
class Attribution:
    project: str
    rule: str


def parse_tags(raw: str | None) -> dict[str, str]:
    """Tags as a lower-cased-key dict from the export's text.

    Accepts a JSON object, or the export's brace-less `"k": "v","k2": "v2"` form. Empty or
    None gives {}. Text that is neither raises ValueError, so a changed export format fails
    loudly instead of quietly sending spend to `untagged`.
    """
    if raw is None or not raw.strip():
        return {}
    text = raw.strip()
    if not text.startswith("{"):
        text = "{" + text + "}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"unreadable tags value: {raw!r}") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"tags are not a JSON object: {raw!r}")
    return {str(k).lower(): "" if v is None else str(v) for k, v in parsed.items()}


def attribute(
    resource_group: str | None,
    cost_date: date,
    tags: dict[str, str],
    rg_rules: list[RgRule],
    windows: list[SessionWindow],
) -> Attribution:
    """The project a cost line belongs to, and which rule step decided it."""
    if resource_group:
        rule = next((r for r in rg_rules if r.matches(resource_group, cost_date)), None)
        if rule is not None:
            if not rule.shared:
                return Attribution(rule.project, RULE_RG)
            window = next((w for w in windows if w.covers(cost_date)), None)
            if window is not None:
                return Attribution(window.project, RULE_SHARED_WINDOW)
            return Attribution(SHARED_PROJECT, RULE_SHARED_DEFAULT)

    tag = {str(k).lower(): v for k, v in (tags or {}).items()}.get("project")
    if tag is not None and tag.strip().lower() in TAG_PROJECTS:
        return Attribution(TAG_PROJECTS[tag.strip().lower()], RULE_TAG)
    return Attribution(UNTAGGED, RULE_UNTAGGED)


def _blank_to_none(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _date_or_none(value: str | None) -> date | None:
    value = _blank_to_none(value)
    return date.fromisoformat(value) if value else None


def load_rg_rules(seeds_dir: str, name: str = "rg_rules.csv") -> list[RgRule]:
    """Resource-group rules from the rg_rules seed, in file order (first match wins)."""
    rules = []
    for row in read_seed_rows(seeds_dir, name):
        shared = row["shared"].strip().lower() == "true"
        project = _blank_to_none(row["project"])
        if (project is None) == (not shared):
            raise ValueError(f"rule {row['pattern']!r} needs a project, or shared=true (not both)")
        rules.append(
            RgRule(
                pattern=row["pattern"].strip(),
                project=project,
                shared=shared,
                valid_from=_date_or_none(row["valid_from"]),
                valid_to=_date_or_none(row["valid_to"]),
                evidence=row["evidence"].strip(),
            )
        )
    return rules


def load_session_windows(seeds_dir: str, name: str = "session_windows.csv") -> list[SessionWindow]:
    """Session windows from the session_windows seed."""
    return [
        SessionWindow(
            project=row["project"].strip(),
            start_date=date.fromisoformat(row["start_date"].strip()),
            end_date=date.fromisoformat(row["end_date"].strip()),
            evidence=row["evidence"].strip(),
        )
        for row in read_seed_rows(seeds_dir, name)
    ]
