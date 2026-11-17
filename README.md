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

**Small data, real platform.** 1,773 result rows and 745 cost lines. The point is the platform
work (PySpark and Delta, dimensional modelling with a Type 2 dimension, Fabric pipelines, git
integration and CI/CD), done with every number measured, dated and reproducible.

## Status, 17 November 2026

| Part | State |
|---|---|
| Code, tests, Fabric item definitions, deploy workflow, Terraform | **1,581 tests** (projects 9–12); CI green on GitHub |
| Cost export (Azure Cost Management → storage) | **Live** since 22 October; daily month-to-date plus a one-time September run |
| Dev workspace | Syncs from git; first passing pipeline run on 9 November, after the first run failed on the notebooks' lakehouse binding; each of the four quality checks shown failing on purpose on 10 November |
| Prod workspace | Deployed by GitHub Actions on 10 November; its first run passed all four checks the same day; ran on a daily schedule at 06:00 Brisbane time on 12–14 November; the schedule was then switched off (14 November) so prod's gold stays as published here |
| The three answers | **Measured** from prod's gold, load date 10 November 2026 |
| Fabric capacity | Five prod runs measured (10–14 November, three of them scheduled), no throttling. Smallest paid size: F2 by capacity units; keeping the trial's Spark nodes needs F16, or a custom pool on F4 |
| Real-time path (project 10) | **Measured** in one staged session on 15 November 2026, then torn down; [see below](#real-time-the-ai-gateways-calls-project-10). One manual prod load on 16 November took prod's gold past the answers' range |
| Semantic model, report and row-level security (project 11) | **Live in prod** since 16 November 2026: a Direct Lake model and a three-page report, each lead seeing only their projects; the model matched gold as the owner and as both test leads; [see below](#semantic-model-report-and-row-level-security-project-11) |
| Capstone data agent (project 12) | **Measured** on 17 November 2026: a Fabric data agent asked 15 frozen questions, 3 passes directly and 3 through Foundry, then torn down; [see below](#the-capstone-a-fabric-data-agent-direct-and-through-foundry-project-12) |

Every figure below is measured and dated. Nothing here is aspirational. The
dated record of how it got here, failures included, is in [docs/live-report.md](docs/live-report.md).
Dates and times are UTC unless marked as Brisbane time (UTC+10).

## The answers

Measured on 10 November 2026 from prod's gold tables (load date 10 November). Costs cover
30 September to 9 November 2026. Each answer comes from the package's own functions
(`lakehouse.answers`), the same ones the tests cover.

### 1. Cost per project

Actual Azure spend from Cost Management, in the billing currency (AUD), attributed by resource
group and, for shared resources, by each project's session windows.

| Project | AUD |
|---|---:|
| p4 API Management as the AI gateway | 9.4072 |
| p8 Azure Pipelines delivery | 6.1357 |
| p3 Foundry agent | 4.5528 |
| p6 App Service front end | 2.8260 |
| p2 RAG benchmark on Azure AI Search | 2.4332 |
| p5 Azure Functions | 1.1564 |
| p1 Azure OpenAI, keyless | 0.1172 |
| p9 this lakehouse (the cost-export storage) | 0.0024 |
| p7 Container Apps build agents | 0.0011 |
| releaselens-shared (shared spend outside any session window) | 0.0001 |
| **untagged** (5 lines, all zero cost) | **0.0000** |
| **Total** (745 cost lines) | **26.6322** |

Figures are rounded to four places; unrounded, the projects add up to the total exactly. Of the total, 22.88 AUD matched a project's own resource group (326 lines), 3.75 AUD was
shared spend split by session window (261 lines), and 0.0001 AUD was shared spend outside any
session window (153 lines, the `releaselens-shared` row); the other 5 lines are the untagged
zero-cost ones. Nothing was dropped: the per-project totals add
up to the export's total, and a quality check holds each month to the export's manifest.

### 2. Quality across models

Quality is only comparable within a study: each study asked different questions with different
measures. Each study also set its own rule, before any data existed, for when a difference
counts; that study's verdict is given with its table, and a gap without a declared difference is
not a result.

**Project 1, cited answers** (2 October 2026; 10 questions × 3 passes = 30 answers per arm):

| Arm | Model | Groundedness (n = 30) | Citation recall (n = 24) | Citation precision (n = 24) | Must-contain (n = 18) | Mean latency |
|---|---|---:|---:|---:|---:|---:|
| A | Claude Sonnet 5 (outside Azure) | 0.840 | 0.889 | 0.569 | 1.000 | 8.5 s |
| O | gpt-4.1-mini `2025-04-14` (OpenAI, outside Azure) | 0.860 | 0.674 | 0.576 | 1.000 | 3.3 s |
| Z | gpt-4.1-mini `2025-04-14` (Azure OpenAI) | 0.783 | 0.646 | 0.585 | 0.889 | 3.8 s |

Each measure counts only the answers it applies to, as project 1 scores them: groundedness every
answer, citation recall and precision the 24 answers to answerable questions, must-contain the
18 answers to questions with something they must contain.

Project 1's own verdict: every comparison (Z against O, Z against A) was **inconclusive** at its
sample size.

**Project 2, retrieval** (3 October 2026; n = 229 questions per arm):

| Arm | What it is | Top-1 | MRR |
|---|---|---:|---:|
| E1 | BGE-small, run locally (no Azure model) | 0.376 | 0.494 |
| E2 | text-embedding-3-small, exact cosine | 0.485 | 0.613 |
| E3 | text-embedding-3-large, exact cosine | 0.646 | 0.739 |
| S1 | ReleaseLens's own hybrid retriever (no Azure model) | 0.367 | 0.483 |
| S2 | Azure AI Search hybrid, with text-embedding-3-small | 0.524 | 0.637 |
| S3 | S2 plus Azure AI Search's semantic ranker | 0.629 | 0.739 |

Project 2's own verdict: three pre-registered comparisons were each a declared **difference**:
E3 − E1 top-1 +0.271 (the published figure), S3 − S1 +0.262 and S3 − S2 +0.105.

**Project 3, Foundry agent** (6 October 2026; n = 60 per arm):

| Arm | Model | Succeeded | Task adherence | Intent resolution | Tool-call accuracy | Mean time per task (whole run) |
|---|---|---:|---:|---:|---:|---:|
| gpt | gpt-5.6-luna `2026-07-09` | 59 / 60 | 0.98 | 4.70 | 3.75 | 18.3 s |
| claude | claude-haiku-4-5 (version 2) | 57 / 60 | 0.93 | 4.67 | 3.88 | 23.7 s |

Task adherence, intent resolution and tool-call accuracy are Foundry's evaluator scores as
project 3 recorded them: descriptive, judged by the same gpt-5.6-luna the GPT arm runs on, and
the first two are preview evaluators. Success is decided by the database. Time per task covers
the whole run, including any waits for throttling. Project 3's own verdict: the success-rate
comparison was **inconclusive** at its 20 scenarios.

**Across model versions: the data can't tell.** Every arm resolved to exactly one model version
(the two no-model arms to none), so no study has two versions of one model to compare
([why](#limits)).

### 3. Cost per measured result

The estimate (USD at list price, from each study's own token counts; project 1's includes its
groundedness judge's calls) sits beside the actual
(AUD, from Cost Management). They are never added together, and the actual is never converted.
The estimate is also shown in AUD at the export's own USD-to-AUD rate for October 2026
(1.4246).

| Project | Results | Estimate, USD (per result) | Estimate in AUD (per result) | Actual, AUD (per result) |
|---|---:|---:|---:|---:|
| p1 | 90 | 2.7055 (0.0301) | 3.8543 (0.0428) | 0.1172 (0.0013) |
| p2 | 1,374 | 0.0012 (0.0000009) | 0.0017 (0.0000012) | 2.4332 (0.0018) |
| p3 | 120 | 2.0606 (0.0172) | 2.9355 (0.0245) | 4.5528 (0.0379) |

No result was unpriced. Why the two columns differ:

- **p1's actual is far below its estimate** because 2.64 of its 2.71 USD bills outside Azure,
  where Cost Management can't see it: arms A and O answer through Anthropic and OpenAI directly,
  and every arm's groundedness judge is Claude Sonnet 5. Only arm Z's answering tokens
  (0.0631 USD, about 0.09 AUD) run on Azure.
- **p2's estimate prices only embedding tokens**; its actual is everything attributed to
  project 2 in Azure, including its session-window share of the shared search service.
- **p3's actual is above its estimate**: the estimate prices the agent's tokens; the actual is
  all of project 3's Azure spend.

## Real-time: the AI gateway's calls (project 10)

> **A demo stream.** One staged session on 15 November 2026: 107 calls from one caller, with a
> forced failover and a short token burst. Good for showing the path works and how fast; not a
> measure of production traffic.

The ReleaseLens AI gateway (Azure API Management) sends one event per call through Event Hubs into
Fabric: an Eventstream into an Eventhouse (KQL), a Real-Time Dashboard, an Activator with two
alert rules, and OneLake availability into the lakehouse, where `nb_gateway_load` builds silver
`gateway_calls` and gold `fact_gateway_call`. The dated record, failures included, is in
[docs/live-report-p10.md](docs/live-report-p10.md).

| Part | Status |
|---|---|
| Event Hubs → Eventstream → Eventhouse (KQL) | Ran 14–15 Nov; Event Hubs torn down 15 Nov; the stream is paused |
| Real-Time Dashboard (7 tiles) | Published; snapshot in [docs/live/p10/](docs/live/p10/) |
| Activator (failover, token spike) | Both fired in the session (4 min and 3 min after their trigger); both stopped since |
| OneLake availability → lakehouse `gateway_calls` | On; replay and session rows arrived (157 = 50 replay + 107 session) |
| `fact_gateway_call` in gold | 107 rows for the session, loaded by `nb_gateway_load` (16 Nov; count check passed) |

**The four answers**, measured 15–16 November 2026:

| Question | Answer | Sample and limits |
|---|---|---|
| Does an alert fire on a forced failover? | **Yes**, 4 min 13 s after the first secondary answer | one failover; the rule polls every 5 minutes |
| How long from the gateway to the Eventhouse? | p50 18.6 s, p95 18.7 s, max 23.7 s | 107 calls; events arrive in batches |
| What does it look like? | [top](docs/live/p10/dashboard-1-top.jpg) and [bottom](docs/live/p10/dashboard-2-bottom.jpg) of the dashboard | the session's 20 minutes |
| What did a gateway call cost? | **A$0.0027** (A$0.29 of API Management, one billed hour, over 107 calls) | one day; the hour, not the calls, sets it |

A second alert, for a token spike, also fired: 3 min 16 s after the burst's minute closed.

**The threshold is 8,000 tokens per caller-minute**, set from the 9 October gateway traffic: the
failover test runs at about 5,250 tokens a minute and the burst at about 21,000, so 8,000 sits above
the failover with a margin and below the gateway's 10,000 limit. On 15 Nov the failover minutes peaked
at about 5,250 and the burst minute at 21,000, as expected.

**What the gateway can observe:** 429s per backend
([design spec](docs/superpowers/specs/2026-10-23-project-10-realtime-gateway-design.md) §4.1). Its retry hides a backend's 429 from
the caller when the other backend answers, so refusals counted here are the gateway's own. None
occurred in the session.

**Spec change (14 Nov):** Fabric read Event Hubs with a listen-only key for the session week, because
the Eventstream source does not accept workspace identity; everything else stayed keyless.

## Semantic model, report and row-level security (project 11)

A Direct Lake semantic model over gold, `sm_portfolio`, with row-level security by project, and a
three-page Power BI report on it, `rp_portfolio`. Both are text in git (TMDL and PBIR) and reach
prod through the same deploy as everything else, on 16 November 2026. The dated record, failures
included, is in [docs/live-report-p11.md](docs/live-report-p11.md).

| Part | Status |
|---|---|
| `sm_portfolio` | Direct Lake on OneLake. In prod it reads gold through a fixed identity: prod's workspace identity, through a cloud connection with single sign-on off, bound by the deploy |
| `rp_portfolio` | Three pages: spend, quality, gateway |
| Row-level security | One role, `ProjectLead`. A lead sees only the projects listed for them in `security.user_project`. Two test leads (p3 and p4) have Read and Build on the model and no workspace role |
| Refresh after the daily load | A third step in `pl_daily_load` refreshes the model in prod (proved by a manual load on 16 Nov); dev skips it |
| Model-versus-gold check | Passed in prod on 16 Nov, as the owner and as both leads |

**Row-level security, three ways.** Prod's report as the owner, and as each lead through "Test as
role" (captured 17 November 2026, Brisbane time; each image is the report canvas only). The
owner's spend total, 27.85 AUD, is above the answers' 26.63 because prod's gold has moved on
([Limits](#limits)).

| Page | Owner | p4 lead | p3 lead |
|---|---|---|---|
| Spend | [every project, 27.85 AUD](docs/live/p11/2026-11-17-p1-spend-owner.png) | [p4 only, 9.70](docs/live/p11/2026-11-17-p1-spend-p4-lead.png) | [p3 only, 4.55](docs/live/p11/2026-11-17-p1-spend-p3-lead.png) |
| Quality | [three studies, 1,584 results](docs/live/p11/2026-11-17-p2-quality-owner.png) | [empty: p4 ran no study](docs/live/p11/2026-11-17-p2-quality-p4-lead.png) | [p3's study only, 120 results](docs/live/p11/2026-11-17-p2-quality-p3-lead.png) |
| Gateway | [the session, 107 calls](docs/live/p11/2026-11-17-p3-gateway-owner.png) | [the whole page: the gateway is project 4](docs/live/p11/2026-11-17-p3-gateway-p4-lead.png) | [empty](docs/live/p11/2026-11-17-p3-gateway-p3-lead.png) |

**The check.** `nb_model_check` runs DAX against the model and compares it with gold's answers,
within 0.005. Run in prod as the owner on 16 November 2026 (07:08–07:10 UTC), it printed:

> model_matches_gold passed: the model matches gold as the owner (cost_per_project,
> quality_across_models, cost_per_result, cost_per_gateway_call, untagged_cost) and as 2 lead(s)
> (the lead of p3: 3 row(s); the lead of p4: 1 row(s))

The first run, about 20 minutes earlier, failed for the leads only, because no step had added them to the
role. Adding a lead is a manual step: see below.

**The measures**, each mirroring a gold answer, so the report invents no figure:

| Page | Measures | Mirrors |
|---|---|---|
| Spend | `Cost (AUD)`, `Untagged cost` | `answers.cost_per_project` |
| Quality | `Results (n)`, `Success rate`, `Latency (ms)`, `Groundedness (mean)`, `Citation recall`, `Citation precision`, `Must-contain rate`, `Top-1 rate`, `MRR`, `Tool-call accuracy`, `Task adherence`, `Intent resolution` | `answers.quality_across_models` |
| Quality | `Estimated cost per result (USD)`, `Estimated cost per result (AUD)` | `answers.quality_across_models` and `answers.cost_per_result` |
| Gateway | `Calls`, `Refused by gateway`, `Tokens (total)`, `Tokens per call`, `Cost per 1,000 calls` | `gateway.answers.cost_per_gateway_call` |

There is no blended score and no share measure: under row-level security a lead's share would be
100% of what they see.

**Adding a lead takes three steps, one of them by hand.** The lead's sign-in name and project go in
the git-ignored access file, which `nb_access_load` loads into `security.user_project`. The lead gets
Read and Build on the model only, never a workspace role, because row-level security doesn't apply
to Admin, Member or Contributor. And the lead is added to `ProjectLead` on the model's Security page.
Role members live in the service, not in the model's definition, so sign-in names never reach
git. A deploy keeps them (checked 16 November).

**Direct Lake fallback: know, don't build.** A Direct Lake model on OneLake, like this one, never
falls back to DirectQuery: a table it can't frame, or one over a guardrail, makes its queries
fail instead. A Direct Lake model on the SQL analytics endpoint falls back to DirectQuery through
the endpoint in several cases: a table with security at the endpoint (row-level security,
for example), a non-materialised SQL view, a table over a guardrail, or a model not reframed after
its tables changed. There, queries keep working, but through DirectQuery. The model's own row-level security causes
no fallback in either kind. The only guardrail this data could hit is 1,000 Parquet files per
table on F2 or F4.

**Licence.** Authoring a model and report needs a Power BI licence, even on the trial capacity. The
author used the Fabric trial's Power BI rights, which on 15 November had 54 days left (to about
8 January 2027). The work was finished on 16 November. Viewing the screenshots needs no licence.

**Capacity.** The model and report together used about 353 CU-seconds over their whole build,
9–17 November (Brisbane days). The report has no line of its own in the metrics; its rendering
counts as queries on the model. Their busiest day, 16 November, used 317 CU-seconds: about 0.2% of
an F2's daily budget, with no operation rejected.

## The capstone: a Fabric data agent, direct and through Foundry (project 12)

A Fabric data agent, `da_portfolio`, answers plain-English questions about gold. Project 12 asked it
15 frozen questions two ways. Directly: through its MCP endpoint. Through Foundry: through project 3's
prompt agent `portfolio-analyst` (`gpt-5.5`), which calls the same endpoint as its one tool. Each
route ran 3 passes, scored by rule against answers computed from a snapshot of prod's gold. Every
answer and verdict is in [capstone/report.md](capstone/report.md).

| Route | Correct per pass (of 15) | Mean | Right in all 3 | Right in none | Fabric CU |
|---|---|---|---|---|---|
| Direct | 14, 14, 14 | 14.00 | 14 | 1 | 15,961 CU-seconds |
| Through Foundry | 14, 14, 12 | 13.33 | 12 | 1 | 13,304 CU-seconds |

- **Dates.** The snapshot of prod's gold was taken on 16 November 2026 at 05:44 UTC. The direct
  passes ran on 17 November, 01:02–01:10 UTC, and the Foundry passes 03:12–03:24 UTC.
- **What was frozen.** Freeze `963eebbaf65d`, the only one: the 15 questions, their expected answers,
  the scoring rules, the data agent's definition (published from dev `36b2076`), project 3's analyst
  at `51bf705`, and the two model labels: `gpt-5.5`, and `fabric-data-agent` for the direct route,
  because Fabric picks the data agent's model and doesn't expose it. Before the freeze, the agent's
  instructions were tuned on 10 separate practice questions only.
- **The misses.** q13 was wrong in every pass of both routes. Asked how many scored results used
  `text-embedding-3-large` in the p2-retrieval study, the agent answered 0, not 229; one answer shows
  it filtering on a model value that matches nothing. Through Foundry, pass 3 also missed q07 (0, not
  60) and q11. The q11 answer named the right projects, but one project's title contains "project 2",
  which the frozen scorer reads as an extra `p2`, so it stays a miss.
- **CU.** Read from the Capacity Metrics app: every background operation on the capstone's items
  that started inside a route's window, summed. That covers the data agent's AI queries (about 95% of
  each figure), its SQL endpoint queries and its OneLake reads. Foundry's own `gpt-5.5` tokens
  (57,599 in and 5,481 out over the 45 answers) bill in Azure, not as Fabric CU.
- **A failed attempt.** The first Foundry run stopped in pass 3 on an HTTP 500 from Foundry. The
  runner can't re-run a single pass, so that attempt was set aside unreported and all three passes
  ran again.
- **Torn down.** The F2 capacity (`capstonef2`, Australia East), the capstone workspace and project
  3's Foundry resources existed only for the session, 16–17 November, and were destroyed on 17
  November.

**What it doesn't show.** 15 questions and 3 passes are too few to estimate a rate or rank the
routes: the figures describe this run, nothing wider. The data agent isn't deterministic: a
practice question it refused directly got an invented answer through Foundry 45 minutes later. The
two routes share the data agent, so they differ only in what Foundry adds around it. The Fabric data
agent and its MCP endpoint were in preview.

## Capacity

Measured with Microsoft's Fabric Capacity Metrics app on the FTL64 trial capacity (64 capacity
units, Australia East), read from the app's semantic model with
[`tools/capacity_report.py`](tools/capacity_report.py). Times here are Brisbane time. A run counts
what prod used in the hour it started in and the hour after. Parts may not add up exactly because
of rounding.

| Prod run, started (Brisbane time) | CU-seconds | Notebooks | Pipeline | Lakehouse traffic |
|---|---|---|---|---|
| 10 Nov 15:26, first prod load (by hand) | 2,046 | 1,844 | 40 | 162 |
| 11 Nov 05:37, re-run after the project 1 fix (by hand) | 1,959 | 1,770 | 40 | 149 |
| 12 Nov 06:00, scheduled | 1,587 | 1,423 | 40 | 124 |
| 13 Nov 06:00, scheduled | 2,417 | 2,249 | 40 | 127 |
| 14 Nov 06:00, scheduled | 2,200 | 2,037 | 40 | 123 |

The two notebooks are most of each run: `nb_load` 1,031–1,392 CU-seconds and `nb_ingest_public`
294–857; each ran 73–134 seconds. Lakehouse traffic is OneLake reads and writes in those two hours.
It also counts anything else that touched prod's tables then: the copies of gold downloaded to
check each run, and the private extract uploaded just before the 11 November re-run.

| Measure | Value | Measured |
|---|---|---|
| Throttling, rejections, overage | **None**: 0 minutes throttled, 0 operations rejected, no day with a delay, rejection or overage | 9–14 Nov 2026 |
| Peak utilization (dev and prod) | 0.67% of the trial capacity | 9–14 Nov 2026 |
| Busiest day, whole capacity | 35,309 CU-seconds, 10 November: mostly dev's runs with each check failing on purpose, plus prod's deploy and first load | 10 Nov 2026 |
| Busiest notebook session | About 21 Spark vCores allocated on average (`nb_load`, 1,357 CU-seconds in 128 s; one CU is two vCores). The metrics don't give the peak | 14 Nov 2026 |
| Smallest paid size that fits | **F2** by capacity units. Keeping the Spark nodes the notebooks had on the trial needs F16 with the default pool, or a custom pool on F4 (not tested). Not bought | 14 Nov 2026 |

Why it depends on Spark. Fabric smooths background jobs over 24 hours, so a capacity's daily
budget is its capacity units × 86,400 CU-seconds: 172,800 for an F2. The largest prod run used
1.4% of that, and even the busiest whole-capacity day used 20%. Spark sets the other limit. The
busiest session averaged about 21 allocated vCores, so at times it had at least three 8-vCore
Medium nodes. The default (starter) pool allows one Medium node on F2 and F4, two on F8 and four
on F16 ([starter pools](https://learn.microsoft.com/en-us/fabric/data-engineering/configure-starter-pools));
a custom pool can use the capacity's burst cores, 24 vCores (three Medium nodes) on F4
([job concurrency](https://learn.microsoft.com/en-us/fabric/data-engineering/spark-job-concurrency-and-queueing));
both read 12 November 2026. On an F2 the notebooks would get one node. One session already ran
that way: `nb_ingest_public` on 12 November averaged 8 vCores, one node, and was its fastest run
(73 seconds). How `nb_load` would do on one node isn't measured.

A backfill isn't measured because none is needed: one load reads the whole cost export, both
private extracts and the public sources, so the first run built everything.

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
- **A Fabric pipeline** (`pl_daily_load`): ingest, then load, with retries, then (in prod only) a
  refresh of the semantic model. It is parameterised by date.
- **A Direct Lake semantic model and a report** over gold (project 11), with row-level security by
  project.

## Layers and grains

| Table | Grain (one row per…) | Rows in prod, 10 Nov 2026 |
|---|---|---:|
| `fact_eval_attempt` | study × item × arm × pass × repeat | 1,773 |
| `fact_daily_cost` | cost-export line: resource × meter × day | 745 |
| `fact_live_check` | check × session | 74 |
| `dim_model_deployment` | deployment version (Type 2, from Terraform's git history) | 17 |
| `dim_project` | project 1–10, plus `capstone`, `releaselens-shared` and `untagged` | 13 |
| `dim_study` | study run | 3 |
| `dim_arm` | arm within a study | 11 |
| `dim_date` | day | 70 |
| `fact_gateway_call` | gateway call (project 10's session data, loaded by its own notebook) | 0 |
| `reject.eval_unresolved` | eval attempt that resolves to no deployment version, or to several | 0 |
| `security.user_project` | user (UPN) × project the user may see (row-level security; loaded from git-ignored `private/access.csv`, never committed) | 0 |

The Type 2 dimension joins every result to the deployment version live when it was measured. A
result that resolves to none, or to several, goes to the reject table and fails a check. It is
never dropped.

## Sources

| Source | Public? | Into the lakehouse |
|---|---|---|
| Project 3 agent study (120 runs + 9 repeats) | Public | Fetched by the pipeline, pinned to a commit |
| Project 2 questions (229) | Public | Fetched, pinned |
| Project 1 and 2 per-query scores | Private | Sanitised extract (no answer text, no identifiers), uploaded by hand to each workspace |
| Projects 4–8 live checks (74 checks, every fail and not-run kept) | Public reports | Transcribed to a seed, each row citing its report and commit |
| Model deployment history | Public Terraform history | Seed rows citing their commits; one hand-entered capacity change, flagged |
| Azure spend | Private | Cost Management export, read through its manifest |
| Project 7 private originals | **Lost** | Not loaded; only its public summary is |

## Quality checks

Four checks, each guarding a claim this README makes. Each was shown failing alone, on purpose,
in dev on 10 November 2026, and each change was then undone:

| Check | Guards | Failed on purpose with |
|---|---|---|
| Every result resolves to exactly one deployment version | The Type 2 join behind "quality across models" | A project 1 Azure result dated 2020, before any deployment: `1 unresolved result(s): p1-three-arm/Z no_version x1` |
| Each month's cost total and row count match the export's manifest | "Nothing unattributable is dropped" | One cost line removed from a copy of November's export: `manifest 133 row(s), silver 132` |
| Gold reproduces the published figures (P1 study cost $2.7055, P3 59/60 and 57/60, P2 n = 229 per arm, P2 E3 − E1 top-1 +0.271) | That gold agrees with what the earlier reports claim | A wheel built with one published figure changed: `p3_gpt_success: gold 59, published 58` |
| Re-running a load date leaves gold unchanged | "Running the same date twice changes nothing" | One extra row after a passing run: `content changed: fact_eval_attempt` |

A failed check fails the notebook, so the pipeline run fails and nothing is logged as passed.

## Deployment

- **Dev:** the dev workspace syncs from git through Fabric's git integration.
- **Prod:** deployed only by GitHub Actions, as a service principal signed in through OIDC
  (`azure/login` plus `fabric-cicd`), behind the owner's approval. The service principal holds no
  secret and no Azure role. The first prod deploy took four minutes.
- **The model's connections:** two Fabric cloud connections, both with prod's workspace identity as
  their credential, so nothing holds a secret or expires. One is the model's fixed identity for
  reading OneLake; the other lets the pipeline's refresh step refresh the model. The workspace
  identity has Read and ReadAll on the lakehouse and the Contributor role on prod's workspace, which a refresh
  needs. Their IDs reach the deploy as environment secrets.
- **Infrastructure:** Terraform creates the workspaces and roles (`microsoft/fabric`) and the
  cost-export storage (`azurerm`).
- **`main` is protected:** pull requests only, with the `test` check required.

## Decisions worth knowing

- **GitHub Actions for prod, not Azure Pipelines.** Project 8 already showed Azure Pipelines,
  and this repo's CI lives on GitHub.
- **The wheel goes to `Files/wheels/`, not into a Fabric Environment.** Publishing an Environment
  with a custom wheel can take over 20 minutes and needs the binary in git.
- **Notebooks carry dev's real lakehouse ID.** Fabric's git sync doesn't bind a notebook's
  default lakehouse: the first dev run failed on the placeholder. The notebooks commit dev's IDs,
  and the prod deploy swaps them for prod's (`fabric/parameter.yml`).
- **Shortcut tracking is off in git.** A synced shortcut would commit the storage URL, and
  "Update from Git" deletes shortcuts it doesn't know. The deploy creates the shortcut from a secret.
- **The cost-export storage has a public endpoint, Entra-only.** Shared keys are off and access
  needs Entra sign-in, but the endpoint is public, because the trial can't reach a firewalled
  account.
- **Terraform doesn't assign the capacity.** The provider doesn't support trial capacity, so it
  is assigned once through the API.

The full reasoning, including the options rejected, is in the
[design spec](docs/superpowers/specs/2026-10-21-fabric-lakehouse-design.md).

## What it costs

| Item | Cost |
|---|---|
| Cost-export storage (all of project 9's Azure spend) | 0.0024 AUD, 22 October to 9 November 2026 |
| Fabric trial capacity | Free for 60 days (from 9 November 2026); capacity used: see [Capacity](#capacity) |
| Project 10's gateway session | About 1.22 AUD: Event Hubs 0.90 (about 20 hours), API Management 0.29 (one hour), model tokens 0.03 |
| Project 11's model and report | Nothing new: they run on the trial capacity, and the two test users have no licence |
| Project 12's capstone | The F2 ran about 14.7 hours over 16–17 November: about 8.80 AUD at its list price of 0.597 AUD an hour. That's an estimate, not reconciled against the cost export. Foundry's tokens bill per use |
| CI | GitHub-hosted runners, public repo |

## Limits

- **Costs start on 30 September 2026.** The subscription was a Free Trial until then, and Cost
  Management does not export costs from before the offer change. A one-time September export
  returned only 30 September.
- **Costs end a day behind the run.** The 10 November load read costs to 9 November.
- **No model changed version between measured runs.** `gpt-4.1-mini` stayed on `2025-04-14`
  (its changes were capacity), and the Foundry version changes all landed the day before that
  study. So the answers compare models, not versions.
- **Estimates and actuals are different things.** Estimated cost is USD at list price; actual
  spend is in the billing currency (AUD) from Cost Management and covers Azure only, so it can't
  see project 1's two external arms or its judge. The estimate is also shown in AUD, converted at the export's
  own USD-to-AUD rate for the study's month (null when the export has no single rate for that
  month). The two are never summed.
- **Commit time stands in for apply time** in the Type 2 dimension, because git doesn't record
  applies. The one capacity change that isn't in git is hand-entered and flagged.
- **Each result keeps the load date it was first seen, until one of its values changes.** Dev
  first loaded the results on 9 November and prod on 10 November; the project 1 fix then moved
  the 36 project 1 results it changed to 10 November in dev. Apart from that date, the two
  workspaces' gold was identical after both re-ran with the fix on 10 November.
- **Three project 10 items can't sync to dev from git yet** (the real-time dashboard, the
  Activator and the Eventstream; each rejected with its own error). Dev follows a branch that
  leaves them out. Prod's deploy published them for project 10's session; it skips them again now
  that the session's connection is gone. Project 11's model and report sync to dev.
- **A lead's AUD estimate uses only the rate they can see.** `Estimated cost per result (AUD)`
  converts at the cost export's USD-to-AUD rate, read from the cost lines. Row-level security still
  filters those lines, so a lead's rate comes only from their own projects' lines, while gold uses
  every line. The check compares this figure as the owner only.
- **Leads see every model deployment.** `dim_model_deployment` has no project column, so
  row-level security doesn't filter it. The pages show deployment names only beside a lead's own
  results, but a lead with Build could query the whole table. It holds metadata, not figures.
- **The model-versus-gold check allows 0.005.** Some figures are far smaller than that, such as
  estimated cost per result in USD (about 0.0000009 to 0.03), so the check catches only gross
  drift in them. They were compared by eye on 17 November instead: they match the published
  figures.
- **The gateway page has one demo session.** Its figures are project 10's 107 staged calls from
  one caller, not production traffic. It has no per-minute charts: neither gold nor the model has a
  minute column. Its banner points to project 10's Real-Time Dashboard for those.
- **Prod's gold has moved past the answers' range.** One manual load on 16 November (for project
  10's cost answer) added 14 and 15 November's costs. The three answers above stay as measured on
  10 November.
- **Small data.** Sample sizes are stated with every answer.

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
| `fabric/` | Fabric item definitions (lakehouse, notebooks, pipeline, real-time items, the semantic model as TMDL, the report as PBIR) and `parameter.yml` |
| `fabric-capstone/` | The capstone data agent, deployed by hand (`publish-capstone`) |
| `.deploy/deploy.py` | Wheel upload, `fabric-cicd` publish, shortcut |
| `infra/` | Terraform: Fabric workspaces, cost-export storage, Event Hubs, the capstone F2 and workspace (`infra/capstone/`) |
| `tools/` | Local tools: the hygiene check, the capacity report, the deployment-seed check, the gateway event replay, the capstone's helpers |
| `docs/live-report.md` | The dated record of the live build, failures included |
| `docs/live-report-p10.md`, `docs/live/p10/` | Project 10's live session: the record, the dashboard snapshot and the query results |
| `docs/live-report-p11.md`, `docs/live/p11/` | Project 11's live run: the record and the report's screenshots, as the owner and as each lead |
| `docs/superpowers/` | Design spec and implementation plans |
| `docs/research/` | Dated Fabric research the design relies on |
