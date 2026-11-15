# Project 10 live report: the gateway session of 15 November 2026

One measured session sent the ReleaseLens AI gateway's calls, one event per call, through Azure
Event Hubs into Fabric: an Eventstream, an Eventhouse with a KQL database, a Real-Time Dashboard,
an Activator, and OneLake availability into project 9's lakehouse. The traffic is a staged demo
from one caller: a smoke test, a forced failover and a short token burst, 107 calls in about 12
minutes. It is not production traffic, and nothing here measures a service under real load.

This follows the [live session runbook](superpowers/plans/2026-10-24-p10-plan-2-live-session.md),
failures included. Times and dates are UTC unless marked as Brisbane time (UTC+10). Identifiers
(tenant, subscription, namespaces, connections, users) are left out on purpose. The README's
[project 10 section](../README.md#real-time-the-ai-gateways-calls-project-10) has the short version.

## The evidence

| Evidence | Result | Date, sample, limits |
|---|---|---|
| An alert that fired for a forced failover | **Fired.** Forced at 00:59:50 UTC; first `secondary` answer at 01:00:03; activation at 01:04:16 (4 min 13 s later) | 15 Nov; one failover (41 of 45 calls answered by the secondary); the rule polls every 5 minutes, so up to ~10 minutes' lag is by design |
| A second alert for a token spike | **Fired.** Burst at 01:05:26 UTC (60 calls, 21,000 tokens in the 01:05 minute); activation at 01:09:16, 3 min 16 s after that minute closed | 15 Nov; one 60-call burst; threshold 8,000 tokens per caller-minute; the rule reads only closed minutes |
| Gateway → Eventhouse delay | p50 18.6 s, p95 18.7 s, max 23.7 s, n = 107 | first availability (earliest ingestion) minus the gateway's event time; p50 and p95 estimated (T-Digest), max exact; clock skew is shown, not clamped. Events arrive in batches, so p50 and p95 sit close together |
| Dashboard snapshot | [top](live/p10/dashboard-1-top.jpg) and [bottom](live/p10/dashboard-2-bottom.jpg) | the session's time range, 00:50–01:10 UTC |
| Cost per gateway call | **A$0.0027 per call** (A$2.73 per 1,000): API Management A$0.29 that day over 107 calls | from the Azure cost export for 15 Nov (UTC), read 16 Nov and loaded to gold by `nb_gateway_load`; one billed hour of Basic v2 for a gateway that existed about 32 minutes, so the hour, not the calls, sets the figure; tokens (37,450, 350 per call) are not priced, since the event has no model |

The 107 calls: 2 smoke calls through the gateway, 45 in the failover test, 60 in the burst. All came
from one caller (`owner`); the optional second caller was not run. No call was refused by the
gateway, and no `request_id` arrived with two different payloads. All 107 rows also reached the
lakehouse through OneLake availability, and gold `fact_gateway_call` holds the same 107 (its count
check against the Eventhouse passed).

The published queries' results for the session are in [`live/p10/`](live/p10/) as CSV, with
[`summary.json`](live/p10/summary.json) (the Eventhouse count and the duplicate check).

## What it cost

From the cost export ([design spec](superpowers/specs/2026-10-23-project-10-realtime-gateway-design.md)
§5), in AUD:

| Item | Cost | Notes |
|---|---|---|
| API Management (Basic v2) | A$0.29 | one billed hour; its calls meter A$0.00. It existed about 32 minutes, in two creations, both inside that hour |
| Event Hubs (Standard) | A$0.90 | about 20 hours, 14 Nov 05:18 to 15 Nov 01:31 UTC (A$0.77 on 14 Nov, A$0.13 on 15 Nov) |
| Model tokens | A$0.03 | the export bills 39.2 thousand tokens on 15 Nov; the gateway counted 37,450 in its 107 calls |
| Storage, bandwidth, logging | under A$0.01 | |
| Fabric capacity | free | the trial |
| **Total** | **about A$1.22** | mostly Event Hubs, kept for the session week rather than the session itself |

## What failed or changed on the way

The day before (14 Nov):

- **The Activator definition.** Fabric rejected the hand-written Activator at dev's git import
  ("The importArtifactRequest field is required"). A probe of Fabric's create API in dev found two
  causes. Microsoft's Reflex definition page shows the KQL source pointing at the Eventhouse; Fabric
  needs the KQL database, plus two fields the page omits. And every rule needs an action. Fixed in
  [#23](https://github.com/DmitryLyubaev/llm-cost-quality-fabric/pull/23); the prod deploy accepted
  it and both rules ran. Prod's exported definition then differed from git in one more way: Fabric
  adds `runSettings.isStopped: false` to each KQL source. Git now carries it too, with its test.
- **The Eventstream's sign-in.** The design had the namespace Entra-only, read by the Fabric
  workspace's identity. The Fabric connection accepted that, but the Eventstream's Event Hubs source
  does not ("WorkspaceIdentity is not supported for data source type EventHub; use Basic"). With the
  owner's decision, key access was switched on for the session week and Fabric read through a
  listen-only key on the one hub. The gateway still sent with its managed identity. The namespace
  and its keys were destroyed straight after the session (15 Nov, 01:31 UTC).
- **The first prod deploy** stopped once on a timing conflict ("previous operation is still in
  progress") right after the KQL database was created; a re-run cleared it.
- **The dashboard's git import** into dev was rejected ("Invalid payload in definition part"), though
  Fabric's create API accepts the same file and the prod deploy published it. Still unexplained.

The session day (15 Nov):

- **The gateway's event statement failed its first save.** API Management checks a policy
  expression only when it saves the policy, and its parser refused two things the unit tests could
  not see: a single-statement `if` without braces, and `DateTime.ToUniversalTime()` (not allowed in
  expressions; `context.Timestamp` is UTC already). The first apply failed at 00:37 UTC and the
  gateway was destroyed at once, as the runbook says. With the owner's yes, both were fixed
  ([ReleaseLens #89](https://github.com/DmitryLyubaev/releaselens/pull/89), with tests for each
  rule) and the gateway re-applied; the second rule was found and fixed in two minutes with the
  gateway still up.

The day after (16 Nov):

- **The first run of `nb_gateway_load` stopped at its first cell** ("%pip magic command is
  disabled"). Fabric disables `%pip` in any run that is not interactive unless the run passes
  `_inlineInstallationEnabled = true`, as `pl_daily_load` does for its notebooks; the run started
  through the job API without it. It stopped before reading anything. Re-run with the flag, it
  passed its count check (107 = 107) in under a minute.
- **Prod's gold moved on.** Prod's daily schedule was off, so one manual `pl_daily_load` run loaded
  15 November's costs first. Prod's gold now runs past the 30 September – 13 November range that
  project 9's answers were measured on; those answers stay as published, dated 10 November.

## The replay test (14 Nov, not evidence)

50 replay events reached the raw KQL table within a minute and the lakehouse within ten; the
published function, every tile and both alert rules ignored them, as designed. Replay rows never
feed a figure.

## After the session

Everything that billed is gone: the gateway (15 Nov, 01:14 UTC), the Event Hubs namespace and its
role assignments (01:31 UTC), the prod connection secret and the Fabric connection. The six
real-time items stay in prod as working definitions, stopped: the Eventstream is paused and both
Activator rules are off. Later prod deploys skip them, since the connection secret is gone.
