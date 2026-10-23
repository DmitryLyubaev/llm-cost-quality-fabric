# Published figures

Every figure published from the real-time session has one query in `queries/`, named by its ID. Each
query starts from `GatewayCalls()` (defined in `gateway_calls.kql`), which drops replay events
(`session = "replay"`), keeps one row per `request_id` (the latest ingested) and adds `ingested`, the
row's ingestion time, and `first_ingested`, the earliest ingestion time of any copy of the request
(a late duplicate does not lengthen a call's delay).

| ID | What it measures | File |
|---|---|---|
| `delay_p50_p95_max` | End-to-end delay to first availability, `first_ingested` minus `event_time` in seconds: p50 and p95 (estimated (T-Digest)), max (the max is exact) and the number of calls `n` | `queries/delay_p50_p95_max.kql` |
| `refusals_per_minute` | Calls the gateway itself refused, by status, per minute | `queries/refusals_per_minute.kql` |
| `answered_by_per_minute` | Calls per backend (`primary`, `secondary`) per minute; calls with no backend are left out | `queries/answered_by_per_minute.kql` |
| `tokens_per_minute_per_caller` | Tokens consumed per caller per minute | `queries/tokens_per_minute_per_caller.kql` |
| `session_counts` | Requests (distinct `request_id`, exact) by session and caller | `queries/session_counts.kql` |
| `failover_minutes` | Minutes with at least one call answered by the secondary backend | `queries/failover_minutes.kql` |
| `token_spike_minutes` | Caller-minutes whose tokens exceed the threshold (5000 here; set from the session plan when deployed) | `queries/token_spike_minutes.kql` |

The delay can be negative if the gateway clock runs ahead of the ingestion clock; `first_ingested` minus
`event_time` is not clamped, so a negative minimum would show the skew rather than hide it.
