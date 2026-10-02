# Demo assets — self-contained, offline

Two fixtures that let `bash scripts/seed_demo.sh` run the whole agent with **no
API keys, no Render services, and no Project 2**. They exist because a fresh
clone otherwise boots healthy but empty: the store universe comes from Project 2
and the store signals come from the deployed forecast service.

| File | Plays the role of | Read by |
| --- | --- | --- |
| `campaign_audit.jsonl` | Project 2's read-only audit log (`GET /audit`) | `tools/campaign_tool.py` via `CAMPAIGN_AUDIT_LOG_PATH` |
| `forecast_stub.py` | the Project 1 forecast API | `tools/forecast_tool.py` via `FORECAST_API_URL` |

**These are fixtures, not models or real data.** The stub's numbers are fixed
per store so the decision path stays deterministic and recomputable — the same
property the golden cases pin. Real deployments point the two variables at the
real services; `scripts/seed_demo.sh` only sets them when you have not.

The store ids are the real ones from Project 2's `Campaign 18` run, so the demo
exercise is faithful even though the sales numbers are synthetic.

`tests/test_demo_fixture.py` pins the contract: the fixture survives the same
parser the runtime uses, the stub satisfies the real `forecast_tool` validators,
and every store in the audit fixture has forecast data.