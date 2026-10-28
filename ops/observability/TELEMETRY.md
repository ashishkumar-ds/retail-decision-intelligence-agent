# Telemetry guide

What the service measures about itself, where it goes, and how to read it.
Companion to `RUNBOOKS.md` (what to do when a rule fires) and
`rag/llm_telemetry.py` (how off-path layers are counted).

## Sources

| Source | What | Where it lands |
|---|---|---|
| `GET /metrics` | All Prometheus series below (bearer token required) | Scraped by Prometheus; rules in `alerts.yml` |
| `rag/llm_telemetry.py` | Per-layer LLM outcomes, guard rejections, latencies, prefilter kept/dropped | In-memory counters surfaced on `/metrics` |
| Sweep scheduler status | Heartbeat, counters, last error, last tick duration | `/health` → `scheduler`, plus `/metrics` gauges |
| Decision ledger + log join | Approval latency (recommendation → decision) | `/metrics` avg/max gauges |

## Series catalog

- `retail_decisions_total{decision}` — double-gated decisions in the ledger.
- `retail_decisions_by_principal{principal}` — concentration input for the governance alert.
- `retail_pending_approvals`, `retail_recommendations_total`, `retail_ledger_entries_total` — backlog and volume.
- `retail_approval_latency_seconds_avg/max` — reviewer responsiveness; joined on `recommendation_id`, records missing either timestamp skipped. Absent until the first decision lands.
- `retail_sweeps_total{result}` — completed vs failed ticks.
- `retail_last_sweep_timestamp_seconds` — heartbeat for staleness.
- `retail_last_sweep_duration_seconds` — wall-clock cost of the last tick; watch for growth as the store universe grows.
- `retail_offpath_llm_events_total{layer,outcome}`, `retail_offpath_llm_guard_rejections_total{layer,reason}`, `retail_offpath_llm_latency_ms_avg{layer}` — the measured-guardrail story (see ADR-0006).
- `retail_prefilter_chunks_total{result}` — kept vs dropped retrieval chunks.

## Reading guide

- **Absent series are honest, not broken.** Gauges appear only when their subject exists (no decisions → no latency; LLM off → no LLM series). Alert rules account for this; don't page on missing series.
- **Counters survive only with the disk.** `/metrics` is computed on scrape from durable stores; after a disk wipe, counters restart and rates dip. Pair any incident timeline with deploy history.
- **Latency is wall-clock.** Sweep duration includes upstream waits (sleeping free-tier services inflate it); approval latency includes nights and weekends. Read trends, not absolutes.
