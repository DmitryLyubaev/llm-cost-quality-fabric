"""Builds the synthetic Cost Management export under tests/fixtures/cost_export (committed).

Run `python tests/fixtures/make_cost_export_fixture.py` to regenerate it. Everything is
deterministic (fixed run ids, fixed submitted times, gzip mtime 0), so the files are reproducible.
`tests/test_costs.py` regenerates the tree and compares it with the committed one.

Layout, as Cost Management writes it: `<root>/<ExportName>/<YYYYMMDD-YYYYMMDD>/<RunID>/` holding
`manifest.json` and the part files. The manifest's `blobName` is deliberately NOT a path inside this
tree (the real one is container-relative), because the reader must resolve part files next to the
manifest.

  portfolio-daily/20261001-20261031/<RUN_A>  superseded October run: 12 rows (one stale), MOSA headers
  portfolio-daily/20261001-20261031/<RUN_B>  latest October run: 11 rows in two parts, MOSA headers
  portfolio-daily/20260901-20260930/<RUN_C>  September run: 4 rows, newer (camelCase) headers

Synthetic values only.
"""

import gzip
import json
import shutil
import sys
from pathlib import Path

EXPORT_NAME = "portfolio-daily"
OCT = "20261001-20261031"
SEP = "20260901-20260930"
RUN_A = "0a1b2c3d-0000-4000-8000-00000000000a"
RUN_B = "0a1b2c3d-0000-4000-8000-00000000000b"
RUN_C = "0a1b2c3d-0000-4000-8000-00000000000c"

SILVER_NAMES = (
    "cost_date", "resource_id", "resource_group", "meter_id", "meter_name", "cost",
    "billing_currency", "tags",
)

# Extra columns the real export carries that silver does not use.
_EXTRA = {"meter_category": "Cognitive Services", "quantity": "1"}

HEADERS: dict[str, dict[str, str]] = {
    # Pay-as-you-go (MOSA) 2019-11-01 names
    "mosa": {
        "cost_date": "UsageDateTime", "resource_id": "InstanceId", "resource_group": "ResourceGroup",
        "meter_id": "MeterId", "meter_name": "MeterName", "cost": "PreTaxCost",
        "billing_currency": "Currency", "tags": "Tags",
        "meter_category": "MeterCategory", "quantity": "UsageQuantity",
    },
    # newer EA/MCA-style names, camelCase as those exports write them
    "new": {
        "cost_date": "date", "resource_id": "resourceId", "resource_group": "resourceGroupName",
        "meter_id": "meterId", "meter_name": "meterName", "cost": "costInBillingCurrency",
        "billing_currency": "billingCurrencyCode", "tags": "tags",
        "meter_category": "meterCategory", "quantity": "quantity",
    },
}

_SUB = "/subscriptions/synthetic-sub/resourceGroups"


def _row(day, group, provider, meter_id, meter_name, cost, tags="", resource="synthetic-res", currency="AUD"):
    resource_id = f"{_SUB}/{group}/providers/{provider}/{resource}" if group else ""
    return {
        "cost_date": day, "resource_id": resource_id, "resource_group": group, "meter_id": meter_id,
        "meter_name": meter_name, "cost": cost, "billing_currency": currency, "tags": tags,
    }


# The 11 rows every load of the fixture lake reads for October.
OCTOBER_ROWS = [
    _row("2026-10-02", "rg-releaselens-bootstrap", "Microsoft.CognitiveServices/accounts", "meter-oai-in",
         "gpt-4.1-mini input tokens", "2.5", '{"project":"releaselens"}', "synthetic-openai"),
    _row("2026-10-03", "rg-releaselens-bootstrap", "Microsoft.CognitiveServices/accounts", "meter-emb",
         "text-embedding input tokens", "1.25", '"project": "ReleaseLens","purpose": "bootstrap"',
         "synthetic-openai"),
    _row("2026-10-25", "rg-releaselens-bootstrap", "Microsoft.Storage/storageAccounts", "meter-blob",
         "Hot LRS Data Stored", "0.04", resource="syntheticstate"),
    _row("2026-10-04", "rg-releaselens-search", "Microsoft.Search/searchServices", "meter-search",
         "Basic Unit", "0.5", resource="synthetic-search"),
    _row("2026-10-08", "rg-releaselens-gateway", "Microsoft.ApiManagement/service", "meter-apim",
         "Basic v2 Unit", "0.41", '{"project":"releaselens"}', "synthetic-apim"),
    _row("2026-10-12", "RG-FWA-eval", "Microsoft.CognitiveServices/accounts", "meter-gpt",
         "gpt output tokens", "3.5", '{"project":"foundry-workshop-agent"}', "synthetic-foundry"),
    _row("2026-10-14", "rg-releaselens-webapp", "Microsoft.Web/sites", "meter-web", "B1 App Service",
         "0.75", resource="synthetic-web"),
    _row("2026-10-16", "rg-p8-platform", "Microsoft.Storage/storageAccounts", "meter-p8",
         "Hot LRS Write Operations", "0.02", resource="syntheticp8"),
    _row("2026-10-18", "rg-unknown", "Microsoft.Web/sites", "meter-other", "Standard Plan", "1.1",
         '{"Project":"ReleaseLens"}', "synthetic-other"),
    _row("2026-10-19", "rg-unknown", "Microsoft.Compute/disks", "meter-disk", "P4 Disks", "0.33",
         resource="synthetic-disk"),
    _row("2026-10-20", "", "", "meter-support", "Support Plan", "0.07"),
]

