# Observability

## Metrics

`GET /metrics` (Prometheus text format) — token-gated with the same bearer
credential as the decision endpoints, and fail-closed (503 when no credential
is configured), because decision counts are sensitive business signal.

| Metric | Type | Meaning |
| --- | --- | --- |
| `retail_decisions_total{decision}` | counter | Double-gated decisions in the audit ledger |
| `retail_decisions_by_principal{principal}` | gauge | Decisions per authenticated approver (`user:alice`, `shared-token`) |
| `retail_ledger_entries_total` | counter | Ledger entries (includes non-approve/reject decisions) |
| `retail_pending_approvals` | gauge | Recommendations awaiting human approval |
| `retail_recommendations_total` | counter | Records in the append-only recommendation log |
| `retail_sweeps_total{result}` | counter | Autonomous sweeps completed / failed |
| `retail_last_sweep_timestamp_seconds` | gauge | Unix time of the last successful sweep (staleness signal) |

All values are computed on scrape from the durable stores — the endpoint holds
no state, so it cannot drift from the ledger or the log.

## Scrape config

```yaml
scrape_configs:
  - job_name: retail-decision-agent
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/retail-approval-token   # never inline a secret
    static_configs:
      - targets: ["decision-agent:8001"]
```

## Alert rules

`alerts.yml` ships four rules, each tied to a real failure mode this system has:

| Alert | Severity | Fires when |
| --- | --- | --- |
| `RetailDecisionsConcentratedInOnePrincipal` | warning | One principal makes >80% of decisions (≥5 total) for 1h — shared token in use, or a broken queue |
| `RetailSweepFailures` | critical | Any failed sweep in the last hour |
| `RetailSweepStale` | critical | No successful sweep for 48h — the heartbeat is dead |
| `RetailPendingApprovalBacklog` | warning | >50 recommendations await approval for 2h |

Load with:

```yaml
rule_files:
  - /etc/prometheus/alerts.yml
```
