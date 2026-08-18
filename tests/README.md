# Test categories

The suite is split into three formal categories, enforced with pytest markers
(registered in `pytest.ini` with `--strict-markers`):

| Category     | Marker       | External integrations        | Deterministic? | Default run? |
| ------------ | ------------ | ---------------------------- | -------------- | ------------ |
| Mock         | `mock`       | All faked/monkeypatched      | Yes            | Yes          |
| Real-data    | `real_data`  | Forecast API mocked; realistic transaction fixtures | Yes | Yes |
| Live API     | `live_api`   | Real deployed Forecast / Campaign Audit APIs | No (network) | No (excluded) |

## Mock (`@pytest.mark.mock`)

Deterministic unit, API, and lifecycle tests in which every external service
(shared Forecast API, Project 2 Campaign Audit API) is faked, monkeypatched,
or served through an in-process TestClient. No network access.

- `test_phase1.py` — decision engine, guardrails, memory, campaign adapter,
  forecast tool, and FastAPI endpoints.
- `test_phase2.py` — intervention contracts, lifecycle reconstruction, outcome
  evaluation, and portfolio aggregation.
- `test_phase2_integration.py` — Phase 2 registry + FastAPI app integration.

## Real-data (`@pytest.mark.real_data`)

Tests that ingest realistic, real-shaped transaction observations (fixed
fixtures) and exercise portfolio evaluation end-to-end, with the forecast API
still mocked so results remain reproducible.

- `test_real_data_validation.py` — portfolio contracts across `AVAILABLE` /
  `NO_DATA` / `ERROR` forecast states, uplift metrics, and provenance joins.

## Live API (`@pytest.mark.live_api`)

Network-dependent smoke/contract tests against the deployed services. They are
excluded from the default run so the offline suite never depends on the
network or external service uptime.

- `test_live_api.py` — live Forecast API store/prediction/window contracts, a
  live portfolio evaluation, and (when `CAMPAIGN_AUDIT_API_URL` is configured)
  the read-only Campaign Audit API contract.

## Running

```bash
pytest                                   # default: mock + real_data only
pytest -m mock                           # mock tests only
pytest -m real_data                      # real-data tests only
pytest -m "not live_api"                 # same as the default run
pytest -m live_api                       # live API tests only (network required)
pytest -m "live_api or real_data"        # combine categories
```