# What the superseded run still lists and the latest no longer does.
STALE_ROW = _row("2026-10-21", "rg-releaselens-search", "Microsoft.Search/searchServices", "meter-stale",
                 "Stale Unit", "9.99", resource="synthetic-search")

SEPTEMBER_ROWS = [
    _row("2026-09-27", "rg-releaselens-bootstrap", "Microsoft.Storage/storageAccounts", "meter-blob",
         "Hot LRS Data Stored", "0.03", resource="syntheticstate"),
    _row("2026-09-28", "rg-releaselens-bootstrap", "Microsoft.CognitiveServices/accounts", "meter-oai-in",
         "gpt-4.1-mini input tokens", "1.75", '{"project":"releaselens"}', "synthetic-openai"),
    _row("2026-09-29", "rg-releaselens-gateway", "Microsoft.ApiManagement/service", "meter-apim",
         "Basic v2 Unit", "0.2", '{"project":"releaselens"}', "synthetic-apim"),
    _row("2026-09-30", "rg-unknown", "Microsoft.Compute/disks", "meter-disk", "P4 Disks", "0.11",
         resource="synthetic-disk"),
]


def csv_text(rows: list[dict[str, str]], style: str) -> str:
    """The rows as one CSV part (header included) under `style`'s header names, LF line ends."""
    header = HEADERS[style]
    columns = list(header)
    lines = [",".join(header[c] for c in columns)]
    for row in rows:
        full = {**_EXTRA, **row}
        lines.append(",".join(_quote(full[c]) for c in columns))
    return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    if any(ch in value for ch in ',"\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def write_run(
    root: Path,
    month_folder: str,
    run_id: str,
    submitted: str,
    parts: list[list[dict[str, str]]],
    *,
    style: str = "mosa",
    export_name: str = EXPORT_NAME,
    gzipped: bool = True,
    part_texts: list[str] | None = None,
) -> Path:
    """Write one export run (part files, then its manifest.json); return the run folder.

    `part_texts` replaces the generated part contents (for tests that need a damaged file); the
    manifest still counts the rows in `parts`.
    """
    folder = Path(root) / export_name / month_folder / run_id
    folder.mkdir(parents=True, exist_ok=True)
    start, end = month_folder.split("-")
    blobs = []
    for index, rows in enumerate(parts):
        name = f"part{index}.csv.gz" if gzipped else f"part{index}.csv"
        text = part_texts[index] if part_texts else csv_text(rows, style)
        payload = text.encode("utf-8")
        if gzipped:
            payload = gzip.compress(payload, mtime=0)  # mtime 0: the bytes do not depend on the clock
        (folder / name).write_bytes(payload)
        blobs.append(
            {
                # container-relative in a real manifest; its prefix is not our tree's layout
                "blobName": f"synthetic-prefix/{export_name}/{month_folder}/{run_id}/{name}",
                "byteCount": len(payload),
                "dataRowCount": len(rows),
            }
        )
    manifest = {
        "manifestVersion": "2024-04-01",
        "byteCount": sum(b["byteCount"] for b in blobs),
        "blobCount": len(blobs),
        "dataRowCount": sum(b["dataRowCount"] for b in blobs),
        "exportConfig": {
            "exportName": export_name,
            "resourceId": "/subscriptions/synthetic-sub",
            "dataVersion": "1.0",
            "apiVersion": "2023-07-01-preview",
            "type": "ActualCost",
            "timeFrame": "Custom",
            "granularity": "Daily",
        },
        "deliveryConfig": {
            "partitionData": True,
            "dataOverwriteBehavior": "OverwritePreviousReport",
            "fileFormat": "Csv",
            "compressionMode": "Gzip" if gzipped else "None",
            "containerUri": "synthetic-container",
            "rootFolderPath": "synthetic-prefix",
        },
        "runInfo": {
            "executionType": "OnDemand",
            "submittedTime": submitted,
            "runId": run_id,
            "startDate": f"{start[:4]}-{start[4:6]}-{start[6:]}T00:00:00",
            "endDate": f"{end[:4]}-{end[4:6]}-{end[6:]}T00:00:00",
        },
        "blobs": blobs,
    }
    (folder / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    return folder


def build(root: Path) -> None:
    """The committed fixture tree."""
    write_run(root, OCT, RUN_A, "2026-10-20T03:30:05.4210000Z", [[*OCTOBER_ROWS, STALE_ROW]])
    write_run(
        root, OCT, RUN_B, "2026-10-21T03:30:09.1230000Z", [OCTOBER_ROWS[:6], OCTOBER_ROWS[6:]]
    )
    write_run(root, SEP, RUN_C, "2026-10-01T04:01:00.0000000Z", [SEPTEMBER_ROWS], style="new")


def main(argv: list[str]) -> None:
    target = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent / "cost_export"
    if target.exists():
        shutil.rmtree(target)
    build(target)
    print(f"wrote {target}")


if __name__ == "__main__":
    main(sys.argv)
