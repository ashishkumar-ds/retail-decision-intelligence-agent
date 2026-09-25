# The off-path LLM layers are measured, not trusted

Every off-path LLM attempt appends one telemetry line
(`rag/llm_telemetry.py` → `logs/offpath_llm.jsonl`), and `/metrics` exposes
guard-rejection counts per layer and per gate. Two alert rules consume it
(`ops/prometheus/alerts.yml`): a rejection surge (the layer has become useless
while the service still looks healthy) and any provider unavailability (the
layer has silently degraded to deterministic output).

This exists because a fail-closed guard is invisible by construction. The
explainer and advisory layers serve the deterministic template on *every*
failure, so a dead provider and a healthy service produce identical `/health`,
identical decisions and identical logs — one warning line at most. "Absence of
failure metrics is not health" (the reasoning behind `RetailSweepStale`) applies
to the LLM layers exactly as it applies to the sweep scheduler.

`evaluation/llm_cases.py` + `evaluation/llm_evals.py` measure the other half:
which drafts the gates must veto, and — holding the harness fixed and swapping
in an adversarial provider — that no provider can get ungrounded content
served. Hallucination is a **veto, not a score**: the escape count must be 0,
and it is a CI gate (`pytest` wrapper + a CI step).

## Considered Options

- **In-memory counters** (rejected: `/metrics` is computed on scrape from
  durable state by design; counters would vanish on restart and be invisible to
  audit)
- **Writing the events into the recommendation log** (rejected: that file is the
  decision system of record, ADR-0002. Off-path LLM attempts are not decisions
  and must not enter the audit trail a reviewer reads)
- **Scoring LLM quality with an LLM judge** (rejected: a judge is one more
  ungrounded model inside the loop. Deterministic guards over numbers,
  citations and vocabulary are auditable, free and reproducible. A judge may be
  added *above* this layer as a report, never as a gate)
- **An open-ended LLM agent loop** (plan → tool-call → reflect): not adopted.
  The decision path is pure code (ADR-0001) and the LLM layers are single-shot
  and structurally inert; a tool-calling loop hands a model the ability to act,
  which is what the Human Gate exists to prevent. Revisit only with a tool
  allowlist, per-call authorization, and the same double gate.

Related: ADR-0001 (the decision path is pure code), ADR-0003 (fail-closed vs
fail-open asymmetry), ADR-0004 (pinned cases gate behaviour), ADR-0005 (numbers
are redacted before egress — the reason telemetry labels carry counts and gate
names, never business figures).
