# llm-cost-quality-fabric

What a small AI portfolio's LLM systems cost, and how their quality compared across models,
answered from a Microsoft Fabric lakehouse built over the portfolio's own records.

This is project 9 of an Azure portfolio. Projects 1–8 (in
[ReleaseLens](https://github.com/DmitryLyubaev/releaselens) and
[foundry-workshop-agent](https://github.com/DmitryLyubaev/foundry-workshop-agent)) left measured
studies, live-check reports, model deployments in Terraform's history, and real Azure spend.
This repo lands those records as they are, cleans them with PySpark and Delta `MERGE`, and models
them as a star schema that answers three questions:

1. What did each project cost, with unattributable spend shown rather than dropped?
2. How did quality compare across models (and, where the data allows, across model versions)?
3. What did a measured result cost?

**Small data, real platform.** About 1,800 result rows and a few hundred cost lines. The point is
the platform work (PySpark and Delta, dimensional modelling with a Type 2 dimension, Fabric
pipelines, git integration and CI/CD), done with every number measured, dated and reproducible.

## Status, 23 October 2026

| Part | State |
|---|---|
| Code, tests, Fabric item definitions, deploy workflow, Terraform | Built and reviewed; **313 tests** pass locally; CI green on GitHub |
| Cost export (Azure Cost Management → storage) | **Live** since 22 October; daily month-to-date plus a one-time September run |
| Local end-to-end run on the real inputs | All four quality checks passed twice, re-run identical (22 October) |
| Fabric workspaces, pipeline runs, dated answers | **Pending.** The Fabric trial was refused on 22 October for a tenant under 90 days old; retry on 8 November 2026 |

Every figure below that isn't measured yet says **pending**. Nothing here is aspirational.

## The answers

| Question | Answer | Measured |
|---|---|---|
| Cost per project (AUD, untagged shown) | pending | pending |
| Quality across models | pending | pending |
| Quality across model versions | **The data can't tell**: no model changed version between two measured runs ([why](#limits)) | 21 Oct 2026 (from the Terraform history) |
| Cost per measured result (estimated beside actual) | pending | pending |
| Fabric capacity used | pending | pending |

## Architecture

```
GitHub (public files, pinned SHAs) ─┐
Private result extracts (sanitised) ─┼─► Files/bronze/…  (as-is, per load date)
Cost Management export ──► storage ─┘   via OneLake shortcut, read through each run's manifest
                                      │
                         PySpark + Delta MERGE (safe to re-run)
                                      ▼
                         Tables/silver/…  (typed, deduplicated)
                                      ▼
                         Tables/gold/…    (star schema) + Tables/reject/…
                                      ▼
                         4 quality checks ── fail the pipeline on a broken claim
```

- **One schema-enabled lakehouse per workspace.** The medallion layers are schemas
  (`silver`, `gold`, `reject`); bronze lives in `Files/`.
- **One Python package** (`src/lakehouse`), unit-tested with pytest against a local Spark with
  Delta. It ships as a wheel to `Files/wheels/`. Each notebook installs it and calls one entry
  point, so the logic is tested outside Fabric.
- **A Fabric pipeline** (`pl_daily_load`): ingest, then load, with retries. It is parameterised
  by date, so a backfill is a single run.

## Layers and grains

| Table | Grain (one row per…) |
|---|---|
| `fact_eval_attempt` | study × item × arm × pass × repeat |
| `fact_daily_cost` | cost-export line: resource × meter × day |
| `fact_live_check` | check × session |
| `dim_model_deployment` | deployment version (Type 2, from Terraform's git history) |
| `dim_project` | project 1–9, plus `releaselens-shared` and `untagged` |
| `dim_study` | study run |
| `dim_arm` | arm within a study |
| `dim_date` | day |
| `reject.eval_unresolved` | eval attempt that resolves to no deployment version, or to several |

The Type 2 dimension joins every result to the deployment version live when it was measured. A
result that resolves to none, or to several, goes to the reject table and fails a check. It is
never dropped.

## Sources

| Source | Public? | Into the lakehouse |
|---|---|---|
| Project 3 agent study (120 runs + 9 repeats) | Public | Fetched by the pipeline, pinned to a commit |
| Project 2 questions (229) | Public | Fetched, pinned |
| Project 1 and 2 per-query scores | Private | Sanitised extract (no answer text, no identifiers), uploaded once |
| Projects 4–8 live checks (74 checks, every fail and not-run kept) | Public reports | Transcribed to a seed, each row citing its report and commit |
| Model deployment history | Public Terraform history | Seed rows citing their commits; one hand-entered capacity change, flagged |
| Azure spend | Private | Cost Management export, read through its manifest |
| Project 7 private originals | **Lost** | Not loaded; only its public summary is |

## Quality checks

Four checks, each guarding a claim this README makes:

| Check | Guards |
|---|---|
| Every result resolves to exactly one deployment version | The Type 2 join behind "quality across models" |
| Each month's cost total and row count match the export's manifest | "Nothing unattributable is dropped" |
| Gold reproduces the published figures (P1 study cost $2.7055, P3 59/60 and 57/60, P2 n = 229 per arm, P2 E3 − E1 top-1 +0.271) | That gold agrees with what the earlier reports claim |
| Re-running a load date leaves gold unchanged | "Running the same date twice changes nothing" |

Each check will be shown failing once on purpose in dev: **pending**.

## Deployment

- **Dev:** the dev workspace syncs from the `dev` branch through Fabric's git integration.
- **Prod:** deployed only by GitHub Actions, as a service principal signed in through OIDC
  (`azure/login` plus `fabric-cicd`), behind the owner's approval. The service principal holds no
  secret and no Azure role.
- **Infrastructure:** Terraform creates the workspaces and roles (`microsoft/fabric`) and the
  cost-export storage (`azurerm`).
- **`main` is protected:** pull requests only, with the `test` check required.

## Decisions worth knowing

- **GitHub Actions for prod, not Azure Pipelines.** Project 8 already showed Azure Pipelines,
  and this repo's CI lives on GitHub.
- **The wheel goes to `Files/wheels/`, not into a Fabric Environment.** Publishing an Environment
  with a custom wheel can take over 20 minutes and needs the binary in git.
- **Shortcut tracking is off in git.** A synced shortcut would commit the storage URL, and
  "Update from Git" deletes shortcuts it doesn't know. The deploy creates the shortcut from a secret.
- **The cost-export storage has a public endpoint, Entra-only.** Shared keys are off and access
  needs Entra sign-in, but the endpoint is public, because the trial can't reach a firewalled
  account.
- **Terraform doesn't assign the capacity.** The provider doesn't support trial capacity, so it
  is assigned once in the UI.

The full reasoning, including the options rejected, is in the
[design spec](docs/superpowers/specs/2026-10-21-fabric-lakehouse-design.md).

## What it costs

| Item | Cost |
|---|---|
| Cost-export storage | Cents a month (measured total: pending) |
| Fabric trial capacity | Free for 60 days; capacity used: pending |
| CI | GitHub-hosted runners, public repo |

## Limits

- **Costs start on 30 September 2026.** The subscription was a Free Trial until then, and Cost
  Management does not export costs from before the offer change. A one-time September export
  returned only 30 September.
- **No model changed version between measured runs.** `gpt-4.1-mini` stayed on `2025-04-14`
  (its changes were capacity), and the Foundry version changes all landed the day before that
  study. So the answers compare models, not versions.
- **Estimates and actuals are different things.** Each study's estimated cost is in USD at list
  price; actual spend is AUD from Cost Management and covers Azure only. They sit side by side
  and are never summed or converted with an invented rate.
- **Commit time stands in for apply time** in the Type 2 dimension, because git doesn't record
  applies. The one capacity change that isn't in git is hand-entered and flagged.
- **Small data.** Sample sizes are stated with every answer.

## What this doesn't do yet

- **Streaming** (gateway events through Event Hubs into an Eventhouse): project 10.
- **The semantic model, DAX measures, row-level security and the report:** project 11.

## Run the tests

Spark needs Linux with Java 21 and Python 3.13 (Fabric Runtime 2.0). On Windows, use WSL:

```bash
pip install -e ".[dev,spark]"
```

```bash
pytest -q
```

## Repo layout

| Path | What |
|---|---|
| `src/lakehouse/` | The package: extract, bronze, silver, gold, checks, answers, run, Fabric entry points |
| `src/lakehouse/seeds/` | Hand-curated, evidence-cited seed data (deployments, attribution rules, live checks, figures) |
| `fabric/` | Fabric item definitions (lakehouse, notebooks, pipeline) and `parameter.yml` |
| `.deploy/deploy.py` | Wheel upload, `fabric-cicd` publish, shortcut |
| `infra/` | Terraform: Fabric workspaces, cost-export storage |
| `docs/superpowers/` | Design spec and implementation plans |
| `docs/research/` | Dated Fabric research the design relies on |
