# Demo assets — a self-contained, offline twin of the two external feeds

Two recordings that let `bash scripts/seed_demo.sh` run the whole agent with **no
API keys, no Render services, and no Project 2**. They exist because a fresh
clone otherwise boots healthy but empty: the store universe comes from Project 2
and the store signals come from the deployed forecast service.

| File | Real source (where the bytes came from) | Read by |
| --- | --- | --- |
| `campaign_audit.jsonl` | `GET /audit` on the campaign service — the Campaign 18 run, verbatim | `tools/campaign_tool.py` via `CAMPAIGN_AUDIT_LOG_PATH` |
| `recordings.json` | the forecast service: `/predict` (5 stores x 21 days), `/controls` and `/actuals` per store, captured 2026-10-02 | `demo/forecast_stub.py`, then `tools/forecast_tool.py` via `FORECAST_API_URL` |
| `forecast_stub.py` | replay server: serves the recorded bytes over the real contract | started by `scripts/seed_demo.sh` |

**These are recordings, not estimates.** Every sales and uplift number the demo
shows is a byte the real services once returned - which is exactly why the demo
recommendations are honest, not theatrical. Refresh them by deleting
`recordings.json` and asking a maintainer to re-run the capture against the
live services (the steps are the `curl` commands behind `tools/*`, one endpoint
each - no new tooling).

Real deployments point the two variables at the real services;
`scripts/seed_demo.sh` only defaults to these recordings when you have set
nothing.

`tests/test_demo_fixtures.py` pins the contract: both fixtures survive the same
runtime parsers the live path uses, the playback satisfies the real
`forecast_tool` validators, and every store in the audit line has recorded
signal.